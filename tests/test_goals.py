import pytest

from app.goals import (
    create_goal,
    delete_goal,
    list_goals,
    load_goal,
    sanitize_name,
    save_goal,
)


def test_sanitize_name_passes_through_safe_chars():
    assert sanitize_name("Norsca Run 1") == "Norsca Run 1"
    assert sanitize_name("file_name-2") == "file_name-2"


def test_sanitize_name_strips_disallowed_chars():
    assert sanitize_name("foo/bar:baz") == "foobarbaz"
    assert sanitize_name("hello!!!world???") == "helloworld"


def test_sanitize_name_strips_leading_trailing_whitespace():
    assert sanitize_name("  spaced  ") == "spaced"


def test_sanitize_name_raises_on_empty_after_stripping():
    with pytest.raises(ValueError):
        sanitize_name("///")
    with pytest.raises(ValueError):
        sanitize_name("")
    with pytest.raises(ValueError):
        sanitize_name("   ")


def test_list_goals_empty_when_dir_missing():
    assert list_goals() == []


def test_list_goals_returns_sorted_stems():
    create_goal("zeta")
    create_goal("alpha")
    create_goal("Mike")
    names = list_goals()
    assert names == sorted(names)  # alphabetic
    assert set(names) == {"zeta", "alpha", "Mike"}


def test_load_missing_goal_returns_empty_string():
    assert load_goal("does_not_exist") == ""


def test_save_then_load_roundtrip():
    save_goal("plan", "hello world")
    assert load_goal("plan") == "hello world"


def test_create_goal_initializes_empty_file():
    safe = create_goal("My Plan")
    assert safe == "My Plan"
    assert load_goal(safe) == ""


def test_create_goal_sanitizes_name():
    safe = create_goal("weird!!name??")
    assert safe == "weirdname"
    assert safe in list_goals()


def test_create_goal_raises_on_duplicate():
    create_goal("plan")
    with pytest.raises(FileExistsError):
        create_goal("plan")


def test_delete_goal_removes_file():
    create_goal("plan")
    assert "plan" in list_goals()
    delete_goal("plan")
    assert "plan" not in list_goals()


def test_delete_missing_goal_is_a_noop():
    # Should NOT raise — matches current behavior (logs a warning instead).
    delete_goal("never_existed")
