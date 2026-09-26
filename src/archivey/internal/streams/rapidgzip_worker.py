"""Child-process side of :class:`~archivey.internal.streams.rapidgzip_child.RapidgzipChildStream`.

rapidgzip 0.16 aborts the whole process (``std::terminate``) when it decodes a gzip,
zlib or raw DEFLATE stream that ends early; see ``dev-docs/known-issues.md``. So
archivey runs it in a child process that runs this file, and an abort costs the
member and not the caller.

This file is run as a script (``python -P rapidgzip_worker.py``) and imports nothing
from ``archivey``: importing any ``archivey`` module imports the whole package, which
costs start-up time on every member. It depends on the standard library and
``rapidgzip`` only.

Protocol. Every frame in both directions is ``<BqI>`` (tag, argument, payload size)
and then the payload. All integers are little-endian.

Parent to child:

- ``OPEN`` once, first. Argument 0: the payload is a file-system path, which the child
  opens itself. Argument 1: the source is the parent's stream, read through ``SRC_*``
  frames (below).
- ``READ`` (argument: at most this many bytes), ``SEEK`` (argument: offset; payload: one
  byte, ``whence``), ``RESUME`` (argument: a decompressed offset; the reply's argument is
  the largest index point at or before it, or -1).
- ``SRC_DATA`` (payload: bytes read), ``SRC_VALUE`` (argument: a position),
  ``SRC_FAIL``: the answers to the child's ``SRC_*`` requests. ``SRC_FAIL`` means the
  parent's source raised; the parent keeps that exception and raises it to its caller.

Child to parent:

- ``OK`` (argument: a position or offset; payload: decoded bytes for ``READ``) or
  ``ERR`` (payload: ``"<type name>\\n<errno or empty>\\n<message>"`` in UTF-8), one per
  request.
- ``SRC_READ`` (argument: size), ``SRC_SEEK`` (argument: offset; payload: ``whence``),
  ``SRC_TELL``: rapidgzip reading the parent's stream. They can arrive before the reply
  to any request, since rapidgzip's own threads read ahead in the background.

The parent closes its end of the pipes to stop the child.
"""

from __future__ import annotations

import importlib
import io
import os
import queue
import struct
import sys
import threading
from typing import IO, Any

FRAME = struct.Struct("<BqI")

OPEN, READ, SEEK, RESUME = 1, 2, 3, 4
SRC_DATA, SRC_VALUE, SRC_FAIL = 10, 11, 12
OK, ERR = 1, 2
SRC_READ, SRC_SEEK, SRC_TELL = 10, 11, 12

OPEN_PATH, OPEN_STREAM = 0, 1

_EOF = (0, 0, b"")


def _read_exact(stream: IO[bytes], size: int) -> bytes | None:
    parts: list[bytes] = []
    while size:
        chunk = stream.read(size)
        if not chunk:
            return None
        parts.append(chunk)
        size -= len(chunk)
    return b"".join(parts)


class _Channel:
    """The pipes to the parent, shared by the main thread and rapidgzip's threads.

    One thread reads stdin and sorts the frames: requests to ``requests``, answers to
    source reads to ``answers``. A parent that goes away puts an end marker on both, so
    no thread waits for ever.
    """

    def __init__(self) -> None:
        self._stdin = sys.stdin.buffer
        self._stdout = sys.stdout.buffer
        self._write_lock = threading.Lock()
        self.requests: queue.SimpleQueue[tuple[int, int, bytes]] = queue.SimpleQueue()
        self.answers: queue.SimpleQueue[tuple[int, int, bytes]] = queue.SimpleQueue()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        try:
            while True:
                header = _read_exact(self._stdin, FRAME.size)
                if header is None:
                    break
                tag, arg, size = FRAME.unpack(header)
                payload = _read_exact(self._stdin, size) if size else b""
                if payload is None:
                    break
                target = self.answers if tag >= SRC_DATA else self.requests
                target.put((tag, arg, payload))
        except (OSError, ValueError):
            pass
        self.requests.put(_EOF)
        self.answers.put(_EOF)

    def send(self, tag: int, arg: int = 0, payload: bytes = b"") -> bool:
        """Write one frame. False if the parent has gone away."""
        with self._write_lock:
            try:
                self._stdout.write(FRAME.pack(tag, arg, len(payload)))
                if payload:
                    self._stdout.write(payload)
                self._stdout.flush()
            except (OSError, ValueError):
                return False
        return True


