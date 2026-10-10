"""ARM64 branch-conversion filter decoder: 7z method ``0x0A``, liblzma filter ``0x0A``.

7-Zip 23 picks this filter by default for AArch64 executables, as it picks BCJ for
x86. Python's ``lzma`` refuses filter id 10 in a raw chain (its filter-spec parser knows
only the filters it names, on 3.11 to 3.13 at least) and ``pybcj`` 1.0.8 has no ARM64
filter, so this module decodes it in Python. An ``.xz`` stream with the ARM64 filter
does not come here: liblzma 5.4 and later decodes it inside its own stream decoder.

The algorithm is liblzma's ``arm64_code`` (``src/liblzma/simple/arm64.c``, xz 5.4).
7-Zip's ``z7_BranchConv_ARM64`` (``C/Bra.c``) produces the same bytes; the tests check
that against archives 7-Zip 23.01 writes. Every aligned little-endian 32-bit word is
examined, at offsets that are multiples of 4 from the start of the stream:

- **BL** (``word >> 26 == 0x25``, opcode ``0x94000000`` under mask ``0xFC000000``): the
  26-bit immediate held an absolute target in 4-byte units. Decoding subtracts
  ``pc >> 2`` and keeps 26 bits.
- **ADRP** (``word & 0x9F000000 == 0x90000000``): the 21-bit page immediate
  (``immlo`` in bits 29-30, ``immhi`` in bits 5-23) held an absolute page. Only an
  immediate whose value is within +/-512 MiB (``(src + 0x20000) & 0x1C0000 == 0`` on
  the 21-bit field) was converted, and only those are decoded: decoding subtracts
  ``pc >> 12`` from the low 18 bits and sign-extends bit 17 into bits 18-20.

``pc`` is the 32-bit position of the word: the coder's start offset (7z coder
properties, a multiple of 4) plus the word's offset in the stream, wrapping at 2**32.
A tail of fewer than 4 bytes at the end of the stream is passed through unchanged.

Where the time goes. The Python loop runs once per *candidate* word, not per word:
the top byte of every word is copied out with one stride slice (``buf[3::4]``) and
translated to ``0x01`` for a BL and ``0x02`` for an ADRP; ``bytes.find`` then walks
each kind at memchr speed. The words are converted in an ``array`` of 32-bit ints, one
copy in and one out, which was about three times faster per candidate than slicing four
bytes and calling ``int.from_bytes``. Measured on CPython 3.11 in 64 KiB calls: about
350 MB/s on real AArch64 objects (Go's ``race_linux_arm64.syso`` and BoringCrypto
``.syso``: 2.5 to 3% of words are BL, about 1% ADRP), 155 MB/s with one candidate in
ten words, 35 MB/s when half are candidates, and 22 to 25 MB/s when every word is a BL,
the worst case. Reading the 2 MB BoringCrypto object from a ``7z -mf=ARM64`` archive
ran at about 45 MB/s, against about 55 MB/s from the same archive without a filter.
"""

from __future__ import annotations

import sys
from array import array

# liblzma's LZMA_FILTER_ARM64 and the 7z method id are the same number.
FILTER_ARM64 = 0x0A

# 0x01 for a top byte that starts a BL (0x94-0x97), 0x02 for an ADRP
# (0x90/0xB0/0xD0/0xF0).
_CANDIDATES = bytes(
    1 if (b & 0xFC) == 0x94 else 2 if (b & 0x9F) == 0x90 else 0 for b in range(256)
)
# An array typecode of exactly 32 bits ("I" on every platform CPython supports).
_WORD = next(code for code in "IL" if array(code).itemsize == 4)
_BIG_ENDIAN = sys.byteorder == "big"


def arm64_decode(data: bytes, pc: int) -> bytes:
    """Decode the whole 4-byte words of ``data``; ``pc`` is ``data[0]``'s position.

    ``pc`` must be a multiple of 4 (the planner refuses any other start offset). The
    result covers the whole words only: the caller keeps the 0 to 3 bytes after them
    for the next call, or passes them through at the end of the stream.
    """
    end = len(data) & ~3
    marks = data[3:end:4].translate(_CANDIDATES)
    k = marks.find(1)
    adrp = marks.find(2)
    if k < 0 and adrp < 0:
        return data[:end]
    words = array(_WORD)
    words.frombytes(memoryview(data)[:end])
    if _BIG_ENDIAN:
        words.byteswap()
    find = marks.find
    # BL: the immediate is in 4-byte units, so word k's pc >> 2 is base + k. The
    # 26-bit mask makes the 32-bit wrap of pc irrelevant.
    base = pc >> 2
    while k >= 0:
        words[k] = 0x94000000 | ((words[k] - base - k) & 0x03FFFFFF)
        k = find(1, k + 1)
    # ADRP: only the low 18 bits of pc >> 12 reach the result, so neither does the
    # 32-bit wrap of pc.
    k = adrp
    while k >= 0:
        instr = words[k]
        src = ((instr >> 29) & 3) | ((instr >> 3) & 0x001FFFFC)
        if not (src + 0x00020000) & 0x001C0000:
            dest = src - ((pc + (k << 2)) >> 12)
            words[k] = (
                (instr & 0x9000001F)
                | ((dest & 3) << 29)
                | ((dest & 0x0003FFFC) << 3)
                | (-(dest & 0x00020000) & 0x00E00000)
            )
        k = find(2, k + 1)
    if _BIG_ENDIAN:
        words.byteswap()
    return words.tobytes()
