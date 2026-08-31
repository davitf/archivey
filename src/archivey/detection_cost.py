"""Detection budget, capability set, and cost receipt.

Detection's I/O happens before a reader exists, so its measured work is a sibling of
:class:`~archivey.cost.CostReceipt` rather than part of it. The two share vocabulary for
kinds of work; they are never summed together. See the ``detection-cost`` and
``access-mode-and-cost`` capability specs.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum


class DetectionBudgetPreset(Enum):
    """Named detection budgets. :attr:`BALANCED` is the ``detect_format`` default."""

    BALANCED = "balanced"
    FAST = "fast"
    THOROUGH = "thorough"


class DetectionCapability(Enum):
    """What a detector needs from the source — evaluated against source **and** budget.

    Capability names align with the kinds of work :class:`~archivey.cost.CostReceipt`
    describes, so a caller reading both receipts sees one cost model.
    """

    PREFIX = "prefix"
    """A bounded head read through the prefix workspace."""

    SIZE_KNOWN = "size_known"
    """A cheap total size is available."""

    REMAINING_KNOWN = "remaining_known"
    """Bytes from the caller's current position are provable, not estimated."""

    TAIL = "tail"
    """The source can be read near its end — seekable, or spooled by explicit policy."""

    SEEK = "seek"
    """Arbitrary range reads."""

    REREAD = "reread"
    """The source can be consumed and still presented to a backend afterwards."""


class TierSkipReason(Enum):
    """Why a detection tier did not run.

    Distinct reasons matter: ``NOT_ENABLED_BY_POLICY`` does not make the search incomplete,
    while ``CAPABILITY_UNAVAILABLE`` and ``BUDGET_EXHAUSTED`` do.
    """

    NOT_ENABLED_BY_POLICY = "not_enabled_by_policy"
    CAPABILITY_UNAVAILABLE = "capability_unavailable"
    BUDGET_EXHAUSTED = "budget_exhausted"


@dataclass(frozen=True)
class TierSkip:
    """A tier that detection did not run, with the reason."""

    tier: str
    reason: TierSkipReason


@dataclass(frozen=True)
class DetectionBudget:
    """Upper bounds on what detection may spend.

    ``max_far_bytes`` is separate from ``max_prefix_bytes`` because a far fixed-offset
    signature (ISO ``CD001`` at 32 769) needs a ~32 KiB window that a 4 KiB near budget
    would otherwise forbid.

    Fields marked reserved in ``openspec/specs/detection-cost/spec.md``
    (``completion_window_bytes``, ``max_index_bytes``, ``max_probe_links``,
    ``collect_nonmaximal_candidates``, and the ZIP-tail pair ``max_tail_bytes`` /
    ``max_seeks`` on every shipping preset) are carried so follow-on changes can wire
    them without a second public shape break. No scheduled tier honours them yet.
    """

    max_prefix_bytes: int
    max_far_bytes: int
    max_tail_bytes: int
    max_seeks: int
    max_scan_bytes: int
    max_decode_input: int
    max_decode_output: int
    completion_window_bytes: int
    max_index_bytes: int
    max_probe_links: int
    spool_non_seekable_up_to: int
    collect_nonmaximal_candidates: bool

    @classmethod
    def for_preset(cls, preset: DetectionBudgetPreset) -> DetectionBudget:
        if preset is DetectionBudgetPreset.BALANCED:
            return BALANCED_BUDGET
        if preset is DetectionBudgetPreset.FAST:
            return FAST_BUDGET
        if preset is DetectionBudgetPreset.THOROUGH:
            return THOROUGH_BUDGET
        raise ValueError(f"unknown detection budget preset: {preset!r}")


