"""The ZIP reader on archivey's own parser: what changed when ``zipfile`` left.

- A damaged central directory lists the entries before the damage, then raises, as a
  cut TAR or RAR does (DR-2).
- "Version needed to extract" above 6.3 no longer refuses the archive (DR-4): only a
  method or flag archivey cannot read refuses a member.
- Bytes after the end record and its comment are ``ARCHIVE_TRAILING_DATA`` (DR-3), as
  after a TAR trailer or a 7z archive's end, also when there are more of them than
  the end-record search reads: a walk over the local headers finds the directory.
- The directory is walked as members are listed; the overlap guard's offsets and
  ``member_count`` come from that walk, or from one index pass when a member opens
  first.
- A listed member keeps a small locator, not the directory entry.
"""

from __future__ import annotations

import gc
import io
import struct
import tracemalloc
import zipfile

import pytest

from archivey import open_archive
from archivey.config import ArchiveyConfig
from archivey.diagnostics import ArchiveEofContext, DiagnosticCode, DiagnosticPolicy
from archivey.exceptions import (
    CorruptionError,
    DiagnosticRaisedError,
    UnsupportedFeatureError,
)
from archivey.internal.backends import zip_parser
from archivey.types import ArchiveFormat
from tests.corruption_util import raises_corruption_not_truncation

_NAMES = ("a.txt", "b.txt", "c.txt")


def _zip(names: tuple[str, ...] = _NAMES, *, comment: bytes = b"") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name in names:
            zf.writestr(name, name.encode() * 3)
        zf.comment = comment
    return buf.getvalue()


def _central_headers(data: bytes) -> list[int]:
    found, at = [], data.find(b"PK\x01\x02")
    while at >= 0:
        found.append(at)
        at = data.find(b"PK\x01\x02", at + 1)
    return found


# --- A damaged directory lists, then raises -----------------------------------------


def _with_bad_third_header() -> bytes:
    data = bytearray(_zip())
    third = _central_headers(bytes(data))[2]
    data[third : third + 4] = b"XXXX"
    return bytes(data)


def _with_cut_header_after_the_second() -> bytes:
    """A 10-byte fragment of a header where the third should start.

    The directory always ends where the end record starts, so a cut header is a
    fragment there, inside the declared size.
    """
    data = bytearray(_zip(_NAMES[:2]))
    eocd = bytes(data).rindex(b"PK\x05\x06")
    (cd_size,) = struct.unpack_from("<I", data, eocd + 12)
    struct.pack_into("<HHI", data, eocd + 8, 3, 3, cd_size + 10)
    return bytes(data[:eocd] + b"PK\x01\x02" + bytes(6) + data[eocd:])


@pytest.mark.parametrize(
    ("build", "match"),
    [
        pytest.param(_with_bad_third_header, "Bad magic number", id="bad-signature"),
        pytest.param(
            _with_cut_header_after_the_second,
            "Truncated central directory",
            id="cut-header",
        ),
    ],
)
def test_damaged_directory_lists_the_entries_before_the_damage(
    build: object, match: str
) -> None:
    data = build()  # type: ignore[operator]
    with open_archive(io.BytesIO(data), format=ArchiveFormat.ZIP) as reader:
        report = reader.members_report()
        assert [m.name for m in report.members] == ["a.txt", "b.txt"]
        assert isinstance(report.error, CorruptionError)
        assert match in str(report.error)
        # The members before the damage read, with the overlap guard's bounds from
        # the offsets the walk read.
        assert reader.read(report.members[1]) == b"b.txtb.txtb.txt"
        with raises_corruption_not_truncation(match=match):
            reader.members()
        assert reader.info.member_count is None


def test_damaged_directory_iteration_yields_then_raises() -> None:
    seen: list[str] = []
    with open_archive(io.BytesIO(_with_bad_third_header())) as reader:
        with raises_corruption_not_truncation(match="Bad magic number"):
            for member in reader:
                seen.append(member.name)
    assert seen == ["a.txt", "b.txt"]


def test_damaged_directory_extract_all_writes_then_raises(tmp_path: object) -> None:
    from pathlib import Path

    dest = Path(str(tmp_path)) / "out"
    with open_archive(io.BytesIO(_with_bad_third_header())) as reader:
        with raises_corruption_not_truncation(match="Bad magic number"):
            reader.extract_all(dest)
    assert sorted(p.name for p in dest.iterdir()) == ["a.txt", "b.txt"]


# --- Version needed above 6.3 -------------------------------------------------------


def _with_version_needed(version: int) -> bytes:
    """Every header says "version needed to extract" is ``version``, a uint16."""
    data = bytearray(_zip(("v.txt",)))
    struct.pack_into("<H", data, 4, version)  # local header
    (central,) = _central_headers(bytes(data))
    struct.pack_into("<H", data, central + 6, version)
    return bytes(data)


