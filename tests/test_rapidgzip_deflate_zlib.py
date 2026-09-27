"""rapidgzip acceleration for raw deflate and zlib (and the AUTO size gate).

Covers the ``rapidgzip-deflate-zlib-acceleration`` change: parity with stdlib, gating
(OFF / absent / below-AUTO-threshold / ON-forces), error translation, and the
bounded-input contract. Gzip's accelerator path is already covered by
``test_accelerator_corruption.py``; these tests focus on the DEFLATE-family extension
and the shared size gate.
"""

from __future__ import annotations

import gzip
import io
import os
import random
import zlib
from pathlib import Path

import pytest

from archivey import open_archive
from archivey.config import RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE
from archivey.exceptions import CorruptionError, ReadError, TruncatedError
from archivey.internal.config import AcceleratorMode, StreamConfig
from archivey.internal.streams import codecs
from archivey.internal.streams.codecs import Codec, open_codec_stream
from archivey.internal.streams.decompressor_stream import DecompressorStream
from archivey.internal.streams.rapidgzip_child import RapidgzipChildStream
from archivey.internal.streams.streamtools import SlicingStream
from archivey.internal.streams.verify import VerifyingStream

# Large enough that compressed size exceeds the AUTO threshold for less-compressible
# payloads; used when a test needs AUTO to select rapidgzip.
_LARGE = os.urandom(2 * 1024 * 1024)
# The AUTO threshold these tests run against. The shipped one (16 MiB) would need
# inputs eight times larger; `_low_auto_threshold` lowers it for the tests that
# exercise AUTO selecting rapidgzip.
_TEST_THRESHOLD = 1024 * 1024
_SMALL = b"the quick brown fox jumps over the lazy dog\n" * 50


def _raw_deflate(data: bytes) -> bytes:
    co = zlib.compressobj(wbits=-15)
    return co.compress(data) + co.flush()


def _assert_accelerator(stream: object) -> None:
    inner = getattr(stream, "_inner", None)
    # Length-verifying / ISIZE wraps sit outside the accelerator.
    from archivey.internal.streams.codecs import (
        _GzipTruncationCheckStream,
        _StdlibOnAcceleratorError,
        _ZlibAdlerCheckStream,
    )

    while isinstance(
        inner,
        (
            VerifyingStream,
            _GzipTruncationCheckStream,
            _StdlibOnAcceleratorError,
            _ZlibAdlerCheckStream,
        ),
    ):
        inner = getattr(inner, "_inner", None)
    assert isinstance(inner, RapidgzipChildStream)


def _assert_stdlib_zlib(stream: object) -> None:
    assert isinstance(getattr(stream, "_inner", None), DecompressorStream)


# --- 4.1 Parity ----------------------------------------------------------------------


def _read_exact(stream: object, n: int) -> bytes:
    """Gather exactly ``n`` bytes (stdlib decompressor reads may return short)."""
    read = getattr(stream, "read")
    buf = bytearray()
    while len(buf) < n:
        chunk = read(n - len(buf))
        if not chunk:
            break
        buf.extend(chunk)
    return bytes(buf)


