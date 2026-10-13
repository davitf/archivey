"""``archivey.paths.match_glob``: glob patterns as a ``members=`` predicate.

``*`` stays inside one folder, ``**`` as a whole component matches any depth, a pattern
ending in ``/`` selects only directories, and a directory is matched by its name without
the trailing ``/``.
"""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from archivey import open_archive
from archivey.paths import match_glob

NAMES = [
    "README.md",
    "docs/",
    "docs/index.md",
    "docs/api/",
    "docs/api/guide.md",
    "docsx/a.md",
    ".hidden",
    "a[1].txt",
    "a*b",
    "x\\y",
    "dir/new\nline.md",
]


@pytest.mark.parametrize(
    ("patterns", "expected"),
    [
        (("*.md",), ["README.md"]),
        (
            ("docs/**",),
            ["docs/", "docs/index.md", "docs/api/", "docs/api/guide.md"],
        ),
        (
            ("**/*.md",),
            [
                "README.md",
                "docs/index.md",
                "docs/api/guide.md",
                "docsx/a.md",
                "dir/new\nline.md",
            ],
        ),
        (("docs/**/*.md",), ["docs/index.md", "docs/api/guide.md"]),
        (("docs/*",), ["docs/index.md", "docs/api/"]),
        (("docs/*/",), ["docs/api/"]),
        (("*/",), ["docs/"]),
        (("**/",), ["docs/", "docs/api/"]),
        (("**",), NAMES),
        (("docs/**.md",), ["docs/index.md"]),
        (("?ocs/",), ["docs/"]),
        (("[!d]*",), ["README.md", ".hidden", "a[1].txt", "a*b", "x\\y"]),
        (("a[[]1].txt",), ["a[1].txt"]),
        (("a[*]b",), ["a*b"]),
        (("x\\y",), ["x\\y"]),
        (("a[1.txt",), []),
        (("readme.md",), []),
        (("README.md", "docs/*/"), ["README.md", "docs/api/"]),
    ],
    ids=lambda value: repr(value) if isinstance(value, tuple) else None,
)
def test_patterns_select_names(patterns: tuple[str, ...], expected: list[str]) -> None:
    predicate = match_glob(*patterns)
    assert [name for name in NAMES if predicate(name)] == expected


def test_unclosed_bracket_is_a_literal() -> None:
    assert match_glob("a[1.txt")("a[1.txt")


def test_closing_bracket_first_in_a_set_is_a_member() -> None:
    assert match_glob("a[]x]b")("a]b")
    assert not match_glob("a[!]]b")("a]b")


def test_bracket_never_matches_a_separator() -> None:
    assert not match_glob("a[!x]b")("a/b")
    assert not match_glob("a[--0]b")("a/b")  # "/" sits inside the range "-" to "0"


@pytest.mark.parametrize("patterns", [(), ("",), ("*.md", "")])
def test_no_or_empty_pattern_is_refused(patterns: tuple[str, ...]) -> None:
    with pytest.raises(ValueError, match="match_glob"):
        match_glob(*patterns)


def test_repr_names_the_patterns() -> None:
    assert repr(match_glob("docs/**", "*.md")) == "match_glob('docs/**', '*.md')"


def test_paths_exports_match_glob_without_re_export() -> None:
    import archivey.paths

    assert "match_glob" in archivey.paths.__all__
    assert "match_glob" not in archivey.__all__
    assert not hasattr(archivey, "match_glob")


def _tar(path: Path) -> Path:
    """A TAR whose ``docs`` directory entry is stored without a trailing ``/``."""
    with tarfile.open(path, "w") as tf:
        for name in ("docs", "docs/api"):
            info = tarfile.TarInfo(name)
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            tf.addfile(info)
        for name in ("README.md", "docs/index.md", "docs/api/guide.md", "notes.txt"):
            info = tarfile.TarInfo(name)
            info.size = len(name)
            tf.addfile(info, io.BytesIO(name.encode()))
    return path


def test_extract_all_with_match_glob(tmp_path: Path) -> None:
    archive_path = _tar(tmp_path / "site.tar")
    dest = tmp_path / "out"
    with open_archive(archive_path) as archive:
        archive.extract_all(dest, members=match_glob("docs/**", "*.md"))
    written = sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*"))
    assert written == [
        "README.md",
        "docs",
        "docs/api",
        "docs/api/guide.md",
        "docs/index.md",
    ]


def test_directory_members_match_by_type(tmp_path: Path) -> None:
    archive_path = _tar(tmp_path / "site.tar")
    with open_archive(archive_path) as archive:
        members = archive.members()
    dirs_only = match_glob("**/")
    assert sorted(m.name.rstrip("/") for m in members if dirs_only(m)) == [
        "docs",
        "docs/api",
    ]
