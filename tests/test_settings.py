import json

import pytest

from app import settings as settings_module
from app.settings import (
    DEFAULTS,
    load_settings,
    qt_hotkey_to_pynput,
    save_settings,
)


# ---- load_settings / save_settings ----


def test_load_returns_defaults_when_file_missing():
    assert load_settings() == DEFAULTS


def test_save_then_load_roundtrip():
    save_settings({"interval_seconds": 30, "model": "claude-haiku-4-5"})
    loaded = load_settings()
    assert loaded["interval_seconds"] == 30
    assert loaded["model"] == "claude-haiku-4-5"
    # Other keys still defaults.
    assert loaded["last_n"] == DEFAULTS["last_n"]


def test_save_persists_values_matching_default():
    """Drop-defaults logic was removed: the file now stores whatever the
    user saved, even if it equals the current default."""
    save_settings({"interval_seconds": DEFAULTS["interval_seconds"]})
    raw = json.loads(settings_module.SETTINGS_PATH.read_text(encoding="utf-8"))
    assert raw["interval_seconds"] == DEFAULTS["interval_seconds"]


def test_save_ignores_unknown_keys():
    save_settings({"interval_seconds": 99, "definitely_not_a_key": "nope"})
    loaded = load_settings()
    assert loaded["interval_seconds"] == 99
    assert "definitely_not_a_key" not in loaded


def test_load_raises_on_corrupt_json():
    settings_module.SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    settings_module.SETTINGS_PATH.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        load_settings()


def test_load_merges_partial_overrides():
    settings_module.SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    settings_module.SETTINGS_PATH.write_text(
        json.dumps({"interval_seconds": 11}), encoding="utf-8"
    )
    loaded = load_settings()
    assert loaded["interval_seconds"] == 11
    assert loaded["model"] == DEFAULTS["model"]


# ---- qt_hotkey_to_pynput ----


@pytest.mark.parametrize(
    "qt,expected",
    [
        ("Ctrl+Alt+S", "<ctrl>+<alt>+s"),
        ("ctrl+alt+s", "<ctrl>+<alt>+s"),
        ("Ctrl+Shift+F1", "<ctrl>+<shift>+<f1>"),
        ("Meta+K", "<meta>+k"),
        ("A", "a"),
        ("", ""),
    ],
)
def test_qt_hotkey_to_pynput(qt, expected):
    assert qt_hotkey_to_pynput(qt) == expected
