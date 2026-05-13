import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

GOALS_DIR = Path.home() / "game_assistant" / "goals"
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9 _\-]+")


def sanitize_name(name: str) -> str:
    safe = _SAFE_NAME_RE.sub("", name).strip()
    if not safe:
        raise ValueError("Goal name must contain at least one letter, digit, dash, underscore, or space.")
    return safe


def _path_for(name: str) -> Path:
    return GOALS_DIR / f"{name}.md"


def list_goals() -> list[str]:
    if not GOALS_DIR.exists():
        return []
    names = sorted(p.stem for p in GOALS_DIR.glob("*.md"))
    logger.debug("list_goals -> %d (%s)", len(names), names)
    return names


def load_goal(name: str) -> str:
    path = _path_for(name)
    if not path.exists():
        logger.warning("load_goal missing file for %r (%s)", name, path)
        return ""
    text = path.read_text(encoding="utf-8")
    logger.info("load_goal %r (%d chars)", name, len(text))
    return text


def save_goal(name: str, content: str) -> None:
    GOALS_DIR.mkdir(parents=True, exist_ok=True)
    path = _path_for(name)
    path.write_text(content, encoding="utf-8")
    logger.info("save_goal %r (%d chars) -> %s", name, len(content), path)


def create_goal(name: str) -> str:
    safe = sanitize_name(name)
    path = _path_for(safe)
    if path.exists():
        logger.warning("create_goal refused: %r already exists", safe)
        raise FileExistsError(safe)
    GOALS_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    logger.info("create_goal %r -> %s", safe, path)
    return safe


def delete_goal(name: str) -> None:
    path = _path_for(name)
    if path.exists():
        path.unlink()
        logger.info("delete_goal %r removed %s", name, path)
    else:
        logger.warning("delete_goal missing file for %r (%s)", name, path)
