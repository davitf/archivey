"""Random-access calls inside a ``stream_members()`` loop on a ``CONCURRENT`` reader.

Once the member list is published, ``open()``, ``read()``, ``get()``, ``members()`` and
iteration are admitted while a ``stream_members()`` pass is running, from the pass's
own thread (the loop body) and from other threads. Before the list is complete, and on
a reader opened without ``concurrent_members=True``, they still raise
``ArchiveyUsageError``. The bookkeeping itself is unit-tested at the bottom.
"""

from __future__ import annotations

import io
import tarfile
import threading
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest

from archivey import ArchiveyConfig, ArchiveyUsageError, open_archive
from archivey.internal.reader_state import LifecycleState, ReaderState
from archivey.reader import ArchiveReader
from archivey.types import MemberStreams
from tests.conftest import requires, requires_binary

pytestmark = pytest.mark.concurrent_reader

_FIXTURES = Path(__file__).parent / "fixtures"

_FILES = {
    "a.txt": b"alpha " * 100,
    "dir/b.bin": bytes(range(256)) * 40,
    "c.txt": b"gamma",
}


def _zip(tmp_path: Path) -> Path:
    path = tmp_path / "files.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in _FILES.items():
            zf.writestr(name, data)
    return path


def _tar(tmp_path: Path, mode: str = "w", suffix: str = ".tar") -> Path:
    path = tmp_path / f"files{suffix}"
    with tarfile.open(path, mode) as tf:
        for name, data in _FILES.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return path


def _tgz(tmp_path: Path) -> Path:
    return _tar(tmp_path, "w:gz", ".tar.gz")


def _directory(tmp_path: Path) -> Path:
    root = tmp_path / "tree"
    for name, data in _FILES.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(data)
    return root


def _iso(tmp_path: Path) -> Path:
    import pycdlib

    iso = pycdlib.PyCdlib()
    iso.new(interchange_level=3, rock_ridge="1.09")
    iso.add_directory("/DIR", rr_name="dir")
    for name, data in _FILES.items():
        iso_name = "/" + name.upper().replace(".", "_") + ".;1"
        iso.add_fp(io.BytesIO(data), len(data), iso_name, rr_name=name.split("/")[-1])
    path = tmp_path / "files.iso"
    iso.write(str(path))
    iso.close()
    return path


def _fixture(name: str) -> Callable[[Path], Path]:
    return lambda _tmp: _FIXTURES / name


# Every format whose random-access pass lists the archive before it yields: the member
# list is published by the time the loop body runs, so no members() call is needed.
LISTED_UP_FRONT = [
    pytest.param(_zip, id="zip"),
    pytest.param(_directory, id="dir"),
    pytest.param(_iso, id="iso", marks=requires("pycdlib")),
    pytest.param(
        _fixture("rar/basic_nonsolid__.rar"),
        id="rar-nonsolid",
        marks=requires_binary("unrar"),
    ),
]

# Formats whose pass lists as it goes: the list is published when a pass ends.
LISTED_BY_THE_PASS = [
    pytest.param(_tar, id="tar"),
    pytest.param(_tgz, id="tar.gz"),
    pytest.param(_fixture("sevenzip/links_mid_folder_solid.7z"), id="7z-solid"),
    pytest.param(
        _fixture("rar/basic_solid__.rar"),
        id="rar-solid",
        marks=requires_binary("unrar"),
    ),
]

ALL_FORMATS = [*LISTED_UP_FRONT, *LISTED_BY_THE_PASS]


def _expected(path: Path) -> dict[str, bytes]:
    with open_archive(path) as reader:
        return {
            m.name: reader.read(m)
            for m in reader.members()
            if m.is_file and m.is_current
        }


def _in_thread(fn: Callable[[], object]) -> object:
    """Run ``fn`` on a new thread; return its result or re-raise its exception."""
    box: list[object] = []
    errors: list[BaseException] = []

    def run() -> None:
        try:
            box.append(fn())
        except BaseException as exc:  # noqa: BLE001 - re-raised on the caller's thread
            errors.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    thread.join()
    if errors:
        raise errors[0]
    return box[0]


def _random_calls(reader: ArchiveReader, expected: dict[str, bytes]) -> None:
    """Every random-access call, each checked against the bytes read outside the pass."""
    for name, data in expected.items():
        assert reader.read(name) == data
        with reader.open(name) as stream:
            assert stream.read() == data
        member = reader.get(name)
        assert member is not None
        assert member.name == name
    names = {m.name for m in reader.members()}
    assert set(expected) <= names
    assert {m.name for m in reader} == names
    assert {m.name for m in reader.members_report().members} == names