@pytest.mark.parametrize(
    ("codec", "compress"),
    [
        (Codec.DEFLATE, _raw_deflate),
        (Codec.ZLIB, zlib.compress),
    ],
)
def test_accelerated_deflate_zlib_decode_and_seek_match_stdlib(
    codec: Codec, compress: object
) -> None:
    pytest.importorskip("rapidgzip")
    payload = _LARGE
    compressed = compress(payload)  # type: ignore[operator]
    assert len(compressed) >= _TEST_THRESHOLD
    mid = len(payload) // 3
    on = StreamConfig(use_rapidgzip=AcceleratorMode.ON, seekable=True)
    off = StreamConfig(use_rapidgzip=AcceleratorMode.OFF, seekable=True)

    with open_codec_stream(codec, io.BytesIO(compressed), config=on) as accel:
        _assert_accelerator(accel)
        head = _read_exact(accel, mid)
        assert accel.seek(mid // 2) == mid // 2
        mid_chunk = _read_exact(accel, 100)
        assert accel.seek(0) == 0
        full = accel.read()

    with open_codec_stream(codec, io.BytesIO(compressed), config=off) as std:
        _assert_stdlib_zlib(std)
        assert _read_exact(std, mid) == head == payload[:mid]
        assert std.seek(mid // 2) == mid // 2
        assert _read_exact(std, 100) == mid_chunk == payload[mid // 2 : mid // 2 + 100]
        assert std.seek(0) == 0
        assert std.read() == full == payload


# --- 4.2 Gating ----------------------------------------------------------------------


@pytest.mark.parametrize("codec", [Codec.DEFLATE, Codec.ZLIB, Codec.GZIP])
def test_off_and_below_auto_threshold_use_stdlib(codec: Codec) -> None:
    pytest.importorskip("rapidgzip")
    if codec is Codec.GZIP:
        compressed = gzip.compress(_SMALL)
    elif codec is Codec.ZLIB:
        compressed = zlib.compress(_SMALL)
    else:
        compressed = _raw_deflate(_SMALL)
    assert len(compressed) < RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE

    off = StreamConfig(use_rapidgzip=AcceleratorMode.OFF, seekable=True)
    auto = StreamConfig(use_rapidgzip=AcceleratorMode.AUTO, seekable=True)

    with open_codec_stream(codec, io.BytesIO(compressed), config=off) as stream:
        if codec is Codec.GZIP:
            assert not isinstance(stream._inner, RapidgzipChildStream)
        else:
            _assert_stdlib_zlib(stream)
        assert stream.read()  # non-empty

    with open_codec_stream(codec, io.BytesIO(compressed), config=auto) as stream:
        assert not isinstance(stream._inner, RapidgzipChildStream)


@pytest.mark.parametrize("codec", [Codec.DEFLATE, Codec.ZLIB, Codec.GZIP])
def test_on_forces_rapidgzip_below_threshold(codec: Codec) -> None:
    pytest.importorskip("rapidgzip")
    if codec is Codec.GZIP:
        compressed = gzip.compress(_SMALL)
    elif codec is Codec.ZLIB:
        compressed = zlib.compress(_SMALL)
    else:
        compressed = _raw_deflate(_SMALL)
    assert len(compressed) < RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE

    on = StreamConfig(use_rapidgzip=AcceleratorMode.ON, seekable=True)
    with open_codec_stream(codec, io.BytesIO(compressed), config=on) as stream:
        _assert_accelerator(stream)
        expected = (
            gzip.decompress(compressed)
            if codec is Codec.GZIP
            else zlib.decompress(
                compressed, -15 if codec is Codec.DEFLATE else zlib.MAX_WBITS
            )
        )
        assert stream.read() == expected


@pytest.fixture
def _low_auto_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(codecs, "RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE", _TEST_THRESHOLD)


def test_the_auto_threshold_is_past_the_child_break_even() -> None:
    """The child costs about 45 ms to start and saves about 3.4 ms per compressed MB,
    so AUTO below about 13 MB would be slower than the stdlib."""
    assert RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE >= 13 * 1000 * 1000


def test_auto_uses_stdlib_below_the_shipped_threshold() -> None:
    pytest.importorskip("rapidgzip")
    compressed = zlib.compress(_LARGE)
    assert _TEST_THRESHOLD <= len(compressed) < RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE
    auto = StreamConfig(
        use_rapidgzip=AcceleratorMode.AUTO,
        seekable=True,
        expected_decompressed_size=len(_LARGE),
    )
    with open_codec_stream(Codec.ZLIB, io.BytesIO(compressed), config=auto) as stream:
        _assert_stdlib_zlib(stream)
        assert stream.read() == _LARGE


@pytest.mark.usefixtures("_low_auto_threshold")
def test_auto_selects_rapidgzip_above_threshold() -> None:
    pytest.importorskip("rapidgzip")
    compressed = zlib.compress(_LARGE)
    assert len(compressed) >= _TEST_THRESHOLD
    auto = StreamConfig(
        use_rapidgzip=AcceleratorMode.AUTO,
        seekable=True,
        expected_decompressed_size=len(_LARGE),
    )
    with open_codec_stream(Codec.ZLIB, io.BytesIO(compressed), config=auto) as stream:
        _assert_accelerator(stream)
        assert stream.read() == _LARGE


@pytest.mark.usefixtures("_low_auto_threshold")
def test_auto_without_decompressed_size_uses_stdlib_even_when_large() -> None:
    """AUTO must not select rapidgzip when truncation cannot be verified."""
    pytest.importorskip("rapidgzip")
    compressed = zlib.compress(_LARGE)
    assert len(compressed) >= _TEST_THRESHOLD
    auto = StreamConfig(use_rapidgzip=AcceleratorMode.AUTO, seekable=True)
    with open_codec_stream(Codec.ZLIB, io.BytesIO(compressed), config=auto) as stream:
        _assert_stdlib_zlib(stream)
        assert stream.read() == _LARGE


def _uses_rapidgzip_child(stream: object) -> bool:
    """Whether a member stream's wrapper chain reaches a ``RapidgzipChildStream``.

    ``_assert_accelerator`` and ``_assert_stdlib_zlib`` know the short chain that
    ``open_codec_stream`` returns; a stream from ``open_archive`` also crosses the
    backend's wrappers, so this walks them. It returns rather than asserts, and only the
    ``declared=True`` leg of the test below keeps the walk honest: if it stops reaching
    the child, that leg fails.
    """
    seen: set[int] = set()
    pending = [stream]
    while pending:
        current = pending.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, RapidgzipChildStream):
            return True
        pending.extend(
            getattr(current, name, None) for name in ("_inner", "_stream", "raw")
        )
    return False


@pytest.mark.usefixtures("_low_auto_threshold")
@pytest.mark.parametrize("declared", [False, True])
def test_auto_on_a_large_gz_file_follows_declared_seeking(
    tmp_path: Path, declared: bool
) -> None:
    """End to end through ``open_archive``: AUTO keys on ``seekable_members``, not on the
    source being seekable, so a plain open of a large ``.gz`` read front to back stays
    on the stdlib and the declared open gets rapidgzip."""
    pytest.importorskip("rapidgzip")
    path = tmp_path / "large.gz"
    path.write_bytes(gzip.compress(_LARGE))
    assert path.stat().st_size >= _TEST_THRESHOLD
    with open_archive(path, seekable_members=declared) as archive:
        member = archive.members()[0]
        with archive.open(member) as stream:
            assert _uses_rapidgzip_child(stream) is declared
            assert stream.read() == _LARGE


# --- 4.3 Error translation + truncation limitation -----------------------------------


@pytest.mark.parametrize(
    ("codec", "compress"),
    [
        (Codec.DEFLATE, _raw_deflate),
        (Codec.ZLIB, zlib.compress),
    ],
)
def test_corrupt_deflate_zlib_body_translates_to_corruption(
    codec: Codec, compress: object
) -> None:
    pytest.importorskip("rapidgzip")
    payload = _SMALL * 20
    corrupt = bytearray(compress(payload))  # type: ignore[operator]
    # Clobber past any zlib/deflate header bytes.
    corrupt[10:40] = b"\x00" * 30
    on = StreamConfig(use_rapidgzip=AcceleratorMode.ON, seekable=True)
    with open_codec_stream(codec, io.BytesIO(bytes(corrupt)), config=on) as stream:
        with pytest.raises(CorruptionError):
            stream.read()


def test_standalone_zlib_midcut_raises_via_stdlib_under_auto() -> None:
    """AUTO without a declared decompressed size must use stdlib (raises TruncatedError)."""
    pytest.importorskip("rapidgzip")
    full = zlib.compress(_SMALL * 100)
    cut = full[: max(len(full) // 2, 20)]
    # Default AUTO + no expected_decompressed_size → stdlib path.
    auto = StreamConfig(use_rapidgzip=AcceleratorMode.AUTO, seekable=True)
    with open_codec_stream(Codec.ZLIB, io.BytesIO(cut), config=auto) as stream:
        with pytest.raises(TruncatedError):
            stream.read()


def test_standalone_zlib_midcut_raises_with_expected_size_on_accelerator() -> None:
    """Accelerator + known decompressed size must surface truncation via VerifyingStream."""
    pytest.importorskip("rapidgzip")
    payload = _SMALL * 100
    full = zlib.compress(payload)
    cut = full[: max(len(full) // 2, 20)]
    on = StreamConfig(
        use_rapidgzip=AcceleratorMode.ON,
        seekable=True,
        expected_decompressed_size=len(payload),
    )
    # Keep close inside the expected-error scope: macOS rapidgzip may raise on read
    # *and* would previously re-raise from VerifyingStream.close on context exit.
    stream = open_codec_stream(Codec.ZLIB, io.BytesIO(cut), config=on)
    try:
        with pytest.raises((TruncatedError, CorruptionError)):
            data = stream.read()
            stream.close()
            assert len(data) < len(payload)
    finally:
        if not stream.closed:
            try:
                stream.close()
            except (TruncatedError, CorruptionError):
                pass


def test_verifying_stream_close_after_inner_read_error_is_quiet() -> None:
    """After a decode error on read, close must not raise a second length fault."""
    from archivey.internal.streams.verify import VerifyingStream

    class _Boom(io.BytesIO):
        def __init__(self) -> None:
            super().__init__(b"partial")
            self._blown = False

        def read(self, n: int = -1) -> bytes:  # noqa: ARG002
            if self._blown:
                raise RuntimeError("std::exception")
            self._blown = True
            return super().read()

    stream = VerifyingStream(_Boom(), {}, expected_size=100)
    with pytest.raises(RuntimeError, match="std::exception"):
        # First read returns "partial"; second (empty follow-up from readall path) —
        # drive a second read that raises, matching mid-cut accelerator behaviour.
        assert stream.read(7) == b"partial"
        stream.read(1)
    # Close must be quiet: verification was abandoned after the inner error.
    stream.close()


def test_verifying_stream_forwards_a_raw_error_on_the_draining_read() -> None:
    """Silent short-read + opaque exception on ``read()``: the raw error is forwarded.

    The verifier does not relabel it: the translator of the ``ArchiveStream`` above
    classifies it (rapidgzip's opaque ``std::exception`` becomes ``CorruptionError``),
    the same as on the bounded ``read(n)`` path. Close stays quiet either way.
    """
    from archivey.internal.streams.verify import VerifyingStream

    class _ShortThenBoom(io.BytesIO):
        def __init__(self) -> None:
            super().__init__(b"partial")
            self._eof_reads = 0

        def read(self, n: int = -1) -> bytes:
            if self.tell() >= 7:
                self._eof_reads += 1
                raise RuntimeError("std::exception")
            return super().read(n)

    stream = VerifyingStream(_ShortThenBoom(), {}, expected_size=100)
    with pytest.raises(RuntimeError, match="std::exception"):
        stream.read()
    stream.close()  # content faults raise from read, never from close


def test_standalone_zlib_midcut_raises_through_rapidgzip_on_without_size() -> None:
    """ON without a declared size: a cut zlib stream raises, never a silent short read.

    Where rapidgzip raises on the cut, the stream finishes with the standard library,
    which names the cut (``TruncatedError``); where it returns a short prefix, the
    Adler-32 check raises (``CorruptionError``).
    """
    pytest.importorskip("rapidgzip")
    full = zlib.compress(_SMALL * 100)
    # Mid-stream cut that leaves a partially-decodable prefix (not just a missing
    # Adler trailer).
    cut = full[: max(len(full) // 2, 20)]
    on = StreamConfig(use_rapidgzip=AcceleratorMode.ON, seekable=True)
    with pytest.raises((CorruptionError, TruncatedError)):
        with open_codec_stream(Codec.ZLIB, io.BytesIO(cut), config=on) as stream:
            stream.read()


# --- Adler-32 under rapidgzip ---------------------------------------------------------

_ADLER_PAYLOAD = random.Random(0).randbytes(300_000) + bytes(200_000)


def _flip(data: bytes, index: int) -> bytes:
    out = bytearray(data)
    out[index] ^= 0x01
    return bytes(out)


def _body_flip_the_stdlib_rejects(good: bytes) -> bytes:
    """Flip a bit near the end of the body that the standard library rejects.

    Chosen at run time: the compressed bytes depend on the zlib build (CPython on
    Windows ships zlib-ng), and a flipped bit can land where it changes nothing.
    """
    for index in range(len(good) - 5, len(good) // 2, -1):
        bad = _flip(good, index)
        try:
            zlib.decompress(bad)
        except zlib.error:
            return bad
    raise AssertionError("no rejected body flip found")


def _on_zlib(data: bytes):  # noqa: ANN202 - the codec's stream type
    pytest.importorskip("rapidgzip")
    on = StreamConfig(use_rapidgzip=AcceleratorMode.ON, seekable=True)
    return open_codec_stream(Codec.ZLIB, io.BytesIO(data), config=on)


@pytest.mark.parametrize("where", ["trailer", "body"])
def test_rapidgzip_zlib_damage_raises_from_the_adler_check(where: str) -> None:
    """Damage rapidgzip does not report raises, never a clean read.

    A flipped trailer bit decodes cleanly in rapidgzip; only the Adler-32 check catches
    it. A flipped bit near the end of the body can make rapidgzip raise instead, and
    the standard library then finishes the decode and names the damage itself.
    """
    good = zlib.compress(_ADLER_PAYLOAD)
    if where == "trailer":
        bad = _flip(good, len(good) - 1)
    else:
        bad = _body_flip_the_stdlib_rejects(good)
    expected = codecs.StreamChecksumError if where == "trailer" else ReadError
    with _on_zlib(bad) as stream:
        with pytest.raises(expected):
            stream.read()
        # A re-read after a seek back raises too, never a clean read.
        stream.seek(0)
        with pytest.raises(ReadError):
            stream.read()


def test_rapidgzip_zlib_adler_check_survives_seeks() -> None:
    """A seek forward reads through, so a reader that skips (TAR) is still checked."""
    bad = _flip(zlib.compress(_ADLER_PAYLOAD), -1)
    with _on_zlib(bad) as stream:
        assert stream.read(10) == _ADLER_PAYLOAD[:10]
        stream.seek(5)  # back: behind the frontier
        assert stream.read(10) == _ADLER_PAYLOAD[5:15]
        stream.seek(400_000)  # forward: read through
        assert stream.tell() == 400_000
        assert stream.read(10) == _ADLER_PAYLOAD[400_000:400_010]
        with pytest.raises(codecs.StreamChecksumError):
            stream.seek(0, io.SEEK_END)


def test_rapidgzip_zlib_good_stream_seeks_anywhere() -> None:
    good = zlib.compress(_ADLER_PAYLOAD)
    with _on_zlib(good) as stream:
        assert stream.seek(0, io.SEEK_END) == len(_ADLER_PAYLOAD)
        stream.seek(123_456)
        assert stream.read(100) == _ADLER_PAYLOAD[123_456:123_556]
        stream.seek(0)
        assert stream.read() == _ADLER_PAYLOAD


def test_rapidgzip_zlib_concatenated_streams_pass_the_check() -> None:
    """rapidgzip reads concatenated zlib streams whole; the trailer covers only the last,
    so the mismatch is settled by the standard-library confirmation, which passes."""
    both = zlib.compress(_ADLER_PAYLOAD) + zlib.compress(b"second stream")
    with _on_zlib(both) as stream:
        assert stream.read() == _ADLER_PAYLOAD + b"second stream"


def test_tar_zz_member_damage_raises_under_rapidgzip_on(tmp_path: Path) -> None:
    """The TAR reader skips member data by seeking and swallows decode failures in its
    end scan; neither may hide an Adler-32 mismatch over the members it read."""
    pytest.importorskip("rapidgzip")
    import tarfile

    from archivey import AcceleratorMode as PublicMode
    from archivey import ArchiveyConfig

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name in ("a.bin", "b.bin"):
            info = tarfile.TarInfo(name)
            info.size = len(_ADLER_PAYLOAD)
            tar.addfile(info, io.BytesIO(_ADLER_PAYLOAD))
    path = tmp_path / "damaged.tar.zz"
    path.write_bytes(_flip(zlib.compress(buf.getvalue()), -1))
    config = ArchiveyConfig(use_rapidgzip=PublicMode.ON)
    for streaming in (False, True):
        with pytest.raises(CorruptionError):
            with open_archive(path, streaming=streaming, config=config) as reader:
                if streaming:
                    for _, stream in reader.stream_members():
                        if stream is not None:
                            stream.read()
                else:
                    for member in reader.members():
                        if member.is_file:
                            reader.read(member)


# --- 4.4 Bounded input ---------------------------------------------------------------


def test_bounded_deflate_with_trailing_bytes_decodes() -> None:
    """A deflate blob + trailing junk, fed through a length-bounded slice, decodes cleanly."""
    pytest.importorskip("rapidgzip")
    payload = _SMALL * 10
    raw = _raw_deflate(payload)
    padded = raw + b"PK\x03\x04" + b"trailing-junk-not-deflate"
    bounded = SlicingStream(io.BytesIO(padded), start=0, length=len(raw))
    on = StreamConfig(use_rapidgzip=AcceleratorMode.ON, seekable=True)
    with open_codec_stream(Codec.DEFLATE, bounded, config=on) as stream:
        _assert_accelerator(stream)
        assert stream.read() == payload
        # Mid-stream seek still works on the bounded accelerator path.
        assert stream.seek(len(payload) // 2) == len(payload) // 2
        assert stream.read() == payload[len(payload) // 2 :]
