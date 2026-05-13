"""Game registry: persistent bindings from window title -> canonical game.

Persistence layout (``~/game_assistant/games.json``):

    {
      "schema_version": 1,
      "games": {
        "<game_id>": {
          "game_id": "path-of-exile-2",
          "display_name": "Path of Exile 2",
          "wiki_url": "https://www.poewiki.net/",
          "wiki_api_url": "https://www.poewiki.net/api.php",
          "wiki_root_page": "Main_Page",
          "crawl_state": "none|running|done|failed",
          "page_count": 0,
          "last_crawl_iso": "...",
          "discovery_confidence": 0.92,
          "is_game": true
        }
      },
      "window_bindings": {
        "Path of Exile 2": "path-of-exile-2",
        "Some App": "__not_a_game__"
      }
    }

The sentinel ``__not_a_game__`` binding records LLM verdicts of "this window
isn't a game" so we don't pay to re-identify it on every reselect.
"""

import base64
import json
import logging
import re
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import anthropic

from app.image_utils import downscale_to_jpeg
from app.prompts import GAME_ID_PROMPT
from app.wiki.discovery import _parse_json_block
from app.wiki.storage import slugify

logger = logging.getLogger(__name__)

GAMES_PATH = Path.home() / "game_assistant" / "games.json"
NOT_A_GAME = "__not_a_game__"

_REQUEST_TIMEOUT_SECONDS = 120.0
_MIN_CONFIDENCE = 0.6
_lock = threading.Lock()


@dataclass
class GameEntry:
    game_id: str
    display_name: str
    wiki_url: str | None = None
    wiki_api_url: str | None = None
    wiki_root_page: str | None = None
    crawl_state: str = "none"
    page_count: int = 0
    last_crawl_iso: str | None = None
    discovery_confidence: float | None = None
    is_game: bool = True
    # Set to False when discovery searched but found no usable community
    # wiki. Submit refuses on these games (raw 412) — no fallback path.
    is_supported: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "GameEntry":
        fields = cls.__dataclass_fields__
        return cls(**{k: v for k, v in d.items() if k in fields})