@pytest.mark.parametrize("make", ALL_FORMATS)
def test_random_calls_inside_the_loop_after_members(
    tmp_path: Path, make: Callable[[Path], Path]
) -> None:
    path = make(tmp_path)
    expected = _expected(path)
    passed: dict[str, bytes] = {}
    with open_archive(path, concurrent_members=True) as reader:
        reader.members()
        for member, stream in reader.stream_members():
            # Before the pass's own stream is read, so a random read between the
            # pass's members cannot move it.
            _random_calls(reader, expected)
            _in_thread(lambda: _random_calls(reader, expected))
            if stream is not None and member.is_current:
                passed[member.name] = stream.read()
    assert passed == expected


@pytest.mark.parametrize("make", ALL_FORMATS)
def test_random_reads_interleaved_with_the_pass_stream(
    tmp_path: Path, make: Callable[[Path], Path]
) -> None:
    """A random stream held open while the pass streams are read a chunk at a time."""
    path = make(tmp_path)
    expected = _expected(path)
    passed: dict[str, bytes] = {}
    with open_archive(path, concurrent_members=True) as reader:
        reader.members()
        held = {name: reader.open(name) for name in expected}
        got = dict.fromkeys(expected, b"")
        for member, stream in reader.stream_members():
            if stream is None or not member.is_current:
                continue
            chunks = []
            while True:
                chunk = stream.read(7)
                for name, other in held.items():
                    got[name] += other.read(5)
                if not chunk:
                    break
                chunks.append(chunk)
            passed[member.name] = b"".join(chunks)
        for name, other in held.items():
            got[name] += other.read()
            other.close()
    assert passed == expected
    assert got == expected


@pytest.mark.parametrize("make", LISTED_UP_FRONT)
def test_formats_listed_up_front_need_no_members_call(
    tmp_path: Path, make: Callable[[Path], Path]
) -> None:
    path = make(tmp_path)
    expected = _expected(path)
    with open_archive(path, concurrent_members=True) as reader:
        for _member, _stream in reader.stream_members():
            _random_calls(reader, expected)


@pytest.mark.parametrize("make", LISTED_BY_THE_PASS)
def test_refused_until_the_listing_is_complete(
    tmp_path: Path, make: Callable[[Path], Path]
) -> None:
    """A first pass that lists as it goes admits no random calls; a later one does."""
    path = make(tmp_path)
    expected = _expected(path)
    name = next(iter(expected))
    with open_archive(path, concurrent_members=True) as reader:
        for _member, _stream in reader.stream_members():
            with pytest.raises(ArchiveyUsageError, match="already active"):
                reader.read(name)
            with pytest.raises(ArchiveyUsageError, match="already active"):
                _in_thread(lambda: reader.get(name))
        # The pass that reached the end published the list.
        for _member, _stream in reader.stream_members():
            _random_calls(reader, expected)


@pytest.mark.parametrize("make", ALL_FORMATS)
def test_default_reader_still_refuses(
    tmp_path: Path, make: Callable[[Path], Path]
) -> None:
    path = make(tmp_path)
    with open_archive(path) as reader:
        name = next(m.name for m in reader.members() if m.is_file)
        it = reader.stream_members()
        next(it)
        for call in (
            lambda: reader.read(name),
            lambda: reader.open(name),
            lambda: reader.get(name),
            reader.members,
        ):
            with pytest.raises(ArchiveyUsageError, match="already active"):
                call()
            with pytest.raises(ArchiveyUsageError, match="already active"):
                _in_thread(call)
        it.close()


@pytest.mark.parametrize("make", ALL_FORMATS)
def test_passes_side_by_side(tmp_path: Path, make: Callable[[Path], Path]) -> None:
    """Two passes advance in lockstep on one thread, and a third runs on another."""
    path = make(tmp_path)
    expected = _expected(path)

    def drain(it):  # noqa: ANN001, ANN202 - test helper
        return {m.name: s.read() for m, s in it if s is not None and m.is_current}

    with open_archive(path, concurrent_members=True) as reader:
        reader.members()
        first: dict[str, bytes] = {}
        second: dict[str, bytes] = {}
        other = reader.stream_members()
        for member, stream in reader.stream_members():
            paired, paired_stream = next(other)
            assert paired is member
            if stream is not None and member.is_current:
                assert paired_stream is not None
                # Interleaved reads: the two passes' streams are independent.
                first[member.name] = stream.read(3)
                second[member.name] = paired_stream.read()
                first[member.name] += stream.read()
                assert _in_thread(lambda: drain(reader.stream_members())) == expected
        assert next(other, None) is None
    assert first == expected
    assert second == expected


