import dataclasses
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

SESSION_ROOT = Path.home() / "game_assistant" / "sessions"


@dataclasses.dataclass(frozen=True)
class Session:
    folder: Path

    @property
    def screenshot_count(self) -> int:
        return sum(1 for _ in self.folder.glob("shot_*.png"))


def new_session() -> Session:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    folder = SESSION_ROOT / timestamp
    folder.mkdir(parents=True, exist_ok=True)
    logger.info("new_session folder=%s", folder)
    return Session(folder=folder)


def capture_path(folder: Path, when: datetime | None = None) -> Path:
    when = when or datetime.now()
    base = f"shot_{when.strftime('%Y%m%d_%H%M%S')}"
    path = folder / f"{base}.png"
    if not path.exists():
        return path
    counter = 2
    while True:
        candidate = folder / f"{base}_{counter}.png"
        if not candidate.exists():
            return candidate
        counter += 1
