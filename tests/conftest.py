"""Top-level pytest configuration and shared fixtures."""

from __future__ import annotations

import importlib.util
import os
import shutil
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from archivey.reader import ForwardArchiveReader
    from archivey.types import ArchiveMember

# Per-test OS-resource leak oracle (child processes, owning streams, pipe fds).
pytest_plugins = ("leak_oracle",)

# Test archive cache dir (configurable via env var)
ARCHIVEY_TEST_CACHE = os.environ.get(
    "ARCHIVEY_TEST_CACHE",
    str(Path(__file__).parent.parent / ".pytest_cache" / "archivey-archives"),
)


def complete_listing(reader: ForwardArchiveReader) -> list[ArchiveMember]:
    """The member list from ``members_report()``, raising its error if incomplete.

    The complete-or-raise listing that works on a streaming reader too.
    """
    report = reader.members_report()
    if report.error is not None:
        raise report.error
    return list(report.members)


class ReadSizeSpy:
    """Wrap a stream and record the largest ``read(n)`` it was asked for.

    Used to pin that 7z password confirmation reads in bounded chunks: a single
    request sized to the whole folder is the gather that PR #318 removed. It is a
    proxy for peak memory, not a measurement of it.
    """

    def __init__(self, inner: object) -> None:
        self._inner = inner
        self.max_requested = 0

    def read(self, n: int = -1, /) -> bytes:
        # A negative n is read-everything, which is the unbounded case; record it as
        # larger than any real request rather than as 0.
        self.max_requested = max(self.max_requested, 2**31 if n < 0 else n)
        return self._inner.read(n)

    def close(self) -> None:
        self._inner.close()

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


def requires(*packages: str) -> pytest.MarkDecorator:
    """Skip a test (or parametrization) when an optional package is not importable.

    This is what lets the whole suite run in the `core-only` CI leg: tests that need
    an optional format library are skipped cleanly there rather than erroring, while
    they run normally in the `[all]` leg. Use it as a decorator::

        @requires_zstd()
        def test_zstd_stream(): ...

    Tests asserting the *degradation* behavior (a missing lib raising
    PackageNotInstalledError) should instead run unconditionally and assert that error.
    """
    missing = [p for p in packages if importlib.util.find_spec(p) is None]
    return pytest.mark.skipif(
        bool(missing),
        reason=f"requires optional package(s): {', '.join(missing)}",
    )


def unar_refusal() -> str | None:
    """Why archivey will not use the ``unar`` on ``PATH``, or ``None`` when it will.

    A ``unar`` that fails archivey's RAR5 check (Debian and Ubuntu packages before
    1.10.8+ds1-10, see ``dev-docs/known-issues.md``) is on ``PATH`` but unusable, so
    a test that needs ``unar`` skips there, as it does where ``unar`` is missing.
    """
    from archivey.exceptions import PackageNotInstalledError
    from archivey.internal.external.unar import find_unar

    if shutil.which("unar") is None:
        return "unar is not on PATH"
    try:
        find_unar(purpose="for the test suite")
    except PackageNotInstalledError as exc:
        return str(exc)
    return None


def unrar_refusal() -> str | None:
    """Why archivey has no RARLAB data program, or ``None`` when it has one.

    ``find_rarlab_unrar`` accepts RARLAB ``unrar``, or ``rar`` 6.0+ when there is no
    usable ``unrar``, and refuses a lookalike or a release below 6.0. A test that reads
    RAR data with ``rar_decompressor="unrar"`` needs exactly what it accepts.
    """
    from archivey.exceptions import PackageNotInstalledError
    from archivey.internal.backends.rar_unrar import find_rarlab_unrar

    try:
        find_rarlab_unrar()
    except PackageNotInstalledError as exc:
        return str(exc)
    return None


def binary_refusal(name: str) -> str | None:
    """Why a test cannot use ``name``, or ``None`` when it can.

    For ``unar`` and ``unrar`` this is archivey's own policy (:func:`unar_refusal`,
    :func:`unrar_refusal`), so a binary on ``PATH`` that archivey refuses reads as
    missing. Any other name only has to be on ``PATH``.
    """
    if name == "unar":
        return unar_refusal()
    if name == "unrar":
        return unrar_refusal()
    return None if shutil.which(name) else f"{name} is not on PATH"


def has_binary(name: str) -> bool:
    """Whether a test can use ``name``: :func:`binary_refusal` has no objection."""
    return binary_refusal(name) is None


