from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app import assistant_client
from app.assistant_client import _web_search_tool, run_completion


# ---- _web_search_tool ----


def test_web_search_tool_zero_returns_none():
    assert _web_search_tool(0) is None


def test_web_search_tool_negative_returns_none():
    assert _web_search_tool(-1) is None


def test_web_search_tool_positive_returns_dict():
    tool = _web_search_tool(5)
    assert tool["name"] == "web_search"
    assert tool["max_uses"] == 5
    assert tool["type"].startswith("web_search_")


# ---- run_completion ----


def _text_block(text: str):
    return SimpleNamespace(type="text", text=text)


def _make_response(text: str = "ok", *, search_queries: list[str] | None = None):
    blocks = []
    for q in search_queries or []:
        blocks.append(SimpleNamespace(
            type="server_tool_use",
            name="web_search",
            input={"query": q},
        ))
    blocks.append(_text_block(text))
    return SimpleNamespace(content=blocks, stop_reason="end_turn", usage=None)


@pytest.fixture
def fake_anthropic(monkeypatch):
    """Replace anthropic.Anthropic with a stub that records messages.create calls."""
    captured: dict = {}

    def fake_ctor(api_key, timeout):
        captured["api_key"] = api_key
        captured["timeout"] = timeout

        def create(**kwargs):
            captured["kwargs"] = kwargs
            return captured.get("response") or _make_response("ok")

        return SimpleNamespace(messages=SimpleNamespace(create=create))

    monkeypatch.setattr(assistant_client.anthropic, "Anthropic", fake_ctor)
    monkeypatch.setattr(assistant_client, "downscale_to_jpeg", lambda p: b"\xff\xd8\xff")
    return captured


def test_returns_concatenated_text_blocks(fake_anthropic, make_png):
    fake_anthropic["response"] = SimpleNamespace(
        content=[_text_block("hello "), _text_block("world")],
        stop_reason="end_turn",
        usage=None,
    )
    out = run_completion(
        api_key="k",
        model="claude-sonnet-4-6",
        history=[],
        strategy_text="",
        question="q",
        image_paths=[make_png()],
        web_search_max_uses=0,
    )
    assert out == "hello world"


def test_history_renders_as_alternating_user_assistant(fake_anthropic, make_png):
    run_completion(
        api_key="k",
        model="claude-sonnet-4-6",
        history=[
            {"question": "q1", "response": "a1"},
            {"question": "q2", "response": "a2"},
        ],
        strategy_text="",
        question="q3",
        image_paths=[make_png()],
        web_search_max_uses=0,
    )
    messages = fake_anthropic["kwargs"]["messages"]
    # 2 history turns x 2 messages each = 4, plus the latest user turn.
    assert len(messages) == 5
    assert messages[0] == {"role": "user", "content": "q1"}
    assert messages[1] == {"role": "assistant", "content": "a1"}
    assert messages[2] == {"role": "user", "content": "q2"}
    assert messages[3] == {"role": "assistant", "content": "a2"}
    assert messages[4]["role"] == "user"
    # The latest user message is a list of blocks (image + text).
    assert isinstance(messages[4]["content"], list)
    text_blocks = [b for b in messages[4]["content"] if b.get("type") == "text"]
    assert text_blocks == [{"type": "text", "text": "q3"}]


def test_image_paths_trimmed_to_20(fake_anthropic, make_png):
    paths = [make_png(name=f"img_{i}.png") for i in range(25)]
    run_completion(
        api_key="k",
        model="claude-sonnet-4-6",
        history=[],
        strategy_text="",
        question="q",
        image_paths=paths,
        web_search_max_uses=0,
    )
    last_user = fake_anthropic["kwargs"]["messages"][-1]
    image_blocks = [b for b in last_user["content"] if b.get("type") == "image"]
    assert len(image_blocks) == 20


def test_strategy_text_appended_to_system_prompt(fake_anthropic, make_png):
    run_completion(
        api_key="k",
        model="claude-sonnet-4-6",
        history=[],
        strategy_text="Skarbrand rush, ignore Cathay until turn 50.",
        question="q",
        image_paths=[make_png()],
        web_search_max_uses=0,
    )
    system = fake_anthropic["kwargs"]["system"]
    assert "Strategic Context (begin)" in system
    assert "Skarbrand rush" in system
    assert "Strategic Context (end)" in system


def test_empty_strategy_text_omits_strategy_block(fake_anthropic, make_png):
    run_completion(
        api_key="k",
        model="claude-sonnet-4-6",
        history=[],
        strategy_text="",
        question="q",
        image_paths=[make_png()],
        web_search_max_uses=0,
    )
    system = fake_anthropic["kwargs"]["system"]
    assert "Strategic Context" not in system


def test_whitespace_only_strategy_text_omits_block(fake_anthropic, make_png):
    run_completion(
        api_key="k",
        model="claude-sonnet-4-6",
        history=[],
        strategy_text="   \n  \t ",
        question="q",
        image_paths=[make_png()],
        web_search_max_uses=0,
    )
    system = fake_anthropic["kwargs"]["system"]
    assert "Strategic Context" not in system


def test_web_search_zero_omits_tools_kwarg(fake_anthropic, make_png):
    run_completion(
        api_key="k",
        model="claude-sonnet-4-6",
        history=[],
        strategy_text="",
        question="q",
        image_paths=[make_png()],
        web_search_max_uses=0,
    )
    assert "tools" not in fake_anthropic["kwargs"]


def test_web_search_positive_includes_tools_kwarg(fake_anthropic, make_png):
    run_completion(
        api_key="k",
        model="claude-sonnet-4-6",
        history=[],
        strategy_text="",
        question="q",
        image_paths=[make_png()],
        web_search_max_uses=3,
    )
    tools = fake_anthropic["kwargs"]["tools"]
    assert len(tools) == 1
    assert tools[0]["name"] == "web_search"
    assert tools[0]["max_uses"] == 3


def test_api_key_and_model_propagate(fake_anthropic, make_png):
    run_completion(
        api_key="sk-ant-xyz",
        model="claude-haiku-4-5",
        history=[],
        strategy_text="",
        question="q",
        image_paths=[make_png()],
        web_search_max_uses=0,
    )
    assert fake_anthropic["api_key"] == "sk-ant-xyz"
    assert fake_anthropic["kwargs"]["model"] == "claude-haiku-4-5"
