"""``SharedSource`` — concurrent-safe byte-range views over one underlying source.

A source (an OS file handle or a seekable ``BinaryIO``) has exactly one file position.
Two consumers that each ``seek``+``read`` the same handle will clobber each other's
offset — even on a single thread. :class:`SharedSource` mints independent, seekable,
**non-owning** views over ``[start, start+length)``; each view keeps its own ``_pos``,
and every read re-seeks the underlying to that absolute position under a shared lock so
the seek+read pair is atomic.

Views are :class:`~archivey.internal.streams.streamtools.slice.SharedView` instances
(a :class:`~archivey.internal.streams.streamtools.slice.SlicingStream` subclass) with
the source lock engaged — the same bound/tell logic, plus lock+reseek on every read.

This is the streamtools analogue of stdlib ``zipfile._SharedFile``. It is deliberately
archivey-dependency-free: it raises stdlib-shaped errors (``ValueError`` / ``OSError`` /
``io.UnsupportedOperation``), never ``archivey.exceptions``.

**Views are unbuffered.** Every ``read`` is one locked seek+read on the shared handle —
cheap (an in-memory offset move on a regular file / ``BytesIO``), and the current
consumers (codec streams, which buffer internally) already read in large chunks. A
consumer that issues many tiny reads should wrap its view in ``io.BufferedReader``
itself; buffering inside the primitive would double-copy for everyone else.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import BinaryIO, Callable

from archivey.internal.streams.streamtools.binaryio import (
    is_filename,
    is_seekable,
    source_byte_size,
)
from archivey.internal.streams.streamtools.slice import SharedView, _clamp_slice_length


class SharedSource:
    """Factory for locked, per-view-position slices over one seekable source.

    Construct from a :class:`~pathlib.Path` (opens and owns the handle) or an already-open
    seekable ``BinaryIO`` (does **not** take ownership — the caller closes it).

    ``wrap_handle`` (optional) is applied once to the underlying file handle after it is
    opened or accepted. Production readers use it to install a seek counter when
    measurement is enabled, and identity otherwise.
    """

    def __init__(
        self,
        source: Path | BinaryIO,
        *,
        wrap_handle: Callable[[BinaryIO], BinaryIO] | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._closed = False
        self._owns_handle = False

        if isinstance(source, Path) or is_filename(source):
            # ``is_filename`` admits bytes; Path wants str/PathLike[str], so fsdecode.
            path = source if isinstance(source, Path) else Path(os.fsdecode(source))
            self._handle: BinaryIO = open(path, "rb")
            self._owns_handle = True
            # Frozen at construction: the source is assumed not to grow.
            self._size: int | None = source_byte_size(path)
        else:
            if not is_seekable(source):
                raise ValueError("SharedSource requires a seekable BinaryIO source")
            self._handle = source
            # Frozen at construction: the source is assumed not to grow.
            self._size = source_byte_size(source)

        if wrap_handle is not None:
            self._handle = wrap_handle(self._handle)

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def size(self) -> int | None:
        """Cheap total byte size of the source, when knowable."""
        return self._size

    def view(self, start: int, length: int | None = None) -> SharedView:
        """Mint a non-owning seekable view over ``[start, start+length)``.

        ``length is None`` means "to the end of the source". When ``_size`` is
        already known, over-long ``length`` is clamped here (and an omitted one
        frozen to the remaining bytes) so a ``wrap_handle`` that hides cheap size
        from ``source_byte_size`` (``SeekCountingStream``) still yields a short
        view. :class:`SharedView` construction clamps again from the handle when
        that probe succeeds. Past-EOF (``start >= size``) is the empty-view case
        of the same clamp. Negative ``start``/``length`` remain hard errors.
        """
        self._raise_if_closed()
        if start < 0:
            raise ValueError(f"view start must be non-negative, got {start}")
        if length is not None and length < 0:
            raise ValueError(f"view length must be non-negative, got {length}")

        length = _clamp_slice_length(start, length, self._size)

        return SharedView(
            self._handle,
            start=start,
            length=length,
            lock=self._lock,
            check_open=self._raise_if_closed,
        )

    def close(self) -> None:
        """Mark closed and close an owned path handle; never closes a caller-owned stream."""
        if self._closed:
            return
        self._closed = True
        if self._owns_handle:
            self._handle.close()

    def __enter__(self) -> SharedSource:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _raise_if_closed(self) -> None:
        if self._closed:
            raise ValueError("I/O operation on closed file.")
