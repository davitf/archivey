"""Lock-wrapping streams for library-owned shared handles.

Two wrappers (easy to mix up):

- :class:`LockedStream` — hold ``lock`` across **every** read/seek/tell on
  ``inner`` (TAR/ISO under ``MemberStreams.CONCURRENT``: seek-then-read must be
  atomic). Archivey buffering/error wrappers sit *outside* this layer.
- :class:`CloseLockedStream` — serializes only ``close()``; reads stay
  unlocked. Use when concurrent readers share a handle for I/O but close
  must not race.

For independent logical positions over one file, prefer
:class:`~archivey.internal.streams.streamtools.shared.SharedSource` instead of
``LockedStream`` (ZIP-style re-seek-under-lock views).
"""

from __future__ import annotations

import io
import threading
from typing import TYPE_CHECKING, BinaryIO

from archivey.internal.streams.streamtools.base import DelegatingStream
from archivey.internal.streams.streamtools.binaryio import readinto_via_read

if TYPE_CHECKING:
    from _typeshed import WriteableBuffer


class LockedStream(DelegatingStream):
    """Hold ``lock`` across each shared-handle operation on ``inner``.

    Used by TAR/ISO under ``MemberStreams.CONCURRENT`` so library seek-then-read
    sequences on a shared fileobj cannot interleave. Archivey buffering/error wrappers
    sit *outside* this layer.
    """

    def __init__(self, inner: BinaryIO, lock: threading.Lock | threading.RLock) -> None:
        super().__init__(inner)
        self._lock = lock

    def read(self, n: int = -1, /) -> bytes:
        with self._lock:
            return self._inner.read(n)

    def readinto(self, b: WriteableBuffer, /) -> int:
        with self._lock:
            readinto = getattr(self._inner, "readinto", None)
            if readinto is None:
                return readinto_via_read(self._inner, b)
            return readinto(b)

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        if not self.seekable():
            raise io.UnsupportedOperation("seek")
        with self._lock:
            return self._inner.seek(offset, whence)

    def tell(self, /) -> int:
        with self._lock:
            return self._inner.tell()

    def close(self) -> None:
        if self.closed:
            return
        with self._lock:
            super().close()


class CloseLockedStream(DelegatingStream):
    """Serialize only ``close()``; leave read/seek unlocked on ``inner``.

    Contrast :class:`LockedStream` (locks every op). ZIP under
    ``MemberStreams.CONCURRENT``: stdlib ``zipfile`` already serializes shared-fp
    seek/read via ``_SharedFile``, but ``_fileRefCnt`` on open/close races under
    free-threaded CPython. This wrapper covers close; the ZIP backend also
    serializes ``ZipFile.open`` under the same lock (``_handle_guard``) — open +
    close together are enough; locking reads would needlessly serialize
    independent decompressors.
    """

    def __init__(self, inner: BinaryIO, lock: threading.Lock | threading.RLock) -> None:
        super().__init__(inner)
        self._lock = lock

    def close(self) -> None:
        if self.closed:
            return
        with self._lock:
            super().close()
