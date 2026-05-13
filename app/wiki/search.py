"""sqlite FTS5 index + search over the per-game corpus.

Indexes ``~/game_assistant/wikis/<game_id>/pages/*.md`` into
``~/game_assistant/wikis/<game_id>/index.sqlite3``. Search returns title,
url, and BM25-ranked snippets.
"""

import logging
import sqlite3
from pathlib import Path

from app.wiki.storage import index_path, pages_dir

logger = logging.getLogger(__name__)


_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS pages USING fts5(
    title,
    url UNINDEXED,
    content,
    tokenize = 'porter unicode61'
);
"""


def _connect(game_id: str) -> sqlite3.Connection:
    path = index_path(game_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.executescript(_SCHEMA)
    return conn


def _parse_md(path: Path) -> tuple[str, str, str]:
    """Parse a crawler-written markdown file.

    Format produced by the crawler:
        # <title>

        <source_url>

        <body...>
    """
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    title = lines[0].lstrip("# ").strip() if lines and lines[0].startswith("#") else path.stem
    url = ""
    body_start = 1
    # Find the next non-empty line for url, then the body starts after it.
    for i in range(1, len(lines)):
        if lines[i].strip():
            url = lines[i].strip()
            body_start = i + 1
            break
    body = "\n".join(lines[body_start:]).strip()
    return title, url, body


def index_page_count(game_id: str) -> int:
    """How many rows the FTS5 index currently holds. 0 if the index doesn't exist.

    Used at submit time to detect a stale index: if pages on disk outnumber
    rows in the index, rebuild before consulting the corpus.
    """
    p = index_path(game_id)
    if not p.exists():
        return 0
    try:
        conn = sqlite3.connect(str(p))
    except sqlite3.OperationalError as exc:
        logger.warning("index_page_count(%s): connect failed: %r", game_id, exc)
        return 0
    try:
        try:
            row = conn.execute("SELECT COUNT(*) FROM pages").fetchone()
        except sqlite3.OperationalError:
            return 0
        return int(row[0]) if row else 0
    finally:
        conn.close()


def build_index(game_id: str) -> int:
    """Re-build the FTS5 index from disk. Returns number of pages indexed."""
    pdir = pages_dir(game_id)
    if not pdir.exists():
        logger.info("build_index %s: no pages dir at %s; nothing to index", game_id, pdir)
        return 0
    conn = _connect(game_id)
    try:
        with conn:
            conn.execute("DELETE FROM pages")
            count = 0
            for md in sorted(pdir.glob("*.md")):
                title, url, body = _parse_md(md)
                conn.execute(
                    "INSERT INTO pages(title, url, content) VALUES(?, ?, ?)",
                    (title, url, body),
                )
                count += 1
        logger.info("build_index %s: indexed %d pages", game_id, count)
        return count
    finally:
        conn.close()


def search(game_id: str, query: str, max_results: int = 5) -> list[dict]:
    """BM25-ranked FTS5 search. Returns ``[{title, url, snippet}, ...]``."""
    p = index_path(game_id)
    if not p.exists():
        logger.info("search %s: no index at %s; returning []", game_id, p)
        return []
    safe_query = _sanitize_query(query)
    if not safe_query:
        return []
    conn = sqlite3.connect(str(p))
    try:
        # FTS5 snippet() max token count is 64 — use the max so the model gets
        # several sentences of context per hit instead of one phrase. Cuts the
        # number of follow-up search_game_rules calls the model needs.
        cur = conn.execute(
            """
            SELECT title, url, snippet(pages, 2, '[', ']', '…', 64)
            FROM pages
            WHERE pages MATCH ?
            ORDER BY bm25(pages)
            LIMIT ?
            """,
            (safe_query, int(max_results)),
        )
        rows = cur.fetchall()
    except sqlite3.OperationalError as exc:
        logger.warning("search %s: query failed: %r (query=%r)", game_id, exc, safe_query)
        return []
    finally:
        conn.close()
    return [{"title": t, "url": u, "snippet": s} for t, u, s in rows]


def _sanitize_query(query: str) -> str:
    """FTS5 has its own query syntax — we quote each word to avoid syntax errors.

    Empty / whitespace-only inputs yield an empty string (no search).
    """
    words = [w for w in query.split() if w.strip()]
    if not words:
        return ""
    # Quote each word with double quotes; FTS5 treats them as literal phrases.
    return " ".join(f'"{w}"' for w in words)
