"""The SFX magic scan: linear candidate search and the fat Mach-O 64 layout.

Kept apart from ``test_sfx.py`` so the search's own contract (same hits as a
from-scratch search, in linear time) reads on its own.
"""

from __future__ import annotations

import io
import random
import struct
import time
from collections.abc import Callable

import pytest

from archivey.internal.backends.rar_parser import RAR5_ID, RAR_ID
from archivey.internal.backends.sevenzip_parser import MAGIC_7Z
from archivey.internal.sfx import (
    ExecutableCue,
    HitOutcome,
    ScanNeedle,
    _EarliestFinder,
    executable_cue,
    iter_magic_in_prefix,
    scan_for_magic,
)

_NEEDLES = (ScanNeedle(RAR5_ID), ScanNeedle(RAR_ID), ScanNeedle(MAGIC_7Z))


def _reference_earliest(
    data: bytes | bytearray,
    needles: tuple[ScanNeedle, ...],
    start: int,
    searched: int,
) -> tuple[int, ScanNeedle] | None:
    """The search as it was before positions were carried: every needle from ``start``."""
    best: tuple[int, ScanNeedle] | None = None
    for needle in needles:
        found = data.find(needle.magic, max(start, searched - (len(needle.magic) - 1)))
        if found >= 0 and (best is None or found < best[0]):
            best = (found, needle)
    return best


def _random_haystack(rng: random.Random, size: int) -> bytearray:
    """Random bytes seeded with whole and partial magics, often overlapping."""
    pieces = [RAR5_ID, RAR_ID, MAGIC_7Z, b"Rar!", b"Rar!\x1a\x07", b"7z", b"\x00"]
    out = bytearray()
    while len(out) < size:
        out += rng.choice(pieces) if rng.random() < 0.6 else bytes([rng.randrange(256)])
    return out


@pytest.mark.parametrize("seed", range(40))
def test_finder_matches_a_from_scratch_search(seed: int) -> None:
    rng = random.Random(seed)
    data = _random_haystack(rng, rng.randrange(0, 400))
    searched = rng.choice([0, 0, rng.randrange(0, len(data) + 1)])
    finder = _EarliestFinder(data, _NEEDLES, searched=searched)
    start = 0
    while True:
        expected = _reference_earliest(data, _NEEDLES, start, searched)
        assert finder.find(start) == expected
        if expected is None:
            break
        # The scans advance to one past the hit, or further (a validator's skip).
        start = expected[0] + 1 + (rng.randrange(8) if rng.random() < 0.2 else 0)


@pytest.mark.parametrize("seed", range(40))
def test_finder_sees_bytes_appended_between_calls(seed: int) -> None:
    """The validated scan's window grows under the finder when a validator peeks.

    ``scan_for_magic`` builds each chunk's finder with ``searched`` carried from the
    previous chunk and then appends to that window, so both are exercised together.
    """
    rng = random.Random(1000 + seed)
    full = _random_haystack(rng, rng.randrange(20, 400))
    data = bytearray(full[: rng.randrange(0, len(full))])
    searched = rng.choice([0, rng.randrange(0, len(data) + 1)])
    finder = _EarliestFinder(data, _NEEDLES, searched=searched)
    start = 0
    while True:
        if len(data) < len(full) and rng.random() < 0.5:
            data += full[len(data) : len(data) + rng.randrange(1, 32)]
        expected = _reference_earliest(data, _NEEDLES, start, searched)
        assert finder.find(start) == expected
        if expected is None:
            if len(data) == len(full):
                break
            data += full[len(data) :]
            continue
        start = expected[0] + 1


def test_iter_magic_in_prefix_is_linear_in_decoys() -> None:
    """Back-to-back RAR5 ids with RAR4 and 7z absent: each miss used to re-read the tail.

    On the from-scratch search this took about 75 s at 1 MiB (0.47 s at 64 KiB).
    """
    data = b"MZ" + RAR5_ID * (1024 * 1024 // len(RAR5_ID))
    started = time.perf_counter()
    count = sum(
        1
        for _ in iter_magic_in_prefix(
            lambda n: data[:n], [RAR5_ID, RAR_ID, MAGIC_7Z], limit=len(data)
        )
    )
    elapsed = time.perf_counter() - started
    assert count == 1024 * 1024 // len(RAR5_ID)
    assert elapsed < 10, f"scan took {elapsed:.1f}s"


def test_validated_scan_hits_are_unchanged() -> None:
    """``scan_for_magic`` still validates candidates in order and returns the first VALID."""
    payload = b"MZ" + b"\x00" * 100 + RAR5_ID + b"\x00" * 50 + MAGIC_7Z + b"\x00" * 64
    payload += RAR_ID + b"\x00" * 64
    seen: list[bytes] = []

    def validator(
        peek_more: Callable[[int], bytes], remaining: int | None
    ) -> HitOutcome:
        head = peek_more(8)
        seen.append(head)
        return (
            HitOutcome.VALID if head.startswith(RAR_ID) else HitOutcome.NOT_THIS_FORMAT
        )

    scan = scan_for_magic(
        io.BytesIO(payload), [RAR5_ID, RAR_ID, MAGIC_7Z], validator=validator
    )
    assert scan.hit is not None
    assert scan.hit.candidate_origin == payload.index(RAR_ID + b"\x00" * 64)
    assert [h[:6] for h in seen] == [RAR5_ID[:6], MAGIC_7Z, RAR_ID[:6]]


def _fat_macho64(endian: str, align: int) -> bytes:
    header = struct.pack(endian + "II", 0xCAFEBABF, 1)
    # fat_arch_64: cputype, cpusubtype, offset(64), size(64), align, reserved.
    arch = struct.pack(endian + "iiQQII", 0x01000007, 3, 0x1000, 0x2000, align, 0)
    return header + arch + b"\x00" * (8192 - len(header) - len(arch))


@pytest.mark.parametrize("endian", [">", "<"])
@pytest.mark.parametrize("align", [0, 12])
def test_fat_macho_64_header_parses(endian: str, align: int) -> None:
    # The old ``iiIQQI`` layout read a little-endian align-0 header's size as 0.
    assert executable_cue(_fat_macho64(endian, align)) is ExecutableCue.STRONG


@pytest.mark.parametrize("endian", [">", "<"])
def test_fat_macho_64_with_implausible_align_is_refused(endian: str) -> None:
    # The old layout read the reserved word as ``align`` and let this through.
    assert executable_cue(_fat_macho64(endian, 40)) is ExecutableCue.NONE
