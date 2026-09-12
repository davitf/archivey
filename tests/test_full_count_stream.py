"""Full-count reads at the source boundary for non-seekable sources.

``ensure_full_count_reads`` used to return a non-seekable source unchanged, so a
legal short ``read(n)`` looked like EOF to every header parser downstream. These
tests pin the wrapper that closes that gap: full-count, zero read-ahead, still
non-seekable, transparent to the metadata probes.
"""

from __future__ import annotations

import io
import os
import sys
import threading
from pathlib import Path

import pytest

from archivey.internal.streams.peekable import PeekableStream
from archivey.internal.streams.streamtools import (
    ensure_bufferedio,
    ensure_full_count_reads,
    source_byte_size,
    source_name,
)
from tests.streams_util import ShortReadNonSeekable

# Larger than BufferedReader's default so the over-read contrast is a partial
# fill, not EOF. 3.14 raised DEFAULT_BUFFER_SIZE from 8 KiB to 128 KiB (gh-117151);
# a 10 KiB payload is swallowed whole on that version.
DATA = bytes(range(256)) * (io.DEFAULT_BUFFER_SIZE // 256 + 8)
_WINDOWS = sys.platform == "win32"


def test_ensure_full_count_reads_coalesces_non_seekable_short_reads() -> None:
    """The boundary, not a backend: ``read(n)`` on the returned stream yields ``n``."""
    source = ShortReadNonSeekable(DATA, 1)
    wrapped = ensure_full_count_reads(source)
    assert wrapped.read(20) == DATA[:20]
    assert source.consumed == 20
    assert wrapped.read(100) == DATA[20:120]
    assert source.consumed == 120


def test_ensure_full_count_reads_consumes_exactly_what_was_asked() -> None:
    """After ``read(n)``, exactly ``n`` bytes have been taken from the source.

    ``max_chunk=1`` is the coalescing case. A generous inner (next test) is what
    would fail if this were implemented with ``BufferedReader``.
    """
    source = ShortReadNonSeekable(DATA, 1)
    wrapped = ensure_full_count_reads(source)
    assert wrapped.read(20) == DATA[:20]
    assert source.consumed == 20


def test_ensure_full_count_reads_does_not_read_ahead() -> None:
    """Zero read-ahead when the inner *can* fill a BufferedReader buffer.

    ``ensure_bufferedio`` on this same source takes ``io.DEFAULT_BUFFER_SIZE``
    for a ``read(20)``.
    """
    source = ShortReadNonSeekable(DATA, max_chunk=len(DATA))
    wrapped = ensure_full_count_reads(source)
    assert wrapped.read(20) == DATA[:20]
    assert source.consumed == 20


def test_ensure_full_count_reads_leaves_existing_buffer() -> None:
    """A caller's ``BufferedReader`` is already full-count; wrapping it drops ``fileno()``."""
    source = ShortReadNonSeekable(DATA, max_chunk=len(DATA))
    buffered = io.BufferedReader(source)
    assert ensure_full_count_reads(buffered) is buffered


def test_buffered_reader_over_non_seekable_over_reads() -> None:
    """The seekable-branch wrapper is unsafe on a pipe: it reads ahead."""
    source = ShortReadNonSeekable(DATA, max_chunk=len(DATA))
    buffered = ensure_bufferedio(source)
    assert buffered.read(20) == DATA[:20]
    assert source.consumed == io.DEFAULT_BUFFER_SIZE


def test_ensure_full_count_reads_is_idempotent_on_full_count_stream() -> None:
    source = ShortReadNonSeekable(DATA, 1)
    wrapped = ensure_full_count_reads(source)
    assert ensure_full_count_reads(wrapped) is wrapped


def test_boundary_stream_stays_non_seekable_and_tell_raises() -> None:
    wrapped = ensure_full_count_reads(ShortReadNonSeekable(DATA, 1))
    assert wrapped.seekable() is False
    with pytest.raises(io.UnsupportedOperation):
        wrapped.tell()
    with pytest.raises(io.UnsupportedOperation):
        wrapped.seek(0)
    with pytest.raises(io.UnsupportedOperation):
        wrapped.fileno()


def test_read_negative_one_drains_even_when_inner_shorts_on_drain() -> None:
    """``read(-1)`` must not lean on the inner's ``readall()``.

    ``cap_drain`` makes ``read(-1)`` illegal RawIOBase behaviour (it caps instead
    of draining). The wrapper still has to return every remaining byte.
    """
    source = ShortReadNonSeekable(DATA, 1, cap_drain=True)
    wrapped = ensure_full_count_reads(source)
    assert wrapped.read(-1) == DATA
    assert source.consumed == len(DATA)


def test_sized_read_past_eof_returns_the_remainder() -> None:
    """Stop-on-empty, not raise: a sized read past EOF is a short return.

    Collapsing ``read`` into a bare ``read_exact`` that raised, or a loop that
    treated empty as "ask again", would break this edge. ``read(0)`` is a
    no-op (``compressed-streams``).
    """
    source = ShortReadNonSeekable(b"abcde", 1)
    wrapped = ensure_full_count_reads(source)
    assert wrapped.read(0) == b""
    assert source.consumed == 0
    assert wrapped.read(100) == b"abcde"
    assert source.consumed == 5
    assert wrapped.read(5) == b""
    buf = bytearray(4)
    assert wrapped.readinto(buf) == 0


def test_readall_drains_a_short_inner() -> None:
    source = ShortReadNonSeekable(DATA, 1)
    wrapped = ensure_full_count_reads(source)
    assert wrapped.readall() == DATA


def test_readinto_inherits_full_count_from_read() -> None:
    source = ShortReadNonSeekable(DATA, 1)
    wrapped = ensure_full_count_reads(source)
    buf = bytearray(50)
    assert wrapped.readinto(buf) == 50
    assert bytes(buf) == DATA[:50]
    assert source.consumed == 50


def test_peel_forwards_explicit_size_not_a_fifo() -> None:
    """A FIFO has no cheap size either way; this is the fsspec ``size`` convention."""

    class _Sized(ShortReadNonSeekable):
        def __init__(self, data: bytes, size: int) -> None:
            super().__init__(data)
            self.size = size

    source = _Sized(b"abc", 4096)
    assert source_byte_size(source) == 4096
    wrapped = ensure_full_count_reads(source)
    assert source_byte_size(wrapped) == 4096
    assert wrapped.seekable() is False


def test_name_forwards_from_a_named_non_seekable_source() -> None:
    class _Named(ShortReadNonSeekable):
        name = "/tmp/pipe-ish.tar"

    source = _Named(b"abc")
    wrapped = ensure_full_count_reads(source)
    assert source_name(wrapped) == "/tmp/pipe-ish.tar"
    assert wrapped.name == "/tmp/pipe-ish.tar"
    peek = PeekableStream(wrapped)
    assert peek.name == "/tmp/pipe-ish.tar"


def test_name_absent_when_inner_has_none() -> None:
    wrapped = ensure_full_count_reads(ShortReadNonSeekable(b"abc"))
    assert not hasattr(wrapped, "name")
    with pytest.raises(AttributeError):
        _ = wrapped.name


def _open_named_fifo(path: Path, payload: bytes) -> io.BufferedReader:
    """Open a named FIFO for reading, with a writer thread to unblock ``open()``."""
    os.mkfifo(path)

    def _fill() -> None:
        try:
            with open(path, "wb") as writer:
                writer.write(payload)
        except OSError:
            pass

    threading.Thread(target=_fill, daemon=True).start()
    return open(path, "rb")  # type: ignore[return-value]


@pytest.mark.skipif(_WINDOWS, reason="os.mkfifo is Unix-only")
def test_full_count_wrapper_preserves_real_fifo_name(tmp_path: Path) -> None:
    """``open(fifo, "rb")`` is a non-seekable ``BufferedReader`` that carries ``.name``.

    The seekable branch keeps that name for free (``BufferedReader.name`` forwards
    at C level). The non-seekable branch returns the same buffer unchanged, so
    ``fileno()`` stays intact too — wrapping it would make the two halves of one
    function disagree about the same source.
    """
    fifo = tmp_path / "pipe-ish.tar"
    payload = b"hello from a named fifo"
    with _open_named_fifo(fifo, payload) as raw:
        assert raw.seekable() is False
        assert raw.name == str(fifo)
        wrapped = ensure_full_count_reads(raw)
        assert wrapped is raw
        assert source_name(wrapped) == str(fifo)
        assert wrapped.name == str(fifo)
        assert wrapped.seekable() is False
        assert wrapped.fileno() == raw.fileno()
        peek = PeekableStream(wrapped)
        assert peek.name == str(fifo)
        assert wrapped.read(len(payload)) == payload
