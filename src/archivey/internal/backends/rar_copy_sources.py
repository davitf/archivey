"""Keep the bytes a solid RAR pass decodes for the file copies that follow them.

A RAR5 file copy (``rar -oi``) has no data of its own; it reads as its source member.
``unrar p`` and ``unar`` emit nothing for it, so a solid pass used to serve each copy
from a named open of the source, and every such open decodes the solid stream again
from its start up to the source. Fifty copies of a 1 KiB file behind 100 MiB of solid
data decoded 5 GiB.

:class:`FileCopySources` sits between the pass's decompressor output and its
:class:`~archivey.internal.streams.streamtools.solid.SolidBlockReader` and keeps the
bytes of every member a later copy reads, as the pipe passes them, whether or not
the caller reads that member. Small sources stay in memory; a larger one goes to a
temporary file charged to the reader's spool budget (``SpoolLimits.max_bytes``). A
source that fits neither is not kept, and its copies fall back to the named open.
Kept bytes are served unverified; the caller wraps them in the source's digest
check, as it does the pipe's own bytes.
"""

from __future__ import annotations

import io
import tempfile
from collections.abc import Callable
from typing import BinaryIO

from archivey.internal.streams.streamtools import DelegatingStream
from archivey.internal.streams.streamtools.binaryio import read_blocking
from archivey.internal.streams.streamtools.slice import SlicingStream

# Total bytes one pass keeps in memory. A source that would pass it goes to the
# temporary file instead.
_MEMORY_LIMIT = 8 << 20


class _Kept:
    """One source's range in the pipe and what has been kept of it."""

    __slots__ = ("data", "file_offset", "received", "size", "start", "state")

    def __init__(self, start: int, size: int) -> None:
        self.start = start
        self.size = size
        self.received = 0
        # "new" until its first byte decides where it goes: "memory", "file", or
        # "dropped" (not kept; its copies use the fallback).
        self.state = "new"
        self.data: bytearray | bytes = bytearray()
        self.file_offset = 0

    @property
    def end(self) -> int:
        return self.start + self.size


class _TeeBlock(DelegatingStream):
    """The pass's decoded block, handing each chunk read to ``feed`` with its offset."""

    # Every byte must reach ``feed``, so readinto goes through read.
    readinto_passthrough = False

    def __init__(self, inner: BinaryIO, feed: Callable[[int, bytes], None]) -> None:
        super().__init__(inner)
        self._feed = feed
        self._at = 0

    def read(self, n: int = -1, /) -> bytes:
        data = read_blocking(self._inner, n)
        if data:
            self._feed(self._at, data)
            self._at += len(data)
        return data


class FileCopySources:
    """The sources of a solid pass's file copies, kept as the pipe passes them.

    A member is keyed by ``id()`` of its one ``ArchiveMember`` object, which the
    reader keeps alive; ``_member_id`` is not assigned yet when a streaming pass
    starts. ``source_ids`` are the keys of every member a copy in the pass reads.
    The pass calls :meth:`register` with each such member's pipe range as it reaches
    it, before any read can move the pipe past it, and builds its block reader over
    :meth:`tee`. ``try_spool(size)`` charges the spool budget and says whether
    ``size`` more bytes may go to the temporary file.
    """

    def __init__(
        self, source_ids: frozenset[int], try_spool: Callable[[int], bool]
    ) -> None:
        self._source_ids = source_ids
        self._try_spool = try_spool
        self._kept: dict[int, _Kept] = {}
        # Registered ranges the pipe has not finished, in pipe order.
        self._pending: list[_Kept] = []
        self._pipe_pos = 0
        self._memory = 0
        self._file: BinaryIO | None = None
        # Bytes of the file promised to registered sources. Two sources can be
        # pending at once (a caller that skips both), so each gets its own span.
        self._file_size = 0

    def is_source(self, key: int) -> bool:
        return key in self._source_ids

    def register(self, key: int, start: int, size: int) -> None:
        """Keep ``[start, start + size)`` of the pipe for ``key``'s copies."""
        kept = _Kept(start, size)
        self._kept[key] = kept
        if start < self._pipe_pos:
            # The pipe has already passed it; never the case for a pass that
            # registers members in order, but a partial range would be wrong bytes.
            kept.state = "dropped"
            return
        self._decide(kept)
        if kept.received < kept.size:
            self._pending.append(kept)

    def tee(self, block: BinaryIO) -> BinaryIO:
        """Wrap the pass's decoded block so every byte read passes through here."""
        return _TeeBlock(block, self._feed)

    def _decide(self, kept: _Kept) -> None:
        if kept.size == 0 or self._memory + kept.size <= _MEMORY_LIMIT:
            kept.state = "memory"
            self._memory += kept.size
        elif self._try_spool(kept.size):
            kept.state = "file"
            if self._file is None:
                self._file = tempfile.TemporaryFile()  # noqa: SIM115 - closed in close()
            kept.file_offset = self._file_size
            self._file_size += kept.size
        else:
            kept.state = "dropped"

    def _feed(self, at: int, data: bytes) -> None:
        end = at + len(data)
        self._pipe_pos = end
        while self._pending:
            kept = self._pending[0]
            if kept.start >= end:
                return
            lo = max(kept.start + kept.received, at)
            hi = min(kept.end, end)
            if hi > lo:
                chunk = memoryview(data)[lo - at : hi - at]
                if kept.state == "memory":
                    assert isinstance(kept.data, bytearray)
                    kept.data += chunk
                elif kept.state == "file":
                    assert self._file is not None
                    self._file.seek(kept.file_offset + kept.received)
                    self._file.write(chunk)
                kept.received += hi - lo
            if kept.received < kept.size:
                return
            if kept.state == "memory":
                # Immutable from here, so each copy's BytesIO shares it.
                kept.data = bytes(kept.data)
            self._pending.pop(0)

    def open(self, key: int, advance_past: Callable[[int], object]) -> BinaryIO | None:
        """The kept bytes of source ``key``, or ``None`` when they were not kept.

        ``advance_past(end)`` moves the pipe to ``end`` when the caller skipped the
        source and the pipe has not reached the end of it yet; the bytes are kept on
        the way. A pipe that ends early raises from there, as reading the source would.
        """
        kept = self._kept.get(key)
        if kept is None or kept.state == "dropped":
            return None
        if kept.received < kept.size:
            advance_past(kept.end)
            if kept.received < kept.size:
                return None
        if kept.state == "memory":
            return io.BytesIO(kept.data)
        assert self._file is not None
        # Nothing writes to the file while a copy reads it: the pipe only moves for a
        # later member or another copy, and the pass closes this copy's stream first.
        return SlicingStream(self._file, kept.file_offset, kept.size)

    def close(self) -> None:
        self._kept.clear()
        self._pending.clear()
        if self._file is not None:
            self._file.close()
            self._file = None
