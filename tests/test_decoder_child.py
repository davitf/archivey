"""The decoder child pool: reuse, blame, re-run in a fresh child, recycling, deaths.

Most tests run a stand-in worker: ``decoder_worker.py`` loaded by path with extra
codecs added to its ``LOADERS``, so the real frame and serve loop answer.
"""

from __future__ import annotations

import os
import signal
import sys
import textwrap
import zlib
from collections.abc import Iterator
from pathlib import Path

import pytest

from archivey.exceptions import CorruptionError, ReadError, ResourceLimitError
from archivey.internal.streams.codecs import decoder_child
from archivey.internal.streams.codecs.decoder_child import POOL, DecoderChild
from archivey.internal.streams.codecs.isolated import ISOLATED

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="the stand-in workers signal themselves"
)

# Codecs the stand-in worker adds:
# - "poison": a decoder whose ``decompress`` returns its input and marks the process
#   damaged, without crashing;
# - "zlib": the real one, except that in a damaged process its ``decompress`` crashes;
# - "liar": returns its input, except that in a damaged process its first call returns
#   it reversed and its second crashes;
# - "die": a decoder whose ``decompress`` ends the process with the signal its argument
#   names.
_STAND_IN = textwrap.dedent(
    """
    import importlib.util, os, signal, sys

    spec = importlib.util.spec_from_file_location("decoder_worker", {worker!r})
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    damaged = []

    class Poison:
        eof = False
        needs_input = True
        def decompress(self, data, max_length=-1):
            damaged.append(True)
            return data

    class Die:
        def __init__(self, signum):
            self.signum = signum
        def decompress(self, data, max_length=-1):
            os.kill(os.getpid(), self.signum)

    class Liar:
        calls = 0
        def decompress(self, data, max_length=-1):
            self.calls += 1
            if not damaged:
                return data
            if self.calls == 1:
                return data[::-1]
            os.kill(os.getpid(), signal.SIGSEGV)

    real_zlib = worker.LOADERS["zlib"]

    def load_zlib():
        build = real_zlib()
        def make(*args, **kwargs):
            decoder = build(*args, **kwargs)
            if not damaged:
                return decoder
            class Crashing:
                eof = False
                needs_input = True
                def decompress(self, data, max_length=0):
                    os.kill(os.getpid(), signal.SIGSEGV)
            return Crashing()
        return make

    worker.LOADERS["poison"] = lambda: Poison
    worker.LOADERS["die"] = lambda: Die
    worker.LOADERS["liar"] = lambda: Liar
    worker.LOADERS["zlib"] = load_zlib
    worker.main()
    """
)


@pytest.fixture(autouse=True)
def _empty_pool() -> Iterator[None]:
    POOL.close_idle()
    yield
    POOL.close_idle()


@pytest.fixture
def stand_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    script = tmp_path / "stand_in_worker.py"
    script.write_text(_STAND_IN.format(worker=str(decoder_child.WORKER)))
    monkeypatch.setattr(decoder_child, "WORKER", script)
    return script


def _zlib_round_trip(data: bytes) -> bytes:
    decomp = ISOLATED.zlib_decompressobj()
    try:
        out = decomp.decompress(zlib.compress(data))
        assert decomp.eof
        return out
    finally:
        decomp.close()


def test_a_child_is_reused_after_a_clean_finish() -> None:
    first = DecoderChild("zlib")
    first.call("decompress", zlib.compress(b"a"))
    first.close()
    assert POOL.idle_count() == 1
    second = DecoderChild("zlib")
    try:
        assert second.served_before
        assert second.call("decompress", zlib.compress(b"b")) == b"b"
    finally:
        second.close()


def test_a_child_is_not_reused_after_an_error() -> None:
    child = DecoderChild("zlib", known={("zlib", "error"): zlib.error})
    with pytest.raises(zlib.error):
        child.call("decompress", b"not zlib data")
    child.close()
    assert POOL.idle_count() == 0


