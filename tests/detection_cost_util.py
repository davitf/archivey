"""Test oracle for detection cost: does a receipt stay inside the budget it ran under?

The library never asks this question; the tests do, to prove that detection reads and
decodes no more than its :class:`~archivey.detection_cost.DetectionBudget` declared.
"""

from __future__ import annotations

from archivey.detection_cost import DetectionBudget, DetectionCostReceipt
from archivey.internal.streams.brotli_framing import CHAIN_HEADER_READ


def within_budget(receipt: DetectionCostReceipt, budget: DetectionBudget) -> bool:
    """Whether aggregate measured work in ``receipt`` stays inside ``budget``'s limits.

    Seek-based content-probe ``read_at`` charges ``unique_bytes_read`` without growing
    the prefix. Those bytes are allowed up to ``max_probe_links * CHAIN_HEADER_READ``
    on top of the prefix/far/scan ceiling.

    ``prefix_bytes`` is the one counter not compared: it bills overlapping requests in
    full, so ``unique_bytes_read`` stands in for it.

    The budget applies per pass: every limit is multiplied by ``receipt.passes``, so a
    receipt that followed a stub to its sibling volume is judged against two budgets,
    the work each pass was allowed.
    """
    # ``passes`` is 1 or 2 from ``detect_format``; a receipt built by hand can carry
    # anything, and fewer than one pass is judged as one.
    n = max(1, receipt.passes)
    probe_allowance = budget.max_probe_links * CHAIN_HEADER_READ
    read_ceiling = max(
        budget.max_prefix_bytes, budget.max_far_bytes, budget.max_scan_bytes
    )
    return (
        receipt.unique_bytes_read <= n * (read_ceiling + probe_allowance)
        and receipt.far_bytes <= n * budget.max_far_bytes
        and receipt.scanned_bytes <= n * budget.max_scan_bytes
        and receipt.decode_input <= n * budget.max_decode_input
        and receipt.decode_output <= n * budget.max_decode_output
    )
