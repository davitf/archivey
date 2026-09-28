"""Audit reproducers: TAR and single-file compressed streams.

Each test asserts the promised behaviour and is marked ``xfail(strict=True)`` with the
bug it reproduces, so the file stays green until a fix lands and then flags the marker
for removal. The promise each test checks is named in its docstring.
"""

from __future__ import annotations

import io
import signal
import sys
import tarfile
import tracemalloc
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from archivey import (
    AcceleratorMode,
    ArchiveFormat,
    ArchiveyConfig,
    ListingLimits,
    open_archive,
)
from archivey.diagnostics import DiagnosticCode
from archivey.exceptions import ArchiveyError, CorruptionError, ResourceLimitError
from tests.conftest import requires, requires_zstd

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _octal(value: int, width: int) -> bytes:
    return b"%0*o\0" % (width - 1, value)


def _base256(value: int, width: int) -> bytes:
    """GNU base-256: two's complement big-endian, top bit of the first byte set."""
    raw = bytearray((value % (1 << (8 * width))).to_bytes(width, "big"))
    raw[0] = 0x80 if value >= 0 else 0xFF
    return bytes(raw)


def _numeric(value: int, width: int) -> bytes:
    if 0 <= value < 8 ** (width - 1):
        return _octal(value, width)
    return _base256(value, width)


def _fix_checksum(header: bytearray) -> bytearray:
    block = bytes(header[:512])
    total = sum(block[:148]) + 8 * 32 + sum(block[156:512])
    header[148:156] = b"%06o\0 " % total
    return header


def _member(name: str, data: bytes, **fields: object) -> bytes:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    for key, value in fields.items():
        setattr(info, key, value)
    fmt = tarfile.PAX_FORMAT if info.pax_headers else tarfile.GNU_FORMAT
    return info.tobuf(format=fmt) + data + b"\0" * (-len(data) % 512)


def _patched(member: bytes, offset: int, value: bytes) -> bytes:
    """``member`` (one header + data) with a raw header field overwritten."""
    header = bytearray(member[:512])
    header[offset : offset + len(value)] = value
    return bytes(_fix_checksum(header)) + member[512:]


_TRAILER = b"\0" * 1024


def _gnu_sparse(
    name: str, entries: list[tuple[int, int]], realsize: int, packed: bytes
) -> bytes:
    """An old-GNU ``S`` member: up to four ``(offset, numbytes)`` map entries in the
    header, ``realsize`` as the logical size and ``packed`` as the stored data."""
    info = tarfile.TarInfo(name)
    info.size = len(packed)
    info.type = tarfile.GNUTYPE_SPARSE
    header = bytearray(info.tobuf(format=tarfile.GNU_FORMAT))
    for i, (offset, numbytes) in enumerate(entries):
        pos = 386 + 24 * i
        header[pos : pos + 12] = _numeric(offset, 12)
        header[pos + 12 : pos + 24] = _numeric(numbytes, 12)
    header[483:495] = _numeric(realsize, 12)
    return bytes(_fix_checksum(header)) + packed + b"\0" * (-len(packed) % 512)


class _OverBudget(BaseException):
    """Raised by the watchdog; a BaseException so no library catch can swallow it."""


@contextmanager
def _watchdog(seconds: float) -> Iterator[None]:
    """Interrupt the body after ``seconds`` of wall time (POSIX only)."""

    def _fire(signum: int, frame: object) -> None:
        raise _OverBudget(f"did not finish within {seconds} s")

    previous = signal.signal(signal.SIGALRM, _fire)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _drain(ar) -> None:
    for _member, stream in ar.stream_members():
        if stream is not None:
            stream.read()


