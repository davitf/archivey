"""Rewind-cost query used by ``ArchiveStream._maybe_warn_rewind``.

This is an archivey concept — seek-point tables on decompressing streams —
so it lives outside ``streamtools``. Wrappers that preserve the inner's
offset space and sit in a decompressed stream's chain forward the query;
:class:`~archivey.internal.streams.streamtools.base.DelegatingStream` does not.
"""

from __future__ import annotations


def forward_resume_offset(inner: object | None, target: int) -> int | None:
    """Ask ``inner`` for the decompressed offset a seek to ``target`` would resume from.

    ``None`` means the inner cannot answer (no method, or a non-int result), which
    the caller treats as "no cost signal" rather than "free".
    """
    if inner is None:
        return None
    ask = getattr(inner, "nearest_resume_offset", None)
    if ask is None:
        return None
    offset = ask(target)
    return offset if isinstance(offset, int) else None