def requires_binary(*names: str) -> pytest.MarkDecorator:
    """Skip a test when an external tool (e.g. the ``7z`` or ``unrar`` CLI) is not on PATH.

    The oracle-availability rule (``testing-contract`` spec): a test that shells out to an external
    binary must *skip*, not fail, where that binary is absent, so CI legs and dev
    machines without it stay green. ``unar`` also skips where archivey refuses the one
    on ``PATH`` (:func:`unar_refusal`), and the reason says why.
    """
    missing = [n for n in names if shutil.which(n) is None]
    reason = f"requires external binary(ies): {', '.join(missing)}"
    if not missing and "unar" in names:
        refusal = unar_refusal()
        if refusal is not None:
            missing = ["unar"]
            reason = f"requires a unar archivey will use: {refusal}"
    return pytest.mark.skipif(bool(missing), reason=reason)


def requires_zstd() -> pytest.MarkDecorator:
    """Skip when neither stdlib ``compression.zstd`` nor ``backports.zstd`` is importable."""
    has = _has_zstd_backend()
    return pytest.mark.skipif(
        not has,
        reason="requires zstd backend (compression.zstd or backports.zstd)",
    )


def _has_zstd_backend() -> bool:
    for name in ("compression.zstd", "backports.zstd"):
        try:
            if importlib.util.find_spec(name) is not None:
                return True
        except ModuleNotFoundError:
            continue
    return False


def zstd_backend():
    """Return the installed zstd codec module (stdlib on 3.14+, else backports)."""
    import importlib

    for name in ("compression.zstd", "backports.zstd"):
        try:
            return importlib.import_module(name)
        except ImportError:
            continue
    raise RuntimeError("no zstd backend installed")


def pytest_exception_interact(node, call, report) -> None:
    """Print the active fuzz mutation when a parametrized case times out or errors."""
    if call.when != "call" or "test_mutation_fuzz.py" not in node.nodeid:
        return
    from tests.test_mutation_fuzz import active_mutation_report

    msg = active_mutation_report()
    if msg is None:
        return
    tr = node.config.pluginmanager.get_plugin("terminalreporter")
    if tr is not None:
        tr.write_line(msg, red=True, bold=True)


def pytest_configure(config: pytest.Config) -> None:
    """Register the shared Hypothesis settings profile used by property-safety tests.

    Default: ``max_examples=100``, ``deadline=None``, ``derandomize=True`` (reproducible
    CI budget). ``ARCHIVEY_FUZZ_EXAMPLES`` selects a deeper local/nightly sweep — mirrors
    the mutation harness's ``ARCHIVEY_FUZZ_MUTATIONS`` pattern. Hypothesis is a ``dev``
    dependency; under ``[core-only]`` the import is absent and this is a no-op (the
    property module itself skips collection).
    """
    try:
        from hypothesis import settings
    except ImportError:
        return
    raw = os.environ.get("ARCHIVEY_FUZZ_EXAMPLES", "100")
    try:
        max_examples = int(raw)
    except ValueError as exc:
        raise pytest.UsageError(
            f"ARCHIVEY_FUZZ_EXAMPLES must be an integer, got {raw!r}"
        ) from exc
    settings.register_profile(
        "archivey",
        max_examples=max_examples,
        deadline=None,
        derandomize=True,
    )
    settings.load_profile("archivey")


@pytest.fixture
def test_dir(tmp_path: Path) -> Path:
    """Create a test directory with some files and subdirectories."""
    (tmp_path / "file1.txt").write_bytes(b"hello world")
    (tmp_path / "file2.txt").write_bytes(b"foo bar baz")
    subdir = tmp_path / "subdir"
    subdir.mkdir()
    (subdir / "nested.txt").write_bytes(b"nested content")
    return tmp_path


@pytest.fixture
def named_fifo_with_writer() -> Iterator[Callable[[Path, bytes], None]]:
    """Make named FIFOs whose writer thread delivers a payload once the read end opens.

    The writer blocks in ``open()`` until something opens the read end, and in
    ``write()`` while a payload larger than the pipe buffer is not drained. A test whose
    code under test raises before reading would leave it blocked for the rest of the
    session, so teardown opens the read end itself, drains it and joins the writer.
    """
    writers: list[tuple[Path, threading.Thread]] = []

    def make(path: Path, payload: bytes) -> None:
        os.mkfifo(path)

        def _fill() -> None:
            try:
                with open(path, "wb") as writer:
                    writer.write(payload)
            except OSError:
                pass

        thread = threading.Thread(target=_fill, daemon=True)
        thread.start()
        writers.append((path, thread))

    yield make
    for path, thread in writers:
        if thread.is_alive():
            # Non-blocking, so this returns at once even if the writer already left.
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            try:
                deadline = time.monotonic() + 5
                while thread.is_alive() and time.monotonic() < deadline:
                    try:
                        os.read(fd, 1 << 16)
                    except BlockingIOError:
                        pass
                    thread.join(0.01)
            finally:
                os.close(fd)
        assert not thread.is_alive(), f"FIFO writer for {path} is still blocked"
