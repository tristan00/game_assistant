from types import SimpleNamespace

import pytest

from app.perception import stage2


@pytest.fixture
def fake_anthropic(monkeypatch):
    captured: dict = {}

    def fake_ctor(api_key, timeout):
        def create(**kwargs):
            captured["kwargs"] = kwargs
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text=captured.get("response_text", "## State\nrows\n"))],
                stop_reason="end_turn",
                usage=None,
            )
        return SimpleNamespace(messages=SimpleNamespace(create=create))

    monkeypatch.setattr(stage2.anthropic, "Anthropic", fake_ctor)
    return captured


def test_synthesize_is_text_only_no_image_blocks(fake_anthropic):
    """Stage 2 must NOT upload images — the enumerations are the input."""
    sidecars = [
        {"slots": {"time_or_phase": {"value": "Turn 1", "confidence": 0.9}}, "raw_text": ""},
        {"slots": {"time_or_phase": {"value": "Turn 2", "confidence": 0.9}}, "raw_text": ""},
    ]
    stage2.synthesize(
        sidecars=sidecars,
        image_filenames=["shot_1.png", "shot_2.png"],
        schema_text="SCHEMA",
        question="What should I do next turn?",
        api_key="k",
        model="sonnet",
    )
    messages = fake_anthropic["kwargs"]["messages"]
    # User message content is a single string (no image blocks).
    assert isinstance(messages[0]["content"], str)
    # Sanity: no base64 in the prompt either.
    assert "base64" not in messages[0]["content"]


def test_synthesize_prompt_contains_enumerations_and_hint(fake_anthropic):
    sidecars = [
        {"slots": {"time_or_phase": {"value": "Turn 1", "confidence": 0.9}}, "raw_text": ""},
        {"slots": {"time_or_phase": {"value": "Turn 2", "confidence": 0.9}}, "raw_text": "extra UI"},
    ]
    stage2.synthesize(
        sidecars=sidecars,
        image_filenames=["shot_1.png", "shot_2.png"],
        schema_text="SCHEMA",
        question="What's the next move?",
        api_key="k",
        model="sonnet",
    )
    user_text = fake_anthropic["kwargs"]["messages"][0]["content"]
    assert "Frame 1" in user_text and "Frame 2" in user_text
    assert "Turn 1" in user_text and "Turn 2" in user_text
    assert "extra UI" in user_text
    assert "What's the next move?" in user_text


def test_synthesize_system_includes_schema_with_cache_marker(fake_anthropic):
    stage2.synthesize(
        sidecars=[{"slots": {}, "raw_text": ""}],
        image_filenames=["x.png"],
        schema_text="MY-SCHEMA-TEXT",
        question="x",
        api_key="k",
        model="sonnet",
    )
    system = fake_anthropic["kwargs"]["system"]
    assert isinstance(system, list)
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    assert "MY-SCHEMA-TEXT" in system[0]["text"]


def test_synthesize_handles_missing_sidecars(fake_anthropic):
    stage2.synthesize(
        sidecars=[None, {"slots": {}, "raw_text": ""}],
        image_filenames=["a.png", "b.png"],
        schema_text="SCHEMA",
        question="x",
        api_key="k",
        model="sonnet",
    )
    user_text = fake_anthropic["kwargs"]["messages"][0]["content"]
    assert "stage-1 enumeration unavailable" in user_text


def test_synthesize_returns_none_on_empty_response(fake_anthropic):
    fake_anthropic["response_text"] = ""
    out = stage2.synthesize(
        sidecars=[{"slots": {}, "raw_text": ""}],
        image_filenames=["x.png"],
        schema_text="SCHEMA", question="x",
        api_key="k", model="sonnet",
    )
    assert out is None


def test_synthesize_no_sidecars_returns_none(fake_anthropic):
    assert stage2.synthesize(
        sidecars=[], image_filenames=[],
        schema_text="x", question="x",
        api_key="k", model="m",
    ) is None
