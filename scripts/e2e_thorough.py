"""Thorough end-to-end validation.

Assumes the TW:WH3 corpus is already on disk (_perception_schema.md +
_quick_ref.md + pages/*.md + index.sqlite3). Runs:

A. Three-submit pass against the live LLM:
   1. State-focused question (exercises stage1+stage2+reasoning).
   2. Rules-focused question (exercises search_game_rules + corpus hits).
   3. History-using follow-up.

B. Missing-ingredient paths — submit must SUCCEED without these:
   1. Rename _perception_schema.md away → submit succeeds; logs confirm
      "perception: skipped". Restore.
   2. Rename _quick_ref.md away → submit succeeds; logs confirm quick_ref
      absent. Restore.

Exit code 0 iff every assertion passed.
"""

import argparse
import json
import logging
import shutil
import sys
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

import httpx
import uvicorn

from app.web_server import create_app

logger = logging.getLogger("e2e_thorough")

GAME_ID = "total-war-warhammer-iii"
TITLE_PREFIX = "Total War: WARHAMMER"


def _wait_for(client: httpx.Client, predicate, *, timeout: float, poll: float = 1.0, label: str) -> dict:
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
    raise TimeoutError(f"timed out waiting for {label} after {timeout:.0f}s (last: {_snippet(last)})")


def _snippet(state):
    if not state:
        return "<no state>"
    ag = state.get("active_game") or {}
    return f"crawl_state={ag.get('crawl_state')} pages={ag.get('page_count')} in_flight={state.get('in_flight')}"


