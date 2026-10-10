"""``ArchiveSource``: the one object every archive source becomes at the boundary.

It carries four guarantees — full-count reads, ownership, bounded reads, cheap facts —
plus the detection replay prefix for a non-seekable source. The tests below take them
one at a time and drive the object directly, so a failure names the property rather
than a format whose parser happened to depend on it. The format-level proof is
elsewhere: ``test_source_ownership.py`` (no caller stream closed, every format),
``test_short_read_sources.py`` (parity from a one-byte-chunk source) and the leak
oracle, which run unchanged over this object.
"""

from __future__ import annotations

import gzip
import io
import os
import subprocess
import sys
import tarfile
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from archivey import detect_format, open_archive
from archivey.internal.detection_workspace import DETECTION_LIMIT
from archivey.internal.source import ArchiveSource
from archivey.internal.streams.streamtools import (
    DEFAULT_UNKNOWN_LENGTH_READ_STEP,
    ensure_bufferedio,
    is_seekable,
    source_byte_size,
    source_name,
)
from archivey.internal.volumes import ConcatenatedFile
from tests.streams_util import (
    FactSizedReadRecorder,
    NonSeekableBytesIO,
    ReadSizeRecorder,
    ShortReadBytesIO,
    ShortReadNonSeekable,
)

if TYPE_CHECKING:
    from _typeshed import WriteableBuffer

