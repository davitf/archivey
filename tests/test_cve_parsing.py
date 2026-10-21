"""Published header-parsing vulnerabilities in other TAR and ZIP readers, replayed
against archivey.

Each test builds the smallest archive with the header shape a CVE describes and checks
that archivey ends it with a typed error, or reads it the way the format defines,
without a hang or an allocation the archive's size does not pay for. A test names the
CVE it replays, so a failure points at the published write-up. The extraction-side
replays (traversal, links) are in ``test_cve_regressions.py``.
"""

from __future__ import annotations

import io
import struct
import tracemalloc
import zipfile

import pytest

from archivey import ArchiveyConfig, DiagnosticCode, ListingLimits, open_archive
from archivey.exceptions import (
    ArchiveyError,
    CorruptionError,
    ResourceLimitError,
    TruncatedError,
)
from tests.streams_util import NonSeekableBytesIO

# ``pipe`` reads a stream that cannot seek, so a ZIP is read by its local headers.
_MODES = pytest.mark.parametrize("mode", ["random", "stream", "pipe"])

BLOCK = 512
_END = b"\x00" * (2 * BLOCK)


def _open(data: bytes, mode: str, config: ArchiveyConfig | None = None):
    source = NonSeekableBytesIO(data) if mode == "pipe" else io.BytesIO(data)
    return open_archive(source, streaming=mode != "random", config=config)


def _read_everything(
    data: bytes, *, mode: str, config: ArchiveyConfig | None = None
) -> dict[str, bytes]:
    """Every member's name and data, the way a caller that reads it all gets them."""
    out: dict[str, bytes] = {}
    with _open(data, mode, config) as reader:
        for member, stream in reader.stream_members():
            out[member.name] = stream.read() if stream is not None else b""
    return out


# --- TAR -------------------------------------------------------------------------


def _octal(value: int, width: int) -> bytes:
    return f"{value:0{width - 1}o}".encode() + b"\x00"


def _tar_header(
    name: bytes = b"f",
    *,
    size: int | bytes = 0,
    typeflag: bytes = b"0",
    mtime: bytes | None = None,
) -> bytes:
    """One ustar header block; ``size`` and ``mtime`` as raw field bytes override the
    octal encoding, for the base-256 forms."""
    h = bytearray(BLOCK)
    h[0 : len(name)] = name
    h[100:108] = _octal(0o644, 8)
    h[108:116] = _octal(1000, 8)
    h[116:124] = _octal(1000, 8)
    h[124:136] = size if isinstance(size, bytes) else _octal(size, 12)
    h[136:148] = mtime if mtime is not None else _octal(1_700_000_000, 12)
    assert len(h) == BLOCK, "a field overflowed"
    h[156:157] = typeflag
    h[257:265] = b"ustar\x0000"
    h[148:156] = b" " * 8
    h[148:156] = f"{sum(h):06o}".encode() + b"\x00 "
    return bytes(h)


def _padded(payload: bytes) -> bytes:
    return payload + b"\x00" * (-len(payload) % BLOCK)


def _pax_record(key: bytes, value: bytes) -> bytes:
    payload = b" " + key + b"=" + value + b"\n"
    length = len(payload) + 1
    while length != len(str(length)) + len(payload):
        length = len(str(length)) + len(payload)
    return str(length).encode() + payload


def _pax(body: bytes, typeflag: bytes = b"x") -> bytes:
    return _tar_header(b"PaxHeader", size=len(body), typeflag=typeflag) + _padded(body)


def _file(name: bytes, data: bytes) -> bytes:
    return _tar_header(name, size=len(data)) + _padded(data)


@_MODES
def test_cve_2025_8194_negative_tar_size(mode: str) -> None:
    # tarfile read a base-256 size field as a negative number and moved its offset
    # back to a header it had read, looping forever. A negative size is damage here.
    minus_one = b"\xff" * 12
    data = _tar_header(b"loop", size=minus_one) + _file(b"after", b"x") + _END
    with pytest.raises(CorruptionError):
        _read_everything(data, mode=mode)


@_MODES
def test_cve_2025_8194_negative_pax_size(mode: str) -> None:
    # The same loop from a PAX ``size`` record, which tarfile also took as signed.
    data = (
        _pax(_pax_record(b"size", b"-1024"))
        + _file(b"loop", b"")
        + _file(b"after", b"x")
        + _END
    )
    with pytest.raises(CorruptionError):
        _read_everything(data, mode=mode)


