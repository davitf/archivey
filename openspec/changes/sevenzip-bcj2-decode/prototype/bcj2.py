"""BCJ2 decoder — prototype, not wired into the 7z pipeline.

BCJ2 (7z method ``0x0303011B``) undoes 7-Zip's x86 branch converter. The encoder
splits an executable into four streams, stored as four separate 7z pack streams:

- **main** — every byte of the output except the 4-byte targets it moved out;
- **call** — big-endian absolute targets of converted ``E8`` (CALL) instructions;
- **jump** — big-endian absolute targets of converted ``E9`` (JMP) and ``0F 8x`` (Jcc);
- **rc** — a range-coded bit per branch candidate: was this one converted?

Decoding copies ``main`` up to and including the next candidate opcode, decodes one
bit, and on a 1 takes four bytes from ``call`` or ``jump``, turns the absolute
target back into a relative one (``target - (position + 4)``) and emits it
little-endian. The bit's probability model has 258 contexts: one per preceding byte
for ``E8``, one for ``E9``, one for ``Jcc``. That is the whole state machine; it is
7-Zip's ``Bcj2_Decode`` (``C/Bcj2.c``, 9.20) with the range coder normalised
lazily, so no input byte is required before it is used.

The Python cost is per *candidate*, not per byte: ``_BRANCH`` finds the next
candidate in C, and the loop body runs once per ``E8``/``E9``/``0F 8x``. Real x86
code has one every few dozen bytes; a hostile ``main`` made of nothing else is the
worst case (measured in the change's design.md).

The four inputs are plain readable streams, so the caller decides how they are
produced: in a 7z folder, ``main``/``call``/``jump`` are LZMA-family decoder
outputs and ``rc`` is a raw pack view, each over its own ``SharedSource`` view.
"""

from __future__ import annotations

import io
import re
from typing import BinaryIO

from archivey.exceptions import CorruptionError, TruncatedError
from archivey.internal.streams.streamtools import ReadOnlyIOStream

# A branch candidate: E8 (CALL rel32), E9 (JMP rel32), or 0F 80..8F (Jcc rel32).
# ``m.end() - 1`` is the opcode byte whose target follows.
_BRANCH = re.compile(rb"[\xe8\xe9]|\x0f[\x80-\x8f]")

_BLOCK = 64 * 1024
_TOP = 1 << 24
_NUM_MOVE_BITS = 5
_PROB_INIT = 1024  # half of 1 << 11
_E9_CONTEXT = 256
_JCC_CONTEXT = 257


class _Bytes:
    """One BCJ2 input, pulled in blocks. ``take`` returns exactly what it is asked for."""

    __slots__ = ("_stream", "_label", "_buf", "_pos")

    def __init__(self, stream: BinaryIO, label: str) -> None:
        self._stream = stream
        self._label = label
        self._buf = b""
        self._pos = 0

    def take(self, n: int) -> bytes:
        end = self._pos + n
        if end > len(self._buf):
            self._buf = self._buf[self._pos :] + self._stream.read(max(_BLOCK, n))
            self._pos = 0
            end = n
            if end > len(self._buf):
                raise TruncatedError(f"BCJ2 {self._label} stream ended early")
        out = self._buf[self._pos : end]
        self._pos = end
        return out

    def byte(self) -> int:
        if self._pos == len(self._buf):
            self._buf = self._stream.read(_BLOCK)
            self._pos = 0
            if not self._buf:
                raise TruncatedError(f"BCJ2 {self._label} stream ended early")
        b = self._buf[self._pos]
        self._pos += 1
        return b

    def unread_count(self) -> int:
        """Bytes left in this input. Drains it — only for the end-of-output check."""
        n = len(self._buf) - self._pos
        while chunk := self._stream.read(_BLOCK):
            n += len(chunk)
        self._buf, self._pos = b"", 0
        return n


