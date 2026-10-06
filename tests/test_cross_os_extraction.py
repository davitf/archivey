"""Extraction under ``STRICT`` and ``STANDARD`` gives the same tree on every OS.

The ``safe-extraction`` rule "Cross-platform name safety is deterministic across policy
levels": an archive extracted on Linux into an NTFS volume ends up as it would on
Windows on that volume. Each test here pins one place where the two used to differ.
The tests run on POSIX and exercise the shared rule; the Windows-only parts either run
only on Windows or simulate the Windows error with monkeypatch.
"""

from __future__ import annotations

import io
import os
import tarfile
from pathlib import Path

import pytest

from archivey import ExtractionPolicy, ExtractionStatus, OnError
from archivey.exceptions import FilterRejectionError
from archivey.internal.filters import apply_name_policy
from archivey.types import ArchiveMember, MemberType
from tests.extract_util import open_and_extract

_PORTABLE = pytest.mark.parametrize(
    "policy", [ExtractionPolicy.STRICT, ExtractionPolicy.STANDARD]
)
_POSIX_SYMLINKS = pytest.mark.skipif(os.name == "nt", reason="needs POSIX symlinks")


def _tar(specs: list[tuple[str, str, bytes | str | None]]) -> bytes:
    """A tar from ``(kind, name, payload)``: kind ``file`` (bytes), ``dir``, ``sym`` or
    ``hard`` (link target)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tf:
        for kind, name, payload in specs:
            info = tarfile.TarInfo(name)
            if kind == "file":
                assert isinstance(payload, bytes)
                info.size = len(payload)
                info.mode = 0o644
                tf.addfile(info, io.BytesIO(payload))
                continue
            if kind == "dir":
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
            else:
                assert isinstance(payload, str)
                info.type = tarfile.SYMTYPE if kind == "sym" else tarfile.LNKTYPE
                info.linkname = payload
            tf.addfile(info)
    return buf.getvalue()


def _member(
    name: str, *, type: MemberType = MemberType.FILE, link_target: str | None = None
) -> ArchiveMember:
    return ArchiveMember(
        type=type, name=name, raw_name=name.encode(), link_target=link_target
    )


def _tree(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))


# --- A backslash in a TAR name is a separator --------------------------------------


@_PORTABLE
def test_tar_backslash_is_written_as_a_separator(
    tmp_path: Path, policy: ExtractionPolicy
) -> None:
    """Windows writes ``a\\b`` as directory ``a`` and file ``b``; so does every OS."""
    report = open_and_extract(
        io.BytesIO(_tar([("file", "a\\b", b"x")])), tmp_path / "out", policy=policy
    )
    result = report.results[0]
    assert result.status is ExtractionStatus.EXTRACTED
    assert _tree(tmp_path / "out") == ["a", "a/b"]
    assert (tmp_path / "out" / "a" / "b").read_bytes() == b"x"
    # The rewrite is recorded like any other portable rewrite.
    assert result.presented_name == "a\\b"
    assert result.member.name == "a\\b"


@pytest.mark.skipif(os.name == "nt", reason="Windows writes '\\' as a separator")
def test_tar_backslash_is_literal_under_trusted(tmp_path: Path) -> None:
    open_and_extract(
        io.BytesIO(_tar([("file", "a\\b", b"x")])),
        tmp_path / "out",
        policy=ExtractionPolicy.TRUSTED,
    )
    assert _tree(tmp_path / "out") == ["a\\b"]


@_PORTABLE
def test_tar_backslash_meets_a_file_of_the_same_name_as_a_slash_does(
    tmp_path: Path, policy: ExtractionPolicy
) -> None:
    """``a`` then ``a\\b``: the outcome is the one ``a`` then ``a/b`` gets."""

    def outcome(name: str, dest: Path) -> list[tuple[ExtractionStatus, str | None]]:
        report = open_and_extract(
            io.BytesIO(_tar([("file", "a", b"1"), ("file", name, b"2")])),
            dest,
            policy=policy,
            on_error=OnError.CONTINUE,
        )
        return [
            (r.status, type(r.error).__name__ if r.error else None)
            for r in report.results
        ]

    slash = outcome("a/b", tmp_path / "slash")
    backslash = outcome("a\\b", tmp_path / "backslash")
    assert backslash == slash
    assert slash[1][0] is ExtractionStatus.FAILED


@_PORTABLE
def test_hardlink_target_backslash_becomes_a_separator(
    tmp_path: Path, policy: ExtractionPolicy
) -> None:
    member = _member("h", type=MemberType.HARDLINK, link_target="d\\f")
    assert apply_name_policy(member, policy).link_target == "d/f"
    # A symlink target is a filesystem path and is not rewritten here.
    link = _member("s", type=MemberType.SYMLINK, link_target="d\\f")
    assert apply_name_policy(link, policy).link_target == "d\\f"

    archive = _tar([("file", "d\\f", b"data"), ("hard", "h\\g", "d\\f")])
    report = open_and_extract(io.BytesIO(archive), tmp_path / "out", policy=policy)
    assert [r.status for r in report.results] == [ExtractionStatus.EXTRACTED] * 2
    assert (tmp_path / "out" / "h" / "g").read_bytes() == b"data"


@_POSIX_SYMLINKS
@pytest.mark.parametrize("name", ["foo\\x", "foo. /x"])
def test_rewritten_name_is_checked_where_it_is_written(
    tmp_path: Path, name: str
) -> None:
    """The universal check runs again on the rewritten name.

    ``foo\\x`` and ``foo. /x`` name no directory ``foo`` until the policy rewrites them
    to ``foo/x``. A ``foo`` symlink already in the destination that leaves it must
    refuse the member, as it refuses ``foo/x``.
    """
    dest = tmp_path / "out"
    outside = tmp_path / "outside"
    dest.mkdir()
    outside.mkdir()
    (dest / "foo").symlink_to(outside)
    report = open_and_extract(
        io.BytesIO(_tar([("file", name, b"x")])),
        dest,
        policy=ExtractionPolicy.STRICT,
        on_error=OnError.CONTINUE,
    )
    result = report.results[0]
    assert result.status is ExtractionStatus.BLOCKED
    assert isinstance(result.error, FilterRejectionError)
    assert result.error.member_name == name
    assert list(outside.iterdir()) == []


# --- Characters Windows refuses are escaped ----------------------------------------


@pytest.mark.parametrize(
    "name,written",
    [
        ("a?b", "a%3Fb"),
        ('q"u<o>t|e*', "q%22u%3Co%3Et%7Ce%2A"),
        ("tab\there", "tab%09here"),
        ("line\nbreak\x1f", "line%0Abreak%1F"),
        ("bell\x01", "bell%01"),
        # A '%' in a name the escape rewrites is escaped too, so it reads back.
        ("50%?", "50%25%3F"),
        # Each segment, directories included.
        ("d?r/f*le", "d%3Fr/f%2Ale"),
    ],
)
@_PORTABLE
def test_windows_invalid_characters_are_escaped(
    name: str, written: str, policy: ExtractionPolicy
) -> None:
    out = apply_name_policy(_member(name), policy)
    assert out.name == written


@_PORTABLE
def test_windows_invalid_characters_are_escaped_on_disk(
    tmp_path: Path, policy: ExtractionPolicy
) -> None:
    dest = tmp_path / "out"
    report = open_and_extract(
        io.BytesIO(_tar([("file", "what?.txt", b"x"), ("file", "a|b", b"y")])),
        dest,
        policy=policy,
    )
    assert [r.status for r in report.results] == [ExtractionStatus.EXTRACTED] * 2
    assert _tree(dest) == ["a%7Cb", "what%3F.txt"]
    assert [r.presented_name for r in report.results] == ["what?.txt", "a|b"]


def test_plain_names_and_percent_are_not_escaped() -> None:
    for name in ["50%.txt", "café", "a b", "x\x7fy", "#&;'"]:
        member = _member(name)
        assert apply_name_policy(member, ExtractionPolicy.STRICT) is member


@pytest.mark.skipif(os.name == "nt", reason="Windows refuses these names")
def test_windows_invalid_characters_are_written_under_trusted(tmp_path: Path) -> None:
    open_and_extract(
        io.BytesIO(_tar([("file", "what?.txt", b"x")])),
        tmp_path / "out",
        policy=ExtractionPolicy.TRUSTED,
    )
    assert _tree(tmp_path / "out") == ["what?.txt"]


@_PORTABLE
def test_hardlink_to_an_escaped_name_links_to_what_was_written(
    tmp_path: Path, policy: ExtractionPolicy
) -> None:
    dest = tmp_path / "out"
    report = open_and_extract(
        io.BytesIO(_tar([("file", "a?b", b"x"), ("hard", "l", "a?b")])),
        dest,
        policy=policy,
    )
    assert [r.status for r in report.results] == [ExtractionStatus.EXTRACTED] * 2
    assert _tree(dest) == ["a%3Fb", "l"]
    assert (dest / "l").read_bytes() == b"x"
