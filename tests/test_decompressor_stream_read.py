"""``DecompressorStream.read(n)`` returns exactly ``n`` bytes until the end.

``read(n)`` hands a decoder's chunk straight back when nothing is buffered and the
chunk is exactly ``n`` bytes, and routes every other shape through its buffer. These
cases pin the contract across both routes: a decoder that returns what was asked, one
that returns less, and one that ignores ``max_length`` and returns more.
"""

from __future__ import annotations

import gzip
import io
import os
from pathlib import Path
from typing import BinaryIO

import pytest

import archivey
from archivey import ArchiveyConfig
from archivey.diagnostics import DiagnosticPolicy
from archivey.exceptions import DiagnosticRaisedError
from archivey.internal.config import StreamConfig
from archivey.internal.diagnostics_collector import DiagnosticCollector
from archivey.internal.streams.codecs import _stdlib_bzip2
from archivey.internal.streams.decompressor_stream import (
    BaseDecoder,
    DecodeOut,
    DecompressorStream,
    SeekPoint,
)

_DATA = bytes(range(256)) * 97  # 24_832 bytes, not a multiple of any read size below


class _ScalingDecoder(BaseDecoder):
    """Emits each input byte ``factor`` times; honours ``max_length`` unless told not to.

    Output that ``max_length`` holds back waits in ``_pending`` for the next ``feed``,
    as zlib's ``unconsumed_tail`` does.
    """

    def __init__(self, factor: int, *, honour_max_length: bool) -> None:
        self._factor = factor
        self._honour = honour_max_length
        self._pending = b""
        self._done = False

    def recreate(self, point: SeekPoint, inner: BinaryIO) -> _ScalingDecoder:
        del point, inner
        return _ScalingDecoder(self._factor, honour_max_length=self._honour)

    def feed(self, chunk: bytes, max_length: int = -1) -> DecodeOut:
        out = self._pending + b"".join(bytes([b]) * self._factor for b in chunk)
        if self._honour and 0 <= max_length < len(out):
            self._pending = out[max_length:]
            return DecodeOut(out[:max_length])
        self._pending = b""
        return DecodeOut(out)

    def flush(self) -> DecodeOut:
        self._done = True
        out, self._pending = self._pending, b""
        return DecodeOut(out)

    @property
    def finished(self) -> bool:
        return self._done

    @property
    def needs_input(self) -> bool:
        return not self._pending


def _stream(factor: int, *, honour_max_length: bool) -> DecompressorStream:
    return DecompressorStream(
        io.BytesIO(_DATA[::factor] if factor > 1 else _DATA),
        make_decoder=lambda point, inner: _ScalingDecoder(
            factor, honour_max_length=honour_max_length
        ),
    )


@pytest.mark.parametrize("honour_max_length", [True, False])
@pytest.mark.parametrize("factor", [1, 3])
@pytest.mark.parametrize("n", [1, 7, 4096, 65536, 1 << 20])
def test_read_n_is_full_count_until_eof(
    n: int, factor: int, honour_max_length: bool
) -> None:
    source = _DATA[::factor] if factor > 1 else _DATA
    expected = b"".join(bytes([b]) * factor for b in source)
    with _stream(factor, honour_max_length=honour_max_length) as stream:
        pieces: list[bytes] = []
        delivered = 0  # re-summing every piece here would make the n=1 rows quadratic
        while True:
            piece = stream.read(n)
            if not piece:
                break
            pieces.append(piece)
            delivered += len(piece)
            assert stream.tell() == delivered
        assert b"".join(pieces) == expected
        # Every piece but the last is exactly n bytes.
        assert all(len(p) == n for p in pieces[:-1])
        assert 0 < len(pieces[-1]) <= n
        assert stream.read(n) == b""


def test_read_n_after_partial_read_keeps_the_buffered_tail() -> None:
    """A read that leaves bytes buffered must hand them out before decoding more."""
    with _stream(1, honour_max_length=False) as stream:
        first = stream.read(10)  # the decoder returned far more; the rest is buffered
        second = stream.read(20)
        rest = stream.read()
    assert first + second + rest == _DATA
    assert (len(first), len(second)) == (10, 20)


class _TrailingAtFillDecoder(_ScalingDecoder):
    """A pass-through decoder that finds trailing data in the feed that fills a read.

    The first feed returns exactly ``max_length`` bytes and, in the same call, flags
    bytes past the stream's end, so the stream reports ``ARCHIVE_TRAILING_DATA``
    while that chunk is in hand.
    """

    def __init__(self) -> None:
        super().__init__(1, honour_max_length=True)

    def recreate(self, point: SeekPoint, inner: BinaryIO) -> _TrailingAtFillDecoder:
        del point, inner
        return _TrailingAtFillDecoder()

    def feed(self, chunk: bytes, max_length: int = -1) -> DecodeOut:
        out = super().feed(chunk, max_length)
        self._pending = b""
        self._trailing_bytes = 1
        self._done = True
        return out