class Bcj2DecoderStream(ReadOnlyIOStream):
    """Forward-only BCJ2 decode of ``unpack_size`` bytes from four input streams.

    Borrows its inputs; the caller that built the four decoder chains closes them.
    Not seekable, like every other 7z folder stream decoded from its start.
    """

    def __init__(
        self,
        main: BinaryIO,
        call: BinaryIO,
        jump: BinaryIO,
        rc: BinaryIO,
        *,
        unpack_size: int,
    ) -> None:
        super().__init__()
        self._main_stream = main
        self._main = b""
        self._mpos = 0
        self._call = _Bytes(call, "call")
        self._jump = _Bytes(jump, "jump")
        self._rc = _Bytes(rc, "range coder")
        self._size = unpack_size
        self._produced = 0
        self._pending = bytearray()
        self._probs = [_PROB_INIT] * 258
        self._prev = 0
        self._range = 0xFFFFFFFF
        self._code: int | None = None  # read lazily: an empty output needs no rc bytes

    def read(self, n: int = -1, /) -> bytes:
        if self.closed:
            raise ValueError("I/O operation on closed file.")
        if n is None or n < 0:
            return self.readall()
        if len(self._pending) < n and self._produced < self._size:
            self._decode(n - len(self._pending))
        out = bytes(self._pending[:n])
        del self._pending[:n]
        return out

    def _decode(self, want: int) -> None:  # noqa: C901 - the loop is the algorithm
        pending = self._pending
        start = len(pending)
        size = self._size
        produced = self._produced
        main, mpos, prev = self._main, self._mpos, self._prev
        probs = self._probs
        rng = self._range
        if self._code is None:
            self._code = int.from_bytes(self._rc.take(5), "big") & 0xFFFFFFFF
        code = self._code
        search = _BRANCH.search
        try:
            while len(pending) - start < want and produced < size:
                if mpos == len(main):
                    main = self._main_stream.read(_BLOCK)
                    mpos = 0
                    if not main:
                        raise TruncatedError("BCJ2 main stream ended early")
                b = main[mpos]
                if (b & 0xFE) == 0xE8 or (prev == 0x0F and (b & 0xF0) == 0x80):
                    # The byte before this one is ``prev``, which is not main[mpos-1]
                    # after a converted branch or at a block boundary.
                    pos, bprev = mpos, prev
                else:
                    m = search(main, mpos)
                    if m is None:
                        chunk = main[mpos : mpos + (size - produced)]
                        pending += chunk
                        produced += len(chunk)
                        mpos += len(chunk)
                        prev = chunk[-1]
                        continue
                    pos = m.end() - 1
                    bprev = main[pos - 1]
                chunk = main[mpos : min(pos + 1, mpos + (size - produced))]
                pending += chunk
                produced += len(chunk)
                mpos += len(chunk)
                if produced == size or mpos != pos + 1:
                    break  # the output ends at (or before) this opcode: no bit follows
                b = main[pos]
                idx = (
                    bprev if b == 0xE8 else (_E9_CONTEXT if b == 0xE9 else _JCC_CONTEXT)
                )
                if rng < _TOP:
                    rng = (rng << 8) & 0xFFFFFFFF
                    code = ((code << 8) | self._rc.byte()) & 0xFFFFFFFF
                p = probs[idx]
                bound = (rng >> 11) * p
                if code < bound:
                    rng = bound
                    probs[idx] = p + ((2048 - p) >> _NUM_MOVE_BITS)
                    prev = b
                    continue
                rng -= bound
                code -= bound
                probs[idx] = p - (p >> _NUM_MOVE_BITS)
                src = (self._call if b == 0xE8 else self._jump).take(4)
                dest = (int.from_bytes(src, "big") - (produced + 4)) & 0xFFFFFFFF
                # 7-Zip truncates a target that runs past the declared size; so do we.
                tail = dest.to_bytes(4, "little")[: size - produced]
                pending += tail
                produced += len(tail)
                prev = dest >> 24
        finally:
            self._main, self._mpos, self._prev = main, mpos, prev
            self._range, self._code = rng, code
            self._produced = produced

    def leftover(self) -> dict[str, int]:
        """Unconsumed input per stream after the output is complete (drains inputs).

        For the verification script, and the evidence for whether the real
        implementation can treat trailing input as corruption.
        """
        if self._produced < self._size:
            raise CorruptionError("BCJ2 output is not complete")
        main_left = len(self._main) - self._mpos
        while chunk := self._main_stream.read(_BLOCK):
            main_left += len(chunk)
        return {
            "main": main_left,
            "call": self._call.unread_count(),
            "jump": self._jump.unread_count(),
            "rc": self._rc.unread_count(),
            "rc_code": self._code or 0,
        }

    def seekable(self) -> bool:
        return False


def decode_bcj2(
    main: bytes, call: bytes, jump: bytes, rc: bytes, unpack_size: int
) -> bytes:
    """Whole-buffer convenience over :class:`Bcj2DecoderStream`."""
    stream = Bcj2DecoderStream(
        io.BytesIO(main),
        io.BytesIO(call),
        io.BytesIO(jump),
        io.BytesIO(rc),
        unpack_size=unpack_size,
    )
    return stream.read(unpack_size)
