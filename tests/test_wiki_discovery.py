from types import SimpleNamespace

import pytest

from app.wiki import discovery


def _text_block(text: str):
    return SimpleNamespace(type="text", text=text)


@pytest.fixture
def fake_anthropic(monkeypatch):
    captured: dict = {"call_count": 0, "responses": []}

    def fake_ctor(api_key, timeout):
        def create(**kwargs):
            captured["call_count"] += 1
            captured["kwargs"] = kwargs
            if captured["responses"]:
                text = captured["responses"].pop(0)
            else:
                text = captured.get("response_text", "")
            return SimpleNamespace(
                content=[_text_block(text)],
                stop_reason="end_turn",
                usage=None,
            )

        return SimpleNamespace(messages=SimpleNamespace(create=create))

    monkeypatch.setattr(discovery.anthropic, "Anthropic", fake_ctor)
    # Skip backoff sleeps in tests.
    monkeypatch.setattr(discovery.time, "sleep", lambda _s: None)
    return captured


class _FakeClient:
    def __init__(self, sitename, *, parse_ok=True):
        self._sitename = sitename
        self._parse_ok = parse_ok

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def siteinfo(self):
        if self._sitename is None:
            return None
        return {"sitename": self._sitename, "mainpage": "Main_Page"}

    def parse_page(self, title):
        if not self._parse_ok:
            return None
        return {"title": title, "displaytitle": title, "wikitext": "x", "links": []}


def test_discover_wiki_happy_path(monkeypatch, fake_anthropic):
    fake_anthropic["response_text"] = '''```json
{
  "wiki_url": "https://www.poewiki.net/",
  "api_url": "https://www.poewiki.net/api.php",
  "root_page": "Main_Page",
  "reason": "PoEWiki is the canonical community wiki"
}
```'''
    monkeypatch.setattr(discovery, "MediaWikiClient", lambda *a, **k: _FakeClient("PoEWiki"))
    result = discovery.discover_wiki(
        "Path of Exile", api_key="k", model="claude-sonnet-4-6", user_agent="ua",
    )
    assert result is not None
    assert result.wiki_url == "https://www.poewiki.net/"
    assert result.api_url == "https://www.poewiki.net/api.php"
    assert result.sitename == "PoEWiki"


def test_discover_wiki_raises_no_wiki_available_on_explicit_flag(monkeypatch, fake_anthropic):
    """LLM explicitly says no wiki exists → NoWikiAvailable, no retry."""
    fake_anthropic["response_text"] = '```json\n{"no_wiki_known": true, "reason": "searched 5 times, nothing found"}\n```'
    monkeypatch.setattr(discovery, "MediaWikiClient", lambda *a, **k: _FakeClient("ignored"))
    with pytest.raises(discovery.NoWikiAvailable, match="no usable community wiki"):
        discovery.discover_wiki("Obscure Game", api_key="k", model="m", user_agent="u")
    assert fake_anthropic["call_count"] == 1


def test_discover_wiki_retries_on_unparseable_then_succeeds(monkeypatch, fake_anthropic):
    """Transient (no JSON in response) → retry → eventual success returns the candidate."""
    fake_anthropic["responses"] = [
        "no json here at all",
        '''```json
{
  "wiki_url": "https://e.com/",
  "api_url": "https://e.com/api.php",
  "root_page": "Main_Page",
  "reason": "ok"
}
```''',
    ]
    monkeypatch.setattr(discovery, "MediaWikiClient", lambda *a, **k: _FakeClient("E"))
    result = discovery.discover_wiki("X", api_key="k", model="m", user_agent="u")
    assert result is not None
    assert result.sitename == "E"
    assert fake_anthropic["call_count"] == 2


def test_discover_wiki_returns_none_after_exhausted_transients(monkeypatch, fake_anthropic):
    """All retries fail transiently → return None; caller leaves state for next trigger."""
    fake_anthropic["response_text"] = "no json here"
    monkeypatch.setattr(discovery, "MediaWikiClient", lambda *a, **k: _FakeClient("X"))
    result = discovery.discover_wiki("X", api_key="k", model="m", user_agent="u")
    assert result is None
    # All _MAX_ATTEMPTS used.
    assert fake_anthropic["call_count"] == discovery._MAX_ATTEMPTS


def test_discover_wiki_retries_on_unprobeable_endpoint(monkeypatch, fake_anthropic):
    """Validation failures count as transient; the LLM can produce different output on retry."""
    fake_anthropic["response_text"] = '''```json
{
  "wiki_url": "https://fake.example/",
  "api_url": "https://fake.example/api.php",
  "root_page": "Main_Page",
  "reason": "made up"
}
```'''
    monkeypatch.setattr(discovery, "MediaWikiClient", lambda *a, **k: _FakeClient(None))
    result = discovery.discover_wiki("X", api_key="k", model="m", user_agent="u")
    assert result is None
    assert fake_anthropic["call_count"] == discovery._MAX_ATTEMPTS


def test_parse_json_block_extracts_from_fenced_block():
    text = 'preamble\n```json\n{"a": 1, "b": 2}\n```\ntrailing'
    assert discovery._parse_json_block(text) == {"a": 1, "b": 2}
