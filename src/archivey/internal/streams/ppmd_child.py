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

import signal
import struct
import subprocess
import sys
from pathlib import Path
from typing import IO

from archivey.exceptions import ResourceLimitError

_OPEN = struct.Struct("<BBIB")
_REQUEST = struct.Struct("<iI")
_REPLY = struct.Struct("<BBBI")

_WORKER = Path(__file__).with_name("ppmd_worker.py")

# How a dead child's exit reads when the system killed it: the kernel's out-of-memory
# killer, or an operator or supervisor, sends SIGKILL. Windows has no such signal.
_SIGKILL: int | None = getattr(signal, "SIGKILL", None)


class _PpmdError(ValueError):
    """Stand-in for ``pyppmd.PpmdError`` reported by the child.

    The cffi backend of pyppmd raises ``PpmdError`` from ``decode`` on corrupt data;
    ``PpmdCodec.translate`` maps both it and ``ValueError`` to ``CorruptionError``.
    """


# Exception types the child may report, by name, and the type the parent re-raises for
# each, chosen so that ``PpmdCodec.translate`` treats it as it would the same exception
# raised in-process. Types ``translate`` does not map (``MemoryError``,
# ``OverflowError``) come back as themselves and propagate as they would in-process.
# Any other name comes back as ``PpmdChildReportedError``, which is deliberately left
# unmapped (not a corruption verdict) so that it propagates.
_KNOWN_ERRORS: dict[str, type[Exception]] = {
    "ValueError": ValueError,
    "EOFError": EOFError,
    "MemoryError": MemoryError,
    "OverflowError": OverflowError,
    "SystemError": SystemError,
    "PpmdError": _PpmdError,
}


class PpmdChildError(RuntimeError):
    """The PPMd child process died while decoding.

    ``PpmdCodec.translate`` maps it to ``CorruptionError``: pyppmd crashes on data it
    cannot decode safely. The message carries the child's exit status or signal, since
    a crash on the data (SIGSEGV, SIGABRT) is not the only way a child can die. A
    child killed by SIGKILL is reported as ``ResourceLimitError`` instead (see
    :meth:`PpmdChildDecoder.decode`).
    """

    def __init__(self, message: str, returncode: int | None = None) -> None:
        super().__init__(message)
        self.returncode = returncode


class PpmdChildStartError(RuntimeError):
    """No working PPMd child process could be started.

    The spawn failed (a sandbox that refuses ``fork``/``exec``, a process cap, a
    ``sys.executable`` that is not Python), or the child failed before it could import
    pyppmd. ``PpmdDecoder`` turns it into the same ``ResourceLimitError`` it raises
    when no child can be started at all.
    """


class PpmdChildAllocationError(RuntimeError):
    """The child started, then died or raised ``MemoryError`` constructing the decoder.

    The constructor allocates the member's declared ``mem_size``, and pyppmd aborts
    the process rather than raising when that allocation is refused (a container
    memory limit, ``RLIMIT_AS``). Decoding in-process would allocate the same amount
    and abort the caller, so ``PpmdDecoder`` refuses the member with this message and
    does not suggest it.
    """


class PpmdChildReportedError(RuntimeError):
    """The child reported an exception whose type is not in ``_KNOWN_ERRORS``.

    Not mapped by ``PpmdCodec.translate``, so it propagates: an unknown exception is
    a bug or an environment fault to map on purpose, not a verdict on the data.
    """


def child_decoding_available() -> bool:
    """Whether a Python child process can be started to run the worker script.

    A frozen application (PyInstaller and the like) has no Python interpreter at
    ``sys.executable``, an embedded interpreter may not know its own path
    (``sys.executable`` is ``None`` or empty), and a zip-imported archivey has no
    worker file on disk.
    """
    return (
        not getattr(sys, "frozen", False) and bool(sys.executable) and _WORKER.is_file()
    )


