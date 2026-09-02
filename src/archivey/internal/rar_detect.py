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
    load_vint,
    rar3_main_crc_end,
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


def validate_rar_main_header(
    peek_more: Callable[[int], bytes],
    remaining: int | None,
) -> HitOutcome:
    """Return whether a candidate origin looks like a RAR 4 or RAR 5 archive.

    ``peek_more(n)`` is a view relative to the candidate. ``remaining`` is
    provable bytes from the candidate to source EOF, or ``None`` if unknown.
    A truncated or non-RAR magic is :attr:`HitOutcome.NOT_THIS_FORMAT`. RAR 5
    requires the first block's CRC32 to match; a plausible header whose CRC
    fails is :attr:`HitOutcome.DAMAGED`. RAR 4 requires a parseable MAIN block
    (type ``0x73``) with a matching 16-bit header CRC.

    ``remaining`` distinguishes a genuine overrun from a scan-window clamp:
    once the declared header size fits in ``remaining``, a short ``peek_more``
    is not ``NOT_THIS_FORMAT``. ``peek_more`` stays outside the parse ``try``
    so a workspace ``OSError`` propagates. A truncated vint before the CRC is
    ``NOT_THIS_FORMAT``; after the CRC has matched, a later vint failure is
    ``DAMAGED``.
    """
    head = peek_more(len(RAR5_ID))
    if head.startswith(RAR5_ID):
        return _validate_rar5(peek_more, remaining)
    if head.startswith(RAR_ID):
        return _validate_rar3(peek_more, remaining)
    return HitOutcome.NOT_THIS_FORMAT


def _header_in_hand(got: int, needed: int, remaining: int | None) -> HitOutcome | None:
    """``None`` if ``got`` covers ``needed``; otherwise the outcome for a short peek.

    A known ``remaining`` that already covers ``needed`` means the bytes exist
    past a scan-window clamp, so a short peek is :attr:`HitOutcome.VALID`.
    """
    if got >= needed:
        return None
    if remaining is not None and needed <= remaining:
        return HitOutcome.VALID
    return HitOutcome.NOT_THIS_FORMAT


def _validate_rar5(
    peek_more: Callable[[int], bytes], remaining: int | None
) -> HitOutcome:
    # Marker + CRC + a generous vint prefix, then the declared body.
    prefix = peek_more(len(RAR5_ID) + 4 + 16)
    if len(prefix) < len(RAR5_ID) + 5:
        return HitOutcome.NOT_THIS_FORMAT
    body = prefix[len(RAR5_ID) :]
    try:
        hdrlen, pos = load_vint(body, 4)
    except CorruptionError:
        return HitOutcome.NOT_THIS_FORMAT
    if hdrlen > _RAR5_HEADER_CAP:
        return HitOutcome.NOT_THIS_FORMAT
    header_size = pos + hdrlen
    needed = len(RAR5_ID) + header_size
    if remaining is not None and needed > remaining:
        return HitOutcome.NOT_THIS_FORMAT
    hdata = peek_more(needed)[len(RAR5_ID) :]
    short = _header_in_hand(len(hdata), header_size, remaining)
    if short is not None:
        return short
    identity_held = False
    try:
        header_crc = int.from_bytes(hdata[:4], "little")
        if header_crc != _crc32(memoryview(hdata)[4:]):
            return HitOutcome.DAMAGED
        identity_held = True
        block_type, _pos = load_vint(hdata, pos)
        if block_type not in (_RAR5_MAIN, _RAR5_ENCRYPTION):
            return HitOutcome.NOT_THIS_FORMAT
        return HitOutcome.VALID
    except CorruptionError:
        return HitOutcome.DAMAGED if identity_held else HitOutcome.NOT_THIS_FORMAT


def _validate_rar3(
    peek_more: Callable[[int], bytes], remaining: int | None
) -> HitOutcome:
    start = peek_more(len(RAR_ID) + _S_BLK_HDR.size)
    if len(start) < len(RAR_ID) + _S_BLK_HDR.size:
        return HitOutcome.NOT_THIS_FORMAT
    buf = start[len(RAR_ID) :]
    header_crc, block_type, flags, header_size = _S_BLK_HDR.unpack_from(buf)
    if block_type != _RAR3_MAIN:
        return HitOutcome.NOT_THIS_FORMAT
    if header_size < _RAR3_MAIN_MIN or header_size > _RAR3_HEADER_CAP:
        return HitOutcome.NOT_THIS_FORMAT
    needed = len(RAR_ID) + header_size
    if remaining is not None and needed > remaining:
        return HitOutcome.NOT_THIS_FORMAT
    hdata = peek_more(needed)[len(RAR_ID) :]
    short = _header_in_hand(len(hdata), header_size, remaining)
    if short is not None:
        return short
    crc_pos = rar3_main_crc_end(flags)
    if crc_pos > header_size:
        return HitOutcome.NOT_THIS_FORMAT
    calc = _crc32(hdata[2:crc_pos]) & 0xFFFF
    if header_crc != calc:
        return HitOutcome.DAMAGED
    return HitOutcome.VALID
