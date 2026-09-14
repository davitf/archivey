"""Per-test oracle for leaked OS resources.

A stream-layer ownership bug (close the wrapper, forget the subprocess) does not
raise and does not fail an assertion: the member reads as the right bytes, the
test goes green, and ``unrar`` stays alive holding its stdout pipe. This plugin
is the gate that makes that a red test.

What it detects, at *teardown* of each test:

- Child processes spawned during the test that are still alive (or zombie).
- Unclosed streams that own a private inner: ``SlicingStream(..., own_source=True)``
  and ``DelegatingStream(..., manual_inner_close=True)`` (and subclasses). Those
  two flags are the population that wraps a subprocess, a finalize-guarded
  accelerator, or some other inner the wrapper is responsible for reaping.
- Extra pipe/socket file descriptors on Linux (``/proc/self/fd``), reported as
  context on a process/stream leak. Not a standalone fail: ``os.pipe()`` test
  helpers and pytest-cov change when GC closes those fds. Regular files are
  ignored.

What it deliberately does not:

- Unclosed ``BytesIO`` / default ``DelegatingStream`` wrappers. They hold no OS
  resource, and tests construct them by the thousand.
- A leftover pipe fd with no leaked child and no unclosed owning stream.
  ``tests/test_stream_inputs.py``'s ``os_pipe_reader`` is the specimen.
- ``unrar`` / ``7z`` leaks under ``[core-only]``. Those binaries are absent, the
  tests that spawn them skip, and there is nothing to observe. The oracle still
  runs; it just has no subprocesses to catch. Isolated tests in
  ``test_leak_oracle.py`` spawn ``sys.executable`` so the gate is exercised in
  every config, including core-only.
- Interpreter-shutdown accelerator leftovers. That is still
  ``scripts/accel_leak_trace.py``: a manual diagnostic, not a per-test gate.

Cost: one ``Popen.__init__`` append, a flag check on two constructors, and two
small ``/proc`` snapshots. Disable with ``ARCHIVEY_LEAK_ORACLE=0``.

Owning streams and ``Popen`` objects are *pinned* until they are explicitly
closed (or until teardown). That is load-bearing: CPython refcounting would
otherwise run ``IOBase.__del__`` → ``close()`` when the test function returns,
reaping the child before this fixture sees it, and the leak would stay silent.
Pinning is why deleting ``own_source=True`` on the glob-mask RAR pipe fails a
test instead of looking fine.
"""

from __future__ import annotations

import os
import signal
import stat
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable, Iterator

import pytest

_ENV_FLAG = "ARCHIVEY_LEAK_ORACLE"

# Markers / env values that mean "do not install, do not check".
_OFF = frozenset({"0", "false", "no", "off"})


def _enabled() -> bool:
    raw = os.environ.get(_ENV_FLAG, "1").strip().lower()
    return raw not in _OFF


@dataclass
class _ProcRec:
    pid: int
    argv: str
    proc: subprocess.Popen[bytes]


@dataclass
class _StreamRec:
    kind: str
    cls: str
    stream: object


_lock = threading.Lock()
_pinned_procs: dict[int, _ProcRec] = {}
_pinned_streams: dict[int, _StreamRec] = {}
_installed = False
_orig_popen_init: Callable[..., None] | None = None
_orig_delegating_init: Callable[..., None] | None = None
_orig_delegating_close: Callable[..., None] | None = None
_orig_slice_init_from_source: Callable[..., None] | None = None
_orig_slice_close: Callable[..., None] | None = None


def _fmt_argv(args: object) -> str:
    if isinstance(args, bytes):
        return args.decode("utf-8", "replace")
    if isinstance(args, str):
        return args
    try:
        parts = []
        for raw in args:  # type: ignore[union-attr]
            if isinstance(raw, bytes):
                parts.append(raw.decode("utf-8", "replace"))
            else:
                parts.append(str(raw))
    except TypeError:
        return repr(args)
    if len(parts) > 6:
        parts = parts[:6] + ["..."]
    return " ".join(parts)


def _note_popen(proc: subprocess.Popen[bytes], args: object) -> None:
    pid = getattr(proc, "pid", None)
    if not isinstance(pid, int) or pid <= 0:
        return
    rec = _ProcRec(pid=pid, argv=_fmt_argv(args), proc=proc)
    with _lock:
        _pinned_procs[pid] = rec


