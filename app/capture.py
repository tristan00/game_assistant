import io
import logging

import mss
import win32con
import win32gui
from PIL import Image

logger = logging.getLogger(__name__)

_TITLE_BLOCKLIST = {"Program Manager", "Default IME", "MSCTFIME UI"}


def list_windows() -> list[tuple[int, str]]:
    results: list[tuple[int, str]] = []

    def _enum(hwnd: int, _: object) -> bool:
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return True
        if title in _TITLE_BLOCKLIST:
            return True
        if win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE) & win32con.WS_EX_TOOLWINDOW:
            return True
        results.append((hwnd, title))
        return True

    win32gui.EnumWindows(_enum, None)
    logger.debug("list_windows -> %d windows", len(results))
    return results


def capture_window(hwnd: int) -> bytes:
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width = right - left
    height = bottom - top
    logger.debug(
        "capture_window hwnd=%d rect=(%d,%d,%d,%d) size=%dx%d",
        hwnd, left, top, right, bottom, width, height,
    )
    if width <= 0 or height <= 0:
        raise RuntimeError(
            f"Window rect is empty or offscreen: {(left, top, right, bottom)}. "
            f"Window may be minimized."
        )

    bbox = {"left": left, "top": top, "width": width, "height": height}
    with mss.mss() as sct:
        shot = sct.grab(bbox)

    image = Image.frombytes("RGB", shot.size, shot.rgb)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    data = buffer.getvalue()
    logger.debug("capture_window PNG bytes=%d", len(data))
    return data
