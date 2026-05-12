import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

STRATEGY_DIR = Path.home() / "game_assistant" / "strategies"
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9 _\-]+")


def sanitize_name(name: str) -> str:
    safe = _SAFE_NAME_RE.sub("", name).strip()
    if not safe:
        raise ValueError("Strategy name must contain at least one letter, digit, dash, underscore, or space.")
    return safe


def _path_for(name: str) -> Path:
    return STRATEGY_DIR / f"{name}.md"


def list_strategies() -> list[str]:
    if not STRATEGY_DIR.exists():
        return []
    names = sorted(p.stem for p in STRATEGY_DIR.glob("*.md"))
    logger.debug("list_strategies -> %d (%s)", len(names), names)
    return names


def load_strategy(name: str) -> str:
    path = _path_for(name)
    if not path.exists():
        logger.warning("load_strategy missing file for %r (%s)", name, path)
        return ""
    text = path.read_text(encoding="utf-8")
    logger.info("load_strategy %r (%d chars)", name, len(text))
    return text


def save_strategy(name: str, content: str) -> None:
    STRATEGY_DIR.mkdir(parents=True, exist_ok=True)
    path = _path_for(name)
    path.write_text(content, encoding="utf-8")
    logger.info("save_strategy %r (%d chars) -> %s", name, len(content), path)


def create_strategy(name: str) -> str:
    safe = sanitize_name(name)
    path = _path_for(safe)
    if path.exists():
        logger.warning("create_strategy refused: %r already exists", safe)
        raise FileExistsError(safe)
    STRATEGY_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    logger.info("create_strategy %r -> %s", safe, path)
    return safe


def delete_strategy(name: str) -> None:
    path = _path_for(name)
    if path.exists():
        path.unlink()
        logger.info("delete_strategy %r removed %s", name, path)
    else:
        logger.warning("delete_strategy missing file for %r (%s)", name, path)
