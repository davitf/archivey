"""Cheap 7z signature-header identity check for the SFX scan.

Six bytes of ``7z\\xbc\\xaf\\x1c`` in a stub are not a 7z. The cued scan calls
:func:`validate_sevenzip_signature_header` with a candidate-relative view.
"""

from __future__ import annotations

import struct
import zlib
from collections.abc import Callable

from archivey.internal.backends.sevenzip_parser import MAGIC_7Z
from archivey.internal.sfx import SFX_MAX, HitOutcome

_SIGNATURE_HEADER_SIZE = 32
# Same cap the parser uses before seeking/allocating a next-header. Real headers
# are kilobytes; tens of MiB is already past any legitimate archive.
_MAX_NEXT_HEADER_SIZE = 64 << 20
# 7z writes major version 0. Anything else is not a signature we can confirm.
_MAJOR_VERSION = 0


def _crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def validate_sevenzip_signature_header(peek_more: Callable[[int], bytes]) -> HitOutcome:
    """Return whether a candidate origin looks like a 7z signature header.

    ``peek_more(n)`` is a view relative to the candidate, not the source. A
    truncated or non-7z magic is :attr:`HitOutcome.NOT_THIS_FORMAT`. A 7z
    magic whose ``StartHeaderCRC`` fails is :attr:`HitOutcome.DAMAGED` — identity
    held, structure did not. The scan treats that like ``NOT_THIS_FORMAT``
    (skip, continue); the evidence-ledger scheduler may later report it.

    ``offset + 32 + NextHeaderOffset + NextHeaderSize`` must fall at or before
    the end of the *view*. A declared end past :data:`~archivey.internal.sfx.SFX_MAX`
    is a real archive whose next header sits outside the scan window — CRC
    passing is enough; do not grow the prefix to chase it (the F1 analogue).
    Exact-EOF vs trailing bytes is not a reject: some SFX tools append
    configuration after the payload. Earliest CRC-valid hit wins; a 32-bit CRC
    decoy in front of a real archive is the intra-format case the scan already
    skips.
    """
    header = peek_more(_SIGNATURE_HEADER_SIZE)
    if len(header) < _SIGNATURE_HEADER_SIZE or header[: len(MAGIC_7Z)] != MAGIC_7Z:
        return HitOutcome.NOT_THIS_FORMAT
    major_version = header[6]
    if major_version != _MAJOR_VERSION:
        return HitOutcome.NOT_THIS_FORMAT
    start_header_crc = int.from_bytes(header[8:12], "little")
    start_header = header[12:32]
    if _crc32(start_header) != start_header_crc:
        return HitOutcome.DAMAGED
    next_header_offset, next_header_size, _next_header_crc = struct.unpack(
        "<QQI", start_header
    )
    if next_header_size > _MAX_NEXT_HEADER_SIZE:
        return HitOutcome.NOT_THIS_FORMAT
    declared = _SIGNATURE_HEADER_SIZE + next_header_offset + next_header_size
    if declared < _SIGNATURE_HEADER_SIZE:
        return HitOutcome.NOT_THIS_FORMAT
    # Only confirm the declared end when it sits inside the scan window. A
    # next-header past SFX_MAX is a large archive, not an overrun we can see.
    if declared <= SFX_MAX:
        got = peek_more(declared)
        if len(got) < declared:
            return HitOutcome.NOT_THIS_FORMAT
    return HitOutcome.VALID
