"""Pure-Python CRC-32 combine (CPython adds a stdlib helper only in 3.15+).

This matches zlib's ``crc32_combine_``: given the digests of ``a`` and ``b`` plus
``len(b)``, return the digest of ``a + b`` without seeing the bytes. It surfaces
whole-stream digests from per-unit trailers (multi-member lzip).
"""

from __future__ import annotations

# ISO/zlib CRC-32 polynomial (reflected).
_CRC32_POLY = 0xEDB88320


def _multmodp(a: int, b: int) -> int:
    """``a * b mod P`` in the reflected bit order zlib uses (``a`` must be nonzero)."""
    m = 1 << 31
    p = 0
    while True:
        if a & m:
            p ^= b
            if (a & (m - 1)) == 0:
                return p
        m >>= 1
        b = (b >> 1) ^ _CRC32_POLY if b & 1 else b >> 1


def _x2n_table() -> tuple[int, ...]:
    # x^(2^k) mod P for k in 0..31, as zlib's ``x2n_table``.
    p = 1 << 30  # x^1
    table = [p]
    for _ in range(1, 32):
        p = _multmodp(p, p)
        table.append(p)
    return tuple(table)


_X2N_TABLE = _x2n_table()


def _x2nmodp(n: int, k: int) -> int:
    """``x^(n * 2^k) mod P``: one table multiply per set bit of ``n``."""
    p = 1 << 31  # x^0
    while n:
        if n & 1:
            p = _multmodp(_X2N_TABLE[k & 31], p)
        n >>= 1
        k += 1
    return p


def crc32_combine(crc1: int, crc2: int, len2: int) -> int:
    """Return ``zlib.crc32(a + b)`` given ``crc32(a)``, ``crc32(b)``, and ``len(b)``.

    zlib 1.2.12+'s method: shift ``crc1`` by ``8 * len2`` zero bits with a
    precomputed x^(2^k) table, so the cost is O(log len2) small multiplies.
    """
    if len2 <= 0:
        return crc1 & 0xFFFFFFFF
    shifted = _multmodp(_x2nmodp(len2, 3), crc1 & 0xFFFFFFFF)
    return (shifted ^ (crc2 & 0xFFFFFFFF)) & 0xFFFFFFFF
