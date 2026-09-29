"""Cross-format audit: a protection one backend applies and another skips.

Each test asserts the promised behaviour and is marked ``xfail(strict=True)`` with the
gap it pins, so a fix turns it into an XPASS failure and the marker has to go. The
per-backend audits cover each format in depth; these are the cells of the protection
matrix where formats disagree with each other or with the published docs.
"""

from __future__ import annotations

import bz2
import io
import lzma
import os
import stat
import struct
import subprocess
import tarfile
import tracemalloc
import zipfile
import zlib
from collections.abc import Callable
from pathlib import Path

import pytest

from archivey import open_archive
from archivey.cli.exit_codes import EXIT_FAIL
from archivey.cli.main import main
from archivey.config import ArchiveyConfig, ListingLimits
from archivey.diagnostics import DiagnosticCode
from archivey.exceptions import ArchiveyUsageError, ResourceLimitError
from tests.conftest import requires, requires_binary

_RAR_FIXTURES = Path(__file__).parent / "fixtures" / "rar"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _vint(buf: bytes | bytearray, pos: int) -> tuple[int, int]:
    """Decode one RAR5 vint at ``pos``; return ``(value, next_pos)``."""
    value = shift = 0
    while True:
        byte = buf[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return value, pos


def _encode_vint(value: int, width: int) -> bytes:
    """Encode ``value`` as a RAR5 vint of exactly ``width`` bytes (padded)."""
    out = bytearray()
    for i in range(width):
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if i < width - 1 else 0))
    assert value == 0
    return bytes(out)


def _rar5_first_file_header(buf: bytearray) -> tuple[int, int, int, int]:
    """``(block_pos, fields_pos, header_end, extra_size)`` of the first FILE header."""
    pos = 8  # RAR5 signature
    while True:
        header_size, start = _vint(buf, pos + 4)
        p = start
        block_type, p = _vint(buf, p)
        block_flags, p = _vint(buf, p)
        extra_size = data_size = 0
        if block_flags & 0x01:
            extra_size, p = _vint(buf, p)
        if block_flags & 0x02:
            data_size, p = _vint(buf, p)
        if block_type == 2:
            return pos, p, start + header_size, extra_size
        pos = start + header_size + data_size


def _rar5_fix_crc(buf: bytearray, block_pos: int, header_end: int) -> None:
    struct.pack_into(
        "<I", buf, block_pos, zlib.crc32(bytes(buf[block_pos + 4 : header_end]))
    )


def _rar4_patch_first_file_header(
    buf: bytearray, patch: Callable[[bytearray, int], None], name: bytes | None = None
) -> None:
    """Apply ``patch(buf, header_pos)`` to a RAR4 FILE header, then fix its CRC16."""
    pos = 7  # RAR4 marker block
    while pos < len(buf):
        _crc, head_type, flags, head_size = struct.unpack_from("<HBHH", buf, pos)
        add = struct.unpack_from("<I", buf, pos + 7)[0] if flags & 0x8000 else 0
        if head_type == 0x74:
            name_size = struct.unpack_from("<H", buf, pos + 26)[0]
            stored = bytes(buf[pos + 32 : pos + 32 + name_size])
            if name is None or stored == name:
                patch(buf, pos)
                crc = zlib.crc32(bytes(buf[pos + 2 : pos + head_size])) & 0xFFFF
                struct.pack_into("<H", buf, pos, crc)
                return
        pos += head_size + add
    raise AssertionError("no matching RAR4 file header")


# ---------------------------------------------------------------------------
# CLI `archivey test` — "full-read integrity check"
# ---------------------------------------------------------------------------


def _zip_with_damaged_symlink(path: Path) -> None:
    link = zipfile.ZipInfo("link")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("target.txt", "hello")
        zf.writestr(link, "target.txt")
    data = bytearray(path.read_bytes())
    second_local = data.find(b"PK\x03\x04", 10)
    link_data = second_local + 30 + len("link")
    assert bytes(data[link_data : link_data + 10]) == b"target.txt"
    data[link_data] = ord("T")  # stored CRC no longer matches
    path.write_bytes(bytes(data))


def _7z_with_damaged_symlink(path: Path) -> None:
    src = path.parent / "src7z"
    src.mkdir()
    (src / "target.txt").write_bytes(b"hi\n")
    os.symlink("target.txt", src / "link")
    subprocess.run(
        ["7z", "a", "-snl", "-mx0", "-bd", "-bso0", str(path), "link"],
        cwd=src,
        check=True,
    )
    data = bytearray(path.read_bytes())
    at = data.find(b"target.txt")
    assert at > 0
    data[at] = ord("T")
    path.write_bytes(bytes(data))


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(_zip_with_damaged_symlink, id="zip"),
        pytest.param(
            _7z_with_damaged_symlink,
            id="7z",
            marks=[
                requires_binary("7z"),
                pytest.mark.skipif(os.name == "nt", reason="needs POSIX symlinks"),
            ],
        ),
    ],
)
def test_cli_test_verifies_data_stored_symlink_targets(
    build: Callable[[Path], None], tmp_path: Path
) -> None:
    ext = "zip" if build is _zip_with_damaged_symlink else "7z"
    archive = tmp_path / f"damaged.{ext}"
    build(archive)
    # Precondition: extraction sees the damage.
    assert main(["x", str(archive), "-d", str(tmp_path / "out")]) == EXIT_FAIL
    # The promise: `test` is a full-read integrity check.
    assert main(["test", str(archive)]) == EXIT_FAIL


