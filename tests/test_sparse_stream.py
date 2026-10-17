"""``SparseStream``: a sparse file's logical bytes over its stored chunks."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path
from random import Random
from typing import BinaryIO

import pytest

from archivey.internal.streams.streamtools import SparseStream
from tests.memory_util import traced_peak


class _ForwardOnly(io.RawIOBase):
    """A non-seekable view of some bytes."""

    def __init__(self, data: bytes) -> None:
        self._inner = io.BytesIO(data)

    def readable(self) -> bool:
        return True

    def readinto(self, b) -> int:  # type: ignore[override]
        return self._inner.readinto(b)


def _expected(chunks: list[tuple[int, bytes]], size: int) -> bytes:
    out = bytearray(size)
    for offset, data in chunks:
        out[offset : offset + len(data)] = data
    return bytes(out)


def _stream(
    chunks: list[tuple[int, bytes]], size: int, *, seekable: bool = True
) -> SparseStream:
    stored = b"".join(data for _, data in chunks)
    inner = io.BytesIO(stored) if seekable else io.BufferedReader(_ForwardOnly(stored))
    return SparseStream(
        inner, [o for o, _ in chunks], [len(d) for _, d in chunks], size
    )


_SHAPES = {
    "data-hole-data": ([(0, b"abc"), (10, b"xyz")], 13),
    "starts-with-hole": ([(5, b"abc")], 8),
    "ends-with-hole": ([(0, b"abc")], 20),
    "only-hole": ([], 9),
    "empty": ([], 0),
    "with-empty-entries": ([(0, b"ab"), (5, b""), (6, b"cd"), (9, b"")], 9),
    "no-holes": ([(0, b"abc"), (3, b"def")], 6),
    "big-hole": ([(0, b"a"), (3 << 20, b"b")], (3 << 20) + 1),
}


@pytest.mark.parametrize("seekable", [True, False])
@pytest.mark.parametrize("shape", _SHAPES)
def test_whole_read(shape: str, seekable: bool) -> None:
    chunks, size = _SHAPES[shape]
    assert _stream(chunks, size, seekable=seekable).read() == _expected(chunks, size)


@pytest.mark.parametrize("seekable", [True, False])
@pytest.mark.parametrize("shape", [s for s in _SHAPES if s != "big-hole"])
def test_small_reads(shape: str, seekable: bool) -> None:
    chunks, size = _SHAPES[shape]
    stream = _stream(chunks, size, seekable=seekable)
    rng = Random(shape)
    parts = []
    while part := stream.read(rng.randrange(1, 7)):
        parts.append(part)
    assert b"".join(parts) == _expected(chunks, size)
    assert stream.tell() == size


def test_random_seeks_match_the_expected_bytes() -> None:
    chunks = [
        (i * 100 + (i % 7), bytes([65 + i % 26]) * (5 + i % 11)) for i in range(50)
    ]
    size = 50 * 100 + 30
    expected = _expected(chunks, size)
    stream = _stream(chunks, size)
    rng = Random(1)
    for _ in range(500):
        at = rng.randrange(size + 20)
        n = rng.randrange(0, 300)
        assert stream.seek(at) == at
        got = stream.read(n)
        assert got == expected[at : at + n]
        assert stream.tell() == at + len(got)


def test_seek_rules() -> None:
    stream = _stream([(2, b"ab")], 6)
    assert stream.seekable()
    assert stream.seek(-2, io.SEEK_END) == 4
    assert stream.read() == b"\x00\x00"
    assert stream.seek(100) == 100
    assert stream.read() == b""
    assert stream.seek(-200, io.SEEK_CUR) == 0
    with pytest.raises(ValueError):
        stream.seek(-1)


def test_forward_only_stream() -> None:
    stream = _stream([(2, b"ab"), (8, b"cd")], 12, seekable=False)
    assert not stream.seekable()
    assert stream.seek(9) == 9  # forward: skips the first chunk and part of the second
    assert stream.read() == b"d\x00\x00"
    with pytest.raises(io.UnsupportedOperation):
        stream.seek(0)


def test_readinto_goes_through_read() -> None:
    stream = _stream([(2, b"ab")], 6)
    buf = bytearray(6)
    assert stream.readinto(buf) == 6
    assert bytes(buf) == b"\x00\x00ab\x00\x00"


def _inner(stored: bytes, seekable: bool) -> BinaryIO:
    return io.BytesIO(stored) if seekable else io.BufferedReader(_ForwardOnly(stored))


class _RaisesAtEnd(io.RawIOBase):
    """Bytes, then an error of its own on the read that finds none, as the TAR
    member views raise ``TruncatedError``."""

    def __init__(self, data: bytes) -> None:
        self._inner = io.BytesIO(data)
        self.raised = 0

    def readable(self) -> bool:
        return True

    def readinto(self, b) -> int:  # type: ignore[override]
        count = self._inner.readinto(b)
        if len(b) and not count:
            self.raised += 1
            raise _CutError(f"the inner stream ended (error {self.raised})")
        return count


class _CutError(Exception):
    pass


@pytest.mark.parametrize("seekable", [True, False])
def test_a_cut_inside_a_chunk_returns_the_prefix_then_raises(seekable: bool) -> None:
    """A sized read returns the bytes before the cut, holes included, and the next
    read raises (ADR 0014). The stream layer raises a builtin; the format reader
    translates it."""
    # The map names 6 stored bytes; 4 are there.
    stream = SparseStream(_inner(b"abcd", seekable), [2, 8], [2, 4], 14)
    assert stream.read(100) == b"\x00\x00ab\x00\x00\x00\x00cd"
    assert stream.tell() == 10
    with pytest.raises(EOFError):
        stream.read(1)


@pytest.mark.parametrize("seekable", [True, False])
def test_a_cut_at_a_chunk_start_returns_the_prefix_then_raises(seekable: bool) -> None:
    """The stored data ends exactly where a chunk starts: the inner read gets no
    bytes. The bytes already read, and the hole before the chunk, still come first."""
    stream = SparseStream(_inner(b"abcd", seekable), [0, 10], [4, 4], 20)
    assert stream.read(20) == b"abcd" + bytes(6)
    assert stream.tell() == 10
    with pytest.raises(EOFError, match="none of the 4 bytes"):
        stream.read(20)


def test_a_seek_clears_the_held_error() -> None:
    """A read that returned the prefix holds the cut's error for the next read. A
    seek elsewhere drops it: the read after the seek starts again and meets the cut
    when it reaches it. A seek to where the stream already is keeps it."""
    stream = SparseStream(_inner(b"abcd", True), [0, 10], [4, 4], 20)
    assert stream.read(20) == b"abcd" + bytes(6)
    # A seek elsewhere drops it: the prefix reads again, and the error is held again.
    assert stream.seek(0) == 0
    assert stream.read(20) == b"abcd" + bytes(6)
    assert stream.seek(2) == 2
    # No stale error fires at a position that never met the cut.
    assert stream.read(3) == b"cd\x00"
    assert stream.read(20) == bytes(5)
    with pytest.raises(EOFError):
        stream.read(20)


def test_a_seek_to_the_same_position_keeps_the_held_error() -> None:
    """The held error is the one the cut raised, not a second one from reading the
    inner again."""
    inner = _RaisesAtEnd(b"ab")
    stream = SparseStream(io.BufferedReader(inner), [0, 5], [2, 2], 9)
    assert stream.read(9) == b"ab" + bytes(3)
    assert stream.seek(stream.tell()) == 5
    with pytest.raises(_CutError, match=r"error 1\)"):
        stream.read(1)
    assert inner.raised == 1


def test_an_inner_error_at_a_chunk_start_comes_after_the_prefix() -> None:
    """An inner that raises its own error at its end, rather than returning no
    bytes, gets the same order: the prefix, then that same error."""
    stream = SparseStream(io.BufferedReader(_RaisesAtEnd(b"ab")), [0, 5], [2, 2], 9)
    assert stream.read(9) == b"ab" + bytes(3)
    with pytest.raises(_CutError):
        stream.read(9)


@pytest.mark.parametrize("seekable", [True, False])
def test_a_cut_fails_a_whole_read(seekable: bool) -> None:
    """A read with no size raises at the cut and returns nothing, as a plain member's
    does, wherever the cut is."""
    for stored, offsets, lengths in (
        (b"ab", [0], [4]),
        (b"abcd", [0, 10], [4, 4]),
    ):
        stream = SparseStream(_inner(stored, seekable), offsets, lengths, 20)
        with pytest.raises(EOFError):
            stream.read()


def test_a_skip_past_the_stored_data_raises() -> None:
    """A forward-only stream skips to a chunk by reading; stored data that ends
    before the chunk raises there."""
    stream = SparseStream(_inner(b"ab", False), [0, 10], [4, 4], 14)
    stream.seek(11)
    with pytest.raises(EOFError):
        stream.read(1)


def test_a_huge_logical_size_costs_nothing_until_a_hole_is_read() -> None:
    stream = _stream([], 1 << 40)
    stream.seek((1 << 40) - 10)
    assert stream.read() == bytes(10)


def test_a_hole_read_builds_its_zeros_once() -> None:
    """One read inside a hole holds its result once, not once in pieces and again
    joined."""
    size = 16 << 20
    stream = _stream([(0, b"a")], size + 1)
    stream.seek(1)
    traced_peak(lambda: None)
    peak = traced_peak(stream.read)
    assert peak < size * 1.5


def test_inner_stream_positioned_past_its_start() -> None:
    """The stored chunks start where the inner stream is when the sparse stream is
    made."""
    inner = io.BytesIO(b"ZZab")
    inner.seek(2)
    stream = SparseStream(inner, [0], [2], 4)
    assert stream.read() == b"ab\x00\x00"
    assert stream.seek(1) == 1
    assert stream.read(1) == b"b"


class _Flaky(io.RawIOBase):
    """Forward-only, two bytes per read, and one read that raises ``OSError``."""

    def __init__(self, data: bytes, fail_on: int) -> None:
        self._data = io.BytesIO(data)
        self._calls = 0
        self._fail_on = fail_on

    def readable(self) -> bool:
        return True

    def readinto(self, b) -> int:  # type: ignore[override]
        self._calls += 1
        if self._calls == self._fail_on:
            raise OSError("transient")
        return self._data.readinto(memoryview(b)[:2])


def test_a_skip_that_raises_keeps_the_inner_position() -> None:
    """A forward skip that fails partway leaves the stream knowing how far the inner
    stream went, so a later read serves the right bytes."""
    stream = SparseStream(
        _Flaky(b"AA" + b"X" * 6 + b"BB", fail_on=3), [0, 20], [8, 2], 22
    )
    assert stream.read(2) == b"AA"
    stream.seek(20)
    with pytest.raises(OSError):
        stream.read(2)
    assert stream.read(2) == b"BB"


def test_closed_stream_refuses_seek_and_tell() -> None:
    stream = _stream([(0, b"ab")], 4)
    stream.close()
    for call in (lambda: stream.seek(1), stream.tell, stream.read):
        with pytest.raises(ValueError, match="closed file"):
            call()


def test_close_closes_the_inner_stream() -> None:
    inner = io.BytesIO(b"ab")
    stream = SparseStream(inner, [0], [2], 4)
    stream.close()
    assert inner.closed
    with pytest.raises(ValueError):
        stream.read()


def test_matches_tarfile_sparse_expansion(tmp_path: Path) -> None:
    """Byte for byte against ``tarfile.extractfile`` on an old GNU sparse member."""
    from tests.test_tar import _tar_sparse_gnu

    data = _tar_sparse_gnu(logical=70_000)
    path = tmp_path / "s.tar"
    path.write_bytes(data)
    with tarfile.open(path) as tar:
        (info,) = tar.getmembers()
        extracted = tar.extractfile(info)
        assert extracted is not None
        expected = extracted.read()
        sparse = info.sparse
        assert sparse is not None
        stored = sum(n for _, n in sparse)
        inner = io.BytesIO(data[info.offset_data : info.offset_data + stored])
        stream = SparseStream(
            inner, [o for o, _ in sparse], [n for _, n in sparse], info.size
        )
        assert stream.read() == expected
