"""bzip2: the codec, and the checks it adds around rapidgzip's bzip2 decoder (empty
results, stream gaps, resume points).
"""

from __future__ import annotations

import bz2
import io
import itertools
import re
from collections.abc import Callable
from typing import BinaryIO

from archivey.exceptions import (
    ArchiveyError,
    CorruptionError,
    PackageNotInstalledError,
    StreamNotSeekableError,
    TruncatedError,
)
from archivey.internal.config import (
    AcceleratorMode,
    StreamConfig,
)
from archivey.internal.streams.archive_stream import (
    ExceptionTranslator,
    RewindWarning,
)
from archivey.internal.streams.codecs import deps
from archivey.internal.streams.codecs.base import (
    Codec,
    CodecParams,
    CodecSource,
    StreamCodec,
)
from archivey.internal.streams.codecs.bzip2_resume import Bzip2Resume
from archivey.internal.streams.codecs.framed_decoder import (
    FramedDecompressorStream,
    stream_magic,
)
from archivey.internal.streams.codecs.rapidgzip_child import from_callers_source
from archivey.internal.streams.codecs.rapidgzip_inprocess import _open_accelerator
from archivey.internal.streams.codecs.rapidgzip_select import (
    _RAPIDGZIP_REQUIREMENT,
    _bound_rapidgzip_source,
    _refuse_forward_only_accelerator,
    _StdlibSeekContract,
)
from archivey.internal.streams.codecs.stdlib_takeover import (
    _accelerator_backstop_source,
    _SourceViews,
    _StdlibOnAcceleratorError,
)
from archivey.internal.streams.decompressor_stream import (
    SeekPoint,
    near_stream_magic,
    report_trailing_data,
)
from archivey.internal.streams.resume import ask_resume_offset
from archivey.internal.streams.streamtools import DelegatingStream
from archivey.types import (
    ArchiveFormat,
    MagicSignature,
    StreamFormat,
)


def _bzip2_uses_accelerator(config: StreamConfig) -> bool:
    """Whether bzip2 opens through rapidgzip for this config.

    Imports rapidgzip when the config asks for it, so that a package that is found but
    fails to import reads as unavailable: ``AUTO`` then falls back to the standard
    library, as it does for a missing package. Called only on the way to opening a
    bzip2 stream (:func:`resolve_codec`, :meth:`Bzip2Codec.open`).
    """
    return (
        deps.rapidgzip.available()
        and config.use_indexed_bzip2.enabled_for(
            seekable=config.seekable, available=True
        )
        and deps.rapidgzip_bzip2() is not None
    )


# What may start a further stream after one ends, so a concatenated file reads whole.
# _BZIP2_HEADER must accept the same bytes.
_BZIP2_MAGIC = (b"B", b"Z", b"h", b"123456789")
_BZIP2_STREAMS = stream_magic(_BZIP2_MAGIC)


# Nothing starts a further stream: what follows the first one is past the data.
_NO_FURTHER_STREAM = stream_magic()


def _stdlib_bzip2(
    source: CodecSource, config: StreamConfig, *, single_stream: bool = False
) -> BinaryIO:
    return FramedDecompressorStream(
        source,
        bz2.BZ2Decompressor,
        codec_name="bzip2",
        magic=_NO_FURTHER_STREAM if single_stream else _BZIP2_STREAMS,
        collector=config.collector,
        report_trailing_data=config.report_trailing_data,
    )


