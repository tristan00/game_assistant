import logging

from pynput import keyboard
from PySide6.QtCore import QObject, Signal

logger = logging.getLogger(__name__)

DEFAULT_HOTKEY = "<ctrl>+<alt>+s"


class HotkeyManager(QObject):
    triggered = Signal()

    def __init__(self, hotkey: str = DEFAULT_HOTKEY, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._hotkey = hotkey
        self._listener: keyboard.GlobalHotKeys | None = None

    def start(self) -> None:
        if self._listener is not None:
            return
        if not self._hotkey:
            logger.warning("hotkey listener not started: hotkey is empty")
            return
        try:
            self._listener = keyboard.GlobalHotKeys({self._hotkey: self._fire})
            self._listener.start()
            logger.info("hotkey listener started for %s", self._hotkey)
        except Exception:
            logger.exception("failed to start hotkey listener for %r", self._hotkey)
            self._listener = None

    def set_hotkey(self, hotkey: str) -> None:
        was_running = self._listener is not None
        if was_running:
            self.stop()
        self._hotkey = hotkey
        if was_running:
            self.start()
        logger.info("hotkey set to %s", hotkey)

    def stop(self) -> None:
        if self._listener is None:
            return
        self._listener.stop()
        self._listener = None
        logger.info("hotkey listener stopped")

    def _fire(self) -> None:
        # Called on pynput's thread; Qt auto-marshals across threads via queued connection.
        logger.debug("hotkey fired")
        self.triggered.emit()