# ---------------------------------------------------------------------------
# TAR: a size field the archive picks drives the streaming skip loop
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="SIGALRM watchdog")
def test_streaming_tar_huge_declared_size_fails_fast() -> None:
    """A forward-only walk must not do work proportional to a size the archive
    declares but does not contain (``streaming=True`` is the O(1) escape hatch in
    threat-model O1; the archive holds ~2 KiB). tarfile's ``_Stream.seek`` loops
    ``size // bufsize`` times reading empty chunks at EOF. Measured: a legal ustar
    octal size of 8 GiB costs 1.4 s (5.6 s as .tar.gz), 2**38 costs 43 s (180 s)."""
    first = _patched(_member("a", b"x" * 10), 124, _base256(2**45, 12))
    data = first + _member("b", b"y") + _TRAILER
    with _watchdog(1.5), pytest.raises(ArchiveyError):
        with open_archive(io.BytesIO(data), streaming=True) as ar:
            # List without reading: a consumer that skips a member is what makes
            # tarfile seek over it. (Reading it hits EOF at once and is typed.)
            for _member_, _stream in ar.stream_members():
                pass


# ---------------------------------------------------------------------------
# TAR: a huge size field in random-access mode leaks the seek's own error
# ---------------------------------------------------------------------------


def _huge_size_tar() -> bytes:
    first = _member("a", b"x" * 10, pax_headers={"size": str(2**63)})
    return first + _member("b", b"y") + _TRAILER


@pytest.mark.parametrize("source_kind", ["bytesio", "path", "tar.xz"])
def test_random_access_tar_huge_declared_size_is_typed(
    source_kind: str, tmp_path: Path
) -> None:
    """error-handling: 'Single rooted archive exception hierarchy' — archive content
    must not surface as a builtin exception."""
    import lzma

    data = _huge_size_tar()
    fmt = None
    source: object = io.BytesIO(data)
    if source_kind == "path":
        path = tmp_path / "huge.tar"
        path.write_bytes(data)
        source = path
    elif source_kind == "tar.xz":
        source = io.BytesIO(lzma.compress(data))
        fmt = ArchiveFormat.TAR_XZ
    with pytest.raises(ArchiveyError):
        with open_archive(source, format=fmt) as ar:
            ar.members()


# ---------------------------------------------------------------------------
# TAR: GNU sparse PAX records tarfile parses with a bare int()
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize(
    "pax",
    [
        {"GNU.sparse.map": "0,x"},  # PAX sparse 0.1
        {"GNU.sparse.size": "1e3"},  # PAX sparse 0.0 / 0.1 size
        {"GNU.sparse.realsize": "nan"},  # PAX sparse 1.0 size
    ],
    ids=["map", "size", "realsize"],
)
def test_gnu_sparse_pax_record_not_an_integer_is_corruption(
    pax: dict[str, str], streaming: bool
) -> None:
    """error-handling: a malformed header value is CorruptionError, not ValueError."""
    data = _member("a", b"x" * 10, pax_headers=pax) + _TRAILER
    with pytest.raises(CorruptionError):
        with open_archive(io.BytesIO(data), streaming=streaming) as ar:
            _drain(ar)


@pytest.mark.parametrize("streaming", [False, True])
def test_gnu_sparse_1_0_map_without_newline_is_corruption(streaming: bool) -> None:
    """error-handling: a malformed sparse map is CorruptionError, not ValueError."""
    pax = {
        "GNU.sparse.major": "1",
        "GNU.sparse.minor": "0",
        "GNU.sparse.name": "f",
        "GNU.sparse.realsize": "10",
    }
    data = _member("GNUSparseFile.0/f", b"no newline here", pax_headers=pax) + _TRAILER
    with pytest.raises(CorruptionError):
        with open_archive(io.BytesIO(data), streaming=streaming) as ar:
            _drain(ar)


