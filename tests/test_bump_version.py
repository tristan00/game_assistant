import pathlib

import pytest

from scripts.bump_version import bump, bump_file


# ---- pure logic ----


@pytest.mark.parametrize(
    "current,segment,expected",
    [
        ("0.1.0", "patch", "0.1.1"),
        ("0.1.0", "minor", "0.2.0"),
        ("0.1.0", "major", "1.0.0"),
        ("1.2.3", "patch", "1.2.4"),
        ("1.2.3", "minor", "1.3.0"),
        ("1.2.3", "major", "2.0.0"),
        ("9.9.9", "patch", "9.9.10"),
        # Patch resets do NOT happen on minor/major bumps: minor zeroes patch, major zeroes both.
        ("1.5.7", "minor", "1.6.0"),
        ("1.5.7", "major", "2.0.0"),
    ],
)
def test_bump_segments(current, segment, expected):
    assert bump(current, segment) == expected


def test_bump_invalid_version_raises():
    with pytest.raises(ValueError):
        bump("1.2", "patch")
    with pytest.raises(ValueError):
        bump("not.a.version", "patch")
    with pytest.raises(ValueError):
        bump("v1.2.3", "patch")  # leading v not allowed


def test_bump_invalid_segment_raises():
    with pytest.raises(ValueError):
        bump("1.2.3", "build")
    with pytest.raises(ValueError):
        bump("1.2.3", "")


# ---- file IO ----


@pytest.fixture
def fake_repo(tmp_path):
    """A tmp_path repo root containing app/__init__.py with a known version."""
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    init = app_dir / "__init__.py"
    init.write_text('__version__ = "1.2.3"\n', encoding="utf-8")
    return tmp_path


def test_bump_file_patch_updates_init(fake_repo):
    new_version = bump_file("patch", root=fake_repo)
    assert new_version == "1.2.4"
    contents = (fake_repo / "app" / "__init__.py").read_text(encoding="utf-8")
    assert '__version__ = "1.2.4"' in contents


def test_bump_file_minor(fake_repo):
    assert bump_file("minor", root=fake_repo) == "1.3.0"


def test_bump_file_major(fake_repo):
    assert bump_file("major", root=fake_repo) == "2.0.0"


def test_bump_file_preserves_surrounding_text(fake_repo):
    init = fake_repo / "app" / "__init__.py"
    init.write_text(
        '# header comment\n'
        '__version__ = "1.2.3"\n'
        '\n'
        'OTHER_CONSTANT = "unchanged"\n',
        encoding="utf-8",
    )
    bump_file("patch", root=fake_repo)
    text = init.read_text(encoding="utf-8")
    assert "# header comment" in text
    assert 'OTHER_CONSTANT = "unchanged"' in text
    assert '__version__ = "1.2.4"' in text


def test_bump_file_missing_version_raises(fake_repo):
    init = fake_repo / "app" / "__init__.py"
    init.write_text("# no version constant here\n", encoding="utf-8")
    with pytest.raises(ValueError):
        bump_file("patch", root=fake_repo)