# Larger than BufferedReader's default so the over-read contrast is a partial
# fill, not EOF. 3.14 raised DEFAULT_BUFFER_SIZE from 8 KiB to 128 KiB (gh-117151);
# a 10 KiB payload is swallowed whole on that version.
DATA = bytes(range(256)) * (io.DEFAULT_BUFFER_SIZE // 256 + 8)
_WINDOWS = sys.platform == "win32"


# ---------------------------------------------------------------------------
# Full-count
# ---------------------------------------------------------------------------


def test_a_non_seekable_short_source_reads_full_count() -> None:
    """The boundary, not a backend: ``read(n)`` yields ``n`` from a one-byte source."""
    source = ShortReadNonSeekable(DATA, 1)
    wrapped = ArchiveSource.for_stream(source)
    assert wrapped.read(20) == DATA[:20]
    assert source.consumed == 20
    assert wrapped.read(100) == DATA[20:120]
    assert source.consumed == 120


def test_a_non_seekable_source_is_not_read_ahead() -> None:
    """Zero read-ahead when the inner *could* fill a ``BufferedReader`` buffer.

    ``ensure_bufferedio`` on this same source takes ``io.DEFAULT_BUFFER_SIZE`` for a
    ``read(20)`` (next test); from a pipe that over-read cannot be given back.
    """
    source = ShortReadNonSeekable(DATA, max_chunk=len(DATA))
    wrapped = ArchiveSource.for_stream(source)
    assert wrapped.seekable() is False
    assert wrapped.read(20) == DATA[:20]
    assert source.consumed == 20


def test_buffered_reader_over_non_seekable_over_reads() -> None:
    """Why the non-seekable strategy is not a buffer: it reads ahead."""
    source = ShortReadNonSeekable(DATA, max_chunk=len(DATA))
    buffered = ensure_bufferedio(source)
    assert buffered.read(20) == DATA[:20]
    assert source.consumed == io.DEFAULT_BUFFER_SIZE


def test_a_seekable_short_source_reads_full_count() -> None:
    """A seekable raw source gets a buffer, which is full-count and recoverable."""
    source = ShortReadBytesIO(DATA, max_chunk=1)
    wrapped = ArchiveSource.for_stream(source)
    assert wrapped.read(20) == DATA[:20]
    assert wrapped.tell() == 20
    wrapped.seek(5)
    assert wrapped.read(10) == DATA[5:15]


def test_an_already_buffered_source_gets_no_second_buffer() -> None:
    """A caller's ``BufferedReader`` is already full-count; it reads for itself.

    Nothing is stacked in front of it, so its read-ahead is the caller's and no more.
    """
    source = ShortReadNonSeekable(DATA, max_chunk=len(DATA))
    buffered = io.BufferedReader(source)
    wrapped = ArchiveSource.for_stream(buffered)
    assert wrapped.read(20) == DATA[:20]
    assert source.consumed == io.DEFAULT_BUFFER_SIZE  # the caller's buffer's, not ours
    wrapped.close()
    assert not buffered.closed


def test_read_negative_one_drains_even_when_inner_shorts_on_drain() -> None:
    """``read(-1)`` must not lean on the inner's ``read(-1)`` / ``readall()``.

    ``cap_drain`` makes ``read(-1)`` illegal ``RawIOBase`` behaviour (it caps instead
    of draining). The drain goes through sized reads, so this never calls the inner's
    ``read(-1)`` at all.
    """
    source = ShortReadNonSeekable(DATA, 1, cap_drain=True)
    wrapped = ArchiveSource.for_stream(source)
    assert wrapped.read(-1) == DATA
    assert source.consumed == len(DATA)


def test_readall_drains_a_short_inner() -> None:
    wrapped = ArchiveSource.for_stream(ShortReadNonSeekable(DATA, 1))
    assert wrapped.readall() == DATA


def test_readinto_is_full_count_too() -> None:
    source = ShortReadNonSeekable(DATA, 1)
    wrapped = ArchiveSource.for_stream(source)
    buf = bytearray(50)
    assert wrapped.readinto(buf) == 50
    assert bytes(buf) == DATA[:50]
    assert source.consumed == 50


def test_over_returning_inner_raises() -> None:
    """An inner that returns more than asked is a broken ``RawIOBase``, not a clamp."""

    class _OverRead(io.RawIOBase):
        def readable(self) -> bool:
            return True

        def read(self, n: int = -1) -> bytes:  # type: ignore[override]
            if n is None or n < 0:
                return b"abcdefghij"
            return b"abcdefghij"[: n + 5]

    wrapped = ArchiveSource.for_stream(_OverRead())  # type: ignore[arg-type]  # RawIOBase double
    with pytest.raises(ValueError, match="inner returned 9 bytes for read\\(4\\)"):
        wrapped.read(4)


def test_over_returning_inner_after_a_short_read_raises() -> None:
    """The gathered follow-up is held to the same "at most ``n``" contract."""

    class _ShortThenOverlong(io.RawIOBase):
        def __init__(self) -> None:
            super().__init__()
            self._chunks = [b"abcd", b"EFGHIJKLM"]

        def readable(self) -> bool:
            return True

        def read(self, n: int = -1) -> bytes:  # type: ignore[override]
            return self._chunks.pop(0) if self._chunks else b""

    wrapped = ArchiveSource.for_stream(_ShortThenOverlong())  # type: ignore[arg-type]  # RawIOBase double
    # The follow-up asks for the missing 6 bytes and gets 9.
    with pytest.raises(ValueError, match="inner returned 9 bytes for read\\(6\\)"):
        wrapped.read(10)


def test_over_returning_inner_with_a_fact_length_raises() -> None:
    """The fact-length fast path refuses an over-read too, rather than passing it on."""

    class _OverReadBuffer(io.BytesIO):
        def read(self, n: int | None = -1, /) -> bytes:
            return super().read(-1 if n is None or n < 0 else n + 5)

    wrapped = ArchiveSource.for_stream(_OverReadBuffer(b"abcdefghij"))
    assert wrapped._length is not None
    with pytest.raises(ValueError, match="inner returned 9 bytes for read\\(4\\)"):
        wrapped.read(4)


class _OverCountingReadinto(io.BytesIO):
    """Fills the buffer correctly, then reports ``extra`` more bytes than it wrote.

    With ``short_first``, the first call fills only 2 bytes and reports them
    truthfully, so the over-count arrives on the follow-up fill.
    """

    def __init__(self, data: bytes, *, extra: int, short_first: bool = False) -> None:
        super().__init__(data)
        self._extra = extra
        self._short_first = short_first

    def readinto(self, b: WriteableBuffer, /) -> int:  # type: ignore[override]
        view = memoryview(b).cast("B")
        if self._short_first:
            self._short_first = False
            return super().readinto(view[:2])
        return super().readinto(view) + self._extra


def test_readinto_refuses_a_count_larger_than_the_buffer() -> None:
    """The count is checked, not only the bytes: a lie would move the position past EOF."""
    wrapped = ArchiveSource.for_stream(_OverCountingReadinto(b"abcdefghij", extra=100))
    assert wrapped._length is not None
    with pytest.raises(
        ValueError, match="inner returned 104 bytes for readinto\\(4\\)"
    ):
        wrapped.readinto(bytearray(4))


def test_readinto_refuses_an_over_count_on_the_follow_up_fill() -> None:
    """A short first fill, then an over-count on the re-ask, raises the same way."""
    inner = _OverCountingReadinto(b"abcdefghij", extra=100, short_first=True)
    wrapped = ArchiveSource.for_stream(inner)
    with pytest.raises(
        ValueError, match="inner returned 102 bytes for readinto\\(2\\)"
    ):
        wrapped.readinto(bytearray(4))


def test_sized_read_past_eof_returns_the_remainder() -> None:
    """Stop-on-empty, not raise: a sized read past EOF is a short return.

    ``read(0)`` is a no-op (``compressed-streams``).
    """
    source = ShortReadNonSeekable(b"abcde", 1)
    wrapped = ArchiveSource.for_stream(source)
    assert wrapped.read(0) == b""
    assert source.consumed == 0
    assert wrapped.read(100) == b"abcde"
    assert source.consumed == 5
    assert wrapped.read(5) == b""
    assert wrapped.readinto(bytearray(4)) == 0


class _NothingReady(io.RawIOBase):
    """A non-blocking raw: ``read`` returns ``None``, which is not EOF.

    ``b""`` is end of file for a blocking and a non-blocking stream alike.
    ``None`` means nothing is ready yet.
    """

    def readable(self) -> bool:
        return True

    def read(self, n: int = -1, /) -> bytes | None:
        return None


class _ShortThenNothing(io.RawIOBase):
    """One short chunk, then ``None`` on every later read."""

    def __init__(self, first: bytes) -> None:
        super().__init__()
        self._first = first
        self.calls = 0

    def readable(self) -> bool:
        return True

    def read(self, n: int = -1, /) -> bytes | None:
        self.calls += 1
        if self._first:
            take = len(self._first) if n < 0 else min(n, len(self._first))
            chunk = self._first[:take]
            self._first = self._first[take:]
            return chunk
        return None


def test_non_blocking_none_is_not_end_of_file() -> None:
    """``None`` from a non-blocking raw must not look like an empty archive."""
    wrapped = ArchiveSource.for_stream(_NothingReady())  # type: ignore[arg-type]  # RawIOBase double
    with pytest.raises(BlockingIOError, match="non-blocking"):
        wrapped.read(10)
    with pytest.raises(BlockingIOError, match="non-blocking"):
        wrapped.read(-1)
    with pytest.raises(BlockingIOError, match="non-blocking"):
        wrapped.readall()
    with pytest.raises(BlockingIOError, match="non-blocking"):
        wrapped.peek(8)
    with pytest.raises(BlockingIOError, match="non-blocking"):
        wrapped.readinto(bytearray(4))


def test_short_chunk_then_none_is_not_a_finished_read() -> None:
    """Bytes already in hand, then ``None``, are not the end of the request."""
    inner = _ShortThenNothing(b"ab")
    wrapped = ArchiveSource.for_stream(inner)  # type: ignore[arg-type]  # RawIOBase double
    with pytest.raises(BlockingIOError, match="non-blocking"):
        wrapped.read(10)
    # The short chunk was taken, and the follow-up that returned ``None`` was
    # not reported as EOF.
    assert inner.calls == 2


def _read_non_blocking(stream: io.IOBase) -> bytes:
    """Open ``stream`` and return the bytes of its single streamed member."""
    reader = open_archive(stream, streaming=True)  # type: ignore[arg-type]  # IOBase double
    try:
        chunks: list[bytes] = []
        for _member, member_stream in reader.stream_members():
            if member_stream is not None:
                chunks.append(member_stream.read())
        return b"".join(chunks)
    finally:
        reader.close()


def test_open_archive_refuses_a_stream_with_nothing_ready() -> None:
    """No bytes ready is not an empty archive."""
    with pytest.raises(BlockingIOError, match="non-blocking"):
        _read_non_blocking(_NothingReady())


def test_open_archive_refuses_a_complete_prefix_followed_by_none() -> None:
    """A whole gzip, then ``None``, is not a finished archive.

    ``None`` means nothing is ready, not that the source ended. The bytes
    happen to be a complete gzip of ``b"hello"``; reading them back would
    hide a caller that still has more to send.
    """
    payload = gzip.compress(b"hello")
    with pytest.raises(BlockingIOError, match="non-blocking"):
        _read_non_blocking(_ShortThenNothing(payload))


def _assert_stall_is_refused(stream: io.IOBase) -> None:
    """``None`` from ``stream`` is ``BlockingIOError`` on every source read."""
    wrapped = ArchiveSource.for_stream(stream)  # type: ignore[arg-type]  # IOBase double
    with pytest.raises(BlockingIOError, match="non-blocking"):
        wrapped.read(10)
    with pytest.raises(BlockingIOError, match="non-blocking"):
        wrapped.read(-1)
    with pytest.raises(BlockingIOError, match="non-blocking"):
        wrapped.readall()
    with pytest.raises(BlockingIOError, match="non-blocking"):
        wrapped.peek(8)
    with pytest.raises(BlockingIOError, match="non-blocking"):
        wrapped.readinto(bytearray(4))


class _BufferedNothingReady(io.BufferedIOBase):
    """An already-buffered non-blocking stream: ``read`` / ``readinto`` return ``None``."""

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def read(self, size: int | None = -1, /) -> bytes | None:  # type: ignore[override]
        return None

    def readinto(self, b: memoryview, /) -> int | None:  # type: ignore[override]
        return None


class _BufferedThenNothing(io.BufferedIOBase):
    """One buffered payload, then ``None`` once it has been handed out."""

    def __init__(self, first: bytes) -> None:
        super().__init__()
        self._first = first

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def read(self, size: int | None = -1, /) -> bytes | None:  # type: ignore[override]
        if not self._first:
            return None
        if size is None or size < 0:
            chunk, self._first = self._first, b""
            return chunk
        take = min(size, len(self._first))
        chunk = self._first[:take]
        self._first = self._first[take:]
        return chunk


class _FactLengthStalls(io.BytesIO):
    """A seekable buffer with a fact length whose ``read`` still returns ``None``."""

    def read(self, size: int | None = -1, /) -> bytes | None:  # type: ignore[override]
        return None


def test_buffered_non_blocking_none_is_not_end_of_file() -> None:
    """An already-buffered stream has no gatherer; ``None`` is still not EOF."""
    _assert_stall_is_refused(_BufferedNothingReady())


def test_fact_length_none_is_not_end_of_file() -> None:
    """The sized-read fast path also refuses ``None`` instead of calling ``len`` on it."""
    wrapped = ArchiveSource.for_stream(_FactLengthStalls(b"hello"))
    assert wrapped._length == 5
    with pytest.raises(BlockingIOError, match="non-blocking"):
        wrapped.read(10)
    assert wrapped.read(0) == b""


def test_open_archive_refuses_a_buffered_stream_with_nothing_ready() -> None:
    """No bytes ready on an already-buffered stream is not an empty archive."""
    with pytest.raises(BlockingIOError, match="non-blocking"):
        _read_non_blocking(_BufferedNothingReady())


def test_open_archive_refuses_a_buffered_gzip_followed_by_none() -> None:
    """A whole gzip from an already-buffered stream, then ``None``, is not finished."""
    with pytest.raises(BlockingIOError, match="non-blocking"):
        _read_non_blocking(_BufferedThenNothing(gzip.compress(b"hello")))


def _nonblocking_pipe(
    payload: bytes | None = None, *, buffered: bool = True
) -> tuple[io.BufferedReader, int]:
    """Read end of a pipe with nothing more ready. The write end stays open.

    Closing it would be a real EOF (``b""``). Leaving it open and writing
    nothing — or only ``payload`` — is the stall ``None`` means. With
    ``buffered=False`` the read end is a ``FileIO``, typed as the buffered
    reader for the callers' sake.
    """
    read_fd, write_fd = os.pipe()
    os.set_blocking(read_fd, False)
    if payload:
        os.write(write_fd, payload)
    return open(read_fd, "rb", buffering=-1 if buffered else 0), write_fd  # type: ignore[return-value]  # FileIO when unbuffered


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="a POSIX non-blocking pipe read returns None; Windows does not",
)
def test_nonblocking_pipe_none_is_not_end_of_file() -> None:
    """``open`` on an ``O_NONBLOCK`` pipe is a ``BufferedReader`` with no gatherer."""
    buffered, write_fd = _nonblocking_pipe()
    try:
        assert isinstance(buffered, io.BufferedReader)
        assert buffered.read(1) is None
        wrapped = ArchiveSource.for_stream(buffered)
        assert wrapped._gatherer is None
        with pytest.raises(BlockingIOError, match="non-blocking"):
            wrapped.read(10)
        with pytest.raises(BlockingIOError, match="non-blocking"):
            wrapped.read(-1)
        with pytest.raises(BlockingIOError, match="non-blocking"):
            wrapped.readall()
        with pytest.raises(BlockingIOError, match="non-blocking"):
            wrapped.readinto(bytearray(4))
        with pytest.raises(BlockingIOError, match="non-blocking"):
            wrapped.peek(8)
    finally:
        buffered.close()
        os.close(write_fd)


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="a POSIX non-blocking pipe read returns None; Windows does not",
)
def test_open_archive_refuses_a_nonblocking_pipe() -> None:
    """Nothing ready on a pipe is not an empty archive."""
    buffered, write_fd = _nonblocking_pipe()
    try:
        with pytest.raises(BlockingIOError, match="non-blocking"):
            _read_non_blocking(buffered)
    finally:
        buffered.close()
        os.close(write_fd)


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="a POSIX non-blocking pipe read returns None; Windows does not",
)
def test_open_archive_refuses_a_nonblocking_pipe_after_a_complete_gzip() -> None:
    """A complete gzip already in a non-blocking pipe, then a stall, is not finished."""
    buffered, write_fd = _nonblocking_pipe(gzip.compress(b"hello"))
    try:
        with pytest.raises(BlockingIOError, match="non-blocking"):
            _read_non_blocking(buffered)
    finally:
        buffered.close()
        os.close(write_fd)


