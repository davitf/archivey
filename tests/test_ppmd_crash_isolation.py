"""PPMd on corrupt input fails cleanly, and valid input decodes the same on both paths.

pyppmd (1.3.1, and 1.2.0) segfaults when asked to decode after a corrupt stream has
hit its end early. ``PpmdDecoder`` avoids that by handing pyppmd a member whole when it
is at most ``DecoderLimits.max_ppmd_in_process_input`` of compressed input, and by
decoding larger members in a child process (``ppmd_child``). The hostile-input tests run in a fresh
interpreter so that a regression fails the test instead of killing the session.
"""

from __future__ import annotations

import io
import random
import struct
import textwrap
from collections.abc import Iterator

import pytest

from archivey.config import DecoderLimits
from archivey.exceptions import CorruptionError, ResourceLimitError, TruncatedError
from archivey.internal.config import StreamConfig
from archivey.internal.streams import decompress as decompress_module
from archivey.internal.streams.codecs import Codec, CodecParams, open_codec_stream
from archivey.internal.streams.ppmd_child import PpmdChildDecoder, PpmdChildError
from tests.conftest import requires
from tests.test_ppmd_raw_streams import (
    _MEM,
    _ORDER,
    _encode_ppmd7,
    _run_ppmd_child,
)

pytestmark = requires("pyppmd")

# Paths: "whole" is the default in-process hold; "child" lowers the limit so the same
# small members go through the child process.
_PATHS = ["whole", "child"]


def _config(path: str) -> StreamConfig:
    limit = 1024 if path == "child" else DecoderLimits().max_ppmd_in_process_input
    return StreamConfig(decoder_limits=DecoderLimits(max_ppmd_in_process_input=limit))


def _params(variant: int, packed_len: int, unpack_size: int | None) -> CodecParams:
    if variant == 7:
        return CodecParams(
            properties=struct.pack("<BL", _ORDER, _MEM),
            unpack_size=unpack_size,
            pack_size=packed_len,
        )
    return CodecParams(
        ppmd_order=_ORDER,
        ppmd_mem_size=_MEM,
        unpack_size=unpack_size,
        pack_size=packed_len,
    )


