"""Shared timestamp helpers for format backends.

The NTFS FILETIME conversion (100 ns ticks since 1601-01-01 UTC → ``datetime``) is used by
every backend that reads Windows-origin timestamps — ZIP's NTFS extra field, the native
7z reader, and RAR5 FILETIME extras — and the Unix-seconds ones by TAR, ZIP's extended
timestamp, RAR, gzip and the directory backend, so each lives here rather than being
copy-pasted per backend. All are ``datetime`` + ``timedelta`` arithmetic, which gives
the same answer on every platform and raises ``OverflowError`` everywhere for a value
outside ``datetime``'s range. For a field wide enough to hold such a value that guard is
the load-bearing part: a hostile timestamp must degrade to ``None`` + a reported issue,
never sink the whole listing. A 32-bit Unix field cannot hold one, so
:func:`unix32_to_datetime` has no failure case.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from archivey.terminal import quoted

# The NTFS FILETIME epoch.
_FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)
# The Unix epoch.
_UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def unix_to_datetime(seconds: float) -> datetime | None:
    """Unix seconds as an aware UTC datetime, or ``None`` outside ``datetime``'s range.

    ``datetime.fromtimestamp(ts, tz=timezone.utc)`` goes through the C library's
    ``gmtime()`` on Windows, which rejects negative values, so a member dated before
    1970 would list with the right date on Linux and macOS and as invalid on Windows.
    Epoch plus ``timedelta`` is the same value on every platform, rounded to the
    microsecond the same way. ``timedelta`` raises ``OverflowError`` for a value too
    large for it or a result outside ``datetime``'s range, and ``ValueError`` only
    for NaN, which a float PAX time record can carry; both mean "no valid time".
    """
    try:
        return _UNIX_EPOCH + timedelta(seconds=seconds)
    except (OverflowError, ValueError):
        return None


def unix32_to_datetime(seconds: int) -> datetime:
    """A 32-bit Unix seconds field (signed or unsigned) as an aware UTC datetime.

    The widest such field spans 1901-12-13 (``-2**31``) to 2106-02-07 (``2**32 - 1``),
    well inside ``datetime``'s range, so unlike :func:`unix_to_datetime` this cannot
    fail and has no ``None`` case for a caller to handle. ZIP's extended timestamp,
    RAR's Unix times and gzip's MTIME are such fields.
    """
    return _UNIX_EPOCH + timedelta(seconds=seconds)


@dataclass(frozen=True)
class TimestampIssue:
    """A non-fatal timestamp-decode problem, surfaced as a ``MEMBER_TIMESTAMP_INVALID`` diagnostic.

    ``source`` names the field family (``"ntfs"``, ``"dos"``, ``"tar"``, …) so a backend that
    reads several timestamp representations (ZIP: DOS + NTFS + extended) can tag each.
    """

    field: str
    source: str
    value_repr: str
    message: str


def filetime_to_datetime(
    value: int | None, filename: str, *, field: str, source: str = "ntfs"
) -> tuple[datetime | None, TimestampIssue | None]:
    """An NTFS FILETIME (100 ns ticks since 1601 UTC) as a datetime; 0/None means "unset".

    Returns ``(datetime, None)`` on success, ``(None, None)`` when unset, and
    ``(None, TimestampIssue)`` for an out-of-range value (which must not fail the listing).
    """
    if value is None or value == 0:
        return None, None
    try:
        # Integer arithmetic throughout: dividing a modern FILETIME (~1.3e17 ticks) as a
        # float leaves ~2 us of precision, so the microsecond would often be wrong.
        # Sub-microsecond ticks are truncated, as the stored value is.
        return _FILETIME_EPOCH + timedelta(microseconds=value // 10), None
    except OverflowError:
        # Raised by timedelta() for a huge tick count, or by the addition for a result
        # outside datetime's range (negative values included).
        return None, TimestampIssue(
            field=field,
            source=source,
            value_repr=repr(value),
            message=f"Invalid NTFS timestamp for {quoted(filename)}: {value!r}",
        )
