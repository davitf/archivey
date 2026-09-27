"""Bytes after a compressed stream's end: the data reads, and the bytes are reported.

Every stream codec but ``.Z`` (which has no end marker) reads its data in full and
reports what follows as one ``ARCHIVE_TRAILING_DATA``, with ``expected_marker=
"end_of_stream"`` and the compressed offset of the first non-zero byte after the end.
Zeros there are padding. ``DiagnosticPolicy.strict()`` makes the report an error. xz
and lzip keep their size and index through the appended bytes.
"""

from __future__ import annotations

import bz2
import dataclasses
import gzip
import io
import lzma
import os
import random
import zlib
from collections.abc import Callable
from pathlib import Path

import pytest

from archivey import AcceleratorMode, ArchiveyConfig, DiagnosticPolicy, open_archive
from archivey.diagnostics import ArchiveEofContext, DiagnosticCode
from archivey.exceptions import CorruptionError, DiagnosticRaisedError
from archivey.internal.streams.decompressor_stream import TRAILING_DATA_SEARCH
from archivey.types import HashAlgorithm
from tests.conftest import requires, requires_zstd, zstd_backend
from tests.streams_util import (
    NonSeekableBytesIO,
    make_lzip_member,
    make_multi_member_lzip,
)

_PAYLOAD = random.Random(178).randbytes(50_000) * 3
_JUNK = b"appended signature\n"


def _zstd(data: bytes) -> bytes:
    return zstd_backend().compress(data)


def _brotli(data: bytes) -> bytes:
    import brotli

    return brotli.compress(data)


def _lz4(data: bytes) -> bytes:
    import lz4.frame

    return lz4.frame.compress(data)


# suffix -> (codec name in the report, compressor, skip marks)
_CODECS: dict[str, tuple[str, Callable[[bytes], bytes], tuple]] = {
    ".gz": ("gzip", gzip.compress, ()),
    ".zz": ("zlib", zlib.compress, ()),
    ".bz2": ("bzip2", bz2.compress, ()),
    ".xz": ("xz", lzma.compress, ()),
    ".lzma": ("lzma", lambda d: lzma.compress(d, format=lzma.FORMAT_ALONE), ()),
    ".lz": ("lzip", make_lzip_member, ()),
    ".zst": ("zstd", _zstd, (requires_zstd(),)),
    ".lz4": ("lz4", _lz4, (requires("lz4"),)),
    ".br": ("brotli", _brotli, (requires("brotli"),)),
}


def _params() -> list:
    return [
        pytest.param(suffix, id=suffix, marks=marks)
        for suffix, (_name, _compress, marks) in _CODECS.items()
    ]


def _reports(reader) -> list[ArchiveEofContext]:  # noqa: ANN001 - any reader
    found = [
        d.context
        for d in reader.diagnostics.retained
        if d.code is DiagnosticCode.ARCHIVE_TRAILING_DATA
    ]
    assert all(isinstance(context, ArchiveEofContext) for context in found)
    return found  # type: ignore[return-value]


def _write(tmp_path: Path, suffix: str, data: bytes) -> Path:
    path = tmp_path / f"payload{suffix}"
    path.write_bytes(data)
    return path


@pytest.mark.parametrize("suffix", _params())
def test_data_reads_and_the_bytes_after_it_are_reported(
    tmp_path: Path, suffix: str
) -> None:
    name, compress, _marks = _CODECS[suffix]
    compressed = compress(_PAYLOAD)
    path = _write(tmp_path, suffix, compressed + _JUNK)

    with open_archive(path) as reader:
        assert reader.read(reader.members()[0]) == _PAYLOAD
        (report,) = _reports(reader)
    assert report.format == name
    assert report.expected_marker == "end_of_stream"
    assert report.observed_bytes == len(compressed)
    assert report.observed_kind == "nonzero"