def test_detect_format_refuses_a_raw_stream_with_nothing_ready() -> None:
    """``None`` on the first detection read is not an empty source."""
    with pytest.raises(BlockingIOError, match="non-blocking"):
        detect_format(_NothingReady())  # type: ignore[arg-type]  # RawIOBase double


def test_detect_format_refuses_a_short_chunk_then_none() -> None:
    """A partial prefix, then ``None``, is not the whole source.

    Detection asks for more than 100 bytes, so it must ask again, and the
    ``None`` it then gets must not end the prefix as if it were EOF.
    """
    inner = _ShortThenNothing(b"x" * 100)
    with pytest.raises(BlockingIOError, match="non-blocking"):
        detect_format(inner)  # type: ignore[arg-type]  # RawIOBase double
    assert inner.calls == 2


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="a POSIX non-blocking pipe read returns None; Windows does not",
)
@pytest.mark.parametrize("payload", [None, b"x" * 100], ids=["empty", "partial"])
@pytest.mark.parametrize("buffered", [True, False], ids=["buffered", "raw"])
def test_detect_format_refuses_a_nonblocking_pipe(
    payload: bytes | None, buffered: bool
) -> None:
    """A pipe with nothing more ready is not a short or empty source."""
    stream, write_fd = _nonblocking_pipe(payload, buffered=buffered)
    try:
        with pytest.raises(BlockingIOError, match="non-blocking"):
            detect_format(stream)
    finally:
        stream.close()
        os.close(write_fd)