# ---------------------------------------------------------------------------
# TAR: mode field outside what stat.S_IMODE accepts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("mode", [-1, 2**40], ids=["negative", "over-32-bit"])
def test_tar_mode_out_of_range_does_not_escape(mode: int, streaming: bool) -> None:
    """error-handling: listing never raises a builtin for a header value; the
    mtime field already degrades a hostile value (MEMBER_TIMESTAMP_INVALID)."""
    data = _patched(_member("a", b"x"), 100, _base256(mode, 8)) + _TRAILER
    try:
        with open_archive(io.BytesIO(data), streaming=streaming) as ar:
            _drain(ar)
    except ArchiveyError:
        pass


# ---------------------------------------------------------------------------
# TAR: sparse maps
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case",
    ["negative-numbytes", "map-past-packed-data"],
)
def test_streaming_sparse_map_backward_seek_is_typed(case: str) -> None:
    """error-handling: tarfile.StreamError is a tarfile error about the archive's
    layout and must surface as an ArchiveyError (TarReader._translate_exception maps
    only ReadError and EOFError)."""
    if case == "negative-numbytes":
        sparse = _gnu_sparse("a", [(0, 10), (100, -5), (200, 10)], 300, b"A" * 30)
    else:
        sparse = _gnu_sparse("a", [(0, 1024)], 1024, b"A" * 512)
    data = sparse + _member("b", b"SECRET") + _TRAILER
    with pytest.raises(ArchiveyError):
        with open_archive(io.BytesIO(data), streaming=True) as ar:
            _drain(ar)


def test_sparse_map_past_packed_data_is_not_served_silently() -> None:
    """A member's bytes come from its own data area; reading past it into the next
    header is damage (tar(1) refuses such a map). Random access returns 1024 bytes
    of which the last 512 are member ``b``'s header."""
    data = (
        _gnu_sparse("a", [(0, 1024)], 1024, b"A" * 512)
        + _member("b", b"SECRET")
        + _TRAILER
    )
    with open_archive(io.BytesIO(data)) as ar:
        with pytest.raises(CorruptionError):
            with ar.open("a") as stream:
                stream.read()


def _sparse_1_0_tar(entries: int) -> bytes:
    body = b"%d\n" % entries + b"0\n0\n" * entries
    pax = {
        "GNU.sparse.major": "1",
        "GNU.sparse.minor": "0",
        "GNU.sparse.name": "f",
        "GNU.sparse.realsize": "0",
    }
    return _member("GNUSparseFile.0/f", body, pax_headers=pax) + _TRAILER


def test_sparse_1_0_map_is_weighed_against_max_metadata_bytes() -> None:
    """threat-model O1: listing-time retained metadata is budgeted by
    ``max_metadata_bytes``. Here 30 000 map entries (120 KB of tar, ~200 bytes of
    .tar.gz) are retained as ~2 MB of tuples against a 64 KiB cap. At a million
    entries a 4 KB .tar.gz retains 64 MB and takes ~2.5 s per member to list."""
    data = _sparse_1_0_tar(30_000)
    config = ArchiveyConfig(listing_limits=ListingLimits(max_metadata_bytes=64 * 1024))
    tracemalloc.start()
    try:
        with open_archive(io.BytesIO(data), config=config) as ar:
            try:
                members = ar.members()
            except ResourceLimitError:
                return
            retained, _peak = tracemalloc.get_traced_memory()
            assert members[0].is_sparse
    finally:
        tracemalloc.stop()
    assert retained < 1_000_000, f"listing retained {retained} bytes past a 64 KiB cap"


# ---------------------------------------------------------------------------
# bzip2 accelerator: untranslated rapidgzip errors
# ---------------------------------------------------------------------------


