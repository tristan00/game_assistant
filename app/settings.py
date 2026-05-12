import json
import logging
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

SETTINGS_PATH = Path.home() / "game_assistant" / "settings.json"

DEFAULTS: dict = {
    "interval_seconds": 60,
    "last_n": 5,
    "model": "claude-sonnet-4-6",
    "hotkey_qt": "Ctrl+Alt+S",
    "active_strategy": "",
    "web_search_max_uses": 2,
}


def load_settings() -> dict:
    s = dict(DEFAULTS)
    if SETTINGS_PATH.exists():
        try:
            raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            for key in DEFAULTS:
                if key in raw:
                    s[key] = _coerce(key, raw[key])
            logger.info("loaded settings from %s: %s", SETTINGS_PATH, s)
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("failed to read settings file %s: %r; using defaults", SETTINGS_PATH, exc)
    else:
        logger.info("no settings file at %s; using defaults: %s", SETTINGS_PATH, s)
    return s


def save_settings(updates: dict) -> None:
    current = load_settings()
    for key, value in updates.items():
        if key in DEFAULTS:
            current[key] = _coerce(key, value)
        else:
            logger.warning("save_settings: ignoring unknown key %r", key)
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write: tmpfile + rename. Avoids half-written JSON if interrupted.
    tmp_fd, tmp_path = tempfile.mkstemp(prefix="settings_", suffix=".tmp", dir=str(SETTINGS_PATH.parent))
    try:
        with open(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=2, sort_keys=True)
        Path(tmp_path).replace(SETTINGS_PATH)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise
    logger.info("saved settings: %s", updates)


def _coerce(key: str, value) -> object:
    default = DEFAULTS[key]
    if isinstance(default, bool):
        return bool(value)
    if isinstance(default, int):
        return int(value)
    if isinstance(default, str):
        return str(value)
    return value


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