@_MODES
def test_cve_2024_6232_pax_header_of_many_records_is_linear(mode: str) -> None:
    # tarfile parsed PAX records with backtracking regular expressions, so a header
    # of many short or padded records took quadratic time. A megabyte of records
    # (and one record padded by a megabyte of spaces) is read in one linear pass.
    many = _pax_record(b"comment", b"x") * 100_000
    padded = _pax_record(b"comment", b" " * 1_000_000)
    data = _pax(many) + _pax(padded) + _file(b"f", b"data") + _END
    assert _read_everything(data, mode=mode) == {"f": b"data"}


@_MODES
def test_cve_2024_6232_malformed_pax_length_is_damage(mode: str) -> None:
    # The same report's other input shape: a length prefix of many digits. The
    # record does not parse, so the header is refused at its first record.
    body = b"0" * 100_000 + b"1 comment=x\n"
    data = _pax(body) + _file(b"f", b"data") + _END
    with pytest.raises(CorruptionError):
        _read_everything(data, mode=mode)


@_MODES
def test_gnu_long_name_chain_is_charged_to_the_listing_limit(mode: str) -> None:
    # A chain of extended headers (GNU long names here) before one member. No header
    # in the chain is a member, so ``max_members`` does not count them; each one is
    # charged to ``max_metadata_bytes`` as it is read.
    name = b"n" * 1000
    chain = (
        _tar_header(b"././@LongLink", size=len(name), typeflag=b"L") + _padded(name)
    ) * 200
    data = chain + _file(b"f", b"data") + _END
    config = ArchiveyConfig(listing_limits=ListingLimits(max_metadata_bytes=64_000))
    with pytest.raises(ResourceLimitError):
        _read_everything(data, mode=mode, config=config)
    # Without the cap the chain reads as tarfile and GNU tar read it: the last long
    # name names the member.
    assert _read_everything(data, mode=mode) == {"n" * 1000: b"data"}


@_MODES
@pytest.mark.parametrize("typeflag", [b"x", b"g", b"L", b"K"])
def test_huge_extended_header_is_not_allocated(mode: str, typeflag: bytes) -> None:
    # An extended header declaring a terabyte (base-256 size) in a file of three
    # blocks. A reader that allocates the declared size up front fails on memory.
    # The size is charged to ``max_metadata_bytes`` before anything is read.
    terabyte = b"\x80" + (1 << 40).to_bytes(11, "big")
    data = _tar_header(b"PaxHeader", size=terabyte, typeflag=typeflag) + _END
    with pytest.raises(ResourceLimitError, match="extended header"):
        _read_everything(data, mode=mode)


@_MODES
@pytest.mark.parametrize(
    "mtime",
    [
        b"\x80" + b"\xff" * 11,
        b"\xff" * 12,
        b"\x80" + b"\x00" * 3 + b"\x7f" + b"\xff" * 7,
    ],
    ids=["huge", "minus-one", "past-year-9999"],
)
def test_cve_2025_25724_out_of_range_mtime(mode: str, mtime: bytes) -> None:
    # bsdtar's verbose listing did not check ``strftime`` for a time it could not
    # format, and used a buffer it had not filled. A time that no ``datetime`` holds lists the member with
    # no time instead of failing the listing. Minus one is a time (1969).
    data = _tar_header(b"f", size=4, mtime=mtime) + _padded(b"data") + _END
    with _open(data, mode) as reader:
        for member, stream in reader.stream_members():
            assert member.name == "f"
            assert stream is not None and stream.read() == b"data"
            if mtime == b"\xff" * 12:
                assert member.modified is not None and member.modified.year == 1969
            else:
                assert member.modified is None
        assert reader.diagnostics.counts.get(
            DiagnosticCode.MEMBER_TIMESTAMP_INVALID
        ) == (None if mtime == b"\xff" * 12 else 1)


# --- ZIP -------------------------------------------------------------------------


def _zip(*members: tuple[str, bytes], compress: int = zipfile.ZIP_STORED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=compress) as zf:
        for name, data in members:
            zf.writestr(name, data)
    return buf.getvalue()


