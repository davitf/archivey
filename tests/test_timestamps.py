"""Tests for the shared NTFS FILETIME and Unix-seconds conversions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from archivey.internal.timestamps import (
    filetime_to_datetime,
    unix32_to_datetime,
    unix_to_datetime,
)

_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("ticks", "expected"),
    [
        # S25-K11: the float path gave .967791 for this one.
        (133530429099677897, datetime(2024, 2, 22, 2, 35, 9, 967789, timezone.utc)),
        # The Unix epoch, and one tick either side of a microsecond boundary.
        (116444736000000000, datetime(1970, 1, 1, tzinfo=timezone.utc)),
        (116444736000000009, datetime(1970, 1, 1, 0, 0, 0, 0, timezone.utc)),
        (116444736000000010, datetime(1970, 1, 1, 0, 0, 0, 1, timezone.utc)),
        # The first tick after the FILETIME epoch, and a pre-1970 value (checked with
        # divmod and time.gmtime, not with this module's expression).
        (1, _EPOCH),
        (10, _EPOCH + timedelta(microseconds=1)),
        (1 << 56, datetime(1829, 5, 5, 23, 50, 3, 792793, timezone.utc)),
    ],
)
def test_filetime_converts_exactly(ticks: int, expected: datetime) -> None:
    assert filetime_to_datetime(ticks, "f", field="modified") == (expected, None)


def test_filetime_microsecond_matches_integer_truncation_across_a_year() -> None:
    # A differential sweep of modern values against the float path this replaced: every
    # one must land on value // 10 microseconds. The literal rows above pin the values.
    start = 133_800_000_000_000_000
    for ticks in range(start, start + 315_360_000_000_000, 9_876_543_210_987):
        dt, issue = filetime_to_datetime(ticks, "f", field="modified")
        assert issue is None
        assert dt == _EPOCH + timedelta(microseconds=ticks // 10), ticks


@pytest.mark.parametrize("ticks", [None, 0])
def test_filetime_unset(ticks: int | None) -> None:
    assert filetime_to_datetime(ticks, "f", field="modified") == (None, None)


@pytest.mark.parametrize("ticks", [2**63 - 1, 2**64 - 1, 10**25, -(10**25)])
def test_filetime_out_of_range_is_an_issue(ticks: int) -> None:
    dt, issue = filetime_to_datetime(ticks, "f", field="modified", source="ntfs")
    assert dt is None
    assert issue is not None
    assert issue.field == "modified"
    assert issue.source == "ntfs"
    assert issue.value_repr == repr(ticks)


@pytest.mark.parametrize(
    "seconds",
    [0, 1, 1_600_000_000, 2**31 - 1, 2**32 - 1, 1_600_000_000.5, 1.0000005, 0.1234567],
)
def test_unix_to_datetime_matches_fromtimestamp(seconds: float) -> None:
    # Where fromtimestamp works on every platform, the helper gives the same value,
    # sub-second rounding included.
    assert unix_to_datetime(seconds) == datetime.fromtimestamp(seconds, tz=timezone.utc)


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (-1, datetime(1969, 12, 31, 23, 59, 59, tzinfo=timezone.utc)),
        (-86_400.25, datetime(1969, 12, 30, 23, 59, 59, 750_000, tzinfo=timezone.utc)),
        (-(2**31), datetime(1901, 12, 13, 20, 45, 52, tzinfo=timezone.utc)),
    ],
)
def test_unix_to_datetime_pre_1970(seconds: float, expected: datetime) -> None:
    assert unix_to_datetime(seconds) == expected


@pytest.mark.parametrize(
    "seconds", [10**20, -(10**20), 2**63, float("inf"), float("-inf"), float("nan")]
)
def test_unix_to_datetime_out_of_range_is_none(seconds: float) -> None:
    assert unix_to_datetime(seconds) is None


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (-(2**31), datetime(1901, 12, 13, 20, 45, 52, tzinfo=timezone.utc)),
        (-1, datetime(1969, 12, 31, 23, 59, 59, tzinfo=timezone.utc)),
        (0, datetime(1970, 1, 1, tzinfo=timezone.utc)),
        (2**31 - 1, datetime(2038, 1, 19, 3, 14, 7, tzinfo=timezone.utc)),
        (2**32 - 1, datetime(2106, 2, 7, 6, 28, 15, tzinfo=timezone.utc)),
    ],
)
def test_unix32_to_datetime_covers_every_32_bit_value(
    seconds: int, expected: datetime
) -> None:
    # The extremes of a signed and an unsigned 32-bit field, so no value such a field
    # holds needs a None case.
    assert unix32_to_datetime(seconds) == expected
