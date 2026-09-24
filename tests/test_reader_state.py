"""Reader lifecycle bookkeeping (``internal/reader_state.py``) and link-chain walks.

Covers the same-thread re-entry diagnosis (S21-K4), a close whose drain is interrupted
while a second closer waits (S21-K5), and long symlink chains: listing must be linear
in the chain length (S17-K10), and opening a dangling or cyclic chain must raise the
library's own errors rather than ``RecursionError`` (S17-K11).
"""

from __future__ import annotations

import io
import random
import tarfile
import threading
import time
import unicodedata
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest

from archivey import ArchiveMember, ArchiveyConfig, open_archive
from archivey.exceptions import (
    ArchiveyUsageError,
    LinkTargetNotFoundError,
    ReadError,
)
from archivey.internal.base_reader import BaseArchiveReader
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


def test_suspended_pass_is_not_diagnosed_as_a_callback() -> None:
    """A generator suspended at a yield: its thread is running the caller's loop body."""
    state = _state()
    token = state.acquire_pass("stream_members")
    state.set_suspended(token, True)
    with pytest.raises(ArchiveyUsageError, match="another reader operation") as ei:
        state.mark_reader_closed()
    assert CLOSE_FROM_INSIDE not in str(ei.value)
    with pytest.raises(ArchiveyUsageError, match="another reader operation"):
        state.acquire_worker("open")
    # Running again (a diagnostic fired inside a step): that is re-entry.
    state.set_suspended(token, False)
    with pytest.raises(ArchiveyUsageError, match=REENTRY):
        state.acquire_worker("open")


def test_internal_open_window_still_admits_children() -> None:
    state = _state()
    root = state.acquire_pass("extract_all")
    state.begin_internal_opens()
    try:
        child = state.acquire_pass("stream_members")
        worker = state.acquire_worker("open")
    finally:
        state.end_internal_opens()
    assert child.parent is root
    assert worker.parent is root


def _zip_with_bidi_name() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("evil\u202etxt.exe", b"x")
        zf.writestr("plain.txt", b"y")
    return buf.getvalue()


def test_default_reader_callback_close_during_members_names_the_callback() -> None:
    """End to end: the case the sweep measured on a default (non-concurrent) reader."""
    assert unicodedata.bidirectional("\u202e") == "RLO"
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


def test_callback_inside_stream_members_step_names_the_callback() -> None:
    """The streaming path: a diagnostic fires while the generator runs a step."""
    errors: list[BaseException] = []
    holder: list[ArchiveReader] = []

    def on_diagnostic(_diag: object) -> None:
        try:
            holder[0].get("plain.txt")
        except ArchiveyUsageError as exc:
            errors.append(exc)

    config = ArchiveyConfig(on_diagnostic=on_diagnostic)
    with open_archive(io.BytesIO(_zip_with_bidi_name()), config=config) as reader:
        holder.append(reader)
        for _member, _stream in reader.stream_members():
            pass
    assert errors, "fixture must emit a diagnostic during the pass"
    assert REENTRY in str(errors[0])


def test_loop_body_of_stream_members_keeps_the_generic_message() -> None:
    """The caller's own loop body is not a callback, so the message stays generic."""
    with open_archive(io.BytesIO(_zip_with_bidi_name())) as reader:
        for _member, _stream in reader.stream_members():
            with pytest.raises(ArchiveyUsageError) as ei:
                reader.get("plain.txt")
            assert "another reader operation ('stream_members')" in str(ei.value)
            with pytest.raises(ArchiveyUsageError) as ei:
                reader.close()
            assert CLOSE_FROM_INSIDE not in str(ei.value)
            break


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


def test_interrupt_after_the_transition_still_tears_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ctrl-C on the closers' notify, after the reader moved to READER_CLOSED."""
    reader = open_archive(io.BytesIO(_zip_with_bidi_name()))
    assert isinstance(reader, BaseArchiveReader)
    state = reader._state
    real_notify = state._close_cv.notify_all
    calls: list[int] = []

    def notify_all() -> None:
        calls.append(1)
        if len(calls) == 1:
            raise KeyboardInterrupt
        real_notify()

    monkeypatch.setattr(state._close_cv, "notify_all", notify_all)
    with pytest.raises(KeyboardInterrupt):
        reader.close()
    assert state.lifecycle is LifecycleState.READER_CLOSED
    reader.close()
    assert state.lifecycle is LifecycleState.TEARDOWN_COMPLETE


