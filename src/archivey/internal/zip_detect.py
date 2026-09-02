"""Cheap ZIP local-header identity check for the SFX scan.

Four bytes of ``PK\\x03\\x04`` in a stub are not a ZIP. The cued scan calls
:func:`validate_zip_local_header` with a candidate-relative view; EOCD plus
central-directory confirmation is the tail probe's job, not this check.
"""

from __future__ import annotations

import struct
from collections.abc import Callable

from archivey.internal.sfx import HitOutcome

_LOCAL_HEADER_MAGIC = b"PK\x03\x04"
_LOCAL_HEADER_SIZE = 30

# APPNOTE 6.3.10 tops out at version 6.3 (63). 1.0 (10) is the floor any writer
# produces; 0 is the zero-padded-stub decoy. 99 is a slack ceiling so a slightly
# newer writer is not mistaken for a decoy; random bytes after ``PK\\x03\\x04``
# (e.g. ``0x90`` filler) land well above it.
_VERSION_NEEDED_MIN = 10
_VERSION_NEEDED_MAX = 99

# Unused / PKWARE-reserved general-purpose flag bits: 7–10, 12, 14, 15.
_GP_RESERVED_MASK = 0xD780

# APPNOTE compression-method ids plus WinZip AES (99). Unknown ids fail the
# cheap check; a method this library cannot decode can still identify as ZIP.
_KNOWN_METHODS = frozenset(
    {
        0,
        1,
        2,
        3,
        4,
        5,
        6,
        8,
        9,
        10,
        12,
        14,
        16,
        18,
        19,
        20,  # pre-reassignment zstd; APPNOTE keeps it, 93 is the current id
        93,
        95,
        96,
        97,
        98,
        99,
    }
)


def validate_zip_local_header(
    peek_more: Callable[[int], bytes],
    remaining: int | None,
) -> HitOutcome:
    """Return whether a candidate origin looks like a ZIP local file header.

    ``peek_more(n)`` is a view relative to the candidate, not the source.
    ``remaining`` is provable bytes from the candidate to source EOF, or
    ``None`` if unknown. A truncated header, reserved flags, an unknown
    method, or name/extra lengths that overrun the source is
    :attr:`HitOutcome.NOT_THIS_FORMAT` — identity never held. This check
    does not return :attr:`HitOutcome.DAMAGED`.

    When ``remaining`` is known, name/extra existence is a length compare:
    a short ``peek_more`` can only mean the candidate view was clamped by
    ``scan_limit``, which is not evidence against ZIP. When ``remaining`` is
    unknown, a short peek is still ``NOT_THIS_FORMAT``.
    """
    header = peek_more(_LOCAL_HEADER_SIZE)
    if len(header) < _LOCAL_HEADER_SIZE or header[:4] != _LOCAL_HEADER_MAGIC:
        return HitOutcome.NOT_THIS_FORMAT
    (
        version_needed,
        flags,
        method,
        _time,
        _date,
        _crc,
        _csize,
        _usize,
        name_len,
        extra_len,
    ) = struct.unpack_from("<HHHHHIIIHH", header, 4)
    if version_needed < _VERSION_NEEDED_MIN or version_needed > _VERSION_NEEDED_MAX:
        return HitOutcome.NOT_THIS_FORMAT
    if flags & _GP_RESERVED_MASK:
        return HitOutcome.NOT_THIS_FORMAT
    if method not in _KNOWN_METHODS:
        return HitOutcome.NOT_THIS_FORMAT
    # A local header always names its member. Combined with the version floor, this
    # rejects ``PK\\x03\\x04`` plus zero-fill — the usual ELF/PE stub padding.
    if name_len == 0:
        return HitOutcome.NOT_THIS_FORMAT
    needed = _LOCAL_HEADER_SIZE + name_len + extra_len
    if remaining is not None:
        if needed > remaining:
            return HitOutcome.NOT_THIS_FORMAT
        return HitOutcome.VALID
    rest = peek_more(needed)
    if len(rest) < needed:
        return HitOutcome.NOT_THIS_FORMAT
    return HitOutcome.VALID