class _BufferedShortThenStall(io.BufferedIOBase):
    """An already-buffered non-blocking stream with ``data`` arrived and nothing more yet.

    Like ``BufferedReader`` over an ``O_NONBLOCK`` pipe: a read larger than what has
    arrived returns it short, and only the read after that answers ``None``.
    """

    def __init__(self, data: bytes) -> None:
        super().__init__()
        self._data = data

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def read(self, size: int | None = -1, /) -> bytes | None:  # type: ignore[override]
        if not self._data:
            return None
        take = len(self._data) if size is None or size < 0 else size
        chunk, self._data = self._data[:take], self._data[take:]
        return chunk

    def readinto(self, b: memoryview, /) -> int | None:  # type: ignore[override]
        chunk = self.read(len(b))
        if chunk is None:
            return None
        b[: len(chunk)] = chunk
        return len(chunk)


_SHORT_THEN_STALL_READS: dict[str, Callable[[ArchiveSource], object]] = {
    "read(n)": lambda s: s.read(10),
    "read(-1)": lambda s: s.read(-1),
    "readall": lambda s: s.readall(),
    "peek": lambda s: s.peek(512),
    "readinto": lambda s: s.readinto(bytearray(10)),
}


@pytest.mark.parametrize(
    "call", _SHORT_THEN_STALL_READS.values(), ids=_SHORT_THEN_STALL_READS
)
def test_buffered_short_then_stall_is_not_end_of_file(
    call: Callable[[ArchiveSource], object],
) -> None:
    """Bytes that have arrived, then a stall, are not a short source.

    The buffered shape has no gatherer, and its first read hands back ``b"ABCDE"``
    without ever returning ``None``. Taken as final, that is a truncated prefix
    reported as the whole source.
    """
    wrapped = ArchiveSource.for_stream(_BufferedShortThenStall(b"ABCDE"))  # type: ignore[arg-type]  # BufferedIOBase double
    with pytest.raises(BlockingIOError, match="non-blocking"):
        call(wrapped)


