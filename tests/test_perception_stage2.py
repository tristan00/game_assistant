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


def test_synthesize_raises_on_empty_response(fake_anthropic):
    fake_anthropic["response_text"] = ""
    with pytest.raises(RuntimeError, match="empty synthesis"):
        stage2.synthesize(
            sidecars=[{"slots": {}, "raw_text": ""}],
            image_filenames=["x.png"],
            schema_text="SCHEMA", question="x",
            api_key="k", model="sonnet",
        )


def test_synthesize_raises_on_no_sidecars(fake_anthropic):
    with pytest.raises(ValueError, match="sidecars list is empty"):
        stage2.synthesize(
            sidecars=[], image_filenames=[],
            schema_text="x", question="x",
            api_key="k", model="m",
        )


def test_synthesize_raises_on_length_mismatch(fake_anthropic):
    with pytest.raises(ValueError, match="length mismatch"):
        stage2.synthesize(
            sidecars=[{"slots": {}, "raw_text": ""}],
            image_filenames=["a.png", "b.png"],
            schema_text="x", question="x",
            api_key="k", model="m",
        )
