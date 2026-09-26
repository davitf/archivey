"""How a decoder child process ended, read from its return code.

A native decoder that archivey runs in a child process (pyppmd, see ``ppmd_child``;
rapidgzip, see ``rapidgzip_child``) can crash on hostile input. Only a crash is a
verdict on the data. A child ended from outside (the out-of-memory killer, an
operator, a supervisor) says nothing about the data, and the caller must not be told
it does.
"""

from __future__ import annotations

import signal
import sys

# The system kills with SIGKILL: the kernel's out-of-memory killer, an operator or a
# supervisor. Windows has no such signal.
SIGKILL: int | None = getattr(signal, "SIGKILL", None)

# The deaths that read as a crash of the decoder itself: the POSIX signals a native
# fault raises, and the Windows NTSTATUS codes for the same faults.
_CRASH_SIGNALS = frozenset(
    -int(sig)
    for name in ("SIGSEGV", "SIGABRT", "SIGBUS", "SIGILL", "SIGFPE")
    if (sig := getattr(signal, name, None)) is not None
)
_CRASH_NTSTATUS = frozenset(
    {
        0xC0000005,  # access violation
        0xC000001D,  # illegal instruction
        0xC0000094,  # integer divide by zero
        0xC00000FD,  # stack overflow
        0xC0000409,  # stack buffer overrun / fail-fast (the C runtime's abort path)
    }
)
# The Microsoft C runtime's abort() exits with status 3 when it does not fail fast.
# A worker that archivey runs never exits with 3 by itself (a Python child exits 0,
# or 1 on an uncaught exception), so on Windows a 3 is the decoder's abort.
_WINDOWS_ABORT_STATUS = 3


def is_crash(returncode: int | None) -> bool:
    """Whether a child that ended with ``returncode`` crashed, rather than was ended.

    A crash is a native fault signal, or the Windows status for one. Every other end
    (SIGKILL, SIGTERM, a plain exit status) came from outside the decoder.
    """
    if returncode is None:
        return False
    if returncode in _CRASH_SIGNALS or returncode in _CRASH_NTSTATUS:
        return True
    return sys.platform == "win32" and returncode == _WINDOWS_ABORT_STATUS


def is_system_kill(returncode: int | None) -> bool:
    """Whether the child was ended by SIGKILL, most often the out-of-memory killer."""
    return SIGKILL is not None and returncode == -SIGKILL


def describe_exit(returncode: int | None) -> str:
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