# ---------------------------------------------------------------------------
# MEMBER_TIMESTAMP_INVALID — every backend with a stored date reports it
# ---------------------------------------------------------------------------


def _rar4_month_13(tmp_path: Path) -> Path:
    data = bytearray((_RAR_FIXTURES / "basic_nonsolid__rar4.rar").read_bytes())

    def patch(buf: bytearray, pos: int) -> None:
        ftime = struct.unpack_from("<I", buf, pos + 20)[0]
        struct.pack_into("<I", buf, pos + 20, (ftime & ~(0xF << 21)) | (13 << 21))

    _rar4_patch_first_file_header(data, patch, name=b"file1.txt")
    out = tmp_path / "month13.rar"
    out.write_bytes(bytes(data))
    return out


def _rar5_filetime_overflow(tmp_path: Path) -> Path:
    data = bytearray((_RAR_FIXTURES / "basic_nonsolid__.rar").read_bytes())
    block_pos, _fields, header_end, extra_size = _rar5_first_file_header(data)
    pos = header_end - extra_size
    while pos < header_end:
        size, q = _vint(data, pos)
        end = q + size
        rec_type, q = _vint(data, q)
        if rec_type == 3:  # HTIME
            # Unix mtime + ns (4 + 4 bytes) becomes one 8-byte FILETIME of the same
            # length, set past datetime's range.
            assert data[q] == 0x13
            data[q] = 0x02
            data[q + 1 : q + 9] = b"\xff" * 8
            break
        pos = end
    else:
        raise AssertionError("no HTIME record")
    _rar5_fix_crc(data, block_pos, header_end)
    out = tmp_path / "filetime.rar"
    out.write_bytes(bytes(data))
    return out


def _iso_month_13(tmp_path: Path) -> Path:
    import pycdlib

    iso = pycdlib.PyCdlib()
    iso.new(interchange_level=3)  # plain ISO 9660: the record date is the only date
    payload = b"hello\n"
    iso.add_fp(io.BytesIO(payload), len(payload), "/F1.TXT;1")
    buf = io.BytesIO()
    iso.write_fp(buf)
    iso.close()
    data = bytearray(buf.getvalue())
    at = data.find(b"F1.TXT;1")
    record = at - 33
    assert data[record + 32] == len(b"F1.TXT;1")
    data[record + 19] = 13  # recording date: month
    out = tmp_path / "month13.iso"
    out.write_bytes(bytes(data))
    return out


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(_rar4_month_13, id="rar4-dos-month-13"),
        pytest.param(_rar5_filetime_overflow, id="rar5-filetime-overflow"),
        pytest.param(_iso_month_13, id="iso-month-13", marks=requires("pycdlib")),
    ],
)
def test_invalid_timestamp_is_none_and_reported(
    build: Callable[[Path], Path], tmp_path: Path
) -> None:
    archive = build(tmp_path)
    with open_archive(archive) as reader:
        member = next(m for m in reader.members() if m.is_file)
        counts = reader.diagnostics.counts
    assert member.modified is None
    assert counts.get(DiagnosticCode.MEMBER_TIMESTAMP_INVALID, 0) == 1


# ---------------------------------------------------------------------------
# Solid access: stream_members() on a compressed TAR decodes the stream twice
# ---------------------------------------------------------------------------


def _compressed_tar(tmp_path: Path, codec: str) -> Path:
    payload = bytes(range(256)) * (8 * 1024)  # 2 MiB, compresses quickly
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tf:
        info = tarfile.TarInfo("member.bin")
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))
    compress = {"gz": zlib_gzip, "bz2": bz2.compress, "xz": lzma.compress}[codec]
    out = tmp_path / f"a.tar.{codec}"
    out.write_bytes(compress(raw.getvalue()))
    return out


def zlib_gzip(data: bytes) -> bytes:
    import gzip

    return gzip.compress(data, compresslevel=1)


@pytest.mark.parametrize("codec", ["gz", "bz2", "xz"])
def test_compressed_tar_stream_members_decodes_once(tmp_path: Path, codec: str) -> None:
    archive = _compressed_tar(tmp_path, codec)
    with open_archive(archive) as reader:
        for _member, stream in reader.stream_members():
            if stream is not None:
                stream.read()
        counts = reader.diagnostics.counts
    assert DiagnosticCode.STREAM_REWIND_REDECOMPRESSES not in counts