def test_version_needed_above_6_3_lists_and_reads() -> None:
    # 7-Zip 23.01 reads a STORED member marked 8.4; Info-ZIP unzip 6.0 skips it;
    # stdlib refused the whole archive at open.
    with open_archive(io.BytesIO(_with_version_needed(84))) as reader:
        (member,) = reader.members()
        assert reader.read(member) == b"v.txtv.txtv.txt"


def test_unknown_method_still_refuses_only_its_member() -> None:
    data = bytearray(_with_version_needed(84))
    (central,) = _central_headers(bytes(data))
    struct.pack_into("<H", data, 8, 77)  # local method
    struct.pack_into("<H", data, central + 10, 77)  # central method
    with open_archive(io.BytesIO(bytes(data))) as reader:
        (member,) = reader.members()
        with pytest.raises(UnsupportedFeatureError, match="compression method 77"):
            reader.read(member)


# --- Trailing bytes -----------------------------------------------------------------


def _trailing(data: bytes) -> list[ArchiveEofContext]:
    with open_archive(io.BytesIO(data), format=ArchiveFormat.ZIP) as reader:
        members = reader.members()
        assert [m.name for m in members] == list(_NAMES)
        assert reader.read(members[0]) == b"a.txta.txta.txt"
        found = [
            d.context
            for d in reader.diagnostics.retained
            if d.code is DiagnosticCode.ARCHIVE_TRAILING_DATA
        ]
    assert all(isinstance(c, ArchiveEofContext) for c in found)
    return found  # type: ignore[return-value]


def test_archive_that_ends_at_its_end_record_reports_nothing() -> None:
    assert _trailing(_zip()) == []
    assert _trailing(_zip(comment=b"a comment")) == []


def test_zero_padding_after_the_end_record_is_silent() -> None:
    assert _trailing(_zip() + b"\x00" * 4096) == []


@pytest.mark.parametrize("zeros", [0, 100, 60_000])
@pytest.mark.parametrize("comment", [b"", b"note"])
def test_junk_after_the_end_record_is_reported(zeros: int, comment: bytes) -> None:
    (context,) = _trailing(_zip(comment=comment) + b"\x00" * zeros + b"JUNK")
    assert context.format == "zip"
    assert context.expected_marker == "zeros_to_eof"
    assert context.observed_kind == "nonzero"
    # Counted from the end of the declared comment.
    assert context.observed_bytes == zeros


def test_junk_inside_a_declared_comment_is_the_comment() -> None:
    # The comment's length decides where the archive ends, not its content.
    data = bytearray(_zip(comment=b"JUNK"))
    assert _trailing(bytes(data)) == []


def test_strict_policy_refuses_trailing_data() -> None:
    config = ArchiveyConfig(diagnostic_policy=DiagnosticPolicy.STRICT)
    with pytest.raises(DiagnosticRaisedError) as ei:
        with open_archive(io.BytesIO(_zip() + b"JUNK"), config=config) as reader:
            reader.members()
    assert ei.value.diagnostic.code is DiagnosticCode.ARCHIVE_TRAILING_DATA


def test_strict_policy_accepts_zero_padding() -> None:
    config = ArchiveyConfig(diagnostic_policy=DiagnosticPolicy.STRICT)
    with open_archive(io.BytesIO(_zip() + b"\x00" * 512), config=config) as reader:
        assert [m.name for m in reader.members()] == list(_NAMES)


def test_start_offset_scan_stays_inside_the_slice() -> None:
    # A stub before the archive moves nothing: the scan starts at the record's end.
    stub = b"MZ" + b"\x00" * 1000
    (context,) = _trailing(stub + _zip() + b"\x00" * 7 + b"JUNK")
    assert context.observed_bytes == 7


# --- Past the search window: the walk over the local headers -------------------------

# More bytes after the archive than the end-record search reads.
_PAST_WINDOW = b"\x01" * (1 << 20)


class _Pipe(io.RawIOBase):
    """A sink that cannot seek: stdlib ``zipfile`` then sets bit 3 on every member."""

    def __init__(self) -> None:
        self.data = bytearray()

    def writable(self) -> bool:
        return True

    def write(self, b: bytes | bytearray | memoryview) -> int:  # type: ignore[override]
        self.data += b
        return len(b)


def _bit3_zip(method: int) -> bytes:
    out = _Pipe()
    with zipfile.ZipFile(out, "w", method) as zf:
        for name in _NAMES:
            zf.writestr(name, name.encode() * 3)
    return bytes(out.data)


@pytest.mark.parametrize("method", [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED])
@pytest.mark.parametrize("bit3", [False, True], ids=["sized", "bit3"])
def test_junk_past_the_search_window_is_reported(method: int, bit3: bool) -> None:
    """The walk over the local headers finds where the members end, and the end
    record is found by stepping over the directory after them."""
    if bit3:
        data = _bit3_zip(method)
    else:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", method) as zf:
            for name in _NAMES:
                zf.writestr(name, name.encode() * 3)
        data = buf.getvalue()
    (context,) = _trailing(data + _PAST_WINDOW)
    assert context.observed_bytes == 0


