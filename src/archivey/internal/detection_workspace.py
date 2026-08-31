"""Detection-owned prefix workspace: one handle, one growing buffer, range views.

Every tier that reads from the front of a source does so through a
:class:`PrefixWorkspace`. Extending the window reads only the delta; bytes already
retrieved are never re-fetched. A seekable caller stream records its entry position,
reads forward once, and restores once in an exception-safe exit. A non-seekable source
uses the same replay buffer the backend will consume (:class:`PeekableStream`).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import BinaryIO, Callable, cast

from archivey.detection_cost import (
    DetectionBudget,
    DetectionCapability,
    DetectionCostReceipt,
    MutableDetectionCostReceipt,
    TierSkipReason,
)
from archivey.internal.streams.peekable import PeekableStream
from archivey.internal.streams.streamtools import (
    is_seekable,
    read_exact,
    source_byte_size,
)

# Non-seekable ``read_at`` ceiling for content-probe chain walks: reaching offset N means
# buffering [0, N). 1 MiB covers a second link after a 4- or 5-nibble first block; a
# 6-nibble first block (up to 16 MiB) is declined (``None`` → cannot disprove).
PROBE_READ_AT_MAX_OFFSET_NONSEEKABLE = 1 << 20


class PrefixWorkspace:
    """Monotonically growing prefix buffer over a detection source.

    Consumers ask for ranges relative to the archive origin (the position detection
    started from). The workspace decides whether that is a buffer slice, a delta read, or
    (once, for the tail) a seek toward the end.
    """

    def __init__(
        self,
        source: str | Path | BinaryIO,
        budget: DetectionBudget,
    ) -> None:
        self._budget = budget
        self._receipt = MutableDetectionCostReceipt()
        self._buf = bytearray()
        self._closed = False
        self._entry_pos: int | None = None
        self._path_handle: BinaryIO | None = None
        self._owned_path = False
        self._seekable_stream: BinaryIO | None = None
        self._peekable: PeekableStream | None = None
        self._raw_forward: BinaryIO | None = None
        self._spool: tempfile.SpooledTemporaryFile[bytes] | None = None
        self._spool_abandoned = False
        self._tail_done = False
        self._source_exhausted = False
        # Total size of the underlying object from its own offset 0, when cheap.
        self._total_size = source_byte_size(source)
        self._kind: str

        if isinstance(source, (str, Path)):
            self._kind = "path"
            self._path_handle = open(source, "rb")
            self._owned_path = True
            self._entry_pos = 0
            if self._total_size is None:
                try:
                    self._total_size = os.fstat(self._path_handle.fileno()).st_size
                except (OSError, AttributeError):
                    pass
        elif isinstance(source, PeekableStream):
            self._kind = "peekable"
            self._peekable = source
            # PeekableStream is the opener's shared replay buffer — never a second layer.
        elif is_seekable(source):
            self._kind = "seekable"
            self._seekable_stream = source
            self._entry_pos = source.tell()
        else:
            self._kind = "forward"
            self._raw_forward = source
            if budget.spool_non_seekable_up_to > 0:
                self._begin_spool(source)

    @property
    def budget(self) -> DetectionBudget:
        return self._budget

    @property
    def receipt(self) -> DetectionCostReceipt:
        return self._receipt.freeze()

    @property
    def skips(self) -> tuple:
        return tuple(self._receipt.skips)

    @property
    def buffer(self) -> memoryview:
        """Live view of the prefix buffer — no copy. Callers that need ownership copy."""
        return memoryview(self._buf)

    @property
    def buffered_length(self) -> int:
        return len(self._buf)

    def capabilities(self) -> frozenset[DetectionCapability]:
        """Capabilities supplied by this source under the active budget."""
        caps: set[DetectionCapability] = {DetectionCapability.PREFIX}
        remaining = self.remaining_known()
        if remaining is not None:
            caps.add(DetectionCapability.REMAINING_KNOWN)
        # SIZE_KNOWN follows a measured total, not the transport kind — a fully-spooled
        # pipe has an exact size even though it was not a path or seekable stream.
        if self._total_size is not None and not self._spool_abandoned:
            if self._kind in ("path", "seekable", "spool"):
                caps.add(DetectionCapability.SIZE_KNOWN)

        can_seek = self._kind in ("path", "seekable") or (
            self._spool is not None and not self._spool_abandoned
        )
        if can_seek and self._budget.max_seeks > 0:
            caps.add(DetectionCapability.SEEK)
            if self._budget.max_tail_bytes > 0:
                caps.add(DetectionCapability.TAIL)
        # Do not advertise TAIL without SEEK: read_tail refuses when max_seeks is
        # exhausted / zero even if a spool handle exists.

        # Paths and PeekableStream / seekable streams leave bytes available to a backend.
        if self._kind in ("path", "peekable", "seekable", "spool") or (
            self._spool is not None and not self._spool_abandoned
        ):
            caps.add(DetectionCapability.REREAD)
        return frozenset(caps)

    def remaining_known(self) -> int | None:
        """Provable bytes from the archive origin, or ``None`` if not known.

        An overestimated total size never proves a later offset reachable — we only report
        a remaining length when it is measured from the entry position (or a short peek
        that hit EOF). An abandoned spool truncated the pipe; more bytes may exist, so
        the buffered length is never reported as a proven remaining size.
        """
        if self._spool_abandoned:
            return None
        if self._total_size is not None and self._entry_pos is not None:
            remaining = self._total_size - self._entry_pos
            return remaining if remaining >= 0 else None
        if self._source_exhausted:
            return len(self._buf)
        return None

    def ensure(self, end: int) -> None:
        """Grow the prefix buffer to at least ``end`` bytes (or EOF).

        Does not materialise a ``bytes`` copy of the whole buffer — callers slice
        ``self._buf`` (or :attr:`buffer`) for the span they need.
        """
        if end < 0:
            raise ValueError("end must be non-negative")
        if end <= len(self._buf) or self._source_exhausted:
            return
        needed = end - len(self._buf)
        chunk = self._fetch_forward(needed)
        if chunk:
            self._buf.extend(chunk)
            self._receipt.unique_bytes_read += len(chunk)
        if len(chunk) < needed:
            # Abandoned spool: more bytes may still sit on the pipe; do not claim EOF.
            if not self._spool_abandoned:
                self._source_exhausted = True

    def peek_range(self, origin: int, length: int) -> bytes:
        """Return ``length`` bytes starting at archive-relative ``origin``.

        Extends the prefix buffer when the range lies in the forward-growing region.
        Does not re-fetch bytes already buffered.
        """
        if origin < 0 or length < 0:
            raise ValueError("origin and length must be non-negative")
        if length == 0:
            return b""
        end = origin + length
        self._receipt.prefix_bytes += length
        self.ensure(end)
        if origin >= len(self._buf):
            return b""
        return bytes(self._buf[origin:end])

    def peek_prefix(self, length: int) -> bytes:
        """Convenience: :meth:`peek_range` from archive origin 0."""
        return self.peek_range(0, length)

    def candidate_view(self, candidate_origin: int) -> Callable[[int], bytes]:
        """A ``peek_more(n)``-shaped callable relative to ``candidate_origin``.

        ``peek_more(n)`` returns the first ``n`` bytes of the *candidate*, which are the
        absolute range ``[candidate_origin, candidate_origin + n)``. Served from the
        shared buffer — never a second fetch of bytes already retrieved.
        """
        if candidate_origin < 0:
            raise ValueError("candidate_origin must be non-negative")

        def peek_more(length: int) -> bytes:
            return self.peek_range(candidate_origin, length)

        return peek_more

    def read_at(self, offset: int, length: int) -> bytes | None:
        """Absolute (archive-origin) range read for content-probe chain walks.

        Returns ``None`` when the caller declines (non-seekable past the non-seekable
        probe cap). Short/empty on EOF.
        """
        if offset < 0 or length < 0:
            return None
        end = offset + length
        nonseekable = self._kind in ("peekable", "forward") and (
            self._spool is None or self._spool_abandoned
        )
        if nonseekable and end > PROBE_READ_AT_MAX_OFFSET_NONSEEKABLE:
            return None
        return self.peek_range(offset, length)

    def charge_far(self, nbytes: int) -> None:
        self._receipt.far_bytes += nbytes

    def charge_scanned(self, nbytes: int) -> None:
        self._receipt.scanned_bytes += nbytes

    def charge_decode(self, *, input_bytes: int = 0, output_bytes: int = 0) -> None:
        self._receipt.decode_input += input_bytes
        self._receipt.decode_output += output_bytes

    def record_skip(self, tier: str, reason: TierSkipReason) -> None:
        self._receipt.record_skip(tier, reason)

    def read_tail(self, nbytes: int) -> bytes | None:
        """Seek once toward the end and read up to ``nbytes``.

        Returns ``None`` when the budget or source cannot support a tail read. Charges one
        seek and the unique bytes fetched. The prefix buffer is untouched.
        """
        if nbytes <= 0 or self._budget.max_tail_bytes <= 0:
            self.record_skip("zip_tail", TierSkipReason.NOT_ENABLED_BY_POLICY)
            return None
        if self._receipt.seeks >= self._budget.max_seeks:
            self.record_skip("zip_tail", TierSkipReason.BUDGET_EXHAUSTED)
            return None
        if DetectionCapability.TAIL not in self.capabilities():
            self.record_skip("zip_tail", TierSkipReason.CAPABILITY_UNAVAILABLE)
            return None
        if self._tail_done:
            return None

        take = min(nbytes, self._budget.max_tail_bytes)
        handle = self._random_access_handle()
        if handle is None:
            self.record_skip("zip_tail", TierSkipReason.CAPABILITY_UNAVAILABLE)
            return None

        remaining = self.remaining_known()
        if remaining is not None:
            if remaining < take:
                # Provably too short for a full take — still read what remains near end.
                if remaining <= 0:
                    self.record_skip("zip_tail", TierSkipReason.CAPABILITY_UNAVAILABLE)
                    return None
                take = remaining
            start = (self._entry_pos or 0) + remaining - take
        else:
            # Size unknown: seek to end then back up (one seek toward end + reposition).
            # Counted as the single allowed tail seek for shape accounting.
            handle.seek(0, os.SEEK_END)
            end_pos = handle.tell()
            start = max(self._entry_pos or 0, end_pos - take)
            take = end_pos - start

        handle.seek(start)
        data = read_exact(handle, take)
        self._receipt.seeks += 1
        self._receipt.tail_bytes += len(data)
        self._receipt.unique_bytes_read += len(data)
        self._tail_done = True
        # Restore path/seekable to entry (or end of prefix buffer) so the exit path is
        # consistent; for seekable streams the close() path restores entry_pos.
        if self._kind == "path" and self._path_handle is not None:
            self._path_handle.seek(len(self._buf))
        elif self._kind == "seekable" and self._seekable_stream is not None:
            self._seekable_stream.seek((self._entry_pos or 0) + len(self._buf))
        return data

    def close(self) -> None:
        """Release the path handle and restore a seekable caller's entry position."""
        if self._closed:
            return
        self._closed = True
        try:
            if self._seekable_stream is not None and self._entry_pos is not None:
                self._seekable_stream.seek(self._entry_pos)
        finally:
            if self._owned_path and self._path_handle is not None:
                self._path_handle.close()
                self._path_handle = None
            if self._spool is not None:
                self._spool.close()
                self._spool = None

    def __enter__(self) -> PrefixWorkspace:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _random_access_handle(self) -> BinaryIO | None:
        if self._path_handle is not None:
            return self._path_handle
        if self._seekable_stream is not None:
            return self._seekable_stream
        if self._spool is not None and not self._spool_abandoned:
            return cast(BinaryIO, self._spool)
        return None

    def _fetch_forward(self, nbytes: int) -> bytes:
        if nbytes <= 0:
            return b""
        if self._peekable is not None:
            # Grow the shared PeekableStream buffer; slice only the delta we lack.
            end = len(self._buf) + nbytes
            peeked = self._peekable.peek(end)
            return peeked[len(self._buf) : end]
        if self._spool is not None and not self._spool_abandoned:
            return read_exact(self._spool, nbytes)
        if self._path_handle is not None:
            # Path handle stays at the end of the buffer (forward-only growth).
            expected = len(self._buf)
            if self._path_handle.tell() != expected:
                self._path_handle.seek(expected)
            return read_exact(self._path_handle, nbytes)
        if self._seekable_stream is not None:
            assert self._entry_pos is not None
            # Sequential growth: only seek when a prior tail read moved us. Never rewind
            # to re-fetch bytes already in the buffer.
            expected = self._entry_pos + len(self._buf)
            if self._seekable_stream.tell() != expected:
                self._seekable_stream.seek(expected)
            return read_exact(self._seekable_stream, nbytes)
        if self._raw_forward is not None:
            return read_exact(self._raw_forward, nbytes)
        return b""

    def _begin_spool(self, source: BinaryIO) -> None:
        """Spill a non-seekable source into a bounded temporary file."""
        limit = self._budget.spool_non_seekable_up_to
        spool: tempfile.SpooledTemporaryFile[bytes] = tempfile.SpooledTemporaryFile(
            max_size=min(limit, 1 << 20),
            mode="w+b",
        )
        remaining = limit
        while remaining > 0:
            chunk = source.read(min(65536, remaining))
            if not chunk:
                break
            spool.write(chunk)
            remaining -= len(chunk)
            self._receipt.spooled_bytes += len(chunk)
            self._receipt.unique_bytes_read += len(chunk)
        if remaining == 0:
            # Source may still have more — abandon spooling; tiers needing TAIL are
            # unavailable. Keep the already-spooled prefix (including the one-byte
            # look-ahead) usable as a forward buffer. Do not claim the truncated length
            # as a proven remaining size — more bytes exist on the pipe.
            extra = source.read(1)
            if extra:
                self._spool_abandoned = True
                spool.seek(0)
                self._buf = bytearray(spool.read())
                self._buf.extend(extra)
                spool.close()
                self._spool = None
                self._raw_forward = (
                    None  # rest of the pipe is not available to detection
                )
                self._source_exhausted = False
                self.record_skip("spool", TierSkipReason.BUDGET_EXHAUSTED)
                return
        spool.seek(0)
        self._spool = spool
        self._kind = "spool"
        self._entry_pos = 0
        try:
            spool.seek(0, os.SEEK_END)
            self._total_size = spool.tell()
            spool.seek(0)
        except OSError:
            pass
        self._raw_forward = None


# candidate_origin_for_hit lives in archivey.internal.sfx (F13) — backends import sfx,
# not this module.
