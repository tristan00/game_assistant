import threading

import httpx
import pytest

from app.wiki import crawler, storage


def _build_crawler(handler, *, game_id="g", root_title="Main_Page", max_depth=2):
    # Patch the MediaWikiClient that Crawler.run constructs internally so we
    # can inject our httpx mock transport.
    real_cls = crawler.MediaWikiClient

    def factory(api_url, *, user_agent, rate_seconds, **kwargs):
        return real_cls(
            api_url,
            user_agent=user_agent,
            rate_seconds=0.0,
            transport=httpx.MockTransport(handler),
        )

    return factory, real_cls


def test_crawler_writes_all_reachable_pages(monkeypatch):
    pages = {
        "Main_Page": {"links": ["A", "B"], "wikitext": "Main."},
        "A": {"links": ["C"], "wikitext": "Page A."},
        "B": {"links": ["C", "D"], "wikitext": "Page B."},
        "C": {"links": [], "wikitext": "Page C."},
        "D": {"links": [], "wikitext": "Page D."},
    }

    def handler(req: httpx.Request) -> httpx.Response:
        title = req.url.params.get("page")
        if title not in pages:
            return httpx.Response(200, json={"error": {"info": "missing"}})
        p = pages[title]
        return httpx.Response(200, json={
            "parse": {
                "title": title,
                "displaytitle": title,
                "wikitext": {"*": p["wikitext"]},
                "links": [{"ns": 0, "exists": "", "*": L} for L in p["links"]],
            }
        })

    factory, _ = _build_crawler(handler)
    monkeypatch.setattr(crawler, "MediaWikiClient", factory)
    c = crawler.Crawler(
        game_id="g",
        wiki_url="https://e.com/wiki/",
        api_url="https://e.com/api.php",
        root_title="Main_Page",
        user_agent="t",
        rate_seconds=0.0,
    )
    result = c.run()
    assert result["state"] == "done"
    assert result["pages_written"] == 5
    files = sorted(p.name for p in storage.pages_dir("g").glob("*.md"))
    assert len(files) == 5
    assert "Main_Page.md" in files


def test_crawler_fails_when_root_title_does_not_parse(monkeypatch):
    """Strict mode: if the LLM-discovered root_title doesn't parse, the crawl
    FAILS. No silent fallback to Main_Page or allpages."""

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.params.get("action") == "parse":
            return httpx.Response(200, json={"error": {"info": "missingtitle"}})
        return httpx.Response(404, json={})

    factory, _ = _build_crawler(handler)
    monkeypatch.setattr(crawler, "MediaWikiClient", factory)
    c = crawler.Crawler(
        game_id="g",
        wiki_url="https://e.com/wiki/",
        api_url="https://e.com/api.php",
        root_title="Bogus Title",
        user_agent="t",
        rate_seconds=0.0,
    )
    result = c.run()
    assert result["state"] == "failed"
    assert result["pages_written"] == 0
    assert "Bogus Title" in (result.get("error") or "")


def test_crawler_marks_zero_page_run_as_failed(monkeypatch):
    """A crawl that visits the root but writes no pages should be `failed`, not `done`."""

    def handler(req: httpx.Request) -> httpx.Response:
        action = req.url.params.get("action")
        if action == "query" and req.url.params.get("list") == "allpages":
            # No fallback seeds available either.
            return httpx.Response(200, json={"query": {"allpages": []}})
        # Every parse returns an API error (e.g. missing title).
        return httpx.Response(200, json={"error": {"info": "Page missing"}})

    factory, _ = _build_crawler(handler)
    monkeypatch.setattr(crawler, "MediaWikiClient", factory)
    c = crawler.Crawler(
        game_id="g",
        wiki_url="https://e.com/",
        api_url="https://e.com/api.php",
        root_title="Some_Bad_Title",
        user_agent="t",
        rate_seconds=0.0,
    )
    result = c.run()
    assert result["state"] == "failed"
    assert result["pages_written"] == 0
    assert "0 pages" in (result.get("error") or "")


def test_crawler_cancellation(monkeypatch):
    pages = {f"P{i}": {"links": [f"P{i+1}"], "wikitext": f"page {i}"} for i in range(20)}
    pages["Main_Page"] = {"links": ["P0"], "wikitext": "root"}

    def handler(req):
        title = req.url.params.get("page")
        if title not in pages:
            return httpx.Response(200, json={"error": {"info": "missing"}})
        return httpx.Response(200, json={
            "parse": {
                "title": title, "displaytitle": title,
                "wikitext": {"*": pages[title]["wikitext"]},
                "links": [{"ns": 0, "exists": "", "*": L} for L in pages[title]["links"]],
            }
        })

    factory, _ = _build_crawler(handler)
    monkeypatch.setattr(crawler, "MediaWikiClient", factory)

    cancel = threading.Event()
    cancel.set()  # cancel before we even start
    c = crawler.Crawler(
        game_id="g",
        wiki_url="https://e.com/",
        api_url="https://e.com/api.php",
        root_title="Main_Page",
        user_agent="t",
        rate_seconds=0.0,
        cancel_event=cancel,
    )
    result = c.run()
    # Cancelled before any page fetched.
    assert result["state"] == "failed"
    assert result["pages_written"] == 0


def test_wikitext_to_markdown_strips_links_and_templates():
    out = crawler._wikitext_to_markdown(
        title="T",
        source_url="https://e.com/wiki/T",
        wikitext="See [[Foo|bar]] and {{template|x}} '''bold''' ''italic'' [[Baz]].",
    )
    assert "[[" not in out
    assert "{{" not in out
    assert "**bold**" in out
    assert "*italic*" in out
    assert "bar" in out
    assert "Baz" in out
    assert "T" in out
    assert "https://e.com/wiki/T" in out


def test_crawler_emits_events(monkeypatch):
    events: list[dict] = []

    pages = {"Main_Page": {"links": [], "wikitext": "root"}}

    def handler(req):
        action = req.url.params.get("action")
        if action == "query" and req.url.params.get("list") == "allpages":
            return httpx.Response(200, json={"query": {"allpages": []}})
        title = req.url.params.get("page")
        if title not in pages:
            return httpx.Response(200, json={"error": {"info": "missing"}})
        return httpx.Response(200, json={
            "parse": {
                "title": title, "displaytitle": title,
                "wikitext": {"*": pages[title]["wikitext"]},
                "links": [],
            }
        })

    factory, _ = _build_crawler(handler)
    monkeypatch.setattr(crawler, "MediaWikiClient", factory)
    c = crawler.Crawler(
        game_id="g",
        wiki_url="https://e.com/",
        api_url="https://e.com/api.php",
        root_title="Main_Page",
        user_agent="t",
        rate_seconds=0.0,
        on_event=events.append,
    )
    c.run()
    types = [e["type"] for e in events]
    assert "crawl_started" in types
    assert "crawl_done" in types
