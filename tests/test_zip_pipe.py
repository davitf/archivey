"""A ZIP read from a source that cannot seek, through its local headers.

The reader (``zip_forward_reader``) walks the local headers, yields each member with
what its header says, and updates the members in place from the central directory at
the end of the pass. ``open_archive`` still refuses a pipe for ZIP until the rest of
the change lands, so these tests open the gate themselves.
"""

from __future__ import annotations

import dataclasses
import io
import struct
import zipfile
import zlib
from collections.abc import Iterator
from pathlib import Path

import pytest

import archivey
from archivey.config import ListingLimits
from archivey.cost import AccessCost, ListingCost, StreamCapability
from archivey.diagnostics import (
    ArchiveEofContext,
    DiagnosticCode,
    SymlinkTargetContext,
)
from archivey.exceptions import (
    CorruptionError,
    ResourceLimitError,
    UnsupportedFeatureError,
)
from archivey.internal.backends.zip_reader import ZipReadBackend
from archivey.types import ArchiveMember, MemberType
from tests.sample_archives import (
    CORPUS,
    CorpusEntry,
    build_archive,
    skip_unless_runnable,
)
from tests.streams_util import NonSeekableBytesIO, ShortReadNonSeekable


@pytest.fixture(autouse=True)
def _pipes_open(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ZipReadBackend, "SUPPORTS_STREAMING_NON_SEEKABLE", True)


class _Writer(io.RawIOBase):
    """A pipe to write into: stdlib ``zipfile`` then sets bit 3 on every member."""

    def __init__(self) -> None:
        self.data = bytearray()

    def writable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def write(self, b: bytes | bytearray | memoryview) -> int:  # type: ignore[override]
        self.data += b
        return len(b)


def _streamed(data: bytes) -> bytes:
    """``data``'s members written again to a pipe: bit 3 and a descriptor on each."""
    out = _Writer()
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(out, "w") as dst:
        dst.comment = src.comment
        for info in src.infolist():
            dst.writestr(info, src.read(info), compress_type=info.compress_type)
    return bytes(out.data)


def _zip(entries: list[tuple[str, bytes]], *, method: int = zipfile.ZIP_DEFLATED,
         comment: bytes = b"") -> bytes:  # fmt: skip
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", method) as zf:
        zf.comment = comment
        for name, data in entries:
            zf.writestr(name, data)
    return buf.getvalue()


def _symlink_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("a.txt", b"hello")
        info = zipfile.ZipInfo("link")
        info.create_system = 3
        info.external_attr = 0o120777 << 16
        zf.writestr(info, b"a.txt")
    return buf.getvalue()


def _symlink_zip_stored() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("a.txt", b"hello")
        info = zipfile.ZipInfo("link")
        info.create_system = 3
        info.external_attr = 0o120777 << 16
        zf.writestr(info, b"a.txt")
    return buf.getvalue()


def _pipe(data: bytes) -> NonSeekableBytesIO:
    return NonSeekableBytesIO(data)


def _visible(member: ArchiveMember) -> dict[str, object]:
    """A member's public fields and the codes of what it reported."""
    out = {
        f.name: getattr(member, f.name)
        for f in dataclasses.fields(member)
        if not f.name.startswith("_") and f.name not in ("link_target_member", "extra")
    }
    out["extra"] = dict(member.extra)
    out["diagnostics"] = sorted(str(d.code) for d in member._diagnostics)  # type: ignore[attr-defined]
    return out


def _seekable_view(data: bytes) -> tuple[list[dict[str, object]], dict[int, bytes]]:
    with archivey.open_archive(io.BytesIO(data)) as reader:
        members = reader.members()
        contents = {
            m._member_id: reader.read(m)  # type: ignore[misc]
            for m in members
            if m.is_file
        }
        return [_visible(m) for m in members], contents