@requires("rapidgzip")
@pytest.mark.parametrize(
    "prefix",
    [b"BZh\x00", b"\xf26oUW"],
    ids=["bad-blocksize-byte", "junk-before-magic"],
)
def test_bzip2_accelerator_header_errors_are_typed(prefix: bytes) -> None:
    """compressed-streams: 'Returned streams translate decompression errors' and
    'An accelerator preserves the error contract of the path it replaces' (the
    stdlib path raises CorruptionError/TruncatedError on the same bytes)."""
    import bz2

    good = bz2.compress(bytes(range(256)) * 64)
    # Either the block-size byte replaced, or five junk bytes before the magic.
    data = prefix + good[4:] if prefix.startswith(b"BZh") else prefix + good
    config = ArchiveyConfig(use_indexed_bzip2=AcceleratorMode.ON)
    with pytest.raises(ArchiveyError):
        with open_archive(
            io.BytesIO(data),
            format=ArchiveFormat.BZ2,
            seekable_members=True,
            config=config,
        ) as ar:
            with ar.open(ar.members()[0]) as stream:
                stream.read()


# ---------------------------------------------------------------------------
# unix-compress: dictionary memory proportional to output
# ---------------------------------------------------------------------------


def _lzw_run_bomb(codes: int) -> bytes:
    """A ``.Z`` stream (16-bit, no block mode) whose every code is the KwKwK case,
    so dictionary entry ``k`` is ``k`` bytes of ``a``: ``codes`` codes decode to
    ``codes * (codes + 1) / 2`` bytes."""
    out = bytearray(b"\x1f\x9d\x10")
    width, in_era, bits, nbits = 9, 0, 0, 0
    for code in [97] + [255 + i for i in range(1, codes)]:
        bits |= code << nbits
        nbits += width
        in_era += 1
        while nbits >= 8:
            out.append(bits & 0xFF)
            bits >>= 8
            nbits -= 8
        if in_era >= 1 << (width - 1) and width < 16:
            width, in_era = width + 1, 0
    if nbits:
        out.append(bits & 0xFF)
    return bytes(out)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "AUDIT: LzwState stores each dictionary entry as its full expansion, so a "
        "~130 KB .Z holds ~2 GiB resident while read in small chunks (8 KB -> 18 MB)"
    ),
)
def test_unix_compress_dictionary_memory_is_bounded() -> None:
    """A chunked read of a stream must not retain memory proportional to the output
    (DecoderLimits: a decoder's working set is what the cap is for; LZW's needs only
    a 64 Ki-entry prefix/suffix table). 6 000 codes (~8 KB) decode 18 MB of ``a``;
    the decoder holds all of it in its dictionary while 64 KiB reads are served."""
    codes = 6_000
    data = _lzw_run_bomb(codes)
    tracemalloc.start()
    try:
        total = 0
        with open_archive(io.BytesIO(data), format=ArchiveFormat.Z) as ar:
            with ar.open(ar.members()[0]) as stream:
                while chunk := stream.read(65536):
                    total += len(chunk)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert total == codes * (codes + 1) // 2
    assert peak < 8_000_000, f"peak {peak} bytes for 64 KiB reads"


# ---------------------------------------------------------------------------
# TAR: extended-header chains recurse in tarfile
# ---------------------------------------------------------------------------


def _extended_header_chain(kind: bytes, count: int) -> bytes:
    """``count`` back-to-back GNU long-name (``L`` / ``K``) or PAX (``x``) headers
    ahead of one ordinary member. tarfile handles each by calling ``fromtarfile``
    again from inside the previous one, so the chain is a recursion."""
    out = bytearray()
    for i in range(count):
        if kind == tarfile.XHDTYPE:
            record = b"path=p%03d\n" % (i % 1000)
            body = b"%d %s" % (len(record) + 3, record)
            info = tarfile.TarInfo("pax")
            fmt = tarfile.USTAR_FORMAT
        else:
            body = b"n%d\0" % i
            info = tarfile.TarInfo("././@LongLink")
            fmt = tarfile.GNU_FORMAT
        info.type = kind
        info.size = len(body)
        out += info.tobuf(format=fmt) + body + b"\0" * (-len(body) % 512)
    return bytes(out) + _member("f", b"data") + _TRAILER


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize(
    "kind",
    [tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK, tarfile.XHDTYPE],
    ids=["L", "K", "x"],
)
def test_extended_header_chain_does_not_raise_recursion_error(
    kind: bytes, streaming: bool
) -> None:
    """error-handling: archive content must not surface as a builtin exception. A
    1 000-header chain is 1 MB of tar and about 3 KB as .tar.gz."""
    data = _extended_header_chain(kind, 1000)
    escaped: str | None = None
    try:
        with open_archive(io.BytesIO(data), streaming=streaming) as ar:
            _drain(ar)
    except ArchiveyError:
        pass
    except RecursionError as exc:
        # Reported without the exception attached: pytest rendering a
        # thousand-frame traceback takes the better part of a minute.
        escaped = repr(exc)
    assert escaped is None, f"raw exception escaped: {escaped}"


