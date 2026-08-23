"""First meta-block framing for the Brotli content probe (RFC 7932 §9.1–9.2).

The detector needs a cheap classification of the opening header — WBITS plus one
meta-block — without Huffman-decoding a compressed body. Declared (uncompressed /
metadata) meta-blocks assert a byte count the source must physically hold; when
``source_length`` is known, a probe that overruns that length is not a complete
valid stream.

Reference behaviour and the deferred chain-walk form live in
``scripts/exploration/brotli_probe_field_survey.py`` and
``openspec/changes/brotli-probe-framing-gate/design.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class BrotliFirstBlock(Enum):
    """Outcome of parsing the stream's first meta-block header."""

    COMPRESSED = "compressed"
    UNCOMPRESSED = "uncompressed"
    METADATA = "metadata"
    EMPTY_LAST = "empty_last"
    # Anything we cannot classify cheaply (short prefix, invalid header, …).
    UNDECIDED = "undecided"


@dataclass(frozen=True)
class BrotliFraming:
    """First-block classification plus the bytes a declaring block consumes."""

    outcome: BrotliFirstBlock
    consumed: int | None = None
    declared_length: int | None = None

    @property
    def declares_length(self) -> bool:
        return self.outcome in (
            BrotliFirstBlock.UNCOMPRESSED,
            BrotliFirstBlock.METADATA,
        )


class _Bits:
    __slots__ = ("_data", "_pos")

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0

    def take(self, n: int) -> int:
        value = 0
        for i in range(n):
            byte_i, bit_i = divmod(self._pos, 8)
            if byte_i >= len(self._data):
                raise EOFError
            value |= ((self._data[byte_i] >> bit_i) & 1) << i
            self._pos += 1
        return value

    @property
    def pos(self) -> int:
        return self._pos


def _wbits(br: _Bits) -> bool:
    """Consume the window-bits field. Return False on an illegal encoding."""
    if br.take(1) == 0:
        return True
    n = br.take(3)
    if n != 0:
        return True
    m = br.take(3)
    # m == 1 is reserved for the large-window extension (invalid in RFC 7932).
    return m != 1


def _metablock(br: _Bits) -> BrotliFraming:
    islast = br.take(1)
    if islast and br.take(1):  # ISLASTEMPTY
        pad = (-br.pos) % 8
        ok = pad == 0 or br.take(pad) == 0
        if not ok:
            return BrotliFraming(BrotliFirstBlock.UNDECIDED)
        return BrotliFraming(BrotliFirstBlock.EMPTY_LAST, consumed=(br.pos + 7) // 8)

    code = br.take(2)
    if code == 3:  # metadata (MNIBBLES == 0); may carry ISLAST
        if br.take(1) != 0:  # reserved
            return BrotliFraming(BrotliFirstBlock.UNDECIDED)
        nbytes = br.take(2)
        if nbytes == 0:
            skip = 0
        else:
            skip = br.take(nbytes * 8)
            if nbytes > 1 and (skip >> ((nbytes - 1) * 8)) == 0:
                return BrotliFraming(BrotliFirstBlock.UNDECIDED)
            skip += 1
        pad = (-br.pos) % 8
        if pad and br.take(pad) != 0:
            return BrotliFraming(BrotliFirstBlock.UNDECIDED)
        return BrotliFraming(
            BrotliFirstBlock.METADATA,
            consumed=br.pos // 8,
            declared_length=skip,
        )

    nibbles = 4 + code
    mlen = 0
    for i in range(nibbles):
        nib = br.take(4)
        # Top nibble must be non-zero when MNIBBLES > 4 (exuberant-nibble rule).
        if i + 1 == nibbles and nibbles > 4 and nib == 0:
            return BrotliFraming(BrotliFirstBlock.UNDECIDED)
        mlen |= nib << (4 * i)
    declared = mlen + 1

    if not islast and br.take(1):  # ISUNCOMPRESSED
        pad = (-br.pos) % 8
        if pad and br.take(pad) != 0:
            return BrotliFraming(BrotliFirstBlock.UNDECIDED)
        return BrotliFraming(
            BrotliFirstBlock.UNCOMPRESSED,
            consumed=br.pos // 8,
            declared_length=declared,
        )
    # Compressed body — Huffman tables follow; we stop without decoding them.
    return BrotliFraming(BrotliFirstBlock.COMPRESSED)


def parse_first_metablock(prefix: bytes) -> BrotliFraming:
    """Classify ``prefix`` as the first meta-block of a Brotli stream would."""
    br = _Bits(prefix)
    try:
        if not _wbits(br):
            return BrotliFraming(BrotliFirstBlock.UNDECIDED)
        return _metablock(br)
    except EOFError:
        return BrotliFraming(BrotliFirstBlock.UNDECIDED)


def first_block_overruns_source(prefix: bytes, source_length: int) -> bool:
    """True when a declaring first meta-block cannot fit in ``source_length``."""
    info = parse_first_metablock(prefix)
    if not info.declares_length:
        return False
    assert info.consumed is not None and info.declared_length is not None
    return info.consumed + info.declared_length > source_length