def _piped_view(
    data: bytes, source: object = None
) -> tuple[list[dict[str, object]], dict[int, bytes]]:
    yielded: list[tuple[ArchiveMember, bytes | None]] = []
    stream = source if source is not None else _pipe(data)
    with archivey.open_archive(stream, streaming=True) as reader:  # type: ignore[arg-type]
        for member, data_stream in reader.stream_members():
            yielded.append(
                (member, data_stream.read() if data_stream is not None else None)
            )
        listed = list(reader.members_report().members)
    assert [m for m, _ in yielded] == listed
    contents = {m._member_id: d for m, d in yielded if m.is_file and d is not None}  # type: ignore[misc]
    return [_visible(m) for m in listed], contents


def _corpus_zips() -> Iterator[pytest.ParameterSet]:
    for entry in CORPUS:
        if "zip" in entry.formats and not entry.passwords:
            yield pytest.param(entry, id=entry.id)


@pytest.mark.filterwarnings("ignore:Duplicate name")
@pytest.mark.parametrize("entry", list(_corpus_zips()))
@pytest.mark.parametrize("written", ["sized", "streamed"])
def test_a_pipe_lists_the_corpus_as_the_seekable_read_does(
    entry: CorpusEntry, written: str, tmp_path: Path
) -> None:
    """Same members, fields, diagnostics and contents after the pass as from a file.

    ``streamed`` writes every member with bit 3 and a data descriptor, as stdlib
    ``zipfile`` does to a pipe."""
    skip_unless_runnable(entry, "zip")
    path = tmp_path / "corpus.zip"
    build_archive(entry, "zip", path)
    data = path.read_bytes()
    if written == "streamed":
        data = _streamed(data)
    assert _piped_view(data) == _seekable_view(data)


def test_short_reads_from_the_pipe_change_nothing() -> None:
    data = _streamed(_zip([("a.txt", b"x" * 70_000), ("b.txt", b"hello")]))
    assert _piped_view(data, ShortReadNonSeekable(data, 7)) == _seekable_view(data)


def test_members_change_until_the_directory_is_read() -> None:
    """A member is typed from its local header, so ``member_state_final`` is false
    until the end of the pass, which updates it from the directory."""
    data = _symlink_zip()
    with archivey.open_archive(_pipe(data), streaming=True) as reader:
        seen = []
        for member, _stream in reader.stream_members():
            assert member.member_state_final is False
            assert member.mode is None
            assert member.create_system is None
            seen.append((member.name, member.type))
        assert seen == [("a.txt", MemberType.FILE), ("link", MemberType.FILE)]
        link = reader.members_report().members[1]
    assert link.type is MemberType.SYMLINK
    assert link.link_target == "a.txt"
    assert link.mode == 0o777
    assert link.member_state_final is True


def test_a_link_whose_data_the_caller_read_keeps_its_target() -> None:
    data = _streamed(_symlink_zip())
    with archivey.open_archive(_pipe(data), streaming=True) as reader:
        for _member, stream in reader.stream_members():
            assert stream is not None
            stream.read()
        link = reader.members_report().members[1]
    assert (link.type, link.link_target) == (MemberType.SYMLINK, "a.txt")


def test_what_the_caller_sees_before_and_after_the_directory() -> None:
    data = _zip([("a.txt", b"one"), ("b.txt", b"two")], comment=b"hi")
    with archivey.open_archive(_pipe(data), streaming=True) as reader:
        info = reader.info
        assert (info.member_count, info.comment) == (None, None)
        assert info.cost.listing_cost is ListingCost.REQUIRES_SCANNING
        assert info.cost.access_cost is AccessCost.DIRECT
        assert info.cost.stream_capability is StreamCapability.FORWARD_ONLY
        assert reader.members_report_if_available() is None
        for _ in reader.stream_members():
            pass
        assert (reader.info.member_count, reader.info.comment) == (2, "hi")
        assert reader.members_report_if_available() is not None


def test_a_stream_reads_nothing_once_the_walk_has_moved_on() -> None:
    data = _zip([("a.txt", b"one"), ("b.txt", b"two")])
    with archivey.open_archive(_pipe(data), streaming=True) as reader:
        passes = reader.stream_members()
        _member, first = next(passes)
        assert first is not None
        next(passes)
        with pytest.raises(ValueError, match="closed"):
            first.read()


