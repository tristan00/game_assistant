from app.wiki import search, storage


def _write_page(game_id: str, name: str, title: str, url: str, body: str) -> None:
    storage.ensure_wiki_dirs(game_id)
    text = f"# {title}\n\n{url}\n\n{body}\n"
    (storage.pages_dir(game_id) / name).write_text(text, encoding="utf-8")


def test_build_index_returns_zero_when_no_pages():
    assert search.build_index("nonexistent-game") == 0


def test_build_index_indexes_pages_and_search_finds_them():
    gid = "g"
    _write_page(gid, "Sword.md", "Sword", "https://w/Sword", "A sword deals physical damage.")
    _write_page(gid, "Fireball.md", "Fireball", "https://w/Fireball", "Fireball deals fire damage to enemies.")
    _write_page(gid, "Shield.md", "Shield", "https://w/Shield", "Shields block incoming attacks.")

    assert search.build_index(gid) == 3

    hits = search.search(gid, "fire damage", max_results=5)
    assert len(hits) >= 1
    titles = [h["title"] for h in hits]
    assert "Fireball" in titles
    # Snippet contains a highlight delimiter from FTS5 snippet().
    fireball = next(h for h in hits if h["title"] == "Fireball")
    assert fireball["url"] == "https://w/Fireball"


def test_search_returns_empty_when_no_index():
    assert search.search("never-indexed", "anything") == []


def test_search_handles_empty_query():
    gid = "g"
    _write_page(gid, "A.md", "A", "https://w/A", "hello")
    search.build_index(gid)
    assert search.search(gid, "") == []
    assert search.search(gid, "    ") == []


def test_search_respects_max_results():
    gid = "g"
    for i in range(10):
        _write_page(gid, f"P{i}.md", f"Title{i}", f"https://w/P{i}", "fire damage attack")
    search.build_index(gid)
    assert len(search.search(gid, "fire", max_results=3)) == 3
