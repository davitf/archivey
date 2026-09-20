"""Rewind-cost query used by ``ArchiveStream._maybe_warn_rewind``.

This is an archivey concept — seek-point tables on decompressing streams.
The table implementations live on the streams that own one
(``DecompressorStream``, ``_AcceleratorStream``) and on wrappers that preserve
that offset space (``ArchiveStream``, ``OutputCountingStream``,
``VerifyingStream``, ``_GzipTruncationCheckStream``). ``DelegatingStream``
does not grow it.

The duck-typing helper itself is generic ``getattr`` plumbing, so it lives in
:mod:`archivey.internal.streams.streamtools.binaryio` and this module
re-exports it as the archivey-facing name.
"""

from __future__ import annotations

from archivey.internal.streams.streamtools.binaryio import ask_resume_offset

__all__ = ["ask_resume_offset"]
