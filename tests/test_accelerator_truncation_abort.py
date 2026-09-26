"""rapidgzip aborts the process on a truncated DEFLATE stream; archivey must not.

rapidgzip 0.16 calls ``std::terminate`` (SIGABRT, "The bit buffer should not contain more
data than have been read from the file!") when it decodes a gzip, zlib or raw DEFLATE stream
that ends early. The throw comes from a destructor in its chunk decoder, so it happens for a
path, a real file object and an in-memory buffer alike, and no ``try/except`` in Python can
catch it. So archivey runs rapidgzip in a child process (``rapidgzip_child.py``), and the
abort becomes an archivey error.

Every case that could abort runs in a child interpreter as well, so a regression fails the
test and does not kill pytest.
"""

from __future__ import annotations

import base64
import gzip
import io
import os
import random
import signal
import subprocess
import sys
import textwrap
import threading
import zlib
from pathlib import Path
from typing import NoReturn

import pytest

from archivey.exceptions import (
    ArchiveyUsageError,
    CorruptionError,
    ReadError,
    ResourceLimitError,
    TruncatedError,
)
from archivey.internal.config import AcceleratorMode, StreamConfig
from archivey.internal.streams import codecs as codecs_module
from archivey.internal.streams import rapidgzip_child
from archivey.internal.streams.codecs import Codec, open_codec_stream
from archivey.internal.streams.rapidgzip_child import (
    RapidgzipChildReportedError,
    RapidgzipChildStream,
)
from tests.conftest import requires

pytestmark = requires("rapidgzip")

# Cut sizes from the original report. The canary test below checks that each one aborts
# raw rapidgzip on this payload.
_CUTS = [500, 1500, 3000]

_ON = StreamConfig(seekable=True, use_rapidgzip=AcceleratorMode.ON)
_OFF = StreamConfig(seekable=True, use_rapidgzip=AcceleratorMode.OFF)
_POSIX = pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")

# On Linux the child's abort names the truncation, so it maps to TruncatedError. Elsewhere
# rapidgzip may raise instead of aborting, or abort without the message (Windows), and a
# truncation it reports without its detail maps to CorruptionError (see
# ``_translate_rapidgzip``).
_TRUNCATION_ERRORS = (
    {"archivey.exceptions TruncatedError"}
    if sys.platform.startswith("linux")
    else {"archivey.exceptions TruncatedError", "archivey.exceptions CorruptionError"}
)


def _payload() -> bytes:
    """About 2.2 MB of base64 text, which compresses to about 1.6 MB: over the 1 MiB
    AUTO threshold the ``open_archive`` harness sets (the shipped one is 16 MiB)."""
    return base64.encodebytes(random.Random(0).randbytes(1_600_000))


def _compress(codec: Codec, data: bytes) -> bytes:
    if codec is Codec.GZIP:
        return gzip.compress(data)
    if codec is Codec.ZLIB:
        return zlib.compress(data)
    compressor = zlib.compressobj(wbits=-15)
    return compressor.compress(data) + compressor.flush()


def _write(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def _run(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    # faulthandler on, as CI runs and as a user may: the decoder child inherits it, and
    # on SIGABRT it writes a stack dump (from 3.14 with the C stack, several KiB) after
    # rapidgzip's abort message.
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script), *args],
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "PYTHONFAULTHANDLER": "1"},
    )


def _assert_clean_exit(
    proc: subprocess.CompletedProcess[str], expected: str | set[str]
) -> None:
    # An abort kills the child before it prints its result line: a negative return code
    # (SIGABRT is -6) on POSIX, exit code 3 on Windows.
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr[-2000:])
    allowed = {expected} if isinstance(expected, str) else expected
    assert proc.stdout.strip().splitlines()[-1] in allowed, proc.stdout


_RAW_RAPIDGZIP = """
import sys, rapidgzip
stream = rapidgzip.open(sys.argv[1], parallelization=0)
outcome = "CLEAN"
try:
    while stream.read(1 << 20):
        pass
except Exception as exc:
    outcome = "RAISED " + type(exc).__name__
stream.close()
print(outcome)
"""


