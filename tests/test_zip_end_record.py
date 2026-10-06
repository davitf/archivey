"""ZIP end record and central directory that disagree (format-zip §"End record checks").

stdlib zipfile reads entries until it has consumed the directory size the end record
gives and ignores the rest. Info-ZIP unzip and 7-Zip list and test every member, then
report the damage; archivey lists the members and reports ``ARCHIVE_EOF_MARKER_MISSING``
after them, so ``strict()`` refuses the archive.
"""

from __future__ import annotations

import io
import struct
import zipfile

import pytest

from archivey import open_archive
from archivey.config import ArchiveyConfig
from archivey.diagnostics import (
    ArchiveEofContext,
    DiagnosticCode,
    DiagnosticPolicy,
    DiagnosticSummary,
)
from archivey.exceptions import DiagnosticRaisedError

_STRICT = ArchiveyConfig(diagnostic_policy=DiagnosticPolicy.strict())
_EOCD = b"PK\x05\x06"


def _zip(comment: bytes = b"") -> bytearray:
    bio = io.BytesIO()
    with zipfile.ZipFile(bio, "w") as zf:
        zf.writestr("a.txt", b"AAAA")
        zf.writestr("b.txt", b"BBBB")
        zf.writestr("c.txt", b"CCCC")
        zf.comment = comment
    return bytearray(bio.getvalue())


def _with_eocd_count(count: int) -> bytes:
    data = _zip()
    eocd = data.rfind(_EOCD)
    struct.pack_into("<HH", data, eocd + 8, count, count)
    return bytes(data)


def _with_zip64_count(count: int) -> bytes:
    """The archive with a ZIP64 end record and locator declaring ``count`` entries,
    and the classic record's fields set to their ZIP64 sentinels."""
    data = _zip()
    eocd = data.rfind(_EOCD)
    cd_size, cd_offset = struct.unpack_from("<II", data, eocd + 12)
    body = bytes(data[:eocd])
    record = struct.pack(
        "<4sQHHIIQQQQ",
        b"PK\x06\x06",
        44,
        45,
        45,
        0,
        0,
        count,
        count,
        cd_size,
        cd_offset,
    )
    locator = struct.pack("<4sIQI", b"PK\x06\x07", 0, len(body), 1)
    classic = struct.pack(
        "<4sHHHHIIH", _EOCD, 0, 0, 0xFFFF, 0xFFFF, 0xFFFFFFFF, 0xFFFFFFFF, 0
    )
    return body + record + locator + classic


def _last_cd_entry_field(offset: int, value: int) -> bytes:
    """The archive with one length field of its last central directory entry set."""
    data = _zip(comment=b"hi there")
    entry = data.rfind(b"PK\x01\x02")
    struct.pack_into("<H", data, entry + offset, value)
    return bytes(data)


def _list(data: bytes) -> tuple[list[str], DiagnosticSummary]:
    with open_archive(io.BytesIO(data)) as reader:
        names = [member.name for member in reader.members()]
        for member in reader.members():
            reader.read(member)
        return names, reader.diagnostics


def _end_record_contexts(summary: DiagnosticSummary) -> list[ArchiveEofContext]:
    contexts = []
    for diagnostic in summary.retained:
        if diagnostic.code is DiagnosticCode.ARCHIVE_EOF_MARKER_MISSING:
            assert isinstance(diagnostic.context, ArchiveEofContext)
            contexts.append(diagnostic.context)
    return contexts


@pytest.mark.parametrize("declared", [2, 4])
def test_entry_count_mismatch_is_reported(declared: int) -> None:
    data = _with_eocd_count(declared)
    names, summary = _list(data)
    assert names == ["a.txt", "b.txt", "c.txt"]
    (context,) = _end_record_contexts(summary)
    assert context.format == "zip"
    assert context.expected_marker == "end_of_central_directory"
    assert context.observed_kind == "nonzero"
    assert context.observed_bytes == data.rfind(_EOCD)
    (diagnostic,) = summary.retained
    assert f"declares {declared} entries" in diagnostic.message
    assert "holds 3" in diagnostic.message


def test_zip64_entry_count_mismatch_is_reported() -> None:
    names, summary = _list(_with_zip64_count(5))
    assert names == ["a.txt", "b.txt", "c.txt"]
    (context,) = _end_record_contexts(summary)
    assert context.expected_marker == "end_of_central_directory"
    assert "ZIP64" in summary.retained[0].message


def test_consistent_zip64_end_record_is_clean() -> None:
    names, summary = _list(_with_zip64_count(3))
    assert names == ["a.txt", "b.txt", "c.txt"]
    assert summary.total_count == 0


def test_archive_comment_past_end_of_file_is_reported() -> None:
    data = _zip(comment=b"hi there")
    eocd = data.rfind(_EOCD)
    struct.pack_into("<H", data, eocd + 20, 5000)
    names, summary = _list(bytes(data))
    assert names == ["a.txt", "b.txt", "c.txt"]
    (context,) = _end_record_contexts(summary)
    assert context.expected_marker == "end_of_central_directory"
    assert context.observed_kind == "short"
    assert context.expected_bytes == 22 + 5000
    assert context.observed_bytes == 22 + len(b"hi there")


@pytest.mark.parametrize(
    ("offset", "field"),
    [(30, "extra field"), (32, "comment")],
    ids=["extra", "comment"],
)
def test_central_directory_field_past_the_directory_is_reported(
    offset: int, field: str
) -> None:
    names, summary = _list(_last_cd_entry_field(offset, 3000))
    assert names == ["a.txt", "b.txt", "c.txt"]
    (context,) = _end_record_contexts(summary)
    assert context.expected_marker == "central_directory"
    assert context.observed_kind == "nonzero"
    assert context.observed_bytes > context.expected_bytes
    message = summary.retained[0].message
    assert "'c.txt'" in message
    assert f"its {field} is cut short" in message


def test_clean_archive_with_comment_reports_nothing() -> None:
    names, summary = _list(bytes(_zip(comment=b"a comment")))
    assert names == ["a.txt", "b.txt", "c.txt"]
    assert summary.total_count == 0


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(_with_eocd_count(2), id="count"),
        pytest.param(_last_cd_entry_field(32, 3000), id="cd-comment"),
    ],
)
def test_end_record_mismatch_refused_under_strict(data: bytes) -> None:
    with open_archive(io.BytesIO(data), config=_STRICT) as reader:
        with pytest.raises(DiagnosticRaisedError):
            reader.members()
