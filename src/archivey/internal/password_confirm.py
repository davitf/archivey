"""Format-agnostic password confirmation: the ladder shared by the ZIP and 7z readers.

A format whose password check can admit a wrong value needs a second opinion before it
accepts a candidate. This module supplies that opinion without knowing any format type.
The ladder has three rungs, strongest first:

1. **Cheap key check** — an O(1) test on the key, before any payload is decoded. ZIP
   has two (ZipCrypto's header check byte, WinZip AES's ``pw_verify``); both are weaker
   than 2⁻³², so they eliminate but never confirm. 7z has none in the format. The
   backends apply this rung themselves; it is named here so the ladder reads the same
   in every backend.
2. **Integrity anchor** — a stored CRC over a decodable prefix of the unit.
   :func:`plan_confirm` picks the earliest one that verifies at least 4 bytes.
3. **Codec rejection** — a decompressor measured to fail on random input settles a
   wrong key inside a bounded prefix. Surviving that prefix is ``INCONCLUSIVE``, not
   ``CONFIRMED``.

Confirmation is a *rejection filter*, not a proof. The caller's own stream still runs
the authoritative digest at EOF. The candidate loop stays
:meth:`~archivey.internal.password._PasswordCandidates.attempt`; this module only
supplies the probe.
"""

from __future__ import annotations

import io
import zlib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import BinaryIO, TypeVar

from archivey.internal.streams.streamtools.base import DelegatingStream

# Decompressed plaintext budget for confirmation. Empirically LZMA1/LZMA2/BZip2/DEFLATE
# reject wrong-key garbage within a few bytes (the archived bounded-password-confirmation
# design, §2); 64 KiB leaves a wide margin, so a decoder change of a few bytes does not
# flip a verdict, and covers typical members exactly (EOF → CRC).
CONFIRM_PREFIX_BYTES = 64 * 1024

# Upper bound on the compressed input a bounded confirm may consume to produce its
# plaintext prefix. An output-only bound does not constrain a block-transform codec: 64
# KiB of bzip2 output costs ~905 KB of input on incompressible data, because bzip2 emits
# nothing until it has read a whole block. The number is the inner-TAR probe's
# (``detection._INNER_TAR_MAX_PROBE_BYTES``), sized for the same worst-case bzip2 block;
# a test pins the two equal. The probes do not share a helper: detection decodes through
# a non-consuming peek callable under a detection budget ledger and ends in a ``ustar``
# check, confirmation decodes a borrowed pack view through a decrypting pipeline. The
# number is the part they share.
CONFIRM_MAX_INPUT_BYTES = 1 << 20

# Read size for the confirm walk. Peak extra memory is one chunk, whatever the unit size.
CONFIRM_CHUNK_BYTES = 64 * 1024

_T = TypeVar("_T")


class ConfirmVerdict(Enum):
    """What one confirmation probe concluded about one candidate."""

    #: The candidate is wrong: an anchor mismatched, the decoder objected, or the
    #: stream ended short. Try the next one.
    REJECTED = "rejected"
    #: A CRC over at least 4 decoded bytes matched (a signal of at least 2⁻³²).
    CONFIRMED = "confirmed"
    #: The candidate survived its budget without reaching a deciding signal.
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class ConfirmPlan:
    """What :func:`run_confirm_plan` reads, and what it may conclude.

    ``segments`` are consecutive ``(length, crc)`` runs from the unit's start; a
    ``None`` CRC is read and not checked. ``unit_crc``, when set, is checked over every
    byte the segments cover, which the planner only allows when they cover the whole
    unit. ``confirms`` says whether reading every segment without a mismatch reaches
    ``CONFIRMED``; otherwise the verdict is ``INCONCLUSIVE``. ``bounded`` says the plan
    stops within the plaintext budget, so the caller should also cap the compressed
    input (:data:`CONFIRM_MAX_INPUT_BYTES`); an unbounded plan walks to a late anchor
    on purpose and takes no input cap.
    """

    segments: tuple[tuple[int, int | None], ...]
    unit_crc: int | None
    confirms: bool
    bounded: bool

    @property
    def read_bytes(self) -> int:
        return sum(length for length, _ in self.segments)


