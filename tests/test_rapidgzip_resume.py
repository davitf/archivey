"""A cut DEFLATE-family stream read through rapidgzip delivers what the stdlib delivers.

rapidgzip decodes ahead of the reader, and on a stream that ends early 0.16 aborts the
child, taking with it output the reader never got: with every core decoding, a cut
file of tens of MB could deliver nothing where the standard library delivers all of it
up to the cut. The standard library now takes over from the last index point the
reader passed (``RapidgzipChildStream.resume_point``), so the two engines deliver the
same bytes and the same error. These tests pin that, the resume points themselves, and
the start-over when a resumed decode reaches the end of a DEFLATE stream.
"""

from __future__ import annotations

import base64
import functools
import gzip
import io
import os
import random
import signal
import sys
import zlib
from pathlib import Path

import pytest

from archivey.exceptions import TruncatedError
from archivey.internal.config import AcceleratorMode, StreamConfig
from archivey.internal.streams import codecs as codecs_module
from archivey.internal.streams.codecs import Codec, open_codec_stream
from archivey.internal.streams.decompress import (
    GzipDecompressorStream,
    ZlibDecompressorStream,
)
from archivey.internal.streams.decompressor_stream import DecompressorStream, SeekPoint
from archivey.internal.streams.deflate_resume import (
    WINDOW_SIZE,
    DeflateResume,
    DeflateResumeDecoder,
)
from archivey.internal.streams.rapidgzip_child import RapidgzipChildStream
from tests.conftest import requires

pytestmark = requires("rapidgzip")

_ON = StreamConfig(seekable=True, use_rapidgzip=AcceleratorMode.ON)
_OFF = StreamConfig(seekable=True, use_rapidgzip=AcceleratorMode.OFF)


@functools.cache
def _payload() -> bytes:
    """About 32 MB of base64 text: rapidgzip's index has a point every few MB."""
    return base64.encodebytes(random.Random(32).randbytes(24_000_000))


@functools.cache
def _compressed(codec: Codec) -> bytes:
    data = _payload()
    if codec is Codec.GZIP:
        return gzip.compress(data, 6)
    if codec is Codec.ZLIB:
        return zlib.compress(data, 6)
    compressor = zlib.compressobj(6, zlib.DEFLATED, -15)
    return compressor.compress(data) + compressor.flush()


def _stdlib(codec: Codec, source: object) -> DecompressorStream:
    if codec is Codec.GZIP:
        return GzipDecompressorStream(source)  # type: ignore[arg-type]
    wbits = zlib.MAX_WBITS if codec is Codec.ZLIB else -15
    return ZlibDecompressorStream(source, wbits=wbits)  # type: ignore[arg-type]


def _child(stream: object) -> RapidgzipChildStream:
    inner = stream
    while not isinstance(inner, RapidgzipChildStream):
        inner = getattr(inner, "_inner")
    return inner


def _read_cut(
    path: Path, codec: Codec, config: StreamConfig, mode: str
) -> tuple[bytes, Exception | None]:
    got = bytearray()
    source: object = str(path) if mode == "path" else path.open("rb")
    try:
        with open_codec_stream(codec, source, config=config) as stream:  # type: ignore[arg-type]
            while block := stream.read(1 << 20):
                got += block
    except Exception as exc:  # noqa: BLE001 - compared below
        return bytes(got), exc
    finally:
        if mode != "path":
            source.close()  # type: ignore[attr-defined]
    return bytes(got), None


@pytest.mark.parametrize("mode", ["path", "file"])
@pytest.mark.parametrize("keep", [0.3, 0.7, 0.999])
@pytest.mark.parametrize("codec", [Codec.GZIP, Codec.ZLIB, Codec.DEFLATE])
def test_a_cut_stream_delivers_what_the_standard_library_delivers(
    tmp_path: Path, codec: Codec, keep: float, mode: str
) -> None:
    """Whether the takeover finds an index point depends on how far rapidgzip got
    before the abort; either way the bytes and the error are the standard library's."""
    data = _compressed(codec)
    path = tmp_path / f"cut.{codec.value}"
    path.write_bytes(data[: int(len(data) * keep)])
    expected, expected_error = _read_cut(path, codec, _OFF, mode)
    got, error = _read_cut(path, codec, _ON, mode)
    assert isinstance(expected_error, TruncatedError)
    assert isinstance(error, TruncatedError)
    assert len(got) == len(expected)
    assert got == expected


