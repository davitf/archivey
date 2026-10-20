"""Detection over a real pipe whose writer has sent a whole archive and keeps it open.

Detection reads a 4 096-byte window. An archive shorter than that, sent down a pipe
whose writer does not close it, used to hold ``open_archive`` until the writer closed
the pipe. Detection now reads what has arrived and stops once no later byte can
change its answer.

The writer holds the pipe open until the test releases it, or for ``_HOLD_SECONDS``
at most. A detection that waits for the end of the source therefore returns only
after the writer closes, and the test fails on the flag that records the close, not
on a clock. A regression fails after ``_HOLD_SECONDS`` and does not hang.
"""

from __future__ import annotations

import bz2
import gzip
import io
import lzma
import os
import tarfile
import threading
import zipfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import BinaryIO

import pytest

from archivey import ArchiveFormat, detect_format, open_archive
from archivey.types import ContainerFormat, StreamFormat
from tests.streams_util import STREAM_COMPRESSORS

_HOLD_SECONDS = 10.0


def _zip(size: int) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.bin", os.urandom(size))
    return buf.getvalue()


def _tar(size: int) -> bytes:
    """A TAR without the 10 KiB record padding, so it is shorter than the window."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tf:
        data = os.urandom(size)
        info = tarfile.TarInfo("a.bin")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    # Header, data padded to a block, and the two zero blocks that end the archive.
    return buf.getvalue()[: 512 + -(-size // 512) * 512 + 1024]


def _gzip(size: int) -> bytes:
    return gzip.compress(os.urandom(size), mtime=0)


def _tar_gz(size: int) -> bytes:
    return gzip.compress(_tar(size), mtime=0)


@contextmanager
def _held_open_pipe(
    payload: bytes, *, buffered: bool
) -> Iterator[tuple[BinaryIO, threading.Event]]:
    """A pipe holding ``payload`` and a flag set when the writer closes its end."""
    assert len(payload) < 4096, "one write fits the pipe buffer on every platform"
    read_fd, write_fd = os.pipe()
    release = threading.Event()
    closed = threading.Event()

    def writer() -> None:
        try:
            os.write(write_fd, payload)
            release.wait(_HOLD_SECONDS)
        finally:
            closed.set()
            os.close(write_fd)

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    stream = open(read_fd, "rb", buffering=-1 if buffered else 0)  # noqa: SIM115
    try:
        yield stream, closed  # type: ignore[misc]  # FileIO when unbuffered
    finally:
        release.set()
        thread.join()
        stream.close()


_CASES: list[tuple[str, Callable[[int], bytes], ArchiveFormat]] = [
    ("zip", _zip, ArchiveFormat.ZIP),
    ("tar", _tar, ArchiveFormat.TAR),
    ("gz", _gzip, ArchiveFormat.GZ),
    ("tar.gz", _tar_gz, ArchiveFormat.TAR_GZ),
]


@pytest.mark.parametrize("buffered", [False, True], ids=["raw", "buffered"])
@pytest.mark.parametrize(
    ("make", "expected"),
    [(make, fmt) for _, make, fmt in _CASES],
    ids=[name for name, _, _ in _CASES],
)
def test_detect_format_does_not_wait_for_the_writer(
    make: Callable[[int], bytes], expected: ArchiveFormat, buffered: bool
) -> None:
    payload = make(2000)
    # The answer on the bytes alone, with the end of the source in view.
    assert detect_format(io.BytesIO(payload)).format == expected
    with _held_open_pipe(payload, buffered=buffered) as (stream, closed):
        info = detect_format(stream)
        assert not closed.is_set(), "detection waited for the writer to close the pipe"
    assert info.format == expected


@pytest.mark.parametrize("buffered", [False, True], ids=["raw", "buffered"])
@pytest.mark.parametrize(
    ("make", "expected"),
    [
        (_zip, ArchiveFormat.ZIP),
        (_gzip, ArchiveFormat.GZ),
        (_tar, ArchiveFormat.TAR),
        (_tar_gz, ArchiveFormat.TAR_GZ),
    ],
    ids=["zip", "gz", "tar", "tar.gz"],
)
def test_open_archive_does_not_wait_for_the_writer(
    make: Callable[[int], bytes], expected: ArchiveFormat, buffered: bool
) -> None:
    with _held_open_pipe(make(2000), buffered=buffered) as (stream, closed):
        with open_archive(stream, streaming=True) as reader:
            assert not closed.is_set(), "open_archive waited for the writer"
            assert reader.format == expected


# Brotli is not here: its decoder keeps a full 64 KiB feed, because it cannot find the
# end of its stream inside a feed (``BrotliDecoder``).
_TAR_STREAMS = [
    None,
    *(f for f in STREAM_COMPRESSORS if f is not StreamFormat.BROTLI),
]


@pytest.mark.parametrize("buffered", [False, True], ids=["raw", "buffered"])
@pytest.mark.parametrize(
    "stream_format",
    _TAR_STREAMS,
    ids=lambda f: "tar" if f is None else f"tar.{f.value}",
)
def test_a_streaming_tar_yields_its_first_member_before_the_writer_closes(
    stream_format: StreamFormat | None, buffered: bool
) -> None:
    """The first member and its bytes arrive while the writer still holds the pipe.

    ``format=`` is passed, since a short bzip2 or xz hit waits in detection (the
    trailer step needs the length). The test leaves the pass after that member: the
    end of the listing waits for the writer by design, because the trailing-data scan
    reads past the trailer.
    """
    data = b"0123456789abcdef" * 125
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.USTAR_FORMAT) as tf:
        info = tarfile.TarInfo("a.bin")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    tar = buf.getvalue()[: 512 + -(-len(data) // 512) * 512 + 1024]
    if stream_format is None:
        payload, fmt = tar, ArchiveFormat.TAR
    else:
        payload = STREAM_COMPRESSORS[stream_format](tar)
        fmt = ArchiveFormat(ContainerFormat.TAR, stream_format)
    with _held_open_pipe(payload, buffered=buffered) as (stream, closed):
        with open_archive(stream, streaming=True, format=fmt) as reader:
            for member, member_stream in reader.stream_members():
                assert member_stream is not None
                got = member_stream.read()
                assert not closed.is_set(), "the first member waited for the writer"
                break
    assert member.name == "a.bin"
    assert got == data


class _Trickle(io.RawIOBase):
    """A forward-only raw stream that returns at most ``step`` bytes per read."""

    def __init__(self, data: bytes, step: int) -> None:
        super().__init__()
        self._data = data
        self._step = step

    def readable(self) -> bool:
        return True

    def readinto(self, b: bytearray | memoryview) -> int:  # type: ignore[override]
        take = min(len(b), self._step, len(self._data))
        b[:take] = self._data[:take]
        self._data = self._data[take:]
        return take


def test_an_early_signature_does_not_settle_before_an_earlier_table_entry() -> None:
    """ZIP magic at offset 0 with ``ustar`` at 257 is TAR, read in small pieces too.

    The TAR row comes before the ZIP rows in the magic table, so the ZIP match
    alone does not settle the answer: detection keeps reading until offset 262.
    """
    payload = b"PK\x03\x04" + b"\0" * 253 + b"ustar" + b"\0" * 250
    assert detect_format(io.BytesIO(payload)).format == ArchiveFormat.TAR
    assert detect_format(_Trickle(payload, 100)).format == ArchiveFormat.TAR  # type: ignore[arg-type]  # RawIOBase double


def test_a_streaming_tar_read_in_small_pieces_matches_the_whole_bytes() -> None:
    """The TAR walk fills its buffer from what has arrived, and reads the same tar.

    The source returns at most 7 bytes per read, so every 512-byte header and every
    member's data is gathered from many short fills.
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tf:
        for i, size in enumerate((0, 1, 511, 513, 9000)):
            info = tarfile.TarInfo(f"m{i}-" + "n" * 120)
            info.size = size
            tf.addfile(info, io.BytesIO(bytes([i]) * size))
    payload = buf.getvalue()

    def contents(source: BinaryIO) -> list[tuple[str, bytes]]:
        with open_archive(source, streaming=True) as reader:
            return [
                (member.name, stream.read() if stream is not None else b"")
                for member, stream in reader.stream_members()
            ]

    expected = contents(io.BytesIO(payload))
    assert len(expected) == 5
    assert contents(_Trickle(payload, 7)) == expected  # type: ignore[arg-type]  # RawIOBase double