def test_junk_past_the_search_window_after_a_stub_and_a_comment() -> None:
    stub = b"MZ" + b"\x00" * 1000
    (context,) = _trailing(stub + _zip(comment=b"note") + bytes(5) + _PAST_WINDOW)
    assert context.observed_bytes == 5


def test_junk_past_the_search_window_is_refused_under_strict() -> None:
    config = ArchiveyConfig(diagnostic_policy=DiagnosticPolicy.STRICT)
    with pytest.raises(DiagnosticRaisedError) as ei:
        with open_archive(io.BytesIO(_zip() + _PAST_WINDOW), config=config) as reader:
            reader.members()
    assert ei.value.diagnostic.code is DiagnosticCode.ARCHIVE_TRAILING_DATA


class _CountingBytesIO(io.BytesIO):
    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self.bytes_read = 0

    def read(self, size: int | None = -1, /) -> bytes:
        out = super().read(size)
        self.bytes_read += len(out)
        return out

    def readinto(self, buffer: bytearray | memoryview, /) -> int:  # type: ignore[override]
        n = super().readinto(buffer)
        self.bytes_read += n
        return n


def test_the_walk_seeks_over_a_member_whose_header_states_its_size() -> None:
    payload = bytes(range(256)) * (32 << 10)  # 8 MiB, stored
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("big.bin", payload)
    source = _CountingBytesIO(buf.getvalue() + _PAST_WINDOW)
    with open_archive(source, format=ArchiveFormat.ZIP) as reader:
        assert [m.name for m in reader.members()] == ["big.bin"]
    assert source.bytes_read < len(payload) // 2


def test_a_directory_missing_past_the_search_window_fails_at_open() -> None:
    data = _zip()
    members_end = data.index(b"PK\x01\x02")
    with pytest.raises(CorruptionError, match="no end of central directory"):
        open_archive(io.BytesIO(data[:members_end] + _PAST_WINDOW), format="zip")


# --- One walk for the listing, one index pass when an open comes first ---------------


def _counting_walks(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    walks: list[int] = []
    real_iter = zip_parser.CentralDirectoryWalk.__iter__

    def counting_iter(self: zip_parser.CentralDirectoryWalk):  # type: ignore[no-untyped-def]
        walks.append(1)
        return real_iter(self)

    monkeypatch.setattr(zip_parser.CentralDirectoryWalk, "__iter__", counting_iter)
    return walks


def test_open_does_not_walk_the_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    walks = _counting_walks(monkeypatch)
    with open_archive(io.BytesIO(_zip())) as reader:
        assert walks == []
        assert reader.cost.listing_cost.name == "INDEXED"
        assert walks == []


def test_listing_then_open_walks_the_directory_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    walks = _counting_walks(monkeypatch)
    with open_archive(io.BytesIO(_zip())) as reader:
        members = reader.members()
        assert [reader.read(m) for m in members][2] == b"c.txtc.txtc.txt"
        assert reader.info.member_count == 3
    assert walks == [1]


def test_open_before_the_listing_ends_runs_one_index_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    walks = _counting_walks(monkeypatch)
    # A streaming pass opens each member as the walk yields it.
    with open_archive(io.BytesIO(_zip()), streaming=True) as reader:
        for member, stream in reader.stream_members():
            assert stream is not None
            assert stream.read() == member.name.encode() * 3
        assert reader.info.member_count == 3
    # The listing walk, and one index pass for the open that came first.
    assert walks == [1, 1]


def test_member_count_before_any_listing_runs_the_index_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    walks = _counting_walks(monkeypatch)
    with open_archive(io.BytesIO(_zip())) as reader:
        assert reader.info.member_count == 3
        assert len(reader.members()) == 3
    assert walks == [1, 1]


# --- A listed member keeps a locator, not the directory entry -----------------------


def _zip_with_large_extras(count: int, extra_size: int) -> bytes:
    """Each central entry carries an unknown extra field and a comment of
    ``extra_size`` bytes each."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i in range(count):
            info = zipfile.ZipInfo(f"f{i:05d}.txt")
            info.extra = struct.pack("<HH", 0xCAFE, extra_size - 4) + bytes(
                extra_size - 4
            )
            info.comment = b"c" * extra_size
            zf.writestr(info, b"x")
    return buf.getvalue()


def test_listing_does_not_keep_extra_fields_or_comments_alive() -> None:
    count, extra_size = 500, 4096
    data = _zip_with_large_extras(count, extra_size)
    gc.collect()
    reader = open_archive(io.BytesIO(data))
    try:
        tracemalloc.start()
        try:
            before, _ = tracemalloc.get_traced_memory()
            members = reader.members()
            gc.collect()
            after, _ = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        per_member = (after - before) / count
        # The comment is kept, decoded, on the member; the extra field is not kept at
        # all. Keeping the directory entry would hold both, over 8 KiB per member.
        assert per_member < extra_size + 3000, per_member
        assert all(m.comment == "c" * extra_size for m in members)
    finally:
        reader.close()
