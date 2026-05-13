"""Post-crawl LLM passes producing ``_quick_ref.md``.

Two-pass: (1) LLM picks the ~30 most relevant page titles from the full
crawled set, (2) LLM reads those pages and writes the compact reference.
"""

import logging
import time
from pathlib import Path

import anthropic

from app.prompts import QUICK_REF_PROMPT, QUICK_REF_TITLE_PICK_PROMPT
from app.wiki.discovery import _parse_json_block
from app.wiki.storage import atomic_write_text, load_meta, pages_dir, quick_ref_path

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_SECONDS = 240.0
_MAX_INPUT_CHARS = 240_000  # ~60–80k tokens, leaves room for response


def build_quick_ref(
    game_id: str,
    *,
    api_key: str,
    model: str,
    log_tag: str = "quick_ref",
) -> Path | None:
    """Build and write ``_quick_ref.md``. Returns its path on success, else None."""
    meta = load_meta(game_id)
    pdir = pages_dir(game_id)
    if not pdir.exists():
        logger.info("%s: no pages dir for %s; skipping", log_tag, game_id)
        return None

    # Map title -> path. Use the same convention as page_filename's inverse:
    # the stem with underscores becomes spaces in the title.
    md_by_title: dict[str, Path] = {}
    for md in pdir.glob("*.md"):
        title = md.stem.replace("_", " ")
        md_by_title[title] = md
    if not md_by_title:
        logger.info("%s: no pages on disk for %s; skipping", log_tag, game_id)
        return None
    all_titles = sorted(md_by_title.keys())
    logger.info("%s: %d titles available for %s", log_tag, len(all_titles), game_id)

    client = anthropic.Anthropic(api_key=api_key, timeout=_REQUEST_TIMEOUT_SECONDS)
    game_display = meta.get("display_name") or game_id

    # Pass 1: LLM picks which titles to read.
    selected_titles = _pick_titles(client, model, game_display, meta.get("wiki_url"), all_titles, log_tag)
    if selected_titles is None:
        return None
    logger.info("%s: title-pick selected %d titles", log_tag, len(selected_titles))

    selected_paths: list[Path] = []
    for title in selected_titles:
        md = md_by_title.get(title)
        if md is None:
            logger.info("%s: picked title not on disk: %r", log_tag, title)
            continue
        selected_paths.append(md)
    if not selected_paths:
        logger.warning("%s: title-pick returned no paths that exist on disk", log_tag)
        return None

    # Pass 2: read selected pages, ask LLM to write the reference.
    parts: list[str] = []
    total = 0
    for md in selected_paths:
        chunk = md.read_text(encoding="utf-8")
        if total + len(chunk) > _MAX_INPUT_CHARS:
            chunk = chunk[: _MAX_INPUT_CHARS - total]
            if chunk:
                parts.append(chunk)
            break
        parts.append(chunk)
        total += len(chunk)
    body = "\n\n---\n\n".join(parts)
    user_text = (
        f"Game: {game_display}\n"
        f"Source wiki: {meta.get('wiki_url')}\n\n"
        f"Pages follow, separated by ---:\n\n{body}"
    )
    logger.info(
        "%s: write-pass calling messages.create model=%s pages=%d input_chars=%d",
        log_tag, model, len(parts), len(body),
    )
    t = time.monotonic()
    try:
        response = client.messages.create(
            model=model,
            system=QUICK_REF_PROMPT,
            max_tokens=4096,
            messages=[{"role": "user", "content": user_text}],
        )
    except anthropic.APIError as exc:
        logger.error("%s: write-pass Anthropic call failed: %r", log_tag, exc)
        return None
    text = "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
    if not text.strip():
        logger.warning("%s: write-pass empty response", log_tag)
        return None
    logger.info(
        "%s: write-pass returned in %.2fs chars=%d",
        log_tag, time.monotonic() - t, len(text),
    )
    out = quick_ref_path(game_id)
    atomic_write_text(out, text)
    return out


def _pick_titles(
    client: anthropic.Anthropic,
    model: str,
    game_display: str,
    wiki_url: str | None,
    all_titles: list[str],
    log_tag: str,
) -> list[str] | None:
    """LLM call 1: pick ~30 titles to read."""
    titles_block = "\n".join(f"- {t}" for t in all_titles)
    user_text = (
        f"Game: {game_display}\n"
        f"Source wiki: {wiki_url}\n"
        f"Available titles ({len(all_titles)}):\n{titles_block}"
    )
    logger.info(
        "%s: title-pick calling messages.create model=%s titles=%d input_chars=%d",
        log_tag, model, len(all_titles), len(user_text),
    )
    t = time.monotonic()
    try:
        response = client.messages.create(
            model=model,
            system=QUICK_REF_TITLE_PICK_PROMPT,
            max_tokens=2048,
            messages=[{"role": "user", "content": user_text}],
        )
    except anthropic.APIError as exc:
        logger.error("%s: title-pick Anthropic call failed: %r", log_tag, exc)
        return None
    text = "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
    logger.info(
        "%s: title-pick returned in %.2fs stop_reason=%s chars=%d",
        log_tag, time.monotonic() - t, response.stop_reason, len(text),
    )
    parsed = _parse_json_block(text)
    if parsed is None:
        logger.warning("%s: title-pick parse failed", log_tag)
        return None
    titles = parsed.get("titles")
    if not isinstance(titles, list):
        logger.warning("%s: title-pick JSON has no titles list: %r", log_tag, parsed)
        return None
    return [str(t) for t in titles if isinstance(t, str)]
