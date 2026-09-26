"""Run the rapidgzip DEFLATE-family decoder in a child process.

rapidgzip 0.16 aborts the whole process when it decodes a gzip, zlib or raw DEFLATE
stream that ends early: a destructor in its chunk decoder throws (``BitReader::tell``,
"The bit buffer should not contain more data than have been read from the file!") and
``std::terminate`` runs. That happens for a path, a file object and an in-memory
buffer alike, and no Python ``try/except`` can catch it. In a child process the abort
costs the member, and the caller gets an archivey error.

:class:`RapidgzipChildStream` is the seekable decompressed stream the codec layer
wraps, as it wrapped rapidgzip's own reader in-process. The child runs
``rapidgzip_worker.py`` as a script, which imports nothing from ``archivey``; the
protocol is in that file's docstring. A path source is opened by the child. A stream
source stays in this process: the child asks for each read, seek and tell, and this
process answers from the caller's stream, so an exception from that stream reaches
the caller unchanged.

Measured cost (``scripts/bench_rapidgzip_child.py``, one Linux machine): about 25 ms to
start the child and import rapidgzip, and about 70 µs per round trip. A full read took
1.1 to 1.35 times as long as in-process rapidgzip, and stayed about 1.5 times faster
than the stdlib engine.

bzip2 is not here: rapidgzip's bzip2 decoder has not been seen to abort on truncated
input, and it stays in-process (``_AcceleratorStream`` in ``codecs.py``).
"""

from __future__ import annotations

import builtins
import io
import os
import subprocess
import sys
import tempfile
import weakref
from pathlib import Path
from typing import IO, BinaryIO

from archivey.exceptions import (
    ArchiveyError,
    ArchiveyUsageError,
    CorruptionError,
    ReadError,
    ResourceLimitError,
    TruncatedError,
)
from archivey.internal.streams.child_exit import (
    describe_exit,
    is_crash,
    is_system_kill,
)
from archivey.internal.streams.rapidgzip_worker import (
    ERR,
    FRAME,
    OK,
    OPEN,
    OPEN_PATH,
    OPEN_STREAM,
    READ,
    RESUME,
    SEEK,
    SRC_DATA,
    SRC_FAIL,
    SRC_READ,
    SRC_SEEK,
    SRC_TELL,
    SRC_VALUE,
    read_exact,
)
from archivey.internal.streams.streamtools import ReadOnlyIOStream

_WORKER = Path(__file__).with_name("rapidgzip_worker.py")

# The least a READ asks for once reads are sequential (see ``read``). Measured over a
# tar.gz, whose reader reads in small pieces, a round trip per piece was the cost.
_MIN_AHEAD = 64 << 10

# The most decoded bytes one READ round trip carries. A larger request is split, which
# bounds the child's buffer; measured, 64 KiB and 1 MiB round trips read a large stream
# equally fast, and 4 MiB ones more slowly.
_CHUNK = 1 << 20

# What rapidgzip 0.16 writes to stderr as it aborts on a stream that ends early.
_TRUNCATION_ABORT = b"The bit buffer should not contain more data than have been read"

# The exception types the child can report that are re-raised here as themselves, so
# the codec's rapidgzip translator reads them as it would read them in-process.
_REPORTED_TYPES: dict[str, type[Exception]] = {
    "ValueError": ValueError,
    "RuntimeError": RuntimeError,
    "EOFError": EOFError,
    "MemoryError": MemoryError,
    "OverflowError": OverflowError,
    "IndexError": IndexError,
    "TypeError": TypeError,
    "UnsupportedOperation": io.UnsupportedOperation,
    # The child could not import rapidgzip: reported as a start failure.
    "ImportError": ImportError,
    "ModuleNotFoundError": ImportError,
}


class RapidgzipChildStartError(RuntimeError):
    """No working rapidgzip child process could be started.

    The spawn failed (a sandbox that refuses ``fork``/``exec``, a process cap), or the
    child could not import rapidgzip. The codec layer reports it as
    ``ResourceLimitError``.
    """


class RapidgzipChildReportedError(Exception):
    """The child reported an exception of a type not in ``_REPORTED_TYPES``.

    No translator maps it, so it propagates: an unknown exception is a bug or an
    environment fault to map on purpose, not a verdict on the data.
    """


