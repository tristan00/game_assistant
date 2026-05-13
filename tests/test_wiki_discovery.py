from types import SimpleNamespace

import pytest

from app.wiki import discovery


def _text_block(text: str):
    return SimpleNamespace(type="text", text=text)


@pytest.fixture
def fake_anthropic(monkeypatch):
    captured: dict = {}

    def fake_ctor(api_key, timeout):
        captured["api_key"] = api_key

        def create(**kwargs):
            captured["kwargs"] = kwargs
            text = captured.get("response_text", "")
            return SimpleNamespace(
                content=[_text_block(text)],
                stop_reason="end_turn",
                usage=None,
            )

        return SimpleNamespace(messages=SimpleNamespace(create=create))

    monkeypatch.setattr(discovery.anthropic, "Anthropic", fake_ctor)
    return captured


class _FakeClient:
    def __init__(self, sitename, *, parse_ok=True, parse_alt_ok=False):
        self._sitename = sitename
        self._parse_ok = parse_ok
        self._parse_alt_ok = parse_alt_ok

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def siteinfo(self):
        if self._sitename is None:
            return None
        return {"sitename": self._sitename, "mainpage": "Main_Page"}

    def parse_page(self, title):
        # Validation step: discovery now requires the proposed root_page to
        # actually parse on the wiki. First call (root_page from LLM) honours
        # parse_ok; second call (siteinfo.mainpage) honours parse_alt_ok.
        if not hasattr(self, "_n"):
            self._n = 0
        self._n += 1
        ok = self._parse_ok if self._n == 1 else self._parse_alt_ok
        if not ok:
            return None
        return {"title": title, "displaytitle": title, "wikitext": "x", "links": []}


def test_discover_wiki_happy_path(monkeypatch, fake_anthropic):
    fake_anthropic["response_text"] = '''Looking up... here's what I found:

```json
{
  "wiki_url": "https://www.poewiki.net/",
  "api_url": "https://www.poewiki.net/api.php",
  "root_page": "Main_Page",
  "reason": "PoEWiki is the canonical community wiki"
}
```'''

    monkeypatch.setattr(discovery, "MediaWikiClient", lambda *a, **k: _FakeClient("PoEWiki"))
    result = discovery.discover_wiki(
        "Path of Exile",
        api_key="k",
        model="claude-sonnet-4-6",
        user_agent="ua",
    )
    assert result is not None
    assert result.wiki_url == "https://www.poewiki.net/"
    assert result.api_url == "https://www.poewiki.net/api.php"
    assert result.root_page == "Main_Page"
    assert result.sitename == "PoEWiki"


def test_discover_wiki_returns_none_when_llm_says_no(monkeypatch, fake_anthropic):
    fake_anthropic["response_text"] = '```json\n{}\n```'
    monkeypatch.setattr(discovery, "MediaWikiClient", lambda *a, **k: _FakeClient("ignored"))
    assert discovery.discover_wiki("X", api_key="k", model="m", user_agent="u") is None


def test_discover_wiki_rejects_unprobeable_endpoint(monkeypatch, fake_anthropic):
    fake_anthropic["response_text"] = '''```json
{
  "wiki_url": "https://fake.example/",
  "api_url": "https://fake.example/api.php",
  "root_page": "Main_Page",
  "reason": "made up"
}
```'''
    monkeypatch.setattr(discovery, "MediaWikiClient", lambda *a, **k: _FakeClient(None))
    assert discovery.discover_wiki("X", api_key="k", model="m", user_agent="u") is None


def test_discover_wiki_rejects_when_seed_does_not_parse(monkeypatch, fake_anthropic):
    """If the LLM-chosen root_page doesn't parse, discovery returns None."""
    fake_anthropic["response_text"] = '''```json
{
  "wiki_url": "https://e.com/",
  "api_url": "https://e.com/api.php",
  "root_page": "Bogus Title",
  "reason": "ok"
}
```'''
    monkeypatch.setattr(
        discovery, "MediaWikiClient",
        lambda *a, **k: _FakeClient("Sitename", parse_ok=False, parse_alt_ok=False),
    )
    assert discovery.discover_wiki("X", api_key="k", model="m", user_agent="u") is None




def test_discover_wiki_handles_no_json_in_response(monkeypatch, fake_anthropic):
    fake_anthropic["response_text"] = "I couldn't find anything useful."
    monkeypatch.setattr(discovery, "MediaWikiClient", lambda *a, **k: _FakeClient("X"))
    assert discovery.discover_wiki("X", api_key="k", model="m", user_agent="u") is None


def test_parse_json_block_extracts_from_fenced_block():
    text = 'preamble\n```json\n{"a": 1, "b": 2}\n```\ntrailing'
    assert discovery._parse_json_block(text) == {"a": 1, "b": 2}


def test_parse_json_block_returns_none_without_fence():
    text = 'no fence here, just {"a": 1}.'
    assert discovery._parse_json_block(text) is None


def test_parse_json_block_returns_none_on_no_object():
    assert discovery._parse_json_block("plain text") is None