@pytest.fixture
def ppmd_config(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[StreamConfig]:
    """The config for one path; afterwards, check that it was the path taken."""
    path = request.param
    started: list[bool] = []
    real_init = PpmdChildDecoder.__init__

    def counting_init(self: PpmdChildDecoder, **kwargs: int) -> None:
        started.append(True)
        real_init(self, **kwargs)

    monkeypatch.setattr(PpmdChildDecoder, "__init__", counting_init)
    yield _config(path)
    assert bool(started) == (path == "child"), started


@pytest.mark.parametrize("path", _PATHS)
@pytest.mark.parametrize("variant", [7, 8])
def test_random_input_raises_and_never_crashes(variant: int, path: str) -> None:
    """Random bytes (a wrong key, a hostile member) raise an archivey error.

    Every input starts with a zero byte, so PPMd7's range decoder accepts it and the
    model decodes garbage until it reaches an end. Before the hold, archivey then fed
    pyppmd the rest of the member, and every child of 100 such inputs segfaulted
    (7z and ZIP alike). Reads alternate between ``read()`` and 4 KiB ``read(n)``.
    """
    _run_ppmd_child(
        textwrap.dedent(
            f"""\
            import io, random
            from archivey.exceptions import ArchiveyError
            from archivey.internal.streams.codecs import Codec, open_codec_stream
            from tests.test_ppmd_crash_isolation import _config, _params

            config = _config({path!r})
            decoded_cleanly = 0
            for seed in range(40):
                data = b"\\\\0" + random.Random(seed).randbytes(128 * 1024 - 1)
                params = _params({variant}, len(data), 2 * len(data))
                try:
                    with open_codec_stream(
                        Codec.PPMD, io.BytesIO(data), params=params, config=config
                    ) as stream:
                        if seed % 2:
                            stream.read()
                        else:
                            while stream.read(4096):
                                pass
                except ArchiveyError:
                    pass
                else:
                    decoded_cleanly += 1
            # Twice the input's size is never what random bytes decode to.
            assert decoded_cleanly == 0, decoded_cleanly
            print("ok")
            """
        ),
        timeout=180.0,
    )


# A long run of one byte compresses to a long run of zero bytes, and a chunk boundary
# inside it raises pyppmd's ``eof`` on a valid stream (it never clears). A decoder that
# took "short at eof" as the end mid-member cut this member short.
# Its compressed size, about 200 KB, is past one 64 KiB read, so the child path is
# really taken when the hold limit is lowered.
_ZERO_RUN = (
    random.Random(7).randbytes(100_000)
    + bytes(600_000)
    + random.Random(8).randbytes(100_000)
)


@pytest.mark.parametrize("ppmd_config", _PATHS, indirect=True)
@pytest.mark.parametrize("read_size", [-1, 1000, 65536])
def test_valid_member_with_zero_runs_decodes_on_both_paths(
    ppmd_config: StreamConfig, read_size: int
) -> None:
    """PPMd7 only: pyppmd's own PPMd8 encoder cannot round-trip this payload (its
    decoder reports corruption on the encoder's output), while a 7-Zip-written ZIP
    PPMd member of it reads back intact."""
    packed = _encode_ppmd7(_ZERO_RUN)
    params = _params(7, len(packed), len(_ZERO_RUN))
    with open_codec_stream(
        Codec.PPMD, io.BytesIO(packed), params=params, config=ppmd_config
    ) as stream:
        if read_size < 0:
            got = stream.read()
        else:
            parts = []
            while chunk := stream.read(read_size):
                parts.append(chunk)
            got = b"".join(parts)
    assert got == _ZERO_RUN


@pytest.mark.parametrize("ppmd_config", _PATHS, indirect=True)
def test_truncated_member_returns_its_prefix_then_raises(
    ppmd_config: StreamConfig,
) -> None:
    """A member cut short is handed over whole at compressed EOF, in this process.

    ``flush`` returns the first 64 KiB of output and the stream drains the rest, so a
    chunked reader still gets everything the truncated input decodes to (well past
    64 KiB here) before ``TruncatedError``.
    """
    rng = random.Random(9)
    words = [rng.randbytes(6).hex().encode() for _ in range(20_000)]
    payload = b" ".join(rng.choice(words) for _ in range(60_000))  # ~780 KB of text
    packed = _encode_ppmd7(payload)
    cut = packed[: len(packed) * 3 // 4]
    params = _params(7, len(packed), len(payload))
    got = bytearray()
    with open_codec_stream(
        Codec.PPMD, io.BytesIO(cut), params=params, config=ppmd_config
    ) as stream:
        with pytest.raises(TruncatedError):
            while chunk := stream.read(4096):
                got += chunk
    assert len(got) > 65536
    assert bytes(got) == payload[: len(got)]


def test_without_a_child_process_a_large_member_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A frozen app cannot start a child: past the limit, ``ResourceLimitError``."""
    monkeypatch.setattr(decompress_module, "child_decoding_available", lambda: False)
    packed = _encode_ppmd7(_ZERO_RUN)
    params = _params(7, len(packed), len(_ZERO_RUN))
    with open_codec_stream(
        Codec.PPMD, io.BytesIO(packed), params=params, config=_config("child")
    ) as stream:
        with pytest.raises(ResourceLimitError, match="max_ppmd_in_process_input"):
            stream.read(10)


def test_unlimited_decodes_in_process_and_never_starts_a_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``None`` holds any member in-process; no child process is started."""

    def no_child(*args: object, **kwargs: object) -> None:
        raise AssertionError("no child process may be started")

    monkeypatch.setattr(PpmdChildDecoder, "__init__", no_child)
    packed = _encode_ppmd7(_ZERO_RUN)
    params = _params(7, len(packed), len(_ZERO_RUN))
    config = StreamConfig(decoder_limits=DecoderLimits.UNLIMITED)
    with open_codec_stream(
        Codec.PPMD, io.BytesIO(packed), params=params, config=config
    ) as stream:
        assert stream.read(10) == _ZERO_RUN[:10]
        assert stream.read() == _ZERO_RUN[10:]


def test_child_decoder_round_trip_and_errors() -> None:
    """The child answers like pyppmd, re-raises its errors, and dies cleanly."""
    packed = _encode_ppmd7(b"hello child " * 50)
    child = PpmdChildDecoder(variant=7, order=_ORDER, mem_size=_MEM)
    try:
        assert child.decode(packed, 600) == b"hello child " * 50
    finally:
        child.close()
    child.close()  # idempotent

    bad = PpmdChildDecoder(variant=7, order=_ORDER, mem_size=_MEM)
    try:
        with pytest.raises(ValueError):
            bad.decode(b"\x00\x01", 10)  # under the 5 bytes pyppmd needs to start
    finally:
        bad.close()
    with pytest.raises(PpmdChildError):
        bad.decode(b"\x00" * 16, 10)


def test_child_crash_surfaces_as_corruption_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dead child is ``CorruptionError`` to the caller, and the caller lives on."""

    def die(self: PpmdChildDecoder, data: bytes, length: int) -> bytes:
        del data, length
        assert self._proc is not None
        self._proc.kill()
        self._proc.wait()
        self._dead = True
        raise PpmdChildError("PPMd decoder process exited unexpectedly")

    monkeypatch.setattr(PpmdChildDecoder, "decode", die)
    packed = _encode_ppmd7(_ZERO_RUN)
    assert len(packed) > 65536  # past one read, so the child is used
    params = _params(7, len(packed), len(_ZERO_RUN))
    with open_codec_stream(
        Codec.PPMD, io.BytesIO(packed), params=params, config=_config("child")
    ) as stream:
        with pytest.raises(CorruptionError, match="crashed"):
            stream.read()