@pytest.mark.parametrize("suffix", _params())
def test_strict_makes_the_report_an_error(tmp_path: Path, suffix: str) -> None:
    _name, compress, _marks = _CODECS[suffix]
    path = _write(tmp_path, suffix, compress(_PAYLOAD) + _JUNK)
    strict = ArchiveyConfig(diagnostic_policy=DiagnosticPolicy.strict())
    with pytest.raises(DiagnosticRaisedError) as info:
        with open_archive(path, config=strict) as reader:
            reader.read(reader.members()[0])
    assert info.value.diagnostic.code is DiagnosticCode.ARCHIVE_TRAILING_DATA


@pytest.mark.parametrize("suffix", _params())
def test_zero_padding_after_the_end_is_not_reported(
    tmp_path: Path, suffix: str
) -> None:
    _name, compress, _marks = _CODECS[suffix]
    path = _write(tmp_path, suffix, compress(_PAYLOAD) + b"\x00" * 10_240)
    with open_archive(path) as reader:
        assert reader.read(reader.members()[0]) == _PAYLOAD
        assert _reports(reader) == []


@pytest.mark.parametrize("suffix", _params())
def test_the_report_names_the_first_non_zero_byte_after_padding(
    tmp_path: Path, suffix: str
) -> None:
    _name, compress, _marks = _CODECS[suffix]
    compressed = compress(_PAYLOAD)
    path = _write(tmp_path, suffix, compressed + b"\x00" * 100 + _JUNK)
    with open_archive(path) as reader:
        assert reader.read(reader.members()[0]) == _PAYLOAD
        (report,) = _reports(reader)
    assert report.observed_bytes == len(compressed) + 100


@pytest.mark.parametrize("suffix", _params())
def test_a_pipe_reads_and_reports_the_same(suffix: str) -> None:
    if suffix == ".br":
        pytest.skip("Brotli finds its end by re-reading the source; see the test below")
    _name, compress, _marks = _CODECS[suffix]
    compressed = compress(_PAYLOAD)
    source = NonSeekableBytesIO(compressed + _JUNK)
    with open_archive(source, streaming=True) as reader:
        for _member, stream in reader.stream_members():
            assert stream is not None
            assert stream.read() == _PAYLOAD
        (report,) = _reports(reader)
    assert report.observed_bytes == len(compressed)


@requires("brotli")
def test_brotli_on_a_pipe_cannot_tell_appended_bytes_from_damage() -> None:
    """``brotli`` fails the same way on both; telling them apart re-reads the source."""
    source = NonSeekableBytesIO(_brotli(_PAYLOAD) + _JUNK)
    with open_archive(source, streaming=True, format=_format(".br")) as reader:
        for _member, stream in reader.stream_members():
            assert stream is not None
            with pytest.raises(CorruptionError):
                stream.read()


def _brotli_rejects_before_ending(data: bytes) -> bool:
    """Whether a byte-at-a-time decode fails before the stream says it ended."""
    import brotli

    decompressor = brotli.Decompressor()
    try:
        for index in range(len(data)):
            decompressor.process(data[index : index + 1])
            if decompressor.is_finished():
                return False
    except brotli.error:
        return True
    return False


