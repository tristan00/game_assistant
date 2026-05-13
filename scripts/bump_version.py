"""Bump the package version in app/__init__.py.

Usage:
    python scripts/bump_version.py {major|minor|patch}

Updates ``__version__`` in ``app/__init__.py`` and prints the new version to
stdout. Pure stdlib; no external deps. Used by the CI release job and exercised
by ``tests/test_bump_version.py``.
"""

import argparse
import pathlib
import re
import sys

_INIT_PATH = pathlib.Path("app/__init__.py")
_INIT_RE = re.compile(r'^(__version__\s*=\s*")(\d+)\.(\d+)\.(\d+)(")', re.MULTILINE)


def bump(version: str, segment: str) -> str:
    """Return the new version string after bumping the named segment."""
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version)
    if not m:
        raise ValueError(f"version must be X.Y.Z, got {version!r}")
    major, minor, patch = (int(g) for g in m.groups())

    if segment == "major":
        return f"{major + 1}.0.0"
    if segment == "minor":
        return f"{major}.{minor + 1}.0"
    if segment == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError(f"segment must be 'major', 'minor', or 'patch', got {segment!r}")


def _read_current_version(path: pathlib.Path) -> str:
    text = path.read_text(encoding="utf-8")
    m = _INIT_RE.search(text)
    if not m:
        raise ValueError(f"could not find __version__ in {path}")
    return f"{m.group(2)}.{m.group(3)}.{m.group(4)}"


def _write_new_version(path: pathlib.Path, new_version: str) -> None:
    text = path.read_text(encoding="utf-8")
    new_text, n = _INIT_RE.subn(rf"\g<1>{new_version}\g<5>", text, count=1)
    if n != 1:
        raise ValueError(f"could not substitute __version__ in {path}")
    path.write_text(new_text, encoding="utf-8")


def bump_file(segment: str, *, root: pathlib.Path | None = None) -> str:
    """Bump app/__init__.py and return the new version."""
    root = root or pathlib.Path.cwd()
    init_path = root / _INIT_PATH
    current = _read_current_version(init_path)
    new_version = bump(current, segment)
    _write_new_version(init_path, new_version)
    return new_version


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bump game_assistant version.")
    parser.add_argument("segment", choices=["major", "minor", "patch"])
    args = parser.parse_args(argv)
    print(bump_file(args.segment))
    return 0


if __name__ == "__main__":
    sys.exit(main())