def rapidgzip_child_available() -> bool:
    """Whether a Python child process can be started to run the worker script.

    A frozen application (PyInstaller and the like) has no Python interpreter at
    ``sys.executable``, an embedded interpreter may not know its own path, and a
    zip-imported archivey has no worker file on disk.
    """
    return (
        not getattr(sys, "frozen", False) and bool(sys.executable) and _WORKER.is_file()
    )


_FROM_CHILD = "_archivey_reported_by_rapidgzip_child"


def reported_by_child(exc: BaseException) -> bool:
    """Whether ``exc`` is an exception rapidgzip raised in the child.

    An exception from the caller's own source is raised here as itself and is never
    marked, so a translator can treat every marked ``RuntimeError`` as rapidgzip's.
    """
    return getattr(exc, _FROM_CHILD, False) is True


def _reported_error(payload: bytes) -> Exception:
    """Rebuild an exception the child reported (see ``_error_payload`` in the worker)."""
    name, _, rest = payload.decode("utf-8", "replace").partition("\n")
    errno, _, message = rest.partition("\n")
    known = _REPORTED_TYPES.get(name)
    if known is not None:
        exc = known(message)
        setattr(exc, _FROM_CHILD, True)
        return exc
    cls = getattr(builtins, name, None)
    if isinstance(cls, type) and issubclass(cls, OSError):
        # A path the child could not open. ``OSError(errno, strerror)`` would pick the
        # subclass from the errno, but the message already carries it.
        exc = cls(message)
        if errno.lstrip("-").isdigit():
            exc.errno = int(errno)
        return exc
    return RapidgzipChildReportedError(f"{name}: {message}")


def _reap(
    proc: subprocess.Popen[bytes], stderr: IO[bytes], *, kill: bool = False
) -> None:
    """End the child and wait for it. Never raises."""
    if kill:
        try:
            proc.kill()
        except OSError:
            pass
    # Both pipes before the wait: a child blocked writing to a pipe nobody reads gets
    # EPIPE and exits, where it would otherwise never see stdin close.
    for pipe in (proc.stdin, proc.stdout):
        try:
            if pipe is not None:
                pipe.close()
        except OSError:
            pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    try:
        stderr.close()
    except OSError:
        pass


# How much of the child's stderr is scanned to classify its death. The abort message
# can sit anywhere in it: the child may have written warnings before it, and a dying
# process adds more after it (with faulthandler on, ``PYTHONFAULTHANDLER`` or
# ``-X faulthandler``, Python dumps every thread's stack, and from 3.14 the C stack
# too). So the scan reads the file from the start, a line at a time, rather than a
# window at either end; the cap only bounds the time a runaway writer can cost.
_STDERR_SCAN_LIMIT = 64 << 20
_STDERR_LINE_LIMIT = 64 << 10


def _scan_stderr(stderr: IO[bytes]) -> tuple[bool, str]:
    """Whether the child's stderr holds rapidgzip's truncation abort, and the text of
    its C++ ``what():`` line (``": <text>"``, or an empty string when there is none)."""
    truncated, reason = False, ""
    try:
        stderr.seek(0)
        scanned = 0
        while scanned < _STDERR_SCAN_LIMIT:
            line = stderr.readline(_STDERR_LINE_LIMIT)
            if not line:
                break
            scanned += len(line)
            truncated = truncated or _TRUNCATION_ABORT in line
            stripped = line.strip()
            if not reason and stripped.startswith(b"what():"):
                text = stripped[len(b"what():") :].strip().decode("utf-8", "replace")
                reason = f": {text[:200]}"
    except (OSError, ValueError):
        pass
    return truncated, reason


