"""Self-extracting (SFX) stubs: the shared scan bound, the forward scan, and the cue.

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
file that opens under ``format=RAR`` and fails under auto-detect. Raising the bound is
one edit here for all three.

Two scan entry points, because the callers differ in what they may do to the source.
A parser owns its handle and reads forward (:func:`scan_for_magic`); the detector must
not consume anything, so it works from growing peeks (:func:`find_magic_in_prefix`).
Both keep the window bounded and neither buffers the whole of it.

:func:`executable_cue` is the gate. Scanning every source for archive magic would claim
any file that happens to contain ``PK\\x03\\x04`` somewhere in its first 2 MiB, so the
scan runs only where a stub could plausibly be. The gate is deliberately two-tiered —
see :class:`ExecutableCue`.
"""

from __future__ import annotations

import struct
from enum import Enum
from typing import BinaryIO, Callable, Sequence

# How far past the start of a source the archive magic may sit before we stop looking.
# 2 MiB comfortably covers real stubs (a `rar a -sfx` ELF stub is ~250 KB, and Windows
# installer stubs are of the same order) while keeping a miss cheap and bounded.
SFX_MAX = 2 * 1024 * 1024

# Read granularity for the forward scan. Large enough that a full 2 MiB window is 32
# reads, small enough that a match near the front stops early.
_SCAN_CHUNK = 65536

# Peek sizes the non-consuming scan steps through. Each peek re-reads from the source
# origin, so growing geometrically keeps a small stub cheap (64 KiB) while capping the
# worst case at a little over 2× the window, instead of the ~32× a fixed chunk would
# cost. A stub whose magic is at 250 KB — the size `rar a -sfx` produces — is found on
# the third peek.
_PEEK_STEPS = (1 << 16, 1 << 18, 1 << 20)

_MZ = b"MZ"
_ELF = b"\x7fELF"
_PE = b"PE\x00\x00"
# Offset of the PE header pointer (``e_lfanew``) in the DOS header, and the smallest
# value it can legally hold — the PE header cannot overlap the DOS header itself.
_E_LFANEW_OFFSET = 0x3C
_DOS_HEADER_SIZE = 0x40


class ExecutableCue(Enum):
    """How strongly a source's leading bytes say "this is an executable".

    ``STRONG`` means structurally confirmed — a DOS header whose ``e_lfanew`` actually
    points at a ``PE\\0\\0`` signature, or an ELF identification block whose class, data
    encoding and version fields are all valid. Arbitrary data reaches this by accident
    with vanishing probability.

    ``WEAK`` is the two- or four-byte prefix alone. It is enough to justify *looking*
    for an appended archive, but not enough to overrule a content probe: ``MZ`` is two
    bytes, and a genuine Brotli stream that happens to start with them must still be
    detectable (``format-detection``: "Executable-looking prefixes must not silently
    become a wrong stream format" is outcome-shaped, not "disable Brotli on ``MZ``").
    """

    NONE = "none"
    WEAK = "weak"
    STRONG = "strong"


def executable_cue(prefix: bytes) -> ExecutableCue:
    """Classify ``prefix`` as :class:`ExecutableCue`.

    Reads only ``prefix`` — the bytes detection already peeked — so a source whose
    leading bytes are not executable-shaped costs two byte comparisons, and nothing here
    ever reaches back to the source for more.

    That bounds how strong ``STRONG`` can get: a DOS header whose ``e_lfanew`` points
    past the end of ``prefix`` grades ``WEAK``, because the ``PE\\0\\0`` it promises is not
    in hand to confirm. Real PE files put the header within a few hundred bytes, so this
    costs nothing in practice; when it does bite, the result is the ordinary weak-cue
    path (scan, then probes on a miss), never a wrong answer.
    """
    if prefix.startswith(_MZ):
        return ExecutableCue.STRONG if _is_pe(prefix) else ExecutableCue.WEAK
    if prefix.startswith(_ELF):
        return ExecutableCue.STRONG if _is_elf(prefix) else ExecutableCue.WEAK
    return ExecutableCue.NONE


def _is_pe(prefix: bytes) -> bool:
    """Whether a ``MZ`` prefix carries a DOS header pointing at a real PE signature."""
    if len(prefix) < _E_LFANEW_OFFSET + 4:
        return False
    (e_lfanew,) = struct.unpack_from("<I", prefix, _E_LFANEW_OFFSET)
    if not _DOS_HEADER_SIZE <= e_lfanew <= len(prefix) - len(_PE):
        return False
    return prefix[e_lfanew : e_lfanew + len(_PE)] == _PE


def _is_elf(prefix: bytes) -> bool:
    """Whether an ``\\x7fELF`` prefix carries a valid identification block.

    ``EI_CLASS`` (32/64-bit), ``EI_DATA`` (endianness) and ``EI_VERSION`` are the three
    ident fields with a closed set of legal values.
    """
    if len(prefix) < 7:
        return False
    return prefix[4] in (1, 2) and prefix[5] in (1, 2) and prefix[6] == 1


def _find_earliest(
    data: bytes | bytearray, needles: Sequence[bytes], start: int = 0
) -> tuple[int, bytes] | None:
    """The earliest needle occurrence at or after ``start``, as ``(index, needle)``."""
    best: tuple[int, bytes] | None = None
    for needle in needles:
        found = data.find(needle, start)
        if found >= 0 and (best is None or found < best[0]):
            best = (found, needle)
    return best


def scan_for_magic(
    source: BinaryIO,
    needles: Sequence[bytes],
    *,
    limit: int = SFX_MAX,
) -> tuple[int, bytes] | None:
    """The earliest ``needles`` match within ``limit`` bytes, as ``(offset, needle)``.

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

        hit = _find_earliest(window, needles)
        if hit is not None:
            index, needle = hit
            return window_start + index, needle

        if len(window) > overlap:
            window_start += len(window) - overlap
            del window[: len(window) - overlap]

    return None


def find_magic_in_prefix(
    peek_more: Callable[[int], bytes],
    needles: Sequence[bytes],
    *,
    limit: int = SFX_MAX,
) -> tuple[int, bytes] | None:
    """:func:`scan_for_magic` for a source that must not be consumed.

    ``peek_more(n)`` returns the source's first ``n`` bytes without consuming them
    (idempotent, growing supersets — see ``detection._peek_prefix``). The window grows
    geometrically and each round searches only the bytes the previous one could not
    have covered, so a stub found early costs one small peek and a miss costs a bounded
    number of large ones.
    """
    if not needles:
        return None
    overlap = max(len(needle) for needle in needles) - 1
    searched = 0

    for step in (*_PEEK_STEPS, limit):
        if step <= searched:
            continue
        data = peek_more(min(step, limit))
        hit = _find_earliest(data, needles, max(0, searched - overlap))
        if hit is not None:
            return hit
        if len(data) < step:
            return None  # the source ended inside this peek; nothing more to search
        searched = len(data)

    return None
