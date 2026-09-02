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
not consume anything, so it works from growing peeks (:func:`iter_magic_in_prefix`).
Both keep the window bounded and neither buffers the whole of it.

:func:`executable_cue` is a **cost gate, not a correctness gate**. Its purpose is to
avoid reading up to :data:`SFX_MAX` from every source a caller opens — not to keep
false matches out. Hit validators (ZIP local-header sanity, later 7z/RAR self-checks)
are what reject a decoy. Widening the cue is therefore a cost decision: ``MZ``, ELF,
a ``#!`` shebang, or a Mach-O header that parses. The gate is deliberately two-tiered
— see :class:`ExecutableCue`.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from enum import Enum
from typing import BinaryIO, Callable, NamedTuple, Protocol, Sequence

# How far past the start of a source the archive magic may sit before we stop looking.
# 2 MiB comfortably covers real stubs (a `rar a -sfx` ELF stub is ~250 KB, and Windows
# installer stubs are of the same order) while keeping a miss cheap and bounded.
SFX_MAX = 2 * 1024 * 1024

# Read granularity for the forward scan. Large enough that a full 2 MiB window is 32
# reads, small enough that a match near the front stops early.
_SCAN_CHUNK = 65536

# Peek sizes the non-consuming scan steps through. With a monotone prefix workspace each
# step reads only the delta, so a full miss costs **1×** the window in unique I/O.
# Geometric growth is about stopping early on a hit. A stub whose magic is at 250 KB —
# the size `rar a -sfx` produces — is found on the third peek.
#
# The pre-workspace loop re-requested from byte 0 each step, so a miss billed
# 64 + 256 + 1024 + 2048 KiB = 3392 KiB for a 2048 KiB window — **1.66×**, not
# "a little over 2×". That 1.66× figure is what the tiering argument was counted
# against; I/O is 1× now, and the gap versus a tail probe is still large either way.
_PEEK_STEPS = (1 << 16, 1 << 18, 1 << 20)

_MZ = b"MZ"
_ELF = b"\x7fELF"
_PE = b"PE\x00\x00"
# Offset of the PE header pointer (``e_lfanew``) in the DOS header, and the smallest
# value it can legally hold — the PE header cannot overlap the DOS header itself.
_E_LFANEW_OFFSET = 0x3C
_DOS_HEADER_SIZE = 0x40


def candidate_origin_for_hit(hit_offset: int, needle_offset: int) -> int | None:
    """Convert a needle hit at ``hit_offset`` to a candidate origin.

    Returns ``None`` when the computed origin would be negative (not a candidate).
    Kept beside :class:`ScanNeedle` / :class:`MagicHit` so backend parsers that import
    this module do not transitively pull in the detection workspace.
    """
    origin = hit_offset - needle_offset
    return origin if origin >= 0 else None


class HitOutcome(Enum):
    """Result of a format-owned scan-hit validator.

    ``NOT_THIS_FORMAT`` — identity never held (decoy magic, unparseable header).
    ``VALID`` — identity and cheap structure both hold.
    ``DAMAGED`` — identity holds, structure does not (a 7z whose ``StartHeaderCRC``
    fails, or whose declared end overruns the source). This change's first-match
    scan treats ``DAMAGED`` like ``NOT_THIS_FORMAT`` (skip, continue). The later
    evidence-ledger scheduler may treat ``DAMAGED`` as a still-identified candidate
    without changing this enum.
    """

    NOT_THIS_FORMAT = "not_this_format"
    VALID = "valid"
    DAMAGED = "damaged"


class HitValidator(Protocol):
    """Format-owned SFX scan check: candidate-relative view plus known remaining.

    ``remaining`` is provable bytes from the *candidate origin* to source EOF
    (``workspace.remaining_known() - candidate_origin``), or ``None`` when the
    source length is unknown. It is a length, not a peek budget — do not confuse
    it with ``scan_limit``. Default ``None`` keeps unit tests and one-arg calls
    working.
    """

    def __call__(
        self,
        peek_more: Callable[[int], bytes],
        remaining: int | None = None,
    ) -> HitOutcome: ...