def _corrupt_stored(data: bytes, payload: bytes) -> bytes:
    at = data.index(payload)
    return data[:at] + bytes([payload[0] ^ 0xFF]) + data[at + 1 :]


def _corrupt_data(data: bytes, method: int) -> bytes:
    """``data`` with the middle of its first member's data overwritten."""
    if method == zipfile.ZIP_STORED:
        return _corrupt_stored(data, b"payload bytes " * 100)
    name_len, extra_len = struct.unpack_from("<HH", data, 26)
    (size,) = struct.unpack_from("<I", data, 18)
    at = 30 + name_len + extra_len + size // 2
    return data[:at] + b"\xff" * 8 + data[at + 8 :]


@pytest.mark.parametrize(
    "method",
    [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA],
)
def test_a_damaged_member_fails_when_read_and_not_when_skipped(method: int) -> None:
    """Short enough to be a link target, the skipped member is decoded anyway (its
    bytes would be the target); that decode failing costs only those bytes."""
    payload = b"payload bytes " * 100
    data = _zip([("bad.bin", payload), ("ok.txt", b"fine")], method=method)
    data = _corrupt_data(data, method)
    with archivey.open_archive(io.BytesIO(data)) as reader:
        with pytest.raises(CorruptionError):
            reader.read(reader.get("bad.bin"))
    with archivey.open_archive(_pipe(data), streaming=True) as reader:
        for member, stream in reader.stream_members():
            if member.name == "bad.bin":
                assert stream is not None
                with pytest.raises(CorruptionError):
                    stream.read()
    with archivey.open_archive(_pipe(data), streaming=True) as reader:
        names = [
            m.name for m, _ in reader.stream_members(lambda m: m.name != "bad.bin")
        ]
    assert names == ["ok.txt"]


def test_a_damaged_bit3_stored_member_ends_the_pass() -> None:
    """The scan finds a bit-3 STORED member's end by the CRC of the bytes before its
    descriptor, so damaged data has no end the walk can find."""
    payload = b"payload bytes " * 100
    data = _streamed(_zip([("bad.bin", payload)], method=zipfile.ZIP_STORED))
    data = _corrupt_stored(data, payload)
    with archivey.open_archive(_pipe(data), streaming=True) as reader:
        with pytest.raises(UnsupportedFeatureError, match="data descriptor"):
            for _ in reader.stream_members():
                pass


def test_a_deflated_member_whose_descriptor_crc_differs_fails_when_read() -> None:
    data = bytearray(_streamed(_zip([("a.txt", b"hello world " * 50)])))
    # The descriptor sits right after the data: PK\x07\x08, then the CRC.
    at = data.index(b"PK\x07\x08") + 4
    data[at] ^= 0x01
    with archivey.open_archive(_pipe(bytes(data)), streaming=True) as reader:
        for _member, stream in reader.stream_members():
            assert stream is not None
            with pytest.raises(CorruptionError, match="CRC"):
                stream.read()


def _drop_local_member(data: bytes, name: bytes) -> bytes:
    """``data`` with the central entry for ``name`` pointed at a missing local header."""
    out = bytearray(data)
    cd = out.index(b"PK\x01\x02" + b"\x14", 0)
    while cd >= 0:
        name_len, extra_len, comment_len = struct.unpack_from("<HHH", out, cd + 28)
        if bytes(out[cd + 46 : cd + 46 + name_len]) == name:
            struct.pack_into("<I", out, cd + 42, 999_999)
            return bytes(out)
        cd = out.find(b"PK\x01\x02", cd + 46 + name_len + extra_len + comment_len)
    raise AssertionError(name)


def test_a_damaged_link_target_is_reported_as_damaged() -> None:
    data = _zip(
        [("link", b"payload bytes " * 100), ("ok.txt", b"fine")],
        method=zipfile.ZIP_DEFLATED,
    )
    data = bytearray(_corrupt_data(data, zipfile.ZIP_DEFLATED))
    cd = data.index(b"PK\x01\x02")
    data[cd + 5] = 3  # made on Unix
    struct.pack_into("<I", data, cd + 38, 0o120777 << 16)  # a symlink's mode
    with archivey.open_archive(_pipe(bytes(data)), streaming=True) as reader:
        reader.members_report()
        assert _link_target_reasons(reader) == ["target_data_damaged"]


