import ctypes
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtWidgets import QApplication

_LOG_DIR = Path.home() / "game_assistant" / "logs"
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_LOG_FILE = _LOG_DIR / "run.log"

_formatter = logging.Formatter(
    "%(asctime)s.%(msecs)03d %(name)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
_stderr_handler = logging.StreamHandler(sys.stderr)
_stderr_handler.setFormatter(_formatter)
_file_handler = RotatingFileHandler(
    _LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
)
_file_handler.setFormatter(_formatter)
logging.basicConfig(level=logging.DEBUG, handlers=[_stderr_handler, _file_handler])

# Quiet the SDK's HTTP libraries — keep their warnings but suppress chatter.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("anthropic").setLevel(logging.INFO)

logger = logging.getLogger(__name__)
logger.info("log file: %s", _LOG_FILE)

from app.main_window import MainWindow  # noqa: E402


def _enable_per_monitor_dpi() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        logger.info("DPI awareness set to PER_MONITOR_DPI_AWARE_V2")
    except (AttributeError, OSError) as exc:
        logger.warning("DPI awareness setup failed: %r", exc)


def main() -> int:
    logger.info("starting app")
    _enable_per_monitor_dpi()
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    logger.info("entering Qt event loop")
    rc = app.exec()
    logger.info("Qt event loop exited rc=%d", rc)
    return rc


if __name__ == "__main__":
    sys.exit(main())
