"""A compressed TAR read streaming from a source that returns a little at a time.

On a forward-only source the decoder is fed what has arrived, so a read of the source
can be short with more to come. The answer must not depend on where the reads split:
the members, their bytes, the error and the diagnostics are the same as from a source
that returns every read in full. The cases cover the clean archive, a cut at several
places, bytes after the compressed stream and two compressed streams joined.

Brotli is here too, though its feed is not split: its decoder cannot find the end of
its stream inside a feed, so a split there would change the verdict on bytes after it.
"""

from __future__ import annotations

import io
import tarfile
from collections.abc import Callable

import pytest

from archivey import ArchiveFormat, open_archive
from archivey.types import ContainerFormat, StreamFormat
from tests.streams_util import STREAM_COMPRESSORS, NonSeekableBytesIO


class _Trickle(io.RawIOBase):
    """A forward-only raw stream that returns at most ``step`` bytes per read."""

    def __init__(self, data: bytes, step: int) -> None:
        super().__init__()
        self._data = data
        self._step = step

    def readable(self) -> bool:
        return True

    def readinto(self, b: bytearray | memoryview) -> int:  # type: ignore[override]
        take = min(len(b), self._step, len(self._data))
        b[:take] = self._data[:take]
        self._data = self._data[take:]
        return take


def _tar() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tf:
        for i, size in enumerate((0, 700, 9000, 70_000)):
            info = tarfile.TarInfo(f"m{i}-" + "n" * 120)
            info.size = size
            tf.addfile(
                info,
                io.BytesIO(bytes(range(256)) * (size // 256) + b"x" * (size % 256)),
            )
    return buf.getvalue()


def _outcome(source: object, fmt: ArchiveFormat) -> tuple[object, ...]:
    """What a streaming pass sees: each member's name and bytes, then the error."""
    members: list[tuple[str, bytes]] = []
    error: tuple[str, str] | None = None
    counts: dict[object, int] = {}
    try:
        with open_archive(source, streaming=True, format=fmt) as reader:  # type: ignore[arg-type]
            try:
                for member, stream in reader.stream_members():
                    members.append((member.name, stream.read() if stream else b""))
            finally:
                counts = dict(reader.diagnostics.counts)
    except Exception as exc:  # noqa: BLE001 - the error is part of the outcome
        error = (type(exc).__name__, str(exc))
    return members, error, counts


def _variants(packed: bytes, compress: Callable[[bytes], bytes]) -> dict[str, bytes]:
    tar = _tar()
    cuts = {f"cut-{f}": packed[: len(packed) * f // 8] for f in range(1, 8)}
    return {
        "clean": packed,
        **cuts,
        "cut-last-byte": packed[:-1],
        "junk-after": packed + b"junk after the stream",
        "zeros-after": packed + bytes(600),
        "two-streams": compress(tar[:10_240]) + compress(tar[10_240:]),
    }


@pytest.mark.parametrize("step", [1, 7, 4096])
@pytest.mark.parametrize(
    "stream_format", list(STREAM_COMPRESSORS), ids=lambda f: f.value
)
def test_a_compressed_tar_reads_the_same_in_any_split(
    stream_format: StreamFormat, step: int
) -> None:
    compress = STREAM_COMPRESSORS[stream_format]
    fmt = ArchiveFormat(ContainerFormat.TAR, stream_format)
    packed = compress(_tar())
    differ = []
    for name, data in _variants(packed, compress).items():
        expected = _outcome(NonSeekableBytesIO(data), fmt)
        got = _outcome(_Trickle(data, step), fmt)
        if got != expected:
            differ.append(name)
    assert not differ


@pytest.mark.parametrize("step", [100, 4096])
def test_the_live_ratio_guard_still_trips_on_short_reads(step: int, tmp_path) -> None:
    """The guard divides by the compressed bytes read, and short reads count the same.

    A bzip2 tar of 120 MiB of zeros, from a source of unknown size, trips the live
    archive-wide ratio from a full-read source and from one that returns ``step``
    bytes per read. A short read leaves the count closer to what the decoder took,
    so the guard trips no later.
    """
    from archivey.config import ExtractionLimits
    from archivey.internal.bomb_tracker import AlwaysStopResourceLimitError

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for i in range(3):
            info = tarfile.TarInfo(f"zeros{i}")
            info.size = 40 * 2**20
            tf.addfile(info, io.BytesIO(bytes(info.size)))
    data = STREAM_COMPRESSORS[StreamFormat.BZIP2](buf.getvalue())
    limits = ExtractionLimits(live_ratio_activation_threshold=6 * 2**20)
    for i, source in enumerate((NonSeekableBytesIO(data), _Trickle(data, step))):
        with open_archive(source, streaming=True, format=ArchiveFormat.TAR_BZ2) as r:  # type: ignore[arg-type]  # RawIOBase doubles
            with pytest.raises(AlwaysStopResourceLimitError):
                r.extract_all(tmp_path / str(i), limits=limits)