def _load_raw() -> dict:
    if not GAMES_PATH.exists():
        return {"schema_version": 1, "games": {}, "window_bindings": {}}
    try:
        data = json.loads(GAMES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("games.json read failed: %r; treating as empty", exc)
        return {"schema_version": 1, "games": {}, "window_bindings": {}}
    data.setdefault("schema_version", 1)
    data.setdefault("games", {})
    data.setdefault("window_bindings", {})
    return data


def _save_raw(data: dict) -> None:
    GAMES_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="games_", suffix=".tmp", dir=str(GAMES_PATH.parent))
    try:
        with open(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        Path(tmp).replace(GAMES_PATH)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise


def list_games() -> list[GameEntry]:
    with _lock:
        data = _load_raw()
    return [GameEntry.from_dict(d) for d in data["games"].values()]


def get_game(game_id: str) -> GameEntry | None:
    with _lock:
        data = _load_raw()
    raw = data["games"].get(game_id)
    return GameEntry.from_dict(raw) if raw else None


def upsert_game(entry: GameEntry) -> None:
    with _lock:
        data = _load_raw()
        data["games"][entry.game_id] = entry.to_dict()
        _save_raw(data)
    logger.info("games: upserted %s", entry.game_id)


def delete_game(game_id: str) -> None:
    with _lock:
        data = _load_raw()
        data["games"].pop(game_id, None)
        # Clear bindings pointing at this game.
        data["window_bindings"] = {
            title: gid for title, gid in data["window_bindings"].items() if gid != game_id
        }
        _save_raw(data)
    logger.info("games: deleted %s and cleared its bindings", game_id)


def get_binding(window_title: str) -> str | None:
    """Return the game_id bound to ``window_title`` (or ``NOT_A_GAME``), else None."""
    with _lock:
        data = _load_raw()
    return data["window_bindings"].get(window_title)


def bind_window(window_title: str, game_id: str) -> None:
    """Record that ``window_title`` is bound to ``game_id``.

    ``game_id`` may be ``NOT_A_GAME`` to record a negative verdict so we don't
    re-call the LLM on every reselect.
    """
    with _lock:
        data = _load_raw()
        data["window_bindings"][window_title] = game_id
        _save_raw(data)
    logger.info("games: bound %r -> %s", window_title, game_id)


def clear_binding(window_title: str) -> None:
    with _lock:
        data = _load_raw()
        data["window_bindings"].pop(window_title, None)
        _save_raw(data)


def identify_game_from_screenshot(
    *,
    api_key: str,
    model: str,
    window_title: str,
    image_path: Path,
    known_game_ids: list[str] | None = None,
    log_tag: str = "game_id",
) -> dict | None:
    """Ask the LLM whether this window is a game and, if so, which one.

    Returns the parsed JSON dict from the model (see ``GAME_ID_PROMPT``) or
    None on hard failure (parse error, API error). The caller decides how to
    apply confidence thresholds.
    """
    try:
        jpeg = downscale_to_jpeg(image_path)
    except Exception as exc:
        logger.error("%s: downscale_to_jpeg failed for %s: %r", log_tag, image_path, exc)
        return None
    known = known_game_ids or []
    user_blocks: list[dict] = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": base64.b64encode(jpeg).decode("ascii"),
            },
        },
        {
            "type": "text",
            "text": (
                f"OS window title: {window_title!r}\n"
                f"Known game IDs in app: {known}\n\n"
                "Identify the game (or say it isn't a game) and return JSON per the system prompt."
            ),
        },
    ]
    client = anthropic.Anthropic(api_key=api_key, timeout=_REQUEST_TIMEOUT_SECONDS)
    logger.info("%s: calling messages.create model=%s title=%r", log_tag, model, window_title)
    t = time.monotonic()
    try:
        response = client.messages.create(
            model=model,
            system=GAME_ID_PROMPT,
            max_tokens=1024,
            messages=[{"role": "user", "content": user_blocks}],
        )
    except anthropic.APIError as exc:
        logger.error("%s: Anthropic call failed: %r", log_tag, exc)
        return None
    text = "".join(b.text for b in response.content if getattr(b, "type", None) == "text")
    logger.info(
        "%s: messages.create returned in %.2fs stop_reason=%s text_len=%d",
        log_tag, time.monotonic() - t, response.stop_reason, len(text),
    )
    parsed = _parse_json_block(text)
    if parsed is None:
        logger.warning("%s: model response had no parseable JSON; raw=%r", log_tag, text[:300])
    return parsed


def accept_identification(window_title: str, parsed: dict) -> str | None:
    """Apply an LLM identification result to the registry.

    Returns the bound ``game_id`` (or ``NOT_A_GAME``) on success, else None
    (low confidence -> nothing recorded so the next reselect tries again).
    """
    if not parsed:
        return None
    confidence = float(parsed.get("confidence", 0.0) or 0.0)
    if confidence < _MIN_CONFIDENCE:
        logger.info("accept_identification: low confidence %.2f for %r; skipping", confidence, window_title)
        return None
    if not parsed.get("is_game"):
        bind_window(window_title, NOT_A_GAME)
        return NOT_A_GAME
    existing = parsed.get("matches_existing_game_id")
    if isinstance(existing, str) and existing and get_game(existing) is not None:
        bind_window(window_title, existing)
        return existing
    name = (parsed.get("name") or "").strip()
    if not name:
        return None
    game_id = slugify(name)
    if get_game(game_id) is None:
        upsert_game(GameEntry(
            game_id=game_id,
            display_name=name,
            discovery_confidence=confidence,
            is_game=True,
        ))
    bind_window(window_title, game_id)
    return game_id
