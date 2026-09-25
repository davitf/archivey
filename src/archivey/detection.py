"""The result of format detection: :class:`FormatInfo` and :class:`DetectionConfidence`.

Returned by :func:`archivey.detect_format` and re-exported from :mod:`archivey`, which is
where callers should import them from. They are defined here, in a public module, so
that their ``__module__`` is a stable path: ``pickle`` records it, and a class defined
under ``archivey.internal`` would freeze an internal path into data a caller persists.
The detector itself is internal (``archivey.internal.detection``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

from archivey.detection_cost import DetectionCostReceipt, TierSkip
from archivey.diagnostics import DiagnosticSummary
from archivey.types import ArchiveFormat

__all__ = ["DetectionConfidence", "FormatInfo"]

# How a format was decided; see ``FormatInfo.detected_by``. Not exported: callers
# compare against the strings, and the alias exists so the detector and the field agree.
DetectedBy = Literal["magic", "extension", "content_probe", "sfx_scan", "directory"]


class DetectionConfidence(Enum):
    """How much :func:`~archivey.detect_format` trusts the format it reports."""

    CERTAIN = "certain"
    """An exact magic-byte match at the expected offset."""

    PROBABLE = "probable"
    """A structural or content probe matched: the inner-TAR probe, or the SFX scan."""

    GUESS = "guess"
    """No confirmation strong enough to rely on: an extension-only guess, or a content
    probe hit in the weak evidence class (today: extensionless Brotli whose first
    meta-block is uncompressed or metadata)."""


@dataclass(frozen=True)
class FormatInfo:
    """The result of :func:`~archivey.detect_format` — the detected format plus how sure
    we are."""

    format: ArchiveFormat
    """The detected format."""

    confidence: DetectionConfidence
    """How far the evidence behind ``format`` goes."""

    detected_by: DetectedBy
    """Which evidence decided: ``"magic"``, ``"extension"``, ``"content_probe"``,
    ``"sfx_scan"`` or ``"directory"``."""

    payload_offset: int = 0
    """Where the archive starts in the source. Nonzero only for a self-extracting
    archive, so ``payload_offset > 0`` is the test for one."""

    diagnostics: DiagnosticSummary = field(default_factory=DiagnosticSummary.empty)
    """What detection reported on its way to the answer."""

    corroborated: bool = field(default=False, compare=False, repr=False)
    """Internal, not part of the ``detect_format`` contract: whether a matching
    extension or an inner-TAR upgrade corroborated a content-probe claim."""
    # ``compare=False`` keeps it out of ``__eq__`` and ``repr=False`` out of ``__repr__``;
    # that is what holds it outside the public contract. Deliberate: ``False`` is
    # overloaded — it means both "a probe with no corroboration" and "not a probe at
    # all", so an exact magic hit reads False — and a bool cannot separate those.
    # ``probe-provenance-unconfirmed`` task 5.1 tracks the evidence-set shape that could.

    cost_receipt: DetectionCostReceipt | None = field(
        default=None, compare=False, repr=False
    )
    """Internal, not part of the ``detect_format`` contract: the work the whole call
    did, both passes when it followed a stub."""
    # Not merged into ``CostReceipt`` / ``ArchiveInfo.cost``. Public exposure is the
    # ``detection-result-surface`` change; kept here so tests and the fuzz harness can
    # assert the access-shape and budget invariants.

    unavailable_tiers: tuple[TierSkip, ...] = field(
        default=(), compare=False, repr=False
    )
    """Internal, not part of the ``detect_format`` contract: the detection tiers that
    did not run, and why (a missing package, the budget)."""
