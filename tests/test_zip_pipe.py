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
import warnings
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
    DiagnosticPolicy,
    SymlinkTargetContext,
    UnlistedDataContext,
)
from archivey.exceptions import (
    CorruptionError,
    DiagnosticRaisedError,
    ResourceLimitError,
    UnsupportedFeatureError,
)
from archivey.internal.backends.zip_forward_reader import ZipForwardReader
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
        pass_ = reader.stream_members()
        _member, stream = next(pass_)
        assert stream is not None
        with pytest.raises(CorruptionError, match="CRC"):
            stream.read()
        # The directory then contradicts the descriptor, as the end of the pass finds.
        with pytest.raises(CorruptionError, match="data descriptor after its data"):
            next(pass_)


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


def _overfilled_with_link(target: bytes, *, damaged: bool = False) -> bytes:
    """A link first, then 20 members whose long names and directory comments take the
    listing past 100 000 bytes, while the directory's own text stays under it (the
    listing weighs each name twice, as text and as raw bytes)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        link = zipfile.ZipInfo("link")
        link.create_system = 3
        link.external_attr = 0o120777 << 16
        zf.writestr(link, target)
        for i in range(20):
            info = zipfile.ZipInfo(f"{i}-" + "n" * 1900 + ".txt")
            info.comment = b"c" * 2000
            zf.writestr(info, b"x")
    data = buf.getvalue()
    return _corrupt_data(data, zipfile.ZIP_DEFLATED) if damaged else data


_OVER_100K = archivey.ArchiveyConfig(
    listing_limits=ListingLimits(max_metadata_bytes=100_000)
)


def test_a_stream_members_pass_discards_a_listing_the_directory_overfills() -> None:
    """The directory's comments take the listing over the limit after the last
    member; the pass keeps nothing then, as from a file, and the link it made still
    gets its target."""
    data = _overfilled_with_link(b"0.txt")
    with archivey.open_archive(
        _pipe(data), streaming=True, config=_OVER_100K
    ) as reader:
        yielded = [m for m, _ in reader.stream_members()]
        assert reader._listing_discarded
        assert reader._listed == []
    assert (yielded[0].type, yielded[0].link_target) == (MemberType.SYMLINK, "0.txt")


def test_a_damaged_link_target_is_reported_before_the_discard() -> None:
    """The pass reads the link before it drops the listing, so a damaged target is
    reported; the seekable pass reports it only from link finalization, which a
    discarded listing never reaches (``design.md``, the ``max_members`` paragraph)."""
    data = _overfilled_with_link(b"payload bytes " * 100, damaged=True)
    with archivey.open_archive(
        _pipe(data), streaming=True, config=_OVER_100K
    ) as reader:
        for _ in reader.stream_members():
            pass
        assert reader._listing_discarded
        assert _link_target_reasons(reader) == ["target_data_damaged"]


def _commented(count: int, comment_len: int) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i in range(count):
            info = zipfile.ZipInfo(f"{i}.txt")
            info.comment = b"c" * comment_len
            zf.writestr(info, b"x")
    return buf.getvalue()


@pytest.mark.parametrize("source", ["pipe", "file"])
def test_directory_text_is_held_only_up_to_max_metadata_bytes(
    source: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The entries wait for the end record, which says where their offsets count
    from; their text is weighed as they arrive, so the pass holds about
    ``max_metadata_bytes`` of it, not the whole directory. Past it the pass
    discards its listing, as the seekable streaming pass does on the same archive."""
    data = _commented(40, 6000)
    held: list[int] = []
    original = ZipForwardReader._charge_held_directory

    def spy(self: ZipForwardReader, nbytes: int) -> None:
        held.append(nbytes)
        original(self, nbytes)

    monkeypatch.setattr(ZipForwardReader, "_charge_held_directory", spy)
    config = archivey.ArchiveyConfig(
        listing_limits=ListingLimits(max_metadata_bytes=30_000)
    )
    opened = _pipe(data) if source == "pipe" else io.BytesIO(data)
    with archivey.open_archive(opened, streaming=True, config=config) as reader:
        yielded = [m for m, _ in reader.stream_members()]
        assert len(yielded) == 40
        assert reader._listing_discarded
        with pytest.raises(ResourceLimitError, match="max_metadata_bytes"):
            reader.members_report()
    if source == "pipe":
        assert len(held) == 1
        assert 30_000 < held[0] <= 30_000 + 6100
        # The members stay as their local headers made them.
        assert yielded[-1].comment is None
    else:
        assert held == []


