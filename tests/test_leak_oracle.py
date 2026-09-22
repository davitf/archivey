"""The leak oracle fails a test that leaves a child or an owning stream open.

Each case runs in a nested pytest so this file's own autouse oracle is not the
thing under test. The nested process loads ``leak_oracle`` via ``-p`` and does
not load ``tests/conftest.py`` (the file lives in tmp_path).
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

_ISOLATED_HEADER = """\
from __future__ import annotations

import subprocess
import sys

"""


def _run_isolated(tmp_path: Path, body: str) -> subprocess.CompletedProcess[str]:
    path = tmp_path / "test_isolated_oracle_case.py"
    path.write_text(_ISOLATED_HEADER + body, encoding="utf-8")
    env = os.environ.copy()
    env["ARCHIVEY_LEAK_ORACLE"] = "1"
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO / "src"), str(REPO / "tests"), str(REPO), env.get("PYTHONPATH", "")]
    )
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(path),
            "--rootdir",
            str(REPO),
            "-p",
            "leak_oracle",
            "-p",
            "no:cov",
            "-o",
            "addopts=--timeout=20",
            "-q",
            "--tb=short",
        ],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )


def _output(proc: subprocess.CompletedProcess[str]) -> str:
    return proc.stdout + proc.stderr


def test_oracle_fails_on_live_child(tmp_path: Path) -> None:
    proc = _run_isolated(
        tmp_path,
        """
def test_leak_child() -> None:
    subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
    )
""",
    )
    assert proc.returncode != 0, _output(proc)
    text = _output(proc)
    assert "test leaked OS resources" in text
    assert "child process still running" in text


def test_oracle_passes_when_child_is_reaped(tmp_path: Path) -> None:
    proc = _run_isolated(
        tmp_path,
        """
def test_reaped_child() -> None:
    child = subprocess.Popen(
        [sys.executable, "-c", "pass"],
    )
    assert child.wait(timeout=10) == 0
""",
    )
    assert proc.returncode == 0, _output(proc)


def test_oracle_passes_when_child_exited_unwaited(tmp_path: Path) -> None:
    """A waited-but-unclosed stdout pipe is fd-alone, not a fail (F8).

    ``poll()`` reaps an exited child, so a zombie never appears in the leak
    list. Closing the ``Popen`` object's pipe is not required.
    """
    proc = _run_isolated(
        tmp_path,
        """
def test_exited_pipe_left_open() -> None:
    child = subprocess.Popen(
        [sys.executable, "-c", "pass"],
        stdout=subprocess.PIPE,
    )
    assert child.wait(timeout=10) == 0
""",
    )
    assert proc.returncode == 0, _output(proc)


def test_oracle_fails_on_non_owning_slice_over_owned_pipe(tmp_path: Path) -> None:
    """The #336 shape: close a non-owning ``SlicingStream``, leave the process alive."""
    proc = _run_isolated(
        tmp_path,
        """
from archivey.internal.streams.streamtools.base import DelegatingStream
from archivey.internal.streams.streamtools.slice import SlicingStream

class _OwnedPipe(DelegatingStream):
    def __init__(self, stdout, proc) -> None:
        super().__init__(stdout, readinto_passthrough=False, subclass_closes_inner=True)
        self._proc = proc

    def close(self) -> None:
        if self.closed:
            return
        self._inner.close()
        if self._proc.poll() is None:
            self._proc.kill()
            self._proc.wait()
        super().close()

def test_forget_owns_inner() -> None:
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys, time; sys.stdout.buffer.write(b'hello world'); "
            "sys.stdout.buffer.flush(); time.sleep(30)",
        ],
        stdout=subprocess.PIPE,
    )
    inner = _OwnedPipe(child.stdout, child)
    view = SlicingStream(inner, length=5, owns_inner=False)
    assert view.read() == b"hello"
    view.close()
""",
    )
    assert proc.returncode != 0, _output(proc)
    text = _output(proc)
    assert "test leaked OS resources" in text
    assert (
        "child process still running" in text
        or "unclosed subclass_closes_inner" in text
    )


