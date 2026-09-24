"""Reader lifecycle bookkeeping (``internal/reader_state.py``) and link-chain walks.

Covers the same-thread re-entry diagnosis (S21-K4), a close whose drain is interrupted
while a second closer waits (S21-K5), and long symlink chains: listing must be linear
in the chain length (S17-K10), and opening a dangling or cyclic chain must raise the
library's own errors rather than ``RecursionError`` (S17-K11).
"""

from __future__ import annotations

import io
import tarfile
import threading
import time
import unicodedata
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest

from archivey import ArchiveyConfig, open_archive
from archivey.exceptions import (
    ArchiveyUsageError,
    LinkTargetNotFoundError,
    ReadError,
)
from archivey.internal.reader_state import (
    LifecycleState,
    OperationToken,
    ReaderState,
)
from archivey.reader import ArchiveReader
from archivey.types import MemberStreams

REENTRY = "re-entered from inside its own"
CLOSE_FROM_INSIDE = "from inside one of its own calls"


def _state(*, concurrent: bool = False) -> ReaderState:
    streams = MemberStreams.CONCURRENT if concurrent else MemberStreams(0)
    return ReaderState(member_streams=streams, open_site=None)


def _in_thread(fn: Callable[[], object]) -> None:
    thread = threading.Thread(target=fn)
    thread.start()
    thread.join()


# ---------------------------------------------------------------------------
# S21-K4: same-thread re-entry is diagnosed before the generic overlap messages
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("concurrent", [False, True])
def test_close_inside_own_pass_names_the_callback(concurrent: bool) -> None:
    state = _state(concurrent=concurrent)
    state.acquire_pass("members")
    with pytest.raises(ArchiveyUsageError, match=CLOSE_FROM_INSIDE):
        state.mark_reader_closed()
    assert state.lifecycle is LifecycleState.OPEN


@pytest.mark.parametrize("concurrent", [False, True])
def test_close_inside_own_worker_names_the_callback(concurrent: bool) -> None:
    state = _state(concurrent=concurrent)
    state.acquire_worker("open")
    with pytest.raises(ArchiveyUsageError, match=CLOSE_FROM_INSIDE):
        state.mark_reader_closed()
    assert state.lifecycle is LifecycleState.OPEN


@pytest.mark.parametrize("concurrent", [False, True])
def test_pass_inside_own_pass_names_the_callback(concurrent: bool) -> None:
    state = _state(concurrent=concurrent)
    state.acquire_pass("members")
    with pytest.raises(ArchiveyUsageError, match=REENTRY):
        state.acquire_pass("members")
    with pytest.raises(ArchiveyUsageError, match=REENTRY):
        state.acquire_worker("open")


def test_worker_inside_own_worker_names_the_callback() -> None:
    state = _state(concurrent=True)
    state.acquire_worker("members")
    with pytest.raises(ArchiveyUsageError, match=REENTRY):
        state.acquire_worker("get")


def test_another_threads_pass_keeps_the_generic_message() -> None:
    state = _state()
    _in_thread(lambda: state.acquire_pass("members"))
    with pytest.raises(ArchiveyUsageError, match="another reader operation") as ei:
        state.mark_reader_closed()
    assert REENTRY not in str(ei.value)
    with pytest.raises(ArchiveyUsageError, match="another reader operation"):
        state.acquire_pass("members")


def test_iterator_pass_is_not_diagnosed_as_a_callback() -> None:
    """A generator's pass is held on the thread running the caller's loop body."""
    state = _state()
    state.acquire_pass("stream_members", spans_yields=True)
    with pytest.raises(ArchiveyUsageError, match="another reader operation") as ei:
        state.mark_reader_closed()
    assert CLOSE_FROM_INSIDE not in str(ei.value)
    with pytest.raises(ArchiveyUsageError, match="another reader operation"):
        state.acquire_worker("open")


def test_internal_open_window_still_admits_children() -> None:
    state = _state()
    root = state.acquire_pass("extract_all")
    state.begin_internal_opens()
    try:
        child = state.acquire_pass("stream_members", spans_yields=True)
        worker = state.acquire_worker("open")
    finally:
        state.end_internal_opens()
    assert child.parent is root
    assert worker.parent is root


def _zip_with_bidi_name() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("evil‮txt.exe", b"x")
        zf.writestr("plain.txt", b"y")
    return buf.getvalue()


def test_default_reader_callback_close_during_members_names_the_callback() -> None:
    """End to end: the case the sweep measured on a default (non-concurrent) reader."""
    assert unicodedata.bidirectional("‮") == "RLO"
    errors: list[BaseException] = []
    holder: list[ArchiveReader] = []

    def on_diagnostic(_diag: object) -> None:
        try:
            holder[0].close()
        except ArchiveyUsageError as exc:
            errors.append(exc)

    config = ArchiveyConfig(on_diagnostic=on_diagnostic)
    with open_archive(io.BytesIO(_zip_with_bidi_name()), config=config) as reader:
        holder.append(reader)
        reader.members()
    assert errors, "fixture must emit a diagnostic during listing"
    assert CLOSE_FROM_INSIDE in str(errors[0])


