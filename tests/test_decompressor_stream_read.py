"""``DecompressorStream.read(n)`` returns exactly ``n`` bytes until the end.

``read(n)`` hands a decoder's chunk straight back when nothing is buffered and the
chunk is exactly ``n`` bytes, and routes every other shape through its buffer. These
cases pin the contract across both routes: a decoder that returns what was asked, one
that returns less, and one that ignores ``max_length`` and returns more.
"""

from __future__ import annotations

import io
from typing import BinaryIO

import pytest

from archivey.internal.streams.decompressor_stream import (
    BaseDecoder,
    DecodeOut,
    DecompressorStream,
    SeekPoint,
)

_DATA = bytes(range(256)) * 97  # 24_832 bytes, not a multiple of any read size below


class _ScalingDecoder(BaseDecoder):
    """Emits each input byte ``factor`` times; honours ``max_length`` unless told not to.

    Output that ``max_length`` holds back waits in ``_pending`` for the next ``feed``,
    as zlib's ``unconsumed_tail`` does.
    """

    def __init__(self, factor: int, *, honour_max_length: bool) -> None:
        self._factor = factor
        self._honour = honour_max_length
        self._pending = b""
        self._done = False

    def recreate(self, point: SeekPoint, inner: BinaryIO) -> _ScalingDecoder:
        del point, inner
        return _ScalingDecoder(self._factor, honour_max_length=self._honour)

    def feed(self, chunk: bytes, max_length: int = -1) -> DecodeOut:
        out = self._pending + b"".join(bytes([b]) * self._factor for b in chunk)
        if self._honour and 0 <= max_length < len(out):
            self._pending = out[max_length:]
            return DecodeOut(out[:max_length])
        self._pending = b""
        return DecodeOut(out)

    def flush(self) -> DecodeOut:
        self._done = True
        out, self._pending = self._pending, b""
        return DecodeOut(out)

    @property
    def finished(self) -> bool:
        return self._done

    @property
    def needs_input(self) -> bool:
        return not self._pending


def _stream(factor: int, *, honour_max_length: bool) -> DecompressorStream:
    return DecompressorStream(
        io.BytesIO(_DATA[::factor] if factor > 1 else _DATA),
        make_decoder=lambda point, inner: _ScalingDecoder(
            factor, honour_max_length=honour_max_length
        ),
    )


@pytest.mark.parametrize("honour_max_length", [True, False])
@pytest.mark.parametrize("factor", [1, 3])
@pytest.mark.parametrize("n", [1, 7, 4096, 65536, 1 << 20])
def test_read_n_is_full_count_until_eof(
    n: int, factor: int, honour_max_length: bool
) -> None:
    source = _DATA[::factor] if factor > 1 else _DATA
    expected = b"".join(bytes([b]) * factor for b in source)
    with _stream(factor, honour_max_length=honour_max_length) as stream:
        pieces: list[bytes] = []
        while True:
            piece = stream.read(n)
            if not piece:
                break
            pieces.append(piece)
            assert stream.tell() == sum(map(len, pieces))
        assert b"".join(pieces) == expected
        # Every piece but the last is exactly n bytes.
        assert all(len(p) == n for p in pieces[:-1])
        assert 0 < len(pieces[-1]) <= n
        assert stream.read(n) == b""


def test_read_n_after_partial_read_keeps_the_buffered_tail() -> None:
    """A read that leaves bytes buffered must hand them out before decoding more."""
    with _stream(1, honour_max_length=False) as stream:
        first = stream.read(10)  # the decoder returned far more; the rest is buffered
        second = stream.read(20)
        rest = stream.read()
    assert first + second + rest == _DATA
    assert (len(first), len(second)) == (10, 20)