class RapidgzipChildStream(ReadOnlyIOStream):
    """A seekable stream of rapidgzip's decoded output, decoded in a child process.

    One child per instance. After the child dies, every later call raises the same
    error again; :meth:`close` still reaps it, and a garbage-collected instance is
    reaped by its finalizer. ``tell`` needs no round trip: the position is kept here.

    ``label`` names the codec in error messages (``gzip``, ``zlib``, ``deflate``).
    """

    def __init__(
        self, source: str | os.PathLike[str] | BinaryIO, *, label: str
    ) -> None:
        # Everything close() reads is assigned before anything that can raise.
        self._label = label
        self._proc: subprocess.Popen[bytes] | None = None
        self._stderr: IO[bytes] | None = None
        self._finalizer: weakref.finalize | None = None
        # (type, message) of what every call raises once the child is gone.
        self._death: tuple[type[Exception], str] | None = None
        # The caller's position; None after an error, when only the child knows it.
        self._pos: int | None = 0
        # Decoded bytes the child sent ahead of the caller's reads; the caller is at
        # ``_buffer_at``. The child's own position is past the end of the buffer.
        self._buffer = b""
        self._buffer_at = 0
        self._ahead = _MIN_AHEAD
        self._sequential = False
        # An exception from the caller's source, raised when the current call ends.
        self._parked: Exception | None = None
        # The first exception from the caller's source. The child was told its input
        # ended there, so a later death may be that, and is not a verdict on the data.
        self._source_fault: Exception | None = None
        self._source: BinaryIO | None = None
        if isinstance(source, (str, os.PathLike)):
            open_kind, open_payload = OPEN_PATH, os.fsencode(os.fspath(source))
        else:
            self._source = source
            open_kind, open_payload = OPEN_STREAM, b""
        super().__init__()
        if not sys.executable:
            # ``Popen([None, ...])`` raises ``TypeError``, not ``OSError``.
            raise RapidgzipChildStartError(
                "cannot start the rapidgzip decoder process: sys.executable is not set"
            )
        try:
            stderr = tempfile.TemporaryFile()
        except OSError as exc:
            raise RapidgzipChildStartError(
                f"cannot start the rapidgzip decoder process: {exc}"
            ) from exc
        self._stderr = stderr
        try:
            self._proc = subprocess.Popen(
                # -P: the worker's own directory is not put on sys.path, so its
                # sibling modules (``codecs.py``) cannot shadow the standard library.
                [sys.executable, "-P", str(_WORKER)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=stderr,
            )
        except OSError as exc:
            stderr.close()
            raise RapidgzipChildStartError(
                f"cannot start the rapidgzip decoder process: {exc}"
            ) from exc
        self._finalizer = weakref.finalize(self, _reap, self._proc, stderr)
        try:
            self._call(OPEN, open_kind, open_payload)
        except ImportError as exc:
            self.close()
            raise RapidgzipChildStartError(
                f"the rapidgzip decoder process cannot import rapidgzip: {exc}"
            ) from exc
        except BaseException:
            self.close()
            raise

    # --- the channel ----------------------------------------------------------------

    def _write(self, tag: int, arg: int = 0, payload: bytes = b"") -> bool:
        proc = self._proc
        assert proc is not None and proc.stdin is not None
        try:
            proc.stdin.write(FRAME.pack(tag, arg, len(payload)))
            if payload:
                proc.stdin.write(payload)
            proc.stdin.flush()
        except (OSError, ValueError):
            return False
        return True

    def _read_frame(self) -> tuple[int, int, bytes] | None:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        try:
            header = read_exact(proc.stdout, FRAME.size)
            if header is None:
                return None
            tag, arg, size = FRAME.unpack(header)
            payload = read_exact(proc.stdout, size) if size else b""
        except (OSError, ValueError):
            return None
        if payload is None:
            return None
        return tag, arg, payload

    def _answer_source(self, tag: int, arg: int, payload: bytes) -> None:
        """Serve one read, seek or tell of the caller's source for the child.

        An ``Exception`` from the source is parked and raised when the current call
        ends; the child is told the read failed, and treats it as the end of input.
        Anything else (``KeyboardInterrupt``) propagates at once.
        """
        source = self._source
        if source is None or self._parked is not None:
            self._write(SRC_FAIL)
            return
        try:
            if tag == SRC_READ:
                data = source.read(arg)
                if data is None:
                    data = b""
                if len(data) > arg:
                    raise ValueError(
                        f"source read({arg}) returned {len(data)} bytes; the excess is "
                        "already consumed and cannot be delivered"
                    )
                reply: tuple[int, int, bytes] = (SRC_DATA, 0, bytes(data))
            elif tag == SRC_SEEK:
                whence = payload[0] if payload else io.SEEK_SET
                reply = (SRC_VALUE, source.seek(arg, whence), b"")
            elif tag == SRC_TELL:
                reply = (SRC_VALUE, source.tell(), b"")
            else:
                raise ValueError(f"unknown request {tag} from the rapidgzip process")
        except Exception as exc:  # noqa: BLE001 - parked, then raised to the caller
            self._parked = exc
            if self._source_fault is None:
                self._source_fault = exc
            self._write(SRC_FAIL)
            return
        self._write(*reply)

    def _exchange(
        self, tag: int, arg: int, payload: bytes
    ) -> tuple[int, int, bytes] | None:
        """Send one request and serve the child until it replies. None: it died."""
        if not self._write(tag, arg, payload):
            return None
        while True:
            frame = self._read_frame()
            if frame is None or frame[0] in (OK, ERR):
                return frame
            self._answer_source(*frame)

    def _call(
        self, tag: int, arg: int = 0, payload: bytes = b"", *, keep_parked: bool = False
    ) -> tuple[int, bytes]:
        self._raise_if_unusable()
        try:
            frame = self._exchange(tag, arg, payload)
        except BaseException:
            # Interrupted part-way through (``KeyboardInterrupt``, a signal-driven
            # timeout): the rest of the exchange is still in the pipes, and nothing
            # can tell where it ends. The child cannot be used again.
            self._abandon()
            raise
        parked = None
        if not keep_parked:
            parked, self._parked = self._parked, None
        if frame is None:
            death = self._child_died(caused_by_source=self._source_fault)
            if parked is not None:
                raise parked
            raise death
        if parked is not None:
            raise parked
        reply, value, data = frame
        if reply == ERR:
            raise _reported_error(data)
        return value, data

    def _raise_if_unusable(self) -> None:
        if self._death is not None:
            cls, message = self._death
            raise cls(message)
        if self._proc is None:
            raise ValueError("I/O operation on closed file.")

    def _abandon(self) -> None:
        self._death = (
            ArchiveyUsageError,
            "the rapidgzip decoder process was interrupted in the middle of a "
            "request, and this stream cannot continue",
        )
        self._stop(kill=True)

    def _stop(self, *, kill: bool = False) -> None:
        # getattr: close() also runs on an instance whose __init__ never started.
        finalizer = getattr(self, "_finalizer", None)
        proc = getattr(self, "_proc", None)
        stderr = getattr(self, "_stderr", None)
        self._finalizer = self._proc = None
        if finalizer is None or proc is None or stderr is None:
            return
        if finalizer.detach() is not None:
            _reap(proc, stderr, kill=kill)

    def _child_died(self, *, caused_by_source: Exception | None) -> Exception:
        """Reap a child that has gone away; record and return the error that reports it."""
        proc = self._proc
        assert proc is not None and self._stderr is not None
        try:
            returncode = proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            returncode = None  # it closed its pipes and did not exit; ended below
        truncated, reason = _scan_stderr(self._stderr)
        self._stop(kill=returncode is None)
        how = describe_exit(returncode)
        label = self._label
        cls: type[Exception]
        if caused_by_source is not None:
            cls = ReadError
            message = (
                f"the rapidgzip decoder process for this {label} stream ended ({how}) "
                f"after a read from the source failed: {caused_by_source!r}"
            )
        elif is_crash(returncode) and truncated:
            cls = TruncatedError
            message = (
                f"{label} stream is truncated: the rapidgzip decoder process aborted "
                f"on it ({how}{reason})"
            )
        elif is_crash(returncode):
            cls = CorruptionError
            message = (
                f"Error reading {label} stream: the rapidgzip decoder process crashed "
                f"on it ({how}{reason})"
            )
        elif is_system_kill(returncode):
            cls = ResourceLimitError
            message = (
                f"the rapidgzip decoder process for this {label} stream was killed "
                f"({how}). SIGKILL comes from outside the decoder, most often the "
                "system's out-of-memory killer, so the data may be valid; read it "
                "again with more memory available."
            )
        else:
            cls = ReadError
            message = (
                f"the rapidgzip decoder process for this {label} stream ended "
                f"unexpectedly ({how}). The decoder did not crash, so the data may "
                "be valid; try reading it again."
            )
        self._death = (cls, message)
        return cls(message)

    # --- the stream -----------------------------------------------------------------

    def _drop_buffer(self) -> None:
        self._buffer = b""
        self._buffer_at = 0
        self._ahead = _MIN_AHEAD

    def _fetch(self, size: int) -> bytes:
        """One READ round trip. On an error the position is asked of the child next."""
        try:
            _, data = self._call(READ, size)
        except BaseException:
            self._drop_buffer()
            self._pos = None
            raise
        return data

    def read(self, n: int | None = -1, /) -> bytes:
        # Before the buffer: a closed or dead stream raises even with read-ahead left.
        self._raise_if_unusable()
        if n == 0:
            return b""
        if self._pos is None:
            self.tell()
        want = -1 if n is None or n < 0 else n
        parts: list[bytes] = []
        if self._buffer_at < len(self._buffer):
            end = len(self._buffer) if want < 0 else self._buffer_at + want
            part = self._buffer[self._buffer_at : end]
            self._buffer_at += len(part)
            parts.append(part)
            if want > 0:
                want -= len(part)
        if want:
            # The buffer is used up; what the child sends next starts where it ended.
            self._buffer, self._buffer_at = b"", 0
        while want:
            if want < 0:
                data = self._fetch(_CHUNK)
            elif self._sequential:
                # A read that follows a read: ask for more than was asked, so a caller
                # that reads in small pieces does not pay a round trip for each. The
                # amount doubles up to _CHUNK while the reads stay sequential.
                data = self._fetch(min(max(want, self._ahead), _CHUNK))
                self._ahead = min(self._ahead * 2, _CHUNK)
            else:
                data = self._fetch(min(want, _CHUNK))
            if not data:
                break
            if 0 < want < len(data):
                self._buffer, self._buffer_at = data, want
                data = data[:want]
            parts.append(data)
            if want > 0:
                want -= len(data)
        self._sequential = True
        result = b"".join(parts)
        assert self._pos is not None
        self._pos += len(result)
        return result

    def readall(self) -> bytes:
        return self.read(-1)

    def seekable(self) -> bool:
        return True

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        self._raise_if_unusable()
        pos = self._pos
        if pos is not None and whence in (io.SEEK_SET, io.SEEK_CUR):
            target = offset if whence == io.SEEK_SET else pos + offset
            # A target inside the read-ahead buffer needs no round trip.
            buffer_start = pos - self._buffer_at
            if buffer_start <= target <= buffer_start + len(self._buffer):
                self._buffer_at = target - buffer_start
                self._pos = target
                return target
            # The child is past the buffer, so a relative seek is made absolute here.
            offset, whence = target, io.SEEK_SET
        self._drop_buffer()
        self._sequential = False
        try:
            position, _ = self._call(SEEK, offset, bytes([whence]))
        except BaseException:
            self._pos = None
            raise
        self._pos = position
        return position

    def tell(self) -> int:
        self._raise_if_unusable()
        if self._pos is None:
            self._drop_buffer()
            self._pos, _ = self._call(SEEK, 0, bytes([io.SEEK_CUR]))
        return self._pos

    def nearest_resume_offset(self, target: int) -> int | None:
        """Decompressed offset rapidgzip would restart from to reach ``target``.

        The largest point of the index built so far (``available_block_offsets``) at
        or before ``target``; ``None`` when there is none or the child cannot answer.
        A partial index reports a resume point further back, which errs toward
        telling the caller. A child that dies during the probe raises its error, as
        the next read would. A parked source error is left for the next read or seek.
        """
        if self._death is not None or self._proc is None:
            return None
        try:
            value, _ = self._call(RESUME, target, keep_parked=True)
        except (ArchiveyError, OSError, MemoryError):
            raise
        except Exception:  # noqa: BLE001 - a diagnostic probe never breaks a read
            return None
        return value if value >= 0 else None

    def close(self) -> None:
        if self.closed:
            return
        self._stop()
        self._source = None
        self._drop_buffer()
        super().close()