def test_oracle_passes_when_slice_owns_the_pipe(tmp_path: Path) -> None:
    proc = _run_isolated(
        tmp_path,
        """
from archivey.internal.streams.streamtools.base import DelegatingStream
from archivey.internal.streams.streamtools.slice import SlicingStream

class _OwnedPipe(DelegatingStream):
    def __init__(self, stdout, proc) -> None:
        super().__init__(stdout, readinto_passthrough=False, subclass_closes_inner=True)
        self._proc = proc

    def close(self) -> None:
        if self.closed:
            return
        self._inner.close()
        if self._proc.poll() is None:
            self._proc.kill()
            self._proc.wait()
        super().close()

def test_owns_inner_reaps() -> None:
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys, time; sys.stdout.buffer.write(b'hello world'); "
            "sys.stdout.buffer.flush(); time.sleep(30)",
        ],
        stdout=subprocess.PIPE,
    )
    inner = _OwnedPipe(child.stdout, child)
    view = SlicingStream(inner, length=5, owns_inner=True)
    assert view.read() == b"hello"
    view.close()
""",
    )
    assert proc.returncode == 0, _output(proc)


def test_oracle_fails_on_unclosed_owning_slice(tmp_path: Path) -> None:
    proc = _run_isolated(
        tmp_path,
        """
import io
from archivey.internal.streams.streamtools.slice import SlicingStream

def test_unclosed_owns_inner() -> None:
    SlicingStream(io.BytesIO(b"hello"), length=5, owns_inner=True)
""",
    )
    assert proc.returncode != 0, _output(proc)
    text = _output(proc)
    assert "unclosed owns_inner stream: SlicingStream" in text


def test_oracle_passes_when_owning_slice_is_closed(tmp_path: Path) -> None:
    proc = _run_isolated(
        tmp_path,
        """
import io
from archivey.internal.streams.streamtools.slice import SlicingStream

def test_closed_owns_inner() -> None:
    SlicingStream(io.BytesIO(b"hello"), length=5, owns_inner=True).close()
""",
    )
    assert proc.returncode == 0, _output(proc)


def test_oracle_fails_on_unclosed_subclass_closes_inner(tmp_path: Path) -> None:
    proc = _run_isolated(
        tmp_path,
        """
import io
from archivey.internal.streams.streamtools.base import DelegatingStream

def test_unclosed_manual() -> None:
    DelegatingStream(io.BytesIO(b"x"), subclass_closes_inner=True)
""",
    )
    assert proc.returncode != 0, _output(proc)
    assert "unclosed subclass_closes_inner stream" in _output(proc)


def test_oracle_passes_when_subclass_closes_inner_is_closed(tmp_path: Path) -> None:
    proc = _run_isolated(
        tmp_path,
        """
import io
from archivey.internal.streams.streamtools.base import DelegatingStream

def test_closed_manual() -> None:
    DelegatingStream(io.BytesIO(b"x"), subclass_closes_inner=True).close()
""",
    )
    assert proc.returncode == 0, _output(proc)


def test_oracle_fails_on_unclosed_subclass_closes_inner_class_flag(
    tmp_path: Path,
) -> None:
    """Pin via ``_SUBCLASS_CLOSES_INNER`` even when the kwarg is omitted."""
    proc = _run_isolated(
        tmp_path,
        """
import io
from archivey.internal.streams.streamtools.base import DelegatingStream

class _Manual(DelegatingStream):
    _SUBCLASS_CLOSES_INNER = True

def test_unclosed_class_flag() -> None:
    _Manual(io.BytesIO(b"x"))
""",
    )
    assert proc.returncode != 0, _output(proc)
    assert "unclosed subclass_closes_inner stream" in _output(proc)


def test_oracle_passes_when_subclass_closes_inner_class_flag_is_closed(
    tmp_path: Path,
) -> None:
    proc = _run_isolated(
        tmp_path,
        """
import io
from archivey.internal.streams.streamtools.base import DelegatingStream

class _Manual(DelegatingStream):
    _SUBCLASS_CLOSES_INNER = True

def test_closed_class_flag() -> None:
    _Manual(io.BytesIO(b"x")).close()
""",
    )
    assert proc.returncode == 0, _output(proc)