def _unpin_stream(stream: object) -> None:
    with _lock:
        _pinned_streams.pop(id(stream), None)


def _pin_stream(stream: object, kind: str) -> None:
    rec = _StreamRec(kind=kind, cls=type(stream).__name__, stream=stream)
    with _lock:
        _pinned_streams[id(stream)] = rec


def _install_popen_hook() -> None:
    global _orig_popen_init
    if _orig_popen_init is not None:
        return
    _orig_popen_init = subprocess.Popen.__init__

    def _init(self: subprocess.Popen[bytes], *args: object, **kwargs: object) -> None:
        assert _orig_popen_init is not None
        _orig_popen_init(self, *args, **kwargs)
        argv = args[0] if args else kwargs.get("args")
        _note_popen(self, argv)

    subprocess.Popen.__init__ = _init  # type: ignore[method-assign]  # wrap, do not subclass


def _install_stream_hooks() -> None:
    global _orig_delegating_init, _orig_delegating_close
    global _orig_slice_init_from_source, _orig_slice_close

    from archivey.internal.streams.streamtools.base import DelegatingStream
    from archivey.internal.streams.streamtools.slice import SlicingStream

    if _orig_delegating_init is None:
        _orig_delegating_init = DelegatingStream.__init__
        _orig_delegating_close = DelegatingStream.close

        def _d_init(
            self: DelegatingStream,
            inner: BinaryIO,
            *,
            readinto_passthrough: bool = True,
            manual_inner_close: bool = False,
        ) -> None:
            assert _orig_delegating_init is not None
            _orig_delegating_init(
                self,
                inner,
                readinto_passthrough=readinto_passthrough,
                manual_inner_close=manual_inner_close,
            )
            if manual_inner_close:
                _pin_stream(self, "manual_inner_close")

        def _d_close(self: DelegatingStream) -> None:
            _unpin_stream(self)
            assert _orig_delegating_close is not None
            _orig_delegating_close(self)

        DelegatingStream.__init__ = _d_init  # type: ignore[method-assign]  # wrap production ctor
        DelegatingStream.close = _d_close  # type: ignore[method-assign]  # unpin on explicit close

    if _orig_slice_init_from_source is None:
        _orig_slice_init_from_source = SlicingStream._init_from_source
        _orig_slice_close = SlicingStream.close

        def _s_init(
            self: SlicingStream,
            stream: BinaryIO,
            start: int | None,
            length: int | None,
            *,
            io_guard: object,
            seek_before_read: bool,
            check_open: object,
            own_source: bool,
            source_size: int | None = None,
            probe_source_size: bool = True,
        ) -> None:
            assert _orig_slice_init_from_source is not None
            _orig_slice_init_from_source(
                self,
                stream,
                start,
                length,
                io_guard=io_guard,
                seek_before_read=seek_before_read,
                check_open=check_open,
                own_source=own_source,
                source_size=source_size,
                probe_source_size=probe_source_size,
            )
            if own_source:
                _pin_stream(self, "own_source")

        def _s_close(self: SlicingStream) -> None:
            _unpin_stream(self)
            assert _orig_slice_close is not None
            _orig_slice_close(self)

        SlicingStream._init_from_source = _s_init  # type: ignore[method-assign]  # wrap; SharedView uses this too
        SlicingStream.close = _s_close  # type: ignore[method-assign]  # unpin on explicit close


def _child_pids() -> set[int]:
    """Direct children of this process, Linux ``/proc`` only.

    A backstop for anything that did not go through ``subprocess.Popen``. Empty
    on macOS/Windows — the Popen hook is the portable path there.
    """
    pid = os.getpid()
    path = Path(f"/proc/{pid}/task/{pid}/children")
    try:
        text = path.read_text(encoding="ascii")
    except OSError:
        return set()
    return {int(x) for x in text.split() if x.isdigit()}