def _tar_head(n: int) -> bytes:
    """The first ``n`` bytes of a one-member tar."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo("a.txt")
        payload = b"x" * 2000
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    return buf.getvalue()[:n]


def test_open_archive_refuses_a_buffered_partial_tar_then_stall() -> None:
    """Part of a tar header arrived, then nothing: not an unrecognisable archive."""
    with pytest.raises(BlockingIOError, match="non-blocking"):
        _read_non_blocking(_BufferedShortThenStall(_tar_head(100)))


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="a POSIX non-blocking pipe read returns None; Windows does not",
)
@pytest.mark.parametrize(
    "call", _SHORT_THEN_STALL_READS.values(), ids=_SHORT_THEN_STALL_READS
)
def test_nonblocking_pipe_partial_then_stall_is_not_end_of_file(
    call: Callable[[ArchiveSource], object],
) -> None:
    """The real shape: some bytes in an ``O_NONBLOCK`` pipe, write end still open."""
    buffered, write_fd = _nonblocking_pipe(b"ABCDE")
    try:
        wrapped = ArchiveSource.for_stream(buffered)
        with pytest.raises(BlockingIOError, match="non-blocking"):
            call(wrapped)
    finally:
        buffered.close()
        os.close(write_fd)


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="a POSIX non-blocking pipe read returns None; Windows does not",
)
def test_open_archive_refuses_a_nonblocking_pipe_with_a_partial_tar() -> None:
    """100 bytes of a tar in a non-blocking pipe, then a stall."""
    buffered, write_fd = _nonblocking_pipe(_tar_head(100))
    try:
        with pytest.raises(BlockingIOError, match="non-blocking"):
            _read_non_blocking(buffered)
    finally:
        buffered.close()
        os.close(write_fd)


class _NoneForZero(io.BytesIO):
    """A duck-typed buffer that answers ``None`` to an empty request; others are real."""

    def read(self, size: int | None = -1, /) -> bytes | None:  # type: ignore[override]
        if size == 0:
            return None
        return super().read(size)

    def readinto(self, b: memoryview, /) -> int | None:  # type: ignore[override]
        if not len(b):
            return None
        return super().readinto(b)


def test_fact_length_read_answers_zero_without_io() -> None:
    """Nothing asked, or nothing left, is ``b""`` without asking the inner."""
    wrapped = ArchiveSource.for_stream(_NoneForZero(b"hi"))
    assert wrapped._length == 2
    assert wrapped.read(0) == b""
    assert wrapped.read(8) == b"hi"
    assert wrapped.read(8) == b""
    assert wrapped.read(0) == b""
    assert wrapped.read(-1) == b""
    assert wrapped.readall() == b""
    assert wrapped.readinto(bytearray(4)) == 0


# ---------------------------------------------------------------------------
# A non-seekable source stays one
# ---------------------------------------------------------------------------


def test_a_non_seekable_source_stays_non_seekable_and_tell_raises() -> None:
    """The seek-required refusals depend on ``tell`` raising, as on the pipe itself."""
    wrapped = ArchiveSource.for_stream(ShortReadNonSeekable(DATA, 1))
    assert wrapped.seekable() is False
    assert is_seekable(wrapped) is False
    with pytest.raises(io.UnsupportedOperation):
        wrapped.tell()
    with pytest.raises(io.UnsupportedOperation):
        wrapped.seek(0)
    with pytest.raises(io.UnsupportedOperation):
        wrapped.fileno()


# ---------------------------------------------------------------------------
# Detection replay prefix
# ---------------------------------------------------------------------------


def test_peek_does_not_consume() -> None:
    source = ArchiveSource.for_stream(NonSeekableBytesIO(b"0123456789"))  # type: ignore[arg-type]
    assert source.peek(4) == b"0123"
    # A second peek sees the same bytes; nothing was consumed.
    assert source.peek(4) == b"0123"
    assert source.read(10) == b"0123456789"


def test_peek_and_read_over_short_returning_non_seekable() -> None:
    """The prefix is filled by one full-count read, so a one-byte source still fills it."""
    data = b"0123456789"
    source = ArchiveSource.for_stream(ShortReadNonSeekable(data, 1))
    assert source.peek(4) == data[:4]
    assert source.read(6) == data[:6]
    assert source.read() == data[6:]


def test_read_replays_prefix_then_passes_through() -> None:
    source = ArchiveSource.for_stream(NonSeekableBytesIO(b"abcdefghij"))  # type: ignore[arg-type]
    source.peek(4)
    assert source.read(2) == b"ab"
    assert source.read(2) == b"cd"
    # ...then falls through to the underlying stream with no bytes dropped.
    assert source.read(6) == b"efghij"
    assert source.read(1) == b""


def test_a_read_straddling_the_prefix_is_whole() -> None:
    source = ArchiveSource.for_stream(ShortReadNonSeekable(b"abcdefghij", 1))
    source.peek(3)
    assert source.read(7) == b"abcdefg"


def test_read_all_drains_prefix_and_underlying() -> None:
    source = ArchiveSource.for_stream(NonSeekableBytesIO(b"hello world"))  # type: ignore[arg-type]
    source.peek(5)
    assert source.read() == b"hello world"
    assert source.read() == b""


def test_peek_beyond_the_default_window_grows() -> None:
    """The ISO probe needs 32 774 bytes; the same bytes are then still replayed."""
    data = bytes(range(256)) * 200
    assert len(data) > DETECTION_LIMIT
    source = ArchiveSource.for_stream(NonSeekableBytesIO(data))  # type: ignore[arg-type]
    assert source.peek(32774) == data[:32774]
    assert source.read(len(data)) == data


def test_peek_past_eof_returns_short() -> None:
    source = ArchiveSource.for_stream(NonSeekableBytesIO(b"abc"))  # type: ignore[arg-type]
    assert source.peek(100) == b"abc"
    assert source.read() == b"abc"


def test_readinto_replays_the_prefix() -> None:
    source = ArchiveSource.for_stream(NonSeekableBytesIO(b"abcdef"))  # type: ignore[arg-type]
    source.peek(3)
    buf = bytearray(4)
    assert source.readinto(buf) == 4
    assert bytes(buf) == b"abcd"


def test_a_buffered_reader_over_the_source_keeps_the_prefix() -> None:
    """A codec may put its own buffer on the source; the prefix must survive it."""
    source = ArchiveSource.for_stream(NonSeekableBytesIO(b"0123456789"))  # type: ignore[arg-type]
    source.peek(4)
    assert io.BufferedReader(source).read(10) == b"0123456789"


def test_a_caller_buffer_on_a_pipe_still_gets_the_prefix() -> None:
    """A ``BufferedReader``'s own ``peek`` returns at most one buffer's worth.

    That is less than detection can need, so the source keeps its own prefix in front
    of the caller's buffer rather than using the buffer's.
    """
    data = bytes(range(256)) * 400
    buffered = io.BufferedReader(NonSeekableBytesIO(data), buffer_size=4096)  # type: ignore[arg-type]
    source = ArchiveSource.for_stream(buffered)
    assert source.peek(32774) == data[:32774]
    assert source.read(len(data)) == data


def test_peek_refuses_a_seekable_source() -> None:
    """A seekable source is rewound, not replayed; a prefix nobody drains would leak."""
    with pytest.raises(io.UnsupportedOperation):
        ArchiveSource.for_stream(io.BytesIO(b"abc")).peek(1)


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------


def test_a_bytesio_is_borrowed() -> None:
    """The commonest caller shape of all, and the one the ZIP close bug rode in on."""
    caller = io.BytesIO(DATA)
    source = ArchiveSource.for_stream(caller)
    assert source.read(20) == DATA[:20]
    assert source.seekable() and source.tell() == 20
    source.close()
    assert not caller.closed


def test_the_buffer_over_a_seekable_raw_source_is_detached_not_closed() -> None:
    raw = ShortReadBytesIO(DATA, max_chunk=7)
    source = ArchiveSource.for_stream(raw)
    assert source.read(20) == DATA[:20]
    source.close()
    assert not raw.closed
    raw.seek(0)
    assert raw.read(7) == DATA[:7]


def test_a_non_seekable_caller_stream_is_borrowed() -> None:
    caller = NonSeekableBytesIO(b"data")
    source = ArchiveSource.for_stream(caller)  # type: ignore[arg-type]
    assert source.read(2) == b"da"
    source.close()
    assert caller.closed is False
    assert caller.read() == b"ta"


def test_a_path_handle_is_archiveys_and_closes_with_the_source(tmp_path: Path) -> None:
    path = tmp_path / "f.bin"
    path.write_bytes(DATA)
    source = ArchiveSource.for_path(path)
    assert source.read(4) == DATA[:4]
    handle = source._reader
    assert handle is not None
    source.close()
    assert handle.closed  # type: ignore[union-attr]


def test_a_joined_set_closes_with_the_source_and_borrows_its_stream_parts() -> None:
    """The join gathers a short-reading part itself, so a part needs no wrapper of its own.

    Fails against a join that takes one short read as final (``b"abcd"`` comes back)
    or one that closes the caller's stream parts.
    """
    callers = [io.BytesIO(b"abc"), ShortReadBytesIO(b"defg", max_chunk=1)]
    joined = ConcatenatedFile(callers)  # type: ignore[arg-type]
    source = ArchiveSource.for_volumes(joined)
    assert source.read(7) == b"abcdefg"
    assert source.volume_count == 2
    source.close()
    assert joined.closed
    assert not any(c.closed for c in callers)


def test_seek_is_expensive_sees_a_member_stream_under_every_pass_through_layer(
    tmp_path: Path,
) -> None:
    """A member stream re-decodes on a seek however it is wrapped; a file never does.

    Fails against a predicate that type-tests only the outermost object (the buffered
    shapes answer ``False``), and against a source or a join that drops the fact.
    """
    import zipfile

    from archivey.internal.measurement import SeekCounter
    from archivey.internal.source import seek_is_expensive
    from archivey.internal.streams.counting import SeekCountingStream

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("m", DATA)
    path = tmp_path / "f.bin"
    path.write_bytes(DATA)
    buf.seek(0)
    with (
        open_archive(buf, seekable_members=True) as reader,
        reader.open("m") as member,
    ):
        expensive = {
            "bare": member,
            "source": ArchiveSource.for_stream(member),
            "buffered": io.BufferedReader(member),  # type: ignore[arg-type]
            "source over buffer": ArchiveSource.for_stream(
                io.BufferedReader(member)  # type: ignore[arg-type]
            ),
            "seek counter": SeekCountingStream(member, SeekCounter()),
            "join": ArchiveSource.for_volumes(
                ConcatenatedFile([io.BytesIO(b"x"), member])
            ),
        }
        for label, stream in expensive.items():
            assert seek_is_expensive(stream), label
        cheap = {
            "BytesIO": io.BytesIO(DATA),
            "source over BytesIO": ArchiveSource.for_stream(io.BytesIO(DATA)),
            "path source": ArchiveSource.for_path(path),
            "join of files": ArchiveSource.for_volumes(ConcatenatedFile([path, path])),
        }
        for label, stream in cheap.items():
            assert not seek_is_expensive(stream), label
        for stream in (*expensive.values(), *cheap.values()):
            if isinstance(stream, ArchiveSource):
                stream.close()


def test_close_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "f.bin"
    path.write_bytes(DATA)
    source = ArchiveSource.for_path(path)
    source.read(1)
    source.close()
    source.close()
    assert source.closed


def test_a_half_built_source_leaves_nothing_to_finalize() -> None:
    """A constructor that raises must not leave a finalizer that fails.

    ``IOBase``'s finalizer calls ``close()``, which reads attributes a failed
    ``__init__`` may never have set. Outside dev mode CPython discards that error, so
    it is checked in a child interpreter under ``-X dev``, where the finalizer reports
    it on stderr.
    """
    code = (
        "from archivey.internal.source import ArchiveSource\n"
        "try:\n"
        "    ArchiveSource(path=None)\n"
        "except TypeError:\n"
        "    pass\n"
        "else:\n"
        "    raise SystemExit('not refused')\n"
    )
    result = subprocess.run(
        [sys.executable, "-X", "dev", "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""


# ---------------------------------------------------------------------------
# A path opens lazily
# ---------------------------------------------------------------------------


def test_a_path_source_opens_nothing_until_read(tmp_path: Path) -> None:
    """A backend that needs only the path (``unrar``, a directory) opens no handle."""
    path = tmp_path / "f.bin"
    path.write_bytes(DATA)
    source = ArchiveSource.for_path(path)
    assert source.path == path
    assert source.seekable() and is_seekable(source)
    assert source.tell() == 0
    assert source.size == len(DATA)
    assert source.name == str(path)
    assert source._reader is None
    source.close()


def test_a_directory_source_has_a_path_and_refuses_to_read(tmp_path: Path) -> None:
    source = ArchiveSource.for_path(tmp_path)
    assert source.is_directory
    assert source.path == tmp_path
    with pytest.raises(io.UnsupportedOperation):
        source.read(1)
    source.close()


# ---------------------------------------------------------------------------
# Bounded reads
# ---------------------------------------------------------------------------


def test_a_read_against_a_fact_length_is_clamped() -> None:
    """A ``BytesIO``'s length is a fact, so a read past it asks for no more than is left."""
    recorder = FactSizedReadRecorder(DATA)
    source = ArchiveSource.for_stream(recorder)
    source.seek(len(DATA) - 10)
    assert source.read(1 << 32) == DATA[-10:]
    assert max(recorder.requested) <= 10


