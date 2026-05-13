import base64
import logging
import time
from collections.abc import Callable
from pathlib import Path

import anthropic

from app.image_utils import downscale_to_jpeg
from app.llm_tools import format_search_game_rules_result, search_game_rules_tool
from app.prompts import CORPUS_SEARCH_NOTE, GOAL_INSTRUCTIONS, SYNTHESIS_NOTE, SYSTEM_PROMPT

logger = logging.getLogger(__name__)

MAX_TOKENS = 2048
REQUEST_TIMEOUT_SECONDS = 180.0
MAX_IMAGES_PER_REQUEST = 20  # Anthropic vision API limit


def _block_to_dict(block) -> dict:
    """Convert an SDK response block to a plain dict for the next messages.create call."""
    if hasattr(block, "model_dump"):
        return block.model_dump(exclude_none=True)
    btype = getattr(block, "type", None)
    if btype == "text":
        return {"type": "text", "text": block.text}
    if btype == "tool_use":
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    return dict(block)


def run_completion(
    *,
    api_key: str,
    model: str,
    history: list[dict[str, str]],
    goal_text: str,
    question: str,
    image_paths: list[Path],
    quick_ref_text: str | None = None,
    synthesis_text: str | None = None,
    search_game_rules_handler: Callable[[str, int], list[dict]],
    enable_prompt_cache: bool = True,
    client_tool_max_iters: int = 6,
    log_tag: str = "completion",
) -> str:
    """Synchronous Anthropic completion. UI-agnostic — used by the web backend.

    Builds messages + system prompt, injects the per-game quick-ref with a
    prompt-cache breakpoint, registers ``search_game_rules`` (the only
    information tool), and runs a small tool-use loop to resolve queries.
    """
    has_synthesis = bool(synthesis_text and synthesis_text.strip())

    # When synthesis is provided, the reasoning call only needs the latest
    # screenshot as a visual fallback — synthesis is the primary state source.
    if has_synthesis and len(image_paths) > 1:
        logger.info(
            "%s: synthesis provided; sending only latest image (%s) of %d",
            log_tag, image_paths[-1].name, len(image_paths),
        )
        image_paths = image_paths[-1:]
    elif len(image_paths) > MAX_IMAGES_PER_REQUEST:
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

    if has_synthesis:
        current.append({
            "type": "text",
            "text": (
                "## Pre-computed scene synthesis (PRIMARY STATE — the image above is a visual fallback only)\n\n"
                + synthesis_text.strip()
            ),
        })
    current.append({"type": "text", "text": question})
    messages.append({"role": "user", "content": current})

    # Build dynamic system prompt.
    system_text = SYSTEM_PROMPT
    if has_synthesis:
        system_text += "\n\n" + SYNTHESIS_NOTE
    if quick_ref_text and quick_ref_text.strip():
        system_text += (
            "\n\n---\n\n## Active game quick reference\n\n" + quick_ref_text.strip()
        )
    system_text += "\n\n" + CORPUS_SEARCH_NOTE
    goal = goal_text.strip() if goal_text else ""
    if goal:
        system_text += (
            f"\n\n---\n\n{GOAL_INSTRUCTIONS}\n"
            f"--- Goal (begin) ---\n"
            f"{goal}\n"
            f"--- Goal (end) ---"
        )
        logger.info(
            "%s goal in effect: %d chars, %d lines",
            log_tag, len(goal), goal.count("\n") + 1,
        )

    # Wrap in a list with a single cache_control breakpoint for prompt caching.
    if enable_prompt_cache:
        system_param: object = [{
            "type": "text",
            "text": system_text,
            "cache_control": {"type": "ephemeral"},
        }]
    else:
        system_param = system_text

    tools = [search_game_rules_tool()]

    base_kwargs = dict(
        model=model,
        system=system_param,
        max_tokens=MAX_TOKENS,
        tools=tools,
    )

    prior_response_chars = sum(len(t["response"]) for t in history)
    logger.info(
        "%s calling messages.create model=%s history_turns=%d prior_response_chars=%d system_chars=%d prompt_cache=%s",
        log_tag, model, len(history), prior_response_chars, len(system_text),
        enable_prompt_cache,
    )

    response = None
    for iteration in range(max(1, int(client_tool_max_iters))):
        t_call = time.monotonic()
        response = client.messages.create(messages=messages, **base_kwargs)
        elapsed = time.monotonic() - t_call
        logger.info(
            "%s messages.create iter=%d returned in %.2fs stop_reason=%s usage=%s",
            log_tag, iteration, elapsed, response.stop_reason, getattr(response, "usage", None),
        )

        if response.stop_reason != "tool_use":
            break

        # Client-side tool use loop. Append the assistant turn, dispatch each
        # tool_use, append tool_result blocks, then re-call.
        messages.append({"role": "assistant", "content": [_block_to_dict(b) for b in response.content]})
        tool_results: list[dict] = []
        for block in response.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            name = getattr(block, "name", None)
            tu_id = getattr(block, "id", None)
            tu_input = getattr(block, "input", None) or {}
            if name == "search_game_rules":
                query = str(tu_input.get("query", "")).strip()
                max_results = int(tu_input.get("max_results", 5))
                logger.info("%s search_game_rules query=%r max_results=%d", log_tag, query, max_results)
                results = search_game_rules_handler(query, max_results)
                logger.info("%s search_game_rules returned %d hits", log_tag, len(results))
                content_text = format_search_game_rules_result(results, query)
            else:
                logger.warning("%s unknown client tool %r; returning empty result", log_tag, name)
                content_text = f"(no handler for tool {name!r})"
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tu_id,
                "content": content_text,
            })
        if not tool_results:
            logger.warning("%s stop_reason=tool_use but no tool_use blocks in content; exiting loop", log_tag)
            break
        messages.append({"role": "user", "content": tool_results})
    else:
        logger.warning(
            "%s tool-use loop hit max_iters=%d; returning current text", log_tag, client_tool_max_iters,
        )

    if response is None:
        return ""
    return "".join(block.text for block in response.content if getattr(block, "type", None) == "text")
