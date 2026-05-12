import base64
import logging
import time
from pathlib import Path

import anthropic

from app.image_utils import downscale_to_jpeg
from app.prompts import STRATEGY_INSTRUCTIONS, SYSTEM_PROMPT

logger = logging.getLogger(__name__)

MAX_TOKENS = 2048
REQUEST_TIMEOUT_SECONDS = 180.0  # web search can add real latency
MAX_IMAGES_PER_REQUEST = 20  # Anthropic vision API limit


def _web_search_tool(max_uses: int) -> dict | None:
    """Build the server-side web search tool definition with the user's cap.

    Setting max_uses <= 0 returns None so the tool can be omitted entirely.
    """
    if max_uses <= 0:
        return None
    return {
        "type": "web_search_20260209",
        "name": "web_search",
        "max_uses": max_uses,
    }


def run_completion(
    *,
    api_key: str,
    model: str,
    history: list[dict[str, str]],
    strategy_text: str,
    question: str,
    image_paths: list[Path],
    web_search_max_uses: int,
    log_tag: str = "completion",
) -> str:
    """Synchronous Anthropic completion. UI-agnostic — used by both Qt worker and web backend.

    Encodes images, builds messages and dynamic system prompt, makes the API call,
    logs progress and any web_search invocations, and returns the final text.
    """
    web_search_max_uses = max(0, int(web_search_max_uses))
    if len(image_paths) > MAX_IMAGES_PER_REQUEST:
        logger.warning(
            "%s: image_paths length %d exceeds %d; trimming to most recent %d",
            log_tag, len(image_paths), MAX_IMAGES_PER_REQUEST, MAX_IMAGES_PER_REQUEST,
        )
        image_paths = image_paths[-MAX_IMAGES_PER_REQUEST:]

    logger.debug("%s building assistant client (timeout=%.1fs)", log_tag, REQUEST_TIMEOUT_SECONDS)
    client = anthropic.Anthropic(api_key=api_key, timeout=REQUEST_TIMEOUT_SECONDS)

    messages: list[dict] = []
    for turn in history:
        messages.append({"role": "user", "content": turn["question"]})
        messages.append({"role": "assistant", "content": turn["response"]})

    current: list[dict] = []
    t_encode = time.monotonic()
    total_jpeg_bytes = 0
    for path in image_paths:
        jpeg = downscale_to_jpeg(path)
        total_jpeg_bytes += len(jpeg)
        current.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": base64.b64encode(jpeg).decode("ascii"),
                },
            }
        )
    logger.info(
        "%s encoded %d images in %.2fs, total JPEG bytes=%d",
        log_tag, len(image_paths), time.monotonic() - t_encode, total_jpeg_bytes,
    )

    current.append({"type": "text", "text": question})
    messages.append({"role": "user", "content": current})

    # Build dynamic system prompt — append Strategic Context if one is active.
    system_prompt = SYSTEM_PROMPT
    strategy = strategy_text.strip() if strategy_text else ""
    if strategy:
        system_prompt += (
            f"\n\n---\n\n{STRATEGY_INSTRUCTIONS}\n"
            f"--- Strategic Context (begin) ---\n"
            f"{strategy}\n"
            f"--- Strategic Context (end) ---"
        )
        logger.info(
            "%s strategy in effect: %d chars, %d lines",
            log_tag, len(strategy), strategy.count("\n") + 1,
        )

    tool_def = _web_search_tool(web_search_max_uses)
    kwargs = dict(
        model=model,
        system=system_prompt,
        max_tokens=MAX_TOKENS,
        messages=messages,
    )
    if tool_def is not None:
        kwargs["tools"] = [tool_def]

    prior_response_chars = sum(len(t["response"]) for t in history)
    logger.info(
        "%s calling messages.create model=%s history_turns=%d prior_response_chars=%d system_chars=%d web_search_max=%d",
        log_tag, model, len(history), prior_response_chars, len(system_prompt), web_search_max_uses,
    )
    t_call = time.monotonic()
    response = client.messages.create(**kwargs)
    elapsed = time.monotonic() - t_call
    logger.info(
        "%s messages.create returned in %.2fs stop_reason=%s usage=%s",
        log_tag, elapsed, response.stop_reason, getattr(response, "usage", None),
    )

    # Log any web searches the model performed for visibility.
    search_count = 0
    for block in response.content:
        btype = getattr(block, "type", None)
        if btype == "server_tool_use" and getattr(block, "name", None) == "web_search":
            search_count += 1
            query = "?"
            inp = getattr(block, "input", None)
            if isinstance(inp, dict):
                query = inp.get("query", "?")
            logger.info("%s web_search #%d: %r", log_tag, search_count, query)
    if search_count:
        logger.info("%s total web searches this turn: %d", log_tag, search_count)

    return "".join(block.text for block in response.content if getattr(block, "type", None) == "text")