class _UnderstatingNonSeekable(NonSeekableBytesIO):
    size = 10


def _understating_seekable_raw(data: bytes) -> ReadSizeRecorder:
    raw = ReadSizeRecorder(data)
    raw.size = 10
    return raw


@pytest.mark.parametrize(
    "build",
    [
        _UnderstatingNonSeekable,
        lambda data: io.BufferedReader(_understating_seekable_raw(data)),
        _understating_seekable_raw,
    ],
    ids=["non-seekable", "seekable-buffered", "seekable-raw"],
)
def test_a_hint_length_steps_instead_of_clamping(build) -> None:
    """An integer ``size`` attribute is a caller's claim and must not truncate a read.

    It understates here on purpose: clamping on it would return 10 bytes of a 100-byte
    stream. It is kept as ``size_hint`` and is not ``size``, which is what
    ``source_byte_size`` reads, so nothing built over the source clamps on it either.
    One case per full-count strategy, since each is built from the caller's object on
    its own branch.
    """
    caller = build(bytes(100))
    source = ArchiveSource.for_stream(caller)
    assert source.size_hint == 10
    assert source.size is None
    assert source_byte_size(source) is None
    assert len(source.read(100)) == 100


def test_an_unknown_length_is_served_in_steps() -> None:
    """No single request to the source exceeds the step, whatever ``n`` asked for."""
    recorder = ReadSizeRecorder(DATA, advertise_size=False)
    source = ArchiveSource.for_stream(recorder)
    assert source.read(1 << 32) == DATA
    assert max(recorder.requested) <= max(
        DEFAULT_UNKNOWN_LENGTH_READ_STEP, io.DEFAULT_BUFFER_SIZE
    )


