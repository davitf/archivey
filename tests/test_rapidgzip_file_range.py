"""The rapidgzip child reads a member's bytes from the archive file itself.

A ZIP member in an archive on disk is a byte range of that file. On POSIX the child
gets the file's descriptor and the range (``OPEN_RANGE``) and reads with ``os.pread``,
so the compressed bytes do not cross a pipe. These tests cover the region detection
(``file_region``), the decoded result, and what the parent still checks.
"""

from __future__ import annotations

import io
import os
import random
import threading
import zipfile
from pathlib import Path

import pytest

from archivey import AcceleratorMode, ArchiveyConfig, open_archive
from archivey.exceptions import ArchiveyError
from archivey.internal.source import ArchiveSource
from archivey.internal.streams.codecs import rapidgzip_child
from archivey.internal.streams.codecs.rapidgzip_worker import OPEN, OPEN_RANGE
from archivey.internal.streams.streamtools import SharedView, SlicingStream
from archivey.internal.streams.streamtools.slice import file_region

_DATA = random.Random(3).randbytes(300_000) + bytes(300_000)

posix_only = pytest.mark.skipif(os.name != "posix", reason="pass_fds is POSIX only")


def _file(tmp_path: Path, data: bytes = bytes(range(256)) * 4) -> Path:
    path = tmp_path / "data.bin"
    path.write_bytes(data)
    return path


def test_a_plain_file_is_its_whole_region(tmp_path: Path) -> None:
    with open(_file(tmp_path), "rb") as f:
        region = file_region(f)
        assert region is not None
        assert (region.fd, region.start, region.length) == (f.fileno(), 0, 1024)
        raw_region = file_region(f.raw)
        assert raw_region is not None
        assert raw_region.length == 1024


def test_nested_views_compose_their_offsets_and_bounds(tmp_path: Path) -> None:
    with open(_file(tmp_path), "rb") as f:
        member = SharedView(f, start=100, length=500, lock=threading.Lock())
        bounded = SlicingStream(member, start=10, length=1000, owns_inner=False)
        outer = SharedView(bounded, start=5, length=50, lock=threading.Lock())
        region = file_region(outer)
        assert region is not None
        assert (region.start, region.length) == (115, 50)
        # The bytes the region names are the bytes the view reads.
        expected = (tmp_path / "data.bin").read_bytes()[region.start :][: region.length]
        assert expected == outer.read()
        # An inner bound shorter than the outer one wins.
        tail = SlicingStream(member, start=480, length=1000, owns_inner=False)
        tail_region = file_region(tail)
        assert tail_region is not None
        assert (tail_region.start, tail_region.length) == (580, 20)


def test_the_views_archive_checks_are_kept(tmp_path: Path) -> None:
    def check() -> None:
        raise ValueError("archive closed")

    with open(_file(tmp_path), "rb") as f:
        view = SharedView(
            f, start=0, length=10, lock=threading.Lock(), check_open=check
        )
        region = file_region(view)
        assert region is not None
        assert region.checks[0] is check
        # The last check is the file's own: the child's copy of the descriptor would
        # still read after the caller closes it.
        region.checks[-1]()
    with pytest.raises(ValueError, match="closed file"):
        region.checks[-1]()


def test_streams_that_are_not_plain_files_have_no_region(tmp_path: Path) -> None:
    assert file_region(io.BytesIO(b"abc")) is None

    class Reader(io.BufferedReader):
        pass

    with open(_file(tmp_path), "rb", buffering=0) as raw:
        # A subclass may change what a read returns.
        assert file_region(Reader(raw)) is None  # type: ignore[arg-type]
    read_end, write_end = os.pipe()
    with open(read_end, "rb") as pipe, open(write_end, "wb"):
        assert file_region(pipe) is None


def test_an_archive_source_over_a_path_is_looked_through(tmp_path: Path) -> None:
    source = ArchiveSource.for_path(_file(tmp_path))
    try:
        # The handle opens lazily: nothing to answer before the first read.
        assert file_region(SharedView(source, 0, 10, lock=threading.Lock())) is None
        source.read(1)
        region = file_region(SharedView(source, 7, 10, lock=threading.Lock()))
        assert region is not None
        assert (region.start, region.length) == (7, 10)
    finally:
        source.close()


