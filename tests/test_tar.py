"""TAR backend tests — random-access read, forward-only streaming on non-seekable
sources, PAX/GNU/ustar member mapping, cost, corrupt/truncated handling, and
end-of-archive verification."""

from __future__ import annotations

import io
import logging
import tarfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO
from unittest import mock

import pytest

from archivey import (
    ArchiveFormat,
    ArchiveyConfig,
    ArchiveyUsageError,
    CompressionAlgorithm,
    CompressionMethod,
    ExtractionLimits,
    ListingLimits,
    MemberType,
    open_archive,
)
from archivey.cost import AccessCost, ListingCost, StreamCapability
from archivey.diagnostics import (
    ArchiveEofContext,
    DiagnosticCode,
    DiagnosticDisposition,
    DiagnosticPolicy,
)
from archivey.exceptions import (
    DiagnosticRaisedError,
    ReadError,
    ResourceLimitError,
    StreamNotSeekableError,
    TruncatedError,
)
from archivey.internal.backends import tar_reader as tar_reader_module
from archivey.internal.streams.streamtools import DEFAULT_UNKNOWN_LENGTH_READ_STEP
from tests.conftest import requires_zstd, zstd_backend
from tests.corruption_util import (
    is_corruption_not_truncation,
    raises_corruption_not_truncation,
)
from tests.extract_util import open_and_extract
from tests.streams_util import (
    FactSizedReadRecorder,
    NonSeekableBytesIO,
    ReadSizeRecorder,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _build_tar(mode: str = "w") -> bytes:
    """A small tar with a file, a nested file, a directory, and a symlink."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode=mode) as t:
        for name, data, mtime in [
            ("hello.txt", b"hello world", 1_600_000_000),
            ("dir/nested.txt", b"nested content", 1_600_000_100),
        ]:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            info.mtime = mtime
            info.uid, info.gid = 1000, 1000
            info.uname, info.gname = "alice", "staff"
            t.addfile(info, io.BytesIO(data))
        d = tarfile.TarInfo("dir")
        d.type = tarfile.DIRTYPE
        d.mode = 0o755
        t.addfile(d)
        link = tarfile.TarInfo("link.txt")
        link.type = tarfile.SYMTYPE
        link.linkname = "hello.txt"
        t.addfile(link)
    return buf.getvalue()


def _tar_missing_eof_block() -> bytes:
    """Valid member data but only one of the two required EOF null blocks."""
    full = _build_tar()
    with tarfile.open(fileobj=io.BytesIO(full), mode="r:") as t:
        members = t.getmembers()
        last = members[-1]
        blocks = (last.size + 511) & ~511
        eof_start = last.offset_data + blocks
    return full[: eof_start + 512]


def _tar_three() -> bytes:
    """A plain tar of three members (the middle one spanning several blocks)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:") as t:
        for name, data in [
            ("a.txt", b"aaa"),
            ("b.txt", b"b" * 4000),
            ("c.txt", b"ccc"),
        ]:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _tar_corrupt_final_header() -> bytes:
    """Member data followed by a single non-null block where the end-of-archive marker
    (and the next header) should be, with nothing after it — the shape stdlib tarfile
    treats as a clean end. Only the block tarfile stopped on reveals the corruption; the
    trailing-block check reads past it into EOF."""
    full = _build_tar()
    with tarfile.open(fileobj=io.BytesIO(full), mode="r:") as t:
        last = t.getmembers()[-1]
        eof_start = last.offset_data + ((last.size + 511) & ~511)
    return full[:eof_start] + b"\xff" * 512


def _tar_content_end(data: bytes) -> int:
    """Byte offset of the first end-of-archive null block (strip record padding)."""
    end = len(data)
    while end >= 512 and data[end - 512 : end] == b"\x00" * 512:
        end -= 512
    return end


def _tar_sparse_gnu(logical: int = 1024 * 1024) -> bytes:
    """A hand-built old-GNU-format sparse tar whose logical size ≫ packed size.

    Constructed in pure Python (no system ``tar``, so it runs identically on every OS —
    BSD/Windows ``tar`` reject ``--sparse``). One 3-byte sparse region carried by a
    ``logical``-byte file (1 MiB by default): the physical next-header offset
    (``offset_data + roundup(3)``) is far below ``offset_data + roundup(logical size)``,
    which is exactly the layout that used to false-negative the RA EOF probe. Verified
    read back through stdlib ``tarfile``.
    """
    physical = b"xyz"

    def octal(value: int, width: int) -> bytes:
        return f"{value:0{width - 1}o}".encode() + b"\x00"

    h = bytearray(512)
    h[0:10] = b"sparse.bin"
    h[100:108] = octal(0o644, 8)
    h[108:116] = octal(0, 8)  # uid
    h[116:124] = octal(0, 8)  # gid
    h[124:136] = octal(len(physical), 12)  # PHYSICAL size (logical goes in realsize)
    h[136:148] = octal(0, 12)  # mtime
    h[156:157] = b"S"  # GNUTYPE_SPARSE
    h[257:265] = b"ustar  \x00"  # GNU magic + version
    h[386:398] = octal(0, 12)  # sparse region: logical offset
    h[398:410] = octal(len(physical), 12)  # sparse region: numbytes
    h[482] = 0  # isextended
    h[483:495] = octal(logical, 12)  # realsize (logical)
    h[148:156] = b" " * 8
    h[148:156] = f"{sum(h):06o}".encode() + b"\x00 "  # checksum
    full = bytes(h) + physical.ljust(512, b"\x00") + b"\x00" * 1024

    # Guard the fixture's premise: a real sparse member whose logical/physical ends diverge.
    with tarfile.open(fileobj=io.BytesIO(full), mode="r:") as t:
        member = t.getmembers()[0]
        assert member.type == tarfile.GNUTYPE_SPARSE
        logical_end = member.offset_data + ((member.size + 511) & ~511)
        assert _tar_content_end(full) < logical_end
    return full


def _tar_corrupt_final_header_sparse() -> bytes:
    """Sparse member + rejected final header (nothing after) — the probe false-negative."""
    full = _tar_sparse_gnu()
    eof_start = _tar_content_end(full)
    return full[:eof_start] + b"\xff" * 512


def _tar_corrupt_mid_header() -> bytes:
    """Corrupt the second member's header so tarfile stops iterating after the first,
    with valid member data still following the bad block."""
    full = _tar_three()
    with tarfile.open(fileobj=io.BytesIO(full), mode="r:") as t:
        second = t.getmembers()[1]
    start = second.offset  # header-block offset of the second member
    return full[:start] + b"\xff" * 512 + full[start + 512 :]


def _tar_minimal_eof() -> bytes:
    """A fully valid archive terminated by exactly the two required EOF null blocks.

    ``tarfile`` pads its own output to a 10240-byte record boundary, so a tar it wrote
    always has plenty of trailing zeros. This helper strips that padding down to the
    bare POSIX minimum (two 512-byte null blocks, no record padding) — what e.g.
    ``tar -b1`` or a streaming producer emits — to exercise the EOF check's boundary.
    """
    full = _build_tar()
    with tarfile.open(fileobj=io.BytesIO(full), mode="r:") as t:
        members = t.getmembers()
        last = members[-1]
        blocks = (last.size + 511) & ~511
        eof_start = last.offset_data + blocks
    return full[: eof_start + 512 * 2]


@pytest.fixture
def plain_tar(tmp_path: Path) -> Path:
    path = tmp_path / "simple.tar"
    path.write_bytes(_build_tar())
    return path


# ---------------------------------------------------------------------------
# Cost / format properties (access-mode-and-cost for TAR)
# ---------------------------------------------------------------------------


def test_plain_tar_cost(plain_tar: Path) -> None:
    with open_archive(plain_tar) as ar:
        assert ar.format == ArchiveFormat.TAR
        assert ar.cost.listing_cost == ListingCost.REQUIRES_SCANNING
        assert ar.cost.access_cost == AccessCost.DIRECT
        assert ar.cost.stream_capability == StreamCapability.SEEKABLE
        assert ar.info.is_solid is False
        assert ar.info.member_count is None  # no central directory: count needs a scan


@pytest.mark.parametrize(
    "mode,fmt",
    [
        ("w:gz", ArchiveFormat.TAR_GZ),
        ("w:bz2", ArchiveFormat.TAR_BZ2),
        ("w:xz", ArchiveFormat.TAR_XZ),
    ],
)
def test_compressed_tar_cost_and_read(
    mode: str, fmt: ArchiveFormat, tmp_path: Path
) -> None:
    path = tmp_path / f"a.{mode.split(':')[1]}"
    path.write_bytes(_build_tar(mode))
    with open_archive(path) as ar:
        assert ar.format == fmt
        assert ar.cost.listing_cost == ListingCost.REQUIRES_DECOMPRESSION
        assert ar.cost.access_cost == AccessCost.SOLID
        assert ar.info.is_solid is True
        assert ar.read("hello.txt") == b"hello world"
        assert ar.read("dir/nested.txt") == b"nested content"


# ---------------------------------------------------------------------------
# Random-access read + listing
# ---------------------------------------------------------------------------


def test_members_listed(plain_tar: Path) -> None:
    with open_archive(plain_tar) as ar:
        names = {m.name for m in ar.members()}
        assert names == {"hello.txt", "dir/nested.txt", "dir/", "link.txt"}


def test_random_access_read_by_name(plain_tar: Path) -> None:
    with open_archive(plain_tar) as ar:
        assert ar.read("dir/nested.txt") == b"nested content"
        assert ar.read("hello.txt") == b"hello world"  # out of order still works
        assert ar.read("hello.txt") == b"hello world"  # re-read


def test_member_list_not_available_without_scan(plain_tar: Path) -> None:
    # TAR has no upfront index: members_report_if_available is None until a scan materializes it.
    with open_archive(plain_tar) as ar:
        assert ar.members_report_if_available() is None
        ar.members()  # forces the scan
        assert ar.members_report_if_available() is not None


def test_stream_members(plain_tar: Path) -> None:
    with open_archive(plain_tar) as ar:
        collected = {}
        for member, stream in ar.stream_members():
            collected[member.name] = stream.read() if stream is not None else None
        assert collected["hello.txt"] == b"hello world"
        assert collected["dir/"] is None  # directory has no data stream


# ---------------------------------------------------------------------------
# Member metadata mapping (ustar / GNU / PAX)
# ---------------------------------------------------------------------------


def test_member_metadata(plain_tar: Path) -> None:
    with open_archive(plain_tar) as ar:
        by_name = {m.name: m for m in ar.members()}
        f = by_name["hello.txt"]
        assert f.type == MemberType.FILE
        assert f.size == len(b"hello world")
        assert f.mode == 0o644
        assert f.uid == 1000 and f.gid == 1000
        assert f.uname == "alice" and f.gname == "staff"
        assert f.modified == datetime.fromtimestamp(1_600_000_000, tz=UTC)
        assert f.modified.tzinfo is not None  # tz-aware UTC
        # tar stores members uncompressed; no encryption.
        assert f.compression == (CompressionMethod(algo=CompressionAlgorithm.STORED),)
        assert f.is_encrypted is False


def test_directory_and_symlink_types(plain_tar: Path) -> None:
    with open_archive(plain_tar) as ar:
        by_name = {m.name: m for m in ar.members()}
        assert by_name["dir/"].type == MemberType.DIRECTORY
        link = by_name["link.txt"]
        assert link.type == MemberType.SYMLINK
        assert link.link_target == "hello.txt"


def test_symlink_followed_on_read(plain_tar: Path) -> None:
    with open_archive(plain_tar) as ar:
        # Opening the symlink follows it to its target's data.
        assert ar.read("link.txt") == b"hello world"


def test_hardlink_type_mapping(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        data = b"original"
        info = tarfile.TarInfo("orig.txt")
        info.size = len(data)
        t.addfile(info, io.BytesIO(data))
        hl = tarfile.TarInfo("hard.txt")
        hl.type = tarfile.LNKTYPE
        hl.linkname = "orig.txt"
        t.addfile(hl)
    path = tmp_path / "hl.tar"
    path.write_bytes(buf.getvalue())
    with open_archive(path) as ar:
        hard = {m.name: m for m in ar.members()}["hard.txt"]
        assert hard.type == MemberType.HARDLINK
        assert hard.link_target == "orig.txt"
        assert ar.read("hard.txt") == b"original"  # follows to the linked data


def test_pax_mtime_override(tmp_path: Path) -> None:
    # A PAX header carries a sub-second mtime; tarfile folds it into TarInfo.mtime, which
    # the backend surfaces (overriding the whole-second ustar field).
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as t:
        info = tarfile.TarInfo("p.txt")
        data = b"x"
        info.size = len(data)
        info.pax_headers["mtime"] = "1600000000.123456"
        t.addfile(info, io.BytesIO(data))
    path = tmp_path / "pax.tar"
    path.write_bytes(buf.getvalue())
    with open_archive(path) as ar:
        m = ar.get("p.txt")
        assert m.modified is not None
        assert abs(m.modified.timestamp() - 1_600_000_000.123456) < 1e-3


def test_raw_name_preserved(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as t:
        info = tarfile.TarInfo("café.txt")
        info.size = 1
        t.addfile(info, io.BytesIO(b"x"))
    path = tmp_path / "u.tar"
    path.write_bytes(buf.getvalue())
    with open_archive(path) as ar:
        m = ar.get("café.txt")
        assert m.name == "café.txt"  # decoded name round-trips
        assert m.raw_name == b"caf\xc3\xa9.txt"  # verbatim stored bytes


def test_pax_atime_ctime(tmp_path: Path) -> None:
    # PAX access/inode-change times live only in pax_headers (tarfile does not fold them
    # into TarInfo like mtime). atime is `accessed`; ctime is st_ctime, never a birth
    # time, so it goes to `ctime` and `created` stays None.
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as t:
        info = tarfile.TarInfo("p.txt")
        info.size = 1
        info.pax_headers["atime"] = "1600000100.5"
        info.pax_headers["ctime"] = "1600000200.25"
        t.addfile(info, io.BytesIO(b"x"))
    path = tmp_path / "pax_times.tar"
    path.write_bytes(buf.getvalue())
    with open_archive(path) as ar:
        m = ar.get("p.txt")
        assert m.accessed is not None
        assert abs(m.accessed.timestamp() - 1_600_000_100.5) < 1e-3
        assert m.created is None
        assert abs(m.ctime.timestamp() - 1_600_000_200.25) < 1e-3


def test_pax_libarchive_creationtime_is_created(tmp_path: Path) -> None:
    # bsdtar stores the source's birth time as LIBARCHIVE.creationtime where the OS
    # has one; it sits beside the PAX ctime, so a member can carry both.
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as t:
        info = tarfile.TarInfo("p.txt")
        info.size = 1
        info.pax_headers["ctime"] = "1600000200.25"
        info.pax_headers["LIBARCHIVE.creationtime"] = "1500000000.5"
        t.addfile(info, io.BytesIO(b"x"))
    path = tmp_path / "pax_birth.tar"
    path.write_bytes(buf.getvalue())
    with open_archive(path) as ar:
        m = ar.get("p.txt")
        assert m.created is not None
        assert abs(m.created.timestamp() - 1_500_000_000.5) < 1e-3
        assert m.ctime is not None
        assert abs(m.ctime.timestamp() - 1_600_000_200.25) < 1e-3


# ---------------------------------------------------------------------------
# Compressed-tar combinations beyond gz/bz2/xz (codec-layer composition)
# ---------------------------------------------------------------------------


@requires_zstd()
def test_tar_zst_via_codec_layer() -> None:
    zstd = zstd_backend()
    plain = _build_tar()
    data = zstd.compress(plain)
    with open_archive(io.BytesIO(data)) as ar:
        assert ar.format == ArchiveFormat.TAR_ZST
        assert ar.read("hello.txt") == b"hello world"


# ---------------------------------------------------------------------------
# Non-seekable source: random-access fails fast; streaming succeeds
# ---------------------------------------------------------------------------


def test_non_seekable_tar_fails_fast() -> None:
    with pytest.raises(StreamNotSeekableError):
        open_archive(NonSeekableBytesIO(_build_tar()), format=ArchiveFormat.TAR)


def test_non_seekable_tar_fails_fast_via_detection() -> None:
    with pytest.raises(StreamNotSeekableError):
        open_archive(NonSeekableBytesIO(_build_tar()))


def test_non_seekable_tar_streaming_opens_without_scanning() -> None:
    with open_archive(
        NonSeekableBytesIO(_build_tar()), format=ArchiveFormat.TAR, streaming=True
    ) as ar:
        assert ar.cost.stream_capability == StreamCapability.FORWARD_ONLY


def test_non_seekable_plain_tar_stream_members() -> None:
    with open_archive(
        NonSeekableBytesIO(_build_tar()), format=ArchiveFormat.TAR, streaming=True
    ) as ar:
        collected = {}
        for member, stream in ar.stream_members():
            collected[member.name] = stream.read() if stream is not None else None
        assert collected["hello.txt"] == b"hello world"
        assert collected["dir/nested.txt"] == b"nested content"
        assert collected["dir/"] is None


def test_non_seekable_plain_tar_iter() -> None:
    with open_archive(
        NonSeekableBytesIO(_build_tar()), format=ArchiveFormat.TAR, streaming=True
    ) as ar:
        names = [m.name for m in ar]
        assert "hello.txt" in names
        assert "dir/nested.txt" in names


def test_streaming_tar_disables_random_access(plain_tar: Path) -> None:
    with open_archive(plain_tar, streaming=True) as ar:
        with pytest.raises(ArchiveyUsageError):
            ar.members()
        with pytest.raises(ArchiveyUsageError):
            ar.read("hello.txt")


def test_streaming_tar_does_not_call_getmembers() -> None:
    data = _build_tar()
    with open_archive(
        NonSeekableBytesIO(data), format=ArchiveFormat.TAR, streaming=True
    ) as ar:
        with mock.patch.object(
            tarfile.TarFile,
            "getmembers",
            side_effect=AssertionError("getmembers called"),
        ):
            list(ar.stream_members())


def test_non_seekable_tar_gz_streaming(tmp_path: Path) -> None:
    path = tmp_path / "a.tar.gz"
    path.write_bytes(_build_tar("w:gz"))
    data = path.read_bytes()
    with open_archive(NonSeekableBytesIO(data), streaming=True) as ar:
        assert ar.format == ArchiveFormat.TAR_GZ
        assert ar.cost.stream_capability == StreamCapability.FORWARD_ONLY
        assert ar.cost.listing_cost == ListingCost.REQUIRES_DECOMPRESSION
        assert ar.cost.access_cost == AccessCost.SOLID
        collected: dict[str, bytes | None] = {}
        for member, stream in ar.stream_members():
            collected[member.name] = stream.read() if stream is not None else None
        assert set(collected) == {"hello.txt", "dir/nested.txt", "dir/", "link.txt"}
        assert collected["hello.txt"] == b"hello world"
        assert collected["dir/nested.txt"] == b"nested content"
        assert collected["dir/"] is None


def test_non_seekable_tar_bz2_streaming_smoke() -> None:
    data = _build_tar("w:bz2")
    with open_archive(
        NonSeekableBytesIO(data), format=ArchiveFormat.TAR_BZ2, streaming=True
    ) as ar:
        names = [m.name for m, _ in ar.stream_members()]
        assert "hello.txt" in names


def test_compressed_source_size_on_path(tmp_path: Path) -> None:
    path = tmp_path / "a.tar.gz"
    path.write_bytes(_build_tar("w:gz"))
    with open_archive(path) as ar:
        assert ar.compressed_source_size == path.stat().st_size


def test_compressed_source_size_generalized(plain_tar: Path) -> None:
    # Generalized (for the archive-wide extraction ratio guard): known for any path
    # source — a plain tar simply yields a harmless ~1:1 ratio — and for seekable
    # streams via a SEEK_END probe; only a non-seekable stream is unknowable.
    with open_archive(plain_tar) as ar:
        assert ar.compressed_source_size == plain_tar.stat().st_size
    data = _build_tar("w:gz")
    with open_archive(io.BytesIO(data), format=ArchiveFormat.TAR_GZ) as ar:
        assert ar.compressed_source_size == len(data)
    with open_archive(NonSeekableBytesIO(data), streaming=True) as ar:
        assert ar.compressed_source_size is None


# ---------------------------------------------------------------------------
# end-of-archive truncation detection
# ---------------------------------------------------------------------------

# A missing trailer is an ordinary diagnostic; a caller who wants it fatal sets the code
# to RAISE and gets DiagnosticRaisedError.
_RAISE_ON_MISSING_EOF = ArchiveyConfig(
    diagnostic_policy=DiagnosticPolicy(
        overrides={
            DiagnosticCode.ARCHIVE_EOF_MARKER_MISSING: DiagnosticDisposition.RAISE
        }
    )
)


def _eof_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    """EOF-check warnings only — filtered to the backends logger so the unrelated
    ``archivey.normalization`` warning from the ``dir`` -> ``dir/`` fixture entry
    doesn't leak into the assertion."""
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == "archivey.backends" and r.levelno == logging.WARNING
    ]


def test_valid_tar_eof_silent(
    plain_tar: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="archivey.backends"):
        with open_archive(plain_tar) as ar:
            ar.members()
    assert _eof_warnings(caplog) == []


def test_missing_eof_blocks_warns_by_default(
    caplog: pytest.LogCaptureFixture,
) -> None:
    data = _tar_missing_eof_block()
    with caplog.at_level(logging.WARNING, logger="archivey.backends"):
        with open_archive(io.BytesIO(data), format=ArchiveFormat.TAR) as ar:
            ar.members()
    warnings = _eof_warnings(caplog)
    assert len(warnings) == 1
    assert "truncated" in warnings[0].lower()


def test_missing_eof_blocks_raise_disposition_raises() -> None:
    data = _tar_missing_eof_block()
    with pytest.raises(DiagnosticRaisedError):
        with open_archive(
            io.BytesIO(data),
            format=ArchiveFormat.TAR,
            config=_RAISE_ON_MISSING_EOF,
        ) as ar:
            ar.members()


def test_members_report_recovers_prefix_on_corrupt_header() -> None:
    """Q7: members_report returns prefix + error; members() raises; iter yields then raises."""
    data = _tar_corrupt_mid_header()
    with open_archive(io.BytesIO(data), format=ArchiveFormat.TAR) as ar:
        report = ar.members_report()
        assert report.error is not None
        assert is_corruption_not_truncation(report.error)
        names = [m.name for m in report.members]
        assert names == ["a.txt"]
        assert ar.members_report_if_available() is report
        with raises_corruption_not_truncation():
            ar.members()
        yielded: list[str] = []
        with raises_corruption_not_truncation():
            for member in ar:
                yielded.append(member.name)
        assert yielded == names
        first = report.members[0]
        assert first in ar
        assert ar.open(first).read() == b"aaa"


def test_members_report_keeps_the_prefix_when_the_walk_raises_mid_batch() -> None:
    # The random-access walk parses headers in batches. A header whose data runs past
    # the end of the file raises while a batch is being filled; the members parsed
    # before it in that batch must still reach the report, not vanish with the batch.
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as t:
        for name, payload in (
            ("a.txt", b"aaa"),
            ("b.txt", b"bbb"),
            ("c.bin", bytes(4096)),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            t.addfile(info, io.BytesIO(payload))
    # Keep a.txt and b.txt whole, c.bin's header, and 100 bytes of its data.
    data = buf.getvalue()[: 4 * tarfile.BLOCKSIZE + tarfile.BLOCKSIZE + 100]
    with open_archive(io.BytesIO(data), format=ArchiveFormat.TAR) as ar:
        report = ar.members_report()
        assert isinstance(report.error, TruncatedError)
        assert [m.name for m in report.members][:2] == ["a.txt", "b.txt"]


def test_members_report_streaming_corrupt_header_yield_then_raise() -> None:
    data = _tar_corrupt_mid_header()
    with open_archive(
        NonSeekableBytesIO(data),
        format=ArchiveFormat.TAR,
        streaming=True,
    ) as ar:
        yielded: list[str] = []
        with raises_corruption_not_truncation():
            for member, _stream in ar.stream_members():
                yielded.append(member.name)
        assert yielded == ["a.txt"]
        report = ar.members_report()
        assert is_corruption_not_truncation(report.error)
        assert [m.name for m in report.members] == yielded


def test_members_report_raises_a_raised_eof_diagnostic() -> None:
    # A missing trailer set to RAISE is the caller's policy firing, not listing damage,
    # so members_report() raises it rather than folding it into report.error. This was
    # TruncatedError-as-report under the removed ``strict_archive_eof`` flag.
    data = _tar_missing_eof_block()
    with open_archive(
        io.BytesIO(data), format=ArchiveFormat.TAR, config=_RAISE_ON_MISSING_EOF
    ) as ar:
        with pytest.raises(DiagnosticRaisedError):
            ar.members_report()


def test_missing_eof_blocks_streaming_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    data = _tar_missing_eof_block()
    with caplog.at_level(logging.WARNING, logger="archivey.backends"):
        with open_archive(
            NonSeekableBytesIO(data), format=ArchiveFormat.TAR, streaming=True
        ) as ar:
            list(ar.stream_members())
    assert len(_eof_warnings(caplog)) == 1


def test_missing_eof_blocks_streaming_raise_disposition_raises() -> None:
    data = _tar_missing_eof_block()
    with pytest.raises(DiagnosticRaisedError):
        with open_archive(
            NonSeekableBytesIO(data),
            format=ArchiveFormat.TAR,
            streaming=True,
            config=_RAISE_ON_MISSING_EOF,
        ) as ar:
            list(ar.stream_members())


def test_minimal_eof_trailer_silent(caplog: pytest.LogCaptureFixture) -> None:
    # A valid archive whose trailer is exactly the two required null blocks (no record
    # padding) must not be flagged: tarfile consumes the first block detecting EOF, so the
    # check must only require the second block, not two more. Random-access path.
    data = _tar_minimal_eof()
    with caplog.at_level(logging.WARNING, logger="archivey.backends"):
        with open_archive(io.BytesIO(data), format=ArchiveFormat.TAR) as ar:
            ar.members()
    assert _eof_warnings(caplog) == []


def test_minimal_eof_trailer_streaming_silent(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Same minimal-but-valid trailer over the forward-only streaming path.
    data = _tar_minimal_eof()
    with caplog.at_level(logging.WARNING, logger="archivey.backends"):
        with open_archive(
            NonSeekableBytesIO(data), format=ArchiveFormat.TAR, streaming=True
        ) as ar:
            list(ar.stream_members())
    assert _eof_warnings(caplog) == []


def test_minimal_eof_trailer_strict_does_not_raise() -> None:
    # DiagnosticPolicy.strict() must accept the minimal valid trailer on both access
    # modes: it raises on both EOF codes, and neither is emitted here.
    strict = ArchiveyConfig(diagnostic_policy=DiagnosticPolicy.strict())
    data = _tar_minimal_eof()
    with open_archive(
        io.BytesIO(data),
        format=ArchiveFormat.TAR,
        config=strict,
    ) as ar:
        assert [m.name for m in ar.members()]
    with open_archive(
        NonSeekableBytesIO(data),
        format=ArchiveFormat.TAR,
        streaming=True,
        config=strict,
    ) as ar:
        assert [m for m, _ in ar.stream_members()]


def test_corrupt_final_header_raises_corruption_by_default() -> None:
    # A rejected header in the archive's final block: tarfile treats it as a clean end,
    # and the trailing-block check reads past it. The random-access EOF probe inspects
    # the block tarfile stopped on and raises CorruptionError — even under the default
    # (non-strict) config, because a non-null block there is unambiguous corruption.
    data = _tar_corrupt_final_header()
    with raises_corruption_not_truncation():
        with open_archive(io.BytesIO(data), format=ArchiveFormat.TAR) as ar:
            ar.members()


def test_corrupt_mid_header_raises_corruption_by_default() -> None:
    # A rejected non-first header with valid data still following: caught by default in
    # both access modes.
    data = _tar_corrupt_mid_header()
    with raises_corruption_not_truncation():
        with open_archive(io.BytesIO(data), format=ArchiveFormat.TAR) as ar:
            ar.members()


def test_corrupt_mid_header_streaming_raises_corruption() -> None:
    # Streaming has no probe, but a rejected mid-archive header leaves valid bytes after
    # the stop, so the trailing-block heuristic still surfaces it as corruption.
    data = _tar_corrupt_mid_header()
    with raises_corruption_not_truncation():
        with open_archive(
            NonSeekableBytesIO(data), format=ArchiveFormat.TAR, streaming=True
        ) as ar:
            list(ar.stream_members())


def test_corrupt_final_header_streaming_warns_not_corruption(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Documented streaming limitation: with nothing after the rejected final block, the
    # forward-only path cannot recover it and surfaces a missing-trailer warning instead
    # of CorruptionError. Random access catches this case (test above); native TAR would
    # close the streaming gap.
    data = _tar_corrupt_final_header()
    with caplog.at_level(logging.WARNING, logger="archivey.backends"):
        with open_archive(
            NonSeekableBytesIO(data), format=ArchiveFormat.TAR, streaming=True
        ) as ar:
            list(ar.stream_members())
    assert len(_eof_warnings(caplog)) == 1


def test_corrupt_final_header_extract_raises(tmp_path: Path) -> None:
    # Extraction surfaces the corruption too. Random access extracts in one pass, as
    # streaming does, so the EOF check runs after the members before the damage are
    # written: those stay on disk, whole, and the call still raises.
    data = _tar_corrupt_final_header()
    dest = tmp_path / "out"
    with raises_corruption_not_truncation():
        with open_archive(io.BytesIO(data), format=ArchiveFormat.TAR) as ar:
            ar.extract_all(dest)
    assert (dest / "hello.txt").read_bytes() == b"hello world"


def test_corrupt_final_header_sparse_raises_corruption() -> None:
    # Regression: logical size ≫ packed size used to make the probe's
    # offset_data+roundup(size) check miss the stop block, so a rejected final header
    # after a GNU sparse member warned as absent instead of raising CorruptionError.
    data = _tar_corrupt_final_header_sparse()
    with raises_corruption_not_truncation():
        with open_archive(io.BytesIO(data), format=ArchiveFormat.TAR) as ar:
            ar.members()


def test_sparse_tar_eof_no_false_positive(caplog: pytest.LogCaptureFixture) -> None:
    # A well-formed GNU sparse tar with a valid trailer must stay silent.
    data = _tar_sparse_gnu()
    with caplog.at_level(logging.WARNING, logger="archivey.backends"):
        with open_archive(io.BytesIO(data), format=ArchiveFormat.TAR) as ar:
            members = ar.members()
    assert members
    assert members[0].is_sparse
    assert _eof_warnings(caplog) == []


def test_sparse_member_extracts_dense_and_counts_toward_ratio(tmp_path: Path) -> None:
    # docs/formats.md promises both halves: the holes are written as zeros, and those
    # zeros count as output for the archive-wide ratio limit. 10 MiB of logical data in
    # a 2 KiB tar is far past the default max_ratio of 1000, and past the 5 MiB
    # activation threshold, so the default limits must refuse it.
    logical = 10 * 1024 * 1024
    archive = tmp_path / "sparse.tar"
    archive.write_bytes(_tar_sparse_gnu(logical))

    with pytest.raises(ResourceLimitError):
        open_and_extract(archive, tmp_path / "default")

    out = tmp_path / "unlimited"
    open_and_extract(archive, out, limits=ExtractionLimits(max_ratio=None))
    written = out / "sparse.bin"
    assert written.stat().st_size == logical
    with written.open("rb") as f:
        assert f.read(3) == b"xyz"
        assert f.read(1024 * 1024).count(0) == 1024 * 1024


def _tar_sparse_pax_1_0() -> bytes:
    """A PAX 1.0 sparse member, the encoding GNU tar writes under ``--format=pax``.

    Unlike the old GNU form its typeflag is a plain ``0``: only the ``GNU.sparse.*``
    records and the map at the head of the data say it is sparse. One 5-byte region at
    offset 100 of a 1 000-byte file.
    """
    sparse_map = b"1\n100\n5\n".ljust(512, b"\0")
    stored = sparse_map + b"hello"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as t:
        info = tarfile.TarInfo("GNUSparseFile.0/holes.bin")
        info.size = len(stored)
        info.pax_headers = {
            "GNU.sparse.major": "1",
            "GNU.sparse.minor": "0",
            "GNU.sparse.name": "holes.bin",
            "GNU.sparse.realsize": "1000",
        }
        t.addfile(info, io.BytesIO(stored))
    return buf.getvalue()


def _pax_record(key: str, value: str) -> bytes:
    """One PAX ``length key=value\n`` record, where the length counts itself."""
    body = f" {key}={value}\n".encode()
    length = len(body) + 1
    while len(str(length)) + len(body) != length:
        length += 1
    return str(length).encode() + body


def _tar_sparse_pax_0_x(minor: int) -> bytes:
    """A PAX 0.0 or 0.1 sparse member: the same one region as the 1.0 fixture.

    Neither can be written through ``tarfile``'s ``pax_headers`` dict. 0.0 repeats the
    ``GNU.sparse.offset`` / ``GNU.sparse.numbytes`` keys once per region, which a dict
    cannot hold, and tarfile reads them by scanning the raw extended header; 0.1 puts
    the whole map in one ``GNU.sparse.map`` record. So the extended header is built by
    hand here. In both the stored data is the packed regions only, with no map in it.
    """
    records = [_pax_record("GNU.sparse.size", "1000")]
    if minor == 0:
        records += [
            _pax_record("GNU.sparse.numblocks", "1"),
            _pax_record("GNU.sparse.offset", "100"),
            _pax_record("GNU.sparse.numbytes", "5"),
        ]
    else:
        records += [
            _pax_record("GNU.sparse.numblocks", "1"),
            _pax_record("GNU.sparse.map", "100,5"),
        ]
    payload = b"".join(records)

    def block(data: bytes) -> bytes:
        return data + bytes(-len(data) % tarfile.BLOCKSIZE)

    xhdr = tarfile.TarInfo("./PaxHeaders/holes.bin")
    xhdr.type = tarfile.XHDTYPE
    xhdr.size = len(payload)
    member = tarfile.TarInfo("holes.bin")
    member.size = 5
    return b"".join(
        [
            xhdr.tobuf(format=tarfile.USTAR_FORMAT),
            block(payload),
            member.tobuf(format=tarfile.USTAR_FORMAT),
            block(b"hello"),
            bytes(2 * tarfile.BLOCKSIZE),
        ]
    )


@pytest.mark.parametrize(
    "build",
    [
        lambda: _tar_sparse_pax_0_x(0),
        lambda: _tar_sparse_pax_0_x(1),
        _tar_sparse_pax_1_0,
    ],
    ids=["pax-0.0", "pax-0.1", "pax-1.0"],
)
def test_pax_sparse_member_is_reported_sparse(build: Any) -> None:
    # tarfile dispatches the three PAX sparse encodings to three different parsers,
    # and none of them sets the old GNU ``S`` typeflag, so each gets its own fixture.
    with open_archive(io.BytesIO(build()), format=ArchiveFormat.TAR) as ar:
        (member,) = ar.members()
        assert member.name == "holes.bin"
        assert member.extra["tar.type"] == tarfile.REGTYPE
        assert member.is_sparse
        assert member.size == 1000
        assert ar.read(member) == bytes(100) + b"hello" + bytes(895)


def test_corrupt_final_header_gzip_raises_corruption(tmp_path: Path) -> None:
    # Compressed path also carries the EOF probe (no re-decompression / backward seek).
    import gzip

    path = tmp_path / "bad.tar.gz"
    path.write_bytes(gzip.compress(_tar_corrupt_final_header()))
    with raises_corruption_not_truncation():
        with open_archive(path) as ar:
            ar.members()


def test_corrupt_mid_header_raise_disposition_still_corruption() -> None:
    # A RAISE disposition does not change the type for a rejected header: the nonzero
    # case escalates as CorruptionError, which outranks DiagnosticRaisedError.
    data = _tar_corrupt_mid_header()
    with raises_corruption_not_truncation():
        with open_archive(
            io.BytesIO(data),
            format=ArchiveFormat.TAR,
            config=_RAISE_ON_MISSING_EOF,
        ) as ar:
            ar.members()


def test_corrupt_final_header_ignore_disposition_still_raises() -> None:
    # escalate_as=CorruptionError takes precedence over IGNORE (spec matrix).
    from archivey.diagnostics import (
        DiagnosticCode,
        DiagnosticDisposition,
        DiagnosticPolicy,
    )

    data = _tar_corrupt_final_header()
    policy = DiagnosticPolicy(
        overrides={
            DiagnosticCode.ARCHIVE_EOF_MARKER_MISSING: DiagnosticDisposition.IGNORE
        }
    )
    with raises_corruption_not_truncation():
        with open_archive(
            io.BytesIO(data),
            format=ArchiveFormat.TAR,
            config=ArchiveyConfig(diagnostic_policy=policy),
        ) as ar:
            ar.members()


def test_corrupt_mid_header_streaming_extract_writes_then_raises(
    tmp_path: Path,
) -> None:
    # Streaming extract writes salvageable members, then raises at end-of-pass.
    data = _tar_corrupt_mid_header()
    dest = tmp_path / "out"
    with raises_corruption_not_truncation():
        with open_archive(
            NonSeekableBytesIO(data), format=ArchiveFormat.TAR, streaming=True
        ) as ar:
            ar.extract_all(dest)
    assert (dest / "a.txt").exists()
    assert (dest / "a.txt").read_bytes() == b"aaa"
    assert not (dest / "b.txt").exists()


# A zero block, then a damaged second end-of-archive block: GNU tar ("A lone zero block")
# and 7-Zip list every member with a warning and exit 0. The second block is damaged by
# one stray byte, or holds a header-shaped run of junk.
_DAMAGED_SECOND_BLOCKS = {
    "stray_byte": b"\x00" * 100 + b"\x01" + b"\x00" * 411,
    "junk_header": b"A" * 100 + b"\x00" * 412,
}


def _tar_damaged_second_eof_block(damage: bytes) -> bytes:
    data = _tar_three()
    end = _tar_content_end(data)
    return data[:end] + b"\x00" * 512 + damage + b"\x00" * (512 * 8)


def _eof_marker_contexts(ar: Any) -> list[ArchiveEofContext]:
    contexts = [
        d.context
        for d in ar.diagnostics.retained
        if d.code is DiagnosticCode.ARCHIVE_EOF_MARKER_MISSING
    ]
    assert all(isinstance(c, ArchiveEofContext) for c in contexts)
    return contexts


_THREE = {"a.txt": b"aaa", "b.txt": b"b" * 4000, "c.txt": b"ccc"}


@pytest.mark.parametrize("damage", _DAMAGED_SECOND_BLOCKS, ids=str)
@pytest.mark.parametrize("streaming", [False, True], ids=["random", "streaming"])
def test_damaged_second_eof_block_lists_every_member(
    streaming: bool, damage: str
) -> None:
    # The listing ends at the zero block, so it is whole: only the end-of-archive
    # marker is damaged. That is the ARCHIVE_EOF_MARKER_MISSING diagnostic, not the
    # CorruptionError a rejected header gets.
    data = _tar_damaged_second_eof_block(_DAMAGED_SECOND_BLOCKS[damage])
    source = NonSeekableBytesIO(data) if streaming else io.BytesIO(data)
    with open_archive(source, format=ArchiveFormat.TAR, streaming=streaming) as ar:
        read = {m.name: stream.read() for m, stream in ar.stream_members() if stream}
        contexts = _eof_marker_contexts(ar)
        if not streaming:
            assert [m.name for m in ar.members()] == list(_THREE)
            assert {m.name: ar.read(m) for m in ar.members()} == _THREE
    assert read == _THREE
    assert len(contexts) == 1
    assert contexts[0].format == "tar"
    # Its own marker, so the context says the listing is whole: a rejected header
    # reports "two_zero_blocks" with the same observed_kind and raises.
    assert contexts[0].expected_marker == "second_zero_block"
    assert contexts[0].expected_bytes == 512
    assert contexts[0].observed_bytes == 512
    assert contexts[0].observed_kind == "nonzero"


def test_damaged_second_eof_block_gzip_lists_every_member(tmp_path: Path) -> None:
    import gzip

    path = tmp_path / "damaged-end.tar.gz"
    path.write_bytes(
        gzip.compress(
            _tar_damaged_second_eof_block(_DAMAGED_SECOND_BLOCKS["stray_byte"])
        )
    )
    with open_archive(path) as ar:
        assert [m.name for m in ar.members()] == list(_THREE)
        assert len(_eof_marker_contexts(ar)) == 1


def _gzip_with_bad_crc(data: bytes) -> bytes:
    import gzip

    out = bytearray(gzip.compress(data))
    out[-8] ^= 0xFF  # the footer's CRC-32 over the whole decoded stream
    return bytes(out)


@pytest.mark.parametrize("streaming", [False, True], ids=["random", "streaming"])
@pytest.mark.parametrize(
    "tail",
    ["healthy_trailer", "damaged_second_block"],
)
def test_bad_gzip_crc_is_reported_after_a_damaged_second_eof_block(
    tmp_path: Path, streaming: bool, tail: str
) -> None:
    # The gzip footer sits past the trailer, in the record padding, so the trailing
    # scan is where its CRC-32 over the members is checked. A damaged second block
    # must not skip that scan and hand the members back as whole. The 64 KiB of
    # padding is a ``tar -b128`` record; with less, the streaming codec can reach the
    # footer while the last header is still being read.
    if tail == "healthy_trailer":
        plain = _tar_three()
    else:
        plain = _tar_damaged_second_eof_block(_DAMAGED_SECOND_BLOCKS["stray_byte"])
    path = tmp_path / "bad-crc.tar.gz"
    path.write_bytes(_gzip_with_bad_crc(plain + b"\x00" * (64 * 1024)))
    with raises_corruption_not_truncation():
        with open_archive(path, streaming=streaming) as ar:
            list(ar.stream_members())


def test_damaged_second_eof_block_then_junk_reports_trailing_data() -> None:
    data = _tar_damaged_second_eof_block(_DAMAGED_SECOND_BLOCKS["stray_byte"])
    end = _tar_content_end(data)  # the end of the damaged block
    data = data[:end] + b"\x00" * 100 + b"junk" + data[end + 104 :]
    with open_archive(io.BytesIO(data), format=ArchiveFormat.TAR) as ar:
        assert [m.name for m in ar.members()] == list(_THREE)
        trailing = [
            d.context
            for d in ar.diagnostics.retained
            if d.code is DiagnosticCode.ARCHIVE_TRAILING_DATA
        ]
        assert len(_eof_marker_contexts(ar)) == 1
    assert len(trailing) == 1
    assert isinstance(trailing[0], ArchiveEofContext)
    assert trailing[0].expected_marker == "zeros_to_eof"
    assert trailing[0].observed_bytes == 100


@pytest.mark.parametrize("streaming", [False, True], ids=["random", "streaming"])
def test_damaged_second_eof_block_refused_under_strict(streaming: bool) -> None:
    data = _tar_damaged_second_eof_block(_DAMAGED_SECOND_BLOCKS["stray_byte"])
    source = NonSeekableBytesIO(data) if streaming else io.BytesIO(data)
    strict = ArchiveyConfig(diagnostic_policy=DiagnosticPolicy.strict())
    with open_archive(
        source, format=ArchiveFormat.TAR, streaming=streaming, config=strict
    ) as ar:
        with pytest.raises(DiagnosticRaisedError):
            list(ar.stream_members())


@pytest.mark.parametrize("streaming", [False, True], ids=["random", "streaming"])
def test_damaged_second_eof_block_extracts_every_member(
    tmp_path: Path, streaming: bool
) -> None:
    data = _tar_damaged_second_eof_block(_DAMAGED_SECOND_BLOCKS["junk_header"])
    source = NonSeekableBytesIO(data) if streaming else io.BytesIO(data)
    dest = tmp_path / "out"
    with open_archive(source, format=ArchiveFormat.TAR, streaming=streaming) as ar:
        ar.extract_all(dest)
        assert len(_eof_marker_contexts(ar)) == 1
    assert {p.name: p.read_bytes() for p in dest.iterdir()} == _THREE


@pytest.mark.parametrize("streaming", [False, True], ids=["random", "streaming"])
def test_zero_block_then_junk_with_no_member_stays_corruption(
    streaming: bool,
) -> None:
    # With no member before the zero block there is no listing to keep, and a file
    # that is a zero block and then junk is not shown to be a tar at all.
    data = b"\x00" * 512 + b"A" * 100 + b"\x00" * (512 * 9 - 100)
    source = NonSeekableBytesIO(data) if streaming else io.BytesIO(data)
    with raises_corruption_not_truncation() as excinfo:
        with open_archive(source, format=ArchiveFormat.TAR, streaming=streaming) as ar:
            list(ar.stream_members())
    # There is no member header and no listing to shorten, so the message says what
    # is actually wrong.
    assert "has no member" in str(excinfo.value)
    assert "shortened listing" not in str(excinfo.value)


def test_padded_tar_eof_no_false_positive(caplog: pytest.LogCaptureFixture) -> None:
    # tarfile writes 10240-byte record padding (many trailing null blocks past the two
    # required ones). The probe must not read that padding as a rejected block.
    data = _build_tar()  # full tarfile output, padded
    with caplog.at_level(logging.WARNING, logger="archivey.backends"):
        with open_archive(io.BytesIO(data), format=ArchiveFormat.TAR) as ar:
            ar.members()
    assert _eof_warnings(caplog) == []


# ---------------------------------------------------------------------------
# Corrupt / truncated input (per-format slice of testing-contract).
# ---------------------------------------------------------------------------


def test_truncated_tar_raises() -> None:
    full = _build_tar()
    # Cut into the body so the header scan hits "unexpected end of data" (tarfile pads the
    # whole archive to a 10 KiB record, so cut well inside the real member region).
    truncated = full[:800]
    with pytest.raises(TruncatedError) as excinfo:
        with open_archive(io.BytesIO(truncated), format=ArchiveFormat.TAR) as ar:
            ar.members()
    assert isinstance(excinfo.value.__cause__, tarfile.ReadError)


def test_corrupt_tar_header_raises() -> None:
    raw = bytearray(_build_tar())
    # Corrupt the checksum field (offset 148, 8 bytes) of the first header.
    raw[148:156] = b"\xff\xff\xff\xff\xff\xff\xff\xff"
    with raises_corruption_not_truncation() as excinfo:
        with open_archive(io.BytesIO(bytes(raw)), format=ArchiveFormat.TAR) as ar:
            ar.members()
    assert isinstance(excinfo.value.__cause__, tarfile.ReadError)


def _raw_tar_header(
    name: str, typeflag: bytes, size: int, *, size_field: bytes | None = None
) -> bytes:
    """A ustar header block for ``name`` with the given typeflag and size. ``size_field``
    replaces the 12-byte size field as stored (for a GNU base-256 size)."""
    info = tarfile.TarInfo(name)
    info.size = size
    info.mode = 0o755
    header = bytearray(info.tobuf(format=tarfile.USTAR_FORMAT))
    header[156:157] = typeflag
    if size_field is not None:
        header[124:136] = size_field
    header[148:156] = b" " * 8
    header[148:156] = b"%06o\0 " % sum(header)
    return bytes(header)


def _tar_slash_entry_with_data(
    typeflag: bytes,
    *,
    own_name: str = "d/",
    wrapper: str | None = None,
    final_name: str = "d/",
    size_field: bytes | None = None,
) -> bytes:
    """A ustar entry with the given typeflag and 15 bytes of data, then a regular file
    ``after.txt``. GNU tar 1.35 and 7-Zip list ``d/`` as a directory and skip its data
    when the typeflag is NUL (old-style ``AREGTYPE``) or ``0``; they report an error
    when it is ``5`` (``DIRTYPE``), which carries no data.

    ``wrapper`` puts a PAX ``path`` record (``"pax"``) or a GNU long name (``"gnu"``)
    holding ``final_name`` before the header, whose own name field is ``own_name``.
    """
    data = b"hello directory"
    prefix = b""
    if wrapper == "pax":
        record = f"path={final_name}\n".encode()
        length = len(record) + 2
        length += len(str(length)) - 1
        record = b"%d " % length + record
        assert len(record) == length
        prefix = _raw_tar_header("PaxHeaders/d", tarfile.XHDTYPE, len(record))
        prefix += record.ljust(512, b"\0")
    elif wrapper == "gnu":
        long_name = final_name.encode() + b"\0"
        prefix = _raw_tar_header(
            "././@LongLink", tarfile.GNUTYPE_LONGNAME, len(long_name)
        )
        prefix += long_name.ljust(512, b"\0")
    after = tarfile.TarInfo("after.txt")
    after.size = 6
    return (
        prefix
        + _raw_tar_header(own_name, typeflag, len(data), size_field=size_field)
        + data.ljust(512, b"\0")
        + after.tobuf(format=tarfile.USTAR_FORMAT)
        + b"after\n".ljust(512, b"\0")
        + b"\0" * 1024
    )


def _stream_all(data: bytes, streaming: bool) -> list[tuple[str, MemberType, Any]]:
    with open_archive(
        NonSeekableBytesIO(data) if streaming else io.BytesIO(data),
        format=ArchiveFormat.TAR,
        streaming=streaming,
    ) as ar:
        return [
            (m.name, m.type, stream.read() if stream is not None else None)
            for m, stream in ar.stream_members()
        ]


@pytest.mark.parametrize("streaming", [False, True])
def test_old_style_directory_with_data_is_skipped(streaming: bool) -> None:
    # stdlib tarfile turns an AREGTYPE header whose name ends in "/" into a directory
    # and then reads its data blocks as the next header. GNU tar skips the data.
    data = _tar_slash_entry_with_data(tarfile.AREGTYPE)
    assert _stream_all(data, streaming) == [
        ("d/", MemberType.DIRECTORY, None),
        ("after.txt", MemberType.FILE, b"after\n"),
    ]


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("wrapper", ["pax", "gnu"])
@pytest.mark.parametrize("own_name", ["d", "d/"])
def test_old_style_directory_name_from_extended_header(
    wrapper: str, own_name: str, streaming: bool
) -> None:
    # GNU tar and 7-Zip decide on the final name, after a PAX path or a GNU long name,
    # whatever the header's own name field holds. stdlib tarfile decides on the own
    # name field, and only on Pythons without the 2025 fixes.
    data = _tar_slash_entry_with_data(
        tarfile.AREGTYPE, own_name=own_name, wrapper=wrapper, final_name="d/"
    )
    assert _stream_all(data, streaming) == [
        ("d/", MemberType.DIRECTORY, None),
        ("after.txt", MemberType.FILE, b"after\n"),
    ]


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("wrapper", ["pax", "gnu"])
def test_aregtype_slash_name_overridden_without_slash_is_a_file(
    wrapper: str, streaming: bool
) -> None:
    # The own name field says "d/", the final name says "d": GNU tar lists a file.
    data = _tar_slash_entry_with_data(
        tarfile.AREGTYPE, own_name="d/", wrapper=wrapper, final_name="d"
    )
    assert _stream_all(data, streaming) == [
        ("d", MemberType.FILE, b"hello directory"),
        ("after.txt", MemberType.FILE, b"after\n"),
    ]


def test_old_style_directory_keeps_stored_typeflag() -> None:
    # extra["tar.type"] is the typeflag byte as stored, which tells an old-style
    # directory apart from a DIRTYPE one.
    data = _tar_slash_entry_with_data(tarfile.AREGTYPE)
    with open_archive(io.BytesIO(data), format=ArchiveFormat.TAR) as ar:
        member = ar.members()[0]
    assert member.type == MemberType.DIRECTORY
    assert member.extra["tar.type"] == tarfile.AREGTYPE


@pytest.mark.parametrize("streaming", [False, True])
def test_old_style_directory_data_cut_off_is_truncation(streaming: bool) -> None:
    # The archive ends inside the data area the directory header declares.
    data = _tar_slash_entry_with_data(tarfile.AREGTYPE)[:512]
    with pytest.raises(TruncatedError):
        _stream_all(data, streaming)


@pytest.mark.parametrize("from_file", [True, False], ids=["file", "bytesio"])
@pytest.mark.parametrize("typeflag", [tarfile.AREGTYPE, tarfile.REGTYPE])
def test_size_past_filesystem_limit_is_truncation(
    tmp_path: Path, typeflag: bytes, from_file: bool
) -> None:
    # A GNU base-256 size of 2**62 is under the largest seek offset. ext4 refuses a
    # seek that far with OSError(EINVAL); APFS and a BytesIO accept it, and the next
    # read finds the end of the data. The same archive must give the same error from
    # every source on every OS.
    size_field = b"\x80" + (2**62).to_bytes(11, "big")
    data = _tar_slash_entry_with_data(typeflag, size_field=size_field)
    path = tmp_path / "big.tar"
    path.write_bytes(data)
    source = path if from_file else io.BytesIO(data)
    with pytest.raises(TruncatedError):
        with open_archive(source, format=ArchiveFormat.TAR) as ar:
            ar.members()


@pytest.mark.parametrize(
    ("typeflag", "expected"),
    [(tarfile.AREGTYPE, True), (tarfile.DIRTYPE, False), (tarfile.REGTYPE, False)],
)
def test_old_style_directory_marked_by_public_frombuf(
    typeflag: bytes, expected: bool
) -> None:
    # Python versions before the 2025 tarfile fixes parse headers through the public
    # frombuf(), not _frombuf(); stdlib's own old-style directory rule must be undone
    # on that path too, so the header reads as the file it is stored as.
    header = _tar_slash_entry_with_data(typeflag)[:512]
    info = tar_reader_module._TarInfo.frombuf(header, "utf-8", "surrogateescape")
    assert (info.type == tarfile.AREGTYPE and info.name == "d/") is expected


def test_old_style_directory_with_data_extracts(tmp_path: Path) -> None:
    data = _tar_slash_entry_with_data(tarfile.AREGTYPE)
    open_and_extract(io.BytesIO(data), tmp_path, format=ArchiveFormat.TAR)
    assert (tmp_path / "d").is_dir()
    assert (tmp_path / "after.txt").read_bytes() == b"after\n"


def test_dirtype_with_data_stays_corruption() -> None:
    # A DIRTYPE header has no data area, so the caller gets CorruptionError: in random
    # access the listing yields no members, and after.txt is not reached. GNU tar 1.35
    # reports an error ("Skipping to next header") and keeps listing; 7-Zip stops.
    data = _tar_slash_entry_with_data(tarfile.DIRTYPE)
    with raises_corruption_not_truncation():
        with open_archive(io.BytesIO(data), format=ArchiveFormat.TAR) as ar:
            ar.members()


def test_filesystem_oserror_propagates_unwrapped(tmp_path: Path) -> None:
    # A genuine OSError (missing file) is not archive corruption: it must propagate
    # unchanged, not be reclassified as CorruptionError (error-handling spec).
    missing = tmp_path / "does-not-exist.tar"
    with pytest.raises(FileNotFoundError):
        open_archive(missing, format=ArchiveFormat.TAR)


def test_corrupt_path_open_releases_owned_handle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Path opens always use fileobj= with an owned fp for the EOF probe. On open failure
    # the owned handle must be closed before re-raising — otherwise the exception
    # traceback pins the frame (and the fd) until the caller drops the exception.
    import builtins

    path = tmp_path / "bad.tar"
    path.write_bytes(b"\xff" * 1024)
    closed: list[bool] = []
    real_open = builtins.open

    def tracking_open(file: object, *args: object, **kwargs: object) -> object:
        f = real_open(file, *args, **kwargs)  # type: ignore[arg-type]
        if Path(file) == path:  # type: ignore[arg-type]
            inner_close = f.close

            def close() -> None:
                closed.append(True)
                inner_close()

            f.close = close  # type: ignore[method-assign]
        return f

    monkeypatch.setattr(builtins, "open", tracking_open)
    kept: list[Exception] = []
    with raises_corruption_not_truncation() as excinfo:
        open_archive(path, format=ArchiveFormat.TAR)
    kept.append(
        excinfo.value
    )  # keep the traceback alive, as a catch-and-continue loop would
    assert closed == [True], "owned path handle must close before open failure escapes"
    del kept


def test_corrupt_compressed_tar_surfaces_codec_corruption(tmp_path: Path) -> None:
    # A gzip-wrapped tar whose deflate body is mangled: the corruption surfaces through the
    # codec layer as a CorruptionError while scanning/reading (not a raw zlib.error).
    path = tmp_path / "bad.tar.gz"
    raw = bytearray(_build_tar("w:gz"))
    raw[len(raw) // 2] ^= 0xFF  # flip a byte inside the deflate stream
    path.write_bytes(bytes(raw))
    with raises_corruption_not_truncation():
        with open_archive(path) as ar:
            for _member, stream in ar.stream_members():
                if stream is not None:
                    stream.read()


# ---------------------------------------------------------------------------
# Password rejection and hostile metadata robustness
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("password", "recorded"),
    [("x", 1), (["a", "b"], 1), (lambda _request: "x", 0)],
    ids=["str", "list", "provider"],
)
def test_password_is_accepted_in_every_form(password: object, recorded: int) -> None:
    # TAR carries no encryption. All three password forms open alike: accepted, never
    # consulted (review O5). Only a concrete value is recorded: a provider offers a
    # password only if asked, and TAR never asks.
    from archivey.diagnostics import DiagnosticCode

    with open_archive(
        io.BytesIO(_build_tar()),
        format=ArchiveFormat.TAR,
        password=password,  # type: ignore[arg-type]
    ) as reader:
        counts = reader.diagnostics.counts
        assert counts.get(DiagnosticCode.PASSWORD_ARGUMENT_UNUSED, 0) == recorded


def test_password_provider_ok_on_unencrypted_format() -> None:
    # A PasswordProvider that is never called is not "supplying a password". Formats
    # without encryption must still open (the CLI registers a getpass provider by default).
    called = {"n": 0}

    def provider(_request: object) -> None:
        called["n"] += 1
        return

    with open_archive(
        io.BytesIO(_build_tar()), format=ArchiveFormat.TAR, password=provider
    ) as reader:
        names = [m.name for m in reader.members()]
    assert "hello.txt" in names
    assert called["n"] == 0


def test_out_of_range_mtime_degrades_to_none() -> None:
    # A crafted PAX mtime beyond datetime's range must not sink the listing.
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as t:
        info = tarfile.TarInfo("weird.txt")
        info.size = 0
        info.mtime = 10**18
        t.addfile(info)
    with open_archive(io.BytesIO(buf.getvalue()), format=ArchiveFormat.TAR) as reader:
        (member,) = reader.members()
        assert member.modified is None


# ---------------------------------------------------------------------------
# Link-target name resolution (relative symlinks, hardlinks, streaming pass)
# ---------------------------------------------------------------------------


def _build_link_tar() -> bytes:
    """dir/file + a root-level decoy `file`, and links exercising target resolution."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        for name, data in [
            ("file", b"ROOT"),
            ("dir/file", b"NESTED"),
            ("top.txt", b"TOP"),
        ]:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
        rel = tarfile.TarInfo("dir/rel_link")  # -> dir/file, not the root decoy
        rel.type = tarfile.SYMTYPE
        rel.linkname = "file"
        t.addfile(rel)
        up = tarfile.TarInfo("dir/up_link")  # ../top.txt -> top.txt
        up.type = tarfile.SYMTYPE
        up.linkname = "../top.txt"
        t.addfile(up)
        absolute = tarfile.TarInfo("dir/abs_link")  # absolute: outside the archive
        absolute.type = tarfile.SYMTYPE
        absolute.linkname = "/etc/passwd"
        t.addfile(absolute)
        hard = tarfile.TarInfo("dir/hard")  # hardlink targets are archive-relative
        hard.type = tarfile.LNKTYPE
        hard.linkname = "dir/file"
        t.addfile(hard)
    return buf.getvalue()


def test_relative_symlink_resolves_against_link_directory() -> None:
    with open_archive(io.BytesIO(_build_link_tar()), format=ArchiveFormat.TAR) as ar:
        member = ar.get("dir/rel_link")
        assert member.link_target == "file"  # raw stored target is untouched
        assert member.link_target_member is not None
        assert member.link_target_member.name == "dir/file"
        assert ar.read("dir/rel_link") == b"NESTED"  # not the root-level decoy


def test_dotdot_symlink_resolves_upward() -> None:
    with open_archive(io.BytesIO(_build_link_tar()), format=ArchiveFormat.TAR) as ar:
        assert ar.get("dir/up_link").link_target_member.name == "top.txt"
        assert ar.read("dir/up_link") == b"TOP"


def test_absolute_symlink_stays_unresolved() -> None:
    from archivey.exceptions import LinkTargetNotFoundError

    with open_archive(io.BytesIO(_build_link_tar()), format=ArchiveFormat.TAR) as ar:
        member = ar.get("dir/abs_link")
        assert member.link_target == "/etc/passwd"
        assert member.link_target_member is None
        with pytest.raises(LinkTargetNotFoundError):
            ar.open(member)


def test_hardlink_target_is_archive_relative() -> None:
    with open_archive(io.BytesIO(_build_link_tar()), format=ArchiveFormat.TAR) as ar:
        assert ar.get("dir/hard").link_target_member.name == "dir/file"
        assert ar.read("dir/hard") == b"NESTED"


def test_streaming_pass_resolves_backward_links() -> None:
    # Hardlinks always point at an earlier member (the TAR model), so a single
    # streaming pass resolves them progressively; relative symlinks to earlier
    # members resolve too.
    source = NonSeekableBytesIO(_build_link_tar())
    with open_archive(source, format=ArchiveFormat.TAR, streaming=True) as ar:
        resolved = {
            m.name: (m.link_target_member.name if m.link_target_member else None)
            for m, _stream in ar.stream_members()
            if m.is_link
        }
    assert resolved == {
        "dir/rel_link": "dir/file",
        "dir/up_link": "top.txt",
        "dir/abs_link": None,
        "dir/hard": "dir/file",
    }


def _link_tar_bytes(specs: list[tuple[str, str, bytes | str]]) -> bytes:
    """Build a tar from (kind, name, payload) specs.

    kind: ``file`` (payload=bytes), ``sym``/``hard`` (payload=linkname).
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        for kind, name, payload in specs:
            info = tarfile.TarInfo(name)
            if kind == "file":
                info.size = len(payload)
                info.mode = 0o644
                t.addfile(info, io.BytesIO(payload))
            elif kind == "sym":
                info.type = tarfile.SYMTYPE
                info.linkname = payload
                t.addfile(info)
            elif kind == "hard":
                info.type = tarfile.LNKTYPE
                info.linkname = payload
                t.addfile(info)
    return buf.getvalue()


_DUP_HARDLINK_TAR = _link_tar_bytes(
    [
        ("file", "A.txt", b"content1"),
        ("hard", "L.txt", "A.txt"),
        ("file", "A.txt", b"content2"),
    ]
)


def test_hardlink_duplicate_name_positional_random_access() -> None:
    with open_archive(io.BytesIO(_DUP_HARDLINK_TAR), format=ArchiveFormat.TAR) as ar:
        link = ar.get("L.txt")
        assert link.link_target_member is not None
        assert link.link_target_member.name == "A.txt"
        assert ar.read(link.link_target_member) == b"content1"
        assert ar.read("L.txt") == b"content1"


def test_hardlink_duplicate_name_positional_streaming() -> None:
    source = NonSeekableBytesIO(_DUP_HARDLINK_TAR)
    with open_archive(source, format=ArchiveFormat.TAR, streaming=True) as ar:
        links = {
            m.name: m.link_target_member
            for m, _ in ar.stream_members()
            if m.type == MemberType.HARDLINK
        }
    assert links["L.txt"] is not None
    assert links["L.txt"].name == "A.txt"


def test_symlink_duplicate_name_last_wins_random_access() -> None:
    data = _link_tar_bytes(
        [
            ("file", "A.txt", b"content1"),
            ("sym", "S.txt", "A.txt"),
            ("file", "A.txt", b"content2"),
        ]
    )
    with open_archive(io.BytesIO(data), format=ArchiveFormat.TAR) as ar:
        link = ar.get("S.txt")
        assert link.link_target_member is not None
        assert link.link_target_member.name == "A.txt"
        assert ar.read(link.link_target_member) == b"content2"
        assert ar.read("S.txt") == b"content2"


# ---------------------------------------------------------------------------
# scan_members(), post-pass cache, one-pass-only streaming
# ---------------------------------------------------------------------------


def _build_forward_symlink_tar() -> bytes:
    """Symlink appears before its target in archive order."""
    return _link_tar_bytes(
        [
            ("sym", "forward_link", "target.txt"),
            ("file", "target.txt", b"TARGET"),
        ]
    )


def test_streaming_scan_members_resolves_forward_symlink() -> None:
    source = NonSeekableBytesIO(_build_forward_symlink_tar())
    with open_archive(source, format=ArchiveFormat.TAR, streaming=True) as ar:
        members = ar.scan_members()
    link = next(m for m in members if m.name == "forward_link")
    assert link.link_target_member is not None
    assert link.link_target_member.name == "target.txt"

    with open_archive(
        io.BytesIO(_build_forward_symlink_tar()), format=ArchiveFormat.TAR
    ) as ar:
        expected = ar.get("forward_link")
    assert link.link_target_member.name == expected.link_target_member.name


def test_streaming_iter_materializes_resolved_cache() -> None:
    source = NonSeekableBytesIO(_build_forward_symlink_tar())
    with open_archive(source, format=ArchiveFormat.TAR, streaming=True) as ar:
        collected: list = []
        for member in ar:
            if member.name == "forward_link":
                collected.append(member)
                assert member.link_target_member is None
        report = ar.members_report_if_available()
        assert report is not None
        link = next(m for m in report if m.name == "forward_link")
        assert link.link_target_member is not None
        assert link.link_target_member.name == "target.txt"
        assert collected[0].link_target_member.name == "target.txt"


def test_streaming_stream_members_materializes_resolved_cache() -> None:
    source = NonSeekableBytesIO(_build_forward_symlink_tar())
    with open_archive(source, format=ArchiveFormat.TAR, streaming=True) as ar:
        collected: list = []
        for member, _stream in ar.stream_members():
            if member.name == "forward_link":
                collected.append(member)
        report = ar.members_report_if_available()
        assert report is not None
        link = next(m for m in report if m.name == "forward_link")
        assert link.link_target_member is not None
        assert collected[0].link_target_member.name == "target.txt"


def test_scan_members_finishes_interrupted_pass(tmp_path: Path) -> None:
    source = NonSeekableBytesIO(_build_forward_symlink_tar())
    with open_archive(source, format=ArchiveFormat.TAR, streaming=True) as ar:
        for member in ar:
            if member.name == "forward_link":
                break
        members = ar.scan_members()
        assert [m.name for m in members] == ["forward_link", "target.txt"]
        link = next(m for m in members if m.name == "forward_link")
        assert link.link_target_member is not None
        assert link.link_target_member.name == "target.txt"
        with pytest.raises(ArchiveyUsageError):
            list(ar)
        with pytest.raises(ArchiveyUsageError):
            list(ar.stream_members())
        with pytest.raises(ArchiveyUsageError):
            ar.extract_all(tmp_path)


def test_abandoned_partial_pass_leaves_get_members_none() -> None:
    source = NonSeekableBytesIO(_build_forward_symlink_tar())
    with open_archive(source, format=ArchiveFormat.TAR, streaming=True) as ar:
        for member in ar:
            if member.name == "forward_link":
                break
        assert ar.members_report_if_available() is None


def test_streaming_second_pass_raises_tar_and_zip(
    plain_tar: Path, tmp_path: Path
) -> None:
    zip_path = tmp_path / "second.zip"
    import zipfile

    with zipfile.ZipFile(zip_path, "w") as z:
        z.writestr("hello.txt", b"hello world")
    with open_archive(plain_tar, streaming=True) as ar:
        list(ar)
        with pytest.raises(ArchiveyUsageError):
            list(ar)
        with pytest.raises(ArchiveyUsageError):
            list(ar.stream_members())
    with open_archive(zip_path, streaming=True) as ar:
        list(ar)
        with pytest.raises(ArchiveyUsageError):
            list(ar)


def test_scan_members_random_access_parity(plain_tar: Path, tmp_path: Path) -> None:
    import zipfile

    zip_path = tmp_path / "scan.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        z.writestr("hello.txt", b"hello world")
    simple_dir = tmp_path / "dirscan"
    simple_dir.mkdir()
    (simple_dir / "a.txt").write_bytes(b"x")
    for source in (plain_tar, zip_path, simple_dir):
        with open_archive(source) as ar:
            assert ar.scan_members() == ar.members()
            assert [m.name for m in ar] == [m.name for m in ar.members()]


def test_scan_members_before_pass_consumes_streaming_reader(
    plain_tar: Path, tmp_path: Path
) -> None:
    import zipfile

    zip_path = tmp_path / "consume.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        z.writestr("hello.txt", b"hello world")
    simple_dir = tmp_path / "dirconsume"
    simple_dir.mkdir()
    (simple_dir / "a.txt").write_bytes(b"x")
    for source in (plain_tar, zip_path, simple_dir):
        with open_archive(source, streaming=True) as ar:
            names = {m.name for m in ar.scan_members()}
            assert len(names) > 0
            with pytest.raises(ArchiveyUsageError):
                list(ar.stream_members())


def test_link_cycle_raises_read_error() -> None:
    data = _link_tar_bytes(
        [
            ("sym", "a", "b"),
            ("sym", "b", "a"),
        ]
    )
    with open_archive(io.BytesIO(data), format=ArchiveFormat.TAR) as ar:
        with pytest.raises(ReadError, match="cycle"):
            ar.read("a")


def test_chain_through_same_named_members_not_false_cycle() -> None:
    """Member-id cycle tracking must not false-positive on distinct same-named members."""
    from archivey.cost import AccessCost, ListingCost, StreamCapability
    from archivey.internal.base_reader import BaseArchiveReader
    from archivey.types import ArchiveInfo, ArchiveMember

    class _Reader(BaseArchiveReader):
        def __init__(
            self, members: list[ArchiveMember], payloads: dict[str, bytes]
        ) -> None:
            super().__init__(ArchiveFormat.TAR, streaming=False, archive_name=None)
            self._payloads = payloads
            self._listed = members
            for m in members:
                self._index_member_name(m)
            self._publish_materialized(error=None)

        def _iter_members(self):
            materialized = self._materialized
            return iter(materialized.report.members if materialized is not None else ())

        def _open_member(self, member: ArchiveMember) -> BinaryIO:
            return io.BytesIO(self._payloads[member.name])

        def _get_archive_info(self) -> ArchiveInfo:
            return ArchiveInfo(
                format=ArchiveFormat.TAR,
                cost=AccessCost(
                    listing=ListingCost.FREE,
                    random_access=StreamCapability.SUPPORTED,
                ),
            )

        def _close_archive(self) -> None:
            return None

    # Hop-by-hop chain start → dup(sym) → tail → dup(file); two distinct "dup.txt" members.
    first = ArchiveMember(name="dup.txt", type=MemberType.SYMLINK, link_target="tail")
    second = ArchiveMember(name="dup.txt", type=MemberType.FILE)
    tail = ArchiveMember(name="tail", type=MemberType.SYMLINK, link_target="dup.txt")
    start = ArchiveMember(name="start", type=MemberType.SYMLINK, link_target="dup.txt")
    for idx, m in enumerate((first, second, tail, start)):
        m._member_id = idx
        m._archive_id = "test"
    first.link_target_member = tail
    tail.link_target_member = second
    start.link_target_member = first
    reader = _Reader(
        [first, second, tail, start],
        {"dup.txt": b"payload", "tail": b"", "start": b""},
    )
    with reader._open_with_link_follow(start, set()) as stream:
        assert stream.read() == b"payload"


# ---------------------------------------------------------------------------
# A failed streaming pass must not publish a partial member list (deep N1)
# ---------------------------------------------------------------------------


def test_error_mid_streaming_pass_poisons_scan_members() -> None:
    """End-to-end N1 repro: a RAISE-disposition diagnostic fires mid-pass; the caller
    catches it; ``scan_members()`` must then fail loud instead of silently returning
    the two-member prefix as the complete resolved list."""
    from archivey.diagnostics import (
        DiagnosticCode,
        DiagnosticDisposition,
        DiagnosticPolicy,
    )
    from archivey.exceptions import DiagnosticRaisedError

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name in ("a", "b", "c", "d"):
            data = b"x" * 100
            info = tarfile.TarInfo(name + ".bin")
            info.size = len(data)
            if name == "c":
                info.mtime = 2**62  # out of range -> MEMBER_TIMESTAMP_INVALID
            tf.addfile(info, io.BytesIO(data))
    buf.seek(0)

    config = ArchiveyConfig(
        diagnostic_policy=DiagnosticPolicy(
            overrides={
                DiagnosticCode.MEMBER_TIMESTAMP_INVALID: DiagnosticDisposition.RAISE
            }
        )
    )
    with open_archive(
        buf, streaming=True, format=ArchiveFormat.TAR, config=config
    ) as reader:
        seen: list[str] = []
        with pytest.raises(DiagnosticRaisedError):
            for member in reader:
                seen.append(member.name)
        assert seen == ["a.bin", "b.bin"]
        with pytest.raises(ReadError, match="previously failed"):
            reader.scan_members()
        assert reader.members_report_if_available() is None


# ---------------------------------------------------------------------------
# Header-sized allocations
# ---------------------------------------------------------------------------


def _tar_with_oversized_metadata_header(typeflag: bytes, declared: int) -> bytes:
    """A tar whose extended-header block declares ``declared`` bytes it does not have.

    ``typeflag`` ``x`` is a PAX extended header, ``L`` a GNU ``././@LongLink``. Both
    are written by ``tarfile`` itself for a name too long for ustar; only the 12-byte
    octal size field and the header checksum are rewritten.
    """
    fmt = tarfile.PAX_FORMAT if typeflag == b"x" else tarfile.GNU_FORMAT
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=fmt) as t:
        info = tarfile.TarInfo("a" * 200)
        info.size = 0
        t.addfile(info)
    data = bytearray(buf.getvalue())
    assert data[156:157] == typeflag
    data[124:136] = b"%011o\0" % declared
    data[148:156] = b" " * 8  # the checksum field reads as spaces while summing
    data[148:156] = b"%06o\0 " % sum(data[:512])
    return bytes(data)


@pytest.mark.parametrize("typeflag", [b"x", b"L"])
@pytest.mark.parametrize("length", ["fact", "hint", "unknown"])
def test_extended_header_size_does_not_drive_the_allocation(
    typeflag: bytes, length: str
) -> None:
    """A 10 KB archive must not make the reader ask its source for 6 GiB.

    stdlib ``tarfile`` reads a PAX extended header or a GNU long name with a single
    ``read`` sized from the header's own 12-byte octal field — attacker-chosen, and
    allocated in full before the short read reveals the archive is tiny. Asking for
    the bytes is the observable: whether the allocation then succeeds depends on the
    machine, so it is the request that is pinned, not a ``MemoryError``.

    The three parameters are the three things the source can know about its length,
    and they take different branches of the bound. ``fact`` is a ``BytesIO``, whose
    length the boundary reads from its buffer, so the read is clamped to exactly what
    is left; it fails against an unbounded ``read(size)``, which passes 6 442 450 944
    straight through. ``hint`` advertises the fsspec ``size`` attribute, a caller's
    unverified claim, which must not clamp (an understating hint would truncate a
    legitimate read), so the read is stepped. ``unknown`` has neither, which is what
    every compressed source and every ordinary caller-supplied file-like looks like;
    it is stepped too, and fails against treating an unknown length as an unlimited
    one (``remaining is None`` forwarding ``size`` down), which passes the same
    6 442 450 944. A bound tested only where the length is known is untested on the
    branch most sources actually take.
    """
    declared = 6 * 1024**3
    data = _tar_with_oversized_metadata_header(typeflag, declared)
    source: FactSizedReadRecorder | ReadSizeRecorder = (
        FactSizedReadRecorder(data)
        if length == "fact"
        else ReadSizeRecorder(data, advertise_size=length == "hint")
    )

    # Without a metadata cap: with one, the declared size is refused before any read
    # (``test_extended_header_over_the_metadata_cap_is_refused_unread``), and the read
    # bound is what this pins.
    config = ArchiveyConfig(listing_limits=ListingLimits.UNLIMITED)
    with raises_corruption_not_truncation():
        with open_archive(source, format=ArchiveFormat.TAR, config=config) as reader:
            reader.members()

    assert source.requested, "the source was never read"
    # A raw source sits under a ``BufferedReader``, whose refill size is a constant of
    # the runtime (``io.DEFAULT_BUFFER_SIZE``: 8 KiB through 3.13, 128 KiB from 3.14)
    # and has nothing to do with the archive. So the bound is one refill or, whichever
    # is larger, the archive when its length is a fact and the step when it is not;
    # what every case pins is that no read scales with ``declared``, which is six
    # gigabytes.
    reach = len(data) if length == "fact" else DEFAULT_UNKNOWN_LENGTH_READ_STEP
    bound = max(reach, io.DEFAULT_BUFFER_SIZE)
    assert max(source.requested) <= bound, (
        f"asked the source for {max(source.requested)} bytes "
        f"from a {len(data)}-byte archive"
    )


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("typeflag", [b"x", b"L"])
def test_extended_header_over_the_metadata_cap_is_refused_unread(
    typeflag: bytes, streaming: bool
) -> None:
    """An extended header declaring more than ``max_metadata_bytes`` is refused from
    its size field, before tarfile reads its data whole: the read would cost what
    the header declares, not what the cap allows."""
    data = _tar_with_oversized_metadata_header(typeflag, 6 * 1024**3)
    source = ReadSizeRecorder(data, advertise_size=False)
    with pytest.raises(ResourceLimitError, match="max_metadata_bytes"):
        with open_archive(source, format=ArchiveFormat.TAR, streaming=streaming):
            pass
    assert max(source.requested) <= max(
        DEFAULT_UNKNOWN_LENGTH_READ_STEP, io.DEFAULT_BUFFER_SIZE, 10240
    )


def test_a_member_larger_than_the_read_step_still_reads_whole(tmp_path: Path) -> None:
    """The bound must not cut a legitimate read short.

    A member past ``_EofProbeStream._UNKNOWN_LENGTH_READ_STEP`` is the case where the
    wrapper stops handing the request straight down, so it is the one that would show
    a truncation or a stitching bug. Compressed, because that is the path with no
    cheap length and therefore the one that takes the stepped route.
    """
    step = tar_reader_module._EofProbeStream._UNKNOWN_LENGTH_READ_STEP
    payload = bytes(range(256)) * ((step // 256) + 1024)
    assert len(payload) > step

    path = tmp_path / "big.tar.gz"
    with tarfile.open(path, "w:gz") as t:
        info = tarfile.TarInfo("big.bin")
        info.size = len(payload)
        t.addfile(info, io.BytesIO(payload))

    with open_archive(path) as reader:
        member = next(m for m in reader.members() if m.is_file)
        with reader.open(member) as stream:
            assert stream.read() == payload


def _one_member_tar(
    name: str,
    codec: str,
    fmt: int,
    *,
    linkname: str | None = None,
    owner: str = "",
) -> bytes:
    """A one-member TAR whose header strings are stored in ``codec``: a regular file,
    or a symlink to ``linkname``, with ``owner`` as both ``uname`` and ``gname``."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=fmt, encoding=codec) as t:
        info = tarfile.TarInfo(name)
        info.uname = info.gname = owner
        if linkname is None:
            info.size = 1
            t.addfile(info, io.BytesIO(b"x"))
        else:
            info.type = tarfile.SYMTYPE
            info.linkname = linkname
            t.addfile(info)
    return buf.getvalue()


@pytest.mark.parametrize("encoding", [None, "utf-8", "latin-1", "ascii"])
def test_pax_raw_name_is_the_stored_utf8_whatever_the_encoding(
    encoding: str | None,
) -> None:
    """A PAX ``path`` record is UTF-8 whatever ``encoding=`` says; re-encoding it with
    the caller's codec either crashed the listing or fabricated bytes."""
    for name in ("日本語.txt", "café.txt"):
        data = _one_member_tar(name, "utf-8", tarfile.PAX_FORMAT)
        with open_archive(io.BytesIO(data), encoding=encoding) as ar:
            (member,) = ar.members()
            assert member.name == name
            assert member.raw_name == name.encode("utf-8")


def test_ustar_raw_name_follows_the_archive_encoding() -> None:
    data = _one_member_tar("café.txt", "latin-1", tarfile.USTAR_FORMAT)
    with open_archive(io.BytesIO(data), encoding="latin-1") as ar:
        (member,) = ar.members()
        assert member.name == "café.txt"
        assert member.raw_name == b"caf\xe9.txt"


# tarfile's own default is TarFile.encoding (= tarfile.ENCODING, the filesystem
# encoding on POSIX). Setting it to Latin-1 stands in for a process under a Latin-1
# locale, where every byte decodes, so none of the tests below can pass by accident.
_NON_UTF8_LOCALE = mock.patch.object(tarfile.TarFile, "encoding", "latin-1")


@pytest.mark.parametrize(
    "fmt",
    [
        pytest.param(tarfile.USTAR_FORMAT, id="ustar"),
        # A name over 100 bytes goes into a GNU long-name record.
        pytest.param(tarfile.GNU_FORMAT, id="gnu-longname"),
    ],
)
def test_utf8_name_decodes_as_utf8_under_a_non_utf8_locale(fmt: int) -> None:
    name = "café-" + "x" * (120 if fmt == tarfile.GNU_FORMAT else 0) + ".txt"
    data = _one_member_tar(name, "utf-8", fmt)
    assert (b"././@LongLink" in data) == (fmt == tarfile.GNU_FORMAT)
    with _NON_UTF8_LOCALE, open_archive(io.BytesIO(data)) as ar:
        (member,) = ar.members()
        assert member.name == name
        assert member.raw_name == name.encode("utf-8")


def test_utf8_link_target_and_owner_decode_as_utf8_under_a_non_utf8_locale() -> None:
    target = "цель/café.txt"
    data = _one_member_tar(
        "link", "utf-8", tarfile.USTAR_FORMAT, linkname=target, owner="josé"
    )
    with _NON_UTF8_LOCALE, open_archive(io.BytesIO(data)) as ar:
        (member,) = ar.members()
        assert member.link_target == target
        assert member.uname == "josé"
        assert member.gname == "josé"


def test_invalid_utf8_name_is_surrogate_escaped_under_a_non_utf8_locale() -> None:
    data = _one_member_tar("café.txt", "latin-1", tarfile.USTAR_FORMAT)
    with _NON_UTF8_LOCALE, open_archive(io.BytesIO(data)) as ar:
        (member,) = ar.members()
        assert member.name == "caf\udce9.txt"
        assert member.raw_name == b"caf\xe9.txt"


@pytest.mark.parametrize(
    "fmt",
    [
        pytest.param(tarfile.USTAR_FORMAT, id="ustar"),
        pytest.param(tarfile.GNU_FORMAT, id="gnu-longname"),
    ],
)
def test_utf8_header_name_wins_over_the_caller_encoding(fmt: int) -> None:
    """``encoding=`` applies only to bytes that are not valid UTF-8, as in every
    other format: the UTF-8 bytes ``c3 a9`` stay ``é``, not ``Ã©``."""
    name = "café-" + "x" * (120 if fmt == tarfile.GNU_FORMAT else 0) + ".txt"
    data = _one_member_tar(name, "utf-8", fmt)
    with open_archive(io.BytesIO(data), encoding="latin-1") as ar:
        (member,) = ar.members()
        assert member.name == name
        assert member.raw_name == name.encode("utf-8")
    # The choice is reported, as ZIP reports it, naming the codec passed over.
    (diag,) = [
        d
        for d in member.diagnostics
        if d.code == DiagnosticCode.MEMBER_NAME_ENCODING_INFERRED
    ]
    assert diag.context.inferred_encoding == "utf-8"
    assert diag.context.declared_encoding == "latin-1"


def test_utf8_link_target_and_owner_win_over_the_caller_encoding() -> None:
    target = "цель/café.txt"
    data = _one_member_tar(
        "link", "utf-8", tarfile.USTAR_FORMAT, linkname=target, owner="josé"
    )
    with open_archive(io.BytesIO(data), encoding="latin-1") as ar:
        (member,) = ar.members()
        assert member.link_target == target
        assert member.uname == "josé"
        assert member.gname == "josé"


def test_caller_encoding_decodes_header_fields_that_are_not_utf8() -> None:
    data = _one_member_tar(
        "café.txt", "latin-1", tarfile.USTAR_FORMAT, linkname="à.txt", owner="josé"
    )
    with open_archive(io.BytesIO(data), encoding="latin-1") as ar:
        (member,) = ar.members()
        assert member.name == "café.txt"
        assert member.raw_name == b"caf\xe9.txt"
        assert member.link_target == "à.txt"
        assert member.uname == "josé"
        # encoding= decoded it: nothing was inferred.
        counts = ar.diagnostics.counts
    assert DiagnosticCode.MEMBER_NAME_ENCODING_INFERRED not in counts


def test_mixed_header_names_each_decode_by_their_own_bytes() -> None:
    """One archive with a UTF-8 name and a Latin-1 name: ``encoding="latin-1"``
    fixes the Latin-1 one and leaves the UTF-8 one alone."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as t:
        for raw in ("é-utf8.txt".encode(), "é-latin1.txt".encode("latin-1")):
            # tarfile writes str names; surrogateescape stores the exact bytes.
            info = tarfile.TarInfo(raw.decode("utf-8", "surrogateescape"))
            info.size = 0
            t.addfile(info)
    with open_archive(io.BytesIO(buf.getvalue()), encoding="latin-1") as ar:
        assert [m.name for m in ar.members()] == ["é-utf8.txt", "é-latin1.txt"]


def _binary_pax_tar(*, global_binary: bool) -> bytes:
    """A PAX archive whose member block says ``hdrcharset=BINARY`` and whose ``path``
    record holds the UTF-8 bytes of ``caé.txt``, optionally after a global header that
    says ``hdrcharset=BINARY`` too."""
    buf = io.BytesIO()
    with tarfile.open(
        fileobj=buf,
        mode="w",
        format=tarfile.PAX_FORMAT,
        encoding="utf-8",
        errors="surrogateescape",
        pax_headers={"hdrcharset": "BINARY"} if global_binary else None,
    ) as t:
        # A name UTF-8 cannot encode makes tarfile write hdrcharset=BINARY.
        info = tarfile.TarInfo("caf\udce9.txt")
        info.size = 0
        t.addfile(info)
    data = buf.getvalue()
    assert data.count(b"hdrcharset=BINARY") == (2 if global_binary else 1)
    # Same length, so the record's length prefix stays right: "caé" in UTF-8.
    return data.replace(b"caf\xe9.txt", b"ca\xc3\xa9.txt")


def test_binary_pax_path_that_is_valid_utf8_wins_over_the_caller_encoding() -> None:
    """``hdrcharset=BINARY`` declares no encoding, so the record decodes as a ustar
    name does: valid UTF-8 is UTF-8."""
    data = _binary_pax_tar(global_binary=False)
    with open_archive(io.BytesIO(data), encoding="latin-1") as ar:
        (member,) = ar.members()
        assert member.name == "caé.txt"
        assert member.raw_name == b"ca\xc3\xa9.txt"


def test_pax_member_repeating_a_global_binary_charset_keeps_the_codec_reading() -> None:
    """A known misread, pinned so it stays deliberate. ``pax_headers`` merges the
    global header into the member's, so a member block that repeats the global
    ``hdrcharset=BINARY`` looks like it inherited it. The record is then taken as one
    tarfile read as UTF-8: no UTF-8 reading replaces the codec's mojibake, and
    ``raw_name`` is that text encoded as UTF-8. Reading it the other way would misread
    a ``BINARY`` set only in the global header, which tarfile ignores."""
    data = _binary_pax_tar(global_binary=True)
    with open_archive(io.BytesIO(data), encoding="latin-1") as ar:
        (member,) = ar.members()
        assert member.name == "caÃ©.txt"
        assert member.raw_name == "caÃ©.txt".encode()


def _pax_tar_with_non_utf8_path(raw: bytes) -> bytes:
    """A PAX archive whose ``path`` record holds ``raw``, which is not UTF-8."""
    data = bytearray(_one_member_tar("café.txt", "utf-8", tarfile.PAX_FORMAT))
    # Swap the PAX path value for non-UTF-8 bytes of the same length.
    stored = "path=café.txt\n".encode()
    at = data.index(stored)
    data[at + 5 : at + len(stored) - 1] = raw
    return bytes(data)


def test_pax_raw_name_with_undecodable_bytes_round_trips() -> None:
    """Bytes that are not UTF-8 fall back to the archive codec with surrogateescape;
    the surrogates send the name back through that codec, recovering the bytes. The
    fallback codec is the UTF-8 default, not the locale's."""
    raw = b"caf\xe9\xe9.txt"
    with (
        _NON_UTF8_LOCALE,
        open_archive(io.BytesIO(_pax_tar_with_non_utf8_path(raw))) as ar,
    ):
        (member,) = ar.members()
        assert member.name == "caf\udce9\udce9.txt"
        assert member.raw_name == raw


def test_pax_path_that_is_not_utf8_falls_back_to_the_caller_encoding() -> None:
    raw = b"caf\xe9\xe9.txt"
    data = _pax_tar_with_non_utf8_path(raw)
    with open_archive(io.BytesIO(data), encoding="latin-1") as ar:
        (member,) = ar.members()
        assert member.name == "caféé.txt"
        # raw_name is not asserted: the UTF-8 bytes of "caféé.txt" decode to the same
        # name, so which bytes were stored cannot be recovered from it.


def test_close_releases_the_owned_stream_when_tarfile_close_raises(
    tmp_path: Path,
) -> None:
    path = tmp_path / "a.tar.gz"
    with tarfile.open(path, "w:gz") as t:
        info = tarfile.TarInfo("a")
        info.size = 1
        t.addfile(info, io.BytesIO(b"x"))
    ar: Any = open_archive(path)
    released: list[bool] = []
    real_release = ar._release_owned_stream

    def release() -> None:
        released.append(True)
        real_release()

    with (
        mock.patch.object(ar._tar, "close", side_effect=OSError("boom")),
        mock.patch.object(ar, "_release_owned_stream", side_effect=release),
    ):
        with pytest.raises(OSError):
            ar.close()
    assert released == [True]


def test_gnu_long_name_under_a_global_pax_path_keeps_the_archive_codec() -> None:
    """``pax_headers`` carries the archive's global headers, so an inherited global
    ``path`` must not make a GNU long name read as a PAX (UTF-8) name."""
    glob = io.BytesIO()
    with tarfile.open(
        fileobj=glob, mode="w", format=tarfile.PAX_FORMAT, pax_headers={"path": "g"}
    ):
        pass
    long_name = "é" * 120  # past ustar's 100 bytes: GNU writes a long-name block
    gnu = io.BytesIO()
    with tarfile.open(
        fileobj=gnu, mode="w", format=tarfile.GNU_FORMAT, encoding="latin-1"
    ) as t:
        info = tarfile.TarInfo(long_name)
        info.size = 1
        t.addfile(info, io.BytesIO(b"x"))
    # The global header's blocks, then the GNU member and its end-of-archive blocks.
    data = glob.getvalue().rstrip(b"\0")
    data += b"\0" * (-len(data) % 512) + gnu.getvalue()
    with open_archive(io.BytesIO(data), encoding="latin-1") as ar:
        (member,) = [m for m in ar.members() if m.name == long_name]
        assert member.raw_name == long_name.encode("latin-1")


def _tar_with_mtime(path: Path, mtime: float, tar_format: int) -> Path:
    with tarfile.open(path, "w", format=tar_format) as tf:
        info = tarfile.TarInfo("old.txt")
        info.mtime = mtime  # type: ignore[assignment]  # tarfile accepts a float
        tf.addfile(info, io.BytesIO(b""))
    return path


@pytest.mark.parametrize(
    ("tar_format", "mtime", "expected"),
    [
        # PAX writes a negative mtime as a "mtime" record; GNU as a base-256 field.
        pytest.param(
            tarfile.PAX_FORMAT,
            -86_400.5,
            datetime(1969, 12, 30, 23, 59, 59, 500_000, tzinfo=UTC),
            id="pax",
        ),
        pytest.param(
            tarfile.GNU_FORMAT,
            -86_400,
            datetime(1969, 12, 31, tzinfo=UTC),
            id="gnu-base256",
        ),
    ],
)
def test_pre_1970_mtime_lists_its_date(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tar_format: int,
    mtime: float,
    expected: datetime,
) -> None:
    """A negative mtime is a 1969 date on every platform.

    ``datetime.fromtimestamp`` goes through ``gmtime()`` on Windows, which rejects a
    negative value, so this member used to list as invalid there only. A
    ``fromtimestamp`` that raises the way Windows' does must not matter any more.
    """
    from archivey.internal import timestamps as timestamps_module

    class _WindowsLikeDatetime(datetime):
        @classmethod
        def fromtimestamp(cls, ts: float, tz: Any = None) -> datetime:
            if ts < 0:
                raise OSError(22, "Invalid argument (simulated Windows gmtime)")
            return datetime.fromtimestamp(ts, tz)

    monkeypatch.setattr(tar_reader_module, "datetime", _WindowsLikeDatetime)
    monkeypatch.setattr(timestamps_module, "datetime", _WindowsLikeDatetime)

    path = _tar_with_mtime(tmp_path / "old.tar", mtime, tar_format)
    if tar_format == tarfile.PAX_FORMAT:
        with tarfile.open(path) as tf:
            assert "mtime" in tf.getmembers()[0].pax_headers
    with open_archive(path) as ar:
        member = ar.get("old.txt")
        assert member.modified == expected
        assert DiagnosticCode.MEMBER_TIMESTAMP_INVALID not in ar.diagnostics.counts


def test_pre_1970_pax_atime_lists_its_date(tmp_path: Path) -> None:
    path = tmp_path / "atime.tar"
    with tarfile.open(path, "w", format=tarfile.PAX_FORMAT) as tf:
        info = tarfile.TarInfo("old.txt")
        info.pax_headers = {"atime": "-1.5"}
        tf.addfile(info, io.BytesIO(b""))
    with open_archive(path) as ar:
        assert ar.get("old.txt").accessed == datetime(
            1969, 12, 31, 23, 59, 58, 500_000, tzinfo=UTC
        )


@pytest.mark.parametrize(
    ("key", "attr"),
    [
        ("atime", "accessed"),
        ("ctime", "ctime"),
        ("LIBARCHIVE.creationtime", "created"),
        ("mtime", "modified"),
    ],
)
@pytest.mark.parametrize("raw", ["not-a-time", "1e30", "nan"])
def test_bad_pax_time_is_reported(
    tmp_path: Path, key: str, attr: str, raw: str
) -> None:
    # Every PAX time record that does not decode is reported the same way: the field
    # is None and MEMBER_TIMESTAMP_INVALID names the member field it would have
    # filled, as every format does. tarfile itself turns a PAX mtime
    # that is not a number into 0, which must not list as the Unix epoch.
    path = tmp_path / "bad_time.tar"
    with tarfile.open(path, "w", format=tarfile.PAX_FORMAT) as tf:
        info = tarfile.TarInfo("t.txt")
        info.mtime = 1_600_000_000
        info.pax_headers = {key: raw}
        tf.addfile(info, io.BytesIO(b""))
    with open_archive(path) as ar:
        member = ar.get("t.txt")
        assert getattr(member, attr) is None
        assert ar.diagnostics.counts[DiagnosticCode.MEMBER_TIMESTAMP_INVALID] == 1
        (diagnostic,) = member.diagnostics
        assert diagnostic.code is DiagnosticCode.MEMBER_TIMESTAMP_INVALID
        assert diagnostic.context.field == attr
        assert key in diagnostic.message


def test_several_bad_pax_times_on_one_member_are_each_reported(
    tmp_path: Path,
) -> None:
    path = tmp_path / "bad_times.tar"
    keys = ("atime", "ctime", "LIBARCHIVE.creationtime")
    with tarfile.open(path, "w", format=tarfile.PAX_FORMAT) as tf:
        info = tarfile.TarInfo("t.txt")
        info.pax_headers = dict.fromkeys(keys, "nan")
        tf.addfile(info, io.BytesIO(b""))
    with open_archive(path) as ar:
        member = ar.get("t.txt")
        assert (member.accessed, member.ctime, member.created) == (None, None, None)
        assert ar.diagnostics.counts[DiagnosticCode.MEMBER_TIMESTAMP_INVALID] == 3
        fields = sorted(d.context.field for d in member.diagnostics)
        assert fields == ["accessed", "created", "ctime"]


def test_pax_mtime_zero_is_the_epoch(tmp_path: Path) -> None:
    path = tmp_path / "epoch.tar"
    with tarfile.open(path, "w", format=tarfile.PAX_FORMAT) as tf:
        info = tarfile.TarInfo("t.txt")
        info.pax_headers = {"mtime": "0"}
        tf.addfile(info, io.BytesIO(b""))
    with open_archive(path) as ar:
        assert ar.get("t.txt").modified == datetime(1970, 1, 1, tzinfo=UTC)
        assert DiagnosticCode.MEMBER_TIMESTAMP_INVALID not in ar.diagnostics.counts


def _tar_hardlink_then_target() -> bytes:
    """``h`` is a hardlink to ``t``; the only ``t`` comes after it, and an earlier
    ``u`` that a later ``u`` replaces is linked from ``hu`` between the two."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        for name, data in (("u", b"FIRST-U"),):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
        for name, target in (("h", "t"), ("hu", "u")):
            link = tarfile.TarInfo(name)
            link.type = tarfile.LNKTYPE
            link.linkname = target
            t.addfile(link)
        for name, data in (("t", b"LATER-T"), ("u", b"LATER-U")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


@pytest.mark.parametrize("streaming", [False, True])
def test_hardlink_resolves_to_an_earlier_member_only(
    tmp_path: Path, streaming: bool
) -> None:
    """A TAR hardlink refers to a file archived before it: tarfile's own lookup
    searches only the members before the link and takes the last match, and
    ``tar(1)`` links to what it already wrote. So ``h -> t`` with ``t`` only later has
    no target, and ``hu -> u`` is the first ``u``, not the later one. Random access
    and a streaming pass agree."""
    from archivey import ExtractionStatus, OnError
    from archivey.exceptions import LinkTargetNotFoundError

    data = _tar_hardlink_then_target()
    with open_archive(io.BytesIO(data), streaming=streaming) as reader:
        report = reader.extract_all(tmp_path / "out", on_error=OnError.CONTINUE)
        listed = {m.name: m for m in reader.members_report().members}
    by_name = {r.member.name: r for r in report.results}
    assert listed["h"].link_target_member is None
    assert by_name["h"].status is ExtractionStatus.FAILED
    assert isinstance(by_name["h"].error, LinkTargetNotFoundError)
    assert listed["hu"].link_target_member is not None
    assert listed["hu"].link_target_member.member_id == 0
    assert (tmp_path / "out" / "hu").read_bytes() == b"FIRST-U"
    assert (tmp_path / "out" / "t").read_bytes() == b"LATER-T"
    if not streaming:
        with open_archive(io.BytesIO(data)) as reader:
            with pytest.raises(LinkTargetNotFoundError):
                reader.read("h")


@pytest.mark.parametrize("codec", ["gz", "bz2", "xz"])
def test_extract_compressed_tar_decodes_once(tmp_path: Path, codec: str) -> None:
    """``extract_all`` on a compressed tar reads it in one forward pass: the
    listing limits are enforced as members arrive rather than by listing the whole
    archive first, so no seek goes back and ``STREAM_REWIND_REDECOMPRESSES``, set to
    ``RAISE``, never fires. A 2 MiB member makes a rewind large enough to report."""
    import bz2
    import gzip
    import lzma

    from archivey.diagnostics import DiagnosticDisposition, DiagnosticPolicy

    payload = bytes(range(256)) * (8 * 1024)
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as t:
        for name in ("a.bin", "b.bin"):
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            t.addfile(info, io.BytesIO(payload))
        link = tarfile.TarInfo("h.bin")
        link.type = tarfile.LNKTYPE
        link.linkname = "a.bin"
        t.addfile(link)
    compress = {"gz": gzip.compress, "bz2": bz2.compress, "xz": lzma.compress}[codec]
    archive = tmp_path / f"x.tar.{codec}"
    archive.write_bytes(compress(raw.getvalue()))
    config = ArchiveyConfig(
        diagnostic_policy=DiagnosticPolicy(
            overrides={
                DiagnosticCode.STREAM_REWIND_REDECOMPRESSES: DiagnosticDisposition.RAISE
            }
        )
    )
    report = open_and_extract(archive, tmp_path / "out", config=config)
    assert len(report.results) == 3
    for name in ("a.bin", "b.bin", "h.bin"):
        assert (tmp_path / "out" / name).read_bytes() == payload
    assert DiagnosticCode.STREAM_REWIND_REDECOMPRESSES not in report.diagnostics.counts


def test_extract_enforces_listing_limits_as_members_arrive(tmp_path: Path) -> None:
    """Without listing first, ``max_members`` still refuses the extraction, at the
    member that crosses it, before that member is written."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as t:
        for i in range(5):
            info = tarfile.TarInfo(f"f{i}")
            info.size = 1
            t.addfile(info, io.BytesIO(b"x"))
    archive = tmp_path / "x.tar"
    archive.write_bytes(raw.getvalue())
    config = ArchiveyConfig(listing_limits=ListingLimits(max_members=3))
    with pytest.raises(ResourceLimitError, match="max_members"):
        open_and_extract(archive, tmp_path / "out", config=config)
    assert not (tmp_path / "out" / "f3").exists()


class _CountingRaw(io.RawIOBase):
    """A raw source that counts the bytes read from it; seekable or not."""

    def __init__(self, data: bytes, *, seekable: bool) -> None:
        self._inner = io.BytesIO(data)
        self._seekable = seekable
        self.read_bytes = 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        n = self._inner.readinto(buffer)
        self.read_bytes += n
        return n

    def seekable(self) -> bool:
        return self._seekable

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if not self._seekable:
            raise io.UnsupportedOperation("seek")
        return self._inner.seek(offset, whence)

    def tell(self) -> int:
        if not self._seekable:
            raise io.UnsupportedOperation("tell")
        return self._inner.tell()


@pytest.mark.parametrize("streaming", [False, True])
def test_a_pass_reads_no_member_data_the_consumer_does_not_reach(
    streaming: bool,
) -> None:
    """Skipping a member is lazy. A consumer that takes the first member and stops
    causes no read of that member's data area, in a streaming pass (whose skip reads
    through the data, but only before the next header) and in the random-access one
    pass. Taking the second member of a forward-only source reads through the first."""
    big = 4 * 2**20
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        for name, size in (("a", big), ("b", 10), ("c", 10)):
            info = tarfile.TarInfo(name)
            info.size = size
            t.addfile(info, io.BytesIO(b"x" * size))
    data = buf.getvalue()

    def reads_after(members_taken: int) -> int:
        raw = _CountingRaw(data, seekable=not streaming)
        with open_archive(
            io.BufferedReader(raw), format=ArchiveFormat.TAR, streaming=streaming
        ) as reader:
            it = iter(reader.stream_members())
            for _ in range(members_taken):
                next(it)
            it.close()  # type: ignore[attr-defined]
        return raw.read_bytes

    assert reads_after(1) < 1 * 2**20
    if streaming:
        assert reads_after(2) >= big
    else:
        assert reads_after(2) < 1 * 2**20  # random access seeks past the data