def test_a_one_byte_non_seekable_source_still_returns_n_through_the_bound() -> None:
    """The bound runs over the full-count strategy, never over the raw inner.

    ``read_within_reach`` takes one read as final. Over the raw one-byte source it would
    return a single byte and stop.
    """
    source = ArchiveSource.for_stream(ShortReadNonSeekable(DATA, 1))
    assert source.read(300) == DATA[:300]


def test_a_rebased_source_clamps_from_its_new_origin() -> None:
    caller = FactSizedReadRecorder(b"junk" + DATA)
    caller.seek(4)
    source = ArchiveSource.for_stream(caller)
    source.seek(4)
    source.rebase_to_current_position()
    assert source.tell() == 0
    assert source.size == len(DATA)
    caller.requested.clear()
    assert source.read(1 << 32) == DATA
    # Clamped to what is left past the new origin, not the whole caller stream.
    assert max(caller.requested) <= len(DATA)


class _UnderstatingBytesIO(io.BytesIO):
    """A ``BytesIO`` whose fsspec-style ``size`` claims a quarter of what it holds."""

    size = 50_000


def _hinted_archive(fmt: str, payload: bytes) -> bytes:
    import tarfile
    import zipfile

    buf = io.BytesIO()
    if fmt == "tar":
        with tarfile.open(fileobj=buf, mode="w") as tar:
            info = tarfile.TarInfo("big.bin")
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
    else:
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
            zf.writestr("big.bin", payload)
    return buf.getvalue()


@pytest.mark.parametrize("fmt", ["tar", "zip"])
@pytest.mark.parametrize("prefix", [b"", b"x" * 1000], ids=["at-0", "mid-stream"])
def test_an_understating_size_hint_truncates_no_member(fmt: str, prefix: bytes) -> None:
    """A caller's ``size`` hint must not bound a slice or view built over the source.

    ``source_byte_size`` reads ``size`` first, and a slice or shared view probes its
    inner with it. Fails against ``size`` reporting the hint, on every case but TAR at
    offset 0: the rebase slice and ZIP's start-offset slice and member views then clamp
    to 50 000 bytes of a 200 000-byte member (ZIP mid-stream cannot even find its
    central directory).
    """
    from archivey import open_archive

    payload = os.urandom(200_000)
    caller = _UnderstatingBytesIO(prefix + _hinted_archive(fmt, payload))
    caller.seek(len(prefix))
    with open_archive(caller) as reader:
        with reader.open("big.bin") as stream:
            assert stream.read() == payload


# ---------------------------------------------------------------------------
# Cheap facts
# ---------------------------------------------------------------------------


def test_explicit_size_is_answered_for_a_non_seekable_source() -> None:
    """The fsspec ``size`` convention is kept as a hint, not as the size."""

    class _Sized(ShortReadNonSeekable):
        def __init__(self, data: bytes, size: int) -> None:
            super().__init__(data)
            self.size = size

    source = ArchiveSource.for_stream(_Sized(b"abc", 4096))
    assert source.size_hint == 4096
    assert source.size is None
    assert source.seekable() is False


