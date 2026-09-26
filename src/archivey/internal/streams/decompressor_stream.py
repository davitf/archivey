"""Seekable decompress *engine*: one stream class, many codec strategies.

:class:`DecompressorStream` owns the buffer, position, seek-point table, and seek
algorithm. Codecs plug in through the :class:`Decoder` protocol (feed / flush /
recreate / index discovery) — not by subclassing the stream.

Where the decoders live (easy to mix with this file's name):

- :mod:`archivey.internal.streams.decompress` — zlib/deflate, Brotli, PPMd, BCJ,
  Deflate64 adapters
- :mod:`archivey.internal.streams.xz` / ``lzip`` / ``unix_compress`` — larger
  format-specific decoders (index scan / LZW)

``codecs.StreamCodec.open`` wires those into an ``ArchiveStream``; this module is
only the shared engine underneath.
"""

from __future__ import annotations

import bisect
import io
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import (
    Any,
    BinaryIO,
    Callable,
    Generic,
    Iterator,
    Protocol,
    Sequence,
    TypeVar,
    cast,
)

from archivey.diagnostics import DiagnosticCode, SeekIndexContext
from archivey.exceptions import CorruptionError, TruncatedError
from archivey.internal.diagnostics_collector import (
    DiagnosticCollector,
    resolve_collector,
)
from archivey.internal.logs import streams as logger
from archivey.internal.streams.streamtools import ReadOnlyIOStream, ensure_bufferedio


@dataclass(order=True)
class SeekPoint:
    """A point from which decompression can resume.

    Ordered by ``decompressed_offset`` only, so ``bisect`` over a ``list[SeekPoint]``
    works without a ``key=`` argument.
    """

    decompressed_offset: int
    compressed_offset: int = field(compare=False)
    # Opaque per-codec resume token. Compared by identity, and by == where a
    # codec re-emits an equal-valued token for the same offset
    # (``_resolve_same_offset_collision``). Deliberately Any: object breaks
    # the assignment of a non-None value to ``_XzBlockBounds`` in
    # ``XzDecoder.from_point``, the block it hands to ``_XzBlockResume``; one Any
    # here vs a cast there.
    state: Any = field(default=None, compare=False)


@dataclass
class DecodeOut:
    """Bytes produced by a decoder step, plus any absolute seek points discovered."""

    data: bytes
    points: list[SeekPoint] = field(default_factory=list)


class Decoder(Protocol):
    """Codec strategy for :class:`DecompressorStream`."""

    def recreate(self, point: SeekPoint, inner: BinaryIO) -> Decoder: ...

    def feed(self, chunk: bytes, max_length: int = -1) -> DecodeOut:
        """Decode ``chunk``, producing at most ``max_length`` output bytes when ≥ 0.

        When ``max_length`` limits output, unconsumed compressed input must be retained
        so a subsequent ``feed(b"", max_length=…)`` (while :attr:`needs_input` is false)
        can continue without the stream reading more from the source. ``max_length=-1``
        means unlimited (used by ``readall`` / flush-to-EOF).
        """
        ...

    def flush(self) -> DecodeOut:
        """Finalize at compressed EOF — the sole truncation-detection point.

        Called exactly once when the compressed source is exhausted. Implementations
        MUST arm :attr:`pending_error` with a :class:`~archivey.exceptions.TruncatedError`
        when the decode is incomplete (not :attr:`finished`, or finished alongside a
        known truncation such as unix-compress leftover bits), and MUST return any
        recoverable flush leftover rather than raising that truncation inline.
        :class:`~archivey.exceptions.CorruptionError` for trailing junk / hard
        corruption MAY still raise from ``flush``.
        """
        ...

    @property
    def finished(self) -> bool: ...

    @property
    def needs_input(self) -> bool:
        """False when more output can be produced without reading new compressed bytes."""
        ...

    @property
    def pending_error(self) -> BaseException | None: ...

    def clear_pending_error(self) -> None:
        """Clear :attr:`pending_error` after the stream has raised it (or on seek reset)."""
        ...

    def close(self) -> None:
        """Release decoder-owned native resources deterministically.

        Optional teardown hook: most decoders need nothing here (GC frees the
        underlying object), but a decoder holding a native worker whose lifetime
        is unsafe under GC (PPMd) uses this to reach a clean state before it is
        dropped. Idempotent; never raises.
        """
        ...

    def build_index(
        self, inner: BinaryIO, last_known: SeekPoint
    ) -> tuple[list[SeekPoint], int | None]: ...


