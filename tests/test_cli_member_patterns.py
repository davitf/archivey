"""CLI member patterns: a missing trailing ``/``, directory contents, Windows ``\\``."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from archivey import open_archive
from archivey.cli.exit_codes import EXIT_FAIL, EXIT_OK
from archivey.cli.filters import member_predicate, unmatched_include_patterns
from archivey.cli.main import main
from archivey.types import ArchiveMember


def _tar(path: Path, entries: list[tuple[str, bytes | None]]) -> Path:
    """Write a TAR of ``(name, content)``; ``None`` content is a directory."""
    with tarfile.open(path, "w") as tar:
        for name, content in entries:
            info = tarfile.TarInfo(name)
            if content is None:
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            else:
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))
    return path


_TREE: list[tuple[str, bytes | None]] = [
    ("docs", None),
    ("docs/a.txt", b"a"),
    ("docs/sub", None),
    ("docs/sub/b.txt", b"b"),
    ("docs.txt", b"not the dir"),
    ("src/main.py", b"code"),
]


def _members(tmp_path: Path) -> list[ArchiveMember]:
    with open_archive(_tar(tmp_path / "t.tar", _TREE)) as ar:
        return ar.members()


def _selected(
    members: list[ArchiveMember],
    includes: list[str],
    excludes: list[str] | None = None,
    *,
    windows: bool = False,
) -> list[str]:
    pred = member_predicate(includes, excludes, backslash_is_separator=windows)
    assert pred is not None
    return [m.name for m in members if pred(m)]


def test_a_bare_directory_name_selects_the_directory_and_its_contents(
    tmp_path: Path,
) -> None:
    """``docs`` selects ``docs/`` and everything under it, as ``tar`` does, but not
    the sibling file ``docs.txt``."""
    members = _members(tmp_path)
    expected = ["docs/", "docs/a.txt", "docs/sub/", "docs/sub/b.txt"]
    assert _selected(members, ["docs"]) == expected
    assert _selected(members, ["docs/"]) == expected


def test_a_directory_pattern_does_not_select_a_file_of_that_name(
    tmp_path: Path,
) -> None:
    members = _members(tmp_path)
    assert _selected(members, ["docs.txt/"]) == []
    assert _selected(members, ["docs.txt"]) == ["docs.txt"]


def test_a_nested_directory_and_a_wildcard_still_work(tmp_path: Path) -> None:
    members = _members(tmp_path)
    assert _selected(members, ["docs/sub"]) == ["docs/sub/", "docs/sub/b.txt"]
    assert _selected(members, ["*.py"]) == ["src/main.py"]
    # A directory stored only through its files (no "src/" entry) is still selected.
    assert _selected(members, ["src"]) == ["src/main.py"]


def test_exclude_matches_the_same_way(tmp_path: Path) -> None:
    members = _members(tmp_path)
    assert _selected(members, ["docs"], ["docs/sub"]) == ["docs/", "docs/a.txt"]


def test_a_backslash_is_a_separator_only_on_windows(tmp_path: Path) -> None:
    members = _members(tmp_path)
    assert _selected(members, ["docs\\sub"], windows=True) == [
        "docs/sub/",
        "docs/sub/b.txt",
    ]
    # Elsewhere a backslash is a literal character a TAR member name can contain.
    assert _selected(members, ["docs\\sub"], windows=False) == []


def test_unmatched_patterns_use_the_same_rule(tmp_path: Path) -> None:
    members = _members(tmp_path)
    assert unmatched_include_patterns(
        ["docs", "src", "docs.txt/", "missing"],
        members,
        backslash_is_separator=False,
    ) == ["docs.txt/", "missing"]


@pytest.mark.parametrize("pattern", ["docs", "docs/"])
def test_extract_a_directory_by_name(
    pattern: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    archive = _tar(tmp_path / "t.tar", _TREE)
    dest = tmp_path / "out"
    assert main(["x", str(archive), "-d", str(dest), pattern]) == EXIT_OK
    assert (dest / "docs" / "sub" / "b.txt").read_bytes() == b"b"
    assert not (dest / "docs.txt").exists()
    assert "pattern matched no members" not in capsys.readouterr().err


def test_list_a_directory_by_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    archive = _tar(tmp_path / "t.tar", _TREE)
    assert main(["list", str(archive), "docs/sub"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "docs/sub/b.txt" in out
    assert "docs/a.txt" not in out


def test_extract_with_only_a_missing_directory_pattern_still_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    archive = _tar(tmp_path / "t.tar", _TREE)
    dest = tmp_path / "out"
    assert main(["x", str(archive), "-d", str(dest), "nothere/"]) == EXIT_FAIL
    assert "pattern matched no members: 'nothere/'" in capsys.readouterr().err


def test_a_matching_pattern_wins_over_the_dash_d_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``out`` names a local folder and a directory in the archive: it is a pattern.

    The ``-d`` hint is for a pattern that matched nothing; this one matched.
    """
    archive = _tar(
        tmp_path / "t.tar", [("out", None), ("out/a.txt", b"a"), ("top.txt", b"t")]
    )
    work = tmp_path / "work"
    (work / "out").mkdir(parents=True)
    monkeypatch.chdir(work)
    assert main(["x", str(archive), "out"]) == EXIT_OK
    err = capsys.readouterr().err
    assert "did you mean -d" not in err
    assert (work / "out" / "a.txt").read_bytes() == b"a"
    assert not (work / "top.txt").exists()


def test_a_pattern_written_with_the_slash_compiles_no_duplicate_form() -> None:
    from archivey.cli.filters import _Pattern

    assert _Pattern("docs/", backslash_is_separator=False).forms == (
        "docs/",
        "docs/*",
    )
