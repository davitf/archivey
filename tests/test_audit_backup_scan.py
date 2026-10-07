"""Reproducers for defects found by `scripts/scan_archives.py` over a real backup tree.

The scan dry-ran 57 390 archives; `dev-docs/investigations/2026-10-backup-scan.md` has
the counts and the clusters these come from. The inputs here are synthetic: each one is
built from the structure that the real files shared, never from their content.

Each test asserts the behaviour a fix should give and is marked ``xfail(strict=True)``
while the defect stands; the ``reason`` names it. Remove the marker when the fix lands.
"""

from __future__ import annotations

import io
import lzma
import struct
import zlib

import pytest

import archivey
from archivey import FormatDetectionError

# --- LZMA Alone: zero runs decode as an endless stream of zero literals. -------------
#
# The Alone probe checks the 13-byte header, then decodes a sample and requires output.
# A range coder fed zero bytes decodes zero literals without error, so any header that
# passes the gate and is followed by a zero run is accepted. Random blobs, which the spec's
# "0 in 20 000" measurement used, never contain such runs; real files often do.

_SYNCSAFE_MASK = 0x7F


def _syncsafe(n: int) -> bytes:
    return bytes((n >> shift) & _SYNCSAFE_MASK for shift in (21, 14, 7, 0))


def _id3v23_mp3_with_padding() -> bytes:
    """An MP3 whose ID3v2.3 tag holds only padding, followed by MPEG audio frames.

    ``ID3`` reads as Alone properties 0x49 (lc=1, lp=3, pb=1, legal), ``D3 03 00`` as a
    209 732-byte dictionary, and the zero padding as range-coder data. The scan found 55
    such MP3s (and some ``.jar`` and ``.data`` files) reported as ``raw_stream.lzma``.
    """
    padding = b"\0" * 2048
    tag = b"ID3\x03\x00\x00" + _syncsafe(len(padding)) + padding
    # An MPEG-1 Layer III frame header, then frame data that is not zero.
    audio = bytes([0xFF, 0xFB, 0x90, 0x64]) + bytes(range(256)) * 64
    return tag + audio


_OLE_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")


@pytest.mark.xfail(
    strict=True,
    reason=(
        "LZMA Alone probe accepts a header followed by a zero run: an ID3v2.3 tag "
        "with padding is detected as LZMA_ALONE (PROBABLE)"
    ),
)
def test_id3_tagged_mp3_is_not_lzma_alone() -> None:
    with pytest.raises(FormatDetectionError):
        archivey.detect_format(io.BytesIO(_id3v23_mp3_with_padding()))


@pytest.mark.xfail(
    strict=True,
    reason=(
        "LZMA Alone probe accepts a header followed by a zero run: the OLE compound "
        "file magic then zeros is detected as LZMA_ALONE (PROBABLE)"
    ),
)
def test_ole_magic_then_zeros_is_not_lzma_alone() -> None:
    # 0xD0 is legal Alone properties (lc=1, lp=3, pb=4). OLE files (.doc, .xls, .msi,
    # Thumbs.db) are sector-aligned and often hold long zero runs early on.
    data = _OLE_MAGIC + b"\0" * 4088
    with pytest.raises(FormatDetectionError):
        archivey.detect_format(io.BytesIO(data))


def test_zero_run_after_an_alone_header_decodes_without_error() -> None:
    # Pins the mechanism the two tests above rely on, so a liblzma change that starts
    # rejecting it shows up here rather than as a mysterious XPASS.
    header = bytes.fromhex("5d00001000ffffffffffffffff")
    out = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE).decompress(
        header + b"\0" * 4096, max_length=1 << 16
    )
    assert out == b"\0" * (1 << 16)


# --- Brotli: the residual false positive (open-issues P12), on OLE files. ----------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "open-issues P12 residual: a standard OLE header followed by zeros is "
        "detected as BROTLI (GUESS); the scan found 437 such files, mostly OLE"
    ),
)
def test_ole_header_then_zeros_is_not_brotli() -> None:
    # The first 32 bytes every version-3 compound file starts with: magic, a zero CLSID,
    # minor version 0x3E, major version 3, byte-order mark 0xFFFE, sector shift 9.
    header = _OLE_MAGIC + b"\0" * 16 + struct.pack("<HHHH", 0x3E, 3, 0xFFFE, 9)
    data = header + b"\0" * (256 * 1024 - len(header))
    with pytest.raises(FormatDetectionError):
        archivey.detect_format(io.BytesIO(data))


# --- ZIP: a local-header name that disagrees with the central directory. -----------


def _zip_with_local_name(central: bytes, local: bytes, data: bytes) -> bytes:
    """One stored member whose local header spells its name differently."""
    crc = zlib.crc32(data)
    dos_date = 0x21  # 1980-01-01
    lfh = (
        struct.pack(
            "<4sHHHHHIIIHH",
            b"PK\x03\x04",
            20,
            0,
            0,
            0,
            dos_date,
            crc,
            len(data),
            len(data),
            len(local),
            0,
        )
        + local
        + data
    )
    cdh = (
        struct.pack(
            "<4sHHHHHHIIIHHHHHII",
            b"PK\x01\x02",
            20,
            20,
            0,
            0,
            0,
            dos_date,
            crc,
            len(data),
            len(data),
            len(central),
            0,
            0,
            0,
            0,
            0,
            0,
        )
        + central
    )
    eocd = struct.pack("<4sHHHHIIH", b"PK\x05\x06", 0, 0, 1, 1, len(cdh), len(lfh), 0)
    return lfh + cdh + eocd


@pytest.mark.xfail(
    strict=True,
    reason=(
        "ZIP member refused when its local-header name differs from the central "
        "directory's; unzip warns and uses the central name, 7-Zip reads it. "
        "Needs a maintainer decision: dev-docs/formats/zip.md documents the check"
    ),
)
def test_local_name_in_another_codepage_still_reads() -> None:
    # Seen in the scan: the central directory holds 0xF4 (ô in Latin-1/cp1252) and the
    # local header 0x93 (ô in cp437/cp850), both without the UTF-8 flag. Archivey takes
    # every name from the central directory, so the local copy decides nothing else.
    blob = _zip_with_local_name(b"Patag\xf4nia.mp3", b"Patag\x93nia.mp3", b"payload")
    with archivey.open_archive(io.BytesIO(blob)) as ar:
        (member,) = ar.members()
        with ar.open(member) as stream:
            assert stream.read() == b"payload"
