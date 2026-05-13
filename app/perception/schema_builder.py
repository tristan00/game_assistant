"""Build a per-game perception schema by extending the base with game-specific slots.

Triggered post-crawl after ``_quick_ref.md`` is generated. The output lands
at ``~/game_assistant/wikis/<game_id>/_perception_schema.md`` and is
consumed by both stage 1 and stage 2 of the perception pipeline.
"""

import logging
import time
from pathlib import Path

import anthropic

from app.prompts import PERCEPTION_SCHEMA_BUILDER_PROMPT
from app.wiki.storage import atomic_write_text, perception_schema_path, quick_ref_path

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_SECONDS = 180.0


def build_perception_schema(
    game_id: str,
    *,
    api_key: str,
    model: str,
    log_tag: str = "perception_schema",
) -> Path | None:
    """Run the LLM pass over ``_quick_ref.md`` and write ``_perception_schema.md``.

    Returns the output path on success, ``None`` if the quick-ref is missing
    or the API call fails. Always idempotent (overwrites previous output).
    """
    qp = quick_ref_path(game_id)
    if not qp.exists():
        logger.info("%s: no quick_ref for %s; skipping schema build", log_tag, game_id)
        return None
    try:
        quick_ref = qp.read_text(encoding="utf-8")
    except OSError as exc:
        logger.error("%s: failed to read quick_ref for %s: %r", log_tag, game_id, exc)
        return None

    user_text = (
        f"Game: {game_id}\n\n"
        "Quick reference (derive the slot list ENTIRELY from this):\n\n"
        + quick_ref
    )
    logger.info(
        "%s: calling messages.create model=%s game_id=%s quick_ref_chars=%d",
        log_tag, model, game_id, len(quick_ref),
    )
    client = anthropic.Anthropic(api_key=api_key, timeout=_REQUEST_TIMEOUT_SECONDS)
    t = time.monotonic()
    try:
        response = client.messages.create(
            model=model,
            system=PERCEPTION_SCHEMA_BUILDER_PROMPT,
            max_tokens=4096,
            messages=[{"role": "user", "content": user_text}],
        )
    except anthropic.APIError as exc:
        logger.error("%s: Anthropic call failed: %r", log_tag, exc)
        return None
    text = "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
    if not text.strip():
        logger.warning("%s: empty response for %s", log_tag, game_id)
        return None
    logger.info(
        "%s: messages.create returned in %.2fs chars=%d",
        log_tag, time.monotonic() - t, len(text),
    )
    out = perception_schema_path(game_id)
    atomic_write_text(out, text)
    return out
