"""``ReadAheadStream`` — a read buffer that keeps a full-count inner's short-is-terminal rule.

``io.BufferedReader`` is the wrong buffer over a full-count stream that defers its
error. When a fill returns fewer bytes than the caller still wants, it fills again in
the same call. A decoder with deferred truncation returns the recoverable prefix on
one read and raises on the next, so that second fill moves the error into the call
and the prefix is lost.

This buffer reads its inner at most once per caller ``read``, and asks for at least
``buffer_size`` bytes each time. The inner is full-count: a short return is its end,
so the caller gets a short return too, and the error comes on the next call. When the
one fill raises and bytes from an earlier fill are still held, those bytes go to the
caller first and the error is raised on the next ``read``.
"""

from __future__ import annotations

import io
from typing import BinaryIO

from archivey.internal.streams.streamtools.base import DelegatingStream
from archivey.internal.streams.streamtools.binaryio import (
    ask_resume_offset,
    check_read_size,
    check_seek_args,
    read_blocking,
)


class ReadAheadStream(DelegatingStream):
    """Buffer reads of a full-count ``inner``, reading it at most once per ``read``.

    Owns ``inner``, as every :class:`DelegatingStream` does. Seekable when ``inner``
    is: a seek drops the buffer and any held error, then seeks ``inner``.
    """

    readinto_passthrough = False

    def __init__(self, inner: BinaryIO, buffer_size: int) -> None:
        # The buffered bytes are ``_buffer[_offset:]``: a read moves the offset and
        # copies only what it returns.
        self._buffer = b""
        self._offset = 0
        self._held: Exception | None = None
        self._buffer_size = buffer_size
        super().__init__(inner)

    def read(self, n: int | None = -1, /) -> bytes:
        n = check_read_size(n)
        if self.closed:
            raise ValueError("I/O operation on closed file.")
        if self._held is not None:
            err, self._held = self._held, None
            raise err
        start = self._offset
        buffered = len(self._buffer) - start
        if 0 <= n <= buffered:
            self._offset = start + n
            return self._buffer[start : start + n]
        held = self._buffer[start:]
        self._buffer = b""
        self._offset = 0
        want = -1 if n < 0 else max(n - buffered, self._buffer_size)
        try:
            data = read_blocking(self._inner, want)
        except Exception as err:
            if not held:
                raise
            self._held = err
            return held
        if n < 0 or len(data) <= n - buffered:
            return held + data
        take = n - buffered
        self._buffer = data
        self._offset = take
        return held + data[:take]

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        offset, whence = check_seek_args(offset, whence)
        if self._buffer and whence != io.SEEK_END:
            # A target inside the buffer moves the offset and leaves the inner where
            # it is: a forward-only inner would otherwise seek back to re-read it.
            end = self._inner.tell()
            begin = end - len(self._buffer)
            target = offset if whence == io.SEEK_SET else begin + self._offset + offset
            if begin <= target <= end:
                self._offset = target - begin
                return target
        if whence == io.SEEK_CUR:
            # The inner is ahead of the caller by the bytes still in the buffer.
            offset -= len(self._buffer) - self._offset
        position = self._inner.seek(offset, whence)
        self._buffer = b""
        self._offset = 0
        self._held = None
        return position

    def tell(self, /) -> int:
        return self._inner.tell() - (len(self._buffer) - self._offset)

    def nearest_resume_offset(self, target: int) -> int | None:
        # The buffer keeps the inner's offsets.
        return ask_resume_offset(self._inner, target)