# ---------------------------------------------------------------------------
# Compressed TAR: a stream checksum reached in the post-trailer scan is dropped
# ---------------------------------------------------------------------------


def _stored_codec(codec: str, raw: bytes) -> bytes:
    """``raw`` compressed so that incompressible bytes are stored verbatim, with the
    codec's whole-stream checksum on."""
    if codec == "gz":
        import gzip

        return gzip.compress(raw, compresslevel=0)
    if codec == "zst":
        from tests.conftest import zstd_backend

        zstd = zstd_backend()
        return zstd.compress(raw, options={zstd.CompressionParameter.checksum_flag: 1})
    import lz4.frame

    return lz4.frame.compress(raw, content_checksum=True)


_CODEC_FORMATS = {
    "gz": ArchiveFormat.TAR_GZ,
    "zst": ArchiveFormat.TAR_ZST,
    "lz4": ArchiveFormat.TAR_LZ4,
}


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("padding", [64 * 1024, 2 * 2**20], ids=["64KiB", "2MiB"])
@pytest.mark.parametrize(
    "codec",
    [
        "gz",
        pytest.param("zst", marks=requires_zstd()),
        pytest.param("lz4", marks=requires("lz4")),
    ],
)
def test_compressed_tar_stream_checksum_after_trailer_is_not_dropped(
    codec: str, padding: int, streaming: bool
) -> None:
    """compressed-streams: 'Decompressed output digests are verified at clean EOF'.
    One flipped byte in a stored member body decodes cleanly, so only the stream
    checksum can see it. With a ``tar -b128`` record (64 KiB of zero padding after
    the trailer) the checksum is reached inside the trailing scan, which must raise
    it (_StreamChecksumError) rather than take it for a tail that failed to decode.
    Past the scan's 1 MiB bound it is never reached, and DIGEST_UNVERIFIABLE says so;
    the same archive without the flipped byte reports the same, and raises nothing."""
    import random

    payload = random.Random(1).randbytes(20_000)
    tar = io.BytesIO()
    with tarfile.open(fileobj=tar, mode="w") as t:
        info = tarfile.TarInfo("a")
        info.size = len(payload)
        t.addfile(info, io.BytesIO(payload))
    clean = _stored_codec(codec, tar.getvalue() + b"\0" * padding)
    compressed = bytearray(clean)
    at = compressed.find(payload[1000:1100])
    assert at >= 0, "fixture premise: the member body is stored verbatim"
    compressed[at + 50] ^= 0x01

    def _drained(data: bytes) -> set[DiagnosticCode]:
        with open_archive(
            io.BytesIO(data), format=_CODEC_FORMATS[codec], streaming=streaming
        ) as ar:
            _drain(ar)
            return set(ar.diagnostics.counts)

    if padding <= 1 * 2**20:
        with pytest.raises(CorruptionError):
            _drained(bytes(compressed))
        assert DiagnosticCode.DIGEST_UNVERIFIABLE not in _drained(clean)
    else:
        assert DiagnosticCode.DIGEST_UNVERIFIABLE in _drained(bytes(compressed))
        assert DiagnosticCode.DIGEST_UNVERIFIABLE in _drained(clean)
