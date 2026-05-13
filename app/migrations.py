"""One-shot startup migrations for ~/game_assistant/ data.

Each migration is a string id + function. Applied ids are recorded in
``~/game_assistant/.migrations_done`` (a JSON list, atomic write). Failed
migrations are NOT recorded, so they retry on next launch.

Called from ``app/__init__.py`` on import.
"""

import json
import logging
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path.home() / "game_assistant"
DONE_PATH = ROOT / ".migrations_done"


def _load_done() -> list[str]:
    if not DONE_PATH.exists():
        return []
    try:
        data = json.loads(DONE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("failed to read %s: %r; treating as empty", DONE_PATH, exc)
        return []
    if not isinstance(data, list):
        logger.error("%s contained non-list; treating as empty", DONE_PATH)
        return []
    return [str(x) for x in data]


def _save_done(done: list[str]) -> None:
    ROOT.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="migrations_done_", suffix=".tmp", dir=str(ROOT))
    try:
        with open(fd, "w", encoding="utf-8") as f:
            json.dump(done, f, indent=2)
        Path(tmp).replace(DONE_PATH)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def _migrate_strategies_to_goals_dir() -> None:
    src = ROOT / "strategies"
    dst = ROOT / "goals"
    if not src.exists():
        logger.info("strategies_to_goals_dir: no %s; nothing to migrate", src)
        return
    if not dst.exists():
        logger.info("strategies_to_goals_dir: moving %s -> %s", src, dst)
        shutil.move(str(src), str(dst))
        return
    logger.warning(
        "strategies_to_goals_dir: BOTH %s and %s exist; copying files with conflict-rename",
        src, dst,
    )
    for md in sorted(src.glob("*.md")):
        target = dst / md.name
        if not target.exists():
            shutil.copy2(md, target)
            logger.info("strategies_to_goals_dir: copied %s -> %s", md.name, target)
            continue
        renamed = dst / f"{md.stem}__from_strategies.md"
        n = 1
        while renamed.exists():
            n += 1
            renamed = dst / f"{md.stem}__from_strategies_{n}.md"
        shutil.copy2(md, renamed)
        logger.warning(
            "strategies_to_goals_dir: %s conflicts with existing %s; wrote %s instead",
            md.name, target.name, renamed.name,
        )


def _migrate_init_wikis_dir() -> None:
    (ROOT / "wikis").mkdir(parents=True, exist_ok=True)
    logger.info("init_wikis_dir: ensured %s", ROOT / "wikis")


MIGRATIONS: list[tuple[str, Callable[[], None]]] = [
    ("strategies_to_goals_dir", _migrate_strategies_to_goals_dir),
    ("init_wikis_dir", _migrate_init_wikis_dir),
]


def run_startup_migrations() -> None:
    """Run any not-yet-applied migrations; record successes in .migrations_done."""
    done = _load_done()
    changed = False
    for mid, fn in MIGRATIONS:
        if mid in done:
            logger.debug("migration %s already applied; skipping", mid)
            continue
        logger.info("migration %s: starting", mid)
        try:
            fn()
        except Exception:
            logger.exception("migration %s FAILED; will retry on next launch", mid)
            continue
        done.append(mid)
        changed = True
        logger.info("migration %s: done", mid)
    if changed:
        _save_done(done)