@pytest.mark.parametrize("cut", _CUTS)
def test_raw_rapidgzip_aborts_on_truncated_gzip(tmp_path: Path, cut: int) -> None:
    """Canary: the upstream hazard the child process guards against still exists.

    On Linux, rapidgzip 0.16 aborts on each of these inputs. The macOS build raises an
    exception on them instead ("Unexpected end of file when getting block ..."), so there
    the canary accepts an abort or a raise. On every platform, rapidgzip must not decode
    the truncated input without an error. If a later rapidgzip stops aborting on Linux,
    this test fails: the signal that rapidgzip may be safe to run in-process again.
    """
    path = _write(tmp_path, "cut.gz", gzip.compress(_payload())[:-cut])
    proc = _run(_RAW_RAPIDGZIP, str(path))
    if proc.returncode != 0:
        return  # aborted: the hazard is present
    assert not sys.platform.startswith("linux"), ("no abort", proc.stdout, proc.stderr)
    assert proc.stdout.strip().startswith("RAISED "), (proc.stdout, proc.stderr)


# Each access pattern a caller can use on a member: read straight through; read part,
# seek back and read again; read to the end first, then seek back; seek to the end; read
# on past a caught error, then seek back. Each must raise an archivey error, and must not
# abort. After the child has died, every later call raises the same error.
_PATTERNS = """
def straight(stream):
    while stream.read(1 << 20):
        pass

def back(stream):
    stream.read(1 << 20)
    stream.seek(0)
    straight(stream)

def end_then_back(stream):
    try:
        straight(stream)
    except Exception:
        pass
    stream.seek(1000)
    straight(stream)

def to_end(stream):
    stream.seek(-100, 2)
    straight(stream)

def past_error_then_back(stream):
    for _ in range(2):
        try:
            straight(stream)
        except Exception:
            pass
    stream.seek(0)
    straight(stream)

def read_all_past_error_then_back(stream):
    for _ in range(2):
        try:
            stream.read()
        except Exception:
            pass
    stream.seek(0)
    stream.read()

PATTERNS = {
    "straight": straight,
    "back": back,
    "end_then_back": end_then_back,
    "to_end": to_end,
    "past_error_then_back": past_error_then_back,
    "read_all_past_error_then_back": read_all_past_error_then_back,
}
"""

_ACCESS = [
    "straight",
    "back",
    "end_then_back",
    "to_end",
    "past_error_then_back",
    "read_all_past_error_then_back",
]

_OPEN_ARCHIVE = (
    _PATTERNS
    + """
import sys
import archivey
from archivey.internal.streams import codecs

# The shipped AUTO threshold is 16 MiB; lowered so AUTO takes these inputs to the child.
codecs.RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE = 1 << 20

path, mode, pattern = sys.argv[1], sys.argv[2], PATTERNS[sys.argv[3]]
source = path if mode == "path" else open(path, "rb")
try:
    with archivey.open_archive(source, seekable_members=True) as reader:
        for member in reader.members():
            with reader.open(member) as stream:
                pattern(stream)
    print("OK")
except Exception as exc:
    print(type(exc).__module__, type(exc).__name__)
finally:
    if mode != "path":
        source.close()
"""
)


@pytest.mark.parametrize("access", _ACCESS)
@pytest.mark.parametrize("mode", ["path", "stream"])
@pytest.mark.parametrize("cut", _CUTS)
def test_truncated_gzip_with_seekable_members_raises_truncated(
    tmp_path: Path, cut: int, mode: str, access: str
) -> None:
    """The reported case: ``seekable_members=True`` on a truncated ``.gz`` killed Python.

    The accelerator is AUTO here, as in the report, with the AUTO threshold lowered to
    the 1 MiB it had then, so these inputs still reach rapidgzip.
    """
    path = _write(tmp_path, "cut.gz", gzip.compress(_payload())[:-cut])
    proc = _run(_OPEN_ARCHIVE, str(path), mode, access)
    _assert_clean_exit(proc, _TRUNCATION_ERRORS)


