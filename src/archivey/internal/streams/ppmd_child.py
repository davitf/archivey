"""Run a pyppmd decoder in a child process, so a native crash cannot kill the caller.

pyppmd 1.3.1 (and 1.2.0) segfaults when it is asked to decode after a corrupt stream
has already hit its end: the model returns its end symbol early, ``eof`` goes up, and
the next ``decode`` starts a new worker on a model that has finished. From Python that
state cannot be told apart from a valid stream that is waiting for more input, because
``eof`` also rises (and never clears) on a valid stream whose compressed bytes happen to
end in zeros at a chunk boundary. ``PpmdDecoder`` avoids the question for members small
enough to hand pyppmd whole; larger ones decode through :class:`PpmdChildDecoder`.

:class:`PpmdChildDecoder` has the same surface as ``pyppmd.Ppmd7Decoder`` /
``Ppmd8Decoder`` as ``PpmdDecoder`` uses it (``decode``, ``eof``, ``needs_input``), so
all the decoding logic stays in ``PpmdDecoder``; the child only owns the native object.
The child runs ``ppmd_worker.py`` as a script, which imports nothing from ``archivey``.
"""

from __future__ import annotations

import struct
import subprocess
import sys
from pathlib import Path
from typing import IO

_OPEN = struct.Struct("<BBIB")
_REQUEST = struct.Struct("<iI")
_REPLY = struct.Struct("<BBBI")

_WORKER = Path(__file__).with_name("ppmd_worker.py")

# Exception types the child may report that ``PpmdCodec.translate`` already maps; any
# other name comes back as ``PpmdChildError``.
_KNOWN_ERRORS: dict[str, type[Exception]] = {
    "ValueError": ValueError,
    "EOFError": EOFError,
    "MemoryError": MemoryError,
    "OverflowError": OverflowError,
    "SystemError": SystemError,
}


class PpmdChildError(RuntimeError):
    """The PPMd child process died, or reported an error it has no type for.

    ``PpmdCodec.translate`` maps it to ``CorruptionError``: the child dies only on
    data pyppmd cannot decode safely.
    """


def child_decoding_available() -> bool:
    """Whether a Python child process can be started to run the worker script.

    A frozen application (PyInstaller and the like) has no Python interpreter at
    ``sys.executable``, and a zip-imported archivey has no worker file on disk.
    """
    return not getattr(sys, "frozen", False) and _WORKER.is_file()


def _read_exact(stream: IO[bytes], size: int) -> bytes:
    parts: list[bytes] = []
    while size:
        chunk = stream.read(size)
        if not chunk:
            raise PpmdChildError("PPMd decoder process exited unexpectedly")
        parts.append(chunk)
        size -= len(chunk)
    return b"".join(parts)


class PpmdChildDecoder:
    """A ``pyppmd`` decoder living in a child process.

    One child per instance. ``decode`` blocks for the child's reply. After the child
    dies, every later ``decode`` raises :class:`PpmdChildError`, and :meth:`close`
    still reaps it. ``eof`` and ``needs_input`` are the values the child reported with
    its last reply.
    """

    def __init__(
        self, *, variant: int, order: int, mem_size: int, restore_method: int = 0
    ) -> None:
        self.eof = False
        self.needs_input = True
        self._dead = False
        self._proc: subprocess.Popen[bytes] | None = subprocess.Popen(
            # -P: the worker's own directory is not put on sys.path, so its sibling
            # modules (``codecs.py``) cannot shadow the standard library.
            [sys.executable, "-P", str(_WORKER)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        try:
            self._send(_OPEN.pack(variant, order, mem_size, restore_method))
            self._receive()
        except BaseException:
            self.close()
            raise

    def _send(self, *parts: bytes) -> None:
        proc = self._proc
        if proc is None or self._dead:
            raise PpmdChildError("PPMd decoder process is not running")
        assert proc.stdin is not None
        try:
            for part in parts:
                proc.stdin.write(part)
            proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            self._dead = True
            raise PpmdChildError("PPMd decoder process exited unexpectedly") from exc

    def _receive(self) -> bytes:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        try:
            status, eof, needs_input, size = _REPLY.unpack(
                _read_exact(proc.stdout, _REPLY.size)
            )
            payload = _read_exact(proc.stdout, size)
        except PpmdChildError:
            self._dead = True
            raise
        self.eof = bool(eof)
        self.needs_input = bool(needs_input)
        if status == 0:
            return payload
        name, _, message = payload.decode("utf-8", "replace").partition("\n")
        raise _KNOWN_ERRORS.get(name, PpmdChildError)(message)

    def decode(self, data: bytes | bytearray | memoryview, length: int) -> bytes:
        self._send(_REQUEST.pack(length, len(data)), bytes(data))
        return self._receive()

    def close(self) -> None:
        """End the child and wait for it. Idempotent; never raises."""
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        if proc.stdout is not None:
            proc.stdout.close()

    def __del__(self) -> None:
        self.close()