# ---------------------------------------------------------------------------
# ListingLimits.max_metadata_bytes — a single compressed PAX header
# ---------------------------------------------------------------------------


def test_tar_pax_header_costs_the_metadata_cap_not_the_header(tmp_path: Path) -> None:
    value_size = 8 * 2**20
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.PAX_FORMAT) as tf:
        info = tarfile.TarInfo("m")
        info.pax_headers = {"comment": "a" * value_size}
        tf.addfile(info)
    archive = tmp_path / "pax.tar.bz2"
    archive.write_bytes(bz2.compress(raw.getvalue()))
    assert archive.stat().st_size < 1024

    cap = 64 * 1024
    config = ArchiveyConfig(listing_limits=ListingLimits(max_metadata_bytes=cap))
    tracemalloc.start()
    try:
        with pytest.raises(ResourceLimitError):
            with open_archive(archive, config=config) as reader:
                reader.members()
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    # "an over-limit tar costs the cap rather than the archive, PAX keywords and
    # values included" (threat model O1). Generous slack: 1/4 of the value.
    assert peak < value_size // 4, f"peak {peak} bytes for a {cap}-byte cap"


# ---------------------------------------------------------------------------
# One live member stream — 7z and solid RAR passes bypass the gate
# ---------------------------------------------------------------------------


def _solid_7z(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_bytes(b"alpha " * 200)
    (src / "b.txt").write_bytes(b"bravo " * 200)
    out = tmp_path / "solid.7z"
    subprocess.run(
        ["7z", "a", "-ms=on", "-bd", "-bso0", str(out), "a.txt", "b.txt"],
        cwd=src,
        check=True,
    )
    return out


def _solid_rar(tmp_path: Path) -> Path:
    return _RAR_FIXTURES / "basic_solid__.rar"


def _nonsolid_zip(tmp_path: Path) -> Path:
    out = tmp_path / "a.zip"
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr("a.txt", b"alpha " * 200)
        zf.writestr("b.txt", b"bravo " * 200)
    return out


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(_nonsolid_zip, id="zip-control"),
        pytest.param(_solid_7z, id="7z", marks=requires_binary("7z")),
        pytest.param(_solid_rar, id="rar-solid", marks=requires_binary("unrar")),
    ],
)
def test_stream_members_refused_while_a_member_stream_is_live(
    build: Callable[[Path], Path], tmp_path: Path
) -> None:
    archive = build(tmp_path)
    with open_archive(archive) as reader:
        first = next(m for m in reader.members() if m.is_file and m.size)
        with reader.open(first) as live:
            live.read(1)
            with pytest.raises(ArchiveyUsageError):
                for _member, stream in reader.stream_members():
                    if stream is not None:
                        stream.read(1)


# ---------------------------------------------------------------------------
# DecoderLimits.max_decoder_memory — RAR dictionary size is never checked
# ---------------------------------------------------------------------------


@requires_binary("rar", "unrar")
@pytest.mark.xfail(
    strict=True,
    reason=(
        "AUDIT: the RAR5 dictionary size the header declares (up to 4 GiB) is never "
        "checked against DecoderLimits.max_decoder_memory before unrar decodes"
    ),
)
def test_rar_declared_dictionary_is_checked_against_decoder_memory(
    tmp_path: Path,
) -> None:
    src = tmp_path / "f.txt"
    src.write_bytes(os.urandom(64 * 1024).hex().encode())
    archive = tmp_path / "d.rar"
    subprocess.run(
        ["rar", "a", "-idq", "-m5", "-ep", str(archive), str(src)], check=True
    )
    data = bytearray(archive.read_bytes())
    block_pos, p, header_end, _extra = _rar5_first_file_header(data)
    file_flags, p = _vint(data, p)
    _unpacked, p = _vint(data, p)
    _attrs, p = _vint(data, p)
    if file_flags & 0x02:
        p += 4
    if file_flags & 0x04:
        p += 4
    ci_pos = p
    comp_info, p = _vint(data, p)
    assert (comp_info >> 7) & 7, "member must be compressed to reach unrar"
    # Dictionary exponent 15: 128 KiB << 15 = 4 GiB, over the 2 GiB default.
    comp_info = (comp_info & ~(0x1F << 10) & ~(0x1F << 15)) | (15 << 10)
    data[ci_pos:p] = _encode_vint(comp_info, p - ci_pos)
    _rar5_fix_crc(data, block_pos, header_end)
    archive.write_bytes(bytes(data))

    with open_archive(archive) as reader:
        with pytest.raises(ResourceLimitError):
            reader.read("f.txt")
