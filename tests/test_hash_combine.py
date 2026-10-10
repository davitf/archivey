"""Unit tests for pure-Python crc32 combine helper."""

from __future__ import annotations

import zlib

import pytest

from archivey.internal.hashing import crc32_combine

_CASES = [
    (b"", b""),
    (b"a", b""),
    (b"", b"a"),
    (b"hello", b"world"),
    (b"x" * 100, b"y" * 50),
    (b"abc", b"def" * 1000),
    (bytes(range(256)), b"\xff" * 17),
]


@pytest.mark.parametrize(("left", "right"), _CASES)
def test_crc32_combine_matches_zlib(left: bytes, right: bytes) -> None:
    c1 = zlib.crc32(left) & 0xFFFFFFFF
    c2 = zlib.crc32(right) & 0xFFFFFFFF
    assert crc32_combine(c1, c2, len(right)) == (zlib.crc32(left + right) & 0xFFFFFFFF)


def test_multi_chunk_fold_matches_full_digest() -> None:
    parts = [b"one", b"two", b"three" * 10, b"", b"tail"]
    crc = 0
    for part in parts:
        crc = crc32_combine(crc, zlib.crc32(part) & 0xFFFFFFFF, len(part))
    full = b"".join(parts)
    assert crc == (zlib.crc32(full) & 0xFFFFFFFF)
