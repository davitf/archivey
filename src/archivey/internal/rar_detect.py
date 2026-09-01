"""Cheap RAR main-header identity check for the SFX scan.

Seven or eight bytes of ``Rar!\\x1a\\x07`` in a stub are not a RAR. The cued
scan calls :func:`validate_rar_main_header` with a candidate-relative view.
"""

from __future__ import annotations

import struct
import zlib
from collections.abc import Callable

from archivey.internal.backends.rar_parser import RAR5_ID, RAR_ID
from archivey.internal.sfx import HitOutcome

# RAR3 MAIN block type and the 7-byte block header (crc16, type, flags, size).
_RAR3_MAIN = 0x73
_RAR3_BLK_HDR = struct.Struct("<HBHH")
# MAIN has 6 reserved bytes after the block header; LONG_BLOCK inserts a 4-byte
# add_size before those (same walk as the parser); ENCRYPTVER adds one more
# before the CRC coverage ends. Real MAIN headers are tens of bytes.
_RAR3_MAIN_MIN = _RAR3_BLK_HDR.size + 6
_RAR3_HEADER_CAP = 64 * 1024
_RAR3_MAIN_ENCRYPTVER = 0x0200
_RAR3_LONG_BLOCK = 0x8000

# RAR5 MAIN / ENCRYPTION (encrypted-headers archive: first block is ENCRYPTION).
_RAR5_MAIN = 1
_RAR5_ENCRYPTION = 4
# Identity only — a MAIN/ENCRYPTION header is small. Cap so a hostile vint
# cannot force a 2 MiB peek (the F1 analogue).
_RAR5_HEADER_CAP = 64 * 1024


def _crc32(data: bytes | memoryview) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def _load_vint(buf: bytes, pos: int) -> tuple[int, int] | None:
    """RAR5 vint, or ``None`` if truncated / too long."""
    length = len(buf)
    if pos >= length:
        return None
    b = buf[pos]
    if b < 0x80:
        return b, pos + 1
    limit = pos + 11
    if limit > length:
        limit = length
    res = b & 0x7F
    ofs = 7
    pos += 1
    while pos < limit:
        b = buf[pos]
        res += (b & 0x7F) << ofs
        pos += 1
        ofs += 7
        if b < 0x80:
            return res, pos
    return None


def validate_rar_main_header(peek_more: Callable[[int], bytes]) -> HitOutcome:
    """Return whether a candidate origin looks like a RAR 4 or RAR 5 archive.

    ``peek_more(n)`` is a view relative to the candidate. A truncated or
    non-RAR magic is :attr:`HitOutcome.NOT_THIS_FORMAT`. RAR 5 requires the
    first block's CRC32 to match; a plausible header whose CRC fails is
    :attr:`HitOutcome.DAMAGED`. RAR 4 requires a parseable MAIN block (type
    ``0x73``) with a matching 16-bit header CRC.
    """
    head = peek_more(len(RAR5_ID))
    if head.startswith(RAR5_ID):
        return _validate_rar5(peek_more)
    if head.startswith(RAR_ID):
        return _validate_rar3(peek_more)
    return HitOutcome.NOT_THIS_FORMAT


def _validate_rar5(peek_more: Callable[[int], bytes]) -> HitOutcome:
    # Marker + CRC + a generous vint prefix, then the declared body.
    prefix = peek_more(len(RAR5_ID) + 4 + 16)
    if len(prefix) < len(RAR5_ID) + 5:
        return HitOutcome.NOT_THIS_FORMAT
    body = prefix[len(RAR5_ID) :]
    loaded = _load_vint(body, 4)
    if loaded is None:
        return HitOutcome.NOT_THIS_FORMAT
    hdrlen, pos = loaded
    if hdrlen > _RAR5_HEADER_CAP:
        return HitOutcome.NOT_THIS_FORMAT
    header_size = pos + hdrlen
    hdata = peek_more(len(RAR5_ID) + header_size)[len(RAR5_ID) :]
    if len(hdata) < header_size:
        return HitOutcome.NOT_THIS_FORMAT
    header_crc = int.from_bytes(hdata[:4], "little")
    if header_crc != _crc32(memoryview(hdata)[4:]):
        return HitOutcome.DAMAGED
    block = _load_vint(hdata, pos)
    if block is None:
        return HitOutcome.NOT_THIS_FORMAT
    block_type, _pos = block
    if block_type not in (_RAR5_MAIN, _RAR5_ENCRYPTION):
        return HitOutcome.NOT_THIS_FORMAT
    return HitOutcome.VALID


def _validate_rar3(peek_more: Callable[[int], bytes]) -> HitOutcome:
    start = peek_more(len(RAR_ID) + _RAR3_BLK_HDR.size)
    if len(start) < len(RAR_ID) + _RAR3_BLK_HDR.size:
        return HitOutcome.NOT_THIS_FORMAT
    buf = start[len(RAR_ID) :]
    header_crc, block_type, flags, header_size = _RAR3_BLK_HDR.unpack_from(buf)
    if block_type != _RAR3_MAIN:
        return HitOutcome.NOT_THIS_FORMAT
    if header_size < _RAR3_MAIN_MIN or header_size > _RAR3_HEADER_CAP:
        return HitOutcome.NOT_THIS_FORMAT
    hdata = peek_more(len(RAR_ID) + header_size)[len(RAR_ID) :]
    if len(hdata) < header_size:
        return HitOutcome.NOT_THIS_FORMAT
    # Mirror rar_parser._parse_rar3's MAIN CRC coverage, including LONG_BLOCK.
    crc_pos = _RAR3_BLK_HDR.size
    if flags & _RAR3_LONG_BLOCK:
        crc_pos += 4
    crc_pos += 6
    if flags & _RAR3_MAIN_ENCRYPTVER:
        crc_pos += 1
    if crc_pos > header_size:
        return HitOutcome.NOT_THIS_FORMAT
    calc = _crc32(hdata[2:crc_pos]) & 0xFFFF
    if header_crc != calc:
        return HitOutcome.DAMAGED
    return HitOutcome.VALID
