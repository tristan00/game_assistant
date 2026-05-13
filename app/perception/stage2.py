"""Stage 2: text-only synthesis from cached stage-1 enumerations.

Produces a unified current-state markdown report consumed as primary state
by the reasoning call downstream. No images are sent here — stage 1
already converted each screenshot's state to structured JSON. Re-uploading
the images would defeat the per-image cache and waste tokens.

The reasoning call gets the latest image as a visual fallback for things
the schema-based enumeration may have missed.
"""

import json
import logging
import time

import anthropic

from app.prompts import PERCEPTION_STAGE2_PROMPT

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_SECONDS = 120.0


def synthesize(
    *,
    sidecars: list[dict | None],
    image_filenames: list[str],
    schema_text: str,
    question: str,
    api_key: str,
    model: str,
    log_tag: str = "stage2",
) -> str | None:
    """Text-only synthesis over N stage-1 enumerations.

    ``sidecars`` and ``image_filenames`` are positional-aligned. A None in
    ``sidecars`` means stage-1 failed for that frame — it gets a placeholder
    in the prompt so the model knows that frame's state is unknown.
    """
    if not sidecars:
        logger.info("%s: no sidecars; skipping", log_tag)
        return None
    if len(sidecars) != len(image_filenames):
        raise ValueError(
            f"sidecars/image_filenames length mismatch: {len(sidecars)} vs {len(image_filenames)}"
        )

    hint = (question or "").strip()
    intro_lines = [f"Synthesize {len(sidecars)} stage-1 enumerations, oldest first."]
    if hint:
        intro_lines.append(f"Emphasize fields relevant to the user's question: {hint!r}.")
    else:
        intro_lines.append("No specific question hint — surface what looks most decision-relevant.")
    intro = "\n".join(intro_lines)

    frame_sections: list[str] = []
    for i, (filename, sidecar) in enumerate(zip(image_filenames, sidecars), start=1):
        if sidecar is None:
            frame_sections.append(
                f"## Frame {i}: {filename}\n"
                "(stage-1 enumeration unavailable for this frame — treat its state as unknown)"
            )
        else:
            slots = sidecar.get("slots", {})
            raw = sidecar.get("raw_text", "") or ""
            body = f"```json\n{json.dumps(slots, indent=2)}\n```"
            if raw.strip():
                body += f"\n\nraw_text: {raw!r}"
            frame_sections.append(f"## Frame {i}: {filename}\n{body}")

    outro = "Produce the State table, Temporal narrative, and Emphasis section per the schema."
    user_text = intro + "\n\n" + "\n\n".join(frame_sections) + "\n\n" + outro

    logger.info(
        "%s synthesize start: sidecars=%d sidecars_missing=%d model=%s prompt_chars=%d hint=%r",
        log_tag, len(sidecars),
        sum(1 for s in sidecars if s is None), model, len(user_text), hint[:80],
    )
    client = anthropic.Anthropic(api_key=api_key, timeout=_REQUEST_TIMEOUT_SECONDS)
    system_text = PERCEPTION_STAGE2_PROMPT + "\n\n---\n\n" + schema_text
    t = time.monotonic()
    try:
        response = client.messages.create(
            model=model,
            system=[{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}],
            max_tokens=2048,
            messages=[{"role": "user", "content": user_text}],
        )
    except anthropic.APIError as exc:
        logger.error("%s Anthropic call failed: %r", log_tag, exc)
        return None
    elapsed = time.monotonic() - t
    text = "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
    logger.info(
        "%s synthesize done: %.2fs out_chars=%d stop_reason=%s usage=%s",
        log_tag, elapsed, len(text), response.stop_reason, getattr(response, "usage", None),
    )
    if not text.strip():
        logger.warning("%s empty synthesis response", log_tag)
        return None
    # Log the synthesis output for visibility into what the reasoning step
    # will receive. Verbose by design — single multi-line block.
    for line in text.splitlines():
        if line.strip():
            logger.info("%s   %s", log_tag, line)
    return text
