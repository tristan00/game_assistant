"""Path helpers for ``~/game_assistant/wikis/<game_id>/``.

All paths are derived from a single ``WIKIS_DIR`` module constant. Tests
monkeypatch that constant to redirect into ``tmp_path``.
"""

import json
import logging
import re
import tempfile
import time
from pathlib import Path

logger = logging.getLogger(__name__)

WIKIS_DIR = Path.home() / "game_assistant" / "wikis"

_SLUG_RE = re.compile(r"[^A-Za-z0-9]+")


def slugify(name: str) -> str:
    """Lowercase + dash-collapsed slug. Always non-empty."""
    s = _SLUG_RE.sub("-", name.lower()).strip("-")
    return s or "unknown-game"


def wiki_dir(game_id: str) -> Path:
    return WIKIS_DIR / game_id


def pages_dir(game_id: str) -> Path:
    return wiki_dir(game_id) / "pages"


def meta_path(game_id: str) -> Path:
    return wiki_dir(game_id) / "_meta.json"


def quick_ref_path(game_id: str) -> Path:
    return wiki_dir(game_id) / "_quick_ref.md"


def perception_schema_path(game_id: str) -> Path:
    return wiki_dir(game_id) / "_perception_schema.md"


def index_path(game_id: str) -> Path:
    return wiki_dir(game_id) / "index.sqlite3"


def crawl_log_path(game_id: str) -> Path:
    return wiki_dir(game_id) / "_crawl.log"


_PAGE_SLUG_RE = re.compile(r"[^A-Za-z0-9_\-]+")


def page_filename(title: str) -> str:
    """Filesystem-safe filename for a MediaWiki page title."""
    safe = _PAGE_SLUG_RE.sub("_", title).strip("_")
    return f"{safe or 'untitled'}.md"


def ensure_wiki_dirs(game_id: str) -> None:
    pages_dir(game_id).mkdir(parents=True, exist_ok=True)


def atomic_write_text(path: Path, content: str) -> None:
    """Atomic write: tmpfile + rename. Avoids half-written files on crash.

    On Windows ``os.replace`` can transiently fail with ``PermissionError``
    when the destination is briefly held by another reader (Defender real-
    time scan, file indexer, or a concurrent thread that just opened it).
    We retry with short exponential backoff before giving up.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f"{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with open(fd, "w", encoding="utf-8") as f:
            f.write(content)
        last_exc: PermissionError | None = None
        for attempt in range(6):
            try:
                Path(tmp).replace(path)
                return
            except PermissionError as exc:
                last_exc = exc
                # 50ms, 100ms, 200ms, 400ms, 800ms — totals ~1.5s before giving up.
                time.sleep(0.05 * (1 << attempt))
        assert last_exc is not None
        logger.error("atomic_write_text(%s): os.replace exhausted retries: %r", path, last_exc)
        raise last_exc
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def load_meta(game_id: str) -> dict:
    """Load ``_meta.json`` if present, else return an empty default."""
    p = meta_path(game_id)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("load_meta(%s) failed: %r", game_id, exc)
        return {}


def save_meta(game_id: str, data: dict) -> None:
    atomic_write_text(meta_path(game_id), json.dumps(data, indent=2, sort_keys=True))


def page_count_on_disk(game_id: str) -> int:
    """Number of crawled ``.md`` pages on disk for this game. 0 if the dir is missing."""
    p = pages_dir(game_id)
    if not p.exists():
        return 0
    return sum(1 for _ in p.glob("*.md"))
