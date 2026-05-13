"""FastAPI backend for the web UI mode.

Mirrors the Qt MainWindow's state and feature set:
  - Window enumeration + selection
  - Auto-capture timer + manual capture + global hotkey (server-side pynput)
  - Sessions on disk
  - Goal file CRUD
  - In-memory conversation history
  - Submit to Anthropic via run_completion() on a worker thread
  - REST endpoints for synchronous operations
  - WebSocket at /ws for server -> browser push (capture_saved, submit_*, etc.)

State is per-process (single shared session); opening multiple browser tabs
shows the same state.
"""

import asyncio
import json
import logging
import shutil
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

from app import games
from app.assistant_client import MAX_IMAGES_PER_REQUEST, run_completion
from app.capture import capture_window, list_windows
from app.config import get_api_key, set_api_key
from app.hotkey import DEFAULT_HOTKEY, HotkeyManager
from app.perception import schema as perception_schema
from app.perception import schema_builder as perception_schema_builder
from app.perception import stage1 as perception_stage1
from app.perception import stage2 as perception_stage2
from app.session import Session, capture_path, new_session
from app.settings import load_settings, qt_hotkey_to_pynput, save_settings
from app.goals import (
    create_goal,
    delete_goal,
    list_goals,
    load_goal,
    save_goal,
)
from app.wiki import discovery as wiki_discovery
from app.wiki import quick_ref as wiki_quick_ref
from app.wiki import search as wiki_search
from app.wiki import storage as wiki_storage
from app.wiki.crawler import Crawler

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
        # Game identity binding.
        self.active_game_id: str | None = None
        self.identifying_window_title: str | None = None
        # Single-slot crawler tied to the currently-selected window's game.
        # On window switch, the previous crawler is cancelled; on reselect,
        # a fresh crawler resumes from the disk-known visited set.
        self._active_crawler: Crawler | None = None
        self._active_crawler_game_id: str | None = None
        self._active_crawler_thread: threading.Thread | None = None
        # Re-entry guard for the one-shot quick_ref + schema build per game.
        # Each game_id appears here for the duration of its build thread.
        self._post_crawl_in_flight: set[str] = set()
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
        with self.lock:
            cr = self._active_crawler
            cr_thread = self._active_crawler_thread
        if cr is not None:
            cr.cancel()
        if self._timer_thread is not None:
            self._timer_thread.join(timeout=2.0)
        if cr_thread is not None:
            cr_thread.join(timeout=5.0)
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

    # ---- Game identity ----

    def _set_active_game(self, game_id: str | None) -> None:
        """Update active_game_id, broadcast active_game_changed, ensure the
        crawler slot is dedicated to ``game_id``.
        """
        entry = None
        display_name = None
        is_game = False
        crawl_state = "none"
        page_count = 0
        wiki_url = None
        if game_id is not None and game_id != games.NOT_A_GAME:
            entry = games.get_game(game_id)
            if entry is not None:
                display_name = entry.display_name
                is_game = entry.is_game
                crawl_state = entry.crawl_state
                page_count = entry.page_count
                wiki_url = entry.wiki_url
        with self.lock:
            self.active_game_id = game_id
        logger.info("active game -> %s (%s)", game_id, display_name)
        self.schedule_broadcast({
            "type": "active_game_changed",
            "game_id": game_id,
            "display_name": display_name,
            "is_game": is_game,
            "is_not_a_game": game_id == games.NOT_A_GAME,
            "crawl_state": crawl_state,
            "page_count": page_count,
            "wiki_url": wiki_url,
        })
        self._ensure_active_crawler(game_id)

    def _ensure_active_crawler(self, game_id: str | None, *, force_restart: bool = False) -> None:
        """Ensure the single crawler slot matches ``game_id``.

        - If a worker thread is already alive (or just-started) for
          ``game_id`` and ``force_restart`` is False: do nothing.
        - If a worker thread is alive for a *different* game, or
          ``force_restart`` is True: cancel its Crawler (when instantiated)
          so its BFS loop exits. The thread's ``finally`` block clears the
          slot when it eventually exits.
        - If no worker is alive and ``game_id`` is a real game: start a fresh
          worker thread targeting ``_discover_and_crawl(game_id)``.

        Reentrant: ``_discover_and_crawl`` calls ``_set_active_game`` (which
        calls back into here) to re-broadcast state — these reentrant calls
        no-op because the current thread is the active one.

        ``force_restart=True`` is for cases where the user changed the wiki
        URL or explicitly asked for a fresh crawl; the running crawler may
        still be hitting the old URL.

        Thread safety: the entire decision (read slot → cancel → start) runs
        under ``self.lock`` to prevent two concurrent callers from each
        seeing an empty / not-yet-started slot and racing two workers for
        the same game (which collides on ``_meta.json`` writes via
        ``os.replace`` on Windows).
        """
        new_thread: threading.Thread | None = None
        with self.lock:
            current_id = self._active_crawler_game_id
            current_crawler = self._active_crawler
            current_thread = self._active_crawler_thread

            # A worker we just registered counts as "occupying the slot" even
            # if its OS thread hasn't transitioned to alive yet. Under the
            # lock this is exact: the worker's finally clears the slot
            # under the same lock, so a non-None thread reference means a
            # worker is mid-flight (starting, running, or about to clear).
            slot_occupied = current_thread is not None

            if slot_occupied and current_id == game_id and not force_restart:
                return

            if slot_occupied and current_crawler is not None and (
                current_id != game_id or force_restart
            ):
                logger.info(
                    "active crawler: cancelling for %s (switching to %s, force=%s)",
                    current_id, game_id, force_restart,
                )
                current_crawler.cancel()

            if game_id is None or game_id == games.NOT_A_GAME:
                return

            entry = games.get_game(game_id)
            if entry is None:
                return

            new_thread = threading.Thread(
                target=self._discover_and_crawl,
                args=(game_id,),
                name=f"wiki-{game_id}",
                daemon=True,
            )
            self._active_crawler_game_id = game_id
            self._active_crawler = None  # Crawler is instantiated inside the worker.
            self._active_crawler_thread = new_thread

        # start() outside the lock — it's fast but blocking the lock during
        # OS thread spawn is unnecessary, and the slot is already claimed
        # so concurrent _ensure_active_crawler callers will see this thread.
        new_thread.start()

    def _ensure_game_binding(self, hwnd: int, window_title: str, *, force_reidentify: bool = False) -> None:
        """Daemon-thread entry point. Resolve window -> game; kick off discovery/crawl if needed.

        Safe to call multiple times for the same hwnd — uses the cached binding
        unless ``force_reidentify`` is True.
        """
        try:
            self._ensure_game_binding_impl(hwnd, window_title, force_reidentify=force_reidentify)
        except Exception:
            logger.exception("_ensure_game_binding raised for hwnd=%s title=%r", hwnd, window_title)

    def _ensure_game_binding_impl(self, hwnd: int, window_title: str, *, force_reidentify: bool) -> None:
        # 1. Cached binding fast path. _set_active_game ensures the crawler
        # slot is dedicated to this game (cancelling any previous crawler).
        if not force_reidentify:
            existing = games.get_binding(window_title)
            if existing:
                self._set_active_game(existing)
                return

        # 2. Need an API key + a screenshot to identify.
        api_key = get_api_key()
        if not api_key:
            logger.info("ensure_game_binding: no API key set; deferring identification")
            self._set_active_game(None)
            self.schedule_broadcast({"type": "game_identifying", "stage": "deferred_no_api_key"})
            return

        with self.lock:
            if self.selected_hwnd != hwnd:
                logger.info("ensure_game_binding: window changed before identify; aborting")
                return
            self.identifying_window_title = window_title
        self.schedule_broadcast({"type": "game_identifying", "stage": "started", "title": window_title})

        image_path = self._latest_or_fresh_capture()
        if image_path is None:
            logger.info("ensure_game_binding: no screenshot available; aborting")
            self.schedule_broadcast({"type": "game_identifying", "stage": "no_screenshot"})
            return

        known = [e.game_id for e in games.list_games()]
        parsed = games.identify_game_from_screenshot(
            api_key=api_key,
            model=self.settings.get("game_id_model", "claude-haiku-4-5-20251001"),
            window_title=window_title,
            image_path=image_path,
            known_game_ids=known,
        )
        if parsed is None:
            self.schedule_broadcast({"type": "game_identifying", "stage": "llm_failed"})
            return

        # Stale-check: user may have swapped windows while LLM was thinking.
        with self.lock:
            if self.selected_hwnd != hwnd:
                logger.info("ensure_game_binding: window changed during identify; not applying result")
                return

        bound = games.accept_identification(window_title, parsed)
        if bound is None:
            self.schedule_broadcast({
                "type": "game_identifying",
                "stage": "low_confidence",
                "parsed": parsed,
            })
            return

        self._set_active_game(bound)

    def _latest_or_fresh_capture(self) -> Path | None:
        """Take a fresh capture of the currently-selected window for identification.

        We never reuse an existing shot because it might be of a previously-
        selected window — the LLM needs the actual game it's being asked about.
        """
        self.capture_now(source="game_id")
        shots = sorted(self.session.folder.glob("shot_*.png"), key=lambda p: p.stat().st_mtime)
        return shots[-1] if shots else None

    def _discover_and_crawl(self, game_id: str) -> None:
        """Worker thread for the active crawler slot. Discovers the wiki if
        needed, then runs the Crawler. Always runs to completion or to cancel.
        On exit, clears the slot iff it's still ours (the user may have
        switched windows mid-run, in which case ``_ensure_active_crawler`` has
        already assigned a new owner)."""
        crawler: Crawler | None = None
        try:
            entry = games.get_game(game_id)
            if entry is None:
                return
            api_key = get_api_key()
            if not api_key:
                logger.info("discover_and_crawl: no API key; aborting")
                return
            if not entry.wiki_url:
                logger.info("discover_and_crawl: discovering wiki for %s", game_id)
                self.schedule_broadcast({
                    "type": "wiki_discovering",
                    "game_id": game_id,
                    "display_name": entry.display_name,
                })
                candidate = wiki_discovery.discover_wiki(
                    entry.display_name,
                    api_key=api_key,
                    model=self.settings.get("wiki_discovery_model", "claude-sonnet-4-6"),
                    user_agent=self.settings.get("wiki_user_agent", "game_assistant"),
                    rate_seconds=float(self.settings.get("wiki_rate_seconds", 1.0)),
                )
                if candidate is None:
                    logger.info("discover_and_crawl: no wiki found for %s", game_id)
                    self.schedule_broadcast({
                        "type": "wiki_not_found",
                        "game_id": game_id,
                        "display_name": entry.display_name,
                    })
                    return
                entry.wiki_url = candidate.wiki_url
                entry.wiki_api_url = candidate.api_url
                entry.wiki_root_page = candidate.root_page
                games.upsert_game(entry)
                self._set_active_game(game_id)  # re-broadcast with updated wiki_url
                self.schedule_broadcast({
                    "type": "wiki_discovered",
                    "game_id": game_id,
                    "wiki_url": candidate.wiki_url,
                    "sitename": candidate.sitename,
                })

            if not (entry.wiki_api_url and entry.wiki_root_page):
                raise RuntimeError(
                    f"_discover_and_crawl: game {game_id!r} has wiki_url but missing api_url or root_page — "
                    "discovery should have populated both"
                )

            # The crawler emits crawl_progress + crawl_done via on_event. We
            # wrap that callback to also fire the one-shot post-crawl builds
            # as soon as the corpus has any content (≥1 page) — decoupled
            # from crawler completion so partial / cancelled crawls also
            # produce quick_ref + schema.
            def _on_crawl_event(ev: dict) -> None:
                self.schedule_broadcast(ev)
                if ev.get("type") in ("crawl_progress", "crawl_done"):
                    self._maybe_kick_off_post_crawl_builds(game_id)

            crawler = Crawler(
                game_id=game_id,
                wiki_url=entry.wiki_url,
                api_url=entry.wiki_api_url,
                root_title=entry.wiki_root_page,
                user_agent=self.settings.get("wiki_user_agent", "game_assistant"),
                rate_seconds=float(self.settings.get("wiki_rate_seconds", 1.0)),
                on_event=_on_crawl_event,
            )
            with self.lock:
                if self._active_crawler_game_id == game_id:
                    self._active_crawler = crawler
            # Kick post-crawl builds eagerly: a previous run may already have
            # left pages on disk without quick_ref/schema (e.g. cancelled
            # before the build finished). This is idempotent.
            self._maybe_kick_off_post_crawl_builds(game_id)
            result = crawler.run()

            entry = games.get_game(game_id) or entry
            on_disk_pages = wiki_storage.page_count_on_disk(game_id)
            entry.crawl_state = result["state"]
            entry.page_count = on_disk_pages
            entry.last_crawl_iso = datetime.now(timezone.utc).isoformat()
            games.upsert_game(entry)
            self._set_active_game(game_id)

            if on_disk_pages > 0:
                n = wiki_search.build_index(game_id)
                logger.info("post-crawl: indexed %d pages for %s", n, game_id)

            # Final pass — picks up the case where the crawler exits without
            # ever firing crawl_progress (e.g. wrote a single page on a wiki
            # smaller than state_save_every).
            self._maybe_kick_off_post_crawl_builds(game_id)
        except Exception:
            logger.exception("_discover_and_crawl failed for %s", game_id)
        finally:
            # Clear the slot iff it's still ours — if the user switched
            # windows mid-run, ``_ensure_active_crawler`` already assigned a
            # new owner and we mustn't stomp it.
            with self.lock:
                if self._active_crawler_game_id == game_id and (
                    crawler is None or self._active_crawler is crawler
                ):
                    self._active_crawler = None
                    self._active_crawler_game_id = None
                    self._active_crawler_thread = None

    def _maybe_kick_off_post_crawl_builds(self, game_id: str) -> None:
        """One-shot per game: build quick_ref then perception schema in a
        background thread when their outputs are missing and the corpus has
        ≥1 page on disk. Idempotent + reentrancy-guarded.
        """
        if not game_id or game_id == games.NOT_A_GAME:
            return
        if wiki_storage.page_count_on_disk(game_id) < 1:
            return
        quick_ref_done = wiki_storage.quick_ref_path(game_id).exists()
        schema_done = wiki_storage.perception_schema_path(game_id).exists()
        if quick_ref_done and schema_done:
            return
        with self.lock:
            if game_id in self._post_crawl_in_flight:
                return
            self._post_crawl_in_flight.add(game_id)

        def _run() -> None:
            try:
                api_key = get_api_key()
                if not api_key:
                    logger.info("post-crawl build: no API key; aborting for %s", game_id)
                    return
                if not wiki_storage.quick_ref_path(game_id).exists():
                    logger.info("post-crawl: building quick_ref for %s", game_id)
                    self.schedule_broadcast({"type": "quick_ref_building", "game_id": game_id})
                    wiki_quick_ref.build_quick_ref(
                        game_id,
                        api_key=api_key,
                        model=self.settings.get("quick_ref_model", "claude-sonnet-4-6"),
                    )
                    self.schedule_broadcast({"type": "quick_ref_done", "game_id": game_id})
                if (
                    wiki_storage.quick_ref_path(game_id).exists()
                    and not wiki_storage.perception_schema_path(game_id).exists()
                ):
                    logger.info("post-crawl: building perception schema for %s", game_id)
                    self.schedule_broadcast({"type": "schema_building", "game_id": game_id})
                    perception_schema_builder.build_perception_schema(
                        game_id,
                        api_key=api_key,
                        model=self.settings.get("schema_builder_model", "claude-sonnet-4-6"),
                    )
                    self.schedule_broadcast({"type": "schema_done", "game_id": game_id})
                if (
                    wiki_storage.quick_ref_path(game_id).exists()
                    and wiki_storage.perception_schema_path(game_id).exists()
                ):
                    self.schedule_broadcast({
                        "type": "corpus_ready",
                        "game_id": game_id,
                        "page_count": wiki_storage.page_count_on_disk(game_id),
                    })
            except Exception:
                logger.exception("post-crawl build failed for %s", game_id)
            finally:
                with self.lock:
                    self._post_crawl_in_flight.discard(game_id)

        threading.Thread(
            target=_run,
            name=f"post-crawl-{game_id}",
            daemon=True,
        ).start()

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

        # Fresh capture before request. If it fails (e.g. window minimized),
        # we proceed with whatever older shots are in the session folder.
        # Only hard-fail when there are no shots at all.
        self.capture_now(source="submit")

        last_n = int(self.settings.get("last_n", 5))
        image_paths = sorted(
            self.session.folder.glob("shot_*.png"),
            key=lambda p: p.stat().st_mtime,
        )[-last_n:]
        if not image_paths:
            raise HTTPException(status_code=500, detail="no screenshots in session folder")

        model = self.settings.get("model", "claude-sonnet-4-6")

        active_goal = self.settings.get("active_goal", "") or ""
        goal_text = load_goal(active_goal) if active_goal else ""

        with self.lock:
            active_game_id = self.active_game_id

        is_real_game = bool(active_game_id) and active_game_id != games.NOT_A_GAME
        on_disk_pages = wiki_storage.page_count_on_disk(active_game_id) if is_real_game else 0

        if is_real_game and on_disk_pages > 0:
            indexed = wiki_search.index_page_count(active_game_id)
            if indexed != on_disk_pages:
                logger.info(
                    "submit: refreshing FTS5 index for %s (on_disk=%d, indexed=%d)",
                    active_game_id, on_disk_pages, indexed,
                )
                wiki_search.build_index(active_game_id)

        quick_ref_text: str | None = None
        if is_real_game:
            qp = wiki_storage.quick_ref_path(active_game_id)
            if qp.exists():
                quick_ref_text = qp.read_text(encoding="utf-8")

        # corpus_game_id is used by the corpus_search tool; perception_game_id
        # by load_schema + stage1. Either may be None for this submit — the
        # worker composes the prompt from whatever ingredients are present.
        corpus_game_id: str | None = active_game_id if (is_real_game and on_disk_pages > 0) else None
        perception_game_id: str | None = active_game_id if is_real_game else None

        logger.info(
            "submit: ingredients game_id=%s pages=%d quick_ref=%s perception_candidate=%s",
            active_game_id, on_disk_pages,
            "present" if quick_ref_text else "absent",
            "yes" if perception_game_id else "no",
        )

        enable_prompt_cache = bool(self.settings.get("enable_prompt_cache", True))
        client_tool_max_iters = int(self.settings.get("client_tool_max_iters", 6))

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
            "corpus_game_id": corpus_game_id,
            "started_iso": started_iso,
        })

        thread = threading.Thread(
            target=self._submit_worker,
            args=(
                api_key, model, history_snapshot, goal_text, question, image_paths,
                quick_ref_text, corpus_game_id, perception_game_id, enable_prompt_cache, client_tool_max_iters,
            ),
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
        goal_text: str,
        question: str,
        image_paths: list,
        quick_ref_text: str | None,
        corpus_game_id: str | None,
        perception_game_id: str | None,
        enable_prompt_cache: bool,
        client_tool_max_iters: int,
    ) -> None:
        t_start = time.monotonic()

        def _corpus_search(query: str, max_results: int) -> list[dict]:
            if corpus_game_id is None:
                return []
            return wiki_search.search(corpus_game_id, query, max_results=max_results)

        # Single outer catch covers BOTH the perception pipeline and the
        # reasoning call. Any raise (stage1/stage2 LLM failures, reasoning
        # failures, API errors) clears in_flight and broadcasts submit_error
        # so the UI sees the failure. The single catch site is the outermost
        # worker; there is no intermediate try/except.
        try:
            synthesis_text: str | None = None
            schema_text = perception_schema.load_schema(perception_game_id) if image_paths else None
            if schema_text is not None:
                stage1_model = self.settings.get("perception_stage1_model", "claude-haiku-4-5-20251001")
                stage2_model = self.settings.get("perception_stage2_model", "claude-sonnet-4-6")
                logger.info(
                    "perception: starting stage-1 fill game_id=%s images=%d schema_chars=%d",
                    perception_game_id, len(image_paths), len(schema_text),
                )
                t_stage1 = time.monotonic()
                sidecars = perception_stage1.enumerate_images(
                    image_paths,
                    api_key=api_key,
                    model=stage1_model,
                    schema_text=schema_text,
                    game_id=perception_game_id,
                    log_tag="stage1",
                )
                logger.info(
                    "perception: stage-1 fill done in %.2fs (filled=%d/%d)",
                    time.monotonic() - t_stage1,
                    sum(1 for s in sidecars if s is not None),
                    len(sidecars),
                )
                t_stage2 = time.monotonic()
                synthesis_text = perception_stage2.synthesize(
                    sidecars=sidecars,
                    image_filenames=[p.name for p in image_paths],
                    schema_text=schema_text,
                    question=question,
                    api_key=api_key,
                    model=stage2_model,
                    log_tag="stage2",
                )
                logger.info("perception: stage-2 synthesis done in %.2fs", time.monotonic() - t_stage2)
            else:
                logger.info(
                    "perception: skipped (game_id=%s, images=%d, schema_present=False) — reasoning will see all images",
                    perception_game_id, len(image_paths),
                )

            text = run_completion(
                api_key=api_key,
                model=model,
                history=history,
                goal_text=goal_text,
                question=question,
                image_paths=image_paths,
                quick_ref_text=quick_ref_text,
                synthesis_text=synthesis_text,
                search_game_rules_handler=_corpus_search,
                enable_prompt_cache=enable_prompt_cache,
                client_tool_max_iters=client_tool_max_iters,
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
        all_games = [e.to_dict() for e in games.list_games()]
        with self.lock:
            active_goal = self.settings.get("active_goal", "") or ""
            active_game_id = self.active_game_id
        active_game_entry = None
        if active_game_id and active_game_id != games.NOT_A_GAME:
            entry = games.get_game(active_game_id)
            if entry is not None:
                active_game_entry = entry.to_dict()
                active_game_entry["has_quick_ref"] = wiki_storage.quick_ref_path(active_game_id).exists()
                active_game_entry["has_perception_schema"] = wiki_storage.perception_schema_path(active_game_id).exists()
                active_game_entry["pages_on_disk"] = wiki_storage.page_count_on_disk(active_game_id)
        return {
            "settings": dict(self.settings),
            "session": {
                "folder_name": self.session.folder.name,
                "total_shots": self.session.screenshot_count,
            },
            "selected_hwnd": self.selected_hwnd,
            "windows": windows,
            "history": list(self.history),
            "goals": list_goals(),
            "active_goal": active_goal,
            "active_goal_content": load_goal(active_goal) if active_goal else "",
            "active_game_id": active_game_id,
            "active_game": active_game_entry,
            "active_game_is_not_a_game": active_game_id == games.NOT_A_GAME,
            "games": all_games,
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
            new_hwnd = state.selected_hwnd
            # Clear stale active game until we re-resolve below.
            state.active_game_id = None
        logger.info("window selected: %s", new_hwnd)
        if new_hwnd is None:
            state.schedule_broadcast({
                "type": "active_game_changed",
                "game_id": None, "display_name": None, "is_game": False,
                "is_not_a_game": False, "crawl_state": "none", "page_count": 0, "wiki_url": None,
            })
            return {"selected_hwnd": new_hwnd}
        # Look up the window title for the chosen hwnd, then dispatch identification.
        title = None
        try:
            for h, t in list_windows():
                if h == new_hwnd:
                    title = t
                    break
        except Exception:
            logger.exception("set_window: list_windows failed")
        if title:
            threading.Thread(
                target=state._ensure_game_binding,
                args=(new_hwnd, title),
                name=f"identify-{new_hwnd}",
                daemon=True,
            ).start()
        else:
            logger.warning("set_window: could not resolve title for hwnd=%s", new_hwnd)
        return {"selected_hwnd": new_hwnd}

    @app.get("/api/games")
    def get_games():
        return {
            "games": [e.to_dict() for e in games.list_games()],
            "active_game_id": state.active_game_id,
        }

    @app.post("/api/games/recrawl")
    async def post_recrawl():
        """Re-run discovery + crawl for the active game, INCREMENTALLY.

        Existing pages on disk are preserved — the crawler seeds its visited
        set from them, so previously-fetched articles are not re-downloaded.
        Only new articles surfaced by the seeds (root_title + Main_Page + the
        list=allpages backup) are fetched.

        Use ``/api/games/reset_corpus`` if you want a full wipe.
        """
        with state.lock:
            game_id = state.active_game_id
        if not game_id or game_id == games.NOT_A_GAME:
            raise HTTPException(status_code=412, detail="no active game (or active window is not a game)")
        entry = games.get_game(game_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"game_id {game_id!r} not in registry")
        entry.crawl_state = "none"
        entry.last_crawl_iso = None
        games.upsert_game(entry)
        state._set_active_game(game_id)
        state._ensure_active_crawler(game_id, force_restart=True)
        return {"ok": True, "game_id": game_id, "wiped": False}

    @app.post("/api/games/reset_corpus")
    async def post_reset_corpus():
        """Full wipe: delete the corpus dir for the active game, then re-crawl.

        Distinct from /api/games/recrawl (which is incremental). Use only
        when you want to discard previously-fetched pages — e.g. the wiki
        content has changed enough that a fresh crawl is warranted.
        """
        with state.lock:
            game_id = state.active_game_id
        if not game_id or game_id == games.NOT_A_GAME:
            raise HTTPException(status_code=412, detail="no active game")
        entry = games.get_game(game_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"game_id {game_id!r} not in registry")
        wdir = wiki_storage.wiki_dir(game_id)
        if wdir.exists():
            try:
                shutil.rmtree(wdir)
            except OSError as exc:
                logger.exception("reset_corpus: failed to remove %s", wdir)
                raise HTTPException(status_code=500, detail=f"failed to wipe corpus dir: {exc!r}")
        entry.crawl_state = "none"
        entry.page_count = 0
        entry.last_crawl_iso = None
        games.upsert_game(entry)
        state._set_active_game(game_id)
        state._ensure_active_crawler(game_id, force_restart=True)
        return {"ok": True, "game_id": game_id, "wiped": True}

    @app.post("/api/games/reidentify")
    async def post_reidentify():
        """Force a fresh LLM identification of the current window, clearing any cached binding."""
        with state.lock:
            hwnd = state.selected_hwnd
        if hwnd is None:
            raise HTTPException(status_code=412, detail="no window selected")
        title = None
        try:
            for h, t in list_windows():
                if h == hwnd:
                    title = t
                    break
        except Exception:
            tb = traceback.format_exc()
            raise HTTPException(status_code=500, detail=tb)
        if not title:
            raise HTTPException(status_code=404, detail=f"hwnd {hwnd} not found in window list")
        games.clear_binding(title)
        threading.Thread(
            target=state._ensure_game_binding,
            args=(hwnd, title),
            kwargs={"force_reidentify": True},
            name=f"reidentify-{hwnd}",
            daemon=True,
        ).start()
        return {"ok": True, "window_title": title}

    # ----- Settings page: wiki management -----

    def _wiki_game_entry(entry: games.GameEntry) -> dict:
        meta = wiki_storage.load_meta(entry.game_id)
        return {
            "game_id": entry.game_id,
            "display_name": entry.display_name,
            "is_game": entry.is_game,
            "sitename": meta.get("sitename", ""),
            "wiki_url": entry.wiki_url,
            "api_url": entry.wiki_api_url,
            "root_page": entry.wiki_root_page,
            "crawl_state": entry.crawl_state,
            "page_count": wiki_storage.page_count_on_disk(entry.game_id),
            "last_crawl_iso": entry.last_crawl_iso,
            "has_quick_ref": wiki_storage.quick_ref_path(entry.game_id).exists(),
            "has_perception_schema": wiki_storage.perception_schema_path(entry.game_id).exists(),
        }

    @app.get("/api/wiki/games")
    def get_wiki_games():
        return {"games": [_wiki_game_entry(e) for e in games.list_games() if e.is_game]}

    @app.put("/api/wiki/games/{game_id}")
    async def put_wiki_game(game_id: str, req: Request):
        entry = games.get_game(game_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"game_id {game_id!r} not found")
        body = await req.json()
        if "wiki_url" in body:
            entry.wiki_url = (body.get("wiki_url") or "").strip() or None
        if "api_url" in body:
            entry.wiki_api_url = (body.get("api_url") or "").strip() or None
        if "root_page" in body:
            entry.wiki_root_page = (body.get("root_page") or "").strip() or None
        entry.crawl_state = "none"
        entry.last_crawl_iso = None
        games.upsert_game(entry)
        # Single-slot rule: only the active game has a crawler. If the edited
        # game is the active one, restart its crawler with the new URL.
        # Otherwise the new settings persist and the crawler will use them
        # the next time this game becomes active.
        with state.lock:
            is_active = state.active_game_id == game_id
        if is_active:
            state._ensure_active_crawler(game_id, force_restart=True)
        logger.info(
            "wiki edit: game_id=%s wiki_url=%s api_url=%s root_page=%s active=%s",
            game_id, entry.wiki_url, entry.wiki_api_url, entry.wiki_root_page, is_active,
        )
        return _wiki_game_entry(entry)

    @app.delete("/api/wiki/games/{game_id}/corpus")
    def delete_wiki_corpus(game_id: str):
        entry = games.get_game(game_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"game_id {game_id!r} not found")
        wdir = wiki_storage.wiki_dir(game_id)
        if wdir.exists():
            shutil.rmtree(wdir)
        entry.wiki_url = None
        entry.wiki_api_url = None
        entry.wiki_root_page = None
        entry.crawl_state = "none"
        entry.page_count = 0
        entry.last_crawl_iso = None
        games.upsert_game(entry)
        with state.lock:
            if state.active_game_id == game_id:
                state._set_active_game(game_id)
        logger.info("wiki delete corpus: game_id=%s wiped %s", game_id, wdir)
        return _wiki_game_entry(entry)

    @app.post("/api/wiki/games/{game_id}/rediscover")
    def post_wiki_rediscover(game_id: str):
        entry = games.get_game(game_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"game_id {game_id!r} not found")
        wdir = wiki_storage.wiki_dir(game_id)
        if wdir.exists():
            shutil.rmtree(wdir)
        entry.wiki_url = None
        entry.wiki_api_url = None
        entry.wiki_root_page = None
        entry.crawl_state = "none"
        entry.page_count = 0
        entry.last_crawl_iso = None
        games.upsert_game(entry)
        with state.lock:
            is_active = state.active_game_id == game_id
        if is_active:
            state._ensure_active_crawler(game_id, force_restart=True)
        logger.info("wiki rediscover: game_id=%s active=%s", game_id, is_active)
        return _wiki_game_entry(entry)

    # ----- Settings page: perception schema -----

    @app.get("/api/perception/schema/{game_id}")
    def get_perception_schema(game_id: str):
        if games.get_game(game_id) is None:
            raise HTTPException(status_code=404, detail=f"game_id {game_id!r} not found")
        sp = wiki_storage.perception_schema_path(game_id)
        if not sp.exists():
            raise HTTPException(status_code=404, detail=f"_perception_schema.md not present for {game_id!r}")
        return {"game_id": game_id, "content": sp.read_text(encoding="utf-8")}

    @app.put("/api/perception/schema/{game_id}")
    async def put_perception_schema(game_id: str, req: Request):
        if games.get_game(game_id) is None:
            raise HTTPException(status_code=404, detail=f"game_id {game_id!r} not found")
        body = await req.json()
        content = body.get("content", "")
        sp = wiki_storage.perception_schema_path(game_id)
        wiki_storage.atomic_write_text(sp, content)
        logger.info("perception schema write: game_id=%s chars=%d", game_id, len(content))
        return {"game_id": game_id, "content": content}

    @app.post("/api/perception/schema/{game_id}/regenerate")
    def post_perception_schema_regenerate(game_id: str):
        if games.get_game(game_id) is None:
            raise HTTPException(status_code=404, detail=f"game_id {game_id!r} not found")
        api_key = get_api_key()
        if not api_key:
            raise HTTPException(status_code=412, detail="no API key set")
        if not wiki_storage.quick_ref_path(game_id).exists():
            raise HTTPException(
                status_code=412,
                detail=f"_quick_ref.md missing for {game_id!r} — re-crawl first",
            )

        def _run():
            try:
                perception_schema_builder.build_perception_schema(
                    game_id,
                    api_key=api_key,
                    model=state.settings.get("schema_builder_model", "claude-sonnet-4-6"),
                )
                state.schedule_broadcast({"type": "perception_schema_rebuilt", "game_id": game_id})
            except Exception:
                logger.exception("regenerate perception schema failed for %s", game_id)

        threading.Thread(target=_run, name=f"schema-rebuild-{game_id}", daemon=True).start()
        return {"ok": True, "game_id": game_id}

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

    @app.get("/api/goals")
    def get_goals():
        return {"goals": list_goals(), "active": state.settings.get("active_goal", "")}

    @app.post("/api/goals")
    async def post_goal(req: Request):
        body = await req.json()
        name = body.get("name", "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="missing name")
        try:
            safe = create_goal(name)
        except FileExistsError:
            raise HTTPException(status_code=409, detail=f"goal {name!r} already exists")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        state.schedule_broadcast({"type": "goal_list_changed", "goals": list_goals()})
        return {"name": safe}

    @app.get("/api/goals/{name}")
    def get_goal(name: str):
        content = load_goal(name)
        return {"name": name, "content": content}

    @app.put("/api/goals/{name}")
    async def put_goal(name: str, req: Request):
        body = await req.json()
        content = body.get("content", "")
        save_goal(name, content)
        return {"name": name, "content": content}

    @app.delete("/api/goals/{name}")
    def remove_goal(name: str):
        delete_goal(name)
        # If the deleted goal was active, clear active.
        if state.settings.get("active_goal") == name:
            with state.lock:
                state.settings["active_goal"] = ""
            save_settings({"active_goal": ""})
            state.schedule_broadcast({"type": "settings_changed", "settings": dict(state.settings)})
        state.schedule_broadcast({"type": "goal_list_changed", "goals": list_goals()})
        return {"ok": True}

    @app.put("/api/active_goal")
    async def put_active_goal(req: Request):
        body = await req.json()
        name = body.get("name", "")
        if name and name not in list_goals():
            raise HTTPException(status_code=404, detail=f"goal {name!r} not found")
        with state.lock:
            state.settings["active_goal"] = name
        save_settings({"active_goal": name})
        state.schedule_broadcast({"type": "settings_changed", "settings": dict(state.settings)})
        return {"active": name, "content": load_goal(name) if name else ""}

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

        @app.get("/settings")
        def settings_page():
            return FileResponse(str(STATIC_DIR / "settings.html"))
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
