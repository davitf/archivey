"""Tests for the shared NTFS FILETIME conversion."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from archivey.internal.timestamps import filetime_to_datetime

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
        # The first tick after the FILETIME epoch, and a pre-1970 value.
        (1, _EPOCH),
        (10, _EPOCH + timedelta(microseconds=1)),
        (1 << 56, _EPOCH + timedelta(microseconds=(1 << 56) // 10)),
    ],
)
def test_filetime_converts_exactly(ticks: int, expected: datetime) -> None:
    assert filetime_to_datetime(ticks, "f", field="modified") == (expected, None)


def test_filetime_microsecond_matches_integer_truncation_across_a_year() -> None:
    # A sweep of modern values: every one must land on value // 10 microseconds.
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
