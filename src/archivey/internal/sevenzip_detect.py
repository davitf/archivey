"""Cheap 7z signature-header identity check for the SFX scan.

Six bytes of ``7z\\xbc\\xaf\\x1c`` in a stub are not a 7z. The cued scan calls
:func:`validate_sevenzip_signature_header` with a candidate-relative view and
the known remaining length from that origin.
"""

from __future__ import annotations

import struct
from collections.abc import Callable

from archivey.internal.backends.sevenzip_parser import (
    _MAX_NEXT_HEADER_SIZE,
    _SIGNATURE_HEADER_SIZE,
    MAGIC_7Z,
    _crc32,
)
from archivey.internal.sfx import HitOutcome

# 7z writes major version 0. Anything else is not a signature we can confirm.
_MAJOR_VERSION = 0


def validate_sevenzip_signature_header(
    peek_more: Callable[[int], bytes],
    remaining: int | None = None,
) -> HitOutcome:
    """Return whether a candidate origin looks like a 7z signature header.

    ``peek_more(n)`` is a view relative to the candidate, not the source.
    ``remaining`` is provable bytes from the candidate to source EOF, or
    ``None`` if unknown. A truncated or non-7z magic is
    :attr:`HitOutcome.NOT_THIS_FORMAT`. After ``StartHeaderCRC`` matches,
    identity has held: an oversized next-header or a declared end past
    ``remaining`` is :attr:`HitOutcome.DAMAGED`. When ``remaining`` is
    unknown, CRC passing is enough — do not peek the declared end (that
    would grow the prefix to the whole archive).

    An empty next-header (``NextHeaderSize == 0``) is not an SFX payload —
    nobody ships a self-extractor with no files — so that is
    :attr:`HitOutcome.NOT_THIS_FORMAT` even when the CRC is valid.

    When ``remaining`` is known and equals the declared end, the outcome is
    :attr:`HitOutcome.VALID_EXACT` ("this 7z ends at EOF"), not a ranking
    score. Trailing bytes after a CRC-valid header stay ``VALID``; the scan
    prefers a later ``VALID_EXACT`` over an earlier ``VALID``.
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
    if next_header_size == 0:
        return HitOutcome.NOT_THIS_FORMAT
    if next_header_size > _MAX_NEXT_HEADER_SIZE:
        return HitOutcome.DAMAGED
    declared = _SIGNATURE_HEADER_SIZE + next_header_offset + next_header_size
    if remaining is not None and declared > remaining:
        return HitOutcome.DAMAGED
    if remaining is not None and declared == remaining:
        return HitOutcome.VALID_EXACT
    return HitOutcome.VALID