def _eocd(data: bytes) -> int:
    return data.rindex(b"PK\x05\x06")


def _central_offsets(data: bytes) -> list[int]:
    """Where each central directory header starts."""
    eocd = _eocd(data)
    count, _size, start = struct.unpack_from("<HLL", data, eocd + 10)
    out = []
    pos = start
    for _ in range(count):
        out.append(pos)
        n, m, k = struct.unpack_from("<HHH", data, pos + 28)
        pos += 46 + n + m + k
    return out


@_MODES
def test_cve_2024_0450_entries_sharing_one_local_header(mode: str) -> None:
    # The overlapping-entry bomb (also unzip CVE-2019-13232): several directory
    # entries over one local header make one compressed kernel decode once per entry.
    # The second entry over the same bytes is refused as an overlap; a pass that
    # cannot seek refuses the directory's second pointer to one header.
    data = bytearray(
        _zip(("a", b"A" * 1000), ("b", b"B" * 1000), compress=zipfile.ZIP_DEFLATED)
    )
    second = _central_offsets(bytes(data))[1]
    struct.pack_into("<L", data, second + 42, 0)
    with pytest.raises(CorruptionError, match="(?i)overlap|already describes"):
        _read_everything(bytes(data), mode=mode)


@_MODES
def test_cve_2024_0450_entry_inside_another_entrys_data(mode: str) -> None:
    # The "quoted" form: a local header placed inside an earlier member's stored
    # data, with a directory entry for it. Reading the outer member's full size
    # would decode the inner one again.
    inner = _zip(("inner", b"I" * 100))
    inner_local = inner[: inner.index(b"PK\x01\x02")]
    data = bytearray(_zip(("outer", inner_local)))
    outer_data = data.index(inner_local)
    entries = _central_offsets(bytes(data))
    cd_entry = bytes(data[entries[0] : _eocd(bytes(data))])
    # A second directory entry: a copy of the inner member's, pointing at the copy of
    # its local header inside ``outer``.
    inner_cd = inner[inner.index(b"PK\x01\x02") : inner.index(b"PK\x05\x06")]
    inner_cd = inner_cd[:42] + struct.pack("<L", outer_data) + inner_cd[46:]
    cd_start = entries[0]
    new = bytes(data[:cd_start]) + cd_entry + inner_cd
    eocd = bytearray(data[_eocd(bytes(data)) :])
    struct.pack_into("<HHLL", eocd, 8, 2, 2, len(cd_entry) + len(inner_cd), cd_start)
    # A pass that cannot seek reads ``outer`` whole, so it never takes the inner
    # header as one, and refuses the directory entry that points into the data.
    expected = "no local header" if mode == "pipe" else "(?i)overlap"
    with pytest.raises(CorruptionError, match=expected):
        _read_everything(new + bytes(eocd), mode=mode)