@pytest.mark.parametrize("make", LISTED_BY_THE_PASS)
def test_second_pass_refused_until_the_listing_is_complete(
    tmp_path: Path, make: Callable[[Path], Path]
) -> None:
    path = make(tmp_path)
    with open_archive(path, concurrent_members=True) as reader:
        it = reader.stream_members()
        next(it)
        with pytest.raises(ArchiveyUsageError, match="already active"):
            next(reader.stream_members())
        it.close()


def test_extraction_still_refused_inside_the_loop(tmp_path: Path) -> None:
    path = _zip(tmp_path)
    with open_archive(path, concurrent_members=True) as reader:
        it = reader.stream_members()
        next(it)
        with pytest.raises(ArchiveyUsageError, match="already active"):
            reader.extract_all(tmp_path / "out")
        side = reader.stream_members()
        next(side)
        # The first pass ends; the one beside it still holds the reader.
        it.close()
        with pytest.raises(ArchiveyUsageError, match="already active"):
            reader.extract_all(tmp_path / "out")
        assert reader.read("c.txt") == b"gamma"
        side.close()
        reader.extract_all(tmp_path / "out")
    assert (tmp_path / "out" / "c.txt").read_bytes() == b"gamma"


def test_close_winds_down_every_suspended_pass(tmp_path: Path) -> None:
    path = _zip(tmp_path)
    reader = open_archive(path, concurrent_members=True)
    first = reader.stream_members()
    second = reader.stream_members()
    _, first_stream = next(first)
    _, second_stream = next(second)
    reader.close()
    assert first_stream is not None and first_stream.closed
    assert second_stream is not None and second_stream.closed
    for it in (first, second):
        with pytest.raises(ArchiveyUsageError):
            next(it)


def test_close_inside_the_loop_after_random_calls(tmp_path: Path) -> None:
    path = _zip(tmp_path)
    reader = open_archive(path, concurrent_members=True)
    it = reader.stream_members()
    next(it)
    held = reader.open("c.txt")
    reader.close()
    with pytest.raises(ArchiveyUsageError):
        next(it)
    # close() closes the pass's stream and every other open member stream with it.
    assert held.closed


# --- ReaderState bookkeeping --------------------------------------------------------


def _state(*, concurrent: bool = True, published: bool = True) -> ReaderState:
    streams = MemberStreams.CONCURRENT if concurrent else MemberStreams(0)
    state = ReaderState(member_streams=streams, open_site=None)
    state.set_listing_published(published)
    return state


def test_pass_starts_beside_another_threads_worker() -> None:
    state = _state()
    worker = _in_thread(lambda: state.acquire_worker("open"))
    token = state.acquire_pass("stream_members")
    state.release_pass(token)
    state.release_worker(worker)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("concurrent", "published", "name"),
    [
        (False, True, "stream_members"),
        (True, False, "stream_members"),
        (True, True, "extract_all"),
        (True, True, "members"),
    ],
)
def test_pass_and_worker_still_exclude_each_other(
    concurrent: bool, published: bool, name: str
) -> None:
    state = _state(concurrent=concurrent, published=published)
    worker = _in_thread(lambda: state.acquire_worker("open"))
    with pytest.raises(ArchiveyUsageError, match="still in progress"):
        state.acquire_pass(name)
    state.release_worker(worker)  # type: ignore[arg-type]
    token = state.acquire_pass(name)
    with pytest.raises(ArchiveyUsageError, match="already active"):
        _in_thread(lambda: state.acquire_worker("open"))
    state.release_pass(token)


