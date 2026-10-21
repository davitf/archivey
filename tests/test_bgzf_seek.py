"""Random access in gzip without ``rapidgzip``: member starts, and BGZF block walks.

``seekable-decompressor-streams`` §"gzip member starts and BGZF blocks are seek points".
Every test turns the accelerator off, since the standard-library engine is what builds
these points. The BGZF sizes are trusted like the xz and lzip indexes (threat-model O17).
"""

from __future__ import annotations

import gzip
import io
import random
import shutil
import struct
import subprocess
from typing import Any

import pytest

from archivey.diagnostics import DiagnosticCode
from archivey.exceptions import CorruptionError
from archivey.internal.config import AcceleratorMode, StreamConfig
from archivey.internal.streams.codecs import Codec, open_codec_stream
from archivey.internal.streams.codecs.bgzf_index import walk_bgzf
from archivey.internal.streams.decompressor_stream import DecompressorStream
from tests.streams_util import BGZF_EOF, CountingBytesIO, make_bgzf, make_bgzf_block

# Incompressible, so the blocks are large next to the reads a walk makes.
PARTS = [random.Random(i).randbytes(40_000 + i) for i in range(6)]
FULL = b"".join(PARTS)
STARTS = [sum(len(p) for p in PARTS[:i]) for i in range(len(PARTS))]


def _collector() -> tuple[Any, list[Any]]:
    from archivey.internal.diagnostics_collector import DiagnosticCollector

    seen: list[Any] = []
    return DiagnosticCollector(on_diagnostic=seen.append), seen


def _open(source: Any, *, seekable: bool = True, collector: Any = None) -> Any:
    config = StreamConfig(
        seekable=seekable, collector=collector, use_rapidgzip=AcceleratorMode.OFF
    )
    return open_codec_stream(Codec.GZIP, source, config=config)


def _engine(stream: Any) -> DecompressorStream:
    """The ``DecompressorStream`` under the ``ArchiveStream`` wrappers."""
    inner = stream
    while not isinstance(inner, DecompressorStream):
        inner = inner._inner
    return inner


def _points(stream: Any) -> list[int]:
    return [p.decompressed_offset for p in _engine(stream)._seek_points]


def test_bgzf_walk_gives_the_size_and_block_points_without_decoding() -> None:
    # Enough members that a walk jumping from header to header, one read each, would
    # make far more reads than one front-to-back pass in 256 KiB pieces.
    parts = PARTS * 4
    full = b"".join(parts)
    starts = [sum(len(p) for p in parts[:i]) for i in range(len(parts))]
    data = make_bgzf(parts)
    # A raw source, so the stream puts its read buffer in front, as for a path.
    source = CountingBytesIO(data)
    with _open(source) as stream:
        assert stream.seek(0, io.SEEK_END) == len(full)
        # The file once, front to back, in a few large reads; nothing inflated.
        assert source.bytes_read <= len(data) + 8192
        assert source.read_calls <= len(data) // (1 << 18) + 3
        assert _points(stream) == starts
        for pos in (starts[20] + 7, 3, starts[2], len(full) - 1):
            assert stream.seek(pos) == pos
            assert stream.read(300) == full[pos : pos + 300]


def test_bgzf_backward_seek_resumes_at_the_block_it_lands_in() -> None:
    with _open(io.BytesIO(make_bgzf(PARTS))) as stream:
        stream.seek(STARTS[5] + 10)
        assert stream.read(10) == FULL[STARTS[5] + 10 : STARTS[5] + 20]
        assert _engine(stream).nearest_resume_offset(STARTS[3] + 5) == STARTS[3]
        stream.seek(STARTS[3] + 5)
        assert stream.read(10) == FULL[STARTS[3] + 5 : STARTS[3] + 15]


def test_bgzf_with_another_extra_subfield_first() -> None:
    data = b"".join(make_bgzf_block(p, extra_before=b"XY\x03\x00abc") for p in PARTS)
    with _open(io.BytesIO(data + BGZF_EOF)) as stream:
        assert stream.seek(0, io.SEEK_END) == len(FULL)
        assert _points(stream) == STARTS


