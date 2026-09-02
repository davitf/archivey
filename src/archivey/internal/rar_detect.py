"""Cheap RAR main-header identity check for the SFX scan.

Seven or eight bytes of ``Rar!\\x1a\\x07`` in a stub are not a RAR. The cued
scan calls :func:`validate_rar_main_header` with a candidate-relative view.
"""

from __future__ import annotations

from collections.abc import Callable

from archivey.exceptions import CorruptionError
from archivey.internal.backends.rar_parser import (
    _RAR3_MAIN,
    _S_BLK_HDR,
    RAR5_ID,
    RAR_ID,
    _crc32,
    _load_vint,
    _rar3_main_crc_end,
)
from archivey.internal.sfx import HitOutcome

# MAIN has 6 reserved bytes after the block header. Real MAIN headers are tens of bytes.
_RAR3_MAIN_MIN = _S_BLK_HDR.size + 6
_RAR3_HEADER_CAP = 64 * 1024

# RAR5 MAIN / ENCRYPTION (encrypted-headers archive: first block is ENCRYPTION).
_RAR5_MAIN = 1
_RAR5_ENCRYPTION = 4
# Identity only — a MAIN/ENCRYPTION header is small. Cap so a hostile vint
# cannot force a 2 MiB peek.
_RAR5_HEADER_CAP = 64 * 1024


def _try_vint(buf: bytes, pos: int) -> tuple[int, int] | None:
    """Parser vint, or ``None`` if truncated / too long (does not raise)."""
    try:
        return _load_vint(buf, pos)
    except CorruptionError:
        return None


def validate_rar_main_header(
    peek_more: Callable[[int], bytes],
    remaining: int | None = None,
) -> HitOutcome:
    """Return whether a candidate origin looks like a RAR 4 or RAR 5 archive.

    ``peek_more(n)`` is a view relative to the candidate. ``remaining`` is
    unused: a RAR identity check never needs the source length. A truncated
    or non-RAR magic is :attr:`HitOutcome.NOT_THIS_FORMAT`. RAR 5 requires
    the first block's CRC32 to match; a plausible header whose CRC fails is
    :attr:`HitOutcome.DAMAGED`. RAR 4 requires a parseable MAIN block (type
    ``0x73``) with a matching 16-bit header CRC.
    """
    del remaining
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
    loaded = _try_vint(body, 4)
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
    block = _try_vint(hdata, pos)
    if block is None:
        return HitOutcome.NOT_THIS_FORMAT
    block_type, _pos = block
    if block_type not in (_RAR5_MAIN, _RAR5_ENCRYPTION):
        return HitOutcome.NOT_THIS_FORMAT
    return HitOutcome.VALID


def _validate_rar3(peek_more: Callable[[int], bytes]) -> HitOutcome:
    start = peek_more(len(RAR_ID) + _S_BLK_HDR.size)
    if len(start) < len(RAR_ID) + _S_BLK_HDR.size:
        return HitOutcome.NOT_THIS_FORMAT
    buf = start[len(RAR_ID) :]
    header_crc, block_type, flags, header_size = _S_BLK_HDR.unpack_from(buf)
    if block_type != _RAR3_MAIN:
        return HitOutcome.NOT_THIS_FORMAT
    if header_size < _RAR3_MAIN_MIN or header_size > _RAR3_HEADER_CAP:
        return HitOutcome.NOT_THIS_FORMAT
    hdata = peek_more(len(RAR_ID) + header_size)[len(RAR_ID) :]
    if len(hdata) < header_size:
        return HitOutcome.NOT_THIS_FORMAT
    crc_pos = _rar3_main_crc_end(flags)
    if crc_pos > header_size:
        return HitOutcome.NOT_THIS_FORMAT
    calc = _crc32(hdata[2:crc_pos]) & 0xFFFF
    if header_crc != calc:
        return HitOutcome.DAMAGED
    return HitOutcome.VALID
