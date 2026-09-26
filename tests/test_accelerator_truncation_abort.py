"""A truncated DEFLATE stream must not reach rapidgzip, which aborts the process on it.

rapidgzip 0.16 calls ``std::terminate`` (SIGABRT, "The bit buffer should not contain more
data than have been read from the file!") when it decodes a gzip, zlib or raw DEFLATE stream
that ends early. The throw comes from a destructor in its chunk decoder, so it happens for a
path, a real file object and an in-memory buffer alike, and no ``try/except`` in Python can
catch it. So archivey decodes with the stdlib engine, and opens rapidgzip only for a
backward seek, on input the stdlib engine has decoded to a clean end
(``_StdlibUntilRandomAccess`` in ``codecs.py``; ``dev-docs/known-issues.md``).

Every case that could abort runs in a child interpreter, so a regression fails the test
and does not kill pytest.
"""

from __future__ import annotations

import base64
import gzip
import io
import random
import subprocess
import sys
import textwrap
import zlib
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

import pytest

from archivey.exceptions import CorruptionError
from archivey.internal.config import AcceleratorMode, StreamConfig
from archivey.internal.streams import codecs as codecs_module
from archivey.internal.streams.codecs import Codec, CodecSource, open_codec_stream
from tests.conftest import requires

pytestmark = requires("rapidgzip")

# Cut sizes from the original report. The canary test below checks that each one aborts
# raw rapidgzip on this payload.
_CUTS = [500, 1500, 3000]

_ON = StreamConfig(seekable=True, use_rapidgzip=AcceleratorMode.ON)


def _payload() -> bytes:
    """About 2.2 MB of base64 text, which compresses to about 1.6 MB: over the 1 MiB
    compressed size where AUTO starts to select rapidgzip."""
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
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script), *args],
        capture_output=True,
        text=True,
        timeout=120,
    )


def _assert_clean_exit(proc: subprocess.CompletedProcess[str], expected: str) -> None:
    # An abort kills the child before it prints its result line: a negative return code
    # (SIGABRT is -6) on POSIX, exit code 3 on Windows.
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr[-2000:])
    assert proc.stdout.strip().splitlines()[-1] == expected, proc.stdout


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
    """Canary: the upstream hazard this module guards against still exists.

    On Linux, rapidgzip 0.16 aborts on each of these inputs. The macOS build raises an
    exception on them instead ("Unexpected end of file when getting block ..."), so there
    the canary accepts an abort or a raise. On every platform, rapidgzip must not decode
    the truncated input without an error. If a later rapidgzip stops aborting on Linux,
    this test fails: the signal that rapidgzip may be safe to use without a stdlib pass
    first.
    """
    path = _write(tmp_path, "cut.gz", gzip.compress(_payload())[:-cut])
    proc = _run(_RAW_RAPIDGZIP, str(path))
    if proc.returncode != 0:
        return  # aborted: the hazard is present
    assert not sys.platform.startswith("linux"), ("no abort", proc.stdout, proc.stderr)
    assert proc.stdout.strip().startswith("RAISED "), (proc.stdout, proc.stderr)


# Each access pattern a caller can use on a member: read straight through; read part,
# seek back and read again (the seek that engages rapidgzip on complete input); read to
# the end first, then seek back; seek to the end. Each must raise the error that the
# default open raises, and must not abort.
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

PATTERNS = {"straight": straight, "back": back, "end_then_back": end_then_back, "to_end": to_end}
"""

_ACCESS = ["straight", "back", "end_then_back", "to_end"]

_OPEN_ARCHIVE = (
    _PATTERNS
    + """
import sys
import archivey

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

    The accelerator is AUTO here, as in the report.
    """
    path = _write(tmp_path, "cut.gz", gzip.compress(_payload())[:-cut])
    proc = _run(_OPEN_ARCHIVE, str(path), mode, access)
    _assert_clean_exit(proc, "archivey.exceptions TruncatedError")


_OPEN_CODEC = (
    _PATTERNS
    + """
import sys
from archivey.internal.config import AcceleratorMode, StreamConfig
from archivey.internal.streams.codecs import Codec, open_codec_stream

path, codec, mode = sys.argv[1], Codec(sys.argv[2]), sys.argv[3]
pattern = PATTERNS[sys.argv[4]]
config = StreamConfig(seekable=True, use_rapidgzip=AcceleratorMode.ON)
source = path if mode == "path" else open(path, "rb")
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
@pytest.mark.parametrize("mode", ["path", "stream"])
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
    _assert_clean_exit(proc, "archivey.exceptions TruncatedError")


@pytest.fixture
def accelerator_opens(monkeypatch: pytest.MonkeyPatch) -> list[CodecSource]:
    """Record each rapidgzip open that goes through ``_open_accelerator``."""
    opened: list[CodecSource] = []
    real_open = codecs_module._open_accelerator

    def spy(
        open_fn: Callable[..., object], source: CodecSource
    ) -> codecs_module._AcceleratorStream:
        opened.append(source)
        return real_open(open_fn, source)

    monkeypatch.setattr(codecs_module, "_open_accelerator", spy)
    return opened


