"""Perception schema loader.

Returns the per-game ``_perception_schema.md`` text for the active game, or
``None`` when no schema exists yet. The schema is an *optional ingredient* in
the submit prompt: when present, stage1+stage2 perception runs; when absent,
the reasoning call sees the raw screenshots directly. Either way the user
gets an answer — missing schema is surfaced via the UI status strip, never as
a submit failure.
"""

import hashlib
import logging

from app.wiki.storage import perception_schema_path

logger = logging.getLogger(__name__)


def load_schema(game_id: str | None) -> str | None:
    """Return the schema text for ``game_id``, or ``None`` if unavailable.

    Returns ``None`` when:
    - ``game_id`` is ``None`` (no active game)
    - the per-game schema file doesn't exist (post-crawl chain hasn't built it)
    - the file exists but is empty

    Callers compose the submit prompt with whatever ingredients exist; ``None``
    here just means perception is skipped this turn.
    """
    if not game_id:
        logger.debug("load_schema: no game_id, returning None")
        return None
    p = perception_schema_path(game_id)
    if not p.exists():
        logger.debug("load_schema(%s): no schema file at %s", game_id, p)
        return None
    text = p.read_text(encoding="utf-8")
    if not text.strip():
        logger.debug("load_schema(%s): schema file at %s is empty", game_id, p)
        return None
    logger.debug("load_schema(%s): loaded %d chars from %s", game_id, len(text), p)
    return text


def schema_hash(text: str) -> str:
    """Stable short hash for schema-versioning sidecars."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