def plan_confirm(
    substreams: Iterable[tuple[int, int | None]],
    tail_crc: int | None,
    *,
    budget: int,
    codec_rejects: bool,
    min_verified_bytes: int = 4,
) -> ConfirmPlan:
    """Plan the cheapest decode that can judge a candidate.

    ``substreams`` are the unit's items in data order, as ``(size, crc | None)``;
    ``tail_crc`` is a CRC over the whole unit, when the format stores one. The rules:

    - **Earliest sufficient anchor.** Item CRCs are consulted in order, and the plan
      stops once CRC-verified bytes reach ``min_verified_bytes``: fewer carry less
      than 32 bits and do not end the plan on their own. ``tail_crc`` sits at the unit's
      end, so it is only ever the last anchor; an item CRC that suffices earlier wins.
    - **Anchor inside the budget:** decode to it and stop.
    - **Anchor past the budget:** a chain whose codec rejects random input
      (``codec_rejects``) stops at ``budget`` bytes, since the decoder settles a wrong
      key inside that prefix. A chain that does not reject walks to the anchor:
      nothing else can tell a wrong key from a right one.
    - **No sufficient anchor at all:** decode ``budget`` bytes (or the whole unit, if
      smaller) and stop. Nobody runs an unbounded decode to discover that nothing
      can be checked.

    Anchors met on the way that verify fewer than ``min_verified_bytes`` still reject
    on a mismatch; they just cannot confirm.
    """
    items = list(substreams)
    total = sum(size for size, _ in items)

    def prefix(limit: int) -> ConfirmPlan:
        # Whole items up to ``limit``, keeping their CRCs (a short anchor inside the
        # prefix can still reject), then the rest of the prefix unchecked.
        planned: list[tuple[int, int | None]] = []
        offset = 0
        for size, crc in items:
            if offset + size > limit:
                break
            planned.append((size, crc))
            offset += size
        if offset < limit:
            planned.append((limit - offset, None))
        return ConfirmPlan(tuple(planned), None, confirms=False, bounded=True)

    segments: list[tuple[int, int | None]] = []
    offset = 0
    verified = 0
    for size, crc in items:
        end = offset + size
        if crc is not None and end > budget and codec_rejects:
            # The next anchor lies past the budget and the decoder decides a wrong key
            # sooner: stop at the budget.
            return prefix(min(budget, total))
        segments.append((size, crc))
        offset = end
        if crc is not None:
            verified += size
            if verified >= min_verified_bytes:
                return ConfirmPlan(
                    tuple(segments), None, confirms=True, bounded=offset <= budget
                )

    # No item anchor sufficed. A unit CRC is the last anchor there is.
    if tail_crc is not None and (total <= budget or not codec_rejects):
        return ConfirmPlan(
            tuple(segments),
            tail_crc,
            confirms=total >= min_verified_bytes,
            bounded=total <= budget,
        )
    # No sufficient anchor within reach: at most the budget. Nobody runs an unbounded
    # decode to discover that nothing can be checked.
    return prefix(min(budget, total))


