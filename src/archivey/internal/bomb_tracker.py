"""The decompression-bomb tracker: the byte, ratio and entry-count limits one extraction
enforces as it writes (``ExtractionLimits``)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from archivey.config import ExtractionLimits
from archivey.exceptions import ResourceLimitError

if TYPE_CHECKING:
    from archivey.internal.base_reader import BaseArchiveReader
    from archivey.types import ArchiveMember


class AlwaysStopResourceLimitError(ResourceLimitError):
    """A cumulative/global bomb guard: halts extraction even under ``OnError.CONTINUE``.

    A subclass of ``ResourceLimitError`` so callers catching that type (or
    ``ArchiveyError``) still catch it; the coordinator uses the type to distinguish it
    from a *skippable* per-member ratio failure.
    """


class CopyCounter:
    """Counts the chunks ``ExtractionCoordinator._copy_to_fileobj`` writes as a copy of
    bytes this run already wrote (``BombTracker.count_copy``), not as decoded output."""

    __slots__ = ("_tracker",)

    def __init__(self, tracker: BombTracker) -> None:
        self._tracker = tracker

    def count(self, chunk_bytes: int) -> None:
        self._tracker.count_copy(chunk_bytes, at_link_limit=False)


class BombTracker:
    """Cumulative byte / per-member ratio / archive-wide ratio / entry-count guards.

    Constructed once per extraction call. ``start_member()`` is called (with the
    **original** member) before each member; ``count()`` is called with each chunk
    written.
    """

    def __init__(
        self,
        max_bytes: int | None,
        max_ratio: float | None,
        # The defaults come from ExtractionLimits, the one place the limits are set.
        ratio_activation_threshold: int = ExtractionLimits.ratio_activation_threshold,
        max_entries: int | None = ExtractionLimits.max_entries,
        *,
        source: BaseArchiveReader | None = None,
        live_ratio_activation_threshold: int = (
            ExtractionLimits.live_ratio_activation_threshold
        ),
    ) -> None:
        self._max_bytes = max_bytes
        self._max_ratio = max_ratio
        self._ratio_floor = ratio_activation_threshold
        self._live_ratio_floor = live_ratio_activation_threshold
        # Archive-wide ratio denominators, taken from the reader so the coordinator just
        # hands over the source: the static outer compressed size when it is cheaply known
        # (captured once), otherwise a LIVE sample of the compressed bytes consumed from the
        # source (read fresh on each count(), since it grows as extraction proceeds).
        self._source = source
        self._compressed_source_size = (
            source.compressed_source_size if source is not None else None
        )
        self._max_entries = max_entries
        self._entry_count = 0
        self._total_bytes = 0  # decoded output, cumulative across all members
        # Bytes copied from a file this run already wrote (the hardlink copy fallback).
        # They count toward the byte cap. The part copied at the filesystem's link-count
        # limit also counts toward the archive-wide ratio (``count_copy``).
        self._copied_bytes = 0
        self._link_limit_copied_bytes = 0
        self._member_bytes = 0  # output bytes for the current member
        self._member: ArchiveMember | None = None
        # Output of members a streaming pass wrote and then took back as superseded (see
        # ``refund``). Subtracted for the byte cap only.
        self._refunded_bytes = 0

    @property
    def total_bytes(self) -> int:
        """Every byte written to the destination, decoded or copied."""
        return self._total_bytes + self._copied_bytes

    @property
    def member_bytes(self) -> int:
        return self._member_bytes

    def start_member(self, member: ArchiveMember) -> None:
        # Called with the ORIGINAL member (not a filter copy), so compressed_size and any
        # late-bound fields are accurate. Increments the entry-count guard, which — like
        # the cumulative byte guard — halts even under OnError.CONTINUE.
        self._member = member
        self._member_bytes = 0
        self._entry_count += 1
        if self._max_entries is not None and self._entry_count > self._max_entries:
            raise AlwaysStopResourceLimitError(
                f"Entry-count limit reached: max_entries={self._max_entries} "
                f"(would write entry {self._entry_count})"
            )

    def refund(self, member_bytes: int) -> None:
        """Stop counting one member that a streaming pass took back as superseded.

        Random access never writes a shadowed duplicate, so it never counts one
        (``safe-extraction``: no bomb-limit counting for the skip). A streaming pass
        wrote it before it could know, and its entry is gone from the destination once
        the later copy replaces it, so the entry count stops counting it. Its bytes are
        gone too unless a hardlink made to it still holds them, so the caller passes
        ``member_bytes`` as 0 in that case and the byte cap keeps them. The archive-wide
        ratio counts them either way: those bytes were decoded, and a name repeated many
        times must not decode for free. ``total_bytes``, which progress reports, is not
        reduced: it counts what was written.
        """
        self._entry_count -= 1
        self._refunded_bytes += member_bytes

    def check_recorded_size(self) -> None:
        """Refuse the current member before its data is read, when its recorded size
        is over the per-member ratio.

        Called once the member is about to be decoded, after every check that could
        still skip it, so a member that would not be decoded is never refused here.
        A member's output never passes its recorded size: the member stream refuses
        a byte past it (``CorruptionError``). So a member whose recorded size is over
        the per-member ratio is one ``count()`` would refuse after decoding past the
        activation threshold, and it is refused now instead, with the same skippable
        error. The one difference is a member whose data stops short of its recorded
        size: ``count()`` might never refuse it, and the read would fail as damaged
        instead. Both fail that member alone.

        The archive-wide ratios are left to ``count()``. Refusing early on the archive's
        recorded sizes would stop the whole run on a size nothing has verified, losing
        the intact members after a damaged one; and ``count()`` already stops at the
        chunk that crosses the archive-wide limit, so an early check would save little.
        ``count()`` still runs on every chunk.
        """
        member = self._member
        if self._max_ratio is None or member is None:
            return
        size = member.size
        cs = member.compressed_size
        if size is None or not cs or cs <= 0:
            return
        if size > max(self._ratio_floor, self._max_ratio * cs):
            raise ResourceLimitError(
                f"Decompression ratio {size / cs:.0f}:1 exceeds limit "
                f"max_ratio={self._max_ratio:.0f}:1 (from recorded sizes: "
                f"{size} bytes from {cs} compressed)",
                member_name=member.name,
            )

    def count(self, chunk_bytes: int) -> None:
        """Count a chunk that is about to be written; raise instead if it may not be."""
        self._check_cumulative_bytes(chunk_bytes)
        self._total_bytes += chunk_bytes
        self._member_bytes += chunk_bytes

        # Per-member ratio: activates on THIS member's output; a per-member failure
        # (skippable under OnError.CONTINUE).
        member = self._member
        cs = member.compressed_size if member is not None else None
        if (
            self._max_ratio is not None
            and member is not None
            and self._member_bytes > self._ratio_floor
            and cs
            and cs > 0
        ):
            ratio = self._member_bytes / cs
            if ratio > self._max_ratio:
                raise ResourceLimitError(
                    f"Decompression ratio {ratio:.0f}:1 exceeds limit "
                    f"max_ratio={self._max_ratio:.0f}:1",
                    member_name=member.name,
                )
        self._check_archive_ratio()

    def count_copy(self, chunk_bytes: int, *, at_link_limit: bool) -> None:
        """Count bytes copied from a file this run already wrote, not decoded.

        A hard link that cannot be made writes a second full copy of content already
        on disk: across a device boundary, or past the filesystem's link-count limit.
        Those bytes land on the filesystem, so they count toward ``max_extracted_bytes``
        like any other write.

        A copy made at the link-count limit (``at_link_limit``) also counts toward the
        archive-wide ratio. The archive drives it: one that declares enough links to
        one member makes a copy per limit's worth of links, so a small archive could
        otherwise write far more than its size while staying under the byte cap, which
        is the case the ratio exists for. A cross-device copy does not: whether it
        happens depends on where the caller extracts to, not on the archive. Neither
        counts toward the per-member ratio, since a link member's compressed size says
        nothing about the copy's size.

        A streaming pass that writes a member again from its own earlier copy, once
        the archive's directory changed the member
        (``ExtractionCoordinator._redo_revised_member``), counts it here too: nothing
        is decoded a second time.
        """
        self._check_cumulative_bytes(chunk_bytes)
        self._copied_bytes += chunk_bytes
        if at_link_limit:
            self._link_limit_copied_bytes += chunk_bytes
            self._check_archive_ratio()

    def _check_cumulative_bytes(self, chunk_bytes: int) -> None:
        """Cumulative byte guard (always-stop), checked before ``chunk_bytes`` is written.

        Both callers count a chunk before they write it, so a refused chunk never
        reaches the destination and is not counted. The message says what is on disk
        and what the refused write would have made it.
        """
        written = self.total_bytes - self._refunded_bytes
        would_be = written + chunk_bytes
        if self._max_bytes is not None and would_be > self._max_bytes:
            raise AlwaysStopResourceLimitError(
                f"Extraction limit reached: max_extracted_bytes={self._max_bytes} "
                f"(written {written} bytes; the next {chunk_bytes}-byte chunk would "
                f"make {would_be})"
            )

    def _check_archive_ratio(self) -> None:
        # Archive-wide ratio: activates on CUMULATIVE output; a whole-archive bomb signal
        # (always-stop). Uses the static outer compressed size when it is cheaply known,
        # otherwise a LIVE denominator — the compressed bytes consumed from the source so
        # far — which works for a streaming/pipe source whose total size is unknown. The two
        # are mutually exclusive (static when known, live otherwise), so the ratio is never
        # counted twice.
        css = self._compressed_source_size
        # Decoded output plus the copies made at the link-count limit (``count_copy``).
        output = self._total_bytes + self._link_limit_copied_bytes
        # Says which part was not decoded, so a trip on a store-only archive with many
        # hard links is not read as a decompression bomb.
        copies = self._link_limit_copied_bytes
        detail = (
            f" ({self._total_bytes} bytes decoded, plus {copies} bytes copied for hard "
            f"links past the filesystem's link-count limit)"
            if copies
            else ""
        )
        if self._max_ratio is not None and output > self._ratio_floor:
            if css and css > 0:
                if output / css > self._max_ratio:
                    raise AlwaysStopResourceLimitError(
                        f"Archive-wide decompression ratio "
                        f"{output / css:.0f}:1 exceeds limit "
                        f"max_ratio={self._max_ratio:.0f}:1{detail}"
                    )
            elif self._source is not None and output > self._live_ratio_floor:
                # The live denominator is what has been read so far, so a blank run at
                # the start of the source reads far above the ratio it will end at once
                # the data after it arrives. Waiting for the live floor lets such a start
                # pass as it does when the whole size is known; once a pipe is read to
                # its end, ``consumed`` is that whole size and this check matches the
                # static one (codec-layer re-reads elsewhere only make it larger).
                consumed = self._source.compressed_bytes_consumed
                if consumed and consumed > 0 and output / consumed > self._max_ratio:
                    raise AlwaysStopResourceLimitError(
                        f"Live decompression ratio "
                        f"{output / consumed:.0f}:1 exceeds limit "
                        f"max_ratio={self._max_ratio:.0f}:1{detail}"
                    )