_KOLY = b"koly" + (4).to_bytes(4, "big") + (512).to_bytes(4, "big")


def _short_udif(first_block: bytes) -> bytes:
    """A first block and a 512-byte ``koly`` trailer: the shape UDIF images end in."""
    trailer = bytearray(512)
    trailer[: len(_KOLY)] = _KOLY
    return first_block + bytes(trailer)


@pytest.mark.parametrize("compress", [bz2.compress, lzma.compress], ids=["bz2", "xz"])
def test_a_short_udif_image_read_forward_only_is_the_image(
    compress: Callable[[bytes], bytes],
) -> None:
    """A bzip2 or xz hit does not settle the near peek: the trailer can outrank it.

    The image fits in the 4 KiB window, so its ``koly`` block is in the prefix and
    the trailer matches on a forward-only source too, as it does on a file.
    """
    image = _short_udif(compress(b"first block"))
    assert len(image) < 4096
    assert detect_format(io.BytesIO(image)).format == ArchiveFormat.DMG
    assert detect_format(_Trickle(image, 4096)).format == ArchiveFormat.DMG  # type: ignore[arg-type]  # RawIOBase double


class _SignalsFirstRead(io.RawIOBase):
    """The read end of a pipe that sets ``first_read`` once its first read returns."""

    def __init__(self, fd: int) -> None:
        super().__init__()
        self._file = open(fd, "rb", buffering=0)  # noqa: SIM115
        self.first_read = threading.Event()
        self.reads = 0

    def readable(self) -> bool:
        return True

    def readinto(self, b: bytearray | memoryview) -> int | None:  # type: ignore[override]
        got = self._file.readinto(b)
        self.reads += 1
        self.first_read.set()
        return got

    def close(self) -> None:
        self._file.close()
        super().close()


