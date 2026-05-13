"""End-to-end validation orchestrator.

Runs separately from main.py — boots uvicorn against app.web_server, then
exercises the game-identification → wiki-crawl → perception → reasoning
flow against the user's actual TW:WH3 window.

Usage: run after `python scripts/e2e_validate.py` returns 0, the full
flow is known to work.
"""

import argparse
import json
import logging
import sys
import threading
import time
from pathlib import Path

import httpx
import uvicorn

from app.web_server import create_app

logger = logging.getLogger("e2e")

# Configurable via CLI: which window title prefix matches the active game,
# and what question to ask once perception is ready.
DEFAULT_TITLE_PREFIX = "Total War: WARHAMMER"
DEFAULT_QUESTION = (
    "Briefly: what is currently on screen, and what's the most useful "
    "next action for me to consider?"
)


def _wait_for_state(client: httpx.Client, predicate, *, timeout: float, poll: float = 1.0, label: str) -> dict:
    """Poll /api/state until predicate(state) is True, or timeout."""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            resp = client.get("/api/state")
            resp.raise_for_status()
            state = resp.json()
        except Exception as exc:
            logger.warning("[%s] /api/state error: %r", label, exc)
            time.sleep(poll)
            continue
        if predicate(state):
            return state
        last = state
        time.sleep(poll)
    raise TimeoutError(f"timed out waiting for {label} after {timeout:.0f}s (last state snippet: {_state_snippet(last)})")


def _state_snippet(state: dict | None) -> str:
    if not state:
        return "<no state>"
    ag = state.get("active_game") or {}
    return (
        f"active_game_id={state.get('active_game_id')!r} "
        f"crawl_state={ag.get('crawl_state')!r} "
        f"page_count={ag.get('page_count')} "
        f"wiki_url={ag.get('wiki_url')!r} "
        f"in_flight={state.get('in_flight')}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--title-prefix", default=DEFAULT_TITLE_PREFIX)
    parser.add_argument("--question", default=DEFAULT_QUESTION)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--crawl-timeout", type=float, default=600.0)
    parser.add_argument("--submit-timeout", type=float, default=240.0)
    args = parser.parse_args()

    # Mirror main.py's logging — stderr + the user's rotating run.log so the
    # validation appears in the same place the user normally reads.
    from logging.handlers import RotatingFileHandler
    log_dir = Path.home() / "game_assistant" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter(
        "%(asctime)s.%(msecs)03d %(name)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    stderr_h = logging.StreamHandler(sys.stderr)
    stderr_h.setFormatter(fmt)
    file_h = RotatingFileHandler(log_dir / "run.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    file_h.setFormatter(fmt)
    logging.basicConfig(level=logging.INFO, handlers=[stderr_h, file_h])
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("anthropic").setLevel(logging.INFO)

    # Boot server in a daemon thread.
    config = uvicorn.Config(
        create_app(),
        host="127.0.0.1",
        port=args.port,
        log_level="info",
        log_config=None,
    )
    server = uvicorn.Server(config)
    server_thread = threading.Thread(target=server.run, name="uvicorn", daemon=True)
    server_thread.start()
    logger.info("uvicorn starting on 127.0.0.1:%d", args.port)
    # Wait for bind.
    deadline = time.monotonic() + 10.0
    while not server.started:
        if time.monotonic() > deadline:
            logger.error("uvicorn failed to start within 10s")
            return 1
        time.sleep(0.05)
    logger.info("uvicorn started")

    base_url = f"http://127.0.0.1:{args.port}"
    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        # 1) Discover OS windows. Find the game.
        windows = client.get("/api/windows").json()
        target = None
        for w in windows:
            if args.title_prefix.lower() in w["title"].lower():
                target = w
                break
        if target is None:
            logger.error(
                "no OS window matching %r; available: %s",
                args.title_prefix, [w["title"] for w in windows][:20],
            )
            return 1
        logger.info("found target window: hwnd=%s title=%r", target["hwnd"], target["title"])

        # 2) Set as active window. This kicks off game-id + discovery + crawl.
        r = client.put("/api/window", json={"hwnd": target["hwnd"]})
        r.raise_for_status()
        logger.info("PUT /api/window -> %s", r.json())

        # 3) Wait until the game is identified.
        logger.info("waiting for game identification…")
        state = _wait_for_state(
            client,
            lambda s: s.get("active_game_id") not in (None, "__not_a_game__"),
            timeout=120.0,
            label="game_identified",
        )
        gid = state["active_game_id"]
        logger.info("identified: game_id=%s name=%r", gid, (state.get("active_game") or {}).get("display_name"))

        # 4) Wait for crawl to finish (state == done with pages > 0).
        logger.info("waiting for crawl to complete (timeout=%.0fs)…", args.crawl_timeout)
        last_log_time = time.monotonic()
        def _crawl_done(s: dict) -> bool:
            nonlocal last_log_time
            ag = s.get("active_game") or {}
            cs = ag.get("crawl_state")
            pc = ag.get("page_count") or 0
            now = time.monotonic()
            if now - last_log_time > 5.0:
                logger.info("  crawl progress: state=%s pages=%d wiki=%r", cs, pc, ag.get("wiki_url"))
                last_log_time = now
            if cs == "failed":
                raise RuntimeError(f"crawl failed: {_state_snippet(s)}")
            if cs == "done" and pc > 0:
                return True
            return False
        state = _wait_for_state(client, _crawl_done, timeout=args.crawl_timeout, poll=2.0, label="crawl_done")
        ag = state["active_game"]
        logger.info(
            "crawl complete: pages=%d wiki_url=%s",
            ag.get("page_count"), ag.get("wiki_url"),
        )

        # 5) Confirm perception schema exists on disk.
        schema_path = Path.home() / "game_assistant" / "wikis" / gid / "_perception_schema.md"
        deadline2 = time.monotonic() + 120.0
        while not schema_path.exists():
            if time.monotonic() > deadline2:
                logger.error("perception schema not produced within 120s: %s", schema_path)
                return 1
            time.sleep(2.0)
        logger.info("perception schema ready: %s (%d bytes)", schema_path, schema_path.stat().st_size)

        # 6) Submit a question.
        logger.info("submitting question: %r", args.question)
        r = client.post("/api/submit", json={"question": args.question})
        r.raise_for_status()
        logger.info("POST /api/submit -> %s", r.json())

        # 7) Wait for completion via /api/state polling.
        logger.info("waiting for submit to complete (timeout=%.0fs)…", args.submit_timeout)
        prev_history_len = len(state.get("history") or [])
        last_log = time.monotonic()
        def _submit_done(s: dict) -> bool:
            nonlocal last_log
            hist = s.get("history") or []
            now = time.monotonic()
            if now - last_log > 5.0:
                logger.info("  in_flight=%s history_len=%d", s.get("in_flight"), len(hist))
                last_log = now
            return not s.get("in_flight") and len(hist) > prev_history_len
        try:
            final = _wait_for_state(client, _submit_done, timeout=args.submit_timeout, poll=1.5, label="submit_done")
        except TimeoutError as exc:
            logger.error("submit timed out: %s", exc)
            return 1
        last = final["history"][-1]
        logger.info("submit complete (response %d chars):", len(last.get("response", "")))
        logger.info("---- response ----")
        for line in (last.get("response") or "").splitlines():
            logger.info("  %s", line)
        logger.info("---- end response ----")

    logger.info("e2e validation complete")
    server.should_exit = True
    server_thread.join(timeout=5.0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
