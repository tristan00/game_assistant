import pytest

from app.strategies import (
    create_strategy,
    delete_strategy,
    list_strategies,
    load_strategy,
    sanitize_name,
    save_strategy,
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


def test_list_strategies_empty_when_dir_missing():
    assert list_strategies() == []


def test_list_strategies_returns_sorted_stems():
    create_strategy("zeta")
    create_strategy("alpha")
    create_strategy("Mike")
    names = list_strategies()
    assert names == sorted(names)  # alphabetic
    assert set(names) == {"zeta", "alpha", "Mike"}


def test_load_missing_strategy_returns_empty_string():
    assert load_strategy("does_not_exist") == ""


def test_save_then_load_roundtrip():
    save_strategy("plan", "hello world")
    assert load_strategy("plan") == "hello world"


def test_create_strategy_initializes_empty_file():
    safe = create_strategy("My Plan")
    assert safe == "My Plan"
    assert load_strategy(safe) == ""


def test_create_strategy_sanitizes_name():
    safe = create_strategy("weird!!name??")
    assert safe == "weirdname"
    assert safe in list_strategies()


def test_create_strategy_raises_on_duplicate():
    create_strategy("plan")
    with pytest.raises(FileExistsError):
        create_strategy("plan")


def test_delete_strategy_removes_file():
    create_strategy("plan")
    assert "plan" in list_strategies()
    delete_strategy("plan")
    assert "plan" not in list_strategies()


def test_delete_missing_strategy_is_a_noop():
    # Should NOT raise — matches current behavior (logs a warning instead).
    delete_strategy("never_existed")