@pytest.mark.parametrize("codec", [Codec.GZIP, Codec.ZLIB, Codec.DEFLATE])
def test_backward_seek_hands_complete_input_to_the_accelerator(
    accelerator_opens: list[CodecSource], codec: Codec
) -> None:
    payload = _payload()
    middle = len(payload) // 2
    with open_codec_stream(
        codec, io.BytesIO(_compress(codec, payload)), config=_ON
    ) as s:
        assert s.read(1 << 20) == payload[: 1 << 20]
        s.seek(1 << 21)  # forward: still the stdlib engine
        assert accelerator_opens == []
        s.seek(middle)  # backward: the stdlib proof pass, then rapidgzip
        assert len(accelerator_opens) == 1
        assert s.read(64) == payload[middle : middle + 64]
        s.seek(10)
        assert s.read() == payload[10:]
    assert len(accelerator_opens) == 1


def test_a_read_to_the_end_is_the_proof(
    accelerator_opens: list[CodecSource], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clean end the caller's own reads reached makes the separate pass unnecessary."""
    payload = _payload()
    proofs: list[object] = []
    real_proof = codecs_module._stdlib_proof_failure

    def spy(open_stdlib: Callable[[], BinaryIO]) -> Exception | None:
        proofs.append(open_stdlib)
        return real_proof(open_stdlib)

    monkeypatch.setattr(codecs_module, "_stdlib_proof_failure", spy)
    source = io.BytesIO(_compress(Codec.GZIP, payload))
    with open_codec_stream(Codec.GZIP, source, config=_ON) as stream:
        assert stream.read() == payload
        stream.seek(5)
        assert stream.read(5) == payload[5:10]
    assert proofs == []
    assert len(accelerator_opens) == 1


def test_a_sequential_read_never_opens_the_accelerator(
    accelerator_opens: list[CodecSource], tmp_path: Path
) -> None:
    import archivey

    payload = _payload()
    path = _write(tmp_path, "valid.gz", gzip.compress(payload))
    with archivey.open_archive(path, seekable_members=True) as reader:
        (member,) = reader.members()
        assert reader.read(member) == payload
    assert accelerator_opens == []


class _OneShotVerdictSource(io.BytesIO):
    """Raises ``CorruptionError`` on the first read that reaches its end, then never again.

    The shape of a ZIP WinZip AES stage, which checks its HMAC once. Here the stdlib
    proof pass is the read that sees this verdict.
    """

    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self._reported = False

    def read(self, size: int | None = -1, /) -> bytes:
        data = super().read(size)
        if not self._reported and self.tell() == len(self.getbuffer()):
            self._reported = True
            raise CorruptionError("verdict reported once")
        return data


@pytest.mark.parametrize("chunked", [False, True], ids=["read-all", "read-n"])
def test_a_verdict_only_the_proof_pass_saw_still_reaches_the_caller(
    chunked: bool,
) -> None:
    payload = _payload()
    source = _OneShotVerdictSource(zlib.compress(payload))
    with open_codec_stream(Codec.ZLIB, source, config=_ON) as stream:
        stream.read(1 << 16)
        stream.seek(0)  # the proof pass reads to the end and gets the verdict
        with pytest.raises(CorruptionError, match="verdict reported once"):
            if chunked:
                while stream.read(1 << 16):
                    pass
            else:
                stream.read()


# --- translation fallback ----------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        # Leaked untranslated from the open-time probe of a truncated gzip.
        "Next block offset index is out of sync!",
        "an internal message no allowlist entry names",
    ],
)
def test_any_rapidgzip_runtime_error_is_translated(message: str) -> None:
    from archivey.internal.streams.codecs import (
        Bzip2Codec,
        DeflateCodec,
        GzipCodec,
        ZlibCodec,
    )

    exc = RuntimeError(message)
    for codec in (GzipCodec(), DeflateCodec(), ZlibCodec(), Bzip2Codec()):
        assert isinstance(codec._translate_accelerator(exc), CorruptionError), codec


_CALLER_SOURCE_FAILS = """
import base64, io, random, zlib
from archivey.internal.config import AcceleratorMode, StreamConfig
from archivey.internal.streams.codecs import Codec, open_codec_stream

data = zlib.compress(base64.encodebytes(random.Random(0).randbytes(1_600_000)))


class Source(io.BytesIO):
    # Serves the first pass in full, then fails once rapidgzip has read 64 KiB.
    def __init__(self):
        super().__init__(data)
        self.passes = 0

    def _check(self):
        if self.passes and self.tell() > 64 * 1024:
            raise RuntimeError("caller source failed")

    def read(self, size=-1, /):
        self._check()
        out = super().read(size)
        if self.tell() == len(data):
            self.passes += 1
        return out

    def readinto(self, buf, /):
        self._check()
        n = super().readinto(buf)
        if self.tell() == len(data):
            self.passes += 1
        return n


config = StreamConfig(seekable=True, use_rapidgzip=AcceleratorMode.ON)
try:
    with open_codec_stream(Codec.ZLIB, Source(), config=config) as stream:
        stream.read()
        stream.seek(0)  # rapidgzip from here
        while stream.read(1 << 20):
            pass
    print("OK")
except Exception as exc:
    print(type(exc).__module__, type(exc).__name__)
"""


def test_a_runtime_error_from_the_callers_source_reaches_the_caller_unchanged() -> None:
    """The trap re-raises the caller's own RuntimeError; the fallback must not relabel it.

    In a child interpreter, because it drives rapidgzip over a source that fails.
    """
    proc = _run(_CALLER_SOURCE_FAILS)
    _assert_clean_exit(proc, "builtins RuntimeError")
