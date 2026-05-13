from app.wiki import storage


def test_slugify_basic():
    assert storage.slugify("Path of Exile 2") == "path-of-exile-2"
    assert storage.slugify("Total War: Warhammer III") == "total-war-warhammer-iii"
    assert storage.slugify("FACTORIO") == "factorio"


def test_slugify_collapses_special_chars():
    assert storage.slugify("Hi!!  there??") == "hi-there"


def test_slugify_never_empty():
    assert storage.slugify("!!!") == "unknown-game"
    assert storage.slugify("") == "unknown-game"


def test_path_helpers_are_under_wikis_dir():
    gid = "test-game"
    assert storage.wiki_dir(gid) == storage.WIKIS_DIR / gid
    assert storage.pages_dir(gid) == storage.WIKIS_DIR / gid / "pages"
    assert storage.meta_path(gid).name == "_meta.json"
    assert storage.quick_ref_path(gid).name == "_quick_ref.md"
    assert storage.perception_schema_path(gid).name == "_perception_schema.md"
    assert storage.index_path(gid).name == "index.sqlite3"


def test_page_filename_sanitizes():
    assert storage.page_filename("Hello World") == "Hello_World.md"
    assert storage.page_filename("Foo/Bar") == "Foo_Bar.md"
    assert storage.page_filename("!!") == "untitled.md"


def test_atomic_write_round_trip(tmp_path):
    p = tmp_path / "x" / "y.txt"
    storage.atomic_write_text(p, "hello")
    assert p.read_text(encoding="utf-8") == "hello"


def test_meta_load_save_round_trip():
    gid = "g"
    assert storage.load_meta(gid) == {}
    storage.save_meta(gid, {"a": 1})
    assert storage.load_meta(gid) == {"a": 1}


def test_page_count_on_disk():
    gid = "g"
    assert storage.page_count_on_disk(gid) == 0
    storage.ensure_wiki_dirs(gid)
    assert storage.page_count_on_disk(gid) == 0
    (storage.pages_dir(gid) / "a.md").write_text("hello", encoding="utf-8")
    (storage.pages_dir(gid) / "b.md").write_text("world", encoding="utf-8")
    assert storage.page_count_on_disk(gid) == 2
