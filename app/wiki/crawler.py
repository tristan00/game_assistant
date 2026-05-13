"""2-hop BFS crawler over a MediaWiki, writing one ``.md`` per page.

Designed to run on a daemon thread. Polite (rate-limited via MediaWikiClient),
cancellable via a ``threading.Event``.

Per-page failures (HTTP 4xx/5xx, malformed responses) are logged and skipped
— a single bad page doesn't abort the crawl.
"""

import logging
import re
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from app.wiki.api_client import MediaWikiClient
from app.wiki.storage import (
    atomic_write_text,
    ensure_wiki_dirs,
    page_filename,
    pages_dir,
    save_meta,
)

logger = logging.getLogger(__name__)


class Crawler:
    """BFS over a MediaWiki, depth-limited to 2 hops from ``root_title``.

    Output:
    - ``pages/<slug>.md`` per crawled page (atomic writes).
    - ``_meta.json`` updated every ``state_save_every`` pages with progress.
    """

    def __init__(
        self,
        *,
        game_id: str,
        wiki_url: str,
        api_url: str,
        root_title: str,
        user_agent: str,
        rate_seconds: float = 1.0,
        max_depth: int = 2,
        state_save_every: int = 25,
        cancel_event: threading.Event | None = None,
        on_event: Callable[[dict], None] | None = None,
    ) -> None:
        self.game_id = game_id
        self.wiki_url = wiki_url
        self.api_url = api_url
        self.root_title = root_title
        self.user_agent = user_agent
        self.rate_seconds = float(rate_seconds)
        self.max_depth = int(max_depth)
        self.state_save_every = int(state_save_every)
        self.cancel_event = cancel_event or threading.Event()
        self.on_event = on_event or (lambda _ev: None)

        self.pages_written = 0
        self.visited: set[str] = set()
        self.inbound_counts: dict[str, int] = {}

    def cancel(self) -> None:
        self.cancel_event.set()

    def run(self) -> dict:
        ensure_wiki_dirs(self.game_id)
        # Seed visited from any existing pages so re-runs don't duplicate work.
        for p in pages_dir(self.game_id).glob("*.md"):
            self.visited.add(p.stem)
        self._save_state(state="running")
        self._emit({"type": "crawl_started", "game_id": self.game_id, "root_title": self.root_title})
        started = time.monotonic()
        frontier: deque[tuple[str, int]] = deque()
        # Seed only with the LLM-discovered root_title. There is no fallback —
        # discovery is responsible for handing the crawler a parseable seed;
        # if it isn't parseable, the crawl fails loudly and the user sees a
        # crawl_error rather than silent degradation to a generic seed.
        frontier.append((self.root_title, 0))
        logger.info("crawl %s seeded with root_title=%r", self.game_id, self.root_title)
        last_error: str | None = None
        try:
            with MediaWikiClient(
                self.api_url,
                user_agent=self.user_agent,
                rate_seconds=self.rate_seconds,
            ) as client:
                while frontier:
                    if self.cancel_event.is_set():
                        logger.info("crawl %s cancelled", self.game_id)
                        last_error = "cancelled"
                        break
                    title, depth = frontier.popleft()
                    fname = page_filename(title)
                    stem = Path(fname).stem
                    if stem in self.visited:
                        continue
                    parsed = client.parse_page(title)
                    self.visited.add(stem)
                    if parsed is None:
                        logger.info("crawl %s: skipping %r (parse_page returned None)", self.game_id, title)
                        continue
                    md = _wikitext_to_markdown(
                        title=parsed["displaytitle"],
                        source_url=f"{self.wiki_url.rstrip('/')}/{title.replace(' ', '_')}",
                        wikitext=parsed["wikitext"],
                    )
                    atomic_write_text(pages_dir(self.game_id) / fname, md)
                    self.pages_written += 1
                    for link in parsed["links"]:
                        self.inbound_counts[link] = self.inbound_counts.get(link, 0) + 1
                        if depth + 1 <= self.max_depth:
                            link_stem = Path(page_filename(link)).stem
                            if link_stem not in self.visited:
                                frontier.append((link, depth + 1))
                    if self.pages_written % self.state_save_every == 0:
                        self._save_state(state="running")
                        self._emit({
                            "type": "crawl_progress",
                            "game_id": self.game_id,
                            "pages_written": self.pages_written,
                            "frontier_size": len(frontier),
                            "current_title": title,
                        })
        except Exception as exc:
            logger.exception("crawl %s raised: %r", self.game_id, exc)
            last_error = repr(exc)
        elapsed = time.monotonic() - started
        if last_error is None and self.pages_written == 0:
            last_error = (
                f"crawl wrote 0 pages — root_title={self.root_title!r} did not parse on the wiki. "
                "Discovery handed us a seed that doesn't exist as an article. Fix discovery to "
                "produce a parseable seed, or correct the wiki entry manually."
            )
        final_state = "failed" if last_error else "done"
        self._save_state(state=final_state, last_error=last_error, elapsed_seconds=elapsed)
        logger.info(
            "crawl %s finished: state=%s pages=%d elapsed=%.1fs",
            self.game_id, final_state, self.pages_written, elapsed,
        )
        self._emit({
            "type": "crawl_done" if final_state == "done" else "crawl_error",
            "game_id": self.game_id,
            "pages_written": self.pages_written,
            "elapsed_seconds": elapsed,
            "error": last_error,
        })
        return {
            "state": final_state,
            "pages_written": self.pages_written,
            "elapsed_seconds": elapsed,
            "error": last_error,
        }

    def _emit(self, event: dict) -> None:
        try:
            self.on_event(event)
        except Exception:
            logger.exception("crawler on_event handler raised; swallowing")

    def _save_state(self, *, state: str, last_error: str | None = None, elapsed_seconds: float | None = None) -> None:
        meta = {
            "wiki_url": self.wiki_url,
            "api_url": self.api_url,
            "root_page": self.root_title,
            "crawl_state": state,
            "page_count": self.pages_written,
            "inbound_counts": self.inbound_counts,
            "last_updated_iso": datetime.now(timezone.utc).isoformat(),
        }
        if last_error is not None:
            meta["last_error"] = last_error
        if elapsed_seconds is not None:
            meta["last_elapsed_seconds"] = elapsed_seconds
        save_meta(self.game_id, meta)


