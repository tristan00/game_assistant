import logging
from typing import Callable

from pynput import keyboard

logger = logging.getLogger(__name__)

DEFAULT_HOTKEY = "<ctrl>+<alt>+s"


class HotkeyManager:
    """Listens for a global hotkey via pynput and invokes a callback when fired.

    The callback runs on pynput's listener thread. Callers must ensure the
    callback is thread-safe (e.g. uses locks or marshals work onto another loop).
    """

    def __init__(self, hotkey: str = DEFAULT_HOTKEY, callback: Callable[[], None] | None = None) -> None:
        self._hotkey = hotkey
        self._callback = callback
        self._listener: keyboard.GlobalHotKeys | None = None

    def set_callback(self, callback: Callable[[], None]) -> None:
        self._callback = callback
        logger.info("hotkey callback set")

    def start(self) -> None:
        if self._listener is not None:
            return
        if not self._hotkey:
            logger.warning("hotkey listener not started: hotkey is empty")
            return
        if self._callback is None:
            logger.warning("hotkey listener not started: no callback set")
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
        logger.debug("hotkey fired")
        cb = self._callback
        if cb is None:
            logger.warning("hotkey fired but no callback registered")
            return
        try:
            cb()
        except Exception:
            logger.exception("hotkey callback raised")