def test_directory_entries_past_the_held_text_are_still_matched() -> None:
    data = _drop_local_member(_commented(10, 6000), b"9.txt")
    config = archivey.ArchiveyConfig(
        listing_limits=ListingLimits(max_metadata_bytes=20_000)
    )
    with archivey.open_archive(_pipe(data), streaming=True, config=config) as reader:
        with pytest.raises(CorruptionError, match="entry #9 points to offset"):
            for _ in reader.stream_members():
                pass


def test_a_name_that_differs_between_the_two_headers_is_refused() -> None:
    """As from a file, where reading the member compares the two."""
    data = bytearray(_zip([("a.txt", b"one"), ("b.txt", b"two")]))
    cd = data.index(b"PK\x01\x02")
    at = data.index(b"b.txt", cd)
    data[at : at + 5] = b"c.txt"
    with archivey.open_archive(io.BytesIO(bytes(data))) as reader:
        with pytest.raises(CorruptionError, match="differ"):
            reader.read(reader.get("c.txt"))
    with archivey.open_archive(_pipe(bytes(data)), streaming=True) as reader:
        names = []
        with pytest.raises(
            CorruptionError, match=r"directory 'c\.txt' and header 'b\.txt' differ"
        ):
            for member, _ in reader.stream_members():
                names.append(member.name)
    assert names == ["a.txt", "b.txt"]


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
    # The local entry of b.txt is then one the directory does not list.
    assert [m.name for m in report.members] == ["a.txt"]
    assert isinstance(report.error, CorruptionError)
    assert "no local header" in str(report.error)
    (unlisted,) = [
        d
        for d in report.diagnostics.retained
        if d.code is DiagnosticCode.ARCHIVE_UNLISTED_DATA
    ]
    assert isinstance(unlisted.context, UnlistedDataContext)
    assert unlisted.context.member_name == "b.txt"


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


# -- What the directory finds at the end of the pass ---------------------------------


class _Hand:
    """A ZIP written by hand: local entries in file order, then a directory listing
    whichever of them a test names, in the order it names them."""

    def __init__(self) -> None:
        self.data = bytearray()
        # name -> (offset, flags, crc, compressed size, size), per local entry.
        self.locals: list[tuple[bytes, int, int, int, int, int]] = []

    def local(
        self,
        name: bytes,
        data: bytes,
        *,
        bit3: bool = False,
        crc: int | None = None,
        sizes: tuple[int, int] | None = None,
    ) -> None:
        """A stored local entry. ``crc`` and ``sizes`` replace what its header says
        (and, for a bit-3 entry, what the directory will say); a bit-3 entry gets a
        signed descriptor with its true CRC and size after its data."""
        true_crc = zlib.crc32(data)
        crc = true_crc if crc is None else crc
        csize, size = (len(data), len(data)) if sizes is None else sizes
        flags = 0x08 if bit3 else 0
        offset = len(self.data)
        in_header = (0, 0, 0) if bit3 else (crc, csize, size)
        self.data += struct.pack(
            "<IHHHHHIIIHH", 0x04034B50, 20, flags, 0, 0, 0x21, *in_header, len(name), 0
        )
        self.data += name + data
        if bit3:
            self.data += struct.pack(
                "<IIII", 0x08074B50, true_crc, len(data), len(data)
            )
        self.locals.append((name, offset, flags, crc, csize, size))

    def raw(self, data: bytes) -> None:
        self.data += data

    def directory(self, *order: int) -> bytes:
        """The archive, its directory listing the local entries at ``order``."""
        cd_offset = len(self.data)
        cd = bytearray()
        for i in order:
            name, offset, flags, crc, csize, size = self.locals[i]
            cd += struct.pack(
                "<IHHHHHHIIIHHHHHII",
                0x02014B50, 20, 20, flags, 0, 0, 0x21, crc, csize, size,
                len(name), 0, 0, 0, 0, 0, offset,
            )  # fmt: skip
            cd += name
        count = len(order)
        end = struct.pack(
            "<IHHHHIIH", 0x06054B50, 0, 0, count, count, len(cd), cd_offset, 0
        )
        return bytes(self.data + cd + end)


