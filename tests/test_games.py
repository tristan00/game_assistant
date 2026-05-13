from pathlib import Path
from types import SimpleNamespace

import pytest

from app import games


def _text_block(text):
    return SimpleNamespace(type="text", text=text)


@pytest.fixture
def fake_anthropic(monkeypatch):
    captured: dict = {}

    def fake_ctor(api_key, timeout):
        def create(**kwargs):
            captured["kwargs"] = kwargs
            return SimpleNamespace(
                content=[_text_block(captured.get("response_text", ""))],
                stop_reason="end_turn",
                usage=None,
            )

        return SimpleNamespace(messages=SimpleNamespace(create=create))

    monkeypatch.setattr(games.anthropic, "Anthropic", fake_ctor)
    monkeypatch.setattr(games, "downscale_to_jpeg", lambda p: b"\xff\xd8\xff")
    return captured


def _png(tmp_path) -> Path:
    p = tmp_path / "x.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n")
    return p


# ---- registry / persistence ----


def test_list_games_empty_initially():
    assert games.list_games() == []


def test_upsert_get_delete_round_trip():
    e = games.GameEntry(game_id="poe2", display_name="Path of Exile 2", is_game=True)
    games.upsert_game(e)
    fetched = games.get_game("poe2")
    assert fetched is not None
    assert fetched.display_name == "Path of Exile 2"
    games.delete_game("poe2")
    assert games.get_game("poe2") is None


def test_bind_window_round_trip():
    assert games.get_binding("Some Title") is None
    games.bind_window("Some Title", "poe2")
    assert games.get_binding("Some Title") == "poe2"
    games.clear_binding("Some Title")
    assert games.get_binding("Some Title") is None


def test_delete_game_clears_bindings():
    games.upsert_game(games.GameEntry(game_id="g", display_name="G"))
    games.bind_window("Win 1", "g")
    games.bind_window("Win 2", "g")
    games.delete_game("g")
    assert games.get_binding("Win 1") is None
    assert games.get_binding("Win 2") is None


# ---- identify_game_from_screenshot ----


def test_identify_game_parses_json_response(fake_anthropic, tmp_path):
    fake_anthropic["response_text"] = (
        '```json\n'
        '{"is_game": true, "name": "Path of Exile 2", '
        '"matches_existing_game_id": null, "confidence": 0.9, "reason": "ok"}\n'
        '```'
    )
    parsed = games.identify_game_from_screenshot(
        api_key="k",
        model="m",
        window_title="Path of Exile 2",
        image_path=_png(tmp_path),
    )
    assert parsed is not None
    assert parsed["is_game"] is True
    assert parsed["name"] == "Path of Exile 2"


def test_identify_game_returns_none_on_no_json(fake_anthropic, tmp_path):
    fake_anthropic["response_text"] = "no json here"
    assert games.identify_game_from_screenshot(
        api_key="k", model="m", window_title="X", image_path=_png(tmp_path),
    ) is None


# ---- accept_identification ----


def test_accept_identification_creates_new_game(fake_anthropic, tmp_path):
    bound = games.accept_identification(
        "Path of Exile 2",
        {"is_game": True, "name": "Path of Exile 2", "matches_existing_game_id": None, "confidence": 0.9},
    )
    assert bound == "path-of-exile-2"
    assert games.get_game("path-of-exile-2") is not None
    assert games.get_binding("Path of Exile 2") == "path-of-exile-2"


def test_accept_identification_matches_existing():
    games.upsert_game(games.GameEntry(game_id="poe2", display_name="Path of Exile 2"))
    bound = games.accept_identification(
        "Path of Exile 2 - Updating",
        {"is_game": True, "name": "Path of Exile 2", "matches_existing_game_id": "poe2", "confidence": 0.95},
    )
    assert bound == "poe2"
    assert games.get_binding("Path of Exile 2 - Updating") == "poe2"


def test_accept_identification_records_not_a_game_verdict():
    bound = games.accept_identification(
        "Notepad",
        {"is_game": False, "name": "", "matches_existing_game_id": None, "confidence": 0.99, "reason": "text editor"},
    )
    assert bound == games.NOT_A_GAME
    assert games.get_binding("Notepad") == games.NOT_A_GAME


def test_accept_identification_skips_low_confidence():
    bound = games.accept_identification(
        "Mystery", {"is_game": True, "name": "Foo", "confidence": 0.3},
    )
    assert bound is None
    assert games.get_binding("Mystery") is None
