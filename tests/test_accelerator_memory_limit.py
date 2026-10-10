"""The rapidgzip child's memory is capped by ``DecoderLimits.max_decoder_memory``.

rapidgzip keeps decoded chunks in memory, and a chunk is as large as its output, so a
small gzip file that decodes to a lot of zeros made the child hold memory in proportion
to the output: 285 MB for a 255 KB file, and the out-of-memory killer for a 1 MB one.
The child now stops when its memory grows past the limit, and the standard library
reads the rest of the stream.

The tests prove the bound with a small input and a small limit: 64 MiB of zeros (a
65 KB file), which takes the child about 70 MB over its start-up memory, against a
limit of 8 MiB.
"""

from __future__ import annotations

import io
import subprocess
import sys
import textwrap
import time
import zlib
from collections.abc import Callable

import pytest

from archivey.config import DecoderLimits
from archivey.exceptions import ResourceLimitError
from archivey.internal.config import AcceleratorMode, StreamConfig
from archivey.internal.streams.codecs import Codec, open_codec_stream
from archivey.internal.streams.codecs.rapidgzip_child import (
    RapidgzipChildStream,
    crashed_on_data,
)
from tests.conftest import requires

pytestmark = requires("rapidgzip")

_OUTPUT_SIZE = 64 << 20
_SMALL_LIMIT = 8 << 20


def _zeros(wbits: int) -> bytes:
    """``_OUTPUT_SIZE`` zero bytes, compressed at level 9 in the format ``wbits`` names."""
    compressor = zlib.compressobj(9, zlib.DEFLATED, wbits)
    block = bytes(1 << 20)
    parts = [compressor.compress(block) for _ in range(_OUTPUT_SIZE >> 20)]
    return b"".join([*parts, compressor.flush()])


def _read_in_chunks(read: Callable[[int], bytes]) -> int:
    """Call ``read`` in 1 MiB reads to the end; check every byte is zero; return the
    count."""
    total = 0
    while True:
        data = read(1 << 20)
        if not data:
            return total
        assert data.count(0) == len(data)
        total += len(data)


def _read_until_stopped(child: RapidgzipChildStream) -> ResourceLimitError:
    """Read ``child`` from the start, again and again, until a read raises
    ``ResourceLimitError``; fail after 20 seconds, well inside the suite's 60-second
    ``--timeout``, which would end the whole worker and hide this message.

    The child checks its memory from a thread every millisecond. On a busy machine
    that thread can wait long enough for the whole stream to be decoded first, but the
    peak stays over the limit, so the child stops at the thread's next check.
    """
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            _read_in_chunks(child.read)
            child.seek(0)
        except ResourceLimitError as exc:
            return exc
        time.sleep(0.05)
    pytest.fail("the child was not stopped at its memory limit")


def test_a_child_over_its_memory_limit_is_stopped() -> None:
    """The child is stopped, and the error names the limit. It is marked so that the
    codec hands the stream to the standard library."""
    with RapidgzipChildStream(
        io.BytesIO(_zeros(31)), label="gzip", max_memory=_SMALL_LIMIT
    ) as child:
        error = _read_until_stopped(child)
        assert "max_decoder_memory" in str(error)
        assert crashed_on_data(error)
        # Every later call raises the same error.
        with pytest.raises(ResourceLimitError):
            child.read(1)


# A parent whose peak resident memory is far above what the child decodes to. Run in
# its own process, so the peak it raises is not the test worker's.
_STOPPED_UNDER_A_LARGE_PARENT = textwrap.dedent(
    """
    import io, sys, zlib
    from archivey.exceptions import ResourceLimitError
    from archivey.internal.streams.codecs.rapidgzip_child import RapidgzipChildStream

    # Touch every page, then free them: the peak stays, the memory does not.
    ballast = bytearray(int(sys.argv[1]))
    for i in range(0, len(ballast), 4096):
        ballast[i] = 1
    del ballast

    compressor = zlib.compressobj(9, zlib.DEFLATED, 31)
    block = bytes(1 << 20)
    data = b"".join([*(compressor.compress(block) for _ in range(64)), compressor.flush()])
    with RapidgzipChildStream(io.BytesIO(data), label="gzip", max_memory=8 << 20) as child:
        try:
            while child.read(1 << 20):
                pass
        except ResourceLimitError:
            print("stopped")
        else:
            print("not stopped")
    """
)


@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="Linux carries the parent's peak memory into the child's getrusage",
)
def test_the_limit_counts_from_the_childs_own_memory_not_the_parents_peak() -> None:
    """On Linux, a child's ``getrusage`` peak starts at its parent's peak: ``exec``
    keeps the high-water mark of the memory it replaces, which is the parent's. With
    that as the start, a child in a parent that once held more than the child's
    decode plus the limit was never stopped. The parent here raises its peak to 256
    MiB, more than the 64 MiB the child decodes to, and frees it again."""
    result = subprocess.run(
        [sys.executable, "-c", _STOPPED_UNDER_A_LARGE_PARENT, str(256 << 20)],
        capture_output=True,
        text=True,
        timeout=40,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "stopped"


def test_a_child_under_its_memory_limit_reads_the_whole_stream() -> None:
    with RapidgzipChildStream(
        io.BytesIO(_zeros(31)), label="gzip", max_memory=1 << 30
    ) as child:
        assert _read_in_chunks(child.read) == _OUTPUT_SIZE


@pytest.mark.parametrize(
    ("codec", "wbits"),
    [(Codec.GZIP, 31), (Codec.ZLIB, 15), (Codec.DEFLATE, -15)],
    ids=["gzip", "zlib", "deflate"],
)
def test_the_standard_library_reads_on_after_the_limit(
    codec: Codec, wbits: int
) -> None:
    """With the accelerator ON and a small limit, the whole output is still delivered,
    and a seek back to the start works after the standard library has taken over."""
    config = StreamConfig(
        seekable=True,
        use_rapidgzip=AcceleratorMode.ON,
        decoder_limits=DecoderLimits(max_decoder_memory=_SMALL_LIMIT),
    )
    with open_codec_stream(codec, io.BytesIO(_zeros(wbits)), config=config) as stream:
        assert _read_in_chunks(stream.read) == _OUTPUT_SIZE
        assert stream.seek(0) == 0
        assert stream.read(4) == bytes(4)
