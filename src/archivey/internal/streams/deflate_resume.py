"""Resume a DEFLATE decode with ``zlib`` at a block boundary another decoder found.

rapidgzip's index lists DEFLATE block boundaries: a compressed *bit* offset and the
decompressed offset there. At a block boundary an inflater carries only two things
forward: where it is in the input, and the last 32 KiB of output (back-references
reach no further; Huffman tables are per block). Given both, ``zlib`` can start there:

- the output before the point goes in as a preset dictionary, which is what a raw
  DEFLATE ``decompressobj(-15, zdict=...)`` fills its window with;
- the bit offset is the hard part. zlib's ``inflatePrime`` would feed the leftover
  bits, and Python's ``zlib`` does not expose it. So the input starts with empty
  DEFLATE blocks, made up here, whose length in bits ends ``bit`` bits into a byte.
  That byte's other bits are the source's own, and every byte after it is the source
  unchanged, so the decode lines up with the source again from there.

zlib only checks a stream's checksum (gzip CRC-32, zlib Adler-32) over the whole
stream, and a resumed decode never saw the part before its point. So a resumed decode
that reaches the end of its DEFLATE stream raises :class:`ResumeReachedStreamEnd`, and
the caller decodes from the start instead, which checks it. Reaching the end of the
input first is a truncation, which a checksum would not change.

The caller is ``_StdlibOnAcceleratorError`` in ``codecs.py``, which takes over a read
from rapidgzip; the points come from ``RapidgzipChildStream.resume_point``.
"""

from __future__ import annotations

import zlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import BinaryIO

from archivey.exceptions import TruncatedError
from archivey.internal.streams.decompressor_stream import (
    BaseDecoder,
    DecodeOut,
    Decoder,
    SeekPoint,
)

# The most output a DEFLATE back-reference can reach.
WINDOW_SIZE = 32 << 10


@dataclass(frozen=True)
class DeflateResume:
    """A :class:`SeekPoint` state: a DEFLATE block boundary inside the stream.

    The block starts ``bit`` bits (0 to 7) into the byte at the point's
    ``compressed_offset``. ``window`` is the output just before the point, up to
    :data:`WINDOW_SIZE` bytes (less only near the start of the stream).
    """

    bit: int
    window: bytes


class ResumeReachedStreamEnd(Exception):
    """A decode resumed at a :class:`DeflateResume` point reached the end of its
    DEFLATE stream. The stream's checksum covers output from before the point, which
    this decode cannot check, so the caller decodes again from the start."""


class _Bits:
    """Bits in DEFLATE order: each byte filled from its least significant bit."""

    def __init__(self) -> None:
        self.bits: list[int] = []

    def value(self, value: int, count: int) -> None:
        """A header field or extra bits: least significant bit first."""
        self.bits.extend((value >> i) & 1 for i in range(count))

    def code(self, code: int, count: int) -> None:
        """A Huffman code: most significant bit first."""
        self.bits.extend((code >> i) & 1 for i in reversed(range(count)))


def _empty_fixed_block(bits: _Bits) -> None:
    """A non-final block with fixed Huffman codes and no symbols: 10 bits."""
    bits.value(0, 1)  # BFINAL
    bits.value(1, 2)  # BTYPE 01, fixed codes
    bits.code(0, 7)  # end of block (256)


# The order the code-length code lengths are written in (RFC 1951 §3.2.7).
_CODE_LENGTH_ORDER = (16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15)


def _empty_dynamic_block(bits: _Bits) -> None:
    """A non-final block with dynamic Huffman codes and no symbols: 95 bits.

    The literal/length code has only end of block, with length 1; zlib accepts that
    one incomplete code. There are no distance codes. The code-length code is complete
    over the three symbols it uses: 18 (a run of zeros) as ``0``, 0 as ``10``, 1 as
    ``11``. All 19 code-length code lengths are written, which makes the length odd.
    """
    bits.value(0, 1)  # BFINAL
    bits.value(2, 2)  # BTYPE 10, dynamic codes
    bits.value(0, 5)  # HLIT: 257 literal/length codes
    bits.value(0, 5)  # HDIST: 1 distance code
    bits.value(len(_CODE_LENGTH_ORDER) - 4, 4)  # HCLEN: all 19
    lengths = {18: 1, 0: 2, 1: 2}
    for symbol in _CODE_LENGTH_ORDER:
        bits.value(lengths.get(symbol, 0), 3)
    bits.code(0b0, 1)  # 18: 138 zeros
    bits.value(138 - 11, 7)
    bits.code(0b0, 1)  # 18: 118 zeros, so symbols 0 to 255 have no code
    bits.value(118 - 11, 7)
    bits.code(0b11, 2)  # 1: end of block has length 1
    bits.code(0b10, 2)  # 0: the one distance code is unused
    bits.code(0b0, 1)  # end of block


