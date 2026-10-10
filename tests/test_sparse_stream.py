"""``SparseStream``: a sparse file's logical bytes over its stored chunks."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path
from random import Random

import pytest

from archivey.internal.streams.streamtools import SparseStream


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


def test_short_stored_data_is_an_eof_error() -> None:
    stream = SparseStream(io.BytesIO(b"ab"), [0], [4], 8)
    with pytest.raises(EOFError):
        stream.read()


def test_a_hole_is_served_in_bounded_steps() -> None:
    """A read inside a large hole does not build the whole hole at once."""
    stream = _stream([], 1 << 40)
    stream.seek((1 << 40) - 10)
    assert stream.read() == bytes(10)
    stream.seek(0)
    assert len(stream.read(3 << 20)) == 3 << 20


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
