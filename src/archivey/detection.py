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

from archivey.detection_cost import DetectionCostReceipt, TierSkip
from archivey.diagnostics import DiagnosticSummary
from archivey.types import ArchiveFormat

__all__ = ["DetectionConfidence", "FormatInfo"]


class DetectionConfidence(Enum):
    CERTAIN = "certain"  # exact magic-byte match at the expected offset
    PROBABLE = "probable"  # structural/content probe (inner-tar probe, SFX scan)
    # No confirmation strong enough to rely on: an extension-only guess, or a content
    # probe hit in the weak evidence class (today: extensionless Brotli whose first
    # meta-block is uncompressed/metadata).
    GUESS = "guess"


@dataclass(frozen=True)
class FormatInfo:
    """The result of :func:`detect_format` — the detected format plus how sure we are."""

    format: ArchiveFormat
    confidence: DetectionConfidence
    detected_by: str  # "magic", "extension", "content_probe", "sfx_scan", "directory"
    encoding_hint: str | None = None
    payload_offset: int = (
        0  # nonzero only for SFX archives (is-SFX == payload_offset > 0)
    )
    diagnostics: DiagnosticSummary = field(default_factory=DiagnosticSummary.empty)
    # Internal provenance for ``format_unconfirmed``: True when a matching extension or
    # an inner-TAR upgrade corroborated a content-probe claim. ``compare=False`` keeps it
    # out of the generated ``__eq__``, ``repr=False`` out of ``__repr__``; that is what
    # actually holds it outside the public ``detect_format`` contract — the field is
    # reachable but constrains nothing. Deliberate: ``False`` here is overloaded — it means
    # both "a probe with no corroboration" and "not a probe at all", so an exact magic hit
    # reads False — and a bool cannot separate those. ``probe-provenance-unconfirmed``
    # task 5.1 tracks the public evidence-set shape that could.
    corroborated: bool = field(default=False, compare=False, repr=False)
    # Detection's own cost receipt — not merged into ``CostReceipt`` / ``ArchiveInfo.cost``.
    # Public exposure on ``FormatInfo`` is ``detection-result-surface``; kept here so tests
    # and the fuzz harness can assert the access-shape and budget invariants. It is the
    # work the whole ``detect_format`` call did, both passes when it followed a stub.
    cost_receipt: DetectionCostReceipt | None = field(
        default=None, compare=False, repr=False
    )
    unavailable_tiers: tuple[TierSkip, ...] = field(
        default=(), compare=False, repr=False
    )
