"""Every codec stream reports into the caller's diagnostics collector.

Raw DEFLATE (standard-library path), Deflate64 and PPMd once built their
``DecompressorStream`` without ``collector=``, so a ``SEEK_INDEX_DEGRADED`` from a ZIP
or 7z member in those methods went to a throwaway collector instead of the caller's.
"""

from __future__ import annotations

import io
import zlib

import pytest

from archivey.config import AcceleratorMode
from archivey.internal.config import StreamConfig
from archivey.internal.diagnostics_collector import DiagnosticCollector
from archivey.internal.streams.codecs.base import Codec, CodecParams
from archivey.internal.streams.codecs.registry import stream_codec
from archivey.internal.streams.decompressor_stream import DecompressorStream

_CONTENT = b"the quick brown fox jumps over the lazy dog\n" * 40


def _raw_deflate(data: bytes) -> bytes:
    comp = zlib.compressobj(wbits=-15)
    return comp.compress(data) + comp.flush()


def _ppmd8(data: bytes) -> bytes:
    pyppmd = pytest.importorskip("pyppmd")
    enc = pyppmd.Ppmd8Encoder(6, 1 << 20, 0)
    return enc.encode(data) + enc.flush(True)


def _deflate64(data: bytes) -> bytes:
    inflate64 = pytest.importorskip("inflate64")
    deflater = inflate64.Deflater()
    return deflater.deflate(data) + deflater.flush()


@pytest.mark.parametrize(
    ("codec", "encode", "params"),
    [
        (Codec.DEFLATE, _raw_deflate, CodecParams()),
        (Codec.DEFLATE64, _deflate64, CodecParams()),
        (
            Codec.PPMD,
            _ppmd8,
            CodecParams(ppmd_order=6, ppmd_mem_size=1 << 20, ppmd_restore_method=0),
        ),
    ],
    ids=["deflate", "deflate64", "ppmd"],
)
def test_codec_stream_uses_callers_collector(codec, encode, params) -> None:
    packed = encode(_CONTENT)
    collector = DiagnosticCollector()
    config = StreamConfig(use_rapidgzip=AcceleratorMode.OFF, collector=collector)
    stream = stream_codec(codec).open(io.BytesIO(packed), params, config)
    try:
        assert isinstance(stream, DecompressorStream)
        assert stream._diagnostics_collector is collector
        assert stream.read() == _CONTENT
    finally:
        stream.close()
