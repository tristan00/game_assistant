from datetime import datetime

from app.session import Session, capture_path, new_session


def test_new_session_creates_folder_under_root(isolated_paths):
    sess = new_session()
    assert sess.folder.exists()
    assert sess.folder.is_dir()
    # Lives under the patched SESSION_ROOT (which is tmp_path/sessions).
    assert sess.folder.parent == isolated_paths / "sessions"


def test_session_screenshot_count_initially_zero(isolated_paths):
    sess = new_session()
    assert sess.screenshot_count == 0


def test_session_screenshot_count_counts_shot_files(isolated_paths):
    sess = new_session()
    (sess.folder / "shot_20260101_120000.png").write_bytes(b"x")
    (sess.folder / "shot_20260101_120001.png").write_bytes(b"x")
    (sess.folder / "not_a_shot.png").write_bytes(b"x")  # should NOT be counted
    (sess.folder / "shot_20260101_120002.jpg").write_bytes(b"x")  # wrong ext, not counted
    assert sess.screenshot_count == 2


def test_capture_path_base_when_no_collision(tmp_path):
    folder = tmp_path / "sess"
    folder.mkdir()
    when = datetime(2026, 5, 11, 14, 30, 0)
    path = capture_path(folder, when)
    assert path.name == "shot_20260511_143000.png"


def test_capture_path_appends_counter_on_collision(tmp_path):
    folder = tmp_path / "sess"
    folder.mkdir()
    when = datetime(2026, 5, 11, 14, 30, 0)
    base = folder / "shot_20260511_143000.png"
    base.write_bytes(b"")
    second = capture_path(folder, when)
    assert second.name == "shot_20260511_143000_2.png"


def test_capture_path_counter_climbs_past_multiple_collisions(tmp_path):
    folder = tmp_path / "sess"
    folder.mkdir()
    when = datetime(2026, 5, 11, 14, 30, 0)
    (folder / "shot_20260511_143000.png").write_bytes(b"")
    (folder / "shot_20260511_143000_2.png").write_bytes(b"")
    (folder / "shot_20260511_143000_3.png").write_bytes(b"")
    next_path = capture_path(folder, when)
    assert next_path.name == "shot_20260511_143000_4.png"
