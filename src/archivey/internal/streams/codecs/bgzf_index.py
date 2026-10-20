"""Seek points for BGZF, the blocked gzip that ``bgzip`` writes, read without decoding.

BGZF (the SAM/BAM specification, §4.1) is ordinary gzip: a run of members, each at
most 64 KiB of input, that any gzip reader decodes. Each member names its own size, in
an extra-field subfield, so the members can be walked from the start of the file with
one small read each:

  Member header:  1f 8b 08 FLG(FEXTRA set) MTIME(4) XFL OS XLEN(2 LE)
  Extra field:    subfields SI1 SI2 SLEN(2 LE) data; BGZF's is "BC", SLEN 2,
                  BSIZE(2 LE) = the whole member's size minus 1
  Member end:     CRC-32(4) ISIZE(4)

Every member starts a fresh DEFLATE stream, so each member's start is a resume point,
and its ISIZE (exact: a member holds at most 64 KiB) gives the output offset of the
next. The file ends with an empty member, the EOF marker.

The walk trusts the sizes, like the xz and lzip indexes (threat-model O17); a forward
read still decodes and checks every member. It ends quietly where a member does not
carry a ``BC`` subfield: the file is ordinary gzip from there (or has bytes after its
data), and only the members walked so far give points. The size is known only when the
walk reaches the end of the file. A ``.gzi`` index is a separate file, which a stream
cannot find, so it is not read.

Spec: https://samtools.github.io/hts-specs/SAMv1.pdf §4.1
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import BinaryIO

from archivey.diagnostics import DiagnosticCode, SeekIndexContext
from archivey.internal.diagnostics_collector import (
    DiagnosticCollector,
    resolve_collector,
)
from archivey.internal.logs import streams as logger
from archivey.internal.streams.decompressor_stream import (
    MAX_SEEK_POINTS,
    SEEK_TABLE_THINNED,
    SeekPoint,
    SpacedCollector,
)

_FEXTRA = 0x04
# Magic, method and flags, MTIME, XFL, OS, XLEN.
_FIXED_HEADER = 12
# The fixed header and a lone BC subfield: what bgzip writes.
_USUAL_HEADER = _FIXED_HEADER + 6
_TRAILER = 8


@dataclass(frozen=True, slots=True)
class _Block:
    compressed_start: int
    decompressed_start: int
    decompressed_size: int


def _block_size(header: bytes, extra: bytes) -> int | None:
    """The member size a BGZF header names, or ``None`` when it is not a BGZF member.

    ``header`` is the 12-byte fixed header, ``extra`` the whole extra field.
    """
    if header[:3] != b"\x1f\x8b\x08" or not header[3] & _FEXTRA:
        return None
    pos = 0
    while pos + 4 <= len(extra):
        length = int.from_bytes(extra[pos + 2 : pos + 4], "little")
        if extra[pos : pos + 2] == b"BC" and length == 2 and pos + 6 <= len(extra):
            size = int.from_bytes(extra[pos + 4 : pos + 6], "little") + 1
            # A member is at least its header and trailer; less is no BGZF member.
            return size if size >= _FIXED_HEADER + len(extra) + _TRAILER else None
        pos += 4 + length
    return None


def walk_bgzf(
    stream: BinaryIO, file_size: int, on_thinned: Callable[[], None] | None = None
) -> tuple[list[_Block], int | None]:
    """The BGZF members from the start of ``stream``, and the total output size.

    ``([], None)`` when the first member is not BGZF. The size is ``None`` unless the
    walk ends exactly at the end of the file. More members than the seek-table cap are
    thinned (:class:`SpacedCollector`).
    """
    kept: SpacedCollector[_Block] = SpacedCollector(lambda b: b.decompressed_start)
    offset = decompressed = 0
    stream.seek(0)
    # After the first, each read takes a member's ISIZE and the next member's header.
    header = stream.read(_USUAL_HEADER)
    while True:
        if len(header) < _FIXED_HEADER:
            break
        xlen = int.from_bytes(header[10:12], "little")
        extra = header[_FIXED_HEADER : _FIXED_HEADER + xlen]
        if len(extra) < xlen:
            extra += stream.read(xlen - len(extra))
        size = _block_size(header[:_FIXED_HEADER], extra)
        if size is None or offset + size > file_size:
            break
        stream.seek(offset + size - 4)
        chunk = stream.read(4 + _USUAL_HEADER)
        if len(chunk) < 4:
            break
        isize = int.from_bytes(chunk[:4], "little")
        kept.add(_Block(offset, decompressed, isize))
        offset += size
        decompressed += isize
        header = chunk[4:]
    if kept.thinned and on_thinned is not None:
        on_thinned()
    return kept.items, decompressed if offset == file_size and kept.items else None


def build_bgzf_index(
    inner: BinaryIO, collector: DiagnosticCollector | None
) -> tuple[list[SeekPoint], int | None]:
    """Seek points at every non-empty BGZF member after the first, and the size."""
    file_size = inner.seek(0, 2)
    thinned = False

    def on_thinned() -> None:
        nonlocal thinned
        thinned = True

    blocks, size = walk_bgzf(inner, file_size, on_thinned)
    if thinned:
        resolve_collector(collector).emit(
            code=DiagnosticCode.SEEK_INDEX_DEGRADED,
            message=(
                f"BGZF file has more than {MAX_SEEK_POINTS} blocks; kept a spaced "
                "subset, so seeks may decode further"
            ),
            context=SeekIndexContext(
                codec="gzip", scan="bgzf_blocks", error_type=SEEK_TABLE_THINNED
            ),
            logger=logger,
        )
    points = [
        SeekPoint(b.decompressed_start, b.compressed_start)
        for b in blocks
        if b.decompressed_start > 0 and b.decompressed_size > 0
    ]
    return points, size
