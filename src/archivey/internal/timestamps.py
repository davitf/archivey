"""Shared timestamp helpers for format backends.

The NTFS FILETIME conversion (100 ns ticks since 1601-01-01 UTC → ``datetime``) is used by
every backend that reads Windows-origin timestamps — ZIP's NTFS extra field, the native
7z reader, and RAR5 FILETIME extras — so it lives here rather than being copy-pasted per
backend. The conversion is integer ``datetime`` + ``timedelta`` arithmetic, which is exact
to the microsecond and raises ``OverflowError`` on every platform for a value outside
``datetime``'s range. That guard is the load-bearing part: a hostile FILETIME must
degrade to ``None`` + a reported issue, never sink the whole listing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from archivey.terminal import quoted

# The NTFS FILETIME epoch.
_FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)


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