_OPEN_CODEC = (
    _PATTERNS
    + """
import io, sys
from archivey.internal.config import AcceleratorMode, StreamConfig
from archivey.internal.streams.codecs import Codec, open_codec_stream

path, codec, mode = sys.argv[1], Codec(sys.argv[2]), sys.argv[3]
pattern = PATTERNS[sys.argv[4]]
config = StreamConfig(seekable=True, use_rapidgzip=AcceleratorMode.ON)
if mode == "path":
    source = path
elif mode == "file":
    source = open(path, "rb")
else:
    source = io.BytesIO(open(path, "rb").read())
try:
    with open_codec_stream(codec, source, config=config) as stream:
        pattern(stream)
    print("OK")
except Exception as exc:
    print(type(exc).__module__, type(exc).__name__)
finally:
    if mode != "path":
        source.close()
"""
)


@pytest.mark.parametrize("access", _ACCESS)
@pytest.mark.parametrize("mode", ["path", "file", "bytesio"])
@pytest.mark.parametrize("codec", [Codec.GZIP, Codec.ZLIB, Codec.DEFLATE])
def test_truncated_deflate_family_with_accelerator_on_raises_truncated(
    tmp_path: Path, codec: Codec, mode: str, access: str
) -> None:
    """Every rapidgzip codec, forced ON: the abort is in DEFLATE decoding, not in gzip.

    A ZIP or 7z member reaches the deflate and zlib codecs through a bounded view, so a
    member whose data is cut short is this case.
    """
    data = _compress(codec, _payload())[:-1500]
    path = _write(tmp_path, f"cut.{codec.value}", data)
    proc = _run(_OPEN_CODEC, str(path), codec.value, mode, access)
    _assert_clean_exit(proc, _TRUNCATION_ERRORS)


# --- the child stream serves the codec layer --------------------------------------------


@pytest.mark.parametrize("chunk", [4096, 1 << 20])
@pytest.mark.parametrize("cut", _CUTS)
@pytest.mark.parametrize("codec", [Codec.GZIP, Codec.ZLIB, Codec.DEFLATE])
def test_what_a_cut_stream_delivers_before_the_abort_is_a_correct_prefix(
    tmp_path: Path, codec: Codec, cut: int, chunk: int
) -> None:
    """The bytes read before the child aborts are the payload's own: the read-ahead
    buffer never serves data from past the cut or out of order. How many there are is
    rapidgzip's to decide (it can abort before the first read returns), so this pins
    only that they are right, and that the error is a truncation or corruption."""
    payload = _payload()
    path = _write(tmp_path, f"cut.{codec.value}", _compress(codec, payload)[:-cut])
    got = bytearray()
    with open_codec_stream(codec, str(path), config=_ON) as stream:
        with pytest.raises((TruncatedError, CorruptionError)):
            while block := stream.read(chunk):
                got += block
    assert len(got) < len(payload)
    assert payload.startswith(got)


@pytest.mark.parametrize("codec", [Codec.GZIP, Codec.ZLIB])
def test_a_large_cut_stream_delivers_a_correct_prefix_before_the_abort(
    tmp_path: Path, codec: Codec
) -> None:
    """On a small cut stream rapidgzip usually aborts before any data comes back, which
    leaves the prefix check above nothing to check. On about 32 MB it returned 22 to
    31 MB first (4 CPUs), so the read-ahead's bookkeeping is exercised here."""
    payload = base64.encodebytes(random.Random(32).randbytes(24_000_000))
    path = _write(tmp_path, f"cut.{codec.value}", _compress(codec, payload)[:-500])
    got = bytearray()
    with open_codec_stream(codec, str(path), config=_ON) as stream:
        with pytest.raises((TruncatedError, CorruptionError)):
            while block := stream.read(4096 if codec is Codec.GZIP else 10_000):
                got += block
    assert got
    assert payload.startswith(got)


@pytest.mark.parametrize("codec", [Codec.GZIP, Codec.ZLIB, Codec.DEFLATE])
@pytest.mark.parametrize("mode", ["path", "bytesio"])
def test_the_child_stream_reads_and_seeks_like_stdlib(
    tmp_path: Path, codec: Codec, mode: str
) -> None:
    payload = _payload()
    compressed = _compress(codec, payload)
    path = _write(tmp_path, "valid.bin", compressed)
    source = str(path) if mode == "path" else io.BytesIO(compressed)
    with open_codec_stream(codec, source, config=_ON) as stream:
        assert stream.read(1000) == payload[:1000]
        assert stream.tell() == 1000
        middle = len(payload) // 2
        assert stream.seek(middle) == middle
        assert stream.read(64) == payload[middle : middle + 64]
        assert stream.seek(-10, io.SEEK_END) == len(payload) - 10
        assert stream.read() == payload[-10:]
        assert stream.seek(5) == 5
        assert stream.read() == payload[5:]
        assert stream.tell() == len(payload)


