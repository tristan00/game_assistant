"""LLM-driven wiki discovery.

Asks Anthropic (with the server-side ``web_search`` tool) to identify the
canonical community wiki for a game and return a structured JSON answer.
Then probes the proposed MediaWiki endpoint to validate it.

No curated hardcoded list, no hand-tuned scoring formula — the model picks
from real search results and we validate by hitting the actual API.

Two terminal outcomes for the caller:
- ``WikiCandidate`` on success.
- ``NoWikiAvailable`` raised when the LLM searched and reported no usable
  community wiki exists (the LLM is the judge — trust its answer).

Transient errors (Anthropic API hiccups, parse failures, validation
mismatches) are retried internally with bounded backoff. If retries are
exhausted, ``discover_wiki`` returns ``None`` and the caller is expected
to leave the game in its current state so a later trigger can retry.
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
_MAX_ATTEMPTS = 3
_BACKOFF_BASE_SECONDS = 2.0


class NoWikiAvailable(Exception):
    """LLM searched and reported no usable community wiki for this game."""


class _Transient(Exception):
    """Internal: retriable failure. Not raised to callers — converted to None."""


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
    """Return a validated wiki candidate for ``game_name``, or ``None``.

    Raises :class:`NoWikiAvailable` if the LLM reports no usable wiki
    exists for this game (permanent: caller marks game unsupported).

    Returns ``None`` after exhausting retries on transient failures
    (caller leaves the game in its current state; a later trigger will
    retry).
    """
    last_transient: Exception | None = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            proposal = _ask_llm(
                game_name,
                api_key=api_key,
                model=model,
                log_tag=log_tag,
            )
            if proposal.get("no_wiki_known") is True:
                raise NoWikiAvailable(
                    f"LLM reported no usable community wiki for {game_name!r}: "
                    f"{proposal.get('reason', '<no reason given>')!r}"
                )
            candidate = _validate_proposal(
                proposal,
                user_agent=user_agent,
                rate_seconds=rate_seconds,
                log_tag=log_tag,
            )
            logger.info(
                "%s: validated candidate for %r on attempt %d: %s",
                log_tag, game_name, attempt + 1, candidate,
            )
            return candidate
        except NoWikiAvailable:
            raise
        except _Transient as exc:
            last_transient = exc
            if attempt + 1 < _MAX_ATTEMPTS:
                delay = _BACKOFF_BASE_SECONDS * (2**attempt)
                logger.warning(
                    "%s: transient on attempt %d/%d (%r); retrying in %.1fs",
                    log_tag, attempt + 1, _MAX_ATTEMPTS, exc, delay,
                )
                time.sleep(delay)
                continue
    logger.error(
        "%s: exhausted %d attempts for %r; last error: %r",
        log_tag, _MAX_ATTEMPTS, game_name, last_transient,
    )
    return None


def _ask_llm(
    game_name: str,
    *,
    api_key: str,
    model: str,
    log_tag: str,
) -> dict:
    """Make the Anthropic call. Return parsed JSON proposal.

    Empty dict ``{}`` signals "LLM searched and reported no wiki exists."
    Raises :class:`_Transient` on retriable failures (API error, no JSON
    block in response).
    """
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
        raise _Transient(f"Anthropic call failed: {exc!r}") from exc
    elapsed = time.monotonic() - t
    text = "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
    logger.info(
        "%s: messages.create returned in %.2fs stop_reason=%s text_len=%d",
        log_tag, elapsed, response.stop_reason, len(text),
    )
    parsed = _parse_json_block(text)
    if parsed is None:
        raise _Transient(f"no parseable JSON block in response; raw_first={text[:200]!r}")
    return parsed


def _validate_proposal(
    proposal: dict,
    *,
    user_agent: str,
    rate_seconds: float,
    log_tag: str,
) -> WikiCandidate:
    """Probe the proposed api_url. Raise :class:`_Transient` on any failure."""
    api_url = proposal.get("api_url")
    wiki_url = proposal.get("wiki_url")
    root_page = proposal.get("root_page") or "Main_Page"
    if not isinstance(api_url, str) or not api_url.startswith("http"):
        raise _Transient(f"proposal missing valid api_url: {proposal!r}")
    if not isinstance(wiki_url, str) or not wiki_url.startswith("http"):
        raise _Transient(f"proposal missing valid wiki_url: {proposal!r}")
    logger.info("%s: probing api_url=%s root_page=%r", log_tag, api_url, root_page)
    with MediaWikiClient(api_url, user_agent=user_agent, rate_seconds=rate_seconds) as client:
        general = client.siteinfo()
        if general is None:
            raise _Transient(f"siteinfo probe failed for {api_url}")
        sitename = general.get("sitename", "")
        if not isinstance(sitename, str) or not sitename.strip():
            raise _Transient(f"siteinfo had no usable sitename: {general!r}")
        if client.parse_page(root_page) is None:
            raise _Transient(
                f"proposed root_page={root_page!r} failed to parse on {api_url}"
            )
    return WikiCandidate(
        wiki_url=wiki_url,
        api_url=api_url,
        root_page=root_page,
        sitename=sitename,
        reason=str(proposal.get("reason", "")),
    )


_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _parse_json_block(text: str) -> dict | None:
    """Extract the JSON object from a fenced ```json ... ``` block."""
    m = _JSON_BLOCK_RE.search(text)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data
