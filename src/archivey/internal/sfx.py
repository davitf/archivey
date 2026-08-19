"""Self-extracting (SFX) stubs: the shared scan bound and the forward magic scan.

A self-extracting archive is an executable stub with a real archive appended, so the
archive magic sits at some offset ``N > 0`` rather than at byte zero. Three places have
to agree on how far forward to look for it:

* :func:`archivey.internal.detection.detect_format` — the SFX scan that turns an
  executable-shaped prefix into ``(format, payload_offset)``;
* ``rar_parser._find_sfx_header`` — the RAR parser's own scan, which is what makes
  forced ``format=RAR`` work on an SFX file;
* ``sevenzip_parser.find_signature_offset`` — the 7z equivalent.

They share :data:`SFX_MAX` rather than each carrying a bound of its own: three separate
limits drift, and a stub the RAR parser accepts but the detector does not would be a
format that opens under ``format=RAR`` and fails under auto-detect. Raising the bound is
one edit here for all three.

The scan itself is :func:`scan_for_magic`, which walks a source forward in chunks and
keeps only the needle-length overlap between them, so a 2 MiB window costs 2 MiB of
reads and a few hundred bytes of buffer — not a 2 MiB buffer, and not the quadratic
re-search a grow-and-rescan loop pays.
"""

from __future__ import annotations

from typing import BinaryIO, Sequence

# How far past the start of a source the archive magic may sit before we stop looking.
# 2 MiB comfortably covers real stubs (a `rar a -sfx` ELF stub is ~250 KB, and Windows
# installer stubs are of the same order) while keeping a miss cheap and bounded.
SFX_MAX = 2 * 1024 * 1024

# Read granularity for the forward scan. Large enough that a full 2 MiB window is 32
# reads, small enough that a match near the front stops early.
_SCAN_CHUNK = 65536


def _find_earliest(data: bytes | bytearray, needles: Sequence[bytes]) -> int:
    """Index of the earliest needle occurrence in ``data``, or ``-1`` if none match."""
    best = -1
    for needle in needles:
        found = data.find(needle)
        if found >= 0 and (best < 0 or found < best):
            best = found
    return best


def scan_for_magic(
    source: BinaryIO,
    needles: Sequence[bytes],
    *,
    limit: int = SFX_MAX,
) -> int | None:
    """Offset of the earliest ``needles`` match within ``limit`` bytes, or ``None``.

    The scan starts at ``source``'s **current position** and the returned offset is
    relative to it. A needle counts as found only when it lies wholly inside the first
    ``limit`` bytes, so the bound is a promise about the whole magic and not just its
    first byte.

    ``source`` is left wherever the scan stopped reading — callers reposition it from
    the returned offset. Overlapping needles are resolved by earliest start, not by
    needle order, so a caller can pass several magics (RAR4's and RAR5's ids, say) and
    get the one that actually comes first in the file.
    """
    if not needles:
        return None
    # Bytes carried between chunks so a magic straddling a chunk boundary still matches.
    overlap = max(len(needle) for needle in needles) - 1

    window = bytearray()
    # Offset (from the scan start) of window[0], so a hit inside the window maps back.
    window_start = 0
    consumed = 0

    while consumed < limit:
        chunk = source.read(min(_SCAN_CHUNK, limit - consumed))
        if not chunk:
            break
        consumed += len(chunk)
        window.extend(chunk)

        found = _find_earliest(window, needles)
        if found >= 0:
            return window_start + found

        if len(window) > overlap:
            window_start += len(window) - overlap
            del window[: len(window) - overlap]

    return None
