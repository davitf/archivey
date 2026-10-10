"""Look for non-zero bytes after the end of an archive whose structure gives its end.

7z, RAR and ISO each say where the archive ends: the next header, the end-of-archive
block, the volume space. Bytes after that end are reported as ``ARCHIVE_TRAILING_DATA``
(DR-3): a warning by default, refused under ``DiagnosticPolicy.strict()``. Zero padding
is silent, as it is after a TAR trailer. TAR and the stream codecs have their own scans,
because theirs read decoded bytes; this one reads the raw source.
"""

from __future__ import annotations

from typing import BinaryIO

# How far past the archive's end the scan looks, the same bound as the TAR scan's: an
# effort limit, not a claim that the rest is zero. A non-zero byte at or past it goes
# unseen. Why a constant and not a config field: dev-docs/formats/tar.md §6.
MAX_TRAILING_SCAN = 1 << 20

_SCAN_CHUNK = 64 * 1024


def first_nonzero_offset(fp: BinaryIO, *, limit: int = MAX_TRAILING_SCAN) -> int | None:
    """The offset of the first non-zero byte in ``fp`` from its current position.

    ``None`` when ``fp`` holds only zeros up to its end or up to ``limit`` bytes,
    whichever comes first. Reads in bounded chunks, so a long tail is never held whole.
    """
    offset = 0
    while offset < limit:
        chunk = fp.read(min(_SCAN_CHUNK, limit - offset))
        if not chunk:
            return None
        stripped = chunk.lstrip(b"\x00")
        if stripped:
            return offset + len(chunk) - len(stripped)
        offset += len(chunk)
    return None
