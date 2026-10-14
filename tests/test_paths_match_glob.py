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

from archivey import ArchiveyUsageError, open_archive
from archivey.paths import match_glob, match_tar_glob

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
    assert not match_glob("a[!--0]b")("a/b")


def test_negated_set_keeps_its_own_characters() -> None:
    # "-" first in the set is a literal, not the end of a range starting at "/".
    predicate = match_glob("a[!-a]b")
    assert not predicate("a-b")
    assert predicate("a0b")
    assert match_glob("a[!--0]b")("a1b")


@pytest.mark.filterwarnings("error::FutureWarning")
@pytest.mark.parametrize(
    ("pattern", "selected", "refused"),
    [
        ("a[*--]", ["a-", "a*", "a+"], ["a."]),
        ("a[--^]b", ["a-b", "a0b", "a^b"], ["a/b", "a_b"]),
        ("a[!a&&b]", ["ac"], ["a&", "aa", "ab"]),
        ("a[~~|]", ["a~", "a|"], ["a&"]),
    ],
)
def test_set_operators_are_literal(
    pattern: str, selected: list[str], refused: list[str]
) -> None:
    # "--", "&&", "~~" and "||" are set operations to re (it warns); in a glob set they
    # are ordinary characters, or a range ending in "-".
    predicate = match_glob(pattern)
    assert [name for name in selected + refused if predicate(name)] == selected


def test_leading_slash_matches_an_absolute_name() -> None:
    assert match_glob("/etc/**")("/etc/passwd")
    assert not match_glob("/etc/**")("etc/passwd")


def test_repeated_double_star_is_one() -> None:
    predicate = match_glob("docs/**/**/*.md")
    assert [name for name in NAMES if predicate(name)] == [
        "docs/index.md",
        "docs/api/guide.md",
    ]


@pytest.mark.parametrize(
    "patterns",
    [
        (),
        ("",),
        ("*.md", ""),
        ("/",),
        ("//",),
        ("a//b",),
        ("//a",),
        ("a//",),
        ("[z-a]",),
        ("a[^--]",),
    ],
)
def test_bad_patterns_are_a_usage_error(patterns: tuple[str, ...]) -> None:
    with pytest.raises(ArchiveyUsageError, match="match_glob") as caught:
        match_glob(*patterns)
    assert isinstance(caught.value, ValueError)


def test_repr_names_the_patterns() -> None:
    assert repr(match_glob("docs/**", "*.md")) == "match_glob('docs/**', '*.md')"
    assert (
        repr(match_glob("docs", with_contents=True))
        == "match_glob('docs', with_contents=True)"
    )
    assert repr(match_tar_glob("docs")) == "match_tar_glob('docs')"


@pytest.mark.parametrize(
    ("patterns", "expected"),
    [
        (("docs",), ["docs/", "docs/index.md", "docs/api/", "docs/api/guide.md"]),
        (("docs/api/",), ["docs/api/", "docs/api/guide.md"]),
        (
            ("*/",),
            [
                "docs/",
                "docs/index.md",
                "docs/api/",
                "docs/api/guide.md",
                "docsx/a.md",
                "dir/new\nline.md",
            ],
        ),
        (("*.md",), ["README.md"]),
        (
            ("d*",),
            [
                "docs/",
                "docs/index.md",
                "docs/api/",
                "docs/api/guide.md",
                "docsx/a.md",
                "dir/new\nline.md",
            ],
        ),
    ],
)
def test_with_contents_selects_what_a_matching_folder_holds(
    patterns: tuple[str, ...], expected: list[str]
) -> None:
    predicate = match_glob(*patterns, with_contents=True)
    assert [name for name in NAMES if predicate(name)] == expected


def test_with_contents_on_an_absolute_name() -> None:
    predicate = match_glob("/etc", with_contents=True)
    assert predicate("/etc/passwd")
    assert not predicate("etc/passwd")
    # The leading "/" of "/etc" ends no folder: "/etc" is not inside one named "".
    assert not match_glob("*/", with_contents=True)("/etc")
    assert not match_glob("**/", with_contents=True)("/etc")


@pytest.mark.parametrize(
    ("patterns", "expected"),
    [
        (
            ("*.md",),
            [
                "README.md",
                "docs/index.md",
                "docs/api/guide.md",
                "docsx/a.md",
                "dir/new\nline.md",
            ],
        ),
        (("docs",), ["docs/", "docs/index.md", "docs/api/", "docs/api/guide.md"]),
        (("docs/",), ["docs/", "docs/index.md", "docs/api/", "docs/api/guide.md"]),
        (("docs/a*",), ["docs/api/", "docs/api/guide.md"]),
        (("x\\y",), ["x\\y"]),
        (("README.MD",), []),
    ],
)
def test_tar_glob_lets_star_cross_folders(
    patterns: tuple[str, ...], expected: list[str]
) -> None:
    predicate = match_tar_glob(*patterns)
    assert [name for name in NAMES if predicate(name)] == expected


@pytest.mark.parametrize(
    "patterns",
    [(), ("",), ("docs", ""), ("/",), ("a//b",), ("//a",), ("docs//",)],
)
def test_tar_glob_refuses_no_or_empty_pattern(patterns: tuple[str, ...]) -> None:
    with pytest.raises(ArchiveyUsageError, match="match_tar_glob"):
        match_tar_glob(*patterns)


def test_tar_glob_leading_slash_matches_an_absolute_name() -> None:
    predicate = match_tar_glob("/etc")
    assert predicate("/etc/passwd")
    assert not predicate("etc/passwd")


def test_paths_exports_match_glob_without_re_export() -> None:
    import archivey.paths

    for name in ("match_glob", "match_tar_glob"):
        assert name in archivey.paths.__all__
        assert name not in archivey.__all__
        assert not hasattr(archivey, name)


def _tar(path: Path) -> Path:
    """A TAR with a root entry ``.``, as ``tar cf x.tar .`` writes, and two folders."""
    with tarfile.open(path, "w") as tf:
        for name in (".", "docs", "docs/api"):
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


def test_root_entry_matches_as_a_directory(tmp_path: Path) -> None:
    # "." is the one directory name without a trailing "/": only is_dir marks it.
    archive_path = _tar(tmp_path / "site.tar")
    with open_archive(archive_path) as archive:
        root = archive.get(".")
    assert root.is_dir
    assert match_glob("*/")(root)
    assert not match_glob("*/")(".")


def test_directory_members_match_by_type(tmp_path: Path) -> None:
    archive_path = _tar(tmp_path / "site.tar")
    with open_archive(archive_path) as archive:
        members = archive.members()
    dirs_only = match_glob("**/")
    assert sorted(m.name.rstrip("/") for m in members if dirs_only(m)) == [
        ".",
        "docs",
        "docs/api",
    ]


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        ("docs", ["docs", "docs/api", "docs/api/guide.md", "docs/index.md"]),
        (
            "*",
            [
                ".",
                "README.md",
                "docs",
                "docs/api",
                "docs/api/guide.md",
                "docs/index.md",
                "notes.txt",
            ],
        ),
        ("*/", ["docs", "docs/api", "docs/api/guide.md", "docs/index.md"]),
        (".", ["."]),
    ],
)
def test_tar_glob_on_archive_members(
    tmp_path: Path, pattern: str, expected: list[str]
) -> None:
    archive_path = _tar(tmp_path / "site.tar")
    with open_archive(archive_path) as archive:
        members = archive.members()
    predicate = match_tar_glob(pattern)
    selected = sorted(m.name.rstrip("/") for m in members if predicate(m))
    assert selected == expected