class ExecutableCue(Enum):
    """How strongly a source's leading bytes say "this is a prefix worth scanning".

    The cue is a **cost gate**: it decides whether to spend the ``SFX_MAX`` window,
    not whether a hit is trustworthy. Hit validators decide that.

    ``STRONG`` means structurally confirmed — a DOS header whose ``e_lfanew`` actually
    points at a ``PE\\0\\0`` signature, an ELF identification block whose class, data
    encoding and version fields are all valid, or a Mach-O header whose ``cputype`` /
    ``filetype`` (thin) or fat arch table parse. Arbitrary data reaches this by accident
    with vanishing probability. A strong cue with no archive needle suppresses content
    probes.

    ``WEAK`` is the two- or four-byte prefix alone (``MZ``, ``\\x7fELF``, or ``#!``).
    It is enough to justify *looking* for an appended archive, but not enough to
    overrule a content probe: ``MZ`` is two bytes, and a genuine Brotli stream that
    happens to start with them must still be detectable (``format-detection``:
    "Executable-looking prefixes must not silently become a wrong stream format" is
    outcome-shaped, not "disable Brotli on ``MZ``").

    Mach-O is the exception: its magic raises **no** cue until the header parses,
    because ``ca fe ba be`` is also the Java class-file magic and a weak cue would
    still put every ``.class`` file through the scan.
    """

    NONE = "none"
    WEAK = "weak"
    STRONG = "strong"


class ScanNeedle(NamedTuple):
    """A magic needle plus the offset at which it sits *within its candidate*.

    TAR's ``ustar`` sits at candidate offset 257, so a hit at absolute ``H`` denotes a
    candidate origin of ``H - 257``. A gzip or ZIP local-header needle begins at
    candidate offset zero.
    """

    magic: bytes
    offset: int = 0


class MagicHit(NamedTuple):
    """A scan hit expressed as a candidate origin, not a raw needle position."""

    candidate_origin: int
    needle: bytes
    needle_offset: int


def executable_cue(prefix: bytes) -> ExecutableCue:
    """Classify ``prefix`` as :class:`ExecutableCue`.

    Reads only ``prefix`` — the bytes detection already peeked — so a source whose
    leading bytes are not prefix-shaped costs a handful of byte comparisons, and
    nothing here ever reaches back to the source for more.

    That bounds how strong ``STRONG`` can get: a DOS header whose ``e_lfanew`` points
    past the end of ``prefix`` grades ``WEAK``, because the ``PE\\0\\0`` it promises is not
    in hand to confirm. Exceeding what this prefix can confirm is "cannot confirm
    cheaply", never "not an executable". Real PE files put the header within a few
    hundred bytes, so this costs nothing in practice; when it does bite, the result is
    the ordinary weak-cue path (scan, then probes on a miss), never a wrong answer.
    """
    if prefix.startswith(_MZ):
        return ExecutableCue.STRONG if _is_pe(prefix) else ExecutableCue.WEAK
    if prefix.startswith(_ELF):
        return ExecutableCue.STRONG if _is_elf(prefix) else ExecutableCue.WEAK
    if prefix.startswith(b"#!"):
        return ExecutableCue.WEAK
    if _is_macho(prefix):
        return ExecutableCue.STRONG
    return ExecutableCue.NONE


def _is_pe(prefix: bytes) -> bool:
    """Whether a ``MZ`` prefix carries a DOS header pointing at a real PE signature.

    No upper bound on ``e_lfanew`` turns a large pointer into "not an executable":
    if the PE signature is not in this prefix, the cue stays ``WEAK``. Alignment is
    not required — a valid image may be unaligned.
    """
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


