"""Stage 1: per-screenshot enumeration with sidecar JSON caching.

Each PNG gets a ``<stem>.json`` sidecar containing the model's enumeration
of the game state slots. Cached: a second call for the same file is a no-op
that returns the existing sidecar (unless ``force=True``).

Verbose logging is intentional — every enumeration is logged slot-by-slot
so it's clear what the model perceived.
"""

import base64
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import anthropic

from app.image_utils import downscale_to_jpeg
from app.perception.schema import schema_hash
from app.prompts import PERCEPTION_STAGE1_PROMPT
from app.wiki.discovery import _parse_json_block

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_SECONDS = 90.0
SCHEMA_VERSION = 1


def sidecar_path(image_path: Path) -> Path:
    return image_path.with_suffix(".json")


def enumerate_images(
    image_paths: list[Path],
    *,
    api_key: str,
    model: str,
    schema_text: str,
    game_id: str | None,
    max_workers: int = 8,
    log_tag: str = "stage1",
) -> list[dict | None]:
    """Parallel stage-1 enumeration over a list of images.

    Cache hits are resolved per-image; cache misses dispatch concurrently
    via a thread pool. Anthropic API tolerates ~5–10 concurrent vision
    calls without issue for short bursts.
    """
    if not image_paths:
        return []
    workers = max(1, min(max_workers, len(image_paths)))
    t = time.monotonic()
    logger.info(
        "%s enumerate_images parallel: count=%d workers=%d",
        log_tag, len(image_paths), workers,
    )

    def _one(p: Path) -> dict | None:
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
    filled = sum(1 for r in results if r is not None)
    logger.info(
        "%s enumerate_images done in %.2fs filled=%d/%d",
        log_tag, time.monotonic() - t, filled, len(results),
    )
    return results


def enumerate_image(
    image_path: Path,
    *,
    api_key: str,
    model: str,
    schema_text: str,
    game_id: str | None,
    force: bool = False,
    log_tag: str = "stage1",
) -> dict | None:
    """Return the sidecar dict for ``image_path``, computing + writing it on miss.

    Cache key is the screenshot file. If a sidecar already exists (and
    ``force`` is False), return it without calling the LLM.
    """
    sp = sidecar_path(image_path)
    if not force and sp.exists():
        try:
            cached = json.loads(sp.read_text(encoding="utf-8"))
            logger.info(
                "%s cache hit: %s (schema_hash=%s, model=%s)",
                log_tag, image_path.name,
                cached.get("schema_hash"), cached.get("model"),
            )
            return cached
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("%s sidecar %s unreadable (%r); re-enumerating", log_tag, sp, exc)

    if not image_path.exists():
        raise FileNotFoundError(f"stage1: image missing: {image_path}")

    return _enumerate_uncached(
        image_path=image_path,
        sidecar_target=sp,
        api_key=api_key,
        model=model,
        schema_text=schema_text,
        game_id=game_id,
        log_tag=log_tag,
    )


def _enumerate_uncached(
    *,
    image_path: Path,
    sidecar_target: Path,
    api_key: str,
    model: str,
    schema_text: str,
    game_id: str | None,
    log_tag: str,
) -> dict | None:
    jpeg = downscale_to_jpeg(image_path)  # raises on disk/PIL errors — let it propagate

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
        "schema_version": SCHEMA_VERSION,
        "screenshot": image_path.name,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "game_id": game_id,
        "schema_hash": schema_hash(schema_text),
        "slots": parsed.get("slots", {}),
        "raw_text": parsed.get("raw_text", ""),
    }
    sidecar_target.write_text(json.dumps(sidecar, indent=2), encoding="utf-8")
    log_sidecar(sidecar, log_tag=log_tag)
    return sidecar


def log_sidecar(sidecar: dict, *, log_tag: str = "stage1") -> None:
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
        log_tag, sidecar.get("screenshot"),
        len(slots), filled, not_visible,
    )