def _listing(data: bytes, *, pipe: bool) -> archivey.MemberListReport:
    source = _pipe(data) if pipe else io.BytesIO(data)
    with archivey.open_archive(source, streaming=pipe) as reader:
        return reader.members_report()


def _unlisted(report: archivey.MemberListReport) -> list[UnlistedDataContext]:
    out = []
    for d in report.diagnostics.retained:
        if d.code is DiagnosticCode.ARCHIVE_UNLISTED_DATA:
            assert isinstance(d.context, UnlistedDataContext)
            out.append(d.context)
    return out


def test_a_local_entry_the_directory_does_not_list_is_dropped_and_reported() -> None:
    hand = _Hand()
    hand.local(b"a.txt", b"one")
    hand.local(b"planted.txt", b"not listed")
    hand.local(b"b.txt", b"two")
    data = hand.directory(0, 2)
    with archivey.open_archive(_pipe(data), streaming=True) as reader:
        yielded = [m for m, _ in reader.stream_members()]
        report = reader.members_report()
    assert [m.name for m in yielded] == ["a.txt", "planted.txt", "b.txt"]
    assert report.error is None
    assert [m.name for m in report.members] == ["a.txt", "b.txt"]
    assert [m.name for m in report.members] == [
        m.name for m in _listing(data, pipe=False).members
    ]
    planted = yielded[1]
    # It leaves the listing; the report is what says why.
    assert planted.member_state_final
    assert all(m is not planted for m in report.members)
    (context,) = _unlisted(report)
    assert (context.member_name, context.member_id) == ("planted.txt", 1)
    assert (context.offset, context.compressed_size) == (hand.locals[1][1], 10)
    assert context.format == "zip"


def test_an_unlisted_local_entry_is_refused_under_strict() -> None:
    hand = _Hand()
    hand.local(b"a.txt", b"one")
    hand.local(b"planted.txt", b"not listed")
    data = hand.directory(0)
    with archivey.open_archive(
        _pipe(data), streaming=True, config=archivey.ArchiveyConfig(
            diagnostic_policy=DiagnosticPolicy.STRICT)
    ) as reader:  # fmt: skip
        with pytest.raises(DiagnosticRaisedError):
            reader.members_report()


def test_the_later_copy_in_the_directory_is_current() -> None:
    """Two members of one name whose directory order reverses their file order: the
    one the directory lists last is current, as from a file, and the pass raises
    nothing, as ``member_state_final`` said the members could still change."""
    hand = _Hand()
    hand.local(b"dup.txt", b"first")
    hand.local(b"dup.txt", b"second")
    data = hand.directory(1, 0)
    with archivey.open_archive(_pipe(data), streaming=True) as reader:
        during = []
        for member, stream in reader.stream_members():
            assert stream is not None
            during.append((stream.read(), member.is_current))
        report = reader.members_report()
    assert during == [(b"first", True), (b"second", True)]
    assert report.error is None
    # Members stay in file order, with the ids the pass gave them.
    assert [(m.size, m.is_current) for m in report.members] == [(5, True), (6, False)]
    with archivey.open_archive(io.BytesIO(data)) as reader:
        assert reader.open("dup.txt").read() == b"first"


def _planted_prefix_zip() -> tuple[bytes, bytes]:
    """A STORED bit-3 member whose data embeds a valid descriptor for a prefix of
    itself, then a local header: from a pipe the scan ends the member at the planted
    descriptor and reads on into a member the directory does not list. Returns the
    archive and the member's whole data."""
    prefix = b"prefix bytes"
    planted = struct.pack(
        "<IIII", 0x08074B50, zlib.crc32(prefix), len(prefix), len(prefix)
    )
    inner = b"inner data"
    # The inner entry's size runs over the outer member's own descriptor, up to the
    # next local header; its CRC is not checked by a pass that skips it.
    inner_header = struct.pack(
        "<IHHHHHIIIHH", 0x04034B50, 20, 0, 0, 0, 0x21, 0, len(inner) + 16,
        len(inner) + 16, 5, 0,
    ) + b"inner"  # fmt: skip
    whole = prefix + planted + inner_header + inner
    hand = _Hand()
    hand.local(b"outer.bin", whole, bit3=True)
    hand.local(b"after.txt", b"after")
    return hand.directory(0, 1), whole


