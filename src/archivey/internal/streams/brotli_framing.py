"""Brotli meta-block framing for the content probe (RFC 7932 §9.1–9.2).

The detector needs a cheap classification of opening headers — WBITS plus
meta-block headers — without Huffman-decoding a compressed body. Declared
(uncompressed / metadata) meta-blocks assert a byte count the source must
physically hold; when ``source_length`` is known, a probe that overruns that
length is not a complete valid stream.

The **chain walk** follows byte-aligned self-describing meta-blocks past the
first, stopping at the first compressed block. Reference behaviour lives in
``scripts/exploration/brotli_probe_field_survey.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

# Link cap 8: real-tree census rejected every fabrication by link index ≤ 1;
# revisit with hard data if a future corpus shows deeper rejecting chains.
# Forward-only memory for non-seekable ``read_at`` is capped separately at 1 MiB
# in ``detection_workspace.PROBE_READ_AT_MAX_OFFSET_NONSEEKABLE`` (declared in the
# format-detection chain-walk requirement).
CHAIN_MAX_LINKS = 8
CHAIN_HEADER_READ = 24


class BrotliBlock(Enum):
    """Outcome of parsing one meta-block header."""

    COMPRESSED = "compressed"
    UNCOMPRESSED = "uncompressed"
    METADATA = "metadata"
    EMPTY_LAST = "empty_last"
    # Anything we cannot classify cheaply (short prefix, invalid header, …).
    UNDECIDED = "undecided"


@dataclass(frozen=True)
class BrotliFraming:
    """Meta-block classification plus the bytes a declaring block consumes."""

    outcome: BrotliBlock
    consumed: int | None = None
    declared_length: int | None = None
    is_last: bool | None = None

    @property
    def declares_length(self) -> bool:
        return self.outcome in (
            BrotliBlock.UNCOMPRESSED,
            BrotliBlock.METADATA,
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
    islast = br.take(1) == 1
    if islast and br.take(1):  # ISLASTEMPTY
        pad = (-br.pos) % 8
        ok = pad == 0 or br.take(pad) == 0
        if not ok:
            return BrotliFraming(BrotliBlock.UNDECIDED)
        return BrotliFraming(
            BrotliBlock.EMPTY_LAST,
            consumed=(br.pos + 7) // 8,
            is_last=True,
        )

    code = br.take(2)
    if code == 3:  # metadata (MNIBBLES == 0); may carry ISLAST
        if br.take(1) != 0:  # reserved
            return BrotliFraming(BrotliBlock.UNDECIDED)
        nbytes = br.take(2)
        if nbytes == 0:
            skip = 0
        else:
            skip = br.take(nbytes * 8)
            if nbytes > 1 and (skip >> ((nbytes - 1) * 8)) == 0:
                return BrotliFraming(BrotliBlock.UNDECIDED)
            skip += 1
        pad = (-br.pos) % 8
        if pad and br.take(pad) != 0:
            return BrotliFraming(BrotliBlock.UNDECIDED)
        return BrotliFraming(
            BrotliBlock.METADATA,
            consumed=br.pos // 8,
            declared_length=skip,
            is_last=islast,
        )

    nibbles = 4 + code
    mlen = 0
    for i in range(nibbles):
        nib = br.take(4)
        # Top nibble must be non-zero when MNIBBLES > 4 (exuberant-nibble rule).
        if i + 1 == nibbles and nibbles > 4 and nib == 0:
            return BrotliFraming(BrotliBlock.UNDECIDED)
        mlen |= nib << (4 * i)
    declared = mlen + 1

    if not islast and br.take(1):  # ISUNCOMPRESSED
        pad = (-br.pos) % 8
        if pad and br.take(pad) != 0:
            return BrotliFraming(BrotliBlock.UNDECIDED)
        return BrotliFraming(
            BrotliBlock.UNCOMPRESSED,
            consumed=br.pos // 8,
            declared_length=declared,
            is_last=False,
        )
    # Compressed body — Huffman tables follow; we stop without decoding them.
    return BrotliFraming(BrotliBlock.COMPRESSED, is_last=islast)


def parse_metablock(data: bytes, *, first: bool = True) -> BrotliFraming:
    """Classify a meta-block header. ``first=False`` skips the stream WBITS field."""
    br = _Bits(data)
    try:
        if first and not _wbits(br):
            return BrotliFraming(BrotliBlock.UNDECIDED)
        return _metablock(br)
    except EOFError:
        return BrotliFraming(BrotliBlock.UNDECIDED)


def first_block_overruns_source(prefix: bytes, source_length: int) -> bool:
    """True when a declaring first meta-block cannot fit in ``source_length``."""
    info = parse_metablock(prefix, first=True)
    if not info.declares_length:
        return False
    assert info.consumed is not None and info.declared_length is not None
    return info.consumed + info.declared_length > source_length


def chain_proves_invalid(
    prefix: bytes,
    source_length: int,
    *,
    read_at: Callable[[int, int], bytes | None] | None = None,
    max_links: int = CHAIN_MAX_LINKS,
) -> bool:
    """True when the self-describing block chain proves the source is not complete Brotli.

    Follows byte-aligned uncompressed/metadata meta-blocks, stopping at the first
    compressed block. Rejects a link that overruns ``source_length`` or a declared
    end that leaves trailing bytes. Link-cap exhaustion or a declined ``read_at``
    (``None``) means *cannot disprove* — returns False so the earlier verdict stands.

    ``read_at(offset, length)`` returns bytes at that absolute offset, short/empty on
    EOF, or ``None`` when the caller will not provide the read. When omitted, only
    bytes already in ``prefix`` are reachable — and prefix exhaustion is EOF only
    when ``len(prefix) >= source_length``; otherwise it is declined (cannot disprove).
    """

    def _get(offset: int, length: int) -> bytes | None:
        end = offset + length
        if offset < len(prefix):
            # Serve whatever of the request sits in the prefix.
            in_prefix = prefix[offset : min(end, len(prefix))]
            if end <= len(prefix):
                return in_prefix
            if read_at is None:
                # Prefix exhausted: real EOF only when the prefix *is* the whole
                # source; otherwise we cannot see further → cannot disprove.
                if len(prefix) >= source_length:
                    return in_prefix
                return None
            rest = read_at(len(prefix), end - len(prefix))
            if rest is None:
                return None
            return in_prefix + rest
        if read_at is None:
            if len(prefix) >= source_length:
                return b""  # past EOF of a fully-visible prefix
            return None
        return read_at(offset, length)

    off = 0
    for _ in range(max_links):
        chunk = _get(off, CHAIN_HEADER_READ)
        if chunk is None:
            return False  # declined — cannot disprove
        if not chunk:
            return True  # expected a header, got EOF
        info = parse_metablock(chunk, first=(off == 0))
        if info.outcome is BrotliBlock.UNDECIDED:
            return True
        if info.outcome is BrotliBlock.COMPRESSED:
            return False  # cannot check further without decompressing
        if info.outcome is BrotliBlock.EMPTY_LAST:
            assert info.consumed is not None
            return off + info.consumed != source_length
        assert info.consumed is not None and info.declared_length is not None
        nxt = off + info.consumed + info.declared_length
        if nxt > source_length:
            return True
        if info.is_last:
            return nxt != source_length
        off = nxt
    return False  # link cap — cannot disprove
