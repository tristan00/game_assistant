import logging

from PySide6.QtCore import QSettings

logger = logging.getLogger(__name__)

ORG = "game_assistant"
APP = "game_assistant"

DEFAULTS: dict = {
    "interval_seconds": 60,
    "last_n": 5,
    "model": "claude-sonnet-4-6",
    "hotkey_qt": "Ctrl+Alt+S",
}


def _qs() -> QSettings:
    return QSettings(ORG, APP)


def load_settings() -> dict:
    qs = _qs()
    s = {
        "interval_seconds": int(qs.value("interval_seconds", DEFAULTS["interval_seconds"])),
        "last_n": int(qs.value("last_n", DEFAULTS["last_n"])),
        "model": str(qs.value("model", DEFAULTS["model"])),
        "hotkey_qt": str(qs.value("hotkey_qt", DEFAULTS["hotkey_qt"])),
    }
    logger.info("loaded settings: %s", s)
    return s


def save_settings(s: dict) -> None:
    qs = _qs()
    for key, value in s.items():
        qs.setValue(key, value)
    qs.sync()
    logger.info("saved settings: %s", s)


def qt_hotkey_to_pynput(seq: str) -> str:
    """Convert a Qt key sequence like 'Ctrl+Alt+S' to pynput format '<ctrl>+<alt>+s'."""
    if not seq:
        return ""
    modifiers = {"ctrl", "alt", "shift", "meta"}
    parts = [p.strip().lower() for p in seq.split("+")]
    out = []
    for p in parts:
        if not p:
            continue
        if p in modifiers or len(p) > 1:
            out.append(f"<{p}>")
        else:
            out.append(p)
    return "+".join(out)