def test_a_descriptor_the_directory_contradicts_fails_at_the_end_of_the_pass() -> None:
    data, whole = _planted_prefix_zip()
    with archivey.open_archive(io.BytesIO(data)) as reader:
        assert reader.open("outer.bin").read() == whole
    with archivey.open_archive(_pipe(data), streaming=True) as reader:
        names = []
        with pytest.raises(CorruptionError, match="data descriptor") as raised:
            for member, stream in reader.stream_members():
                names.append(member.name)
                if stream is not None and member.name == "outer.bin":
                    assert stream.read() == b"prefix bytes"
        report = reader.members_report()
    assert names == ["outer.bin", "inner", "after.txt"]
    assert raised.value.member_name == "outer.bin"
    assert report.error is raised.value
    # As the directory describes it, and as the seekable read lists it.
    assert [(m.name, m.size) for m in report.members] == [
        ("outer.bin", len(whole)),
        ("after.txt", 5),
    ]
    assert [c.member_name for c in _unlisted(report)] == ["inner"]


def test_an_unlisted_entry_of_a_discarded_listing_is_reported_by_offset() -> None:
    """A directory whose text overfills ``max_metadata_bytes`` leaves the pass no
    member to drop or name, so the report carries the offset alone."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("planted.txt", b"not listed")
        for i in range(20):
            info = zipfile.ZipInfo(f"{i}.txt")
            info.comment = b"c" * 2000
            zf.writestr(info, b"x")
    data = bytearray(buf.getvalue())
    # Remove planted.txt's directory entry, the first, and fix the end record.
    cd = data.index(b"PK\x01\x02")
    name_len, extra_len, comment_len = struct.unpack_from("<HHH", data, cd + 28)
    entry_len = 46 + name_len + extra_len + comment_len
    del data[cd : cd + entry_len]
    eocd = data.rindex(b"PK\x05\x06")
    count, _, size, offset = struct.unpack_from("<HHII", data, eocd + 8)
    struct.pack_into(
        "<HHII", data, eocd + 8, count - 1, count - 1, size - entry_len, offset
    )
    config = archivey.ArchiveyConfig(
        listing_limits=ListingLimits(max_metadata_bytes=20_000)
    )
    with archivey.open_archive(
        _pipe(bytes(data)), streaming=True, config=config
    ) as reader:
        for _ in reader.stream_members():
            pass
        assert reader._listing_discarded
        retained = reader.diagnostics.retained
    (context,) = [
        d.context for d in retained if d.code is DiagnosticCode.ARCHIVE_UNLISTED_DATA
    ]
    assert isinstance(context, UnlistedDataContext)
    assert (context.member_name, context.member_id, context.offset) == ("", None, 0)
    assert context.compressed_size is None


@pytest.mark.parametrize(
    "limit",
    [
        pytest.param(100_000, id="members-overfill"),
        pytest.param(20_000, id="held-text-overfills"),
    ],
)
def test_a_discarded_listing_stamps_the_current_copy_by_the_directory(
    limit: int,
) -> None:
    """When the directory pushes the listing over the limits, the discard stamps
    ``is_current`` too, and in the directory's order as well: whether the members
    the directory updated overfill it, or the directory's text overfills it while
    it is held, before any member is updated."""
    buf = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)  # zipfile: duplicate name
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("dup.txt", b"first")
            zf.writestr("dup.txt", b"second")
            # Long names take the updated members over 100 000 bytes; short ones
            # keep the walk under 20 000, which the held comments then pass.
            stem = "n" * 1900 if limit == 100_000 else "n"
            for i in range(20):
                info = zipfile.ZipInfo(f"{i}-{stem}.txt")
                info.comment = b"c" * 2000
                zf.writestr(info, b"x")
    data = bytearray(buf.getvalue())
    # Swap the two dup.txt directory entries, which are the first two and the same
    # length, so the directory lists the second copy first.
    first = data.index(b"PK\x01\x02")
    length = 46 + len("dup.txt")
    one, two = data[first : first + length], data[first + length : first + 2 * length]
    data[first : first + 2 * length] = two + one
    config = archivey.ArchiveyConfig(
        listing_limits=ListingLimits(max_metadata_bytes=limit)
    )
    with archivey.open_archive(
        _pipe(bytes(data)), streaming=True, config=config
    ) as reader:
        yielded = [m for m, _ in reader.stream_members()]
        assert reader._listing_discarded
    assert [(m.size, m.is_current) for m in yielded[:2]] == [(5, True), (6, False)]