def test_member_b_reads_correctly_after_member_a_damaged_the_child(
    stand_in: Path,
) -> None:
    del stand_in
    a = DecoderChild("poison")
    assert a.call("decompress", b"member A") == b"member A"
    a.close()  # a clean finish: the damaged child goes back to the pool
    assert POOL.idle_count() == 1
    # Member B's decode crashes in the damaged child; it re-runs in a fresh one.
    assert _zlib_round_trip(b"member B" * 100) == b"member B" * 100


def test_a_crash_in_a_fresh_child_is_the_verdict(stand_in: Path) -> None:
    del stand_in
    child = DecoderChild("die", (int(signal.SIGSEGV),))
    assert not child.served_before
    with pytest.raises(CorruptionError, match="crashed"):
        child.call("decompress", b"x")
    with pytest.raises(CorruptionError):
        child.call("decompress", b"x")  # every later call raises it again
    child.close()


def test_a_crash_in_a_reused_child_is_re_run_fresh(stand_in: Path) -> None:
    del stand_in
    first = DecoderChild("zlib")
    first.call("decompress", zlib.compress(b"a"))
    first.close()
    child = DecoderChild("die", (int(signal.SIGSEGV),))
    assert child.served_before
    # The re-run in a fresh child crashes too: only then is it the verdict.
    with pytest.raises(CorruptionError, match="crashed"):
        child.call("decompress", b"x")
    child.close()


def test_an_error_in_a_reused_child_comes_from_the_fresh_re_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = DecoderChild("zlib")
    first.call("decompress", zlib.compress(b"a"))
    first.close()
    spawned: list[object] = []
    real = decoder_child._Process

    def counting() -> decoder_child._Process:
        process = real()
        spawned.append(process)
        return process

    child = DecoderChild("zlib", known={("zlib", "error"): zlib.error})
    assert child.served_before
    monkeypatch.setattr(decoder_child, "_Process", counting)
    try:
        with pytest.raises(zlib.error):
            child.call("decompress", b"not zlib data")
    finally:
        child.close()
    assert len(spawned) == 1


def test_a_re_run_that_disagrees_with_delivered_output_raises(stand_in: Path) -> None:
    del stand_in
    a = DecoderChild("poison")
    a.call("decompress", b"member A")
    a.close()
    b = DecoderChild("liar")
    assert b.served_before
    try:
        assert b.call("decompress", b"abc") == b"cba"  # wrong, and already returned
        with pytest.raises(ReadError, match="does not reproduce"):
            b.call("decompress", b"def")
    finally:
        b.close()
    assert POOL.idle_count() == 0


@pytest.mark.parametrize(
    ("signum", "error"),
    [(signal.SIGKILL, ResourceLimitError), (signal.SIGTERM, ReadError)],
)
def test_an_end_from_outside_is_not_a_verdict(
    stand_in: Path, signum: int, error: type[Exception]
) -> None:
    del stand_in
    child = DecoderChild("die", (int(signum),))
    with pytest.raises(error) as caught:
        child.call("decompress", b"x")
    assert not isinstance(caught.value, CorruptionError)
    child.close()


def test_a_child_is_ended_after_its_decoder_quota(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(decoder_child, "MAX_DECODERS_PER_CHILD", 2)
    pids = []
    for _ in range(4):
        child = DecoderChild("zlib")
        process = child._lease.process
        assert process is not None
        pids.append(process.proc.pid)
        child.call("decompress", zlib.compress(b"a"))
        child.close()
    assert pids[0] == pids[1] != pids[2] == pids[3]


def test_a_long_decoder_moves_to_a_fresh_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(decoder_child, "REPLAY_LIMIT", 1000)
    first = DecoderChild("zlib")
    first.call("decompress", zlib.compress(b"a"))
    first.close()
    data = os.urandom(5000)
    packed = zlib.compress(data)
    child = DecoderChild("zlib")
    assert child.served_before
    try:
        out = b"".join(
            child.call("decompress", packed[i : i + 700])
            for i in range(0, len(packed), 700)
        )
        assert not child.served_before  # moved, and blamed directly from now on
        assert out == data
    finally:
        child.close()


def test_threads_get_children_of_their_own() -> None:
    a = DecoderChild("zlib")
    b = DecoderChild("zlib")
    try:
        assert a._lease.process is not b._lease.process
    finally:
        a.close()
        b.close()
