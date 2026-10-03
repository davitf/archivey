"""A cut bzip2 or zlib stream reads the same with the accelerator on as with it off.

Maintainer ruling: a truncated stream read under ``AUTO`` or ``ON`` raises the error the
standard library raises with the accelerator ``OFF``, after the same bytes. rapidgzip's
bzip2 decoder raises an opaque ``RuntimeError('std::exception')`` on a cut stream, so
the standard library takes over (``_StdlibOnAcceleratorError``) at the newest block the
accelerator had indexed at or before the reader (``bzip2_resume``), and gives the
verdict. rapidgzip reads a zlib stream cut mid-body as a short stream with no error;
the Adler-32 check hands it to the standard library the same way.

Each cut lands in one class of place: inside the first block (no block boundary before
it, so the takeover starts at the origin), inside a later block, and inside the
end-of-stream marker (for zlib, the Adler-32 trailer).
"""

from __future__ import annotations

import bz2
import functools
import io
import random
import zlib
from pathlib import Path

import pytest

from archivey.config import AcceleratorMode
from archivey.exceptions import CorruptionError, TruncatedError
from archivey.internal.config import StreamConfig
from archivey.internal.streams.codecs import Codec, open_codec_stream
from archivey.internal.streams.decompressor_stream import DecompressorStream
from tests.conftest import requires

pytestmark = requires("rapidgzip")

_MODES = [AcceleratorMode.AUTO, AcceleratorMode.ON]


@functools.cache
def _payload() -> bytes:
    """About 1.8 MB of mixed text and noise: about eighteen 100 kB bzip2 blocks."""
    rng = random.Random(7)
    words = [b"alpha ", b"beta ", b"gamma\n"]
    return b"".join(
        rng.choice(words) if rng.random() < 0.75 else bytes([rng.randrange(256)])
        for _ in range(400_000)
    )


@functools.cache
def _bzip2() -> bytes:
    # Level 1: 100 kB blocks, so a later block is not far in.
    return bz2.compress(_payload(), 1)


@functools.cache
def _bzip2_two_streams() -> bytes:
    """Two streams of different levels: a resume in the second needs its blocks."""
    return bz2.compress(_payload()[:700_000], 3) + bz2.compress(_payload(), 1)


@functools.cache
def _block_bits(blob: bytes) -> list[int]:
    """The bit offset of every block magic and end-of-stream marker, in order."""
    import rapidgzip

    with rapidgzip.IndexedBzip2File(io.BytesIO(blob), parallelization=0) as f:
        f.read()
        return sorted(f.block_offsets())


