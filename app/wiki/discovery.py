"""LLM-driven wiki discovery.

Asks Anthropic (with the server-side ``web_search`` tool) to identify the
canonical community wiki for a game and return a structured JSON answer.
Then probes the proposed MediaWiki endpoint to validate it.

No curated hardcoded list, no hand-tuned scoring formula — the model picks
from real search results and we validate by hitting the actual API.
"""

import json
import logging
import re
import time
from dataclasses import asdict, dataclass

import anthropic

from app.prompts import WIKI_DISCOVERY_PROMPT
from app.wiki.api_client import MediaWikiClient

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT_SECONDS = 180.0
_WEB_SEARCH_MAX_USES = 5


@dataclass
class WikiCandidate:
    wiki_url: str
    api_url: str
    root_page: str
    sitename: str
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


def discover_wiki(
    game_name: str,
    *,
    api_key: str,
    model: str,
    user_agent: str,
    rate_seconds: float = 1.0,
    log_tag: str = "wiki_discovery",
) -> WikiCandidate | None:
    """Return a validated wiki candidate for ``game_name``, or None.

    Two-stage: (1) Anthropic call with ``web_search`` enabled returns JSON,
    (2) we probe the proposed ``api_url`` via MediaWiki ``siteinfo`` to confirm
    it is actually a live MediaWiki installation.
    """
    proposal = _ask_llm(
        game_name,
        api_key=api_key,
        model=model,
        log_tag=log_tag,
    )
    if proposal is None:
        logger.info("%s: LLM returned no proposal for %r", log_tag, game_name)
        return None
    api_url = proposal.get("api_url")
    wiki_url = proposal.get("wiki_url")
    root_page = proposal.get("root_page") or "Main_Page"
    if not isinstance(api_url, str) or not api_url.startswith("http"):
        logger.warning("%s: proposal missing valid api_url: %s", log_tag, proposal)
        return None
    if not isinstance(wiki_url, str) or not wiki_url.startswith("http"):
        logger.warning("%s: proposal missing valid wiki_url: %s", log_tag, proposal)
        return None

    logger.info("%s: probing api_url=%s root_page=%r", log_tag, api_url, root_page)
    with MediaWikiClient(api_url, user_agent=user_agent, rate_seconds=rate_seconds) as client:
        general = client.siteinfo()
        if general is None:
            logger.warning("%s: siteinfo probe failed for %s; rejecting", log_tag, api_url)
            return None
        sitename = general.get("sitename", "")
        if not isinstance(sitename, str) or not sitename.strip():
            logger.warning("%s: siteinfo had no usable sitename; rejecting", log_tag)
            return None
        if client.parse_page(root_page) is None:
            logger.warning(
                "%s: proposed root_page=%r failed to parse on %s — rejecting candidate",
                log_tag, root_page, api_url,
            )
            return None
    candidate = WikiCandidate(
        wiki_url=wiki_url,
        api_url=api_url,
        root_page=root_page,
        sitename=sitename,
        reason=str(proposal.get("reason", "")),
    )
    logger.info("%s: validated candidate for %r: %s", log_tag, game_name, candidate)
    return candidate


def _ask_llm(
    game_name: str,
    *,
    api_key: str,
    model: str,
    log_tag: str,
) -> dict | None:
    """Make the Anthropic call. Return parsed JSON proposal or None."""
    client = anthropic.Anthropic(api_key=api_key, timeout=_REQUEST_TIMEOUT_SECONDS)
    tools = [{
        "type": "web_search_20260209",
        "name": "web_search",
        "max_uses": _WEB_SEARCH_MAX_USES,
    }]
    user_text = f"Find the canonical community wiki for the game: {game_name!r}."
    logger.info("%s: calling messages.create model=%s web_search_max=%d", log_tag, model, _WEB_SEARCH_MAX_USES)
    t = time.monotonic()
    try:
        response = client.messages.create(
            model=model,
            system=WIKI_DISCOVERY_PROMPT,
            max_tokens=2048,
            messages=[{"role": "user", "content": user_text}],
            tools=tools,
        )
    except anthropic.APIError as exc:
        logger.error("%s: Anthropic call failed: %r", log_tag, exc)
        return None
    elapsed = time.monotonic() - t
    text = "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
    logger.info(
        "%s: messages.create returned in %.2fs stop_reason=%s text_len=%d",
        log_tag, elapsed, response.stop_reason, len(text),
    )
    return _parse_json_block(text)


_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _parse_json_block(text: str) -> dict | None:
    """Extract the JSON object from a fenced ```json ... ``` block."""
    m = _JSON_BLOCK_RE.search(text)
    if not m:
        logger.warning("discovery: no fenced JSON block found in response")
        return None
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError as exc:
        logger.warning("discovery: JSON decode failed: %r; raw=%r", exc, m.group(1)[:200])
        return None
    if not isinstance(data, dict):
        logger.warning("discovery: top-level JSON not an object: %r", type(data))
        return None
    return data
