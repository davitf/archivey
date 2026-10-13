"""``MEMBER_NAME_INVISIBLE_CHARACTER``: Unicode format characters a reader cannot see.

A zero-width space makes ``invoice.pdf`` and ``invoice<U+200B>.pdf`` display the same
and extract side by side. The code reports every character of Unicode category Cf that
``MEMBER_NAME_BIDI_CONTROL`` does not, in a name or a link target. It is advisory under
every preset, because a zero-width joiner is part of ordinary emoji sequences.
"""

from __future__ import annotations

import io
import tarfile
import zipfile
from pathlib import Path

import pytest

import archivey
from archivey import ArchiveyConfig
from archivey.diagnostics import (
    DiagnosticCode,
    DiagnosticPolicy,
    MemberNameControlsContext,
)
from archivey.exceptions import DiagnosticRaisedError

_INVISIBLE = DiagnosticCode.MEMBER_NAME_INVISIBLE_CHARACTER
_BIDI = DiagnosticCode.MEMBER_NAME_BIDI_CONTROL


def _archive(tmp_path: Path, kind: str, names: list[str]) -> Path:
    """A ``kind`` archive holding one small file under each of ``names``."""
    if kind == "zip":
        path = tmp_path / "a.zip"
        with zipfile.ZipFile(path, "w") as zf:
            for i, name in enumerate(names):
                zf.writestr(name, f"{i}".encode())
        return path
    path = tmp_path / "a.tar"
    with tarfile.open(path, "w", format=tarfile.PAX_FORMAT) as tf:
        for i, name in enumerate(names):
            data = f"{i}".encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return path


def _contexts(member: archivey.ArchiveMember, code: DiagnosticCode) -> list[object]:
    return [d.context for d in member.diagnostics if d.code is code]


@pytest.mark.parametrize("kind", ["zip", "tar"])
def test_zero_width_space_is_reported_and_both_files_extract(
    tmp_path: Path, kind: str
) -> None:
    archive = _archive(tmp_path, kind, ["invoice.pdf", "invoice​.pdf"])
    dest = tmp_path / "out"
    with archivey.open_archive(archive) as reader:
        plain, hidden = reader.members()
        assert _contexts(plain, _INVISIBLE) == []
        (context,) = _contexts(hidden, _INVISIBLE)
        reader.extract_all(dest)
        assert reader.diagnostics.counts[_INVISIBLE] == 1
    assert isinstance(context, MemberNameControlsContext)
    assert context.category == "invisible"
    assert context.field == "name"
    assert context.controls == "U+200B"
    assert context.member_name == "invoice​.pdf"
    assert (dest / "invoice.pdf").read_bytes() == b"0"
    assert (dest / "invoice​.pdf").read_bytes() == b"1"


def test_strict_does_not_refuse_an_invisible_character(tmp_path: Path) -> None:
    archive = _archive(tmp_path, "zip", ["invoice.pdf", "invoice​.pdf"])
    config = ArchiveyConfig(diagnostic_policy=DiagnosticPolicy.STRICT)
    with archivey.open_archive(archive, config=config) as reader:
        report = reader.extract_all(tmp_path / "out")
        assert len(report.results) == 2
        assert reader.diagnostics.counts[_INVISIBLE] == 1


def test_raise_on_refuses_an_invisible_character(tmp_path: Path) -> None:
    archive = _archive(tmp_path, "zip", ["invoice​.pdf"])
    config = ArchiveyConfig(
        diagnostic_policy=DiagnosticPolicy(
            raise_on=frozenset({"member_name_invisible_character"})
        )
    )
    with archivey.open_archive(archive, config=config) as reader:
        with pytest.raises(DiagnosticRaisedError) as excinfo:
            reader.members()
    assert excinfo.value.diagnostic.code is _INVISIBLE


@pytest.mark.parametrize(
    ("char", "spelling"),
    [
        ("­", "U+00AD"),  # soft hyphen
        ("‌", "U+200C"),  # zero-width non-joiner
        ("‍", "U+200D"),  # zero-width joiner
        ("⁠", "U+2060"),  # word joiner
        ("﻿", "U+FEFF"),  # byte order mark
        ("\U000e0067", "U+E0067"),  # tag character, as in subdivision flag emoji
    ],
)
def test_each_invisible_format_character_is_named(
    tmp_path: Path, char: str, spelling: str
) -> None:
    archive = _archive(tmp_path, "zip", [f"a{char}b.txt"])
    with archivey.open_archive(archive) as reader:
        (member,) = reader.members()
        (context,) = _contexts(member, _INVISIBLE)
    assert isinstance(context, MemberNameControlsContext)
    assert context.controls == spelling


@pytest.mark.parametrize(
    "name",
    [
        "café.txt",  # printable non-ASCII
        "❤️.txt",  # variation selector: category Mn, not Cf
        "evil‮gnp.exe",  # bidi override: MEMBER_NAME_BIDI_CONTROL only
        "‏דוח.pdf",  # bidi mark: MEMBER_NAME_BIDI_CONTROL only
    ],
)
def test_other_characters_are_not_reported_as_invisible(
    tmp_path: Path, name: str
) -> None:
    archive = _archive(tmp_path, "zip", [name])
    with archivey.open_archive(archive) as reader:
        (member,) = reader.members()
        assert _contexts(member, _INVISIBLE) == []
        assert _INVISIBLE not in reader.diagnostics.counts


def test_bidi_and_invisible_characters_are_reported_under_one_code_each(
    tmp_path: Path,
) -> None:
    archive = _archive(tmp_path, "zip", ["a‮b‍c​d.txt"])
    with archivey.open_archive(archive) as reader:
        (member,) = reader.members()
        (bidi,) = _contexts(member, _BIDI)
        (invisible,) = _contexts(member, _INVISIBLE)
    assert isinstance(bidi, MemberNameControlsContext)
    assert isinstance(invisible, MemberNameControlsContext)
    assert (bidi.category, bidi.controls) == ("bidi", "U+202E")
    assert (invisible.category, invisible.controls) == ("invisible", "U+200D,U+200B")


_TARGET = "invoice​.pdf"


def _link_archive(tmp_path: Path, kind: str) -> Path:
    """A ``kind`` archive whose symlink ``link`` stores ``_TARGET``."""
    if kind == "tar":
        path = tmp_path / "a.tar"
        with tarfile.open(path, "w", format=tarfile.PAX_FORMAT) as tf:
            info = tarfile.TarInfo("link")
            info.type = tarfile.SYMTYPE
            info.linkname = _TARGET
            tf.addfile(info)
        return path
    path = tmp_path / "a.zip"
    with zipfile.ZipFile(path, "w") as zf:
        info = zipfile.ZipInfo("link")
        info.create_system = 3
        info.external_attr = 0o120777 << 16
        zf.writestr(info, _TARGET.encode("utf-8"))
    return path


# tar: the target is in the header. zip: the target is the member's data.
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("kind", ["tar", "zip"])
def test_link_target_invisible_character_is_reported_once(
    tmp_path: Path, kind: str, streaming: bool
) -> None:
    archive = _link_archive(tmp_path, kind)
    with archivey.open_archive(archive, streaming=streaming) as reader:
        if not streaming:
            reader.members()
        report = reader.extract_all(tmp_path / "out", on_error="continue")
        (result,) = report.results
        link = result.member
        assert link.link_target == _TARGET
        (context,) = _contexts(link, _INVISIBLE)
        assert reader.diagnostics.counts[_INVISIBLE] == 1
    assert isinstance(context, MemberNameControlsContext)
    assert context.field == "link_target"
    assert context.member_name == "link"
    assert context.controls == "U+200B"