def run_confirm_plan(
    stream: BinaryIO, plan: ConfirmPlan, *, chunk_size: int = CONFIRM_CHUNK_BYTES
) -> ConfirmVerdict:
    """Read ``stream`` as ``plan`` says and return the verdict.

    Reads in chunks of at most ``chunk_size``, so peak extra memory is one chunk. A
    short read is ``REJECTED``: the stream is the decoded unit, and a correct key
    decodes the declared length. Decoder exceptions propagate; the caller decides
    which of them mean "wrong key".
    """
    unit_crc = 0
    for length, expected in plan.segments:
        crc = 0
        remaining = length
        # `> 0`, not truthiness: a stream that over-returns would drive `remaining`
        # negative, and `read(negative)` is read-everything.
        while remaining > 0:
            chunk = stream.read(min(chunk_size, remaining))
            if not chunk:
                return ConfirmVerdict.REJECTED
            crc = zlib.crc32(chunk, crc)
            if plan.unit_crc is not None:
                unit_crc = zlib.crc32(chunk, unit_crc)
            remaining -= len(chunk)
        if expected is not None and crc & 0xFFFFFFFF != expected & 0xFFFFFFFF:
            return ConfirmVerdict.REJECTED
    if plan.unit_crc is not None and unit_crc != plan.unit_crc & 0xFFFFFFFF:
        return ConfirmVerdict.REJECTED
    return ConfirmVerdict.CONFIRMED if plan.confirms else ConfirmVerdict.INCONCLUSIVE


def first_crc_match(expected_crc: int, items: Sequence[tuple[_T, int]]) -> _T | None:
    """Return the first item whose CRC-32 matches ``expected_crc`` (candidate order)."""
    expected = expected_crc & 0xFFFFFFFF
    for item, crc in items:
        if (crc & 0xFFFFFFFF) == expected:
            return item
    return None


class UnverifiedReadWatch(DelegatingStream):
    """Report a member stream closed before its declared digest was reached.

    Wraps the decoded member stream of an encrypted member whose password was accepted
    on a check weaker than the member's digest (a weak cheap-key check, or a confirm
    that ran out of budget). If the caller closes the stream after reading some bytes
    but before the reads reach ``size``, ``on_unverified`` runs once: those bytes may
    have decrypted under a wrong key, and nothing checked them. A stream closed before
    any read delivered nothing to distrust, and a read that raised has already told the
    caller something is wrong; neither reports.

    ``seek_keeps_digest`` says whether the inner stream still checks its digest after a
    seek. When it does not (a fused verifier forfeits the checksum on a seek off the
    read frontier), any position-changing seek means the digest can no longer be
    reached. When it does (zipfile's ``ZipExtFile`` reads through a forward seek and
    restarts its CRC on a backward one), reaching ``size`` by any route counts.
    """

    readinto_passthrough = False

    def __init__(
        self,
        inner: BinaryIO,
        *,
        size: int,
        on_unverified: Callable[[], None],
        seek_keeps_digest: bool,
    ) -> None:
        # Set before the base constructor, which ``close()`` must survive: IOBase's
        # finalizer calls ``close()`` on an instance whose ``__init__`` raised.
        self._watch_size = size
        self._on_unverified: Callable[[], None] | None = on_unverified
        self._seek_keeps_digest = seek_keeps_digest
        self._watch_pos = 0
        self._delivered = False
        self._reached = size <= 0
        self._forfeited = False
        super().__init__(inner)

    def _note_position(self) -> None:
        if self._watch_pos >= self._watch_size and not self._forfeited:
            self._reached = True

    def read(self, n: int = -1, /) -> bytes:
        try:
            data = super().read(n)
        except BaseException:
            # The failure is the caller's signal; the close-time report would add
            # nothing to it.
            self._on_unverified = None
            raise
        if data:
            self._delivered = True
            self._watch_pos += len(data)
            self._note_position()
        elif n != 0:
            # EOF: the inner stream has run its end-of-stream check (and a mismatch
            # would have raised above).
            if not self._forfeited:
                self._reached = True
        return data

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        try:
            position = super().seek(offset, whence)
        except BaseException:
            self._on_unverified = None
            raise
        if position != self._watch_pos and not self._seek_keeps_digest:
            self._forfeited = True
        self._watch_pos = position
        if self._seek_keeps_digest:
            self._note_position()
        return position

    def close(self) -> None:
        if self.closed:
            return
        callback = self._on_unverified
        self._on_unverified = None
        try:
            super().close()
        finally:
            if callback is not None and self._delivered and not self._reached:
                callback()