def _prefix(bit: int) -> tuple[bytes, int]:
    """Empty blocks whose length in bits is ``bit`` more than a whole number of bytes.

    Returns the whole bytes, and the ``bit`` low bits of the byte they end in. Fixed
    blocks are 10 bits, so they reach the even values; one 95-bit dynamic block before
    them reaches the odd ones.
    """
    bits = _Bits()
    if bit % 2:
        _empty_dynamic_block(bits)
        fixed = ((bit - 7) % 8) // 2
    else:
        fixed = bit // 2
    for _ in range(fixed):
        _empty_fixed_block(bits)
    assert len(bits.bits) % 8 == bit
    whole = len(bits.bits) // 8
    data = bytes(sum(bits.bits[8 * i + j] << j for j in range(8)) for i in range(whole))
    low = sum(bits.bits[8 * whole + j] << j for j in range(bit))
    return data, low


class DeflateResumeDecoder(BaseDecoder):
    """Inflate from a :class:`DeflateResume` point to the end of the input.

    ``base`` is the codec's own decoder, which every other seek point recreates.
    ``corruption`` maps a ``zlib.error`` the way the base decoder does (``None``
    raises it as is), and ``truncated`` is the message the base decoder gives a
    truncation.
    """

    def __init__(
        self,
        resume: DeflateResume,
        base: Decoder,
        *,
        corruption: Callable[[zlib.error], Exception] | None,
        truncated: str,
    ) -> None:
        if not 0 <= resume.bit < 8 or len(resume.window) > WINDOW_SIZE:
            raise ValueError(f"not a DEFLATE resume point: {resume!r}")
        self._base = base
        self._corruption = corruption
        self._truncated = truncated
        if resume.window:
            self._decomp = zlib.decompressobj(-15, zdict=resume.window)
        else:
            self._decomp = zlib.decompressobj(-15)
        self._bit = resume.bit
        # Cleared once the first byte of input has been spliced onto the prefix.
        self._before_input = True

    def recreate(self, point: SeekPoint, inner: BinaryIO) -> Decoder:
        return self._base.recreate(point, inner)

    def _spliced(self, chunk: bytes) -> bytes:
        if not self._before_input or not chunk:
            return chunk
        self._before_input = False
        if not self._bit:
            return chunk
        prefix, low = _prefix(self._bit)
        high = chunk[0] & (0xFF << self._bit) & 0xFF
        return prefix + bytes([low | high]) + chunk[1:]

    def _decompress(self, data: bytes, max_length: int) -> bytes:
        try:
            if max_length < 0:
                out = self._decomp.decompress(data)
            else:
                out = self._decomp.decompress(data, max_length)
        except zlib.error as exc:
            if self._corruption is None:
                raise
            raise self._corruption(exc) from exc
        if self._decomp.eof:
            raise ResumeReachedStreamEnd
        return out

    def feed(self, chunk: bytes, max_length: int = -1) -> DecodeOut:
        data = self._decomp.unconsumed_tail + self._spliced(chunk)
        if not data:
            return DecodeOut(b"")
        return DecodeOut(self._decompress(data, max_length))

    def flush(self) -> DecodeOut:
        out = b""
        if self._decomp.unconsumed_tail:
            out = self._decompress(self._decomp.unconsumed_tail, -1)
        out += self._decomp.flush()
        self._pending_error = TruncatedError(self._truncated)
        return DecodeOut(out)

    @property
    def finished(self) -> bool:
        return False

    @property
    def needs_input(self) -> bool:
        return not self._decomp.unconsumed_tail