def _strict_collector() -> DiagnosticCollector:
    return DiagnosticCollector(policy=DiagnosticPolicy.strict())


def test_a_raise_held_while_decoding_a_full_chunk_propagates_and_keeps_the_bytes() -> (
    None
):
    """A read whose one decode fills ``n`` but also holds a raise must raise, and the
    bytes it decoded must come back on the next read, not be dropped with the raise."""
    n = 4096
    with DecompressorStream(
        io.BytesIO(_DATA),
        make_decoder=lambda point, inner: _TrailingAtFillDecoder(),
        collector=_strict_collector(),
        report_trailing_data=True,
    ) as stream:
        with pytest.raises(DiagnosticRaisedError):
            stream.read(n)
        assert stream.read(n) == _DATA[:n]
        assert stream.read(n) == b""


def _largest_library_local(exc: BaseException) -> int:
    """The largest ``bytes``/``bytearray`` local on the non-test frames of ``exc``."""
    biggest = 0
    tb = exc.__traceback__
    while tb is not None:
        if "tests" not in Path(tb.tb_frame.f_code.co_filename).parts:
            for value in list(tb.tb_frame.f_locals.values()):
                if isinstance(value, (bytes, bytearray)):
                    biggest = max(biggest, len(value))
        tb = tb.tb_next
    return biggest


def test_a_raise_after_a_short_chunk_does_not_keep_the_chunk_alive() -> None:
    """The raise's traceback keeps ``read``'s frame alive; the chunk it buffered must
    not stay bound there as a second copy of the bytes."""
    payload = os.urandom(200_000)
    blob = gzip.compress(payload) + b"appended signature\n"
    config = ArchiveyConfig(diagnostic_policy=DiagnosticPolicy.strict())
    with archivey.open_stream(io.BytesIO(blob), config=config) as stream:
        with pytest.raises(DiagnosticRaisedError) as caught:
            # One request larger than the payload: the decode comes back short.
            stream.read(len(payload) + 100_000)
        assert _largest_library_local(caught.value) < 4096


def test_a_decoder_error_is_raised_again_after_a_seek_to_the_same_place() -> None:
    # The decoder raised at offset 0, so seek(0) moves nothing; it must still restart
    # the decoder rather than feed the spent one. bz2 then raises ValueError on
    # CPython 3.11.17 and 3.12.15, which the bzip2 translator reads as a truncation.
    stream = _stdlib_bzip2(io.BytesIO(b"not bzip2"), StreamConfig(seekable=True))
    with pytest.raises(OSError, match="Invalid data stream"):
        stream.read(10)
    for _ in range(2):
        assert stream.seek(0) == 0
        with pytest.raises(OSError, match="Invalid data stream"):
            stream.read(10)
    with pytest.raises(OSError, match="Invalid data stream"):
        stream.read(10)  # and without a seek, the verdict stays


class _FailsOnSecondFeed(BaseDecoder):
    """Emits the first 100 bytes of its first chunk, raises on the second, then is spent.

    Once it has raised it answers every call with ``ValueError``, as ``bz2`` does on
    CPython 3.11.17 and 3.12.15.
    """

    def __init__(self) -> None:
        self._feeds = 0
        self._raised = False

    def recreate(self, point: SeekPoint, inner: BinaryIO) -> _FailsOnSecondFeed:
        del point, inner
        return _FailsOnSecondFeed()

    def feed(self, chunk: bytes, max_length: int = -1) -> DecodeOut:
        if self._raised:
            raise ValueError("Decompressor is unusable after a previous error")
        self._feeds += 1
        if self._feeds == 2:
            self._raised = True
            raise OSError("Invalid data stream")
        return DecodeOut(chunk[:100])

    def flush(self) -> DecodeOut:
        return self.feed(b"")

    @property
    def finished(self) -> bool:
        return False

    @property
    def needs_input(self) -> bool:
        return True


def test_a_decoder_error_after_output_hands_back_the_buffer_then_raises() -> None:
    # The read that reaches the error raises with the first chunk's output buffered.
    # Later reads hand that output back, then raise the same error; none of them
    # feeds the spent decoder, whose ValueError would replace the verdict.
    stream = DecompressorStream(
        io.BytesIO(_DATA), make_decoder=lambda point, inner: _FailsOnSecondFeed()
    )
    with pytest.raises(OSError, match="Invalid data stream") as first:
        stream.read(len(_DATA))
    got = stream.read(len(_DATA))
    assert got
    assert got == _DATA[: len(got)]
    for _ in range(2):
        with pytest.raises(OSError) as again:
            stream.read(len(_DATA))
        assert again.value is first.value
    # A seek restarts the decoder, which hands back its first chunk again.
    assert stream.seek(0) == 0
    assert stream.read(len(got)) == got