def test_plain_gzip_has_no_block_walk_and_reads_right() -> None:
    collector, seen = _collector()
    data = gzip.compress(FULL)
    with _open(io.BytesIO(data), collector=collector) as stream:
        stream.seek(STARTS[3])
        assert stream.read(10) == FULL[STARTS[3] : STARTS[3] + 10]
        assert _points(stream) == [0]
        assert stream.seek(0, io.SEEK_END) == len(FULL)
    assert not seen


def test_forward_read_records_member_starts_of_any_multi_member_gzip() -> None:
    data = b"".join(gzip.compress(p) for p in PARTS)
    with _open(io.BytesIO(data)) as stream:
        assert stream.read() == FULL
        assert _points(stream) == STARTS
        stream.seek(STARTS[2] + 1)
        assert stream.read(5) == FULL[STARTS[2] + 1 : STARTS[2] + 6]


def test_bgzf_then_plain_member_keeps_the_walked_points_and_no_size() -> None:
    data = make_bgzf(PARTS[:3], eof=False) + gzip.compress(PARTS[3])
    whole = b"".join(PARTS[:4])
    with _open(io.BytesIO(data)) as stream:
        stream.seek(STARTS[2] + 5)
        # The walk stops at the plain member; the decode records its start.
        assert _engine(stream)._size is None
        assert _points(stream) == STARTS[:4]
        assert stream.read(10) == whole[STARTS[2] + 5 : STARTS[2] + 15]
        assert stream.seek(0, io.SEEK_END) == len(whole)


def test_trailing_zeros_after_bgzf_keep_the_points() -> None:
    data = make_bgzf(PARTS) + b"\x00" * 512
    with _open(io.BytesIO(data)) as stream:
        stream.seek(STARTS[4])
        assert _points(stream) == STARTS
        assert stream.read() == FULL[STARTS[4] :]


def test_undeclared_stream_walks_nothing_and_keeps_no_points() -> None:
    with _open(io.BytesIO(make_bgzf(PARTS)), seekable=False) as stream:
        assert stream.seek(STARTS[3]) == STARTS[3]
        assert stream.read() == FULL[STARTS[3] :]
        assert _points(stream) == [0]


def _lying_bgzf() -> bytes:
    """Block 1's ISIZE says it holds 100 bytes less than it does."""
    blocks = [make_bgzf_block(p) for p in PARTS]
    one = blocks[1]
    blocks[1] = one[:-4] + struct.pack("<I", len(PARTS[1]) - 100)
    return b"".join(blocks) + BGZF_EOF


def test_bgzf_cold_seek_trusts_the_block_sizes() -> None:
    """Pins threat-model O17 for BGZF: a seek past the lie serves the walk's answer."""
    data = _lying_bgzf()
    told = STARTS[2] - 100  # where the walk puts block 2
    with _open(io.BytesIO(data)) as stream:
        assert stream.seek(told) == told
        assert stream.read(10) == PARTS[2][:10]


def test_bgzf_seek_into_the_lying_block_raises_at_its_trailer() -> None:
    data = _lying_bgzf()
    with _open(io.BytesIO(data)) as stream:
        stream.seek(0, io.SEEK_END)
        with pytest.raises(CorruptionError):
            stream.seek(STARTS[1] + 5)
            stream.read()


