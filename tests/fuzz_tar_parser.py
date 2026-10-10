"""Env-gated mutation harness for the native TAR header walker.

Run locally with::

    ARCHIVEY_FUZZ=1 uv run --no-sync pytest tests/fuzz_tar_parser.py

Every mutated archive is walked to its end in both modes, reading each member's
stored data, under a small metadata budget. Only archivey's own errors may escape.
"""

from __future__ import annotations

import io
import os
import tarfile
from collections.abc import Iterable
from random import Random

import pytest

from archivey import ArchiveyError
from archivey.internal.backends.tar_parser import (
    BLOCKSIZE,
    HeaderBlock,
    SparseFormat,
    TarEntry,
    TarWalker,
    parse_header_block,
    read_sparse_map_1_0,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("ARCHIVEY_FUZZ") != "1",
    reason="set ARCHIVEY_FUZZ=1 to run the TAR parser fuzz harness",
)

_HARNESS_VERSION = 1
_BUDGET = (64 * 1024, 64 * 1024)


def _archive(fmt: int) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=fmt) as tar:
        for name, data in (("a.txt", b"hello"), ("d/" + "n" * 90, b"x" * 700)):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo("link")
        link.type = tarfile.SYMTYPE
        link.linkname = "a.txt"
        tar.addfile(link)
    return buf.getvalue()


def _seed_bytes() -> list[bytes]:
    seeds = [b"", bytes(1024), b"not a tar archive" * 40]
    seeds += [
        _archive(fmt)
        for fmt in (tarfile.USTAR_FORMAT, tarfile.GNU_FORMAT, tarfile.PAX_FORMAT)
    ]
    return seeds


def _fix_checksums(data: bytearray, seed: bytes) -> None:
    """Re-sum every block that was a header in the seed, so a mutation reaches the
    fields behind the checksum instead of stopping at it."""
    for start in range(0, len(data) - BLOCKSIZE + 1, BLOCKSIZE):
        if isinstance(
            parse_header_block(seed[start : start + BLOCKSIZE], start), HeaderBlock
        ):
            data[start + 148 : start + 156] = b" " * 8
            total = sum(data[start : start + BLOCKSIZE])
            data[start + 148 : start + 156] = f"{total:06o}".encode() + b"\x00 "


def _mutations(seed: bytes, label: str) -> Iterable[bytes]:
    yield seed
    if seed:
        yield seed[: max(1, len(seed) // 2)]
        rng = Random(f"tar-parser|{label}|v{_HARNESS_VERSION}")
        for round_ in range(400):
            data = bytearray(seed)
            for _ in range(rng.randrange(1, 4)):
                data[rng.randrange(len(data))] = rng.choice(
                    (rng.randrange(256), 0x30 + rng.randrange(10), 0x80, 0xFF, 0)
                )
            if round_ % 2:
                _fix_checksums(data, seed)
            yield bytes(data)


def _walk(data: bytes, *, seekable: bool) -> None:
    stream = io.BytesIO(data)
    walker = TarWalker(stream, seekable=seekable)
    while isinstance(entry := walker.next_entry(_BUDGET), TarEntry):
        if seekable:
            stream.seek(entry.data_offset)
            if entry.sparse_format is SparseFormat.PAX_1_0:
                read_sparse_map_1_0(
                    stream.read, entry.stored_size, "m", lambda n, w: None
                )
        else:
            walker.open_data(entry).read()


def test_tar_walker_fuzz_harness() -> None:
    for index, seed in enumerate(_seed_bytes()):
        for mutated in _mutations(seed, str(index)):
            for seekable in (True, False):
                try:
                    _walk(mutated, seekable=seekable)
                except ArchiveyError:
                    pass