def _describe_exit(returncode: int | None) -> str:
    """How a child ended, for an error message: a signal name or an exit status."""
    if returncode is None:
        return "exit status unknown"
    if returncode < 0:
        try:
            return f"killed by {signal.Signals(-returncode).name}"
        except ValueError:
            return f"killed by signal {-returncode}"
    if returncode > 255:  # a Windows NTSTATUS, such as 0xC0000005 (access violation)
        return f"exit status {returncode:#x}"
    return f"exit status {returncode}"


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
    dies, every later ``decode`` raises the same error again, and :meth:`close` still
    reaps it. ``eof`` and ``needs_input`` are the values the child reported with its
    last reply.
    """

    def __init__(
        self, *, variant: int, order: int, mem_size: int, restore_method: int = 0
    ) -> None:
        self.eof = False
        self.needs_input = True
        self._dead = False
        # Set when a reply was cut short (see ``_receive``).
        self._interrupted = False
        # Set when the child died: what every later ``decode`` raises again.
        self._death: PpmdChildError | None = None
        # Assigned before the spawn, so ``close`` (and ``__del__``) work on an object
        # whose ``Popen`` raised.
        self._proc: subprocess.Popen[bytes] | None = None
        if not sys.executable:
            # ``Popen([None, ...])`` raises ``TypeError``, not ``OSError``.
            raise PpmdChildStartError(
                "cannot start the PPMd decoder process: sys.executable is not set"
            )
        try:
            self._proc = subprocess.Popen(
                # -P: the worker's own directory is not put on sys.path, so its
                # sibling modules (``codecs.py``) cannot shadow the standard library.
                [sys.executable, "-P", str(_WORKER)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise PpmdChildStartError(
                f"cannot start the PPMd decoder process: {exc}"
            ) from exc
        # The child replies once after ``import pyppmd`` and once after constructing
        # the decoder (see ``ppmd_worker``); a death between the two is the
        # constructor's allocation of ``mem_size``.
        try:
            self._send(_OPEN.pack(variant, order, mem_size, restore_method))
            self._receive()
        except (PpmdChildError, PpmdChildReportedError) as exc:
            self.close()
            raise PpmdChildStartError(
                f"the PPMd decoder process failed to start: {exc}"
            ) from exc
        except BaseException:
            self.close()
            raise
        try:
            self._receive()
        except (PpmdChildError, MemoryError) as exc:
            self.close()
            raise PpmdChildAllocationError(
                f"the PPMd decoder process could not allocate this member's model "
                f"(mem_size={mem_size} bytes): {str(exc) or type(exc).__name__}. A memory "
                "limit on this process (a container limit, RLIMIT_AS) is the likely "
                "cause, and decoding in-process would fail the same way and take this "
                "process down with it. Run with more memory, or lower "
                "DecoderLimits.max_decoder_memory to refuse such members up front."
            ) from exc
        except BaseException:
            self.close()
            raise

    def _child_died(self) -> PpmdChildError:
        """Reap a child that has gone away; return the error that reports it."""
        proc = self._proc
        self.close()
        returncode = proc.returncode if proc is not None else None
        self._death = PpmdChildError(
            f"PPMd decoder process exited unexpectedly ({_describe_exit(returncode)})",
            returncode,
        )
        return self._death

    def _send(self, *parts: bytes) -> None:
        if self._death is not None:
            raise PpmdChildError(str(self._death), self._death.returncode)
        if self._interrupted:
            # Not ``PpmdChildError``: nothing is known about the data.
            raise RuntimeError(
                "PPMd decoder process is not running: a reply from it was "
                "interrupted, and this decoder cannot continue"
            )
        proc = self._proc
        if proc is None or self._dead:
            raise PpmdChildError("PPMd decoder process is not running")
        assert proc.stdin is not None
        try:
            for part in parts:
                proc.stdin.write(part)
            proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise self._child_died() from exc

    def _receive(self) -> bytes:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        try:
            status, eof, needs_input, size = _REPLY.unpack(
                _read_exact(proc.stdout, _REPLY.size)
            )
            payload = _read_exact(proc.stdout, size)
        except PpmdChildError as exc:
            raise self._child_died() from exc
        except BaseException:
            # Interrupted part-way through a reply (``KeyboardInterrupt``, a
            # signal-driven timeout): the rest of it is still in the pipe, and the
            # next read would take its bytes for a reply header.
            self._interrupted = True
            raise
        self.eof = bool(eof)
        self.needs_input = bool(needs_input)
        if status == 0:
            return payload
        name, _, message = payload.decode("utf-8", "replace").partition("\n")
        known = _KNOWN_ERRORS.get(name)
        if known is None:
            raise PpmdChildReportedError(f"{name}: {message}")
        raise known(message)

    def decode(self, data: bytes | bytearray | memoryview, length: int) -> bytes:
        try:
            self._send(_REQUEST.pack(length, len(data)), bytes(data))
            return self._receive()
        except PpmdChildError as exc:
            if _SIGKILL is not None and exc.returncode == -_SIGKILL:
                # Not a crash on the data: the system's out-of-memory killer, an
                # operator or a supervisor ended the child.
                raise ResourceLimitError(
                    f"{exc}. SIGKILL comes from outside the decoder, most often the "
                    "system's out-of-memory killer, so the archive may be valid; "
                    "decode it with more memory available."
                ) from exc
            raise

    def close(self) -> None:
        """End the child and wait for it. Idempotent; never raises."""
        proc, self._proc = getattr(self, "_proc", None), None
        if proc is None:
            return
        self._dead = True
        # Both pipes before the wait: a child blocked writing a reply nobody will read
        # gets EPIPE and exits, where it would otherwise never see stdin close.
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

    def __del__(self) -> None:
        self.close()
