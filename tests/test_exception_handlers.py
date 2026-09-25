"""Pins from the review of blind ``except`` handlers in ``src/``.

Each test names the handler it pins and the exception a caller now sees. The review
record is ``review/exception-catchalls/``; the house rules it produced are
``dev-docs/topics/exception-handlers.md``.
"""

from __future__ import annotations

import io
import subprocess
import sys
import textwrap
import zipfile
from typing import TYPE_CHECKING, NoReturn

import pytest

import archivey
from archivey.exceptions import CorruptionError, TruncatedError
from archivey.internal.reader_state import LifecycleState
from archivey.internal.streams.codecs import _AcceleratorStream, _TrappingSource
from archivey.internal.streams.verify import VerifyingStream
from tests.conftest import requires

if TYPE_CHECKING:
    from _typeshed import WriteableBuffer


def _zip_with_corrupt_deflate_body() -> bytes:
    """A one-member ZIP whose deflate body fails to decode (zlib error -3)."""
    data = b"".join(b"line %d of text, some words here\n" % i for i in range(20000))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("a.txt", data)
    raw = bytearray(buf.getvalue())
    raw[30 + len("a.txt") + 38] ^= 0xFF  # inside the first block's Huffman tables
    return bytes(raw)


@pytest.mark.parametrize("how", ["read()", "read(n)"])
def test_corrupt_member_reads_as_corrupt_through_either_read(how: str) -> None:
    """verify.py ``_read_sized_all``: a raw decoder error is no longer relabelled.

    It used to become ``TruncatedError`` on ``read()`` while the same member raised
    ``CorruptionError`` on ``read(n)``, because only the bounded path let the
    translator see the decoder's error.
    """
    blob = _zip_with_corrupt_deflate_body()
    with (
        archivey.open_archive(io.BytesIO(blob)) as reader,
        reader.open(reader.get("a.txt")) as stream,
        pytest.raises(CorruptionError) as info,
    ):
        if how == "read()":
            stream.read()
        else:
            while stream.read(4096):
                pass
    assert not isinstance(info.value, TruncatedError)


class _ExactThenFails(io.BytesIO):
    """Delivers its bytes, then raises ``error`` on the read that probes past them."""

    def __init__(self, data: bytes, error: BaseException) -> None:
        super().__init__(data)
        self._size = len(data)
        self._error = error

    def read(self, n: int | None = -1) -> bytes:
        if self.tell() >= self._size:
            raise self._error
        return super().read(n)


@pytest.mark.parametrize("error", [OSError("disk gone"), MemoryError()])
def test_overrun_probe_lets_resource_errors_through(error: BaseException) -> None:
    """verify.py ``_probe_past_declared``: an I/O or memory failure is not "no more data"."""
    stream = VerifyingStream(_ExactThenFails(b"x" * 10, error), {}, expected_size=10)
    with pytest.raises(type(error)):
        stream.read()
    stream.close()


def test_overrun_probe_still_reads_an_opaque_decoder_error_as_the_end() -> None:
    """The narrowed probe keeps its reason: an opaque decoder error past the end is EOF."""
    inner = _ExactThenFails(b"x" * 10, RuntimeError("std::exception"))
    with VerifyingStream(inner, {}, expected_size=10) as stream:
        assert stream.read() == b"x" * 10


