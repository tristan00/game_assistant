"""Stage 1: per-screenshot enumeration.

For each PNG, the model returns the game state slots named by the per-game
perception schema. Run in parallel across the batch. No on-disk caching —
every submit calls the LLM fresh per image.

Verbose logging is intentional — every enumeration is logged slot-by-slot
so it's clear what the model perceived.
"""

import base64
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import anthropic

from app.image_utils import downscale_to_jpeg
from app.prompts import PERCEPTION_STAGE1_PROMPT
from app.wiki.discovery import _parse_json_block

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_SECONDS = 90.0


def enumerate_images(
    image_paths: list[Path],
    *,
    api_key: str,
    model: str,
    schema_text: str,
    game_id: str | None,
    max_workers: int = 8,
    log_tag: str = "stage1",
) -> list[dict]:
    """Parallel stage-1 enumeration over a list of images.

    Anthropic API tolerates ~5–10 concurrent vision calls without issue for
    short bursts. Any image failure raises and aborts the batch.
    """
    if not image_paths:
        return []
    workers = max(1, min(max_workers, len(image_paths)))
    t = time.monotonic()
    logger.info(
        "%s enumerate_images parallel: count=%d workers=%d",
        log_tag, len(image_paths), workers,
    )

    def _one(p: Path) -> dict:
        return enumerate_image(
            p,
            api_key=api_key,
            model=model,
            schema_text=schema_text,
            game_id=game_id,
            log_tag=log_tag,
        )

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="stage1") as ex:
        results = list(ex.map(_one, image_paths))
    logger.info(
        "%s enumerate_images done in %.2fs count=%d",
        log_tag, time.monotonic() - t, len(results),
    )
    return results


def enumerate_image(
    image_path: Path,
    *,
    api_key: str,
    model: str,
    schema_text: str,
    game_id: str | None,
    log_tag: str = "stage1",
) -> dict:
    """Return ``{"slots": {...}, "raw_text": str}`` for ``image_path``."""
    jpeg = downscale_to_jpeg(image_path)
    system_text = PERCEPTION_STAGE1_PROMPT + "\n\n---\n\n" + schema_text
    messages = [{
        "role": "user",
        "content": [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": base64.b64encode(jpeg).decode("ascii"),
                },
            },
            {"type": "text", "text": "Enumerate per the schema. Return JSON only."},
        ],
    }]
    logger.info(
        "%s enumerate_image start: file=%s game_id=%s model=%s",
        log_tag, image_path.name, game_id, model,
    )
    client = anthropic.Anthropic(api_key=api_key, timeout=_REQUEST_TIMEOUT_SECONDS)
    t = time.monotonic()
    response = client.messages.create(
        model=model,
        system=[{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}],
        max_tokens=2048,
        messages=messages,
    )
    elapsed = time.monotonic() - t
    text = "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
    logger.info(
        "%s enumerate_image LLM done: %s in %.2fs stop_reason=%s usage=%s",
        log_tag, image_path.name, elapsed, response.stop_reason, getattr(response, "usage", None),
    )
    parsed = _parse_json_block(text)
    if parsed is None:
        raise RuntimeError(
            f"stage1: model returned no parseable JSON for {image_path.name}; "
            f"raw_first={text[:200]!r}"
        )

    sidecar = {
        "slots": parsed.get("slots", {}),
        "raw_text": parsed.get("raw_text", ""),
    }
    log_sidecar(sidecar, filename=image_path.name, log_tag=log_tag)
    return sidecar


def log_sidecar(sidecar: dict, *, filename: str, log_tag: str = "stage1") -> None:
    """Log every perception slot the model returned at INFO.

    Slot names come from the per-game `_perception_schema.md` — we don't have
    a fixed list to compare against, so we just iterate what the model
    returned.
    """
    slots = sidecar.get("slots", {}) or {}
    filled = 0
    not_visible = 0
    for name, slot in slots.items():
        if not isinstance(slot, dict):
            logger.warning("%s   %-30s : MALFORMED slot (not a dict): %r", log_tag, name, slot)
            continue
        val = slot.get("value")
        conf = slot.get("confidence")
        if val == "not visible" or val is None:
            logger.info("%s   %-30s : not visible", log_tag, name)
            not_visible += 1
            continue
        filled += 1
        if isinstance(val, list):
            preview = ", ".join(str(v)[:60] for v in val[:3])
            if len(val) > 3:
                preview += f", … (+{len(val) - 3} more)"
            val_str = f"[{len(val)}] {preview}"
        else:
            s = str(val)
            val_str = (s[:160] + "…") if len(s) > 160 else s
        logger.info(
            "%s   %-30s : (conf=%s) %s",
            log_tag, name,
            ("?" if conf is None else f"{float(conf):.2f}"),
            val_str,
        )
    raw = sidecar.get("raw_text") or ""
    if raw.strip():
        preview = raw if len(raw) <= 200 else raw[:200] + "…"
        logger.info("%s   raw_text: %s", log_tag, preview.replace("\n", " | "))
    logger.info(
        "%s sidecar summary: file=%s slots=%d filled=%d not_visible=%d",
        log_tag, filename,
        len(slots), filled, not_visible,
    )