def _child_stream(stream: object) -> RapidgzipChildStream:
    inner = stream
    while not isinstance(inner, RapidgzipChildStream):
        inner = getattr(inner, "_inner")
    return inner


def _has_child_stream(stream: object) -> bool:
    inner: object = stream
    while inner is not None:
        if isinstance(inner, RapidgzipChildStream):
            return True
        inner = getattr(inner, "_inner", None)
    return False


def test_rewind_offset_comes_from_the_childs_index() -> None:
    payload = _payload()
    with open_codec_stream(
        Codec.ZLIB, io.BytesIO(zlib.compress(payload)), config=_ON
    ) as stream:
        child = _child_stream(stream)
        stream.read()
        offset = child.nearest_resume_offset(len(payload) // 2)
        assert offset is not None and 0 <= offset <= len(payload) // 2


def test_bzip2_stays_in_process() -> None:
    """rapidgzip's bzip2 decoder has not been seen to abort; it keeps its in-process path."""
    import bz2

    with open_codec_stream(
        Codec.BZIP2,
        io.BytesIO(bz2.compress(b"x" * 1000)),
        config=StreamConfig(seekable=True, use_indexed_bzip2=AcceleratorMode.ON),
    ) as stream:
        inner = stream
        while not isinstance(inner, codecs_module._AcceleratorStream):
            inner = getattr(inner, "_inner")
        assert stream.read() == b"x" * 1000


# --- the caller's source ----------------------------------------------------------------


class _FailingSource(io.BytesIO):
    """The caller's own stream, failing on a read that starts past its first 64 KiB.

    Reads near the end still work, for the gzip ISIZE probe at open.
    """

    def __init__(self, data: bytes, exc: BaseException) -> None:
        super().__init__(data)
        self._exc = exc

    def read(self, size: int | None = -1, /) -> bytes:
        if 64 * 1024 < self.tell() < len(self.getbuffer()) - 64 * 1024:
            raise self._exc
        return super().read(size)


@pytest.mark.parametrize("error", [RuntimeError, EOFError])
@pytest.mark.parametrize("codec", [Codec.GZIP, Codec.ZLIB, Codec.DEFLATE])
def test_an_exception_from_the_callers_source_reaches_the_caller_unchanged(
    codec: Codec, error: type[Exception]
) -> None:
    """The child's reads of a stream source are served here; the caller's own exception
    from that source is raised as itself, not as a verdict on the data.

    ``EOFError`` is a type the codecs' stdlib translation maps to ``TruncatedError``
    (and a type a network file object raises on a dropped connection), so it shows the
    source's exception bypasses that translation too."""
    failure = error("caller source failed")
    source = _FailingSource(_compress(codec, _payload()), failure)
    with open_codec_stream(codec, source, config=_ON) as stream:
        with pytest.raises(error) as info:
            while stream.read(1 << 16):
                pass
        assert info.value is failure
        # The child was told its input ended where the source failed. Whatever it does
        # with that, it is not reported as a verdict on the data.
        with pytest.raises((error, ReadError)) as later:
            while stream.read(1 << 16):
                pass
        assert not isinstance(later.value, (CorruptionError, TruncatedError))


def test_an_interrupt_from_the_callers_source_leaves_the_stream_unusable() -> None:
    source = _FailingSource(_compress(Codec.ZLIB, _payload()), KeyboardInterrupt())
    with open_codec_stream(Codec.ZLIB, source, config=_ON) as stream:
        with pytest.raises(KeyboardInterrupt):
            while stream.read(1 << 16):
                pass
        with pytest.raises(ArchiveyUsageError, match="interrupted"):
            stream.read(1)


# --- how a child death is reported ------------------------------------------------------


@_POSIX
@pytest.mark.parametrize(
    ("sig", "expected"),
    [
        ("SIGKILL", ResourceLimitError),
        ("SIGTERM", ReadError),
        ("SIGSEGV", CorruptionError),
        ("SIGABRT", CorruptionError),
    ],
)
def test_a_child_death_is_reported_by_how_it_ended(
    tmp_path: Path, sig: str, expected: type[Exception]
) -> None:
    """Only a crash is a verdict on the data; SIGKILL is most often the OOM killer."""
    payload = _payload()
    path = _write(tmp_path, "valid.gz", gzip.compress(payload))
    with open_codec_stream(Codec.GZIP, str(path), config=_ON) as stream:
        child = _child_stream(stream)
        assert stream.read(10) == payload[:10]
        assert child._proc is not None
        os.kill(child._proc.pid, getattr(signal, sig))
        child._proc.wait()
        with pytest.raises(expected) as first:
            stream.seek(0)
        assert type(first.value) is expected
        assert sig in str(first.value)
        # Every later call raises the same error again.
        with pytest.raises(expected) as second:
            stream.read(1)
        assert type(second.value) is expected
        assert str(second.value) == str(first.value)


@pytest.mark.parametrize("where", ["before", "after"])
def test_the_abort_message_is_found_among_other_output(
    tmp_path: Path, where: str
) -> None:
    """Output before or after the abort message does not hide it.

    With ``PYTHONFAULTHANDLER`` set, Python 3.14 wrote about 6.5 KiB of thread and C
    stacks after rapidgzip's ``what():`` line, and a search of the last 4 KiB missed
    it: every truncation read as ``CorruptionError``. A window at the start of the file
    would miss it the same way behind earlier output, so both sides get more than a
    MiB.
    """
    abort = (
        b"terminate called after throwing an instance of 'std::logic_error'\n"
        b"  what():  The bit buffer should not contain more data than have been read "
        b"from the file!\nFatal Python error: Aborted\n\n"
    )
    other = b'  Binary file "/lib/x86_64-linux-gnu/libc.so.6", at +0x9caa4\n' * 40_000
    assert len(other) > 2 << 20
    content = abort + other if where == "after" else other + abort
    with (tmp_path / "stderr").open("w+b") as stderr:
        stderr.write(content)
        truncated, reason = rapidgzip_child._scan_stderr(stderr)
    assert truncated
    assert reason.startswith(": The bit buffer")


@pytest.mark.parametrize("inside", [1, 30, 62])
def test_the_abort_message_is_found_across_a_split_long_line(
    tmp_path: Path, inside: int
) -> None:
    """A line longer than the scan's line cap is read in pieces; the abort message that
    straddles a split, with ``inside`` of its bytes in the first piece, is still found."""
    marker = rapidgzip_child._TRUNCATION_ABORT
    filler = b"x" * (rapidgzip_child._STDERR_LINE_LIMIT - inside)
    with (tmp_path / "stderr").open("w+b") as stderr:
        stderr.write(filler + marker + b" from the file!\n")
        truncated, _ = rapidgzip_child._scan_stderr(stderr)
    assert truncated


def test_the_last_what_line_gives_the_reason(tmp_path: Path) -> None:
    """Earlier output holding ``what():`` does not stand in for the abort's own line."""
    content = (
        b"  what():  an earlier message\n"
        b"terminate called after throwing an instance of 'std::logic_error'\n"
        b"  what():  The bit buffer should not contain more data than have been read "
        b"from the file!\n"
    )
    with (tmp_path / "stderr").open("w+b") as stderr:
        stderr.write(content)
        truncated, reason = rapidgzip_child._scan_stderr(stderr)
    assert truncated
    assert reason.startswith(": The bit buffer")


@pytest.mark.parametrize(
    "message",
    [
        # Leaked untranslated from the open-time probe of a truncated gzip.
        "Next block offset index is out of sync!",
        "an internal message no allowlist entry names",
    ],
)
def test_any_runtime_error_rapidgzip_raised_is_translated(message: str) -> None:
    """A RuntimeError the child reported maps to CorruptionError; the same RuntimeError
    from anywhere else (the caller's source) is left alone."""
    for codec in (
        codecs_module.GzipCodec(),
        codecs_module.DeflateCodec(),
        codecs_module.ZlibCodec(),
    ):
        reported = rapidgzip_child._reported_error(
            f"RuntimeError\n\n{message}".encode()
        )
        assert isinstance(codec._translate_accelerator(reported), CorruptionError)
        assert codec._translate_accelerator(RuntimeError(message)) is None


def test_an_eof_error_the_child_reported_is_still_translated() -> None:
    """The mark that keeps the caller's source exception unchanged is on that exception
    only: an ``EOFError`` rapidgzip raised in the child is still a truncation."""
    for codec in (
        codecs_module.GzipCodec(),
        codecs_module.DeflateCodec(),
        codecs_module.ZlibCodec(),
    ):
        reported = rapidgzip_child._reported_error(b"EOFError\n\nend of stream")
        assert isinstance(codec._translate_accelerator(reported), TruncatedError)


def test_an_unknown_exception_from_the_child_propagates_unmapped() -> None:
    exc = rapidgzip_child._reported_error(b"KeyError\n\n'x'")
    assert isinstance(exc, RapidgzipChildReportedError)
    assert codecs_module.GzipCodec()._translate_accelerator(exc) is None
    missing = rapidgzip_child._reported_error(b"FileNotFoundError\n2\nno such file")
    assert isinstance(missing, FileNotFoundError) and missing.errno == 2


def test_close_reaps_the_child_and_later_calls_raise(tmp_path: Path) -> None:
    path = _write(tmp_path, "valid.gz", gzip.compress(_payload()))
    stream = open_codec_stream(Codec.GZIP, str(path), config=_ON)
    child = _child_stream(stream)
    proc = child._proc
    assert proc is not None
    # Two sequential reads: the second fills the read-ahead buffer, so a read after
    # close() could be answered from it without reaching the child.
    child.read(10)
    child.read(10)
    assert child._buffer_at < len(child._buffer)
    stream.close()
    assert proc.returncode is not None
    for call in (lambda: child.read(1), lambda: child.read(), child.tell):
        with pytest.raises(ValueError, match="closed file"):
            call()


def test_an_interrupted_request_leaves_the_stream_unusable(tmp_path: Path) -> None:
    path = _write(tmp_path, "valid.gz", gzip.compress(_payload()))
    with open_codec_stream(Codec.GZIP, str(path), config=_ON) as stream:
        child = _child_stream(stream)
        real = child._read_frame

        def interrupted() -> tuple[int, int, bytes] | None:
            raise KeyboardInterrupt

        child._read_frame = interrupted
        with pytest.raises(KeyboardInterrupt):
            stream.read(10)
        child._read_frame = real
        with pytest.raises(ArchiveyUsageError):
            stream.read(10)


# --- where no child can run -------------------------------------------------------------


def test_without_a_child_auto_uses_stdlib_and_on_refuses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A frozen application has no interpreter to run the worker. AUTO decodes with the
    standard library; ON, which asked for rapidgzip, is refused rather than run in-process."""
    monkeypatch.setattr(codecs_module, "rapidgzip_child_available", lambda: False)
    # Low enough that AUTO would otherwise pick rapidgzip for this input.
    monkeypatch.setattr(codecs_module, "RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE", 1 << 20)
    payload = _payload()
    path = _write(tmp_path, "valid.gz", gzip.compress(payload))
    auto = StreamConfig(seekable=True, use_rapidgzip=AcceleratorMode.AUTO)
    with open_codec_stream(Codec.GZIP, str(path), config=auto) as stream:
        assert stream.read() == payload
    with pytest.raises(ResourceLimitError, match="use_rapidgzip"):
        open_codec_stream(Codec.GZIP, str(path), config=_ON)


def _refuse(*args: object, **kwargs: object) -> NoReturn:
    raise OSError(11, "Resource temporarily unavailable")


# What can refuse a child at open: the spawn itself (a process cap, RLIMIT_NPROC, EMFILE)
# and the temporary file its stderr goes to (no writable temporary directory).
_START_FAILURES = {
    "popen": ("subprocess", "Popen"),
    "tempfile": ("tempfile", "TemporaryFile"),
    "no-interpreter": None,
}


def _break_child_start(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, how: str
) -> None:
    target = _START_FAILURES[how]
    if target is None:
        monkeypatch.setattr(sys, "executable", str(tmp_path / "no-such-python"))
    else:
        module, name = target
        monkeypatch.setattr(getattr(rapidgzip_child, module), name, _refuse)


@pytest.mark.parametrize("how", sorted(_START_FAILURES))
def test_a_child_that_cannot_start_is_a_resource_limit_under_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, how: str
) -> None:
    """ON asked for rapidgzip, so a refused child is an error, not a quiet fallback."""
    path = _write(tmp_path, "valid.gz", gzip.compress(_payload()))
    _break_child_start(monkeypatch, tmp_path, how)
    with pytest.raises(ResourceLimitError, match="cannot start"):
        open_codec_stream(Codec.GZIP, str(path), config=_ON)


@pytest.mark.parametrize("mode", ["path", "bytesio"])
@pytest.mark.parametrize("codec", [Codec.GZIP, Codec.ZLIB, Codec.DEFLATE])
@pytest.mark.parametrize("how", sorted(_START_FAILURES))
def test_a_child_that_cannot_start_falls_back_to_stdlib_under_auto(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, how: str, codec: Codec, mode: str
) -> None:
    """AUTO decodes with the standard library when no child starts, as it does when
    rapidgzip is absent: valid data reads, and cut or damaged data raises as the stdlib
    backend reports it (translated, from the original start of the source)."""
    monkeypatch.setattr(codecs_module, "RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE", 1 << 20)
    payload = _payload()
    data = _compress(codec, payload)
    damaged = bytearray(data)
    damaged[len(data) // 2 : len(data) // 2 + 64] = bytes(64)
    cases: list[tuple[bytes, type[Exception] | None]] = [
        (data, None),
        (data[:-1500], TruncatedError),
    ]
    if codec is not Codec.DEFLATE:
        # Raw deflate carries no checksum: the stdlib can decode damage it cannot see.
        cases.append((bytes(damaged), CorruptionError))
    _break_child_start(monkeypatch, tmp_path, how)
    for i, (content, error) in enumerate(cases):
        path = _write(tmp_path, f"{i}.{codec.value}", content)
        source: str | io.BytesIO = str(path) if mode == "path" else io.BytesIO(content)
        config = StreamConfig(
            seekable=True,
            use_rapidgzip=AcceleratorMode.AUTO,
            compressed_input_size=len(content),
            expected_decompressed_size=(None if codec is Codec.GZIP else len(payload)),
        )
        with open_codec_stream(codec, source, config=config) as stream:
            assert not _has_child_stream(stream)
            if error is None:
                assert stream.read() == payload
            else:
                with pytest.raises(error):
                    stream.read()


def test_background_reads_of_a_stream_source_are_served(tmp_path: Path) -> None:
    """rapidgzip's own threads read the source ahead; those reads arrive between
    requests and must be served by the next one, not deadlock."""
    payload = os.urandom(6 * 1024 * 1024)  # incompressible: many source reads
    compressed = gzip.compress(payload, compresslevel=1)
    done = threading.Event()
    result: list[bytes] = []

    def run() -> None:
        with open_codec_stream(Codec.GZIP, io.BytesIO(compressed), config=_ON) as s:
            parts = []
            while chunk := s.read(4096 * 7):
                parts.append(chunk)
            result.append(b"".join(parts))
        done.set()

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    assert done.wait(60), "reading through the child stalled"
    assert result == [payload]


@pytest.mark.parametrize("seed", range(6))
def test_reads_and_seeks_in_any_order_match_the_payload(seed: int) -> None:
    """The read-ahead buffer and the kept position agree with the data for any mix of
    small reads, large reads, and seeks inside and outside the buffer."""
    payload = _payload()
    rng = random.Random(seed)
    with open_codec_stream(
        Codec.ZLIB, io.BytesIO(zlib.compress(payload)), config=_ON
    ) as stream:
        pos = 0
        for _ in range(300):
            action = rng.random()
            if action < 0.5:
                size = rng.choice([1, 17, 512, 4096, 70_000, 1_500_000])
                data = stream.read(size)
                assert data == payload[pos : pos + size]
                pos += len(data)
            elif action < 0.7:
                delta = rng.randint(-5000, 5000)
                pos = min(max(pos + delta, 0), len(payload))
                assert stream.seek(pos - stream.tell(), io.SEEK_CUR) == pos
            elif action < 0.9:
                pos = rng.randrange(len(payload))
                assert stream.seek(pos) == pos
            else:
                back = rng.randint(0, 100)
                pos = len(payload) - back
                assert stream.seek(-back, io.SEEK_END) == pos
            assert stream.tell() == pos