@requires("rapidgzip")
def test_bzip2_accelerator_traps_a_failing_caller_source() -> None:
    """codecs.py: the bzip2 accelerator reads a caller's stream through the trap too.

    Without it, the caller's ``OSError`` crossed into rapidgzip's C++ callback and
    aborted the interpreter (``std::invalid_argument``), so this runs in a child.
    """
    code = textwrap.dedent(
        """
        import bz2, io, random
        import archivey
        random.seed(0)
        blob = bz2.compress(random.randbytes(3_000_000))

        class Flaky(io.BytesIO):
            armed = False
            def read(self, n=-1):
                if self.armed and self.tell() > len(blob) // 2:
                    raise OSError("disk gone")
                return super().read(n)

        source = Flaky(blob)
        stream = archivey.open_stream(source, seekable=True)
        source.armed = True
        try:
            stream.read()
        except OSError as exc:
            print("raised", exc)
        stream.close()
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120
    )
    assert proc.returncode == 0, proc.stderr
    assert "raised disk gone" in proc.stdout


class _FailingSource(io.BytesIO):
    """A caller's stream whose ``read`` fails; the shim parks the failure."""

    def read(self, n: int | None = -1) -> bytes:
        raise OSError("disk gone")


def _accelerator_over_failing_source(
    raised: BaseException,
) -> tuple[_AcceleratorStream, _TrappingSource]:
    """An ``_AcceleratorStream`` whose decoder reads the real shim, gets its EOF-shaped
    answer, then raises ``raised`` from every read / readinto / seek."""
    trap = _TrappingSource(_FailingSource())

    class _Accel(io.BytesIO):
        def _fail(self) -> NoReturn:
            assert trap.read(16) == b""  # the shim parks the OSError
            raise raised

        def read(self, n: int | None = -1) -> bytes:
            self._fail()

        def readinto(self, b: WriteableBuffer, /) -> int:
            self._fail()

        def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
            self._fail()

    return _AcceleratorStream(_Accel(), trap=trap), trap


def _call(stream: _AcceleratorStream, how: str) -> None:
    if how == "read":
        stream.read(10)
    elif how == "readinto":
        stream.readinto(bytearray(10))
    else:
        stream.seek(5)


@pytest.mark.parametrize("how", ["read", "readinto", "seek"])
def test_parked_source_fault_wins_over_the_accelerator_error(how: str) -> None:
    """codecs.py ``_AcceleratorStream``: when the accelerator raises its own error on
    the shim's EOF-shaped answer, the parked source fault is what propagates."""
    stream, _ = _accelerator_over_failing_source(RuntimeError("Unexpected end of file"))
    with pytest.raises(OSError, match="disk gone") as info:
        _call(stream, how)
    assert isinstance(info.value.__context__, RuntimeError)
    stream.close()


@pytest.mark.parametrize("how", ["read", "readinto", "seek"])
def test_an_interrupt_is_not_replaced_by_a_parked_fault(how: str) -> None:
    """The parked fault wins only over an ``Exception``: an interrupt propagates as
    itself, and the fault stays parked for the next boundary."""
    stream, trap = _accelerator_over_failing_source(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        _call(stream, how)
    assert isinstance(trap.trapped, OSError)
    stream.close()


def test_fault_parked_during_accelerator_open_raises_at_open() -> None:
    """codecs.py ``_open_accelerator``: a fault seen while the decoder opens (through
    the shim's ``seekable``/``tell``) raises there, not on a later read."""
    from archivey.internal.streams.codecs import _open_accelerator

    class _Broken(io.BytesIO):
        def seekable(self) -> bool:
            raise KeyboardInterrupt

    def _open_ok(source: io.RawIOBase, parallelization: int) -> io.BytesIO:
        source.seekable()  # parked by the shim; the open itself succeeds
        return io.BytesIO(b"data")

    def _open_fails(source: io.RawIOBase, parallelization: int) -> io.BytesIO:
        source.seekable()
        raise ValueError("has no valid fileno")

    with pytest.raises(KeyboardInterrupt):
        _open_accelerator(_open_ok, _Broken())
    with pytest.raises(KeyboardInterrupt) as info:
        _open_accelerator(_open_fails, _Broken())
    assert isinstance(info.value.__context__, ValueError)

    # An interrupt from the open itself is never replaced by a different parked fault.
    class _FailingSeekable(io.BytesIO):
        def seekable(self) -> bool:
            raise OSError("disk gone")

    def _open_interrupted(source: io.RawIOBase, parallelization: int) -> io.BytesIO:
        source.seekable()
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        _open_accelerator(_open_interrupted, _FailingSeekable())


def test_interrupted_teardown_still_marks_the_lifecycle_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """base_reader.py ``_maybe_teardown``: teardown is never retried, so an interrupt
    in the backend's close still leaves the lifecycle at TEARDOWN_COMPLETE.

    The lifecycle assertion is on internal state on purpose: the fix is bookkeeping with
    no external effect today (the retry is refused by the claim flag either way), so
    the state is the only thing that can pin it."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.txt", b"a")
    reader = archivey.open_archive(io.BytesIO(buf.getvalue()))

    calls = 0

    def _interrupted() -> None:
        nonlocal calls
        calls += 1
        raise KeyboardInterrupt

    monkeypatch.setattr(reader, "_close_archive", _interrupted)
    with pytest.raises(KeyboardInterrupt):
        reader.close()
    reader.close()  # quiet; the claim flag, not the lifecycle, refuses a second run
    assert calls == 1
    assert reader._state.lifecycle is LifecycleState.TEARDOWN_COMPLETE
