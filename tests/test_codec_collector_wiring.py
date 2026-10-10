"""Every codec stream decodes under the caller's diagnostics collector.

A diagnostic a codec stream reports itself then lands in ``reader.diagnostics`` under
the caller's policy, not in a throwaway collector. Raw DEFLATE, Deflate64 and PPMd
report none inside a container today; the check keeps them wired for the first one
they do.
"""

from __future__ import annotations

import dataclasses
import io
import struct
import zlib
from collections.abc import Callable

import pytest

from archivey.config import AcceleratorMode
from archivey.diagnostics import DiagnosticCode
from archivey.internal.config import StreamConfig
from archivey.internal.diagnostics_collector import DiagnosticCollector
from archivey.internal.streams.codecs.base import Codec, CodecParams
from archivey.internal.streams.codecs.registry import open_codec_stream, stream_codec
from archivey.internal.streams.decompressor_stream import DecompressorStream

_CONTENT = b"the quick brown fox jumps over the lazy dog\n" * 40
_ORDER = 6
_MEM = 1 << 20
_STDLIB = StreamConfig(use_rapidgzip=AcceleratorMode.OFF)


def _raw_deflate() -> tuple[bytes, CodecParams]:
    comp = zlib.compressobj(wbits=-15)
    return comp.compress(_CONTENT) + comp.flush(), CodecParams()


def _deflate64() -> tuple[bytes, CodecParams]:
    inflate64 = pytest.importorskip("inflate64")
    deflater = inflate64.Deflater()
    return deflater.deflate(_CONTENT) + deflater.flush(), CodecParams()


def _ppmd8_zip() -> tuple[bytes, CodecParams]:
    pyppmd = pytest.importorskip("pyppmd")
    enc = pyppmd.Ppmd8Encoder(_ORDER, _MEM, 0)
    packed = enc.encode(_CONTENT) + enc.flush(True)
    return packed, CodecParams(ppmd_order=_ORDER, ppmd_mem_size=_MEM)


def _ppmd7_7z() -> tuple[bytes, CodecParams]:
    pyppmd = pytest.importorskip("pyppmd")
    enc = pyppmd.Ppmd7Encoder(_ORDER, _MEM)
    packed = enc.encode(_CONTENT) + enc.flush()
    # 7z var.H properties; PPMd7 has no end mark, so the 7z sizes bound it.
    params = CodecParams(
        properties=struct.pack("<BL", _ORDER, _MEM),
        pack_size=len(packed),
        unpack_size=len(_CONTENT),
    )
    return packed, params


@pytest.mark.parametrize(
    ("codec", "make"),
    [
        (Codec.DEFLATE, _raw_deflate),
        (Codec.DEFLATE64, _deflate64),
        (Codec.PPMD, _ppmd8_zip),
        (Codec.PPMD, _ppmd7_7z),
    ],
    ids=["deflate", "deflate64", "ppmd8-zip", "ppmd7-7z"],
)
def test_codec_stream_uses_callers_collector(
    codec: Codec, make: Callable[[], tuple[bytes, CodecParams]]
) -> None:
    packed, params = make()
    collector = DiagnosticCollector()
    config = dataclasses.replace(_STDLIB, collector=collector)
    stream = stream_codec(codec).open(io.BytesIO(packed), params, config)
    try:
        assert isinstance(stream, DecompressorStream)
        assert stream._diagnostics_collector is collector
        assert stream.read() == _CONTENT
    finally:
        stream.close()


def test_raw_deflate_trailing_data_reaches_callers_collector() -> None:
    """A report raw DEFLATE can make arrives in the caller's collector."""
    packed, params = _raw_deflate()
    collector = DiagnosticCollector()
    config = dataclasses.replace(_STDLIB, report_trailing_data=True)
    with open_codec_stream(
        Codec.DEFLATE,
        io.BytesIO(packed + b"JUNK"),
        config=config,
        params=params,
        collector=collector,
    ) as stream:
        assert stream.read() == _CONTENT
    reports = [
        d.context.observed_bytes  # type: ignore[union-attr]
        for d in collector.snapshot().retained
        if d.code is DiagnosticCode.ARCHIVE_TRAILING_DATA
    ]
    assert reports == [len(packed)]
