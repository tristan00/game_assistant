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
    "active_goal": "",
    # Phase 1: game knowledge layer
    "wiki_rate_seconds": 1.0,
    "wiki_user_agent": "game_assistant (+contact: tristandelforge1@gmail.com)",
    "game_id_model": "claude-haiku-4-5-20251001",
    "wiki_discovery_model": "claude-sonnet-4-6",
    "quick_ref_model": "claude-sonnet-4-6",
    # Phase 2: corpus routing
    "enable_prompt_cache": True,
    "client_tool_max_iters": 6,
    # Phase 3: two-stage perception
    "perception_stage1_model": "claude-haiku-4-5-20251001",
    "perception_stage2_model": "claude-sonnet-4-6",
    "schema_builder_model": "claude-sonnet-4-6",
}


def _migrate_raw(raw: dict) -> dict:
    """In-place key migrations on raw JSON loaded from disk.

    The legacy ``active_strategy`` key is dropped rather than carried over —
    the rename to "goals" is intentional, and re-selecting your goal is a
    one-time cost. Default state after rename is "no goal active".
    """
    if "active_strategy" in raw:
        logger.info("settings migration: dropping legacy active_strategy=%r (goal defaults to none)", raw.get("active_strategy"))
    raw.pop("active_strategy", None)
    return raw


def load_settings() -> dict:
    s = dict(DEFAULTS)
    if SETTINGS_PATH.exists():
        raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        raw = _migrate_raw(raw)
        for key in DEFAULTS:
            if key in raw:
                s[key] = raw[key]
        logger.info("loaded settings from %s: %s", SETTINGS_PATH, s)
    else:
        logger.info("no settings file at %s; using defaults: %s", SETTINGS_PATH, s)
    return s


def save_settings(updates: dict) -> None:
    """Persist updates merged onto the existing file contents."""
    raw: dict = {}
    if SETTINGS_PATH.exists():
        raw = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    raw = _migrate_raw(raw)

    for key, value in updates.items():
        if key not in DEFAULTS:
            logger.warning("save_settings: ignoring unknown key %r", key)
            continue
        raw[key] = value

    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write: tmpfile + rename.
    tmp_fd, tmp_path = tempfile.mkstemp(prefix="settings_", suffix=".tmp", dir=str(SETTINGS_PATH.parent))
    try:
        with open(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(raw, f, indent=2, sort_keys=True)
        Path(tmp_path).replace(SETTINGS_PATH)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise
    logger.info("saved settings: %s", raw)


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