# ---------------------------------------------------------------------------
# S21-K5: an interrupted drain must not tell a waiting closer the reader closed
# ---------------------------------------------------------------------------


def test_interrupted_drain_hands_close_to_the_waiting_closer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state(concurrent=True)
    worker_box: list[OperationToken] = []
    _in_thread(lambda: worker_box.append(state.acquire_worker("open")))

    real_workers_wait = state._workers_cv.wait
    real_close_wait = state._close_cv.wait
    second_waiting = threading.Event()
    first_closer: list[threading.Thread] = []

    def workers_wait(timeout: float | None = None) -> bool:
        if threading.current_thread() in first_closer:
            # Hold the drain until the second closer is parked, then abort it the
            # way Ctrl-C does during Condition.wait().
            deadline = time.monotonic() + 5
            while not second_waiting.is_set() and time.monotonic() < deadline:
                real_workers_wait(0.01)
            raise KeyboardInterrupt
        return real_workers_wait(timeout)

    def close_wait(timeout: float | None = None) -> bool:
        second_waiting.set()
        return real_close_wait(timeout)

    monkeypatch.setattr(state._workers_cv, "wait", workers_wait)
    monkeypatch.setattr(state._close_cv, "wait", close_wait)

    first_raised: list[BaseException] = []
    second_result: list[bool] = []

    def first() -> None:
        first_closer.append(threading.current_thread())
        try:
            state.mark_reader_closed()
        except KeyboardInterrupt as exc:
            first_raised.append(exc)

    def second() -> None:
        # Enter only once the first closer is draining.
        deadline = time.monotonic() + 5
        while not state._closing and time.monotonic() < deadline:
            time.sleep(0.001)
        second_result.append(state.mark_reader_closed())

    t1 = threading.Thread(target=first, daemon=True)
    t2 = threading.Thread(target=second, daemon=True)
    t1.start()
    t2.start()
    t1.join(5)
    assert first_raised, "the first closer's drain must have been interrupted"
    # The second closer must now be draining itself, not reporting a peer's close.
    time.sleep(0.05)
    assert not second_result
    assert state.lifecycle is LifecycleState.OPEN
    state.release_worker(worker_box[0])
    t2.join(5)
    assert not t2.is_alive()
    assert second_result == [True]
    assert state.lifecycle is LifecycleState.READER_CLOSED
    assert state.claim_teardown()


# ---------------------------------------------------------------------------
# S17-K10 / S17-K11: long symlink chains
# ---------------------------------------------------------------------------


def _symlink_chain_tar(
    path: Path, n: int, *, last_target: str, file_at_end: bool = False
) -> Path:
    with tarfile.open(path, "w") as tf:
        for i in range(n):
            info = tarfile.TarInfo(f"l{i}")
            info.type = tarfile.SYMTYPE
            info.linkname = f"l{i + 1}" if i + 1 < n else last_target
            tf.addfile(info)
        if file_at_end:
            data = b"payload"
            info = tarfile.TarInfo(last_target)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return path


def test_listing_a_long_symlink_chain_is_linear(tmp_path: Path) -> None:
    """5000 chained links took ~27s when every link walked the chain from scratch."""
    n = 5000
    path = _symlink_chain_tar(
        tmp_path / "chain.tar", n, last_target="end.txt", file_at_end=True
    )
    start = time.perf_counter()
    with open_archive(path) as reader:
        members = {m.name: m for m in reader.members()}
        elapsed = time.perf_counter() - start
        for i in (0, n // 2, n - 1):
            target = members[f"l{i}"].link_target_member
            assert target is not None and target.name == "end.txt"
        assert reader.read("l0") == b"payload"
    assert elapsed < 5.0, f"listing {n} chained links took {elapsed:.1f}s"


def test_open_long_dangling_chain_raises_link_target_not_found(
    tmp_path: Path,
) -> None:
    n = 2000
    path = _symlink_chain_tar(tmp_path / "dangling.tar", n, last_target=f"l{n}")
    with open_archive(path) as reader:
        start = reader.get("l0")
        assert start is not None and start.link_target_member is None
        with pytest.raises(LinkTargetNotFoundError):
            reader.open("l0")


def test_open_long_cycle_raises_read_error(tmp_path: Path) -> None:
    n = 2000
    path = _symlink_chain_tar(tmp_path / "cycle.tar", n, last_target="l0")
    with open_archive(path) as reader:
        start = reader.get("l0")
        assert start is not None and start.link_target_member is None
        with pytest.raises(ReadError, match="Link cycle detected"):
            reader.open("l0")
