import json
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


def test_enumerate_image_writes_sidecar_and_returns_dict(fake_anthropic, tmp_path):
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
    assert out is not None
    assert out["slots"]["time_or_phase"]["value"] == "Turn 17"
    assert out["raw_text"] == "some UI text"
    sidecar = img.with_suffix(".json")
    assert sidecar.exists()
    on_disk = json.loads(sidecar.read_text(encoding="utf-8"))
    assert on_disk["screenshot"] == "shot_x.png"
    assert on_disk["game_id"] == "g"
    assert on_disk["model"] == "haiku"
    assert on_disk["schema_hash"]


def test_enumerate_image_cache_hit_skips_llm(fake_anthropic, tmp_path):
    img = _png(tmp_path)
    img.with_suffix(".json").write_text(
        json.dumps({
            "schema_version": 1,
            "screenshot": img.name,
            "captured_at": "x",
            "model": "haiku",
            "game_id": "g",
            "schema_hash": "abc",
            "slots": {"time_or_phase": {"value": "cached", "confidence": 1.0}},
            "raw_text": "",
        }),
        encoding="utf-8",
    )

    fake_anthropic["response_text"] = "should not be used"
    out = stage1.enumerate_image(
        img,
        api_key="k", model="haiku", schema_text="schema", game_id="g",
    )
    assert out is not None
    assert out["slots"]["time_or_phase"]["value"] == "cached"
    # Anthropic stub never got called.
    assert "kwargs" not in fake_anthropic


def test_enumerate_image_force_bypasses_cache(fake_anthropic, tmp_path):
    img = _png(tmp_path)
    img.with_suffix(".json").write_text(
        json.dumps({"slots": {}, "raw_text": ""}), encoding="utf-8",
    )
    fake_anthropic["response_text"] = '```json\n{"slots": {"resources": {"value": "v", "confidence": 0.5}}, "raw_text": ""}\n```'
    out = stage1.enumerate_image(
        img,
        api_key="k", model="haiku", schema_text="schema", game_id="g",
        force=True,
    )
    assert out is not None
    assert out["slots"]["resources"]["value"] == "v"
    assert "kwargs" in fake_anthropic  # LLM was called


def test_enumerate_image_raises_on_unparseable_response(fake_anthropic, tmp_path):
    fake_anthropic["response_text"] = "no json here"
    img = _png(tmp_path)
    with pytest.raises(RuntimeError) as exc_info:
        stage1.enumerate_image(
            img,
            api_key="k", model="haiku", schema_text="schema", game_id="g",
        )
    assert "no parseable JSON" in str(exc_info.value)
    assert not img.with_suffix(".json").exists()


def test_log_sidecar_handles_all_slot_shapes(caplog, fake_anthropic, tmp_path):
    sidecar = {
        "screenshot": "shot_x.png",
        # Slot names are wiki-derived per-game — log_sidecar must handle
        # whatever names appear, not a hardcoded list.
        "slots": {
            "ritual_countdowns": {"value": "Ritual of Rebirth: 3 turns", "confidence": 0.9},
            "army_composition": {"value": ["Eternal Guard", "Wardancers"], "confidence": 0.8},
            "lord_stances": {"value": "not visible", "confidence": None},
        },
        "raw_text": "extra UI",
    }
    import logging
    with caplog.at_level(logging.INFO, logger="app.perception.stage1"):
        stage1.log_sidecar(sidecar)
    text = caplog.text
    assert "Ritual of Rebirth" in text
    assert "Eternal Guard" in text
    assert "not visible" in text
    assert "extra UI" in text
    assert "ritual_countdowns" in text


def test_sidecar_path_is_alongside_png(tmp_path):
    p = tmp_path / "shot_y.png"
    assert stage1.sidecar_path(p) == tmp_path / "shot_y.json"


def test_enumerate_images_runs_in_parallel(monkeypatch, tmp_path):
    """enumerate_images should dispatch the underlying calls concurrently.

    We assert this by recording the active-call count peak: if N images run
    serially, peak is 1; if parallel, peak is N.
    """
    import threading
    import time

    paths = [tmp_path / f"shot_{i}.png" for i in range(4)]
    for p in paths:
        p.write_bytes(b"\x89PNG\r\n\x1a\n")

    call_lock = threading.Lock()
    state = {"active": 0, "peak": 0}

    def fake_enum(path, *, api_key, model, schema_text, game_id, log_tag="stage1"):
        with call_lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        time.sleep(0.05)
        with call_lock:
            state["active"] -= 1
        return {"slots": {}, "raw_text": ""}

    monkeypatch.setattr(stage1, "enumerate_image", fake_enum)
    out = stage1.enumerate_images(
        paths, api_key="k", model="m", schema_text="s", game_id="g", max_workers=4,
    )
    assert len(out) == 4
    assert state["peak"] >= 2, f"expected concurrent dispatch but peak was {state['peak']}"


def test_enumerate_images_empty_list_returns_empty(fake_anthropic):
    assert stage1.enumerate_images([], api_key="k", model="m", schema_text="s", game_id="g") == []
