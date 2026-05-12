"""Shared fixtures for the test suite.

The autouse `isolated_paths` fixture redirects every Path.home()-rooted
module constant into a per-test tmp_path, so tests never touch the user's
real ~/game_assistant directory.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest
from PIL import Image

# Make the repo importable so `import app.*` works without an install step.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app import session as session_module  # noqa: E402
from app import settings as settings_module  # noqa: E402
from app import strategies as strategies_module  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_paths(tmp_path, monkeypatch):
    """Redirect every disk path the app touches into tmp_path for the test."""
    sessions = tmp_path / "sessions"
    settings_file = tmp_path / "settings.json"
    strategies = tmp_path / "strategies"

    monkeypatch.setattr(session_module, "SESSION_ROOT", sessions)
    monkeypatch.setattr(settings_module, "SETTINGS_PATH", settings_file)
    monkeypatch.setattr(strategies_module, "STRATEGY_DIR", strategies)

    yield tmp_path


@pytest.fixture
def make_png(tmp_path):
    """Factory: write a PNG of `size` (W,H) at `name` under tmp_path and return its Path."""
    def _make(name: str = "img.png", size: tuple[int, int] = (200, 150), mode: str = "RGB", color=(255, 0, 0)) -> Path:
        path = tmp_path / name
        img = Image.new(mode, size, color)
        img.save(path, format="PNG")
        return path
    return _make