def _pipe_fds() -> dict[int, str]:
    """Open pipe/socket fds → ``readlink`` target. Linux only; else empty."""
    fd_dir = Path("/proc/self/fd")
    if not fd_dir.is_dir():
        return {}
    found: dict[int, str] = {}
    try:
        entries = list(fd_dir.iterdir())
    except OSError:
        return {}
    for entry in entries:
        try:
            fd = int(entry.name)
        except ValueError:
            continue
        if fd < 3:
            continue
        try:
            mode = os.fstat(fd).st_mode
        except OSError:
            continue
        if not (stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode)):
            continue
        try:
            target = os.readlink(entry)
        except OSError:
            target = "?"
        found[fd] = target
    return found


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _reap_pid(pid: int) -> None:
    """Best-effort: reap a zombie, else SIGTERM then SIGKILL.

    One leaked ``unrar`` must not sit around for the rest of the suite. Never
    ``pkill`` — only this pid.
    """
    try:
        waited, _ = os.waitpid(pid, os.WNOHANG)
    except (ChildProcessError, OSError):
        waited = 0
    if waited:
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        try:
            waited, _ = os.waitpid(pid, os.WNOHANG)
        except (ChildProcessError, OSError):
            return
        if waited:
            return
        time.sleep(0.02)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        return
    try:
        os.waitpid(pid, 0)
    except (ChildProcessError, OSError):
        return


def _close_quietly(stream: object) -> None:
    close = getattr(stream, "close", None)
    if close is None:
        return
    try:
        close()
    except Exception:  # noqa: BLE001 - teardown cleanup; the fail message is the report
        pass


def _leaked_procs() -> list[_ProcRec]:
    leaked: list[_ProcRec] = []
    with _lock:
        recs = list(_pinned_procs.values())
    for rec in recs:
        poll = rec.proc.poll()
        if poll is None and _pid_alive(rec.pid):
            leaked.append(rec)
    return leaked


def _leaked_streams() -> list[_StreamRec]:
    with _lock:
        return list(_pinned_streams.values())


def _reset_pins() -> None:
    with _lock:
        _pinned_procs.clear()
        _pinned_streams.clear()


def _cleanup_leaks(procs: list[_ProcRec], streams: list[_StreamRec]) -> None:
    for rec in streams:
        _close_quietly(rec.stream)
    for rec in procs:
        if rec.proc.poll() is None:
            _reap_pid(rec.pid)
    _reset_pins()


def _report(
    procs: list[_ProcRec],
    streams: list[_StreamRec],
    extra_fds: dict[int, str],
    extra_children: set[int],
) -> str:
    lines = ["test leaked OS resources:"]
    for rec in procs:
        lines.append(f"  child process still running: pid={rec.pid} argv={rec.argv!r}")
    for pid in sorted(extra_children):
        if any(rec.pid == pid for rec in procs):
            continue
        lines.append(
            f"  child process still running: pid={pid} (not spawned via subprocess.Popen)"
        )
    for rec in streams:
        lines.append(f"  unclosed {rec.kind} stream: {rec.cls}")
    for fd, target in sorted(extra_fds.items()):
        lines.append(f"  leaked pipe/socket fd {fd} -> {target}")
    lines.append(
        "These are pinned until explicit close so GC/__del__ cannot hide them. "
        "Close the owning wrapper (own_source=True / the stream that reaps the "
        "subprocess), or reap the child, in the test (or the code under test)."
    )
    return "\n".join(lines)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "allow_resource_leaks: skip the OS-resource leak oracle for this test",
    )
    if not _enabled():
        return
    global _installed
    if _installed:
        return
    _install_popen_hook()
    _install_stream_hooks()
    _installed = True


@pytest.fixture(autouse=True)
def _archivey_leak_oracle(request: pytest.FixtureRequest) -> Iterator[None]:
    """Snapshot children/pipe fds, run the test, fail if anything new is still live."""
    if not _enabled():
        yield
        return
    if request.node.get_closest_marker("allow_resource_leaks") is not None:
        yield
        return

    _reset_pins()
    fds_before = _pipe_fds()
    children_before = _child_pids()
    yield

    procs = _leaked_procs()
    streams = _leaked_streams()
    extra_fds = {
        fd: target for fd, target in _pipe_fds().items() if fd not in fds_before
    }
    extra_children = {pid for pid in _child_pids() - children_before if _pid_alive(pid)}
    # Pipe fds alone are not a fail: os.pipe() test helpers close them via GC
    # on a schedule coverage changes. They still annotate a process/stream leak.
    if not (procs or streams or extra_children):
        _reset_pins()
        return

    message = _report(procs, streams, extra_fds, extra_children)
    _cleanup_leaks(procs, streams)
    pytest.fail(message)