def test_cleanup_reaps_via_popen() -> None:
    """Teardown reaps with ``Popen.terminate``, not POSIX ``waitpid``/``WNOHANG``.

    Windows has no ``os.WNOHANG``; using it hid the leak report on CI
    (``windows-latest / py3.11|3.14 / all``).
    """
    import leak_oracle as lo

    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
    )
    try:
        rec = lo._ProcRec(pid=child.pid, argv="sleep", proc=child)
        lo._cleanup_leaks([rec], [])
        assert child.poll() is not None
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def test_reap_pid_is_noop_without_wnohang(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_reap_pid`` is the Linux /proc backstop; skip it when WNOHANG is absent."""
    import leak_oracle as lo

    class _OsWithoutWnohang:
        name = "nt"

        def waitpid(self, pid: int, flags: int) -> tuple[int, int]:
            raise AssertionError("waitpid must not run without WNOHANG")

        def kill(self, pid: int, sig: int) -> None:
            raise AssertionError("kill must not run without WNOHANG")

    monkeypatch.setattr(lo, "os", _OsWithoutWnohang())
    lo._reap_pid(12345)


def test_allow_resource_leaks_reaps_without_failing(tmp_path: Path) -> None:
    """The marker skips the fail, not the reap (F1)."""
    pidfile = tmp_path / "leaked.pid"
    proc = _run_isolated(
        tmp_path,
        f"""
import os
from pathlib import Path

import pytest

_PIDFILE = Path({str(pidfile)!r})

@pytest.mark.allow_resource_leaks
def test_marked_leak() -> None:
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
    )
    _PIDFILE.write_text(str(child.pid), encoding="utf-8")

def test_following_sees_child_reaped() -> None:
    pid = int(_PIDFILE.read_text(encoding="utf-8"))
    try:
        os.kill(pid, 0)
        alive = True
    except OSError:
        alive = False
    assert not alive, f"pid {{pid}} still alive after marked test"
""",
    )
    assert proc.returncode == 0, _output(proc)


def test_pid_alive_does_not_terminate_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``os.kill(pid, 0)`` is TerminateProcess on Windows, not a liveness probe."""
    import leak_oracle as lo

    def _kill(pid: int, sig: int) -> None:
        raise AssertionError(f"os.kill({pid}, {sig}) must not run on Windows")

    monkeypatch.setattr(lo.os, "name", "nt")
    monkeypatch.setattr(lo.os, "kill", _kill)
    assert lo._pid_alive(1) is True


def test_an_unpin_inside_a_locked_section_does_not_deadlock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A GC finalizer can reenter the oracle's lock on the thread already holding it.

    An unclosed ``DelegatingStream`` is closed by ``io.IOBase.__del__``, and the
    oracle's ``close`` wrapper unpins. A collection can fire inside any allocation,
    including the one a locked section makes when it snapshots a pinned map, so the
    unpin can land on a thread that is already inside ``_lock`` and cannot leave until
    it returns. With a non-reentrant lock that is a permanent self-deadlock, which is
    what a macOS CI job hit at teardown.

    Run in a worker thread, against a stand-in lock of the same type, so a regression
    fails this test instead of hanging the session: the thread stays blocked forever on
    a lock nothing else uses, and the real one is never held.
    """
    import leak_oracle as lo

    try:
        stand_in = type(lo._lock)()
    except TypeError:
        # ``type(threading.Lock())`` is ``_thread.lock``, which cannot be instantiated;
        # only the reentrant one round-trips. Build the non-reentrant kind directly so
        # a regression reaches the deadlock this test is about rather than dying here.
        stand_in = threading.Lock()
    monkeypatch.setattr(lo, "_lock", stand_in)
    done = threading.Event()

    def _reenter() -> None:
        with lo._lock:
            lo._unpin_stream(object())
        done.set()

    worker = threading.Thread(target=_reenter, daemon=True)
    worker.start()
    assert done.wait(timeout=10), (
        "an unpin from inside a locked section never returned: the oracle's lock is "
        "not reentrant"
    )


def test_a_snapshot_retries_when_a_finalizer_mutates_mid_iteration() -> None:
    """The other half of the same hazard: the reentrant unpin lands during the copy.

    ``dict`` raises ``RuntimeError`` rather than returning a torn list, so the retry is
    what keeps a teardown from failing on a stream that was genuinely closed.
    """
    import leak_oracle as lo

    class _MutatesOnce(dict):  # type: ignore[type-arg]  # values() is all _snapshot uses
        def __init__(self) -> None:
            super().__init__({1: "pinned"})
            self.calls = 0

        def values(self):  # type: ignore[no-untyped-def]  # returns dict's own view
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("dictionary changed size during iteration")
            return super().values()

    pinned = _MutatesOnce()
    assert lo._snapshot(pinned) == ["pinned"]
    assert pinned.calls == 2