def test_an_inner_tar_probe_waits_only_for_the_bytes_it_needs() -> None:
    """The second installment arrives after detection has read the first, and
    the probe decodes its TAR header from both without waiting for the close.

    The first installment settles the near magic (gzip at 0, past offset 262) but,
    stored without compression, holds fewer than 512 decoded bytes, so the
    inner-TAR probe has to read again.
    """
    payload = gzip.compress(_tar(2000), compresslevel=0, mtime=0)
    first, rest = payload[:300], payload[300:]
    read_fd, write_fd = os.pipe()
    stream = _SignalsFirstRead(read_fd)
    release = threading.Event()
    closed = threading.Event()

    def writer() -> None:
        try:
            os.write(write_fd, first)
            if stream.first_read.wait(_HOLD_SECONDS):
                os.write(write_fd, rest)
                release.wait(_HOLD_SECONDS)
        finally:
            closed.set()
            os.close(write_fd)

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    try:
        info = detect_format(stream)  # type: ignore[arg-type]  # RawIOBase over the pipe
        assert not closed.is_set(), "detection waited for the writer to close the pipe"
        assert stream.reads >= 2, "the probe never needed the second installment"
    finally:
        release.set()
        thread.join()
        stream.close()
    assert info.format == ArchiveFormat.TAR_GZ