class BaseDecoder:
    """Default decoder behavior: empty points, no pending error, no-op index build.

    Subclasses that override :meth:`flush` own truncation detection: at compressed
    EOF, arm :attr:`pending_error` when the stream is incomplete and return any
    leftover bytes — do not raise :class:`~archivey.exceptions.TruncatedError` from
    ``flush`` itself (the stream raises it on the next empty ``read``, or from
    ``readall``).
    """

    _pending_error: BaseException | None = None

    @property
    def pending_error(self) -> BaseException | None:
        return self._pending_error

    def clear_pending_error(self) -> None:
        self._pending_error = None

    @property
    def needs_input(self) -> bool:
        return True

    @property
    def drains_after_flush(self) -> bool:
        """True when ``flush`` returned output and more follows through ``feed(b"")``.

        Only PPMd, which takes a truncated member's input whole at compressed EOF,
        sets it; every other decoder hands back all its remaining output from
        ``flush``.
        """
        return False

    def close(self) -> None:
        """No-op teardown hook (see :meth:`Decoder.close`); overridden by PPMd."""

    def build_index(
        self, inner: BinaryIO, last_known: SeekPoint
    ) -> tuple[list[SeekPoint], int | None]:
        del inner, last_known
        return [], None


# Compressed bytes read per fill when the decoder needs more input.
#
# Historically matched CPython gzip / ``_compression.DecompressReader``
# (``io.DEFAULT_BUFFER_SIZE`` = 8 KiB). That feed forces ~17 Python trips through
# the decode loop for a typical 256 KiB ZIP member while ``zipfile`` decompresses
# each member in one C call — the dominant residual ZIP read-all gap after the
# stream-layering work in #136/#137 (see ``review/archive/2026-07-28-performance/residual-gap.md``).
# 64 KiB reaches the measured plateau for those members; ``max_length`` still
# bounds peak *output* buffer on ``read(n)`` (the #128 / F3a contract), and ZIP
# members are additionally capped by their ``SlicingStream`` compressed extent.
_COMPRESSED_READ_SIZE = 65536
# Ceiling when a large bounded ``read(n)`` (whole-member via fused verify) asks
# for more output than the default feed — one compressed read ≈ one C inflate.
_COMPRESSED_READ_SIZE_MAX = 1 << 20
# Output budget when skipping forward during seek (unbounded skip would reintroduce
# the per-read amplification bomb on highly compressible spans).
_SEEK_OUTPUT_CHUNK = 65536


def _compressed_feed_size(max_length: int) -> int:
    """How many compressed bytes to pull for one decoder feed.

    Large bounded requests scale up toward ``max_length`` (capped) so a known-size
    whole-member read collapses to one inflate call. Small / unbounded requests
    keep the default feed; output amplification remains gated by ``max_length``.
    """
    if max_length < 0 or max_length <= _COMPRESSED_READ_SIZE:
        return _COMPRESSED_READ_SIZE
    return min(max_length, _COMPRESSED_READ_SIZE_MAX)


MakeDecoder = Callable[[SeekPoint, BinaryIO], Decoder]


# Upper bound on a stream's seek table, in entries. A seek table is an optimisation, so
# passing the cap thins the table instead of failing: points are kept at least a spacing
# apart (in decompressed bytes) chosen so the table falls to half the cap, and later
# points keep that spacing. A seek then decodes at most about one spacing plus one unit
# further than it would with every point. Nothing becomes unreadable (unless a policy
# escalates the SEEK_INDEX_DEGRADED it reports), which is why this is a structural
# constant and not a ListingLimits field. Without it the table grows with the unit count
# the file declares: an lzip member can be 26 bytes, so a table cost ~9x the file's size.
# Real files stay far below it: xz -T0 writes 24 MiB blocks and ncompress checks for a
# CLEAR every 10 kB of input, so reaching it takes a multi-GiB file even at the densest.
MAX_SEEK_POINTS = 1 << 18

# ``SeekIndexContext.error_type`` when a table was thinned; no exception is involved.
SEEK_TABLE_THINNED = "SeekTableThinned"

_T = TypeVar("_T")


