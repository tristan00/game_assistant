from app.perception import schema
from app.wiki import storage as wiki_storage


def test_load_schema_returns_none_when_no_game_id():
    assert schema.load_schema(None) is None


def test_load_schema_returns_none_when_no_per_game_file():
    assert schema.load_schema("never-crawled-game") is None


def test_load_schema_returns_none_when_per_game_file_empty():
    wiki_storage.ensure_wiki_dirs("g")
    wiki_storage.perception_schema_path("g").write_text("", encoding="utf-8")
    assert schema.load_schema("g") is None


def test_load_schema_returns_per_game_file_when_present():
    wiki_storage.ensure_wiki_dirs("g")
    wiki_storage.perception_schema_path("g").write_text(
        "# Perception Schema — Total War: WH3\n\n## Slots\n- **lord_stances** (list): …\n",
        encoding="utf-8",
    )
    text = schema.load_schema("g")
    assert text is not None
    assert "lord_stances" in text


def test_schema_hash_is_deterministic_and_short():
    h1 = schema.schema_hash("hello")
    h2 = schema.schema_hash("hello")
    h3 = schema.schema_hash("hello!")
    assert h1 == h2 != h3
    assert len(h1) == 12