def _zip64_record(cd_offset: int, cd_size: int, count: int) -> bytes:
    return struct.pack(
        "<4sQHHLLQQQQ",
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


def _zip64_decoy() -> bytes:
    """A ZIP whose ZIP64 locator names a decoy ZIP64 record inside a stored member's
    data, which in turn names a decoy central directory listing ``evil``. The real
    ZIP64 record, naming the real directory, sits right before the locator."""
    name = b"payload"
    data_at = 30 + len(name)  # the member's data starts right after its local header
    decoy = _zip(("evil", b"evil"))
    decoy_cd = decoy[decoy.index(b"PK\x01\x02") : decoy.index(b"PK\x05\x06")]
    payload = decoy_cd + _zip64_record(data_at, len(decoy_cd), 1)
    real = _zip((name.decode(), payload))
    assert real.index(payload) == data_at
    eocd_at = _eocd(real)
    count = struct.unpack_from("<H", real, eocd_at + 10)[0]
    cd_size, cd_offset = struct.unpack_from("<LL", real, eocd_at + 12)
    record = _zip64_record(cd_offset, cd_size, count)
    locator = struct.pack("<4sLQL", b"PK\x06\x07", 0, data_at + len(decoy_cd), 1)
    # A classic end record that defers every field to ZIP64.
    eocd = bytearray(real[eocd_at:])
    struct.pack_into("<HHHHLL", eocd, 4, 0, 0, 0xFFFF, 0xFFFF, 0xFFFFFFFF, 0xFFFFFFFF)
    return real[:eocd_at] + record + locator + bytes(eocd)


@_MODES
def test_cve_2025_8291_zip64_locator_pointing_elsewhere(mode: str) -> None:
    # zipfile trusted the ZIP64 locator's record offset without checking that the
    # record ends where the locator starts, so a locator could name a record (and,
    # through it, a central directory) hidden in member data, and zipfile listed
    # different members from other readers. A record that does not end at the
    # locator is not used.
    data = _zip64_decoy()
    with pytest.raises(CorruptionError, match="ZIP64 end of central directory"):
        _read_everything(data, mode=mode)


@_MODES
@pytest.mark.parametrize(
    "declared", [0xFFFF, 4], ids=["past-the-header", "past-the-area"]
)
def test_extra_field_longer_than_its_area(mode: str, declared: int) -> None:
    # Info-ZIP's extra-field bugs (CVE-2014-8139, -8140, -8141, -9636): a length
    # that runs past the extra area, or past the header, was read out of bounds.
    # Here the cut field keeps only the bytes inside the area, is kept as an
    # unparsed field, and gives no time.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        info = zipfile.ZipInfo("f")
        info.extra = struct.pack("<HH", 0x5455, declared)
        zf.writestr(info, b"data")
    data = buf.getvalue()
    with _open(data, mode) as reader:
        for member, stream in reader.stream_members():
            assert stream is not None and stream.read() == b"data"
            assert member.modified is not None and member.modified.year == 1980


@_MODES
def test_cve_2024_8088_name_of_many_slashes(mode: str) -> None:
    # zipfile.Path could loop forever building implied directories from crafted
    # names. Names of many slashes and many levels list in one pass.
    name = "a" + "/" * 60_000 + "b"
    data = _zip((name, b"x"), ("c/" * 20_000 + "d", b"y"))
    with _open(data, mode) as reader:
        assert len(list(reader.stream_members())) >= 2


@_MODES
def test_declared_size_smaller_than_the_data(mode: str) -> None:
    # libarchive CVE-2016-1541: a buffer sized from a ZIP header's declared size
    # overflowed when the data decoded to more. The declared size bounds the read
    # here, and more output than declared is damage.
    raw = b"Z" * 100_000
    data = bytearray(_zip(("f", raw), compress=zipfile.ZIP_DEFLATED))
    struct.pack_into("<L", data, 22, 10)
    struct.pack_into("<L", data, _central_offsets(bytes(data))[0] + 24, 10)
    with pytest.raises(CorruptionError, match="exceeds its declared size"):
        _read_everything(bytes(data), mode=mode)


@pytest.mark.parametrize("mode", ["stream", "pipe"])
@pytest.mark.parametrize("cut", [300, 512, 700, 1024, 1536, 2047])
def test_cve_2026_11972_truncated_tar_in_a_stream_ends(mode: str, cut: int) -> None:
    # tarfile in streaming mode (``r|``) mishandled the end of a cut archive and
    # looped. A TAR cut anywhere ends with a typed error or with the members before
    # the cut, in one pass.
    data = _file(b"a", b"x" * 600) + _END
    try:
        _read_everything(data[:cut], mode=mode)
    except ArchiveyError as exc:
        assert isinstance(exc, (TruncatedError, CorruptionError)), exc


@_MODES
@pytest.mark.parametrize(
    "method", [zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA], ids=["bzip2", "lzma"]
)
def test_cve_2026_15310_declared_size_is_not_preallocated(
    mode: str, method: int
) -> None:
    # zipfile sized an output buffer from the member's declared size for bzip2 and
    # LZMA members, so a small member declaring 4 GiB took 4 GiB. The declared size
    # here only bounds the output; the member's real data is all that is held.
    data = bytearray(_zip(("f", b"z" * 1000), compress=method))
    struct.pack_into("<L", data, 22, 0xFFFFFFFE)
    struct.pack_into("<L", data, _central_offsets(bytes(data))[0] + 24, 0xFFFFFFFE)
    tracemalloc.start()
    try:
        with pytest.raises(TruncatedError, match="ended after 1000 of"):
            _read_everything(bytes(data), mode=mode)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 64 << 20, peak