# Mach-O magics as they appear on disk. Thin ones are unique; fat ``ca fe ba be`` is
# also the Java class-file magic, which is why Mach-O raises no cue until the header
# parses — a weak match would still enroll every ``.class`` file in the scan.
_MACHO_THIN = {
    b"\xce\xfa\xed\xfe": ("<", False),  # 32-bit LE
    b"\xfe\xed\xfa\xce": (">", False),  # 32-bit BE
    b"\xcf\xfa\xed\xfe": ("<", True),  # 64-bit LE
    b"\xfe\xed\xfa\xcf": (">", True),  # 64-bit BE
}
_MACHO_FAT = {
    b"\xca\xfe\xba\xbe": (">", False),  # fat 32-bit offsets, BE
    b"\xbe\xba\xfe\xca": ("<", False),  # fat 32-bit offsets, LE
    b"\xca\xfe\xba\xbf": (">", True),  # fat 64-bit offsets, BE
    b"\xbf\xba\xfe\xca": ("<", True),  # fat 64-bit offsets, LE
}
# mach/machine.h CPU_TYPE_* values, unpacked in the file's endianness.
_MACHO_CPU_TYPES = frozenset(
    {
        1,  # VAX
        6,  # MC680x0
        7,  # X86 / I386
        0x01000007,  # X86_64
        8,  # MIPS
        10,  # MC98000
        11,  # HPPA
        12,  # ARM
        0x0100000C,  # ARM64
        0x0200000C,  # ARM64_32
        13,  # MC88000
        14,  # SPARC
        15,  # I860
        18,  # POWERPC
        0x01000012,  # POWERPC64
    }
)
# loader.h MH_* filetypes; 1–16 leaves a little room past MH_FILESET without
# accepting a zeroed or class-file field.
_MACHO_FILETYPES = range(1, 17)
_MACHO_FAT_ARCH_MAX = 16


def _is_macho(prefix: bytes) -> bool:
    """Whether ``prefix`` is a Mach-O header that parses (thin or fat).

    Magic alone is not enough — ``ca fe ba be`` is the Java class-file magic too.
    """
    if len(prefix) < 4:
        return False
    key = bytes(prefix[:4])
    thin = _MACHO_THIN.get(key)
    if thin is not None:
        return _thin_macho_parses(prefix, thin[0], thin[1])
    fat = _MACHO_FAT.get(key)
    if fat is not None:
        return _fat_macho_parses(prefix, fat[0], fat[1])
    return False


def _thin_macho_parses(prefix: bytes, endian: str, is_64: bool) -> bool:
    size = 32 if is_64 else 28
    if len(prefix) < size:
        return False
    cputype, _cpusubtype, filetype = struct.unpack_from(endian + "iiI", prefix, 4)
    return cputype in _MACHO_CPU_TYPES and filetype in _MACHO_FILETYPES


def _fat_macho_parses(prefix: bytes, endian: str, is_64: bool) -> bool:
    if len(prefix) < 8:
        return False
    (nfat_arch,) = struct.unpack_from(endian + "I", prefix, 4)
    if not 1 <= nfat_arch <= _MACHO_FAT_ARCH_MAX:
        return False
    arch_size = 32 if is_64 else 20
    table_end = 8 + nfat_arch * arch_size
    if len(prefix) < table_end:
        return False
    arch_fmt = endian + ("iiIQQI" if is_64 else "iiIII")
    for i in range(nfat_arch):
        fields = struct.unpack_from(arch_fmt, prefix, 8 + i * arch_size)
        cputype = fields[0]
        offset = fields[3] if is_64 else fields[2]
        size = fields[4] if is_64 else fields[3]
        align = fields[5] if is_64 else fields[4]
        if (
            cputype not in _MACHO_CPU_TYPES
            or size == 0
            or offset < table_end
            or align >= 32
        ):
            return False
    return True


def _normalize_needles(
    needles: Sequence[bytes | ScanNeedle],
) -> tuple[ScanNeedle, ...]:
    out: list[ScanNeedle] = []
    for n in needles:
        if isinstance(n, ScanNeedle):
            out.append(n)
        else:
            out.append(ScanNeedle(n, 0))
    return tuple(out)


def _find_earliest(
    data: bytes | bytearray,
    needles: Sequence[ScanNeedle],
    start: int = 0,
    *,
    searched: int = 0,
) -> tuple[int, ScanNeedle] | None:
    """The earliest needle occurrence at or after ``start``, as ``(index, needle)``.

    ``searched`` is how far a previous growing peek already covered. A shorter
    needle that fitted entirely in that prefix must not be re-found in the overlap
    kept for a longer sibling (RAR5's 8 bytes vs ZIP's 4).
    """
    best: tuple[int, ScanNeedle] | None = None
    for needle in needles:
        needle_start = max(start, max(0, searched - (len(needle.magic) - 1)))
        found = data.find(needle.magic, needle_start)
        if found >= 0 and (best is None or found < best[0]):
            best = (found, needle)
    return best


