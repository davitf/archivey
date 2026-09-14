"""The leak oracle fails a test that leaves a child or an owning stream open.

Each case runs in a nested pytest so this file's own autouse oracle is not the
thing under test. The nested process loads ``leak_oracle`` via ``-p`` and does
not load ``tests/conftest.py`` (the file lives in tmp_path).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

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


def test_oracle_fails_on_non_owning_slice_over_owned_pipe(tmp_path: Path) -> None:
    """The #336 shape: close a non-owning ``SlicingStream``, leave the process alive."""
    proc = _run_isolated(
        tmp_path,
        """
from archivey.internal.streams.streamtools.base import DelegatingStream
from archivey.internal.streams.streamtools.slice import SlicingStream

class _OwnedPipe(DelegatingStream):
    def __init__(self, stdout, proc) -> None:
        super().__init__(stdout, readinto_passthrough=False, manual_inner_close=True)
        self._proc = proc

    def close(self) -> None:
        if self.closed:
            return
        self._inner.close()
        if self._proc.poll() is None:
            self._proc.kill()
            self._proc.wait()
        super().close()

def test_forget_own_source() -> None:
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
    view = SlicingStream(inner, length=5, own_source=False)
    assert view.read() == b"hello"
    view.close()
""",
    )
    assert proc.returncode != 0, _output(proc)
    text = _output(proc)
    assert "test leaked OS resources" in text
    assert (
        "child process still running" in text or "unclosed manual_inner_close" in text
    )


def test_oracle_passes_when_slice_owns_the_pipe(tmp_path: Path) -> None:
    proc = _run_isolated(
        tmp_path,
        """
from archivey.internal.streams.streamtools.base import DelegatingStream
from archivey.internal.streams.streamtools.slice import SlicingStream

class _OwnedPipe(DelegatingStream):
    def __init__(self, stdout, proc) -> None:
        super().__init__(stdout, readinto_passthrough=False, manual_inner_close=True)
        self._proc = proc

    def close(self) -> None:
        if self.closed:
            return
        self._inner.close()
        if self._proc.poll() is None:
            self._proc.kill()
            self._proc.wait()
        super().close()

def test_own_source_reaps() -> None:
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
    view = SlicingStream(inner, length=5, own_source=True)
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

def test_unclosed_own_source() -> None:
    SlicingStream(io.BytesIO(b"hello"), length=5, own_source=True)
""",
    )
    assert proc.returncode != 0, _output(proc)
    text = _output(proc)
    assert "unclosed own_source stream: SlicingStream" in text


def test_oracle_passes_when_owning_slice_is_closed(tmp_path: Path) -> None:
    proc = _run_isolated(
        tmp_path,
        """
import io
from archivey.internal.streams.streamtools.slice import SlicingStream

def test_closed_own_source() -> None:
    SlicingStream(io.BytesIO(b"hello"), length=5, own_source=True).close()
""",
    )
    assert proc.returncode == 0, _output(proc)


def test_oracle_fails_on_unclosed_manual_inner_close(tmp_path: Path) -> None:
    proc = _run_isolated(
        tmp_path,
        """
import io
from archivey.internal.streams.streamtools.base import DelegatingStream

def test_unclosed_manual() -> None:
    DelegatingStream(io.BytesIO(b"x"), manual_inner_close=True)
""",
    )
    assert proc.returncode != 0, _output(proc)
    assert "unclosed manual_inner_close stream" in _output(proc)


def test_oracle_passes_when_manual_inner_close_is_closed(tmp_path: Path) -> None:
    proc = _run_isolated(
        tmp_path,
        """
import io
from archivey.internal.streams.streamtools.base import DelegatingStream

def test_closed_manual() -> None:
    DelegatingStream(io.BytesIO(b"x"), manual_inner_close=True).close()
""",
    )
    assert proc.returncode == 0, _output(proc)