def test_bgzf_walk_over_the_cap_is_thinned_and_keeps_the_total(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from archivey.internal.streams import decompressor_stream

    monkeypatch.setattr(decompressor_stream, "MAX_SEEK_POINTS", 4)
    parts = [bytes([i]) * (1000 + i) for i in range(20)]
    data = make_bgzf(parts)
    full = b"".join(parts)
    blocks, size, thinned = walk_bgzf(io.BytesIO(data), len(data))
    assert thinned
    assert len(blocks) <= 5
    assert size == len(full)
    collector, seen = _collector()
    with _open(io.BytesIO(data), collector=collector) as stream:
        assert stream.seek(0, io.SEEK_END) == len(full)
        degraded = [d for d in seen if d.code is DiagnosticCode.SEEK_INDEX_DEGRADED]
        assert degraded[-1].context.error_type == "SeekTableThinned"
        assert degraded[-1].context.scan == "bgzf_blocks"
        for pos in (5, len(full) // 3, len(full) - 3):
            stream.seek(pos)
            assert stream.read(1500) == full[pos : pos + 1500]


@pytest.mark.parametrize(
    "data",
    [b"", b"\x1f\x8b\x08\x04", BGZF_EOF[:20], gzip.compress(b"x", mtime=0)],
)
def test_short_or_plain_input_walks_no_blocks(data: bytes) -> None:
    assert walk_bgzf(io.BytesIO(data), len(data)) == ([], None, False)


def test_a_plain_gzip_costs_the_walk_one_header_read() -> None:
    """Most gzip files are not BGZF; the first header says so before any piece."""
    data = gzip.compress(random.Random(3).randbytes(600_000))
    source = CountingBytesIO(data)
    assert walk_bgzf(io.BufferedReader(source), len(data)) == ([], None, False)
    assert source.bytes_read <= io.DEFAULT_BUFFER_SIZE


def test_the_eof_marker_alone_is_an_empty_walk_with_size_zero() -> None:
    blocks, size, _ = walk_bgzf(io.BytesIO(BGZF_EOF), len(BGZF_EOF))
    assert size == 0
    assert [b.decompressed_size for b in blocks] == [0]


@pytest.mark.skipif(shutil.which("bgzip") is None, reason="needs bgzip (tabix)")
def test_a_real_bgzip_file_seeks_by_its_blocks(tmp_path: Any) -> None:
    rng = random.Random(7)
    payload = bytes(rng.choice(b"ACGT\n") for _ in range(300_000))
    source = tmp_path / "reads.txt"
    source.write_bytes(payload)
    data = subprocess.run(
        ["bgzip", "-c", str(source)], check=True, capture_output=True
    ).stdout
    blocks, size, _ = walk_bgzf(io.BytesIO(data), len(data))
    assert size == len(payload)
    assert len(blocks) > 2
    with _open(io.BytesIO(data)) as stream:
        assert stream.seek(0, io.SEEK_END) == len(payload)
        for _ in range(50):
            pos = rng.randrange(len(payload))
            stream.seek(pos)
            assert stream.read(100) == payload[pos : pos + 100]


# Enough data that a walk of the whole file reads far more than one read buffer.
_BIG_PARTS = PARTS * 8


def test_a_size_query_on_a_fresh_bgzf_stream_walks_nothing() -> None:
    """A size query starts unknown, as for plain gzip: the walk reads the whole file."""
    data = make_bgzf(_BIG_PARTS)
    source = CountingBytesIO(data)
    with _open(source) as stream:
        assert _engine(stream).try_get_size() is None
        assert stream.size is None
        assert source.bytes_read <= io.DEFAULT_BUFFER_SIZE
        assert _points(stream) == [0]


@pytest.mark.parametrize("fill", ["seek_end", "read_all", "seek_forward"])
def test_bgzf_size_is_known_after_a_walk_or_a_full_read(fill: str) -> None:
    full = b"".join(_BIG_PARTS)
    with _open(io.BytesIO(make_bgzf(_BIG_PARTS))) as stream:
        if fill == "seek_end":
            stream.seek(0, io.SEEK_END)
        elif fill == "read_all":
            assert stream.read() == full
        else:
            stream.seek(len(full) // 2)
        assert _engine(stream).try_get_size() == len(full)
        assert stream.size == len(full)


def test_a_bgzf_member_size_is_cheap_on_an_archive_stream() -> None:
    from archivey import AcceleratorMode as PublicMode
    from archivey import ArchiveyConfig, open_archive

    data = make_bgzf(_BIG_PARTS)
    source = CountingBytesIO(data)
    config = ArchiveyConfig(use_rapidgzip=PublicMode.OFF)
    with open_archive(
        io.BufferedReader(source), config=config, seekable_members=True
    ) as reader:
        (member,) = reader.members()
        with reader.open(member) as stream:
            before = source.bytes_read
            assert stream.size is None
            assert source.bytes_read - before <= io.DEFAULT_BUFFER_SIZE
            assert stream.read() == b"".join(_BIG_PARTS)
            assert stream.size == len(b"".join(_BIG_PARTS))