def _close_waiting_on_a_worker(
    resume_pass: bool,
) -> tuple[ReaderState, list[str], list[BaseException]]:
    """close() beside a suspended pass and an in-flight worker; the worker then ends.

    With ``resume_pass`` the pass resumes while close() waits for the worker.
    """
    state = _state()
    token = state.acquire_pass("stream_members")
    closed: list[str] = []
    state.set_pass_closer(token, lambda: closed.append("pass"))
    state.set_suspended(token, True)
    worker = _in_thread(lambda: state.acquire_worker("open"))
    errors: list[BaseException] = []

    def close() -> None:
        try:
            state.mark_reader_closed()
        except BaseException as exc:  # noqa: BLE001 - checked by the caller
            errors.append(exc)

    closer = threading.Thread(target=close)
    closer.start()
    while not state._closing:
        threading.Event().wait(0.001)
    if resume_pass:
        state.set_suspended(token, False)
    state.release_worker(worker)  # type: ignore[arg-type]
    closer.join()
    return state, ["pass"] if state._pass_wind_down else [], errors


def test_close_waits_for_the_worker_then_winds_the_pass_down() -> None:
    state, wind_down, errors = _close_waiting_on_a_worker(resume_pass=False)
    assert errors == []
    assert state.lifecycle is LifecycleState.READER_CLOSED
    assert wind_down == ["pass"]


def test_close_refuses_a_pass_that_resumed_while_it_waited() -> None:
    state, wind_down, errors = _close_waiting_on_a_worker(resume_pass=True)
    assert len(errors) == 1
    assert isinstance(errors[0], ArchiveyUsageError)
    assert "already active" not in str(errors[0])
    assert "is active" in str(errors[0])
    assert state.lifecycle is LifecycleState.OPEN
    assert not state._closing
    assert wind_down == []


def test_7z_link_opened_from_another_thread_leaves_the_pass_decoder_alone() -> None:
    """A link the pass sits on, followed by ``open()`` on another thread.

    With ``read_link_targets=False`` the pass offers its own folder decoder for the
    link's bytes. Only the pass's thread may read through it: a read from another
    thread would move the decoder under the pass.
    """
    path = _FIXTURES / "sevenzip" / "links_mid_folder_solid.7z"
    expected = _expected(path)
    config = ArchiveyConfig(read_link_targets=False)
    with open_archive(path, config=config) as reader:
        through_links = {m.name: reader.read(m) for m in reader.members() if m.is_link}
    assert through_links
    passed: dict[str, bytes] = {}
    followed = 0
    with open_archive(path, concurrent_members=True, config=config) as reader:
        reader.members()
        for member, stream in reader.stream_members():
            if member.is_link:
                # Count reads through the pass's decoder.
                pass_link = reader._pass_link  # type: ignore[attr-defined]
                assert pass_link is not None
                assert pass_link[0] is member
                used: list[str] = []

                def counted(opener=pass_link[1]):  # noqa: ANN202 - test shim
                    used.append(threading.current_thread().name)
                    return opener()

                reader._pass_link = (member, counted, *pass_link[2:])  # type: ignore[attr-defined]
                target = _in_thread(lambda m=member: reader.read(m))
                assert target == through_links[member.name]
                assert used == []
                followed += 1
            elif stream is not None:
                passed[member.name] = stream.read()
    assert followed
    assert passed == expected


def test_close_takes_every_suspended_pass_closer() -> None:
    state = _state()
    ran: list[str] = []
    tokens = []
    for _ in range(2):
        tokens.append(state.acquire_pass("stream_members"))
        state.set_suspended(tokens[-1], True)
    for i, token in enumerate(tokens):

        def closer(i: int = i) -> None:
            ran.append(f"pass {i}")
            raise ValueError(f"pass {i}")

        state.set_pass_closer(token, closer)
        state.set_suspended(token, True)
    state.mark_reader_closed()
    wind_down = state.take_pass_wind_down()
    assert wind_down is not None
    with pytest.raises(ExceptionGroup) as info:
        wind_down()
    assert ran == ["pass 0", "pass 1"]
    assert [str(e) for e in info.value.exceptions] == ["pass 0", "pass 1"]
    assert state.take_pass_wind_down() is None


def test_ending_the_first_pass_hands_the_reader_to_the_one_beside_it() -> None:
    state = _state()
    first = state.acquire_pass("stream_members")
    state.set_suspended(first, True)
    second = state.acquire_pass("stream_members")
    state.set_suspended(second, True)
    state.release_pass(first)
    with pytest.raises(ArchiveyUsageError, match="'stream_members'"):
        state.acquire_pass("extract_all")
    state.release_pass(second)
    state.release_pass(state.acquire_pass("extract_all"))
