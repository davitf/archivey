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

import gzip
import io
import os
import tarfile
import threading
import zipfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import BinaryIO

import pytest

from archivey import ArchiveFormat, detect_format, open_archive

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


# TAR and TAR_GZ are not here: their backends read ahead at open (an 8 KiB buffer
# for TAR, a 64 KiB decoder feed for TAR_GZ) and still wait, also with ``format=``.
@pytest.mark.parametrize("buffered", [False, True], ids=["raw", "buffered"])
@pytest.mark.parametrize(
    ("make", "expected"),
    [(_zip, ArchiveFormat.ZIP), (_gzip, ArchiveFormat.GZ)],
    ids=["zip", "gz"],
)
def test_open_archive_does_not_wait_for_the_writer(
    make: Callable[[int], bytes], expected: ArchiveFormat, buffered: bool
) -> None:
    with _held_open_pipe(make(2000), buffered=buffered) as (stream, closed):
        with open_archive(stream, streaming=True) as reader:
            assert not closed.is_set(), "open_archive waited for the writer"
            assert reader.format == expected


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