_LINK_PIPE_RE = re.compile(r"\[\[([^\[\]|]+)\|([^\[\]]+)\]\]")
_LINK_PLAIN_RE = re.compile(r"\[\[([^\[\]|]+)\]\]")
_TEMPLATE_RE = re.compile(r"\{\{[^{}]*?\}\}", re.DOTALL)
_BOLD_RE = re.compile(r"'''(.+?)'''")
_ITALIC_RE = re.compile(r"''(.+?)''")
_REF_RE = re.compile(r"<ref[^>]*?(?:/>|>.*?</ref>)", re.DOTALL | re.IGNORECASE)


def _wikitext_to_markdown(*, title: str, source_url: str, wikitext: str) -> str:
    """Minimal wikitext → markdown. Whitespace-preserving, lossy on templates.

    The output is for FTS5 indexing + LLM consumption — we deliberately keep
    enough text that key terms remain searchable but don't try to render
    accurately.
    """
    text = wikitext or ""
    # Repeatedly strip nested templates ({{foo|...{{bar}}...}})
    for _ in range(5):
        new_text, n = _TEMPLATE_RE.subn("", text)
        if n == 0:
            break
        text = new_text
    text = _REF_RE.sub("", text)
    text = _LINK_PIPE_RE.sub(lambda m: m.group(2), text)
    text = _LINK_PLAIN_RE.sub(lambda m: m.group(1), text)
    text = _BOLD_RE.sub(lambda m: f"**{m.group(1)}**", text)
    text = _ITALIC_RE.sub(lambda m: f"*{m.group(1)}*", text)
    # Collapse 3+ blank lines to 2.
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return f"# {title}\n\n{source_url}\n\n{text}\n"
