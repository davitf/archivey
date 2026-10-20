"""In-process native decoders tell PyPy's GC what they hold outside its heap, and a
decoder's close ends its liblzma decoders at once on PyPy.

Without that, PyPy keeps every dropped decoder's buffers until a major collection
that the GC heap's own growth never calls for, and ``fork`` (``subprocess``) then fails
with ``ENOMEM`` against the grown process heap.
"""

from __future__ import annotations

import lzma
import sys
from pathlib import Path

import pytest

from archivey.internal.streams.codecs import deps, isolated
from archivey.internal.streams.codecs.isolated import IN_PROCESS
from archivey.internal.streams.decompressor_stream import release_decoder_children

_TYPICAL = 8 << 20


@pytest.mark.parametrize(
    ("memlimit", "filters", "expected"),
    [
        (None, None, _TYPICAL),
        (1 << 20, None, 1 << 20),
        (None, [{"id": lzma.FILTER_LZMA2, "dict_size": 1 << 30}], 1 << 30),
        (None, [{"id": lzma.FILTER_X86}, {"id": lzma.FILTER_LZMA2}], _TYPICAL),
        (None, [{"id": lzma.FILTER_LZMA1, "dict_size": 1 << 16}], 1 << 16),
        (None, [{"id": lzma.FILTER_DELTA}], 0),
    ],
)
def test_the_lzma_estimate_is_the_dictionary_capped_by_memlimit(
    memlimit: int | None, filters: list[dict[str, int]] | None, expected: int
) -> None:
    assert isolated._lzma_decoder_memory(memlimit, filters) == expected


def test_each_in_process_decoder_reports_its_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    noted: list[int] = []
    monkeypatch.setattr(isolated, "_note_native_memory", noted.append)
    IN_PROCESS.lzma_decompressor(format=lzma.FORMAT_XZ)
    IN_PROCESS.lzma_decompressor(
        format=lzma.FORMAT_RAW,
        filters=[{"id": lzma.FILTER_LZMA2, "dict_size": 1 << 24}],
    )
    IN_PROCESS.bz2_decompressor()
    expected = [_TYPICAL, 1 << 24, 3600 << 10]
    if deps.zstd is not None:
        IN_PROCESS.zstd_decompressor()
        expected.append(_TYPICAL)
    assert noted == expected


class _Holder:
    def __init__(self) -> None:
        self.decoder = lzma.LZMADecompressor()


def test_close_ends_an_in_process_lzma_decoder_only_on_pypy() -> None:
    data = lzma.compress(b"x" * 100)
    holder = _Holder()
    holder.decoder.decompress(data[:20])
    release_decoder_children(holder)
    if deps.UNDER_PYPY:
        with pytest.raises(lzma.LZMAError):
            holder.decoder.decompress(data[20:])
    else:
        assert holder.decoder.decompress(data[20:]) == b"x" * 100


def _private_writable_mib() -> int:
    total = 0
    for line in Path("/proc/self/maps").read_text().splitlines():
        span, perms = line.split()[:2]
        if "w" in perms and perms.endswith("p"):
            lo, hi = (int(x, 16) for x in span.split("-"))
            total += hi - lo
    return total >> 20


@pytest.mark.skipif(
    not deps.UNDER_PYPY or not sys.platform.startswith("linux"),
    reason="PyPy's deferred freeing of native buffers, measured through /proc",
)
def test_dropped_xz_decoders_do_not_pile_up_on_pypy() -> None:
    """800 default-preset decoders hold 6.4 GiB if nothing frees them between drops."""
    data = lzma.compress(b"x" * 100)
    before = _private_writable_mib()
    for _ in range(800):
        IN_PROCESS.lzma_decompressor(format=lzma.FORMAT_XZ).decompress(data)
    assert _private_writable_mib() - before < 2048