@pytest.mark.parametrize("codec", [Codec.GZIP, Codec.ZLIB, Codec.DEFLATE])
def test_every_resume_point_reproduces_the_stream(tmp_path: Path, codec: Codec) -> None:
    """Each checkpoint the child stream takes, with its window from the output, starts
    the standard library at the right place: what it decodes there is the payload."""
    payload = _payload()
    path = tmp_path / f"whole.{codec.value}"
    path.write_bytes(_compressed(codec))
    points: list[SeekPoint] = []
    with open_codec_stream(codec, str(path), config=_ON) as stream:
        child = _child(stream)
        while stream.read(1 << 20):
            point = child.resume_point()
            if point is not None and (not points or point is not points[-1]):
                points.append(point)
    assert len(points) >= 3
    for point in points:
        assert isinstance(point.state, DeflateResume)
        assert (
            point.state.window
            == payload[
                max(
                    0, point.decompressed_offset - WINDOW_SIZE
                ) : point.decompressed_offset
            ]
        )
        with _stdlib(codec, str(path)) as stdlib:
            stdlib.add_seek_points([point])
            stdlib.seek(point.decompressed_offset + 1000)
            assert type(stdlib._decoder) is DeflateResumeDecoder
            at = point.decompressed_offset + 1000
            assert stdlib.read(200_000) == payload[at : at + 200_000]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
def test_a_resumed_decode_that_ends_its_member_starts_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash part-way through a two-member gzip: the standard library takes over at a
    point in the first member, and its resumed decode reaches the end of that member's
    DEFLATE stream, whose CRC-32 it cannot check. It starts over from the start, which
    checks it, and the caller gets every byte once and in order."""
    payload = _payload()
    path = tmp_path / "two.gz"
    path.write_bytes(
        gzip.compress(payload[:20_000_000], 6) + gzip.compress(payload[20_000_000:], 6)
    )
    resumed: list[DeflateResume] = []
    real_init = DeflateResumeDecoder.__init__

    def spy(
        self: DeflateResumeDecoder,
        resume: DeflateResume,
        *args: object,
        **kwargs: object,
    ) -> None:
        resumed.append(resume)
        real_init(self, resume, *args, **kwargs)  # type: ignore[arg-type]

    restarts: list[int] = []
    real_restart = codecs_module._StdlibOnAcceleratorError._restart_without_resume

    def count_restart(self: object) -> None:
        restarts.append(1)
        real_restart(self)  # type: ignore[arg-type]

    monkeypatch.setattr(DeflateResumeDecoder, "__init__", spy)
    monkeypatch.setattr(
        codecs_module._StdlibOnAcceleratorError,
        "_restart_without_resume",
        count_restart,
    )
    got = bytearray()
    with open_codec_stream(Codec.GZIP, str(path), config=_ON) as stream:
        child = _child(stream)
        while len(got) < 14_000_000:
            got += stream.read(1 << 20)
        point = child.resume_point()
        assert point is not None and point.decompressed_offset < 14_000_000
        assert child._proc is not None
        os.kill(child._proc.pid, signal.SIGABRT)
        child._proc.wait()
        while block := stream.read(1 << 20):
            got += block
    assert bytes(got) == payload
    assert resumed
    assert restarts


def test_a_corrupt_member_found_after_a_resume_is_still_corrupt(tmp_path: Path) -> None:
    """Damage that only the CRC-32 shows, in a member the takeover resumed inside: the
    start-over checks the CRC, and the error is the standard library's."""
    payload = _payload()[:8_000_000]
    data = bytearray(gzip.compress(payload, 6))
    data[-8] ^= 0xFF  # the CRC-32
    path = tmp_path / "crc.gz"
    path.write_bytes(bytes(data) + b"junk after the member")
    expected, expected_error = _read_cut(path, Codec.GZIP, _OFF, "path")
    got, error = _read_cut(path, Codec.GZIP, _ON, "path")
    assert expected_error is not None
    assert type(error) is type(expected_error)
    assert payload.startswith(got)


def test_the_window_needs_contiguous_output_after_a_seek(tmp_path: Path) -> None:
    """After a seek the output before the next point is not the child's last output, so
    no checkpoint is taken until a full window has been read since the seek."""
    payload = _payload()
    path = tmp_path / "whole.gz"
    path.write_bytes(_compressed(Codec.GZIP))
    with open_codec_stream(Codec.GZIP, str(path), config=_ON) as stream:
        child = _child(stream)
        stream.seek(20_000_000)
        while stream.tell() < 30_000_000:
            assert stream.read(1 << 20)
        point = child.resume_point()
    assert point is not None
    assert point.decompressed_offset >= 20_000_000 + WINDOW_SIZE
    assert isinstance(point.state, DeflateResume)
    start = point.decompressed_offset - WINDOW_SIZE
    assert point.state.window == payload[start : point.decompressed_offset]


def test_a_bytes_source_cut_stream_matches_too() -> None:
    data = _compressed(Codec.GZIP)
    cut = data[: int(len(data) * 0.8)]
    whole = zlib.decompressobj(31)
    expected = whole.decompress(cut)
    got = bytearray()
    with pytest.raises(TruncatedError):
        with open_codec_stream(Codec.GZIP, io.BytesIO(cut), config=_ON) as stream:
            while block := stream.read(1 << 20):
                got += block
    assert bytes(got) == expected
