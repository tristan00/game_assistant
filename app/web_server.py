"""FastAPI backend for the web UI mode.

Mirrors the Qt MainWindow's state and feature set:
  - Window enumeration + selection
  - Auto-capture timer + manual capture + global hotkey (server-side pynput)
  - Sessions on disk
  - Strategy file CRUD
  - In-memory conversation history
  - Submit to Anthropic via run_completion() on a worker thread
  - REST endpoints for synchronous operations
  - WebSocket at /ws for server -> browser push (capture_saved, submit_*, etc.)

State is per-process (single shared session); opening multiple browser tabs
shows the same state.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import threading
import time
import traceback
import webbrowser
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.assistant_client import MAX_IMAGES_PER_REQUEST, run_completion
from app.capture import capture_window, list_windows
from app.config import get_api_key, set_api_key
from app.hotkey import DEFAULT_HOTKEY, HotkeyManager
from app.session import Session, capture_path, new_session
from app.settings import load_settings, qt_hotkey_to_pynput, save_settings
from app.strategies import (
    create_strategy,
    delete_strategy,
    list_strategies,
    load_strategy,
    save_strategy,
)

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"


# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------


class WebState:
    """Process-wide state for the web UI. Thread-safe-ish: simple mutex around mutations."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.settings: dict[str, Any] = load_settings()
        self.session: Session = new_session()
        self.selected_hwnd: int | None = None
        self.history: list[dict[str, str]] = []
        self.in_flight: bool = False
        self.in_flight_started_iso: str = ""
        self.in_flight_question: str = ""
        self.in_flight_model: str = ""
        self.in_flight_n_images: int = 0
        self.ws_clients: set[WebSocket] = set()
        self.loop: asyncio.AbstractEventLoop | None = None
        # Background capture timer.
        self._timer_stop = threading.Event()
        self._timer_thread: threading.Thread | None = None
        # Hotkey.
        pynput_hotkey = qt_hotkey_to_pynput(self.settings["hotkey_qt"]) or DEFAULT_HOTKEY
        self.hotkey = HotkeyManager(hotkey=pynput_hotkey, callback=self._on_hotkey)
        logger.info("WebState init: session=%s", self.session.folder.name)

    # ---- Lifecycle ----

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.hotkey.start()
        self._timer_thread = threading.Thread(target=self._timer_loop, name="capture-timer", daemon=True)
        self._timer_thread.start()
        logger.info("WebState started")

    def shutdown(self) -> None:
        self._timer_stop.set()
        self.hotkey.stop()
        if self._timer_thread is not None:
            self._timer_thread.join(timeout=2.0)
        logger.info("WebState shut down")

    # ---- Broadcasting to WS clients ----

    def schedule_broadcast(self, event: dict[str, Any]) -> None:
        """Thread-safe: marshal a broadcast onto the asyncio loop."""
        if self.loop is None:
            logger.debug("schedule_broadcast called before loop ready; dropping %s", event.get("type"))
            return
        asyncio.run_coroutine_threadsafe(self._broadcast(event), self.loop)

    async def _broadcast(self, event: dict[str, Any]) -> None:
        # Snapshot to avoid mutation during iteration.
        clients = list(self.ws_clients)
        dead: list[WebSocket] = []
        for ws in clients:
            try:
                await ws.send_json(event)
            except Exception as exc:  # client gone, broken pipe, etc.
                logger.debug("ws send failed (%r); marking client dead", exc)
                dead.append(ws)
        for ws in dead:
            self.ws_clients.discard(ws)

    # ---- Captures ----

    def capture_now(self, *, source: str) -> dict[str, Any] | None:
        """Take a capture of the selected window into the active session folder.

        Returns event payload or None if no window selected / capture failed.
        Broadcasts capture_saved on success.
        """
        with self.lock:
            hwnd = self.selected_hwnd
        if hwnd is None:
            logger.info("capture_now source=%s aborted: no window selected", source)
            return None
        try:
            png_bytes = capture_window(int(hwnd))
        except Exception:
            tb = traceback.format_exc()
            logger.error("capture_now source=%s failed:\n%s", source, tb)
            self.schedule_broadcast({"type": "capture_error", "source": source, "error": tb})
            return None
        path = capture_path(self.session.folder)
        path.write_bytes(png_bytes)
        with self.lock:
            total = self.session.screenshot_count
        event = {
            "type": "capture_saved",
            "source": source,
            "filename": path.name,
            "bytes": len(png_bytes),
            "total_shots": total,
            "session_folder": self.session.folder.name,
        }
        logger.info("capture_now source=%s saved %s (%d bytes) total=%d", source, path.name, len(png_bytes), total)
        self.schedule_broadcast(event)
        return event

    def _timer_loop(self) -> None:
        logger.info("capture timer thread starting")
        while not self._timer_stop.is_set():
            interval = max(5, int(self.settings.get("interval_seconds", 60)))
            # Poll the stop event in small steps so interval changes apply promptly.
            slept = 0.0
            while slept < interval and not self._timer_stop.is_set():
                self._timer_stop.wait(timeout=min(1.0, interval - slept))
                slept += 1.0
            if self._timer_stop.is_set():
                break
            self.capture_now(source="timer")
        logger.info("capture timer thread exiting")

    def _on_hotkey(self) -> None:
        logger.debug("hotkey trigger -> capture")
        self.capture_now(source="hotkey")

    # ---- Submit ----

    def submit(self, question: str) -> dict[str, Any]:
        """Validate, take a fresh capture, dispatch run_completion on a worker thread."""
        question = question.strip()
        if not question:
            raise HTTPException(status_code=400, detail="empty question")

        api_key = get_api_key()
        if not api_key:
            raise HTTPException(status_code=412, detail="no API key set")

        with self.lock:
            if self.selected_hwnd is None:
                raise HTTPException(status_code=412, detail="no window selected")
            if self.in_flight:
                raise HTTPException(status_code=409, detail="a submit is already in flight")

        # Fresh capture before request.
        fresh = self.capture_now(source="submit")
        if fresh is None:
            raise HTTPException(status_code=500, detail="pre-submit capture failed")

        last_n = int(self.settings.get("last_n", 5))
        image_paths = sorted(
            self.session.folder.glob("shot_*.png"),
            key=lambda p: p.stat().st_mtime,
        )[-last_n:]
        if not image_paths:
            raise HTTPException(status_code=500, detail="no screenshots in session folder")

        model = self.settings.get("model", "claude-sonnet-4-6")
        web_search_max = int(self.settings.get("web_search_max_uses", 2))

        active_strategy = self.settings.get("active_strategy", "") or ""
        strategy_text = load_strategy(active_strategy) if active_strategy else ""

        started_iso = datetime.now(timezone.utc).isoformat()
        with self.lock:
            self.in_flight = True
            self.in_flight_started_iso = started_iso
            self.in_flight_question = question
            self.in_flight_model = model
            self.in_flight_n_images = len(image_paths)
            history_snapshot = list(self.history)

        excerpt = (question.replace("\n", " ")[:80]).strip()
        if len(question) > 80:
            excerpt += "…"
        self.schedule_broadcast({
            "type": "submit_started",
            "question_excerpt": excerpt,
            "model": model,
            "n_images": len(image_paths),
            "web_search_max": web_search_max,
            "started_iso": started_iso,
        })

        thread = threading.Thread(
            target=self._submit_worker,
            args=(api_key, model, history_snapshot, strategy_text, question, image_paths, web_search_max),
            name="submit-worker",
            daemon=True,
        )
        thread.start()
        return {"started_iso": started_iso, "n_images": len(image_paths), "model": model}

    def _submit_worker(
        self,
        api_key: str,
        model: str,
        history: list[dict[str, str]],
        strategy_text: str,
        question: str,
        image_paths: list,
        web_search_max: int,
    ) -> None:
        t_start = time.monotonic()
        try:
            text = run_completion(
                api_key=api_key,
                model=model,
                history=history,
                strategy_text=strategy_text,
                question=question,
                image_paths=image_paths,
                web_search_max_uses=web_search_max,
                log_tag="web_submit",
            )
        except Exception:
            tb = traceback.format_exc()
            elapsed = time.monotonic() - t_start
            logger.error("submit failed after %.1fs:\n%s", elapsed, tb)
            with self.lock:
                self.in_flight = False
            self.schedule_broadcast({
                "type": "submit_error",
                "error": tb,
                "elapsed_seconds": elapsed,
            })
            return
        elapsed = time.monotonic() - t_start
        with self.lock:
            self.history.append({"question": question, "response": text})
            self.in_flight = False
            history_now = list(self.history)
        logger.info("submit done in %.1fs (%d chars)", elapsed, len(text))
        self.schedule_broadcast({
            "type": "submit_result",
            "question": question,
            "response": text,
            "elapsed_seconds": elapsed,
            "history": history_now,
        })

    # ---- Session ----

    def new_session(self) -> dict[str, Any]:
        with self.lock:
            self.session = new_session()
            self.history.clear()
        event = {"type": "session_changed", "folder_name": self.session.folder.name, "total_shots": 0}
        self.schedule_broadcast(event)
        self.schedule_broadcast({"type": "history_cleared"})
        return event

    # ---- Snapshot ----

    def snapshot(self) -> dict[str, Any]:
        try:
            windows = [{"hwnd": h, "title": t} for h, t in list_windows()]
        except Exception:
            logger.exception("snapshot: list_windows failed")
            windows = []
        with self.lock:
            active_strategy = self.settings.get("active_strategy", "") or ""
            return {
                "settings": dict(self.settings),
                "session": {
                    "folder_name": self.session.folder.name,
                    "total_shots": self.session.screenshot_count,
                },
                "selected_hwnd": self.selected_hwnd,
                "windows": windows,
                "history": list(self.history),
                "strategies": list_strategies(),
                "active_strategy": active_strategy,
                "active_strategy_content": load_strategy(active_strategy) if active_strategy else "",
                "in_flight": self.in_flight,
                "in_flight_started_iso": self.in_flight_started_iso if self.in_flight else "",
                "in_flight_question": self.in_flight_question if self.in_flight else "",
                "in_flight_model": self.in_flight_model if self.in_flight else "",
                "in_flight_n_images": self.in_flight_n_images if self.in_flight else 0,
                "has_api_key": bool(get_api_key()),
            }


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app(state: WebState | None = None) -> FastAPI:
    state = state or WebState()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        loop = asyncio.get_running_loop()
        state.start(loop)
        try:
            yield
        finally:
            state.shutdown()

    app = FastAPI(lifespan=lifespan)
    app.state.web_state = state  # type: ignore[attr-defined]

    # ----- REST endpoints -----

    @app.get("/api/state")
    def get_state():
        return state.snapshot()

    @app.get("/api/windows")
    def get_windows():
        try:
            return [{"hwnd": h, "title": t} for h, t in list_windows()]
        except Exception:
            tb = traceback.format_exc()
            logger.error("list_windows failed:\n%s", tb)
            raise HTTPException(status_code=500, detail=tb)

    @app.put("/api/window")
    async def set_window(req: Request):
        body = await req.json()
        hwnd = body.get("hwnd")
        with state.lock:
            state.selected_hwnd = int(hwnd) if hwnd is not None else None
        logger.info("window selected: %s", state.selected_hwnd)
        return {"selected_hwnd": state.selected_hwnd}

    @app.post("/api/capture")
    def manual_capture():
        result = state.capture_now(source="button")
        if result is None:
            raise HTTPException(status_code=412, detail="no window selected or capture failed")
        return result

    @app.post("/api/session/new")
    def post_new_session():
        return state.new_session()

    @app.get("/api/settings")
    def get_settings():
        return dict(state.settings)

    @app.put("/api/settings")
    async def put_settings(req: Request):
        body = await req.json()
        save_settings(body)
        with state.lock:
            for k, v in body.items():
                state.settings[k] = v
        # Hotkey change: restart listener if hotkey_qt changed.
        if "hotkey_qt" in body:
            new_hotkey = qt_hotkey_to_pynput(body["hotkey_qt"]) or DEFAULT_HOTKEY
            state.hotkey.set_hotkey(new_hotkey)
        state.schedule_broadcast({"type": "settings_changed", "settings": dict(state.settings)})
        return dict(state.settings)

    @app.get("/api/strategies")
    def get_strategies():
        return {"strategies": list_strategies(), "active": state.settings.get("active_strategy", "")}

    @app.post("/api/strategies")
    async def post_strategy(req: Request):
        body = await req.json()
        name = body.get("name", "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="missing name")
        try:
            safe = create_strategy(name)
        except FileExistsError:
            raise HTTPException(status_code=409, detail=f"strategy {name!r} already exists")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        state.schedule_broadcast({"type": "strategy_list_changed", "strategies": list_strategies()})
        return {"name": safe}

    @app.get("/api/strategies/{name}")
    def get_strategy(name: str):
        content = load_strategy(name)
        return {"name": name, "content": content}

    @app.put("/api/strategies/{name}")
    async def put_strategy(name: str, req: Request):
        body = await req.json()
        content = body.get("content", "")
        save_strategy(name, content)
        return {"name": name, "content": content}

    @app.delete("/api/strategies/{name}")
    def remove_strategy(name: str):
        delete_strategy(name)
        # If the deleted strategy was active, clear active.
        if state.settings.get("active_strategy") == name:
            with state.lock:
                state.settings["active_strategy"] = ""
            save_settings({"active_strategy": ""})
            state.schedule_broadcast({"type": "settings_changed", "settings": dict(state.settings)})
        state.schedule_broadcast({"type": "strategy_list_changed", "strategies": list_strategies()})
        return {"ok": True}

    @app.put("/api/active_strategy")
    async def put_active_strategy(req: Request):
        body = await req.json()
        name = body.get("name", "")
        if name and name not in list_strategies():
            raise HTTPException(status_code=404, detail=f"strategy {name!r} not found")
        with state.lock:
            state.settings["active_strategy"] = name
        save_settings({"active_strategy": name})
        state.schedule_broadcast({"type": "settings_changed", "settings": dict(state.settings)})
        return {"active": name, "content": load_strategy(name) if name else ""}

    @app.post("/api/submit")
    async def post_submit(req: Request):
        body = await req.json()
        question = body.get("question", "")
        return state.submit(question)

    @app.get("/api/api_key/has")
    def has_api_key():
        return {"has_key": bool(get_api_key())}

    @app.put("/api/api_key")
    async def put_api_key(req: Request):
        body = await req.json()
        key = (body.get("key") or "").strip()
        if not key:
            raise HTTPException(status_code=400, detail="missing key")
        set_api_key(key)
        return {"ok": True}

    # ----- WebSocket -----

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        await ws.accept()
        state.ws_clients.add(ws)
        logger.info("ws client connected; total=%d", len(state.ws_clients))
        try:
            # Send initial snapshot.
            await ws.send_json({"type": "snapshot", "state": state.snapshot()})
            while True:
                # Keep the connection alive; we don't use client->server messages.
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("ws handler error")
        finally:
            state.ws_clients.discard(ws)
            logger.info("ws client disconnected; total=%d", len(state.ws_clients))

    # ----- Static files (index, css, js) -----

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

        @app.get("/")
        def root():
            return FileResponse(str(STATIC_DIR / "index.html"))
    else:
        logger.warning("STATIC_DIR not found: %s", STATIC_DIR)

        @app.get("/")
        def root_missing():
            return JSONResponse({"error": "static frontend not bundled"}, status_code=500)

    return app


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run(*, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> int:
    import uvicorn

    if host != "127.0.0.1":
        logger.warning(
            "Web UI listening on %s (NOT just localhost). The API key, screenshots, "
            "and conversation history will be accessible from any device that can "
            "route to this address. There is no auth.",
            host,
        )

    app = create_app()
    if open_browser:
        url = f"http://{('localhost' if host in ('127.0.0.1', '0.0.0.0') else host)}:{port}/"
        # Defer the browser open by ~1s so uvicorn has a chance to bind.
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
        logger.info("opening browser at %s", url)

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="info",
        log_config=None,  # use our own logging config
    )
    server = uvicorn.Server(config)
    try:
        server.run()
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt; shutting down")
    return 0