@pytest.mark.parametrize(
    ("target", "method"),
    [
        # Between the transition and the reader's lease drop.
        ("state", "_drop_reader_lease_locked"),
        # Past the transition, in close()'s own stream-shutdown step.
        ("state", "claim_stream_shutdown"),
        ("reader", "_close_public_streams"),
    ],
)
def test_interrupted_close_is_finished_by_the_next_close(
    monkeypatch: pytest.MonkeyPatch, target: str, method: str
) -> None:
    reader = open_archive(io.BytesIO(_zip_with_bidi_name()))
    assert isinstance(reader, BaseArchiveReader)
    state = reader._state
    obj: object = state if target == "state" else reader
    real = getattr(obj, method)
    calls: list[int] = []

    def interrupt_once(*args: object, **kwargs: object) -> object:
        calls.append(1)
        if len(calls) == 1:
            raise KeyboardInterrupt
        return real(*args, **kwargs)

    monkeypatch.setattr(obj, method, interrupt_once)
    with pytest.raises(KeyboardInterrupt):
        reader.close()
    assert state.lifecycle is LifecycleState.READER_CLOSED
    reader.close()
    assert state.lifecycle is LifecycleState.TEARDOWN_COMPLETE


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


# The terminal memo is sound only because every lookup is node-local. These shapes
# reuse the memo from a second starting member, which the straight chains above never do.


def _tar(path: Path, entries: list[tuple[str, str, str | None]]) -> Path:
    """``entries`` are ``(name, kind, target)``; kind is "file", "sym" or "hard"."""
    with tarfile.open(path, "w") as tf:
        for name, kind, target in entries:
            info = tarfile.TarInfo(name)
            if kind == "file":
                data = f"payload:{name}".encode()
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
                continue
            info.type = tarfile.SYMTYPE if kind == "sym" else tarfile.LNKTYPE
            assert target is not None
            info.linkname = target
            tf.addfile(info)
    return path


def _terminal_names(reader: ArchiveReader) -> dict[str, str | None]:
    return {
        m.name: (m.link_target_member.name if m.link_target_member else None)
        for m in reader.members()
        if m.is_link
    }


def test_tail_into_cycle_leaves_every_prefix_unresolved(tmp_path: Path) -> None:
    entries: list[tuple[str, str, str | None]] = [
        ("t0", "sym", "t1"),
        ("t1", "sym", "t2"),
        ("t2", "sym", "c0"),
        ("c0", "sym", "c1"),
        ("c1", "sym", "c0"),
    ]
    with open_archive(_tar(tmp_path / "rho.tar", entries)) as reader:
        assert set(_terminal_names(reader).values()) == {None}
        with pytest.raises(ReadError, match="Link cycle detected"):
            reader.open("t0")


def test_hardlink_chain_resolves_every_hop(tmp_path: Path) -> None:
    entries: list[tuple[str, str, str | None]] = [
        ("f", "file", None),
        ("h1", "hard", "f"),
        ("h2", "hard", "h1"),
        ("h3", "hard", "h2"),
    ]
    with open_archive(_tar(tmp_path / "hard.tar", entries)) as reader:
        assert _terminal_names(reader) == {"h1": "f", "h2": "f", "h3": "f"}
        assert reader.read("h3") == b"payload:f"


def test_converging_chains_share_the_memo(tmp_path: Path) -> None:
    entries: list[tuple[str, str, str | None]] = [
        ("a", "sym", "m"),
        ("b", "sym", "m"),
        ("m", "sym", "end"),
        ("end", "file", None),
    ]
    with open_archive(_tar(tmp_path / "branch.tar", entries)) as reader:
        assert _terminal_names(reader) == {"a": "end", "b": "end", "m": "end"}
        assert reader.read("b") == b"payload:end"


@pytest.mark.parametrize("seed", range(20))
def test_memoized_terminals_match_a_fresh_walk_per_member(
    tmp_path: Path, seed: int
) -> None:
    """Random link graphs: the memo must agree with an independent walk per member."""
    rng = random.Random(seed)
    names = [f"n{i}" for i in range(40)]
    entries: list[tuple[str, str, str | None]] = []
    for i, name in enumerate(names):
        roll = rng.random()
        if roll < 0.2:
            entries.append((name, "file", None))
        elif roll < 0.35 and i > 0:
            entries.append((name, "hard", rng.choice(names[:i])))
        else:
            # Includes names that do not exist (dead ends) and forward references.
            entries.append((name, "sym", rng.choice([*names, "missing"])))
    with open_archive(_tar(tmp_path / f"g{seed}.tar", entries)) as reader:
        assert isinstance(reader, BaseArchiveReader)
        members = reader.members()
        materialized = reader._materialized
        assert materialized is not None
        by_name = materialized.by_name_lists
        for member in members:
            if not (member.is_link and member.link_target):
                continue
            fresh = ArchiveMember(
                name=member.name, type=member.type, link_target=member.link_target
            )
            fresh._member_id = member._member_id
            fresh._archive_id = member._archive_id
            reader._resolve_link(fresh, by_name, {})
            assert member.link_target_member is fresh.link_target_member, member.name