class _ParentSource(io.RawIOBase):
    """The parent's stream, as rapidgzip sees it.

    Never raises: an exception that crosses into rapidgzip's C++ layer aborts the
    process. A failed read reads as the end of the stream; the parent already holds
    the real error and raises it. There is deliberately no ``fileno``, so rapidgzip
    stays on these methods.
    """

    def __init__(self, channel: _Channel) -> None:
        super().__init__()
        self._channel = channel
        self._lock = threading.Lock()

    def _ask(
        self, tag: int, arg: int = 0, payload: bytes = b""
    ) -> tuple[int, int, bytes]:
        """Send one request; return the answer's ``(tag, argument, payload)``."""
        # One question at a time: the answers come back in the order asked.
        with self._lock:
            if not self._channel.send(tag, arg, payload):
                return SRC_FAIL, 0, b""
            answer = self._channel.answers.get()
            if answer[0] == 0:  # the parent went away; keep answering the next caller
                self._channel.answers.put(_EOF)
                return SRC_FAIL, 0, b""
            return answer

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def readinto(self, buf: Any) -> int:
        view = memoryview(buf).cast("B")
        answer, _, data = self._ask(SRC_READ, len(view))
        if answer != SRC_DATA:
            return 0
        view[: len(data)] = data
        return len(data)

    def seek(self, offset: int, whence: int = 0) -> int:
        answer, value, _ = self._ask(SRC_SEEK, offset, bytes([whence]))
        return value if answer == SRC_VALUE else 0

    def tell(self) -> int:
        answer, value, _ = self._ask(SRC_TELL)
        return value if answer == SRC_VALUE else 0


def _error_payload(exc: BaseException) -> bytes:
    errno = getattr(exc, "errno", None)
    text = f"{type(exc).__name__}\n{'' if errno is None else errno}\n{exc}"
    return text.encode("utf-8", "replace")


def _serve(channel: _Channel, stream: Any) -> None:
    while True:
        tag, arg, payload = channel.requests.get()
        if tag == 0:
            return
        try:
            if tag == READ:
                data = stream.read(arg)
                ok = channel.send(OK, len(data), data)
            elif tag == SEEK:
                ok = channel.send(OK, stream.seek(arg, payload[0] if payload else 0))
            elif tag == RESUME:
                offsets = stream.available_block_offsets().values()
                preceding = [value for value in offsets if value <= arg]
                ok = channel.send(OK, max(preceding) if preceding else -1)
            else:
                ok = channel.send(ERR, 0, _error_payload(ValueError(f"bad tag {tag}")))
        except Exception as exc:  # noqa: BLE001 - reported to the parent, which raises
            ok = channel.send(ERR, 0, _error_payload(exc))
        if not ok:
            return


def main() -> None:
    channel = _Channel()
    tag, kind, payload = channel.requests.get()
    if tag != OPEN:
        return
    try:
        # import_module: rapidgzip has no type stubs, and the type checkers read this
        # file as part of the package.
        rapidgzip = importlib.import_module("rapidgzip")

        source: object = (
            os.fsdecode(payload) if kind == OPEN_PATH else _ParentSource(channel)
        )
        stream = rapidgzip.open(source, parallelization=0)
    except Exception as exc:  # noqa: BLE001 - reported to the parent, which raises
        channel.send(ERR, 0, _error_payload(exc))
        return
    try:
        if channel.send(OK):
            _serve(channel, stream)
    finally:
        # close(), not join_threads(), stops rapidgzip's threads; one still running
        # when the interpreter finalizes aborts the process.
        try:
            stream.close()
        except Exception:  # noqa: BLE001 - the process is ending either way
            pass


if __name__ == "__main__":
    main()
    # Skip interpreter finalization: the pump thread may still be blocked on stdin.
    try:
        sys.stdout.flush()
    except (OSError, ValueError):
        pass
    os._exit(0)