def _link_target_reasons(reader: archivey.ArchiveReader) -> list[str]:
    return [
        d.context.reason
        for d in reader.diagnostics.retained
        if d.code is DiagnosticCode.SYMLINK_TARGET_UNAVAILABLE
        and isinstance(d.context, SymlinkTargetContext)
    ]


def test_an_encrypted_link_target_needs_a_password_as_from_a_file() -> None:
    data = bytearray(_symlink_zip_stored())
    data[data.index(b"PK\x03\x04", 1) + 6] |= 0x01
    cd = data.index(b"PK\x01\x02", data.index(b"PK\x01\x02") + 1)
    data[cd + 8] |= 0x01

    for source, streaming in ((_pipe(bytes(data)), True), (io.BytesIO(data), False)):
        with archivey.open_archive(source, streaming=streaming) as reader:
            reader.members_report()
            assert _link_target_reasons(reader) == ["password_required"]


def test_an_encrypted_bit3_compressed_member_ends_the_pass() -> None:
    """Nothing but its decrypted codec finds where its data ends."""
    data = bytearray(_streamed(_zip([("a.txt", b"one" * 50), ("b.txt", b"two")])))
    data[6] |= 0x01
    data[data.index(b"PK\x01\x02") + 8] |= 0x01
    with archivey.open_archive(_pipe(bytes(data)), streaming=True) as reader:
        with pytest.raises(UnsupportedFeatureError, match="only decrypting"):
            for _ in reader.stream_members(lambda m: m.name == "b.txt"):
                pass


def _repeat_directory(data: bytes, times: int) -> bytes:
    """``data`` (one member) with its one central entry repeated ``times`` times."""
    cd = data.index(b"PK\x01\x02")
    eocd = data.index(b"PK\x05\x06")
    entry = data[cd:eocd]
    end = bytearray(data[eocd:])
    struct.pack_into("<HHI", end, 8, times, times, len(entry) * times)
    return data[:cd] + entry * times + bytes(end)


@pytest.mark.parametrize("source", ["pipe", "file"])
def test_max_members_counts_the_directory_entries(source: str) -> None:
    data = _repeat_directory(_zip([("a.txt", b"one")], method=zipfile.ZIP_STORED), 50)
    config = archivey.ArchiveyConfig(listing_limits=ListingLimits(max_members=10))
    opened = _pipe(data) if source == "pipe" else io.BytesIO(data)
    with pytest.raises(ResourceLimitError, match="max_members=10"):
        with archivey.open_archive(opened, streaming=True, config=config) as reader:
            for _ in reader.stream_members():
                pass


def test_two_entries_for_one_local_header_are_named_as_such() -> None:
    data = _repeat_directory(_zip([("a.txt", b"one")], method=zipfile.ZIP_STORED), 2)
    with archivey.open_archive(_pipe(data), streaming=True) as reader:
        report = reader.members_report()
    assert isinstance(report.error, CorruptionError)
    assert "an earlier entry already describes" in str(report.error)


def test_directory_comments_count_toward_max_metadata_bytes() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i in range(20):
            info = zipfile.ZipInfo(f"{i}.txt")
            info.comment = b"c" * 6000
            zf.writestr(info, b"x")
    data = buf.getvalue()
    config = archivey.ArchiveyConfig(
        listing_limits=ListingLimits(max_metadata_bytes=100_000)
    )
    with pytest.raises(ResourceLimitError, match="max_metadata_bytes"):
        with archivey.open_archive(io.BytesIO(data), config=config) as reader:
            reader.members()
    with archivey.open_archive(_pipe(data), streaming=True, config=config) as reader:
        with pytest.raises(ResourceLimitError, match="max_metadata_bytes"):
            reader.members_report()


