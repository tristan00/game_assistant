import json

from app import migrations


def test_strategies_to_goals_dir_moves_when_only_strategies_exists(tmp_path, monkeypatch):
    root = tmp_path
    monkeypatch.setattr(migrations, "ROOT", root)
    monkeypatch.setattr(migrations, "DONE_PATH", root / ".migrations_done")
    (root / "strategies").mkdir()
    (root / "strategies" / "Plan A.md").write_text("hello", encoding="utf-8")

    migrations.run_startup_migrations()

    assert not (root / "strategies").exists()
    assert (root / "goals" / "Plan A.md").read_text(encoding="utf-8") == "hello"
    done = json.loads((root / ".migrations_done").read_text(encoding="utf-8"))
    assert "strategies_to_goals_dir" in done


def test_strategies_to_goals_dir_handles_conflict_by_renaming(tmp_path, monkeypatch):
    root = tmp_path
    monkeypatch.setattr(migrations, "ROOT", root)
    monkeypatch.setattr(migrations, "DONE_PATH", root / ".migrations_done")
    (root / "strategies").mkdir()
    (root / "goals").mkdir()
    (root / "strategies" / "conflict.md").write_text("from-strategies", encoding="utf-8")
    (root / "goals" / "conflict.md").write_text("from-goals", encoding="utf-8")
    (root / "strategies" / "unique.md").write_text("hi", encoding="utf-8")

    migrations.run_startup_migrations()

    assert (root / "goals" / "conflict.md").read_text(encoding="utf-8") == "from-goals"
    assert (root / "goals" / "conflict__from_strategies.md").read_text(encoding="utf-8") == "from-strategies"
    assert (root / "goals" / "unique.md").read_text(encoding="utf-8") == "hi"
    # strategies dir is NOT auto-deleted on conflict path.
    assert (root / "strategies").exists()


def test_init_wikis_dir_creates_dir(tmp_path, monkeypatch):
    root = tmp_path
    monkeypatch.setattr(migrations, "ROOT", root)
    monkeypatch.setattr(migrations, "DONE_PATH", root / ".migrations_done")

    migrations.run_startup_migrations()

    assert (root / "wikis").is_dir()
    done = json.loads((root / ".migrations_done").read_text(encoding="utf-8"))
    assert "init_wikis_dir" in done


def test_run_startup_migrations_is_idempotent(tmp_path, monkeypatch):
    root = tmp_path
    monkeypatch.setattr(migrations, "ROOT", root)
    monkeypatch.setattr(migrations, "DONE_PATH", root / ".migrations_done")

    migrations.run_startup_migrations()
    first = json.loads((root / ".migrations_done").read_text(encoding="utf-8"))
    # Touch a file so we can detect re-application: writing to .migrations_done changes mtime.
    mtime_before = (root / ".migrations_done").stat().st_mtime_ns

    migrations.run_startup_migrations()
    second = json.loads((root / ".migrations_done").read_text(encoding="utf-8"))
    mtime_after = (root / ".migrations_done").stat().st_mtime_ns

    assert first == second
    # No re-write when nothing changes.
    assert mtime_before == mtime_after