@requires("brotli")
def test_brotli_damage_is_still_corruption(tmp_path: Path) -> None:
    """Damage the decoder rejects stays corruption; the replay does not hide it.

    Brotli has no checksum, so some flips decode to other bytes or end the stream
    early. The test picks one the decoder rejects before any end.
    """
    payload = b"".join(b"line %d of text\n" % i for i in range(20_000))
    compressed = _brotli(payload)
    for position in range(len(compressed) // 2, len(compressed)):
        damaged = bytearray(compressed)
        damaged[position] ^= 0xFF
        if _brotli_rejects_before_ending(bytes(damaged)):
            break
    else:
        pytest.fail("no rejected flip found")
    path = _write(tmp_path, ".br", bytes(damaged))
    with open_archive(path, format=_format(".br")) as reader:
        with pytest.raises(CorruptionError):
            reader.read(reader.members()[0])


@requires("brotli")
@pytest.mark.parametrize("chunk", [-1, 65536])
def test_brotli_replay_keeps_output_the_library_held_back(
    tmp_path: Path, chunk: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``brotli`` holds output back even without a limit, so a replay starts only from a
    point where a call returned nothing; compressible data over many pieces shows it.
    The replayed region stays one piece long, not the whole file."""
    from archivey.internal.streams import decompress

    regions: list[int] = []
    start_replay = decompress.BrotliDecoder._start_replay

    def recording(
        self: decompress.BrotliDecoder, failed_end: int, error: BaseException
    ) -> None:
        regions.append(failed_end - self._settled)
        start_replay(self, failed_end, error)

    monkeypatch.setattr(decompress.BrotliDecoder, "_start_replay", recording)
    payload = b"".join(
        b"%d,%08x\n" % (i, i * 2654435761 % 2**32) for i in range(200_000)
    )
    import brotli

    path = _write(tmp_path, ".br", brotli.compress(payload, quality=5) + _JUNK)
    with open_archive(path, format=_format(".br")) as reader:
        with reader.open(reader.members()[0]) as stream:
            collected = bytearray()
            while data := stream.read(chunk):
                collected.extend(data)
        assert bytes(collected) == payload
        assert len(_reports(reader)) == 1
    assert regions
    assert max(regions) <= 2 * decompress._BROTLI_REPLAY_CHUNK


@pytest.mark.parametrize("suffix", _params())
@pytest.mark.parametrize("chunk", [1, 7, 4096])
def test_small_reads_get_every_byte_then_a_clean_end(
    tmp_path: Path, suffix: str, chunk: int
) -> None:
    _name, compress, _marks = _CODECS[suffix]
    payload = _PAYLOAD[:3000]
    path = _write(tmp_path, suffix, compress(payload) + _JUNK)
    with open_archive(path) as reader:
        with reader.open(reader.members()[0]) as stream:
            collected = bytearray()
            while data := stream.read(chunk):
                collected.extend(data)
            assert stream.read(chunk) == b""
        assert bytes(collected) == payload
        assert len(_reports(reader)) == 1


@pytest.mark.parametrize("suffix", _params())
def test_rereading_after_a_seek_reports_once(tmp_path: Path, suffix: str) -> None:
    _name, compress, _marks = _CODECS[suffix]
    path = _write(tmp_path, suffix, compress(_PAYLOAD) + _JUNK)
    with open_archive(path, seekable_members=True) as reader:
        with reader.open(reader.members()[0]) as stream:
            assert stream.read() == _PAYLOAD
            stream.seek(10)
            assert stream.read() == _PAYLOAD[10:]
            assert stream.seek(0, io.SEEK_END) == len(_PAYLOAD)
        assert len(_reports(reader)) == 1


@pytest.mark.parametrize(
    ("suffix", "compress"),
    [
        pytest.param(".bz2", bz2.compress, id="bz2"),
        pytest.param(".zst", _zstd, id="zst", marks=requires_zstd()),
        pytest.param(".lz4", _lz4, id="lz4", marks=requires("lz4")),
        pytest.param(".gz", gzip.compress, id="gz"),
        pytest.param(
            ".lzma",
            lambda data: lzma.compress(data, format=lzma.FORMAT_ALONE),
            id="lzma",
        ),
    ],
)
def test_concatenated_streams_read_whole_before_the_bytes(
    tmp_path: Path, suffix: str, compress: Callable[[bytes], bytes]
) -> None:
    first, second = compress(b"first|"), compress(b"second")
    path = _write(tmp_path, suffix, first + second + _JUNK)
    with open_archive(path) as reader:
        assert reader.read(reader.members()[0]) == b"first|second"
        (report,) = _reports(reader)
    assert report.observed_bytes == len(first) + len(second)


def test_a_second_lzma_stream_is_checked_against_the_dictionary_cap(
    tmp_path: Path,
) -> None:
    """A second Alone stream is refused over ``max_decoder_memory`` like the first."""
    from archivey import DecoderLimits
    from archivey.exceptions import ResourceLimitError

    first = lzma.compress(b"first", format=lzma.FORMAT_ALONE)
    second = bytearray(lzma.compress(b"second", format=lzma.FORMAT_ALONE))
    second[1:5] = (1 << 30).to_bytes(4, "little")
    path = _write(tmp_path, ".lzma", first + bytes(second))
    limits = DecoderLimits(max_decoder_memory=1 << 26)
    with open_archive(path, config=ArchiveyConfig(decoder_limits=limits)) as reader:
        with pytest.raises(ResourceLimitError):
            reader.read(reader.members()[0])


@requires_zstd()
def test_a_zstd_skippable_frame_is_part_of_the_data(tmp_path: Path) -> None:
    skippable = b"\x50\x2a\x4d\x18" + (4).to_bytes(4, "little") + b"note"
    path = _write(tmp_path, ".zst", _zstd(b"a") + skippable + _zstd(b"b"))
    with open_archive(path) as reader:
        assert reader.read(reader.members()[0]) == b"ab"
        assert _reports(reader) == []


def test_xz_keeps_its_size_and_index_through_appended_bytes(tmp_path: Path) -> None:
    path = _write(tmp_path, ".xz", lzma.compress(_PAYLOAD) + _JUNK)
    with open_archive(path, seekable_members=True) as reader:
        member = reader.members()[0]
        assert member.size == len(_PAYLOAD)
        with reader.open(member) as stream:
            stream.seek(len(_PAYLOAD) - 10)
            assert stream.read() == _PAYLOAD[-10:]
        assert DiagnosticCode.SEEK_INDEX_DEGRADED not in reader.diagnostics.counts


def test_lzip_keeps_its_size_and_crc_through_appended_bytes(tmp_path: Path) -> None:
    parts = [_PAYLOAD[:1000], _PAYLOAD[1000:]]
    path = _write(tmp_path, ".lz", make_multi_member_lzip(parts) + _JUNK)
    with open_archive(path) as reader:
        member = reader.members()[0]
        assert member.size == len(_PAYLOAD)
        assert member.hashes[HashAlgorithm.CRC32] == zlib.crc32(_PAYLOAD).to_bytes(
            4, "big"
        )
        assert DiagnosticCode.SEEK_INDEX_DEGRADED not in reader.diagnostics.counts


@pytest.mark.parametrize("suffix", [".xz", ".lz"])
def test_the_index_search_reaches_its_bound_and_no_further(
    tmp_path: Path, suffix: str
) -> None:
    """Past the search bound the index is reported unreadable; reads still work."""
    _name, compress, _marks = _CODECS[suffix]
    path = _write(tmp_path, suffix, compress(_PAYLOAD) + b"J" * TRAILING_DATA_SEARCH)
    with open_archive(path, seekable_members=True) as reader:
        member = reader.members()[0]
        assert member.size is None
        with reader.open(member) as stream:
            assert stream.seek(0, io.SEEK_END) == len(_PAYLOAD)
            stream.seek(0)
            assert stream.read() == _PAYLOAD
        assert reader.diagnostics.counts[DiagnosticCode.SEEK_INDEX_DEGRADED] >= 1
        assert len(_reports(reader)) == 1


@pytest.mark.parametrize(
    ("suffix", "module", "check", "tail"),
    [
        pytest.param(
            ".xz", "xz", "_parse_xz_footer", b"\x00\x00YZ" * (1 << 18), id="xz"
        ),
        pytest.param(
            ".lz", "lzip", "_member_ends_at", b"\x00" * (1 << 20) + b"J", id="lz"
        ),
    ],
)
def test_the_index_search_checks_a_bounded_number_of_candidates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    suffix: str,
    module: str,
    check: str,
    tail: bytes,
) -> None:
    """A tail made of candidate ends (every ``YZ`` 4-aligned, or a run of zeros where
    every offset is one) is given up on after a fixed number, not checked per byte."""
    import importlib

    target = importlib.import_module(f"archivey.internal.streams.{module}")
    original = getattr(target, check)
    calls = 0

    def counting(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(target, check, counting)
    _name, compress, _marks = _CODECS[suffix]
    path = _write(tmp_path, suffix, compress(_PAYLOAD) + tail)
    with open_archive(path) as reader:
        assert reader.members()[0].size is None
        assert reader.read(reader.members()[0]) == _PAYLOAD
    # Per candidate the tail holds 262 144 (xz) or a million (lzip).
    assert calls <= len(tail) // 64


def test_compressed_tar_reports_bytes_after_the_codec(tmp_path: Path) -> None:
    import tarfile

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        info = tarfile.TarInfo("a.txt")
        info.size = 3
        tar.addfile(info, io.BytesIO(b"abc"))
    path = tmp_path / "a.tar.xz"
    path.write_bytes(lzma.compress(buffer.getvalue()) + _JUNK)
    with open_archive(path) as reader:
        assert reader.read("a.txt") == b"abc"
        (report,) = _reports(reader)
    assert report.format == "xz"


@requires("rapidgzip")
@pytest.mark.parametrize("size", [100_000, 6_000_000])
def test_rapidgzip_reads_to_the_end_and_reports(tmp_path: Path, size: int) -> None:
    """rapidgzip either raises before delivering its last chunk or reads through the
    bytes silently, depending on where they fall; either way the read completes."""
    payload = os.urandom(size)
    compressed = gzip.compress(payload, compresslevel=1)
    path = _write(tmp_path, ".gz", compressed + _JUNK)
    config = ArchiveyConfig(use_rapidgzip=AcceleratorMode.ON)
    with open_archive(path, config=config, seekable_members=True) as reader:
        assert reader.read(reader.members()[0]) == payload
        (report,) = _reports(reader)
    assert report.observed_bytes == len(compressed)


@requires("rapidgzip")
def test_the_bzip2_accelerator_reports_the_same_offset(tmp_path: Path) -> None:
    compressed = bz2.compress(_PAYLOAD)
    path = _write(tmp_path, ".bz2", compressed + b"\x00" * 3 + _JUNK)
    config = ArchiveyConfig(use_indexed_bzip2=AcceleratorMode.ON)
    with open_archive(path, config=config, seekable_members=True) as reader:
        assert reader.read(reader.members()[0]) == _PAYLOAD
        (report,) = _reports(reader)
    assert report.observed_bytes == len(compressed) + 3


@pytest.mark.parametrize(("report", "expected"), [(False, 0), (True, 1)])
def test_inside_a_container_the_codec_stops_silently(
    report: bool, expected: int
) -> None:
    """A ZIP or 7z bounds the coder's input; what follows its end is theirs.

    Containers open their coders with ``report_trailing_data`` off: the same bytes
    that report on a bare stream stop the coder without a diagnostic.
    """
    from archivey.internal.config import StreamConfig
    from archivey.internal.diagnostics_collector import DiagnosticCollector
    from archivey.internal.streams.codecs import Codec, open_codec_stream

    collector = DiagnosticCollector()
    config = dataclasses.replace(StreamConfig(), report_trailing_data=report)
    with open_codec_stream(
        Codec.BZIP2,
        io.BytesIO(bz2.compress(b"data") + b"junk"),
        config=config,
        collector=collector,
    ) as stream:
        assert stream.read() == b"data"
    assert collector.snapshot().total_count == expected


def _format(suffix: str):  # noqa: ANN202 - an ArchiveFormat
    with open_archive(io.BytesIO(_CODECS[suffix][1](b"probe"))) as reader:
        return reader.format