def _submit(client, question: str, *, timeout: float = 240.0) -> str:
    """Submit a question. Return the response text. Raise if the submit errors."""
    pre = client.get("/api/state").json()
    pre_len = len(pre.get("history") or [])
    logger.info("submit q=%r", question[:80] + ("…" if len(question) > 80 else ""))
    r = client.post("/api/submit", json={"question": question})
    if r.status_code >= 400:
        raise RuntimeError(f"submit returned HTTP {r.status_code}: {r.text}")
    final = _wait_for(
        client,
        lambda s: not s.get("in_flight") and len(s.get("history") or []) > pre_len,
        timeout=timeout, poll=1.5, label="submit_done",
    )
    return final["history"][-1].get("response", "")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8772)
    args = parser.parse_args()

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

    # Pre-flight: corpus artifacts must be on disk for this test.
    wiki_dir = Path.home() / "game_assistant" / "wikis" / GAME_ID
    schema_path = wiki_dir / "_perception_schema.md"
    quick_ref_path = wiki_dir / "_quick_ref.md"
    pages_dir = wiki_dir / "pages"
    if not (schema_path.exists() and quick_ref_path.exists() and pages_dir.exists()):
        logger.error("pre-flight: required corpus artifacts missing under %s", wiki_dir)
        return 1
    page_count = sum(1 for _ in pages_dir.glob("*.md"))
    logger.info(
        "pre-flight: schema=%d bytes, quick_ref=%d bytes, %d pages on disk",
        schema_path.stat().st_size, quick_ref_path.stat().st_size, page_count,
    )

    # Boot server.
    config = uvicorn.Config(create_app(), host="127.0.0.1", port=args.port, log_level="info", log_config=None)
    server = uvicorn.Server(config)
    server_thread = threading.Thread(target=server.run, daemon=True, name="uvicorn")
    server_thread.start()
    deadline = time.monotonic() + 10.0
    while not server.started:
        if time.monotonic() > deadline:
            logger.error("uvicorn failed to start within 10s")
            return 1
        time.sleep(0.05)
    logger.info("uvicorn started on 127.0.0.1:%d", args.port)

    failures: list[str] = []
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{args.port}", timeout=300.0) as client:
            # Bind the window.
            windows = client.get("/api/windows").json()
            target = next((w for w in windows if TITLE_PREFIX.lower() in w["title"].lower()), None)
            if target is None:
                failures.append("no matching window for prefix " + TITLE_PREFIX)
                return 1
            client.put("/api/window", json={"hwnd": target["hwnd"]}).raise_for_status()
            _wait_for(
                client,
                lambda s: s.get("active_game_id") == GAME_ID,
                timeout=60.0, label="active_game",
            )
            logger.info("active game set to %s", GAME_ID)

            # ---- A. Three-submit pass ----
            logger.info("==== A1: state-focused question ====")
            t0 = time.monotonic()
            ans1 = _submit(client, "Briefly: what's currently on screen, and what's the most useful next action for me to consider?")
            logger.info("A1 complete in %.1fs: %d chars", time.monotonic() - t0, len(ans1))

            logger.info("==== A2: rules-focused question (should exercise search_game_rules) ====")
            t0 = time.monotonic()
            ans2 = _submit(client, "How does the Wood Elves' faction mechanic work in Total War: Warhammer III, specifically around the Oak of Ages?")
            logger.info("A2 complete in %.1fs: %d chars", time.monotonic() - t0, len(ans2))

            # Confirm search_game_rules was actually called for A2 (find it in run.log).
            log_tail = (Path.home() / "game_assistant" / "logs" / "run.log").read_text(encoding="utf-8", errors="replace")[-30000:]
            if "search_game_rules query=" not in log_tail:
                failures.append("A2: expected search_game_rules call in run.log but didn't find one")
            else:
                # And it should have returned >0 hits at least once.
                if "search_game_rules returned 0 hits" in log_tail and "search_game_rules returned " in log_tail:
                    # Check whether ANY call returned >0 hits.
                    hit_lines = [l for l in log_tail.splitlines() if "search_game_rules returned " in l]
                    nonzero = any(("returned 0 hits" not in l) for l in hit_lines)
                    if not nonzero:
                        failures.append(
                            f"A2: search_game_rules was called {len(hit_lines)} times but every call returned 0 hits — "
                            "corpus may not cover the question, or queries aren't well-formed"
                        )
                    else:
                        logger.info("A2: search_game_rules returned non-zero hits at least once — good")

            logger.info("==== A3: history-using follow-up ====")
            t0 = time.monotonic()
            ans3 = _submit(client, "Given your previous answer, what should I prioritize for my next 3 turns?")
            logger.info("A3 complete in %.1fs: %d chars", time.monotonic() - t0, len(ans3))

            # ---- B. Missing-ingredient paths — submit must SUCCEED ----
            log_path = Path.home() / "game_assistant" / "logs" / "run.log"

            logger.info("==== B1: rename _perception_schema.md away → submit must succeed ====")
            schema_backup = schema_path.with_suffix(".md.e2e_backup")
            shutil.move(str(schema_path), str(schema_backup))
            try:
                log_size_before = log_path.stat().st_size if log_path.exists() else 0
                ans_b1 = _submit(client, "Brief sanity question (perception schema missing).")
                logger.info("B1: submit succeeded (%d chars)", len(ans_b1))
                tail = log_path.read_text(encoding="utf-8", errors="replace")[log_size_before:]
                if "perception: skipped" not in tail:
                    failures.append(
                        "B1: submit succeeded but log doesn't show 'perception: skipped' — "
                        "perception may have run despite missing schema"
                    )
            except Exception as exc:
                failures.append(f"B1 failed (submit should have succeeded): {exc}")
            finally:
                shutil.move(str(schema_backup), str(schema_path))

            logger.info("==== B2: rename _quick_ref.md away → submit must succeed ====")
            quick_ref_backup = quick_ref_path.with_suffix(".md.e2e_backup")
            shutil.move(str(quick_ref_path), str(quick_ref_backup))
            try:
                log_size_before = log_path.stat().st_size if log_path.exists() else 0
                ans_b2 = _submit(client, "Brief sanity question (quick_ref missing).")
                logger.info("B2: submit succeeded (%d chars)", len(ans_b2))
                tail = log_path.read_text(encoding="utf-8", errors="replace")[log_size_before:]
                if "quick_ref=absent" not in tail:
                    failures.append(
                        "B2: submit succeeded but log doesn't show 'quick_ref=absent' — "
                        "ingredient tracking may not be wired"
                    )
            except Exception as exc:
                failures.append(f"B2 failed (submit should have succeeded): {exc}")
            finally:
                shutil.move(str(quick_ref_backup), str(quick_ref_path))

            # Final sanity: post-restore submit should succeed.
            logger.info("==== sanity: post-restore submit must succeed ====")
            try:
                ans4 = _submit(client, "One-sentence sanity check after restore.")
                logger.info("post-restore sanity: %d chars", len(ans4))
            except Exception as exc:
                failures.append(f"post-restore sanity failed: {exc}")

    finally:
        server.should_exit = True
        server_thread.join(timeout=5.0)

    if failures:
        logger.error("==== thorough e2e: %d failures ====", len(failures))
        for f in failures:
            logger.error("  - %s", f)
        return 1
    logger.info("==== thorough e2e: ALL PASSED ====")
    return 0


if __name__ == "__main__":
    sys.exit(main())
