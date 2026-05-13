import argparse
import ctypes
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

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


def _enable_per_monitor_dpi() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        logger.info("DPI awareness set to PER_MONITOR_DPI_AWARE_V2")
    except (AttributeError, OSError) as exc:
        logger.warning("DPI awareness setup failed: %r", exc)


def _run_native_shell() -> int:
    """Default mode: boot uvicorn in-process and render the web UI in a pywebview window."""
    import threading
    import time

    import uvicorn
    import webview

    from app.web_server import create_app

    logger.info("starting native shell")
    _enable_per_monitor_dpi()

    app = create_app()
    # Ephemeral port (0) — we'll read the actual port after the server binds, so a
    # stale process holding 8765 can't break startup.
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=0,
        log_level="info",
        log_config=None,  # use our own logging config
    )
    server = uvicorn.Server(config)

    def _serve() -> None:
        try:
            server.run()
        except Exception:
            logger.exception("uvicorn server crashed")

    server_thread = threading.Thread(target=_serve, name="uvicorn", daemon=True)
    server_thread.start()
    logger.info("uvicorn thread started; waiting for bind")

    # Wait for the server to bind. server.started flips True once startup is complete.
    deadline = time.monotonic() + 10.0
    while not server.started:
        if time.monotonic() > deadline:
            logger.error("uvicorn failed to start within 10s; aborting native shell")
            server.should_exit = True
            server_thread.join(timeout=2.0)
            return 1
        if not server_thread.is_alive():
            logger.error("uvicorn thread died before reporting started; aborting")
            return 1
        time.sleep(0.05)

    try:
        port = server.servers[0].sockets[0].getsockname()[1]
    except (IndexError, AttributeError, OSError) as exc:
        logger.error("could not read uvicorn bound port: %r", exc)
        server.should_exit = True
        server_thread.join(timeout=2.0)
        return 1

    url = f"http://127.0.0.1:{port}/"
    logger.info("uvicorn bound to %s; opening pywebview window", url)

    try:
        webview.create_window("game_assistant", url, width=1200, height=900)
        webview.start()  # blocks on the main thread until the window closes
    except Exception:
        logger.exception("pywebview failed")
        server.should_exit = True
        server_thread.join(timeout=2.0)
        return 1

    logger.info("pywebview window closed; signaling uvicorn shutdown")
    server.should_exit = True
    server_thread.join(timeout=5.0)
    if server_thread.is_alive():
        logger.warning("uvicorn thread did not exit within 5s")
    else:
        logger.info("uvicorn thread exited cleanly")
    return 0


def _run_web(host: str, port: int, open_browser: bool) -> int:
    from app.web_server import run as run_web_server

    logger.info("starting web server host=%s port=%d open_browser=%s", host, port, open_browser)
    return run_web_server(host=host, port=port, open_browser=open_browser)


def main() -> int:
    parser = argparse.ArgumentParser(prog="game_assistant", description="game_assistant — native shell (default) or headless web server")
    parser.add_argument("--web", action="store_true", help="Headless/dev mode: serve the web UI and open the system browser instead of the native window.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (--web only). Default 127.0.0.1 (localhost only).")
    parser.add_argument("--port", type=int, default=8765, help="Bind port (--web only). Default 8765.")
    parser.add_argument("--no-browser", action="store_true", help="Don't auto-open the browser (--web only).")
    args, _ = parser.parse_known_args()

    logger.info("starting app web=%s host=%s port=%d", args.web, args.host, args.port)

    # One-shot disk migrations under ~/game_assistant/. Idempotent.
    from app.migrations import run_startup_migrations
    run_startup_migrations()

    if args.web:
        return _run_web(host=args.host, port=args.port, open_browser=not args.no_browser)
    return _run_native_shell()


if __name__ == "__main__":
    sys.exit(main())