def _bzip2_cut(where: str) -> bytes:
    blob = _bzip2()
    bits = _block_bits(blob)
    # bits: the first block, ..., the end-of-stream marker, the byte-aligned end.
    if where == "first-block":
        return blob[: bits[1] // 8 - 1000]
    if where == "later-block":
        return blob[: (bits[7] + bits[8]) // 16]
    if where == "end-of-stream":
        return blob[: bits[-2] // 8 + 4]
    if where == "second-stream":
        two = _bzip2_two_streams()
        two_bits = _block_bits(two)
        return two[: (two_bits[-4] + two_bits[-3]) // 16]
    raise AssertionError(where)


@functools.cache
def _zlib() -> bytes:
    return zlib.compress(_payload(), 6)


def _zlib_cut(where: str) -> bytes:
    blob = _zlib()
    if where == "first-block":
        return blob[:200]
    if where == "later-block":
        return blob[: len(blob) // 2]
    if where == "end-of-stream":
        return blob[:-2]  # inside the Adler-32 trailer
    raise AssertionError(where)


def _read(
    codec: Codec, blob: bytes, config: StreamConfig, source: str, chunk: int, tmp: Path
) -> tuple[bytes, type[Exception] | None]:
    got = bytearray()
    if source == "path":
        path = tmp / "cut"
        path.write_bytes(blob)
        src: object = str(path)
    else:
        src = io.BytesIO(blob)
    try:
        with open_codec_stream(codec, src, config=config) as stream:  # type: ignore[arg-type]
            while block := stream.read(chunk):
                got += block
                if chunk < 0:
                    break
    except Exception as exc:  # noqa: BLE001 - compared below
        return bytes(got), type(exc)
    return bytes(got), None


def _bzip2_config(mode: AcceleratorMode) -> StreamConfig:
    return StreamConfig(seekable=True, use_indexed_bzip2=mode)


def _zlib_config(mode: AcceleratorMode, *, declared: bool) -> StreamConfig:
    # AUTO takes rapidgzip only with a declared size it can verify.
    return StreamConfig(
        seekable=True,
        use_rapidgzip=mode,
        expected_decompressed_size=len(_payload()) if declared else None,
    )


@pytest.mark.parametrize("chunk", [1 << 16, -1])
@pytest.mark.parametrize("source", ["path", "file"])
@pytest.mark.parametrize("mode", _MODES, ids=lambda m: m.name)
@pytest.mark.parametrize(
    "where", ["first-block", "later-block", "end-of-stream", "second-stream"]
)
def test_a_cut_bzip2_reads_as_it_does_with_the_accelerator_off(
    tmp_path: Path, where: str, mode: AcceleratorMode, source: str, chunk: int
) -> None:
    blob = _bzip2_cut(where)
    off = _read(
        Codec.BZIP2, blob, _bzip2_config(AcceleratorMode.OFF), source, chunk, tmp_path
    )
    assert off[1] is TruncatedError
    got = _read(Codec.BZIP2, blob, _bzip2_config(mode), source, chunk, tmp_path)
    assert (len(got[0]), got[1]) == (len(off[0]), off[1])
    assert got[0] == off[0]


def test_a_damaged_bzip2_block_reads_as_it_does_with_the_accelerator_off(
    tmp_path: Path,
) -> None:
    """A damaged block after a resume point: the resumed decode cannot tell the damage
    from the end of the stream, so the takeover decodes from the start, which raises at
    the damage after the same bytes."""
    blob = bytearray(_bzip2())
    bits = _block_bits(bytes(blob))
    blob[(bits[9] + bits[10]) // 16] ^= 0x55
    off = _read(
        Codec.BZIP2,
        bytes(blob),
        _bzip2_config(AcceleratorMode.OFF),
        "file",
        1 << 16,
        tmp_path,
    )
    assert off[1] is CorruptionError
    for mode in _MODES:
        got = _read(
            Codec.BZIP2, bytes(blob), _bzip2_config(mode), "file", 1 << 16, tmp_path
        )
        assert (len(got[0]), got[1]) == (len(off[0]), off[1])


@pytest.mark.parametrize("chunk", [1 << 16, -1])
@pytest.mark.parametrize("source", ["path", "file"])
@pytest.mark.parametrize(
    ("mode", "declared"),
    [
        (AcceleratorMode.ON, False),
        (AcceleratorMode.ON, True),
        (AcceleratorMode.AUTO, True),
    ],
    ids=["ON", "ON-declared", "AUTO-declared"],
)
@pytest.mark.parametrize("where", ["first-block", "later-block", "end-of-stream"])
def test_a_cut_zlib_reads_as_it_does_with_the_accelerator_off(
    tmp_path: Path,
    where: str,
    mode: AcceleratorMode,
    declared: bool,
    source: str,
    chunk: int,
) -> None:
    blob = _zlib_cut(where)
    off_config = _zlib_config(AcceleratorMode.OFF, declared=declared)
    off = _read(Codec.ZLIB, blob, off_config, source, chunk, tmp_path)
    assert off[1] is TruncatedError
    got = _read(
        Codec.ZLIB, blob, _zlib_config(mode, declared=declared), source, chunk, tmp_path
    )
    if declared and where == "end-of-stream" and chunk > 0:
        # The stated exception. The read that reaches the declared size is the
        # VerifyingStream's verifying event, and its probe past the size meets the
        # cut trailer; a verifying event that fails withholds its own chunk. Without
        # the accelerator nothing verifies the size, and that chunk is delivered.
        assert got[1] is off[1]
        assert off[0].startswith(got[0])
        assert len(off[0]) - len(got[0]) <= chunk
        return
    assert (len(got[0]), got[1]) == (len(off[0]), off[1])
    assert got[0] == off[0]


def _decompressor(stream: object) -> DecompressorStream:
    inner = stream
    while not isinstance(inner, DecompressorStream):
        inner = getattr(inner, "_inner")
    return inner


@pytest.mark.parametrize("mode", _MODES, ids=lambda m: m.name)
def test_after_a_takeover_seeks_back_and_forward_read_the_data(
    mode: AcceleratorMode,
) -> None:
    """The takeover resumes at a block, and keeps the blocks before it as seek points: a
    seek back before them decodes from the start, a seek forward resumes again, and the
    cut still raises.

    The cut is near the end: rapidgzip decodes ahead of the reader, and on a cut it
    meets before the first read returns, the reader is at 0 and the takeover starts
    at the origin."""
    blob = _bzip2_cut("end-of-stream")
    data = _payload()
    with open_codec_stream(
        Codec.BZIP2, io.BytesIO(blob), config=_bzip2_config(mode)
    ) as s:
        got = bytearray()
        with pytest.raises(TruncatedError):
            while block := s.read(1 << 16):
                got += block
        assert bytes(got) == data[: len(got)]
        stdlib = _decompressor(s)
        resumes = [
            p for p in stdlib._seek_points if type(p.state).__name__ == "Bzip2Resume"
        ]
        assert resumes, "the takeover started from the origin"

        assert s.seek(1000) == 1000
        assert s.read(5000) == data[1000:6000]
        forward = resumes[-1].decompressed_offset + 10
        assert s.seek(forward) == forward
        assert s.read(5000) == data[forward : forward + 5000]
        with pytest.raises(TruncatedError):
            while s.read(1 << 16):
                pass


def test_a_read_after_a_seek_past_the_cut_raises_as_with_the_accelerator_off() -> None:
    blob = _bzip2_cut("later-block")
    for mode in [AcceleratorMode.OFF, *_MODES]:
        with open_codec_stream(
            Codec.BZIP2, io.BytesIO(blob), config=_bzip2_config(mode)
        ) as s:
            # Every engine defers the seek's decode to the read after it.
            target = len(_payload()) - 10
            assert s.seek(target) == target
            with pytest.raises(TruncatedError):
                s.read(100)
