import base64
import logging
import time
import traceback
from pathlib import Path

import anthropic
from PySide6.QtCore import QObject, QRunnable, Signal

from app.image_utils import downscale_to_jpeg
from app.prompts import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

MAX_TOKENS = 2048
REQUEST_TIMEOUT_SECONDS = 120.0


class WorkerSignals(QObject):
    result = Signal(str)
    error = Signal(str)
    finished = Signal()


class ClaudeWorker(QRunnable):
    _counter = 0

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        history: list[dict[str, str]],
        game_context: str,
        question: str,
        image_paths: list[Path],
    ) -> None:
        super().__init__()
        ClaudeWorker._counter += 1
        self.worker_id = ClaudeWorker._counter
        self.signals = WorkerSignals()
        self._api_key = api_key
        self._model = model
        self._history = history
        self._game_context = game_context
        self._question = question
        self._image_paths = image_paths
        logger.info(
            "worker#%d created model=%s history_turns=%d images=%d question_chars=%d",
            self.worker_id,
            model,
            len(history),
            len(image_paths),
            len(question),
        )

    def run(self) -> None:
        logger.info("worker#%d run() entered on thread", self.worker_id)
        try:
            text = self._call()
        except Exception:
            tb = traceback.format_exc()
            logger.error("worker#%d run() raised:\n%s", self.worker_id, tb)
            self.signals.error.emit(tb)
            self.signals.finished.emit()
            return
        logger.info("worker#%d run() success: %d chars; emitting result", self.worker_id, len(text))
        self.signals.result.emit(text)
        self.signals.finished.emit()
        logger.info("worker#%d run() returning", self.worker_id)

    def _call(self) -> str:
        logger.debug("worker#%d building Anthropic client (timeout=%.1fs)", self.worker_id, REQUEST_TIMEOUT_SECONDS)
        client = anthropic.Anthropic(api_key=self._api_key, timeout=REQUEST_TIMEOUT_SECONDS)

        messages: list[dict] = []
        for turn in self._history:
            messages.append({"role": "user", "content": turn["question"]})
            messages.append({"role": "assistant", "content": turn["response"]})

        current: list[dict] = []
        t_encode = time.monotonic()
        total_jpeg_bytes = 0
        for path in self._image_paths:
            jpeg = downscale_to_jpeg(path)
            total_jpeg_bytes += len(jpeg)
            current.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": base64.b64encode(jpeg).decode("ascii"),
                    },
                }
            )
        logger.info(
            "worker#%d encoded %d images in %.2fs, total JPEG bytes=%d",
            self.worker_id,
            len(self._image_paths),
            time.monotonic() - t_encode,
            total_jpeg_bytes,
        )

        text = self._question
        if self._game_context.strip():
            text = f"Game context: {self._game_context.strip()}\n\n{text}"
        current.append({"type": "text", "text": text})

        messages.append({"role": "user", "content": current})

        prior_response_chars = sum(len(t["response"]) for t in self._history)
        logger.info(
            "worker#%d calling messages.create model=%s history_turns=%d prior_response_chars=%d",
            self.worker_id,
            self._model,
            len(self._history),
            prior_response_chars,
        )
        t_call = time.monotonic()
        response = client.messages.create(
            model=self._model,
            system=SYSTEM_PROMPT,
            max_tokens=MAX_TOKENS,
            messages=messages,
        )
        elapsed = time.monotonic() - t_call
        logger.info(
            "worker#%d messages.create returned in %.2fs stop_reason=%s usage=%s",
            self.worker_id,
            elapsed,
            response.stop_reason,
            getattr(response, "usage", None),
        )
        return "".join(block.text for block in response.content if block.type == "text")
