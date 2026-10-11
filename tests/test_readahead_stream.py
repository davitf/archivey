"""``ReadAheadStream``: a read buffer that reads its full-count inner once per read."""

from __future__ import annotations

import io
from random import Random

import pytest

from archivey.internal.streams.streamtools import ReadAheadStream


class _DeferredCut(io.RawIOBase):
    """Full-count bytes that end in a deferred error, as a cut decoder's do.

    A read that reaches the end returns the bytes left, short; the read after it raises.
    """

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0
        self.reads: list[int] = []

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        assert whence == io.SEEK_SET
        self._pos = offset
        return offset

    def tell(self) -> int:
        return self._pos

    def read(self, n: int = -1, /) -> bytes:
        self.reads.append(n)
        if self._pos >= len(self._data):
            raise EOFError("cut")
        end = len(self._data) if n < 0 else self._pos + n
        data = self._data[self._pos : end]
        self._pos += len(data)
        return data


def _drain(stream: ReadAheadStream, size: int) -> bytes:
    out = bytearray()
    with pytest.raises(EOFError):
        while block := stream.read(size):
            out += block
    return bytes(out)


@pytest.mark.parametrize("size", [1, 7, 100, 1000, 5000])
def test_the_prefix_comes_before_a_deferred_error(size: int) -> None:
    data = Random(0).randbytes(2500)
    assert _drain(ReadAheadStream(_DeferredCut(data), 1024), size) == data


def test_the_inner_is_read_once_per_read() -> None:
    inner = _DeferredCut(bytes(2500))
    stream = ReadAheadStream(inner, 1024)
    assert len(stream.read(10)) == 10
    assert inner.reads == [1024]
    assert len(stream.read(1020)) == 1020  # 1014 buffered, 6 from one fill
    assert inner.reads == [1024, 1024]
    assert len(stream.read(3000)) == 1470  # 1018 buffered, then the short end
    assert inner.reads == [1024, 1024, 1982]


def test_buffered_bytes_come_before_an_error_from_the_fill() -> None:
    """A fill that raises with bytes still buffered returns them; the error waits for
    the next read."""
    stream = ReadAheadStream(_DeferredCut(bytes(1024)), 1024)
    assert len(stream.read(1000)) == 1000
    assert len(stream.read(100)) == 24
    with pytest.raises(EOFError):
        stream.read(100)


def test_read_all_returns_the_buffer_and_the_rest() -> None:
    data = Random(1).randbytes(3000)
    stream = ReadAheadStream(io.BytesIO(data), 1024)
    assert stream.read(5) == data[:5]
    assert stream.read() == data[5:]
    assert stream.read() == b""


def test_seek_and_tell_count_the_bytes_still_buffered() -> None:
    data = Random(2).randbytes(3000)
    stream = ReadAheadStream(io.BytesIO(data), 1024)
    assert stream.read(10) == data[:10]
    assert stream.tell() == 10
    assert stream.seek(5, io.SEEK_CUR) == 15
    assert stream.read(10) == data[15:25]
    assert stream.seek(-10, io.SEEK_END) == 2990
    assert stream.read() == data[2990:]
    assert stream.seek(1) == 1
    assert stream.tell() == 1
    assert stream.read(3) == data[1:4]


def test_a_seek_drops_a_held_error() -> None:
    stream = ReadAheadStream(_DeferredCut(bytes(1024)), 1024)
    stream.read(1000)
    assert len(stream.read(100)) == 24  # the error is held
    assert stream.seek(0) == 0
    assert stream.read(4) == bytes(4)


def test_a_seek_inside_the_buffer_does_not_move_the_inner() -> None:
    data = Random(3).randbytes(2048)
    inner = _DeferredCut(data)
    stream = ReadAheadStream(inner, 1024)
    assert stream.read(10) == data[:10]
    assert stream.seek(500) == 500
    assert stream.read(10) == data[500:510]
    assert stream.seek(-510, io.SEEK_CUR) == 0
    assert stream.read(4) == data[:4]
    assert inner.reads == [1024]
    assert inner.tell() == 1024


def test_close_closes_the_inner() -> None:
    inner = io.BytesIO(b"abc")
    stream = ReadAheadStream(inner, 1024)
    stream.close()
    assert inner.closed
    with pytest.raises(ValueError, match="closed"):
        stream.read(1)