@pytest.mark.parametrize(("delta", "kind"), [(-10, "nonzero"), (10, "short")])
def test_a_directory_size_the_entries_do_not_fill_is_reported(
    delta: int, kind: str
) -> None:
    data = bytearray(_zip([("a.txt", b"one"), ("b.txt", b"two")]))
    eocd = data.index(b"PK\x05\x06")
    (size,) = struct.unpack_from("<I", data, eocd + 12)
    struct.pack_into("<I", data, eocd + 12, size + delta)
    with archivey.open_archive(_pipe(bytes(data)), streaming=True) as reader:
        names = [m.name for m in reader.members_report().members]
        found = [
            (d.context.expected_marker, d.context.observed_kind)
            for d in reader.diagnostics.retained
            if d.code is DiagnosticCode.ARCHIVE_EOF_MARKER_MISSING
            and isinstance(d.context, ArchiveEofContext)
        ]
    assert names == ["a.txt", "b.txt"]
    assert found == [("central_directory", kind)]


def test_a_stream_members_pass_discards_a_listing_the_directory_overfills() -> None:
    """The directory's comments take the listing over the limit after the last
    member; the pass keeps nothing then, as from a file, and the link it made still
    gets its target."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i in range(20):
            info = zipfile.ZipInfo(f"{i}.txt")
            info.comment = b"c" * 6000
            zf.writestr(info, b"x")
        link = zipfile.ZipInfo("link")
        link.create_system = 3
        link.external_attr = 0o120777 << 16
        zf.writestr(link, b"0.txt")
    config = archivey.ArchiveyConfig(
        listing_limits=ListingLimits(max_metadata_bytes=100_000)
    )
    with archivey.open_archive(
        _pipe(buf.getvalue()), streaming=True, config=config
    ) as reader:
        yielded = [m for m, _ in reader.stream_members()]
        assert reader._listing_discarded
        assert reader._listed == []
    assert (yielded[-1].type, yielded[-1].link_target) == (MemberType.SYMLINK, "0.txt")


def test_too_many_local_headers_name_what_was_counted() -> None:
    data = _zip([(f"{i}.txt", b"x") for i in range(5)])
    config = archivey.ArchiveyConfig(listing_limits=ListingLimits(max_members=3))
    with archivey.open_archive(_pipe(data), streaming=True, config=config) as reader:
        with pytest.raises(ResourceLimitError, match="local headers"):
            for _ in reader.stream_members():
                pass


def test_a_directory_entry_with_no_local_header_fails_after_the_members() -> None:
    data = _drop_local_member(_zip([("a.txt", b"one"), ("b.txt", b"two")]), b"b.txt")
    with archivey.open_archive(_pipe(data), streaming=True) as reader:
        report = reader.members_report()
    assert [m.name for m in report.members] == ["a.txt", "b.txt"]
    assert isinstance(report.error, CorruptionError)
    assert "no local header" in str(report.error)


@pytest.mark.parametrize("source", ["pipe", "file"])
def test_max_members_is_charged_under_streaming(source: str) -> None:
    """From a pipe the walk charges each local header; from a file, under
    ``streaming=True``, the end record's count is refused at open."""
    data = _zip([(f"{i}.txt", b"x") for i in range(5)])
    config = archivey.ArchiveyConfig(listing_limits=ListingLimits(max_members=3))
    opened = _pipe(data) if source == "pipe" else io.BytesIO(data)
    with pytest.raises(ResourceLimitError, match="max_members=3"):
        with archivey.open_archive(opened, streaming=True, config=config) as reader:
            for _ in reader.stream_members():
                pass


def test_bytes_after_the_end_record_are_reported() -> None:
    data = _zip([("a.txt", b"one")]) + b"\x00\x00junk"
    with archivey.open_archive(_pipe(data), streaming=True) as reader:
        reader.members_report()
        codes = {d.code for d in reader.diagnostics.retained}
    assert DiagnosticCode.ARCHIVE_TRAILING_DATA in codes


def test_a_stream_that_is_no_zip_fails_at_open() -> None:
    with pytest.raises(CorruptionError):
        archivey.open_archive(
            _pipe(b"PK\x03\x04" + b"\xff" * 100), streaming=True, format="zip"
        )