@dataclass(frozen=True)
class DetectionCostReceipt:
    """Measured detection work — charged as reads happen, not reconstructed afterwards."""

    prefix_bytes: int = 0
    """Bytes requested from the workspace (sum of range lengths asked for)."""

    unique_bytes_read: int = 0
    """Bytes actually fetched from the source (each source byte counted once)."""

    far_bytes: int = 0
    tail_bytes: int = 0
    scanned_bytes: int = 0
    seeks: int = 0
    decode_input: int = 0
    decode_output: int = 0
    index_bytes: int = 0
    spooled_bytes: int = 0

    def charge(
        self,
        *,
        prefix_bytes: int = 0,
        unique_bytes_read: int = 0,
        far_bytes: int = 0,
        tail_bytes: int = 0,
        scanned_bytes: int = 0,
        seeks: int = 0,
        decode_input: int = 0,
        decode_output: int = 0,
        index_bytes: int = 0,
        spooled_bytes: int = 0,
    ) -> DetectionCostReceipt:
        return replace(
            self,
            prefix_bytes=self.prefix_bytes + prefix_bytes,
            unique_bytes_read=self.unique_bytes_read + unique_bytes_read,
            far_bytes=self.far_bytes + far_bytes,
            tail_bytes=self.tail_bytes + tail_bytes,
            scanned_bytes=self.scanned_bytes + scanned_bytes,
            seeks=self.seeks + seeks,
            decode_input=self.decode_input + decode_input,
            decode_output=self.decode_output + decode_output,
            index_bytes=self.index_bytes + index_bytes,
            spooled_bytes=self.spooled_bytes + spooled_bytes,
        )

    def within_budget(self, budget: DetectionBudget) -> bool:
        """Whether aggregate measured work stays inside ``budget``'s limits."""
        return (
            self.unique_bytes_read
            <= max(budget.max_prefix_bytes, budget.max_far_bytes, budget.max_scan_bytes)
            + budget.max_tail_bytes
            + budget.spool_non_seekable_up_to
            and self.seeks <= budget.max_seeks
            and self.tail_bytes <= budget.max_tail_bytes
            and self.scanned_bytes <= budget.max_scan_bytes
            and self.decode_input <= budget.max_decode_input
            and self.decode_output <= budget.max_decode_output
            and self.index_bytes <= budget.max_index_bytes
            and self.spooled_bytes <= budget.spool_non_seekable_up_to
        )


# ISO CD001 ends at offset 32 773 inclusive → 32 774 bytes from origin.
_ISO_FAR_BYTES = 32_774
_SFX_SCAN_BYTES = 2 * 1024 * 1024
_COMPLETION_WINDOW = 64 * 1024
_INNER_TAR_DECODE = 1 << 20


BALANCED_BUDGET = DetectionBudget(
    max_prefix_bytes=4096,
    max_far_bytes=_ISO_FAR_BYTES,
    max_tail_bytes=0,  # ZIP tail stays out until measured
    max_seeks=0,
    max_scan_bytes=_SFX_SCAN_BYTES,
    max_decode_input=_INNER_TAR_DECODE,
    max_decode_output=_INNER_TAR_DECODE,
    completion_window_bytes=_COMPLETION_WINDOW,
    max_index_bytes=0,
    max_probe_links=8,
    spool_non_seekable_up_to=0,
    collect_nonmaximal_candidates=False,
)

FAST_BUDGET = DetectionBudget(
    max_prefix_bytes=4096,
    max_far_bytes=_ISO_FAR_BYTES,
    max_tail_bytes=0,
    max_seeks=0,
    max_scan_bytes=256 * 1024,
    max_decode_input=64 * 1024,
    max_decode_output=64 * 1024,
    completion_window_bytes=0,  # no whole-source completion
    max_index_bytes=0,
    max_probe_links=2,
    spool_non_seekable_up_to=0,
    collect_nonmaximal_candidates=False,
)

THOROUGH_BUDGET = DetectionBudget(
    max_prefix_bytes=4096,
    max_far_bytes=_ISO_FAR_BYTES,
    # ZIP tail stays off until prefixed-archive-detection schedules it and measures cost.
    max_tail_bytes=0,
    max_seeks=0,
    max_scan_bytes=_SFX_SCAN_BYTES,
    max_decode_input=_INNER_TAR_DECODE,
    max_decode_output=_INNER_TAR_DECODE,
    # Reserved numeric defaults for detection-evidence-ledger — not honoured yet.
    completion_window_bytes=1 << 62,  # effectively unbounded when wired
    max_index_bytes=1 << 20,
    max_probe_links=32,
    spool_non_seekable_up_to=0,  # still opt-in via replace()
    collect_nonmaximal_candidates=True,
)


def default_detection_budget() -> DetectionBudget:
    return BALANCED_BUDGET


@dataclass
class MutableDetectionCostReceipt:
    """Mutable accumulator used by the workspace; freeze with :meth:`freeze`."""

    prefix_bytes: int = 0
    unique_bytes_read: int = 0
    far_bytes: int = 0
    tail_bytes: int = 0
    scanned_bytes: int = 0
    seeks: int = 0
    decode_input: int = 0
    decode_output: int = 0
    index_bytes: int = 0
    spooled_bytes: int = 0
    skips: list[TierSkip] = field(default_factory=list)

    def freeze(self) -> DetectionCostReceipt:
        return DetectionCostReceipt(
            prefix_bytes=self.prefix_bytes,
            unique_bytes_read=self.unique_bytes_read,
            far_bytes=self.far_bytes,
            tail_bytes=self.tail_bytes,
            scanned_bytes=self.scanned_bytes,
            seeks=self.seeks,
            decode_input=self.decode_input,
            decode_output=self.decode_output,
            index_bytes=self.index_bytes,
            spooled_bytes=self.spooled_bytes,
        )

    def record_skip(self, tier: str, reason: TierSkipReason) -> None:
        self.skips.append(TierSkip(tier=tier, reason=reason))
