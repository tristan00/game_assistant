"""Thin MediaWiki action-API client.

Polite: configurable rate limit (default 1 req/sec) enforced per-instance
behind a threading lock; explicit User-Agent header sent on every request.

Only the calls the crawler needs:
- ``siteinfo()`` for endpoint validation.
- ``parse_page(title)`` for wikitext + outbound links.
"""

import logging
import threading
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class MediaWikiClient:
    def __init__(
        self,
        api_url: str,
        *,
        user_agent: str,
        rate_seconds: float = 1.0,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.api_url = api_url
        self.rate_seconds = float(rate_seconds)
        self.client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept": "application/json"},
            timeout=timeout,
            transport=transport,
        )
        self._lock = threading.Lock()
        self._last_request_monotonic = 0.0

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "MediaWikiClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _gate(self) -> None:
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_request_monotonic
            if elapsed < self.rate_seconds:
                sleep_for = self.rate_seconds - elapsed
                logger.debug("rate gate sleeping %.3fs", sleep_for)
                time.sleep(sleep_for)
            self._last_request_monotonic = time.monotonic()

    def siteinfo(self) -> dict[str, Any] | None:
        """Return parsed siteinfo dict, or None on any failure."""
        self._gate()
        try:
            r = self.client.get(
                self.api_url,
                params={"action": "query", "meta": "siteinfo", "format": "json"},
            )
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("siteinfo %s failed: %r", self.api_url, exc)
            return None
        general = data.get("query", {}).get("general")
        if not isinstance(general, dict):
            logger.warning("siteinfo %s: unexpected shape, no query.general", self.api_url)
            return None
        return general

    def list_main_namespace_pages(self, *, limit: int = 50) -> list[str]:
        """Return up to ``limit`` article titles from main namespace via list=allpages.

        Used by the crawler as a robust fallback when the LLM-discovered
        root_title (or ``Main_Page``) doesn't resolve — every real wiki has
        at least one article that ``allpages`` will surface, so this can't
        return nothing for a working wiki.
        """
        self._gate()
        capped = min(max(1, int(limit)), 500)
        try:
            r = self.client.get(
                self.api_url,
                params={
                    "action": "query",
                    "list": "allpages",
                    "apnamespace": 0,
                    "aplimit": capped,
                    "format": "json",
                },
            )
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("list_main_namespace_pages failed: %r", exc)
            return []
        if not isinstance(data, dict):
            return []
        pages = data.get("query", {}).get("allpages") or []
        titles: list[str] = []
        for p in pages:
            if isinstance(p, dict):
                title = p.get("title")
                if isinstance(title, str) and title:
                    titles.append(title)
        logger.info("list_main_namespace_pages returned %d titles", len(titles))
        return titles

    def parse_page(self, title: str) -> dict[str, Any] | None:
        """Fetch a page's wikitext + outbound links via ?action=parse.

        Returns a dict with keys ``title``, ``wikitext``, ``links`` (list of titles),
        ``displaytitle``, or None on error.
        """
        self._gate()
        try:
            r = self.client.get(
                self.api_url,
                params={
                    "action": "parse",
                    "page": title,
                    "prop": "wikitext|links",
                    "format": "json",
                    "redirects": 1,
                },
            )
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("parse_page %r failed: %r", title, exc)
            return None
        if "error" in data:
            logger.info("parse_page %r returned API error: %s", title, data["error"].get("info"))
            return None
        parse = data.get("parse")
        if not isinstance(parse, dict):
            logger.warning("parse_page %r: missing parse block", title)
            return None
        wikitext_obj = parse.get("wikitext")
        wikitext = wikitext_obj.get("*") if isinstance(wikitext_obj, dict) else ""
        raw_links = parse.get("links") or []
        # Each link entry: {"ns": int, "exists": "" (if exists), "*": "Title"}
        links: list[str] = []
        for entry in raw_links:
            if not isinstance(entry, dict):
                continue
            # Only follow main-namespace article links (ns 0). Skip categories, files, talk, etc.
            if entry.get("ns") != 0:
                continue
            # MediaWiki API marks existing links by presence of "exists" key.
            if "exists" not in entry:
                continue
            title_val = entry.get("*")
            if isinstance(title_val, str) and title_val:
                links.append(title_val)
        return {
            "title": parse.get("title", title),
            "displaytitle": parse.get("displaytitle", parse.get("title", title)),
            "wikitext": wikitext or "",
            "links": links,
        }