def test_an_encrypted_member_is_refused_when_read_and_skipped_otherwise() -> None:
    plain = _zip([("a.txt", b"one"), ("b.txt", b"two")], method=zipfile.ZIP_STORED)
    data = bytearray(plain)
    # Set the encryption bit on a.txt in both headers; its data is then "ciphertext".
    data[6] |= 0x01
    cd = data.index(b"PK\x01\x02")
    data[cd + 8] |= 0x01
    with archivey.open_archive(_pipe(bytes(data)), streaming=True) as reader:
        passes = reader.stream_members()
        member, stream = next(passes)
        assert member.is_encrypted and stream is not None
        with pytest.raises(UnsupportedFeatureError, match="encrypted"):
            stream.read()
        assert [m.name for m, _ in passes] == ["b.txt"]


def test_a_bit3_lzma_member_with_no_end_marker_is_refused() -> None:
    import lzma

    filt = {"id": lzma.FILTER_LZMA1, "dict_size": 1 << 16}
    props = lzma._encode_filter_properties(filt)  # type: ignore[attr-defined]
    body = struct.pack("<BBH", 9, 20, len(props)) + props
    body += lzma.compress(b"abc" * 100, format=lzma.FORMAT_RAW, filters=[filt])
    name = b"a.bin"
    crc = zlib.crc32(b"abc" * 100)
    local = struct.pack(
        "<IHHHHHIIIHH", 0x04034B50, 63, 0x08, 14, 0, 0x21, 0, 0, 0, len(name), 0
    )
    member = local + name + body
    member += struct.pack("<IIII", 0x08074B50, crc, len(body), 300)
    central = struct.pack(
        "<IHHHHHHIIIHHHHHII", 0x02014B50, 63, 63, 0x08, 14, 0, 0x21, crc, len(body),
        300, len(name), 0, 0, 0, 0, 0, 0,
    ) + name  # fmt: skip
    end = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, 1, 1, len(central), len(member), 0)
    data = member + central + end
    with archivey.open_archive(_pipe(data), streaming=True) as reader:
        with pytest.raises(UnsupportedFeatureError, match="end-of-stream marker"):
            for _ in reader.stream_members():
                pass


def _reparse_zip(data: bytes) -> bytes:
    from archivey.internal.windows_reparse import FILE_ATTRIBUTE_REPARSE_POINT

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        info = zipfile.ZipInfo("tree/entry")
        info.create_system = 0
        info.external_attr = 0x20 | FILE_ATTRIBUTE_REPARSE_POINT
        zf.writestr(info, data)
        zf.writestr("tree/after.txt", b"next member")
    return buf.getvalue()


def test_a_reparse_member_whose_data_is_no_link_stays_a_file() -> None:
    """From a pipe the reparse flag arrives after the data, so the member is yielded
    as a file with its content, and the directory leaves it one."""
    content = b"not a reparse buffer " * 10
    with archivey.open_archive(_pipe(_reparse_zip(content)), streaming=True) as reader:
        got = [
            (m.type, s.read() if s is not None else None)
            for m, s in reader.stream_members()
        ]
        entry = reader.members_report().members[0]
    assert got[0] == (MemberType.FILE, content)
    assert entry.type is MemberType.FILE


def test_a_reparse_member_holding_a_link_buffer_becomes_a_link() -> None:
    from archivey.internal.windows_reparse import IO_REPARSE_TAG_SYMLINK
    from tests.test_windows_reparse import _reparse_buffer

    buffer = _reparse_buffer(IO_REPARSE_TAG_SYMLINK, "after.txt", "after.txt")
    with archivey.open_archive(_pipe(_reparse_zip(buffer)), streaming=True) as reader:
        entry = reader.members_report().members[0]
    assert (entry.type, entry.link_target) == (MemberType.SYMLINK, "after.txt")


def test_a_stub_in_front_of_the_archive_is_read_past() -> None:
    stub = b"MZ" + b"\x90" * 5000
    data = stub + _zip([("a.txt", b"one")])
    with archivey.open_archive(_pipe(data), streaming=True, format="zip") as reader:
        report = reader.members_report()
    assert report.error is None
    assert [m.name for m in report.members] == ["a.txt"]
