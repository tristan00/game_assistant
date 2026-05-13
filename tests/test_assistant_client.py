from types import SimpleNamespace

import pytest

from app import assistant_client
from app.assistant_client import run_completion


# ---- Helpers ----


def _text_block(text: str):
    return SimpleNamespace(type="text", text=text)


def _tool_use_block(*, id: str, name: str, input: dict):
    return SimpleNamespace(type="tool_use", id=id, name=name, input=input)


def _make_response(text: str = "ok", *, tool_uses=None, stop_reason="end_turn"):
    blocks = []
    for tu in tool_uses or []:
        blocks.append(tu)
    blocks.append(_text_block(text))
    return SimpleNamespace(content=blocks, stop_reason=stop_reason, usage=None)


def _system_text(system):
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        return "".join(b.get("text", "") for b in system if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _noop_handler(query: str, n: int) -> list[dict]:
    return []


REQUIRED_KWARGS = {
    "quick_ref_text": "## quick-ref body\n- thing.\n",
    "synthesis_text": "## State\nsynthesis content\n",
}


@pytest.fixture
def fake_anthropic(monkeypatch):
    captured: dict = {"call_log": []}

    def fake_ctor(api_key, timeout):
        captured["api_key"] = api_key
        captured["timeout"] = timeout

        def create(**kwargs):
            captured["kwargs"] = kwargs
            captured["call_log"].append(kwargs)
            responses = captured.get("responses")
            if responses:
                return responses.pop(0)
            return captured.get("response") or _make_response("ok")

        return SimpleNamespace(messages=SimpleNamespace(create=create))

    monkeypatch.setattr(assistant_client.anthropic, "Anthropic", fake_ctor)
    monkeypatch.setattr(assistant_client, "downscale_to_jpeg", lambda p: b"\xff\xd8\xff")
    return captured


# ---- Required ingredients ----


def test_empty_quick_ref_raises(make_png):
    with pytest.raises(ValueError, match="quick_ref_text"):
        run_completion(
            api_key="k", model="claude-sonnet-4-6",
            history=[], goal_text="", question="q",
            image_path=make_png(),
            quick_ref_text="   ",
            synthesis_text="x",
            search_game_rules_handler=_noop_handler,
        )


def test_empty_synthesis_raises(make_png):
    with pytest.raises(ValueError, match="synthesis_text"):
        run_completion(
            api_key="k", model="claude-sonnet-4-6",
            history=[], goal_text="", question="q",
            image_path=make_png(),
            quick_ref_text="x",
            synthesis_text="   ",
            search_game_rules_handler=_noop_handler,
        )


# ---- Response shape ----


def test_returns_concatenated_text_blocks(fake_anthropic, make_png):
    fake_anthropic["response"] = SimpleNamespace(
        content=[_text_block("hello "), _text_block("world")],
        stop_reason="end_turn",
        usage=None,
    )
    out = run_completion(
        api_key="k", model="claude-sonnet-4-6",
        history=[], goal_text="", question="q",
        image_path=make_png(),
        search_game_rules_handler=_noop_handler,
        **REQUIRED_KWARGS,
    )
    assert out == "hello world"


def test_history_renders_as_alternating_user_assistant(fake_anthropic, make_png):
    run_completion(
        api_key="k", model="claude-sonnet-4-6",
        history=[
            {"question": "q1", "response": "a1"},
            {"question": "q2", "response": "a2"},
        ],
        goal_text="", question="q3",
        image_path=make_png(),
        search_game_rules_handler=_noop_handler,
        **REQUIRED_KWARGS,
    )
    messages = fake_anthropic["kwargs"]["messages"]
    assert len(messages) == 5
    assert messages[0] == {"role": "user", "content": "q1"}
    assert messages[1] == {"role": "assistant", "content": "a1"}
    assert messages[2] == {"role": "user", "content": "q2"}
    assert messages[3] == {"role": "assistant", "content": "a2"}
    assert messages[4]["role"] == "user"
    assert isinstance(messages[4]["content"], list)


def test_single_image_in_user_message(fake_anthropic, make_png):
    run_completion(
        api_key="k", model="claude-sonnet-4-6",
        history=[], goal_text="", question="q",
        image_path=make_png(),
        search_game_rules_handler=_noop_handler,
        **REQUIRED_KWARGS,
    )
    last_user = fake_anthropic["kwargs"]["messages"][-1]
    image_blocks = [b for b in last_user["content"] if b.get("type") == "image"]
    assert len(image_blocks) == 1


def test_user_message_contains_synthesis_and_question(fake_anthropic, make_png):
    run_completion(
        api_key="k", model="claude-sonnet-4-6",
        history=[], goal_text="", question="my-question",
        image_path=make_png(),
        search_game_rules_handler=_noop_handler,
        quick_ref_text="x",
        synthesis_text="## State\nsome synthesis\n",
    )
    last_user = fake_anthropic["kwargs"]["messages"][-1]
    text_blocks = [b for b in last_user["content"] if b.get("type") == "text"]
    joined = "\n".join(b["text"] for b in text_blocks)
    assert "PRIMARY STATE" in joined
    assert "some synthesis" in joined
    assert joined.endswith("my-question")


# ---- System prompt composition ----


def test_goal_text_appended_to_system_prompt(fake_anthropic, make_png):
    run_completion(
        api_key="k", model="claude-sonnet-4-6",
        history=[],
        goal_text="Skarbrand rush, ignore Cathay until turn 50.",
        question="q", image_path=make_png(),
        search_game_rules_handler=_noop_handler,
        **REQUIRED_KWARGS,
    )
    system = _system_text(fake_anthropic["kwargs"]["system"])
    assert "Goal (begin)" in system
    assert "Skarbrand rush" in system
    assert "Goal (end)" in system


def test_empty_goal_text_omits_goal_block(fake_anthropic, make_png):
    run_completion(
        api_key="k", model="claude-sonnet-4-6",
        history=[], goal_text="", question="q",
        image_path=make_png(),
        search_game_rules_handler=_noop_handler,
        **REQUIRED_KWARGS,
    )
    system = _system_text(fake_anthropic["kwargs"]["system"])
    assert "Goal (begin)" not in system


def test_quick_ref_is_injected_into_system_prompt(fake_anthropic, make_png):
    run_completion(
        api_key="k", model="claude-sonnet-4-6",
        history=[], goal_text="", question="q",
        image_path=make_png(),
        search_game_rules_handler=_noop_handler,
        quick_ref_text="## Total War facts\n- Skaven scurry.\n",
        synthesis_text="x",
    )
    system = _system_text(fake_anthropic["kwargs"]["system"])
    assert "Active game quick reference" in system
    assert "Skaven scurry" in system


def test_synthesis_note_in_system_prompt(fake_anthropic, make_png):
    run_completion(
        api_key="k", model="claude-sonnet-4-6",
        history=[], goal_text="", question="q",
        image_path=make_png(),
        search_game_rules_handler=_noop_handler,
        **REQUIRED_KWARGS,
    )
    system = _system_text(fake_anthropic["kwargs"]["system"])
    assert "Synthesis primary-state mode" in system


def test_corpus_search_note_always_added(fake_anthropic, make_png):
    run_completion(
        api_key="k", model="claude-sonnet-4-6",
        history=[], goal_text="", question="q",
        image_path=make_png(),
        search_game_rules_handler=_noop_handler,
        **REQUIRED_KWARGS,
    )
    system = _system_text(fake_anthropic["kwargs"]["system"])
    assert "search_game_rules" in system
    assert "corpus" in system.lower()


# ---- Caching + tools ----


def test_prompt_cache_control_on_system_block_by_default(fake_anthropic, make_png):
    run_completion(
        api_key="k", model="claude-sonnet-4-6",
        history=[], goal_text="", question="q",
        image_path=make_png(),
        search_game_rules_handler=_noop_handler,
        **REQUIRED_KWARGS,
    )
    system = fake_anthropic["kwargs"]["system"]
    assert isinstance(system, list)
    assert system[0]["cache_control"] == {"type": "ephemeral"}


def test_prompt_cache_disabled_yields_plain_string_system(fake_anthropic, make_png):
    run_completion(
        api_key="k", model="claude-sonnet-4-6",
        history=[], goal_text="", question="q",
        image_path=make_png(),
        search_game_rules_handler=_noop_handler,
        enable_prompt_cache=False,
        **REQUIRED_KWARGS,
    )
    assert isinstance(fake_anthropic["kwargs"]["system"], str)


def test_search_game_rules_is_the_only_tool(fake_anthropic, make_png):
    run_completion(
        api_key="k", model="claude-sonnet-4-6",
        history=[], goal_text="", question="q",
        image_path=make_png(),
        search_game_rules_handler=_noop_handler,
        **REQUIRED_KWARGS,
    )
    tools = fake_anthropic["kwargs"]["tools"]
    assert [t.get("name") for t in tools] == ["search_game_rules"]


def test_api_key_and_model_propagate(fake_anthropic, make_png):
    run_completion(
        api_key="sk-ant-xyz", model="claude-haiku-4-5",
        history=[], goal_text="", question="q",
        image_path=make_png(),
        search_game_rules_handler=_noop_handler,
        **REQUIRED_KWARGS,
    )
    assert fake_anthropic["api_key"] == "sk-ant-xyz"
    assert fake_anthropic["kwargs"]["model"] == "claude-haiku-4-5"


# ---- Tool-use loop ----


def test_search_game_rules_tool_use_loop(fake_anthropic, make_png):
    handler_calls: list[tuple[str, int]] = []

    def handler(query: str, n: int) -> list[dict]:
        handler_calls.append((query, n))
        return [{"title": "Ritual of Rebirth", "url": "https://w/RoR", "snippet": "Requires a magical forest."}]

    fake_anthropic["responses"] = [
        _make_response(
            "",
            tool_uses=[_tool_use_block(id="tu_1", name="search_game_rules", input={"query": "ritual of rebirth", "max_results": 3})],
            stop_reason="tool_use",
        ),
        _make_response("The Ritual of Rebirth requires a magical forest.", stop_reason="end_turn"),
    ]
    out = run_completion(
        api_key="k", model="claude-sonnet-4-6",
        history=[], goal_text="", question="q",
        image_path=make_png(),
        search_game_rules_handler=handler,
        **REQUIRED_KWARGS,
    )
    assert "magical forest" in out
    assert handler_calls == [("ritual of rebirth", 3)]


def test_tool_use_loop_max_iters_raises(fake_anthropic, make_png):
    """Hitting the tool-use cap is a loud failure, not a silent truncation."""
    fake_anthropic["responses"] = [
        _make_response(
            "stuck",
            tool_uses=[_tool_use_block(id=f"tu_{i}", name="search_game_rules", input={"query": "x"})],
            stop_reason="tool_use",
        )
        for i in range(10)
    ]
    with pytest.raises(RuntimeError, match="exceeded client_tool_max_iters"):
        run_completion(
            api_key="k", model="claude-sonnet-4-6",
            history=[], goal_text="", question="q",
            image_path=make_png(),
            search_game_rules_handler=lambda q, n: [],
            client_tool_max_iters=2,
            **REQUIRED_KWARGS,
        )


def test_unknown_tool_raises(fake_anthropic, make_png):
    fake_anthropic["responses"] = [
        _make_response(
            "",
            tool_uses=[_tool_use_block(id="tu_1", name="weather", input={})],
            stop_reason="tool_use",
        ),
    ]
    with pytest.raises(RuntimeError, match="unknown client tool"):
        run_completion(
            api_key="k", model="claude-sonnet-4-6",
            history=[], goal_text="", question="q",
            image_path=make_png(),
            search_game_rules_handler=_noop_handler,
            **REQUIRED_KWARGS,
        )