def test_name_forwards_from_a_named_non_seekable_source() -> None:
    class _Named(ShortReadNonSeekable):
        name = "/tmp/pipe-ish.tar"

    source = ArchiveSource.for_stream(_Named(b"abc"))
    assert source_name(source) == "/tmp/pipe-ish.tar"
    assert source.name == "/tmp/pipe-ish.tar"


def test_name_absent_when_inner_has_none() -> None:
    source = ArchiveSource.for_stream(ShortReadNonSeekable(b"abc"))
    assert not hasattr(source, "name")
    with pytest.raises(AttributeError):
        _ = source.name


def test_fileno_forwards_for_a_caller_file(tmp_path: Path) -> None:
    path = tmp_path / "f.bin"
    path.write_bytes(DATA)
    with open(path, "rb") as handle:
        assert ArchiveSource.for_stream(handle).fileno() == handle.fileno()


@pytest.mark.skipif(_WINDOWS, reason="os.mkfifo is Unix-only")
def test_a_real_fifo_keeps_its_name_and_fileno(
    tmp_path: Path, named_fifo_with_writer: Callable[[Path, bytes], None]
) -> None:
    """``open(fifo, "rb")`` is a non-seekable ``BufferedReader`` that carries ``.name``.

    The caller's buffer reads for itself — no second full-count layer — and ``name``,
    ``fileno`` and ``seekable`` all answer through the source. ``close`` does not
    reach it.
    """
    fifo = tmp_path / "pipe-ish.tar"
    payload = b"hello from a named fifo"
    named_fifo_with_writer(fifo, payload)
    with open(fifo, "rb") as raw:
        assert raw.seekable() is False
        source = ArchiveSource.for_stream(raw)
        assert source_name(source) == str(fifo)
        assert source.seekable() is False
        assert source.fileno() == raw.fileno()
        assert source.peek(5) == payload[:5]
        assert source.read(len(payload)) == payload
        source.close()
        assert not raw.closed


@pytest.mark.skipif(_WINDOWS, reason="os.mkfifo is Unix-only")
def test_a_fifo_path_is_a_non_seekable_source_without_a_path(
    tmp_path: Path, named_fifo_with_writer: Callable[[Path, bytes], None]
) -> None:
    """``stat`` says a FIFO cannot reposition, so the path source must not claim it can.

    ``seekable()`` is settled at construction and ``is_seekable`` takes it at its word,
    so a ``True`` here could never be corrected downstream. ``path`` is ``None`` too: a
    backend handed the path would reopen the pipe and read different bytes.
    """
    fifo = tmp_path / "pipe.tar"
    payload = b"bytes through a named pipe"
    named_fifo_with_writer(fifo, payload)
    source = ArchiveSource.for_path(fifo)
    try:
        assert source.seekable() is False
        assert is_seekable(source) is False
        assert source.path is None
        assert source.name == str(fifo)
        assert source.peek(5) == payload[:5]
        assert source.read() == payload
    finally:
        source.close()


@pytest.mark.skipif(_WINDOWS, reason="os.mkfifo is Unix-only")
@pytest.mark.parametrize("streaming", [False, True])
def test_open_archive_on_a_fifo_path_behaves_as_a_pipe(
    tmp_path: Path,
    streaming: bool,
    named_fifo_with_writer: Callable[[Path, bytes], None],
) -> None:
    """Random access over a FIFO path is refused as a pipe's is, not a bare ``OSError``.

    Before the path source read its file type from ``stat``, the seek-needing backends
    were reached and failed with ``[Errno 29] Illegal seek``.
    """
    import tarfile

    from archivey import open_archive
    from archivey.exceptions import StreamNotSeekableError

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo("a.txt")
        info.size = 3
        tar.addfile(info, io.BytesIO(b"abc"))
    fifo = tmp_path / "archive.tar"
    named_fifo_with_writer(fifo, buf.getvalue())

    if not streaming:
        with pytest.raises(StreamNotSeekableError):
            open_archive(fifo)
        return
    with open_archive(fifo, streaming=True) as reader:
        read = [
            (member.name, stream.read() if stream else None)
            for member, stream in reader.stream_members()
        ]
    assert read == [("a.txt", b"abc")]


@pytest.mark.skipif(_WINDOWS, reason="os.mkfifo is Unix-only")
@pytest.mark.parametrize("seekable", [False, True])
def test_open_stream_on_a_fifo_path_reads_it_forward_only(
    tmp_path: Path,
    seekable: bool,
    named_fifo_with_writer: Callable[[Path, bytes], None],
) -> None:
    """``open_stream`` reads a FIFO path as ``open_archive`` does, forward-only.

    It used to gate on ``is_file()`` and report the pipe as a missing file. Fails
    against that gate: both arms then raise ``FileNotFoundError``.
    """
    import gzip

    from archivey import open_stream
    from archivey.exceptions import StreamNotSeekableError

    fifo = tmp_path / "payload.gz"
    named_fifo_with_writer(fifo, gzip.compress(b"hello" * 10))

    if seekable:
        with pytest.raises(StreamNotSeekableError):
            open_stream(fifo, seekable=True)
        return
    with open_stream(fifo) as stream:
        assert stream.read() == b"hello" * 10


def test_a_closed_source_refuses_to_read_a_borrowed_stream() -> None:
    """Whatever still holds a closed source must get an error, not the caller's bytes.

    A borrowed stream is still open after the source closes, so the source itself has
    to refuse. Fails against a close that leaves the reader in place: the read path
    reaches it without checking ``closed``.
    """
    caller = io.BytesIO(b"abcdefghij")
    source = ArchiveSource.for_stream(caller)
    assert source.read(3) == b"abc"
    source.close()
    with pytest.raises(ValueError, match="closed"):
        source.read(3)
    with pytest.raises(ValueError, match="closed"):
        source.readinto(bytearray(3))
    with pytest.raises(ValueError, match="closed"):
        source.tell()
    assert caller.tell() == 3
    assert not caller.closed