def scan_for_magic(
    source: BinaryIO,
    needles: Sequence[bytes | ScanNeedle],
    *,
    limit: int = SFX_MAX,
) -> MagicHit | None:
    """The earliest ``needles`` match within ``limit`` bytes, as a :class:`MagicHit`.

    The scan starts at ``source``'s **current position** and the returned candidate
    origin is relative to it. A needle counts as found only when it lies wholly inside
    the first ``limit`` bytes, so the bound is a promise about the whole magic and not
    just its first byte. A hit whose computed candidate origin would be negative is
    discarded and the scan continues.

    ``source`` is left wherever the scan stopped reading — callers reposition it from
    the returned origin. Overlapping needles are resolved by earliest start, not by
    needle order, so a caller can pass several magics (RAR4's and RAR5's ids, say) and
    get the one that actually comes first in the file.
    """
    normalized = _normalize_needles(needles)
    if not normalized:
        return None
    # Bytes carried between chunks so a magic straddling a chunk boundary still matches.
    overlap = max(len(needle.magic) for needle in normalized) - 1

    window = bytearray()
    # Offset (from the scan start) of window[0], so a hit inside the window maps back.
    window_start = 0
    consumed = 0
    search_from = 0

    while consumed < limit:
        chunk = source.read(min(_SCAN_CHUNK, limit - consumed))
        if not chunk:
            break
        consumed += len(chunk)
        window.extend(chunk)

        while True:
            hit = _find_earliest(window, normalized, search_from)
            if hit is None:
                break
            index, needle = hit
            abs_pos = window_start + index
            origin = candidate_origin_for_hit(abs_pos, needle.offset)
            if origin is not None:
                return MagicHit(origin, needle.magic, needle.offset)
            # Negative origin — not a candidate; resume just past this decoy.
            search_from = index + 1

        search_from = 0
        if len(window) > overlap:
            window_start += len(window) - overlap
            del window[: len(window) - overlap]

    return None


def iter_magic_in_prefix(
    peek_more: Callable[[int], bytes],
    needles: Sequence[bytes | ScanNeedle],
    *,
    limit: int = SFX_MAX,
) -> Iterator[MagicHit]:
    """Yield every in-window ``needles`` match, earliest first, as :class:`MagicHit`.

    Callers skip decoys (a candidate that fails its format-owned validator) and
    keep iterating. A negative candidate origin is discarded rather than yielded.
    Each match is yielded once: a shorter needle that sat wholly inside the previous
    peek is not re-emitted when the next step rewinds by the longest needle's overlap.
    ``peek_more(n)`` returns the source's first ``n`` bytes without consuming them
    (idempotent, growing supersets — a
    :class:`~archivey.internal.detection_workspace.PrefixWorkspace` view).
    """
    normalized = _normalize_needles(needles)
    if not normalized:
        return
    searched = 0

    for step in (*_PEEK_STEPS, limit):
        if step <= searched:
            continue
        data = peek_more(min(step, limit))
        search_from = 0
        while True:
            hit = _find_earliest(data, normalized, search_from, searched=searched)
            if hit is None:
                break
            index, needle = hit
            origin = candidate_origin_for_hit(index, needle.offset)
            if origin is not None:
                yield MagicHit(origin, needle.magic, needle.offset)
            search_from = index + 1
        if len(data) < step:
            return  # the source ended inside this peek; nothing more to search
        searched = len(data)


def find_magic_in_prefix(
    peek_more: Callable[[int], bytes],
    needles: Sequence[bytes | ScanNeedle],
    *,
    limit: int = SFX_MAX,
) -> MagicHit | None:
    """:func:`scan_for_magic` for a source that must not be consumed.

    Returns the earliest hit. Prefer :func:`iter_magic_in_prefix` when a validator
    may reject a decoy and the scan must continue.
    """
    return next(iter_magic_in_prefix(peek_more, needles, limit=limit), None)
