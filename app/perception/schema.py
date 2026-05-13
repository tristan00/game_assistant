"""Perception schema loader for the active game.

Returns the per-game ``_perception_schema.md`` text. Submit enforces that
the schema exists for the active game; a missing schema is a worker-
boundary error (raised by ``load_schema``), not a "compose-without"
ingredient.
"""

import logging

from app.wiki.storage import perception_schema_path

logger = logging.getLogger(__name__)


def load_schema(game_id: str) -> str:
    """Return the schema text for ``game_id``. Raise if absent or empty.

    Callers are responsible for ensuring the schema is built before invoking
    perception. Submit gates on its presence.
    """
    p = perception_schema_path(game_id)
    text = p.read_text(encoding="utf-8")
    if not text.strip():
        raise RuntimeError(f"perception schema at {p} is empty")
    logger.debug("load_schema(%s): loaded %d chars from %s", game_id, len(text), p)
    return text
