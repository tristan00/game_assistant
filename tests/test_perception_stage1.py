from pathlib import Path
from types import SimpleNamespace

import pytest

from app.perception import stage1


@pytest.fixture
def fake_anthropic(monkeypatch):
    captured: dict = {}

    def fake_ctor(api_key, timeout):
        def create(**kwargs):
            captured["kwargs"] = kwargs
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text=captured.get("response_text", ""))],
                stop_reason="end_turn",
                usage=None,
            )
        return SimpleNamespace(messages=SimpleNamespace(create=create))

    monkeypatch.setattr(stage1.anthropic, "Anthropic", fake_ctor)
    monkeypatch.setattr(stage1, "downscale_to_jpeg", lambda p: b"\xff\xd8\xff")
    return captured


def _png(tmp_path) -> Path:
    p = tmp_path / "shot_x.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n")
    return p


def test_enumerate_image_returns_slots_and_raw_text(fake_anthropic, tmp_path):
    fake_anthropic["response_text"] = (
        '```json\n'
        '{"slots": {"time_or_phase": {"value": "Turn 17", "confidence": 0.9}}, '
        '"raw_text": "some UI text"}\n'
        '```'
    )
    img = _png(tmp_path)
    out = stage1.enumerate_image(
        img,
        api_key="k",
        model="haiku",
        schema_text="schema",
        game_id="g",
    )
    assert out["slots"]["time_or_phase"]["value"] == "Turn 17"
    assert out["raw_text"] == "some UI text"
    # No sidecar caching anymore — nothing written to disk.
    assert not img.with_suffix(".json").exists()


def test_enumerate_image_raises_on_unparseable_response(fake_anthropic, tmp_path):
    fake_anthropic["response_text"] = "no json here"
    img = _png(tmp_path)
    with pytest.raises(RuntimeError) as exc_info:
        stage1.enumerate_image(
            img,
            api_key="k", model="haiku", schema_text="schema", game_id="g",
        )
    assert "no parseable JSON" in str(exc_info.value)


def test_log_sidecar_handles_all_slot_shapes(caplog, fake_anthropic):
    sidecar = {
        "slots": {
            "ritual_countdowns": {"value": "Ritual of Rebirth: 3 turns", "confidence": 0.9},
            "army_composition": {"value": ["Eternal Guard", "Wardancers"], "confidence": 0.8},
            "lord_stances": {"value": "not visible", "confidence": None},
        },
        "raw_text": "extra UI",
    }
    import logging
    with caplog.at_level(logging.INFO, logger="app.perception.stage1"):
        stage1.log_sidecar(sidecar, filename="shot_x.png")
    text = caplog.text
    assert "Ritual of Rebirth" in text
    assert "Eternal Guard" in text
    assert "not visible" in text
    assert "extra UI" in text
    assert "ritual_countdowns" in text


def test_enumerate_images_empty_list_returns_empty(fake_anthropic):
    assert stage1.enumerate_images([], api_key="k", model="m", schema_text="s", game_id="g") == []


def test_enumerate_images_propagates_failure(fake_anthropic, tmp_path):
    """If any image fails, the whole batch raises (no per-image None placeholder)."""
    img = _png(tmp_path)
    fake_anthropic["response_text"] = "no json here"
    with pytest.raises(RuntimeError):
        stage1.enumerate_images(
            [img], api_key="k", model="m", schema_text="s", game_id="g",
        )
