import logging
import time
import traceback
from pathlib import Path

from PySide6.QtCore import Qt, QThreadPool, QTimer
from PySide6.QtGui import QCloseEvent, QTextCursor

logger = logging.getLogger(__name__)
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenuBar,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.capture import capture_window, list_windows
from app.claude_client import ClaudeWorker
from app.config import get_api_key, set_api_key
from app.hotkey import DEFAULT_HOTKEY, HotkeyManager
from app.session import Session, capture_path, new_session
from app.settings import load_settings, qt_hotkey_to_pynput, save_settings
from app.settings_dialog import SettingsDialog

_MODELS = [
    "claude-sonnet-4-6",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-haiku-4-5-20251001",
]


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("game_assistant")
        self.resize(1000, 800)

        self.settings = load_settings()
        self.session: Session = new_session()
        self.history: list[dict[str, str]] = []
        self.thread_pool = QThreadPool.globalInstance()
        self._submit_start: float | None = None
        # CRITICAL: keep workers alive until they finish, otherwise the Python wrapper
        # (and its Signal connections) can be GC'd before the slot fires on the main thread.
        self._active_workers: set = set()
        logger.info(
            "MainWindow init: thread_pool max=%d active=%d",
            self.thread_pool.maxThreadCount(),
            self.thread_pool.activeThreadCount(),
        )

        self._build_menu()
        self._build_ui()

        # Capture timer
        self.capture_timer = QTimer(self)
        self.capture_timer.setInterval(self.settings["interval_seconds"] * 1000)
        self.capture_timer.timeout.connect(self._timer_capture)

        # Elapsed-time ticker for in-flight requests
        self.elapsed_timer = QTimer(self)
        self.elapsed_timer.setInterval(500)
        self.elapsed_timer.timeout.connect(self._tick_elapsed)

        # Global hotkey (using persisted hotkey, falling back to default if unparseable)
        pynput_hotkey = qt_hotkey_to_pynput(self.settings["hotkey_qt"]) or DEFAULT_HOTKEY
        self.hotkey = HotkeyManager(hotkey=pynput_hotkey)
        self.hotkey.triggered.connect(self._hotkey_capture)
        self.hotkey.start()

        self._refresh_windows()
        self._update_session_label()

        # First-run API key prompt
        QTimer.singleShot(0, self._ensure_api_key)

    # ---- UI construction ----

    def _build_menu(self) -> None:
        menubar: QMenuBar = self.menuBar()
        settings_menu = menubar.addMenu("Settings")
        prefs = settings_menu.addAction("Preferences…")
        prefs.triggered.connect(self._open_settings_dialog)
        update_key = settings_menu.addAction("Update API key…")
        update_key.triggered.connect(self._prompt_api_key)

    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # Window row
        window_row = QHBoxLayout()
        window_row.addWidget(QLabel("Window:"))
        self.window_combo = QComboBox()
        self.window_combo.setMinimumWidth(400)
        self.window_combo.currentIndexChanged.connect(self._on_window_selected)
        window_row.addWidget(self.window_combo, stretch=1)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self._refresh_windows)
        window_row.addWidget(self.refresh_button)
        self.capture_button = QPushButton("Capture")
        self.capture_button.clicked.connect(self._manual_capture)
        window_row.addWidget(self.capture_button)
        layout.addLayout(window_row)

        # Session row
        session_row = QHBoxLayout()
        self.session_label = QLabel()
        session_row.addWidget(self.session_label, stretch=1)
        session_row.addWidget(QLabel("Interval:"))
        self.interval_spinbox = QSpinBox()
        self.interval_spinbox.setRange(5, 3600)
        self.interval_spinbox.setValue(self.settings["interval_seconds"])
        self.interval_spinbox.setSuffix(" s")
        self.interval_spinbox.valueChanged.connect(self._on_interval_changed)
        session_row.addWidget(self.interval_spinbox)
        self.new_session_button = QPushButton("New session")
        self.new_session_button.clicked.connect(self._new_session)
        session_row.addWidget(self.new_session_button)
        layout.addLayout(session_row)

        # Model row
        model_row = QHBoxLayout()
        model_row.addWidget(QLabel("Model:"))
        self.model_combo = QComboBox()
        self.model_combo.addItems(_MODELS)
        if self.settings["model"] in _MODELS:
            self.model_combo.setCurrentText(self.settings["model"])
        self.model_combo.currentTextChanged.connect(self._on_model_changed)
        model_row.addWidget(self.model_combo)
        model_row.addWidget(QLabel("Last N:"))
        self.last_n_spinbox = QSpinBox()
        self.last_n_spinbox.setRange(1, 50)
        self.last_n_spinbox.setValue(self.settings["last_n"])
        self.last_n_spinbox.valueChanged.connect(self._on_last_n_changed)
        model_row.addWidget(self.last_n_spinbox)
        model_row.addStretch(1)
        layout.addLayout(model_row)

        # Game context
        layout.addWidget(QLabel("Game context (optional):"))
        self.game_context_edit = QLineEdit()
        self.game_context_edit.setPlaceholderText(
            "e.g., Total War: Warhammer 3, Skarbrand campaign"
        )
        layout.addWidget(self.game_context_edit)

        # Question
        layout.addWidget(QLabel("Question:"))
        self.question_edit = QTextEdit()
        self.question_edit.setFixedHeight(80)
        layout.addWidget(self.question_edit)

        # Submit
        submit_row = QHBoxLayout()
        submit_row.addStretch(1)
        self.submit_button = QPushButton("Submit")
        self.submit_button.clicked.connect(self._submit)
        submit_row.addWidget(self.submit_button)
        layout.addLayout(submit_row)

        # In-flight banner
        self.pending_label = QLabel()
        self.pending_label.setWordWrap(True)
        self.pending_label.setStyleSheet(
            "background: #1f3a5f; color: white; font-weight: bold; "
            "padding: 8px; border-radius: 4px;"
        )
        self.pending_label.hide()
        layout.addWidget(self.pending_label)

        # Q&A log
        layout.addWidget(QLabel("Q&A log:"))
        self.qa_log = QTextEdit()
        self.qa_log.setReadOnly(True)
        layout.addWidget(self.qa_log, stretch=1)

        # Footer
        self.status_label = QLabel("Pick a window. Capture starts automatically.")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.hotkey_hint = QLabel(
            f"Manual capture: click Capture or press {self.settings['hotkey_qt']}"
        )
        self.hotkey_hint.setStyleSheet("color: gray;")
        layout.addWidget(self.hotkey_hint)

    # ---- API key ----

    def _ensure_api_key(self) -> None:
        if get_api_key():
            return
        self._prompt_api_key(first_run=True)

    def _prompt_api_key(self, *, first_run: bool = False) -> None:
        title = "Anthropic API key" + (" (first-run setup)" if first_run else "")
        prompt = (
            "Enter your Anthropic API key.\n\n"
            "Don't have one? Create an account and generate a key at:\n"
            "https://console.anthropic.com/settings/keys\n\n"
            "You can dismiss this dialog and add the key later via Settings → "
            "Update API key. The app works without a key for screenshot capture; "
            "the key is only needed to ask Claude questions."
        )
        key, ok = QInputDialog.getText(
            self,
            title,
            prompt,
            QLineEdit.EchoMode.Password,
        )
        if ok and key.strip():
            set_api_key(key.strip())
            self.status_label.setText("API key saved.")

    # ---- window list / selection ----

    def _refresh_windows(self) -> None:
        self.window_combo.clear()
        try:
            windows = list_windows()
        except Exception:
            QMessageBox.critical(self, "Enumeration failed", traceback.format_exc())
            return

        if not windows:
            self.status_label.setText("No top-level visible windows found.")
            return

        for hwnd, title in windows:
            display = title if len(title) <= 80 else title[:77] + "…"
            self.window_combo.addItem(f"{display}  [hwnd {hwnd}]", userData=hwnd)
        self.status_label.setText(f"{len(windows)} windows.")

    def _on_window_selected(self) -> None:
        hwnd = self.window_combo.currentData()
        if hwnd is None:
            self.capture_timer.stop()
            return
        if not self.capture_timer.isActive():
            self.capture_timer.start()

    def _on_interval_changed(self, seconds: int) -> None:
        self.capture_timer.setInterval(seconds * 1000)
        self.settings["interval_seconds"] = seconds
        save_settings({"interval_seconds": seconds})

    def _on_model_changed(self, model: str) -> None:
        self.settings["model"] = model
        save_settings({"model": model})

    def _on_last_n_changed(self, value: int) -> None:
        self.settings["last_n"] = value
        save_settings({"last_n": value})

    def _open_settings_dialog(self) -> None:
        dlg = SettingsDialog(dict(self.settings), parent=self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        new = dlg.values()
        save_settings(new)
        self.settings.update(new)
        # Apply to inline widgets (block signals to avoid re-persisting)
        self.model_combo.blockSignals(True)
        if new["model"] in _MODELS:
            self.model_combo.setCurrentText(new["model"])
        self.model_combo.blockSignals(False)
        self.interval_spinbox.blockSignals(True)
        self.interval_spinbox.setValue(new["interval_seconds"])
        self.interval_spinbox.blockSignals(False)
        self.last_n_spinbox.blockSignals(True)
        self.last_n_spinbox.setValue(new["last_n"])
        self.last_n_spinbox.blockSignals(False)
        self.capture_timer.setInterval(new["interval_seconds"] * 1000)
        # Update hotkey live
        pynput_hotkey = qt_hotkey_to_pynput(new["hotkey_qt"]) or DEFAULT_HOTKEY
        self.hotkey.set_hotkey(pynput_hotkey)
        self.hotkey_hint.setText(f"Manual capture: click Capture or press {new['hotkey_qt']}")
        self.status_label.setText("Settings saved.")

    # ---- captures ----

    def _capture_now(self, *, source: str) -> Path | None:
        hwnd = self.window_combo.currentData()
        if hwnd is None:
            logger.info("_capture_now source=%s aborted: no window selected", source)
            self.status_label.setText(f"{source}: pick a window first.")
            return None
        try:
            png_bytes = capture_window(int(hwnd))
        except Exception:
            tb = traceback.format_exc()
            logger.error("_capture_now source=%s failed:\n%s", source, tb)
            if source == "timer":
                self.capture_timer.stop()
                self.status_label.setText("Capture failed — timer stopped. Pick a window to resume.")
            else:
                self.status_label.setText(f"{source}: capture failed (see dialog).")
            QMessageBox.critical(self, "Capture failed", tb)
            return None

        path = capture_path(self.session.folder)
        path.write_bytes(png_bytes)
        logger.info("_capture_now source=%s saved %s (%d bytes)", source, path.name, len(png_bytes))
        self._update_session_label()
        self.status_label.setText(f"{source}: saved {path.name} ({len(png_bytes):,} bytes)")
        return path

    def _timer_capture(self) -> None:
        self._capture_now(source="timer")

    def _manual_capture(self) -> None:
        self._capture_now(source="button")

    def _hotkey_capture(self) -> None:
        self._capture_now(source="hotkey")

    # ---- session ----

    def _new_session(self) -> None:
        self.session = new_session()
        self.history.clear()
        self.qa_log.clear()
        self._update_session_label()
        self.status_label.setText(f"New session: {self.session.folder.name} (history cleared)")

    def _update_session_label(self) -> None:
        self.session_label.setText(
            f"Active session: {self.session.folder.name} — "
            f"{self.session.screenshot_count} shots"
        )

    # ---- submit ----

    def _submit(self) -> None:
        logger.info("_submit clicked")
        question = self.question_edit.toPlainText().strip()
        if not question:
            logger.info("_submit aborted: empty question")
            self.status_label.setText("Type a question first.")
            return

        api_key = get_api_key()
        if not api_key:
            logger.warning("_submit aborted: no API key")
            self._append_log("Error: no API key set. Settings → Update API key.", error=True)
            return

        if self.window_combo.currentData() is None:
            logger.warning("_submit aborted: no window selected")
            self._append_log("Error: pick a window first.", error=True)
            return

        # Fresh capture before request
        fresh = self._capture_now(source="submit")
        if fresh is None:
            logger.warning("_submit aborted: pre-submit capture failed")
            self._append_log("Error: pre-submit capture failed; aborting request.", error=True)
            return

        last_n = self.last_n_spinbox.value()
        image_paths = self._collect_last_n(last_n)
        logger.info("_submit collected %d image paths (last_n=%d)", len(image_paths), last_n)
        if not image_paths:
            logger.warning("_submit aborted: no screenshots in session folder")
            self._append_log("Error: no screenshots in session folder.", error=True)
            return

        self.submit_button.setEnabled(False)
        self.question_edit.setEnabled(False)

        model = self.model_combo.currentText()
        self._submit_start = time.monotonic()
        self._pending_model = model
        self._pending_n_images = len(image_paths)
        self._pending_question_excerpt = self._excerpt(question, 80)
        self.pending_label.show()
        self._tick_elapsed()
        self.elapsed_timer.start()
        self.status_label.setText(f"Sending {len(image_paths)} image(s) to {model}…")

        worker = ClaudeWorker(
            api_key=api_key,
            model=model,
            history=list(self.history),
            game_context=self.game_context_edit.text(),
            question=question,
            image_paths=image_paths,
        )
        # Keep a strong Python ref so the wrapper isn't GC'd while the C++ runnable lives.
        self._active_workers.add(worker)
        logger.info("worker#%d queued; active_workers=%d", worker.worker_id, len(self._active_workers))

        wid = worker.worker_id

        def _on_result_wrapper(text: str, q: str = question, w=worker) -> None:
            logger.info("worker#%d result slot fired on main thread", wid)
            try:
                self._on_result(q, text)
            finally:
                self._active_workers.discard(w)
                logger.info("worker#%d cleaned up; active_workers=%d", wid, len(self._active_workers))

        def _on_error_wrapper(err: str, w=worker) -> None:
            logger.info("worker#%d error slot fired on main thread", wid)
            try:
                self._on_error(err)
            finally:
                self._active_workers.discard(w)
                logger.info("worker#%d cleaned up; active_workers=%d", wid, len(self._active_workers))

        worker.signals.result.connect(_on_result_wrapper)
        worker.signals.error.connect(_on_error_wrapper)
        self.thread_pool.start(worker)
        logger.info("worker#%d started in thread pool", wid)

    def _collect_last_n(self, n: int) -> list[Path]:
        pngs = sorted(self.session.folder.glob("shot_*.png"), key=lambda p: p.stat().st_mtime)
        return pngs[-n:]

    def _tick_elapsed(self) -> None:
        if self._submit_start is None:
            return
        elapsed = int(time.monotonic() - self._submit_start)
        # Log at coarse milestones so terminal isn't flooded but hangs are visible.
        if elapsed in (10, 30, 60, 90, 120, 180, 300) and elapsed != getattr(self, "_last_logged_elapsed", -1):
            logger.warning(
                "still waiting after %ds; active_workers=%d, pool active=%d",
                elapsed,
                len(self._active_workers),
                self.thread_pool.activeThreadCount(),
            )
            self._last_logged_elapsed = elapsed
        self.pending_label.setText(
            f"⏳ Waiting on {self._pending_model} ({self._pending_n_images} image"
            f"{'s' if self._pending_n_images != 1 else ''}) — "
            f"{self._fmt_elapsed(elapsed)}\n"
            f"You: {self._pending_question_excerpt}"
        )

    def _finish_pending(self) -> int:
        elapsed = int(time.monotonic() - self._submit_start) if self._submit_start else 0
        self.elapsed_timer.stop()
        self._submit_start = None
        self._last_logged_elapsed = -1
        self.pending_label.hide()
        self.submit_button.setEnabled(True)
        self.question_edit.setEnabled(True)
        logger.info("_finish_pending elapsed=%ds", elapsed)
        return elapsed

    def _on_result(self, question: str, response_text: str) -> None:
        elapsed = self._finish_pending()
        self.history.append({"question": question, "response": response_text})
        self._append_plain(f"You: {question}\n\n")
        self._append_plain(f"Claude ({self._fmt_elapsed(elapsed)}): {response_text}\n\n")
        self._append_plain("---\n\n")
        self.question_edit.clear()
        self.status_label.setText(f"Response received in {self._fmt_elapsed(elapsed)}.")

    def _on_error(self, error_text: str) -> None:
        elapsed = self._finish_pending()
        self._append_log(f"[after {self._fmt_elapsed(elapsed)}]\n{error_text}", error=True)
        self.status_label.setText(
            f"Submit failed after {self._fmt_elapsed(elapsed)} (see Q&A log)."
        )

    def _append_plain(self, text: str) -> None:
        cursor = self.qa_log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        self._scroll_log_to_end()

    @staticmethod
    def _fmt_elapsed(seconds: int) -> str:
        if seconds < 60:
            return f"{seconds}s"
        return f"{seconds // 60}m {seconds % 60}s"

    @staticmethod
    def _excerpt(text: str, max_chars: int) -> str:
        text = text.replace("\n", " ").strip()
        return text if len(text) <= max_chars else text[: max_chars - 1] + "…"

    def _append_log(self, text: str, *, error: bool = False) -> None:
        if error:
            cursor = self.qa_log.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            cursor.insertHtml(
                f'<div style="color:#c00;"><b>Error:</b><pre style="white-space:pre-wrap;">'
                f'{self._escape(text)}</pre></div><hr/>'
            )
            cursor.insertText("\n")
        else:
            self._append_plain(text + "\n")
        self._scroll_log_to_end()

    @staticmethod
    def _escape(text: str) -> str:
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

    def _scroll_log_to_end(self) -> None:
        cursor = self.qa_log.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.qa_log.setTextCursor(cursor)
        self.qa_log.ensureCursorVisible()

    # ---- lifecycle ----

    def closeEvent(self, event: QCloseEvent) -> None:
        self.capture_timer.stop()
        self.hotkey.stop()
        super().closeEvent(event)