def _zip(tmp_path: Path, method: int) -> Path:
    path = tmp_path / "member.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("first.txt", b"x" * 1000, compress_type=zipfile.ZIP_STORED)
        zf.writestr("data.bin", _DATA, compress_type=method)
    return path


def _open_kinds(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Record the kind of every child OPEN."""
    kinds: list[int] = []
    call = rapidgzip_child.RapidgzipChildStream._call

    def spy(self, tag, arg=0, payload=b"", **kwargs):  # type: ignore[no-untyped-def]
        if tag == OPEN:
            kinds.append(arg)
        return call(self, tag, arg, payload, **kwargs)

    monkeypatch.setattr(rapidgzip_child.RapidgzipChildStream, "_call", spy)
    return kinds


_ON = ArchiveyConfig(
    use_rapidgzip=AcceleratorMode.ON, use_indexed_bzip2=AcceleratorMode.ON
)


@posix_only
@pytest.mark.parametrize("method", [zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2])
def test_a_zip_member_is_read_from_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method: int
) -> None:
    pytest.importorskip("rapidgzip")
    kinds = _open_kinds(monkeypatch)
    with open_archive(_zip(tmp_path, method), config=_ON, seekable_members=True) as r:
        member = "data.bin"
        with r.open(member) as stream:
            assert stream.read() == _DATA
            stream.seek(250_000)
            assert stream.read(1000) == _DATA[250_000:251_000]
    assert kinds == [OPEN_RANGE]


def _read_after_close(path: Path, config: ArchiveyConfig, what: str) -> Exception:
    """Close the reader's archive handle, or the caller's file under it, while the
    member stream stays open; then read on."""
    with open(path, "rb") as f:
        r = open_archive(f, config=config, seekable_members=True)
        stream = r.open("data.bin")
        try:
            assert stream.read(10) == _DATA[:10]
            if what == "archive":
                # Not ``r.close()``: that closes the member stream first, and the read
                # would fail on the stream's own closed check before reaching the child.
                r._close_archive()  # type: ignore[attr-defined]
            else:
                f.close()
            with pytest.raises(Exception) as excinfo:
                stream.read(500_000)
            return excinfo.value
        finally:
            stream.close()
            r.close()


@posix_only
@pytest.mark.parametrize("what", ["archive", "caller's file"])
def test_a_member_read_after_a_close_raises_as_the_stdlib_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, what: str
) -> None:
    pytest.importorskip("rapidgzip")
    path = _zip(tmp_path, zipfile.ZIP_DEFLATED)
    expected = _read_after_close(
        path, ArchiveyConfig(use_rapidgzip=AcceleratorMode.OFF), what
    )
    kinds = _open_kinds(monkeypatch)
    # The child could still read the file; the parent's checks stop it.
    actual = _read_after_close(path, _ON, what)
    assert kinds == [OPEN_RANGE]
    assert (type(actual), str(actual)) == (type(expected), str(expected))


@posix_only
def test_a_damaged_member_raises_as_the_stdlib_does(tmp_path: Path) -> None:
    pytest.importorskip("rapidgzip")
    path = _zip(tmp_path, zipfile.ZIP_DEFLATED)
    with zipfile.ZipFile(path) as zf:
        info = zf.getinfo("data.bin")
    data = bytearray(path.read_bytes())
    # Into the member's compressed body, past its local header.
    data[info.header_offset + 30 + len("data.bin") + 5000] ^= 0xFF
    path.write_bytes(bytes(data))
    errors = []
    for config in (ArchiveyConfig(use_rapidgzip=AcceleratorMode.OFF), _ON):
        with (
            open_archive(path, config=config, seekable_members=True) as r,
            r.open("data.bin") as stream,
            pytest.raises(ArchiveyError) as excinfo,
        ):
            stream.read()
        errors.append(type(excinfo.value))
    assert errors[0] is errors[1]