def thinning_spacing(span: int) -> int:
    """The spacing that keeps points spread over ``span`` bytes to half the cap."""
    return max(1, -(-2 * span // MAX_SEEK_POINTS))


def spaced_subset(
    items: Sequence[_T], key: Callable[[_T], int], spacing: int
) -> list[_T]:
    """Keep the first item, then each item at least ``spacing`` past the last one kept.

    ``items`` must be in non-decreasing ``key`` order. Over a key span ``S`` at most
    ``S // spacing + 1`` items survive, whatever their count.
    """
    kept: list[_T] = []
    last = 0
    for item in items:
        k = key(item)
        if not kept or k - last >= spacing:
            kept.append(item)
            last = k
    return kept


class SpacedCollector(Generic[_T]):
    """Collect items with non-decreasing keys, thinning to stay within the cap.

    For the backward index scans, which cannot know their entry count up front: an
    lzip trailer names only the member before it, and xz streams are found one at a
    time. Keys are decompressed distances, so thinning keeps seek cost bounded.
    Memory stays at most :data:`MAX_SEEK_POINTS` items. The first item is always kept.
    """

    def __init__(self, key: Callable[[_T], int]) -> None:
        self._key = key
        self._limit = MAX_SEEK_POINTS
        self.items: list[_T] = []
        self.spacing = 0
        self.thinned = False

    def add(self, item: _T) -> None:
        k = self._key(item)
        if self.items and k - self._key(self.items[-1]) < self.spacing:
            return
        self.items.append(item)
        if len(self.items) > self._limit:
            self.thinned = True
            self.spacing = max(
                self.spacing * 2, thinning_spacing(k - self._key(self.items[0]))
            )
            self.items = spaced_subset(self.items, self._key, self.spacing)


class _IndexBlock(Protocol):
    """Fields ``build_index_backwards`` reads on a scanned block.

    Codec-specific extras (XZ ``uncompressed_size``, lzip CRC) stay on the
    concrete block type; ``include_block`` / ``to_point`` see that type via
    ``_B``, not this protocol.
    """

    @property
    def decompressed_start(self) -> int: ...

    @property
    def decompressed_end(self) -> int: ...


_B = TypeVar("_B", bound=_IndexBlock)


class _ScanFn(Protocol[_B]):
    """The backward index/trailer scan ``build_index_backwards`` calls.

    Parameter names in a callback protocol bind every implementation, so the
    first two are positional-only: a scanner may call them whatever it likes.
    This module only ever passes them positionally. A scanner that had to thin its
    entries to stay within :data:`MAX_SEEK_POINTS` calls ``on_thinned``.
    """

    def __call__(
        self,
        stream: BinaryIO,
        file_size: int,
        /,
        *,
        stop_at: int,
        start_decompressed_offset: int,
        on_thinned: Callable[[], None] | None = None,
    ) -> list[_B]: ...


def build_index_backwards(
    inner: BinaryIO,
    last_known: SeekPoint,
    scan_fn: _ScanFn[_B],
    to_point: Callable[[_B], SeekPoint],
    warning_msg: str,
    *,
    codec_name: str = "",
    collector: DiagnosticCollector | None = None,
    scan: str = "backwards_index",
    include_block: Callable[[_B], bool] | None = None,
) -> tuple[list[SeekPoint], int | None]:
    """Backward scan → seek points + total decompressed size.

    ``scan_fn`` reads only index/trailer structures (no decompression). On a
    ``CorruptionError`` (e.g. valid-but-unparseable trailing data) it emits
    ``SEEK_INDEX_DEGRADED`` and returns an empty index so the stream falls back to
    sequential decoding.

    ``include_block``, when set, filters scanned bounds before they become seek
    points (e.g. XZ zero-``uncompressed_size`` blocks that share a decompressed
    offset with the next real block and are never useful resume targets). The
    total size still comes from the last bound's ``decompressed_end``, so a scanner
    that thins its entries always keeps the last one.
    """
    file_size = inner.seek(0, io.SEEK_END)
    thinned = False

    def on_thinned() -> None:
        nonlocal thinned
        thinned = True

    try:
        bounds = scan_fn(
            inner,
            file_size,
            stop_at=last_known.compressed_offset,
            start_decompressed_offset=last_known.decompressed_offset,
            on_thinned=on_thinned,
        )
    except CorruptionError as e:
        message = warning_msg % (e,)
        resolve_collector(collector).emit(
            code=DiagnosticCode.SEEK_INDEX_DEGRADED,
            message=message,
            context=SeekIndexContext(
                codec=codec_name,
                scan=scan,
                error_type=type(e).__name__,
            ),
            logger=logger,
        )
        return [], None
    if thinned:
        resolve_collector(collector).emit(
            code=DiagnosticCode.SEEK_INDEX_DEGRADED,
            message=(
                f"{codec_name} index has more than {MAX_SEEK_POINTS} entries; kept a "
                "spaced subset, so seeks may decode further"
            ),
            context=SeekIndexContext(
                codec=codec_name, scan=scan, error_type=SEEK_TABLE_THINNED
            ),
            logger=logger,
        )
    points = [
        to_point(b)
        for b in bounds
        if b.decompressed_start > last_known.decompressed_offset
        and (include_block is None or include_block(b))
    ]
    total: int | None = bounds[-1].decompressed_end if bounds else None
    return points, total


class DecompressorStream(ReadOnlyIOStream):
    """Seekable ``BinaryIO`` over compressed bytes, driven by a :class:`Decoder`.

    Owns: output buffer, logical position, seek-point table, and the seek algorithm
    (bisect to a point → recreate decoder → skip forward). Does **not** know codec
    formats — ``make_decoder`` supplies that.

    ``seekable=False`` skips index/seek-point work (forward-only cheap path).
    ``readable``/``writable``/``write``/``readinto`` come from :class:`ReadOnlyIOStream`.

    A stream ``path`` is borrowed by default (``owns_inner=False``): the archive
    handle / ``SharedView`` stays with the caller. Pipeline stages that wrap a
    *private* inner (a later 7z ``_FilterStage`` over the previous coder's
    output, including the LZMA1 cap ``SlicingStream(owns_inner=True)``) pass
    ``owns_inner=True`` so that inner is closed with this stream. A first-stage
    BCJ (Copy+BCJ, BCJ-alone) leaves the default: the pack view is borrowed.
    ``ensure_bufferedio`` is non-closing, so ``_inner.close()`` would not reach
    that source — ``_owned_inner`` holds the object this stream is responsible
    for closing (the path we opened, or the stream we were handed with
    ``owns_inner=True``).

    A report the collector's policy escalates (``SEEK_INDEX_DEGRADED`` under
    ``strict()``) is raised when the operation that met it has left the stream
    consistent, never from inside a decode or an index scan. ``read`` raises before it
    consumes: the bytes it decoded stay buffered and the position is unchanged, so the
    next read returns them. ``seek`` and a size query raise after they finish, with the
    position where they left it. Either way the handle stays usable.
    """

    def __init__(
        self,
        path: str | os.PathLike[str] | BinaryIO,
        *,
        make_decoder: MakeDecoder,
        collector: DiagnosticCollector | None = None,
        codec_name: str = "",
        seekable: bool = True,
        owns_inner: bool = False,
    ) -> None:
        super().__init__()
        self._owned_inner: BinaryIO | None = None
        self._diagnostics_collector = collector
        self._codec_name = codec_name
        # Declared seek demand: without it, skip seek-point tables / index scans, but
        # still allow O(n) seeks from the origin (compressed TAR needs that for random
        # access even when MemberStreams.SEEKABLE was not declared).
        self._index_enabled = seekable
        self._seek_points: list[SeekPoint] = [SeekPoint(0, 0)]
        self._index_built = False
        self._index_build_attempted = False
        # Minimum decompressed distance between points once the table has been thinned
        # (0 until then); see _thin_seek_table.
        self._min_spacing = 0
        self._table_thinned = False
        self._make_decoder = make_decoder
        try:
            if isinstance(path, (str, os.PathLike)):
                self._inner: BinaryIO = open(os.fspath(path), "rb")
                self._owned_inner = self._inner
            else:
                # typeshed keeps BufferedIOBase and BinaryIO apart; at runtime it is one.
                self._inner = cast("BinaryIO", ensure_bufferedio(path))
                if owns_inner:
                    self._owned_inner = path
            self._decoder: Decoder = make_decoder(self._seek_points[0], self._inner)
        except BaseException:
            # ``close()`` needs a decoder, and ``IOBase.__del__`` calls it on any
            # instance not marked closed, including one whose ``__init__`` raised.
            # Release what this stream already owns and mark it closed here, so the
            # finalizer has nothing left to do. A decoder constructor that raises
            # (``PpmdDecoder`` refuses PPMd7 without a pack or unpack size) or a path
            # that cannot be opened otherwise dies again there as ``AttributeError``.
            owned = self._owned_inner
            self._owned_inner = None
            try:
                if owned is not None:
                    owned.close()
            except Exception:  # noqa: BLE001 - the refusal below is the error to report
                pass
            finally:
                super().close()
            raise
        self._buffer = bytearray()
        self._eof = False
        self._pos = 0
        self._size: int | None = None

    def seekable(self) -> bool:
        return self._inner.seekable()

    def add_seek_points(self, points: Sequence[SeekPoint]) -> None:
        """Merge ``points`` into the sorted index, skipping duplicates.

        Pass points in ascending ``decompressed_offset`` order; the common in-order case
        is an O(1) append, out-of-order insertions fall back to bisect. No-ops when
        index construction was not declared (no seek-point table is built), except for
        refining the origin's ``compressed_offset`` / ``state`` (unix-compress header
        commit must apply even when the table is not built).

        Once the table has been thinned (:meth:`_thin_seek_table`), a point closer than
        ``_min_spacing`` to a neighbour is dropped before the collision rules below.

        Same-``decompressed_offset`` collisions:
        - Origin (offset 0) may always be refined in place (unix-compress header commit).
        - For other offsets, an exact duplicate is skipped; a *forward* refinement
          (same ``state``, ``compressed_offset`` moves forward) last-wins — unix-compress
          empty CLEAR segments legitimately re-emit the same decompressed offset at a
          later compressed resume point. A ``state=None`` placeholder yields to a richer
          non-``None`` resume state (XZ block bounds over a progressive stream-start).
          Divergent non-``None`` states raise :class:`CorruptionError` (never a raw
          ``AssertionError``) so hostile indexes cannot escape the ``ArchiveyError`` tree.
        """
        for point in points:
            # Origin refinement always applies — resume must skip a committed header
            # even when seek-point indexing was not declared. Last-wins here is
            # intentional: unix-compress emits SeekPoint(0, HEADER_SIZE) to replace
            # the placeholder SeekPoint(0, 0).
            origin = self._seek_points[0]
            if (
                point.decompressed_offset == 0
                and origin.decompressed_offset == 0
                and (
                    origin.compressed_offset != point.compressed_offset
                    or origin.state is not point.state
                )
            ):
                self._seek_points[0] = point
                continue
            if not self._index_enabled:
                continue
            if self._min_spacing and self._too_close(point):
                continue
            if point < self._seek_points[-1]:
                i = bisect.bisect_left(self._seek_points, point)
                if i < len(self._seek_points) and self._seek_points[i] == point:
                    self._resolve_same_offset_collision(i, point)
                    continue
                self._seek_points.insert(i, point)
            elif self._seek_points[-1] == point:
                self._resolve_same_offset_collision(len(self._seek_points) - 1, point)
                continue
            else:
                self._seek_points.append(point)
            if len(self._seek_points) > MAX_SEEK_POINTS:
                self._thin_seek_table()

    def _too_close(self, point: SeekPoint) -> bool:
        """Whether ``point`` lands within ``_min_spacing`` of a neighbour in the table."""
        points = self._seek_points
        i = bisect.bisect_left(points, point)
        if i < len(points) and points[i] == point:
            return False  # same offset: the collision rules decide
        # i >= 1: the origin sits at offset 0 and point is past it.
        if point.decompressed_offset - points[i - 1].decompressed_offset < (
            self._min_spacing
        ):
            return True
        return (
            i < len(points)
            and points[i].decompressed_offset - point.decompressed_offset
            < self._min_spacing
        )

    def _thin_seek_table(self) -> None:
        """Bring a table past :data:`MAX_SEEK_POINTS` down to at most half of it.

        Points are kept at least a spacing apart, and later points keep it. Any subset
        is safe: every point resumes on its own, whatever else the table holds.
        """
        span = self._seek_points[-1].decompressed_offset
        self._min_spacing = max(self._min_spacing * 2, thinning_spacing(span))
        self._seek_points[:] = spaced_subset(
            self._seek_points, lambda p: p.decompressed_offset, self._min_spacing
        )
        message = (
            f"{self._codec_name} seek table passed {MAX_SEEK_POINTS} entries; kept "
            "a spaced subset, so seeks may decode further"
        )
        context = SeekIndexContext(
            codec=self._codec_name, scan="seek_table", error_type=SEEK_TABLE_THINNED
        )
        collector = resolve_collector(self._diagnostics_collector)
        if self._table_thinned:
            # Recorded once per stream; the policy still applies to every thinning.
            collector.escalate_only(
                code=DiagnosticCode.SEEK_INDEX_DEGRADED,
                message=message,
                context=context,
            )
            return
        self._table_thinned = True
        collector.emit(
            code=DiagnosticCode.SEEK_INDEX_DEGRADED,
            message=message,
            context=context,
            logger=logger,
        )

    def _resolve_same_offset_collision(self, index: int, point: SeekPoint) -> None:
        """Skip duplicates; allow forward refinement / richer-state merge; else error."""
        existing = self._seek_points[index]

        def _same_state(a: object, b: object) -> bool:
            # Identity for shared objects; value equality for re-emitted XZ block bounds
            # (progressive enrichment vs build_index construct distinct instances).
            return a is b or a == b

        if existing.compressed_offset == point.compressed_offset and _same_state(
            existing.state, point.state
        ):
            return
        if point.compressed_offset >= existing.compressed_offset and _same_state(
            existing.state, point.state
        ):
            self._seek_points[index] = point
            return
        # Prefer a non-None resume state over a progressive placeholder (XZ: block
        # bounds beat a stream-start SeekPoint emitted before enrichment / after a
        # prior build_index). Same-offset with only one side carrying state is the
        # legitimate multi-stream path; keep the richer point.
        if existing.state is None and point.state is not None:
            self._seek_points[index] = point
            return
        if existing.state is not None and point.state is None:
            return
        raise CorruptionError(
            "seek-point collision at the same decompressed_offset with "
            f"differing resume data: existing={existing!r} new={point!r}"
        )

    def _find_best_seek_point(self, pos: int) -> SeekPoint:
        """The last seek point with ``decompressed_offset <= pos``."""
        i = bisect.bisect_right(self._seek_points, SeekPoint(pos, 0)) - 1
        return self._seek_points[i]

    def nearest_resume_offset(self, target: int) -> int:
        """Decompressed offset the decoder must restart from to reach ``target``.

        The origin when no seek point lies closer, which is what makes a single-block
        ``.xz`` behave exactly like a codec with no index at all — the case
        ``STREAM_REWIND_REDECOMPRESSES`` used to be blind to, because it keyed on the
        codec's identity rather than on this.

        Deliberately reads the table **as it stands** and never calls
        ``_ensure_index_built()``: a diagnostic that built an index would change the cost
        it is reporting on. An index still being filled in reports a resume point further
        back than the finished one would, which errs toward telling the caller.
        """
        return self._find_best_seek_point(target).decompressed_offset

    def _reset_to_seek_point(self, point: SeekPoint) -> None:
        self._inner.seek(point.compressed_offset)
        # Dispose the outgoing decoder deterministically before dropping it:
        # mid-member a PPMd decode can leave its native worker parked, and relying
        # on __del__/GC timing to quiesce it is exactly what close() exists to avoid
        # (no-op for every other codec). recreate() builds a fresh decoder from the
        # config, not from the old native state, so closing first is safe.
        old_decoder = self._decoder
        old_decoder.close()
        self._decoder = old_decoder.recreate(point, self._inner)
        self._decoder.clear_pending_error()
        self._buffer.clear()
        self._eof = False
        self._pos = point.decompressed_offset

    def _ingest_decode(self, out: DecodeOut) -> bytes:
        if out.points:
            self.add_seek_points(out.points)
        return out.data

    def _read_decompressed_chunk(self, max_length: int = -1) -> bytes:
        if not self._decoder.needs_input:
            drained = self._ingest_decode(self._decoder.feed(b"", max_length))
            if drained:
                return drained
            # Decoder claimed retained input but produced nothing (e.g. a stuck
            # lzma needs_input=False under a budget). Fall through to reading more
            # compressed bytes — or EOF — so the caller cannot spin forever.
        chunk = self._inner.read(_compressed_feed_size(max_length))
        if not chunk:
            leftover = self._ingest_decode(self._decoder.flush())
            if leftover and getattr(self._decoder, "drains_after_flush", False):
                # The decoder took its input whole at compressed EOF and has more
                # output: keep pulling it through ``feed(b"")``. It reports False
                # once drained, and the next empty read calls ``flush`` again.
                return leftover
            self._eof = True
            # Incomplete EOF: decoder owns TruncatedError via pending_error (set in
            # flush). Deliver leftover now; bounded read raises on the next empty
            # read. Only publish a clean complete size when truly finished and not
            # truncated (pending_error alone is insufficient — unix-compress can be
            # finished=True with leftover-bits truncation).
            if self._decoder.pending_error is None and self._decoder.finished:
                self._size = self._pos + len(self._buffer) + len(leftover)
                self._index_built = True  # a forward scan to EOF is a complete index
            return leftover
        return self._ingest_decode(self._decoder.feed(chunk, max_length))

    @contextmanager
    def _deferring_raises(self) -> Iterator[Callable[[], Exception | None]]:
        """Hold escalated reports until this operation's state is consistent.

        See the class docstring. Without a collector there is nothing to hold: the
        throwaway :func:`resolve_collector` builds runs the library default policy.
        """
        collector = self._diagnostics_collector
        if collector is None:
            yield lambda: None
            return
        with collector.deferring_raises() as pending:
            yield pending

    def readall(self) -> bytes:
        # Prefer join-of-chunks over staging through the shared bytearray: a whole-stream
        # read never needs the partial-read buffer, and the extend + bytes(buffer) copy
        # was a measurable share of ZIP read-all overhead (perf review H2).
        chunks: list[bytes] = []
        with self._deferring_raises() as pending:
            if self._buffer:
                chunks.append(bytes(self._buffer))
                self._buffer.clear()
            while not self._eof:
                chunk = self._read_decompressed_chunk()
                if chunk:
                    chunks.append(chunk)
            held = pending()
            if held is not None:
                # Put back what was decoded, unconsumed. When the decode finished
                # clean, the EOF branch published a size from the emptied buffer; the
                # true total is _pos plus everything in chunks.
                joined = b"".join(chunks)
                self._buffer[:0] = joined
                if self._decoder.pending_error is None and self._decoder.finished:
                    self._size = self._pos + len(joined)
                raise held
        data = b"".join(chunks)
        # A read(-1)/readall() caller expects the complete stream and will not call
        # again, so a deferred pending_error (e.g. truncated .Z) must raise here —
        # unlike chunked read(n), which returns bytes now and raises on the next empty
        # read. Partial bytes from this call are dropped: the caller asked for the
        # whole stream and it is incomplete. Gate _size *before* raising so a caller
        # that catches TruncatedError cannot then read a clean prefix-as-complete size.
        # The dropped bytes still count in _pos: the decoder has consumed them, and a
        # _pos left behind would make seek() to it a no-op over a finished decoder, so
        # the next read would return b"" as if the stream ended cleanly.
        err = self._decoder.pending_error
        if err is not None:
            self._decoder.clear_pending_error()
            self._pos += len(data)
            raise err
        if self._size is None or self._pos <= self._size:
            self._pos += len(data)
            self._size = self._pos
        return data

    def read(self, n: int = -1, /) -> bytes:
        if n == 0:
            return b""
        if n is None or n < 0:
            return self.readall()
        with self._deferring_raises() as pending:
            while len(self._buffer) < n and not self._eof:
                need = n - len(self._buffer)
                self._buffer.extend(self._read_decompressed_chunk(need))
            held = pending()
            if held is not None:
                raise held
        data = bytes(self._buffer[:n])
        del self._buffer[:n]
        self._pos += len(data)
        if not data:
            err = self._decoder.pending_error
            if err is not None:
                self._decoder.clear_pending_error()
                raise err
        return data

    def close(self) -> None:
        # Quiesce any decoder-owned native worker before dropping references, so a
        # blocked PPMd worker cannot be resumed into freed memory at GC.
        if self.closed:
            return
        try:
            self._decoder.close()
        finally:
            owned = self._owned_inner
            self._owned_inner = None
            try:
                if owned is not None:
                    owned.close()
            finally:
                super().close()

    def _ensure_index_built(self) -> None:
        if not self._index_enabled or self._index_built or self._index_build_attempted:
            return
        inner_pos = self._inner.tell()
        # Always scan from the absolute origin. Using a mid-stream last_known (from
        # progressive enrichment) as the baseline renumbers later streams' decompressed
        # offsets incorrectly. A full from-origin scan is cheap (index/trailer only).
        new_points, new_size = self._decoder.build_index(self._inner, SeekPoint(0, 0))
        self._index_build_attempted = True
        if new_points or new_size is not None:
            self._index_built = True
        if new_points:
            self.add_seek_points(new_points)
        if new_size is not None:
            self._size = new_size
        # build_index may have seeked _inner (e.g. lzip's backward trailer scan);
        # restore it so the decompressor's expected read position is still valid.
        if self._inner.tell() != inner_pos:
            self._inner.seek(inner_pos)

    def try_get_size(self) -> int | None:
        """The total decompressed size if cheaply available (via the index), else ``None``."""
        if self._size is not None:
            return self._size
        if not self._index_enabled or not self._inner.seekable():
            return None
        with self._deferring_raises() as pending:
            self._ensure_index_built()
            held = pending()
            if held is not None:
                raise held
        return self._size

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        with self._deferring_raises() as pending:
            pos = self._seek(offset, whence)
            held = pending()
            if held is not None:
                raise held
        return pos

    def _seek(self, offset: int, whence: int) -> int:
        if not self._inner.seekable():
            raise io.UnsupportedOperation("seek")

        if whence == io.SEEK_SET:
            new_pos = offset
        elif whence == io.SEEK_CUR:
            new_pos = self._pos + offset
        elif whence == io.SEEK_END:
            new_pos = -1  # resolved below once _size is known
        else:
            raise ValueError(f"Invalid whence: {whence}")

        if whence == io.SEEK_END or (
            new_pos > self._pos + len(self._buffer)
            and new_pos > self._seek_points[-1].decompressed_offset
        ):
            self._ensure_index_built()

        if whence == io.SEEK_END:
            if self._size is None:
                # Building the index didn't reveal the size; scan to EOF to find it
                # without buffering all remaining data in RAM.
                self._pos += len(self._buffer)
                self._buffer.clear()
                while not self._eof:
                    data = self._read_decompressed_chunk(_SEEK_OUTPUT_CHUNK)
                    self._pos += len(data)
                # Truncated streams must not publish a clean complete size; surface
                # the deferred fault instead of asserting or treating the prefix as
                # the full stream.
                err = self._decoder.pending_error
                if err is not None:
                    self._decoder.clear_pending_error()
                    raise err
                if self._size is None:
                    raise TruncatedError(
                        "Cannot seek to end: decompressed size is unknown "
                        "(stream ended incompletely)"
                    )
            new_pos = self._size + offset

        if new_pos < 0:
            raise ValueError(f"Invalid offset: {offset}")

        if self._size is not None and new_pos >= self._size:
            self._buffer.clear()
            self._eof = True
            self._pos = new_pos
            return self._pos

        if new_pos == self._pos:
            return self._pos

        if new_pos < self._pos:
            self._reset_to_seek_point(self._prepare_seek_point(new_pos))
        elif new_pos <= self._pos + len(self._buffer):
            del self._buffer[: new_pos - self._pos]
            self._pos = new_pos
            return self._pos
        else:
            best = self._prepare_seek_point(new_pos)
            if best.decompressed_offset > self._pos:
                self._reset_to_seek_point(best)
            else:
                self._pos += len(self._buffer)
                self._buffer.clear()

        assert not self._buffer
        if self._pos == new_pos:
            return self._pos

        while not self._eof:
            decompressed = self._read_decompressed_chunk(_SEEK_OUTPUT_CHUNK)
            if self._pos + len(decompressed) >= new_pos:
                self._buffer.extend(decompressed[new_pos - self._pos :])
                self._pos = new_pos
                return self._pos
            self._pos += len(decompressed)

        self._pos = new_pos
        return self._pos

    def _prepare_seek_point(self, pos: int) -> SeekPoint:
        """Best resume point for ``pos`` in the table as it stands.

        Every point resumes on its own (an XZ block point carries its stream's bounds),
        so a table that progressive enrichment has only partly filled is safe to use.
        """
        return self._find_best_seek_point(pos)

    def tell(self, /) -> int:
        return self._pos
