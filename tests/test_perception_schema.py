import pytest

from app.perception import schema
from app.wiki import storage as wiki_storage


def test_load_schema_raises_when_file_missing():
    with pytest.raises(FileNotFoundError):
        schema.load_schema("never-crawled-game")


def test_load_schema_raises_when_file_empty():
    wiki_storage.ensure_wiki_dirs("g")
    wiki_storage.perception_schema_path("g").write_text("", encoding="utf-8")
    with pytest.raises(RuntimeError, match="empty"):
        schema.load_schema("g")


def test_load_schema_returns_per_game_file_when_present():
    wiki_storage.ensure_wiki_dirs("g")
    wiki_storage.perception_schema_path("g").write_text(
        "# Perception Schema — Total War: WH3\n\n## Slots\n- **lord_stances** (list): …\n",
        encoding="utf-8",
    )
    text = schema.load_schema("g")
    assert "lord_stances" in text
