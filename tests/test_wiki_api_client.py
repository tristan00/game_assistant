import httpx
import pytest

from app.wiki.api_client import MediaWikiClient


def _client(handler, rate_seconds=0.0):
    return MediaWikiClient(
        "https://example.com/api.php",
        user_agent="test/1.0",
        rate_seconds=rate_seconds,
        transport=httpx.MockTransport(handler),
    )


def test_siteinfo_happy_path():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.params.get("action") == "query"
        assert req.url.params.get("meta") == "siteinfo"
        return httpx.Response(200, json={"query": {"general": {"sitename": "PoEWiki", "mainpage": "Main_Page"}}})

    with _client(handler) as c:
        info = c.siteinfo()
    assert info == {"sitename": "PoEWiki", "mainpage": "Main_Page"}


def test_siteinfo_returns_none_on_http_error():
    def handler(req):
        return httpx.Response(500, json={"error": "boom"})

    with _client(handler) as c:
        assert c.siteinfo() is None


def test_siteinfo_returns_none_on_missing_general():
    def handler(req):
        return httpx.Response(200, json={"query": {}})

    with _client(handler) as c:
        assert c.siteinfo() is None


def test_parse_page_extracts_wikitext_and_links():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.params.get("action") == "parse"
        assert req.url.params.get("page") == "Atlas"
        return httpx.Response(200, json={
            "parse": {
                "title": "Atlas",
                "displaytitle": "Atlas",
                "wikitext": {"*": "Some '''wikitext'''."},
                "links": [
                    {"ns": 0, "exists": "", "*": "Map"},
                    {"ns": 0, "exists": "", "*": "Boss"},
                    {"ns": 0, "*": "RedLink"},  # missing 'exists' -> skipped
                    {"ns": 14, "exists": "", "*": "Category:Foo"},  # non-article ns -> skipped
                ],
            }
        })

    with _client(handler) as c:
        page = c.parse_page("Atlas")
    assert page is not None
    assert page["title"] == "Atlas"
    assert page["wikitext"] == "Some '''wikitext'''."
    assert page["links"] == ["Map", "Boss"]


def test_parse_page_returns_none_on_api_error():
    def handler(req):
        return httpx.Response(200, json={"error": {"info": "Page missing"}})

    with _client(handler) as c:
        assert c.parse_page("Missing") is None


def test_list_main_namespace_pages_returns_titles():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.params.get("action") == "query"
        assert req.url.params.get("list") == "allpages"
        assert req.url.params.get("apnamespace") == "0"
        return httpx.Response(200, json={
            "query": {"allpages": [
                {"pageid": 1, "title": "Wood Elves"},
                {"pageid": 2, "title": "Skarbrand"},
                {"pageid": 3, "title": ""},          # filtered (empty)
                {"pageid": 4, "title": "Atlas"},
            ]},
        })

    with _client(handler) as c:
        titles = c.list_main_namespace_pages(limit=5)
    assert titles == ["Wood Elves", "Skarbrand", "Atlas"]


def test_list_main_namespace_pages_returns_empty_on_http_error():
    def handler(req):
        return httpx.Response(500, json={"error": "boom"})

    with _client(handler) as c:
        assert c.list_main_namespace_pages() == []


def test_rate_gate_paces_calls(monkeypatch):
    times = []

    real_sleep_calls = []

    def fake_sleep(s):
        real_sleep_calls.append(s)

    monkeypatch.setattr("app.wiki.api_client.time.sleep", fake_sleep)

    def handler(req):
        return httpx.Response(200, json={"query": {"general": {"sitename": "X"}}})

    with MediaWikiClient(
        "https://e.com/api.php",
        user_agent="t",
        rate_seconds=1.0,
        transport=httpx.MockTransport(handler),
    ) as c:
        c.siteinfo()
        c.siteinfo()
    # Second call should have triggered a sleep close to 1.0s.
    assert any(s > 0.5 for s in real_sleep_calls)