class _Bzip2EmptyStreamCheck(DelegatingStream):
    """Hand a silent empty result from rapidgzip's bzip2 decoder to the stdlib engine.

    The bundled bzip2 decoder ends the stream with no output and no error when the input is
    not bzip2 at all: forty thousand zero bytes, a zero-byte file and a bare ``BZh9`` read as
    an empty stream. Its ``size()``, ``tell_compressed()`` and block offsets read the same
    for that garbage as for a valid empty stream, so nothing the accelerator reports can
    tell them apart.

    The stdlib path raises on the same inputs, and an accelerator must not change whether a
    corrupt source raises. So the first read that comes back empty, before any byte was
    delivered, retargets the stream at ``bz2`` over a fresh view of the source. That engine
    raises the translated error for garbage and reads a genuinely empty stream (one or more
    header-plus-end-of-stream records) as ``b""``. The fallback costs nothing on a stream
    with content: the first non-empty read disarms the check. It costs at most one stdlib
    decode of an input that produced nothing, which for a valid stream is a few bytes per
    concatenated empty record.

    A caller ``seek`` that lands away from offset 0 before the first read disarms the
    check, as it does for the gzip backstop: the stream has shown it has content there.
    That cannot hide garbage. For input it reads as empty, the decoder clamps every seek
    to 0 (measured on rapidgzip 0.16: ``seek(100)`` over 40 000 zero bytes returns 0), so
    the check stays armed, and that seek, landing short of its target at the decoder's
    end, falls back there: the error arrives at the seek, as it would at a read.

    The decoder also skips what it cannot read between blocks. It finds blocks by their
    magic, so junk before the first stream, a stream whose header is damaged, or one
    whose block and end-of-stream magics are both damaged is passed over with no error,
    and the output goes on with the next block it recognises: measured on rapidgzip
    0.16, ``BZh9`` and forty zero bytes before a stream read as that stream, and three
    streams with two bytes flipped in the second read as the first and the third. The
    combined CRC check below does not see it, since nothing of the skipped region is in
    the index. Before a read returns, :class:`_Bzip2Layout` checks the index built so
    far: each stream starts where the one before it ended, after nothing but zero
    padding and empty streams, which the standard library also skips. At the first
    place that does not hold, the read stops, and the standard library takes over there
    and gives the verdict, as with the accelerator off. The index is fetched again only
    when a read ends past the entries already walked and the decoder has read further
    into the source. Each fetch copies rapidgzip's whole index, so the cost grows with
    the square of the block count (``dev-docs/formats/bzip2.md`` §2.3).

    At the end, the decoder can also stop short: after zero padding it does not find a
    further stream, where the standard library decodes it. So when what follows the
    last stream starts another stream after any zero padding and empty streams, the
    standard library takes over at the end, and decodes it or raises; not for a
    container coder's single stream (``CodecParams.single_stream``), where that stream
    is trailing data, as with the accelerator off.

    A seek gets the same verdicts as a read. A seek past a skipped region hands over
    at it, and a seek that reaches the decoder's end runs the end check, or, when the
    decoder read the stream as empty, the first-empty fallback. The standard library
    then seeks, and raises where it does with the accelerator off, so a caller sizing
    the stream with ``seek(0, SEEK_END)`` gets the error rather than a wrong size.
    """

    # Side-effecting read() (first-empty fallback); disable passthrough so readinto does
    # not skip it.
    readinto_passthrough = False

    def __init__(
        self,
        inner: BinaryIO,
        *,
        views: _SourceViews,
        config: StreamConfig,
        single_stream: bool = False,
    ) -> None:
        super().__init__(inner)
        self._views = views
        self._config = config
        # A container coder's data is one stream (CodecParams.single_stream): a stream
        # after it is not decoded at the end, as the standard library would not.
        self._single_stream = single_stream
        self._armed = True
        # The accelerator is still the decoder, and has not reached the end yet.
        self._end_unchecked = True
        self._layout = _Bzip2Layout()
        # The decoder's compressed position when the layout was last checked.
        self._layout_checked_at: int | None = None

    def read(self, size: int = -1, /) -> bytes:
        if size == 0:
            return b""  # an explicit read(0) is not EOF; it must not trip the check
        takeover = self._takeover()
        before = takeover.position if takeover is not None else 0
        data = self._inner.read(size)
        if takeover is not None:
            data = self._stop_at_layout_gap(takeover, before, data, size)
        if not self._armed:
            if self._end_unchecked and (not data or size < 0) and self._check_end():
                data += self._inner.read(size)
            return data
        self._armed = False
        if data:
            if size < 0 and self._check_end():
                data += self._inner.read(size)
            return data
        self._fall_back_to_stdlib()
        return self._inner.read(size)

    def _takeover(self) -> _StdlibOnAcceleratorError | None:
        """The takeover stream while the accelerator is still the decoder, else ``None``."""
        inner = self._inner
        if isinstance(inner, _StdlibOnAcceleratorError) and not inner.switched:
            return inner
        return None

    def _stop_at_layout_gap(
        self, takeover: _StdlibOnAcceleratorError, before: int, data: bytes, size: int
    ) -> bytes:
        """``data``, read from output position ``before``, cut where the decoder skipped
        part of the source; the standard library takes over from there.

        An empty read is checked too: after a seek to or past the end of the output, the
        read comes back empty, and a skipped region before that position is only found
        here. A gap right at the end of a non-empty read is left to the next read."""
        end = before + len(data)
        gap = self._layout_gap(end, bool(data))
        if gap is None or gap > end or (gap == end and data):
            return data
        # A seek can have landed past the skipped region already; then nothing from
        # this read is kept, and the standard library decodes up to ``before``.
        kept = data[: max(0, gap - before)]
        takeover.switch_to_stdlib(before + len(kept))
        if kept and size >= 0:
            return kept
        return kept + self._inner.read(size)

    def _layout_gap(self, end: int, nonempty: bool) -> int | None:
        """Where in the output the decoder's index first skips part of the source, or
        ``None``, as far as a read ending at output offset ``end`` needs to know.

        The index lists entries in source order, and their output offsets never go
        down, so an entry not walked yet cannot be at an output offset below the
        highest one walked (``covered``). A read that ends below it, or at it with
        output (a gap at the end of a read is the next read's), needs no new entries.
        Otherwise the index is asked again, and only when the decoder has read further
        into the source, which holds the fetches to one per batch of blocks the decoder
        reads ahead. The ``covered`` test saves a fetch only for a read that stays
        below the entries already walked, such as a repeat read after a backward seek;
        a forward read usually ends past ``covered``, which lags the output by up to a
        block. Each fetch copies the whole index; the walk itself takes only the new
        entries (:meth:`_Bzip2Layout.first_gap`)."""
        layout = self._layout
        if layout.gap is None and (
            end < layout.covered or (nonempty and end == layout.covered)
        ):
            return None
        accelerator = self._accelerator()
        position = getattr(accelerator, "compressed_position", lambda: None)()
        if position is not None and position == self._layout_checked_at:
            return self._layout.gap
        self._layout_checked_at = position
        offsets = getattr(accelerator, "available_block_offsets", dict)()
        return self._layout.first_gap(offsets, self._views.view)

    def _accelerator(self) -> BinaryIO | None:
        """The accelerator, while it is still the decoder; ``None`` after a takeover,
        where the standard library checks the end itself. After this class's own
        fallback it is the standard library's stream, which has neither probe, and the
        end check is already off."""
        inner = self._inner
        if isinstance(inner, _StdlibOnAcceleratorError):
            return inner.accelerator
        return inner

    def _check_end(self) -> bool:
        """Check the combined CRCs, then what follows the last stream. Return whether
        the standard library took over at the end to decode a further stream."""
        self._end_unchecked = False
        self._check_combined_crcs()
        return self._check_trailing_data()

    def _check_combined_crcs(self) -> None:
        """Check each stream's combined CRC, which the accelerator does not.

        rapidgzip's bzip2 decoder checks every block's CRC against its data, but not the
        stream's combined CRC in the end-of-stream marker, and that is the one check
        that covers the sequence of blocks: a stream with a whole block cut out reads
        short with no error. The combined CRC is built from the block CRCs, each stored
        in its block's header, so the decoder's index (where every block and every
        end-of-stream marker starts) and 80 bits of the source at each of those places
        are enough to check it, without decoding anything again.
        """
        offsets_fn = getattr(self._accelerator(), "block_offsets", None)
        offsets = offsets_fn() if offsets_fn is not None else None
        if not offsets:
            return
        with self._views.view() as view:
            combined = 0
            stream_end = -1
            for bit in sorted(offsets):
                if bit == stream_end:
                    # The byte-aligned end of the last end-of-stream marker. What
                    # follows is the next stream's header, or trailing bytes (which
                    # _check_trailing_data reports), and neither is checked here.
                    continue
                marker = _bzip2_marker_at(view, bit)
                if marker is None:
                    continue
                magic, crc = marker
                if magic == _BZIP2_BLOCK_MAGIC:
                    combined = ((combined << 1) | (combined >> 31)) & 0xFFFFFFFF
                    combined ^= crc
                elif magic == _BZIP2_EOS_MAGIC:
                    if crc != combined:
                        raise CorruptionError(
                            "bzip2 stream is corrupt: the combined CRC in the "
                            f"end-of-stream marker at bit {bit} is {crc:#010x}, but "
                            f"the stream's blocks combine to {combined:#010x}"
                        )
                    combined = 0
                    stream_end = -(-(bit + 80) // 8) * 8

    def _check_trailing_data(self) -> bool:
        """Find the first byte after the last stream that is neither zero padding nor
        part of an empty stream. The accelerator skips such bytes. Report it, or, when
        a stream header starts there, hand the read to the standard library.

        rapidgzip's bzip2 decoder reads past them with a warning on stderr and no error.
        After the last read its compressed position is the end of the last stream that
        produced data (see ``compressed_position``), so empty streams after that one are
        not counted in it. The bytes from there to the end of the source are read (a
        fresh view, so the decoder's cursor does not move) until one that is neither
        zero padding nor part of an empty stream. The standard-library path accepts
        both, so this path accepts both too. Where a stream header follows, or a
        damaged one, the standard library decodes that stream or raises on it, and the
        decoder here stopped before it, so the standard library takes over at the end
        and decides (class docstring). For a container coder's single stream the
        standard library would not read a further stream either, so that stream is
        reported as trailing bytes instead. Return whether the standard library took
        over.
        """
        end = getattr(self._accelerator(), "compressed_position", lambda: None)()
        if end is None:
            return False
        found = self._first_trailing_byte(end)
        if found is None:
            return False
        offset, starts_stream = found
        takeover = self._takeover()
        if starts_stream and takeover is not None and not self._single_stream:
            takeover.switch_to_stdlib()
            return True
        if self._config.report_trailing_data:
            report_trailing_data(self._config.collector, "bzip2", offset)
        return False

    def _first_trailing_byte(self, end: int) -> tuple[int, bool] | None:
        """The offset of the first byte from ``end`` that is neither zero padding nor
        part of an empty stream, and whether a stream header (``BZh`` and a block-size
        digit) or a damaged one (:func:`near_stream_magic`) starts there, which the
        standard library decodes or refuses; ``None`` when there is no such byte."""
        with self._views.view() as view:
            view.seek(end)
            # ``held`` is the start of an empty stream that the previous chunk cut, or
            # nothing. ``offset`` is the source offset of ``data[0]``.
            offset = end
            held = b""
            while True:
                chunk = view.read(_TRAILING_SCAN_CHUNK)
                data = held + chunk
                skipped = _padding_and_empty_bzip2_streams(data)
                rest = data[skipped:]
                # A short ``rest`` that could begin an empty stream, or is shorter than
                # a stream header, waits for the next chunk. An empty ``chunk`` means
                # the end of the source: ``rest`` cannot become a whole stream, so it is
                # reported.
                short = len(rest) < _BZIP2_HEADER_LEN
                if rest and not (chunk and (short or _starts_empty_bzip2_stream(rest))):
                    header = _BZIP2_HEADER.match(rest) is not None
                    damaged = near_stream_magic(rest, _BZIP2_MAGIC)
                    return offset + skipped, header or damaged
                if not chunk:
                    return None
                held = rest
                offset += skipped

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        # The one caller, _StdlibSeekContract, resolves a relative seek itself and
        # passes SEEK_SET or SEEK_END only.
        result = super().seek(offset, whence)
        if result != 0:
            self._armed = False
        if whence == io.SEEK_END or result < offset:
            # The seek reached the decoder's end, which can be short or wrong: settle
            # it before a position is handed out, as a read at the end would.
            if self._settle_end_for_seek(result):
                # The standard library holds the stream now; it seeks to the real
                # place, or raises as it does with the accelerator off.
                return super().seek(offset, whence)
        else:
            takeover = self._takeover()
            if takeover is not None and self._hand_over_at_layout_gap(takeover, result):
                return super().seek(offset, whence)
        return result

    def _settle_end_for_seek(self, position: int) -> bool:
        """Run the checks a read at the decoder's end runs, for a seek that reached it
        at output ``position``; return whether the standard library took over.

        An armed check means the decoder read the stream as empty: the standard library
        decides, as on the first read. Otherwise a skipped region before ``position``
        hands over at it, then the end check runs (combined CRCs, and what follows the
        last stream)."""
        if self._armed:
            self._fall_back_to_stdlib()
            return True
        takeover = self._takeover()
        if takeover is None:
            return False
        if self._hand_over_at_layout_gap(takeover, position):
            return True
        return self._end_unchecked and self._check_end()

    def _hand_over_at_layout_gap(
        self, takeover: _StdlibOnAcceleratorError, position: int
    ) -> bool:
        """Hand over at the first skipped region at or before output ``position``, so
        a seek past it gets the standard library's verdict; return whether it did."""
        gap = self._layout_gap(position, False)
        if gap is None or gap > position:
            return False
        takeover.switch_to_stdlib(gap)
        return True

    def nearest_resume_offset(self, target: int) -> int | None:
        # Sits on the decompressed chain (accelerator, then maybe stdlib fallback).
        return ask_resume_offset(self._inner, target)

    def _fall_back_to_stdlib(self) -> None:
        """Replace a decoder that read the stream as empty with the standard library,
        from the start; a read and a seek fall back here alike. The seek is why this is
        not ``empty_to_stdlib`` of :class:`_StdlibOnAcceleratorError`, which acts on a
        read only."""
        self._armed = False
        self._end_unchecked = False  # the stdlib engine reports its own end
        self._replace_inner(_stdlib_bzip2(self._views.for_stdlib(), self._config))


# The 48-bit magic numbers that start a bzip2 block and an end-of-stream marker. Each
# is followed by a 32-bit CRC: the block's, or the stream's combined CRC.
_BZIP2_BLOCK_MAGIC = 0x314159265359
_BZIP2_EOS_MAGIC = 0x177245385090

# How many blocks a bzip2 takeover keeps as resume points: the newest at or before the
# reader, which it starts from, and those before it, which make a later seek back
# cheap. A block point is a few bytes (no window), but each costs one read to check.
_BZIP2_RESUME_POINTS = 8


def _bzip2_marker_at(view: BinaryIO, bit: int) -> tuple[int, int] | None:
    """The 48-bit magic and the 32-bit CRC after it, at bit offset ``bit`` of ``view``;
    ``None`` when the source ends first."""
    view.seek(bit // 8)
    raw = view.read(11)
    shift = bit % 8
    if len(raw) * 8 < shift + 80:
        return None
    field = (int.from_bytes(raw, "big") >> (len(raw) * 8 - shift - 80)) & (
        (1 << 80) - 1
    )
    return field >> 32, field & 0xFFFFFFFF


# The most bytes between two streams that :class:`_Bzip2Layout` reads to check they are
# zero padding and empty streams. A longer stretch counts as skipped, which only hands
# the read to the standard library: it then decodes that stretch itself. The spec row
# on zero padding between streams states this bound.
_BZIP2_LAYOUT_MAX_BETWEEN = 1 << 20


class _Bzip2Layout:
    """Check that rapidgzip's bzip2 index covers the source with nothing skipped.

    The index (``available_block_offsets``) maps the bit offset of each block, of each
    end-of-stream marker of a stream with data, and of the end of the last such marker,
    to its offset in the output. A block's end is not in it, so blocks inside a stream
    are not checked here (the combined CRC covers them); what is checked is where each
    stream starts. The first starts at offset 0. Each later one starts where the end of
    the one before it is rounded up to a byte, after any zero padding and empty streams,
    which the standard library also accepts there. A stream starts with its four-byte
    header, and its first block follows the header.

    :meth:`first_gap` is called with the index as it grows; entries already walked are
    not read again. rapidgzip lists the index in ascending bit order and only appends to
    it, so the new entries are the ones after the count already walked, found without
    sorting or scanning the whole index from Python. Fetching the index from rapidgzip
    still copies all of it, so each walk costs time linear in the blocks indexed so far
    (see ``dev-docs/formats/bzip2.md`` §2.3 for the measured cost). An index that is not
    in that order, or that lists an entry behind the ones walked, which rapidgzip has
    not been seen to do, restarts the walk over the whole index.
    """

    def __init__(self) -> None:
        # The magic read at each bit offset walked (``None`` for the end entry).
        self._kinds: dict[int, int | None] = {}
        self._last_bit = -1
        # How many index entries, in the index's own order, have been walked.
        self._walked = 0
        # Between streams: the byte where the next stream, or padding, may start.
        self._next_stream: int | None = 0
        # The output offset where the index first skips part of the source, once found.
        self.gap: int | None = None
        # The highest output offset of the entries walked; -1 before the first.
        self.covered = -1

    def _restart(self) -> None:
        self._last_bit = -1
        self._walked = 0
        self.covered = -1
        self._next_stream = 0

    def first_gap(
        self, offsets: dict[int, int], open_view: Callable[[], BinaryIO]
    ) -> int | None:
        """The output offset of the first skipped part of the source, or ``None``."""
        if self.gap is not None:
            return self.gap
        if len(offsets) <= self._walked:
            return None
        new = list(itertools.islice(offsets, self._walked, None))
        if new[0] <= self._last_bit or any(a >= b for a, b in itertools.pairwise(new)):
            self._restart()
            new = sorted(offsets)
        self._walked = len(offsets)
        with open_view() as view:
            for bit in new:
                if self._next_stream is not None:
                    if bit == self._next_stream * 8:
                        # The end of the last end-of-stream marker, rounded to a byte.
                        self._kinds[bit] = None
                        self._last_bit = bit
                        self.covered = offsets[bit]
                        continue
                    if not _bzip2_stream_starts(view, self._next_stream, bit):
                        self.gap = offsets[bit]
                        return self.gap
                    self._next_stream = None
                kind = self._kinds.get(bit)
                if bit not in self._kinds:
                    marker = _bzip2_marker_at(view, bit)
                    kind = self._kinds[bit] = marker[0] if marker else None
                if kind == _BZIP2_EOS_MAGIC:
                    self._next_stream = -(-(bit + 80) // 8)
                self._last_bit = bit
                self.covered = offsets[bit]
        return None


def _bzip2_stream_starts(view: BinaryIO, start: int, bit: int) -> bool:
    """Whether a stream whose first block (or end-of-stream marker) is at bit ``bit``
    begins at byte ``start``, after nothing but zero padding and empty streams."""
    if bit % 8:
        return False
    header = bit // 8 - _BZIP2_HEADER_LEN
    if header < start or header - start > _BZIP2_LAYOUT_MAX_BETWEEN:
        return False
    view.seek(start)
    between = view.read(header - start + _BZIP2_HEADER_LEN)
    if len(between) != header - start + _BZIP2_HEADER_LEN:
        return False
    return (
        _padding_and_empty_bzip2_streams(between[: header - start]) == header - start
        and _BZIP2_HEADER.fullmatch(between[header - start :]) is not None
    )


def _bzip2_resume_points(
    accelerator: BinaryIO, position: int, open_view: Callable[[], BinaryIO]
) -> list[SeekPoint]:
    """The blocks rapidgzip's bzip2 decoder had indexed before its error, at or before
    ``position``, as points ``Bzip2ResumeDecoder`` starts from.

    Only the accelerator's index knows where a block's output starts. Scanning the
    source for the block magic would find where blocks are, but the decompressed offset
    of each is known only by decoding everything before it, which is the decode from
    the start a resume point saves. So with no indexed block before ``position`` the
    takeover starts at the origin.

    The index also lists each end-of-stream marker; the magic read at each offset keeps
    only blocks. Offset 0 is left out, since the origin is already a point.
    """
    offsets = getattr(accelerator, "available_block_offsets", None)
    if offsets is None:
        return []
    candidates = sorted(
        (
            (decoded, bit)
            for bit, decoded in offsets().items()
            if 0 < decoded <= position
        ),
        reverse=True,
    )
    points: list[SeekPoint] = []
    if not candidates:
        return points
    with open_view() as view:
        for decoded, bit in candidates:
            marker = _bzip2_marker_at(view, bit)
            if marker is None or marker[0] != _BZIP2_BLOCK_MAGIC:
                continue
            points.append(SeekPoint(decoded, bit // 8, Bzip2Resume(bit % 8)))
            if len(points) == _BZIP2_RESUME_POINTS:
                break
    points.reverse()
    return points


# Bytes read per step while looking past an accelerator's end for the first byte that is
# neither zero padding nor part of an empty stream.
_TRAILING_SCAN_CHUNK = 1 << 16

# A bzip2 stream with no blocks: the ``BZh`` header and block-size digit, the
# end-of-stream magic, and a combined CRC of zero. The header is byte-aligned and the
# two fields after it fill whole bytes, so the stream has no padding bits. This is what
# ``bzip2 -c /dev/null`` writes (with the digit ``9``).
#
# The pattern is also the guard: bytes that only look like such a stream are not
# skipped, and so are still reported. The CRC must be zero, because the combined CRC of
# no blocks is zero; the standard-library engine refuses any other value as corrupt.
# The digit must be 1 to 9: with any other, the standard-library engine does not start
# a stream there either (``_BZIP2_STREAMS``); it refuses the bytes as a damaged stream
# header (``near_stream_magic``), and the end check hands over to it there.
_EMPTY_BZIP2_TEMPLATE = b"BZh9\x17\x72\x45\x38\x50\x90\x00\x00\x00\x00"
_EMPTY_BZIP2_STREAM_LEN = len(_EMPTY_BZIP2_TEMPLATE)
_EMPTY_BZIP2_STREAM_BYTES = (
    re.escape(_EMPTY_BZIP2_TEMPLATE[:3])
    + rb"[1-9]"
    + re.escape(_EMPTY_BZIP2_TEMPLATE[4:])
)
_EMPTY_BZIP2_STREAM = re.compile(_EMPTY_BZIP2_STREAM_BYTES)
# A stream header: ``BZh`` and the block-size digit. It must accept the same bytes as
# _BZIP2_STREAMS, which decides whether the standard library reads a further stream: the
# end handover and the layout walk rely on the two agreeing.
_BZIP2_HEADER = re.compile(rb"BZh[1-9]")
_BZIP2_HEADER_LEN = 4
_EMPTY_BZIP2_STREAM_RUN = re.compile(rb"(?:" + _EMPTY_BZIP2_STREAM_BYTES + rb")*")
_ZERO_RUN = re.compile(rb"\x00*")


def _run_end(pattern: re.Pattern[bytes], data: bytes, pos: int) -> int:
    match = pattern.match(data, pos)
    # Both run patterns are repetitions, so they match at least the empty string.
    assert match is not None
    return match.end()


def _padding_and_empty_bzip2_streams(data: bytes) -> int:
    """How many bytes at the start of ``data`` are zeros and whole empty streams.

    A run of zeros and a run of empty streams are matched in turn. One pattern with a
    zero byte and a stream as alternatives costs about fifty times the CPU on a chunk of
    zeros, and allocates megabytes.
    """
    pos = _run_end(_ZERO_RUN, data, 0)
    while (after := _run_end(_EMPTY_BZIP2_STREAM_RUN, data, pos)) != pos:
        pos = _run_end(_ZERO_RUN, data, after)
    return pos


def _starts_empty_bzip2_stream(data: bytes) -> bool:
    """Whether ``data`` is shorter than an empty bzip2 stream and could begin one."""
    if len(data) >= _EMPTY_BZIP2_STREAM_LEN:
        return False
    completed = data + _EMPTY_BZIP2_TEMPLATE[len(data) :]
    return _EMPTY_BZIP2_STREAM.fullmatch(completed) is not None


class Bzip2Codec(StreamCodec):
    codec = Codec.BZIP2
    stream_format = StreamFormat.BZIP2
    magic = (MagicSignature(0, b"BZh", ArchiveFormat.BZ2),)

    def open(
        self, source: CodecSource, params: CodecParams, config: StreamConfig
    ) -> BinaryIO:
        # Imports rapidgzip into this process, which on a free-threaded build re-enables
        # the GIL (see deps.LazyOptional).
        if _bzip2_uses_accelerator(config):
            indexed_bzip2_file = deps.rapidgzip_bzip2()
            assert indexed_bzip2_file is not None
            _refuse_forward_only_accelerator(source, "use_indexed_bzip2", "bzip2")
            # rapidgzip's bundled bzip2 decoder, not the separate indexed_bzip2 package (see the
            # deps.rapidgzip_bzip2 note): keeps a single accelerator library in the process.
            # Bound the input: AES pad after EOS is trailing garbage that rapidgzip
            # prints to stderr outside DiagnosticCollector.
            accel_source, views = _accelerator_backstop_source(
                _bound_rapidgzip_source(source, params, config)
            )
            stream = _open_accelerator(indexed_bzip2_file, accel_source)
            # _refuse_forward_only_accelerator has refused a source that cannot seek.
            assert views is not None
            # A data error hands the read to the standard library, which delivers what
            # it delivers with the accelerator off and raises its error; see
            # _StdlibOnAcceleratorError.
            takeover = _StdlibOnAcceleratorError(
                stream,
                views=views,
                open_stdlib=lambda fallback: _stdlib_bzip2(fallback, config),
                label="bzip2",
                takes_over=self._accelerator_data_error,
                resume_points=_bzip2_resume_points,
            )
            # The decoder reads garbage as an empty stream; see _Bzip2EmptyStreamCheck.
            return _StdlibSeekContract(
                _Bzip2EmptyStreamCheck(
                    takeover,
                    views=views,
                    config=config,
                    single_stream=params.single_stream,
                )
            )
        if config.use_indexed_bzip2 is AcceleratorMode.ON:
            raise PackageNotInstalledError(
                _RAPIDGZIP_REQUIREMENT.message("bzip2 random access")
            )
        # A rewind re-decompresses from the start; the outer ArchiveStream warns about
        # that (see rewind_warning). The [seekable] accelerator (above) gives real
        # random access.
        return _stdlib_bzip2(source, config, single_stream=params.single_stream)

    def translate(self, exc: Exception) -> ArchiveyError | None:
        if isinstance(exc, OSError) and "Invalid data stream" in str(exc):
            return CorruptionError(f"bzip2 stream is corrupt: {exc!r}")
        if isinstance(exc, (EOFError, ValueError)):
            return TruncatedError(f"bzip2 stream is truncated: {exc!r}")
        return None

    def translator(self, config: StreamConfig) -> ExceptionTranslator:
        if _bzip2_uses_accelerator(config):
            return self._translate_accelerator
        return self.translate

    def rewind_warning(self, config: StreamConfig) -> RewindWarning | None:
        # An engaged accelerator no longer suppresses the report: its index can be sparse
        # enough that a backward seek still re-decodes megabytes. The distance decides.
        return RewindWarning(
            "bzip2",
            accelerator="rapidgzip",
            suggest_install=not _bzip2_uses_accelerator(config)
            and not deps.rapidgzip.available(),
        )

    def _accelerator_data_error(self, exc: Exception) -> bool:
        """Whether ``exc`` is the bzip2 accelerator's verdict on the data, which the
        standard library takes over from.

        Not an ``EOFError`` or ``OSError``: :meth:`_translate_accelerator` maps those for
        the standard-library engine behind it, and from the accelerator they would be
        an I/O fault, not a verdict. Not an error from the caller's own source either.
        """
        if isinstance(exc, (EOFError, OSError)) or from_callers_source(exc):
            return False
        return isinstance(
            self._translate_accelerator(exc), (CorruptionError, TruncatedError)
        )

    def _translate_accelerator(self, exc: Exception) -> ArchiveyError | None:
        """Translate the rapidgzip bzip2 accelerator's exceptions to the library's error types."""
        if from_callers_source(exc):
            return None  # the caller's source raised it, through _TrappingSource
        text = str(exc)
        if isinstance(exc, UnicodeDecodeError) and isinstance(exc.object, bytes):
            # rapidgzip quotes the offending input byte in some messages ("…magic
            # string 'BZh' … with \xf2 …"). When that byte is not UTF-8, the message
            # itself fails to decode on its way to Python, and the error raised is
            # this one, holding the message bytes. Read the message from them, so the
            # arms below match it as they match a message that did decode.
            text = exc.object.decode("utf-8", "replace")
        if isinstance(exc, RuntimeError) and "Calculated CRC" in text:
            return CorruptionError(
                f"Error reading bzip2 stream (rapidgzip bzip2): {exc!r}"
            )
        if isinstance(exc, RuntimeError) and text in (
            "std::exception",
            "Unknown exception",
        ):
            return CorruptionError(
                f"Error reading bzip2 stream (rapidgzip bzip2): {exc!r}"
            )
        if "[BZip2 block" in text:
            # Corrupt block data or block header (e.g. "[BZip2 block header] Invalid Huffman
            # coding group count"); surfaced as ValueError or RuntimeError depending on where.
            return CorruptionError(
                f"Error reading bzip2 stream (rapidgzip bzip2): {exc!r}"
            )
        if isinstance(exc, (ValueError, RuntimeError)) and (
            "Huffman" in text
            or "magic" in text  # "Input header is not BZip2 magic string 'BZh'…"
            or "Blocksize must be one of" in text  # stream header's level byte
            or "bit string" in text
            or "bad optional access" in text  # accelerator read past a corrupt block
        ):
            # Corrupt Huffman tables, stream header, block magic, or internal state,
            # outside a "[BZip2 block]"-tagged context (e.g. "Constructing a Huffman
            # coding … failed!" or "bad optional access") — found by the corpus
            # mutation harness, apart from the header's block-size byte.
            return CorruptionError(
                f"Error reading bzip2 stream (rapidgzip bzip2): {exc!r}"
            )
        if isinstance(exc, ValueError) and "has no valid fileno" in text:
            return StreamNotSeekableError(
                "the rapidgzip bzip2 accelerator does not support non-seekable streams"
            )
        if isinstance(exc, io.UnsupportedOperation) and (
            "seek" in text or "tell" in text
        ):
            return StreamNotSeekableError(
                "the rapidgzip bzip2 accelerator does not support non-seekable streams"
            )
        if isinstance(exc, (EOFError, OSError)):
            # The stdlib engine that _Bzip2EmptyStreamCheck falls back to raises these.
            # This does not widen translation: translate maps only an OSError saying
            # "Invalid data stream"; any other OSError comes back None and propagates.
            return self.translate(exc)
        return None
