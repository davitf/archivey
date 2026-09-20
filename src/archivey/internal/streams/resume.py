"""Rewind-cost query used by ``ArchiveStream._maybe_warn_rewind``.

This is an archivey concept — seek-point tables on decompressing streams.
Implementations:

- own a table — ``DecompressorStream``, ``_AcceleratorStream``
- preserve that offset space — ``ArchiveStream``, ``OutputCountingStream``,
  ``VerifyingStream``, ``_GzipTruncationCheckStream``
- translate the offset space — ``AesDecryptStream`` (ciphertext to plaintext),
  ``SlicingStream`` (contiguous window; ``SharedView`` inherits it)

``DelegatingStream`` does not grow it.

The duck-typing helper itself is generic ``getattr`` plumbing, so it lives in
:mod:`archivey.internal.streams.streamtools.binaryio` and this module
re-exports it as the archivey-facing name.
"""

from __future__ import annotations

from archivey.internal.streams.streamtools.binaryio import ask_resume_offset

__all__ = ["ask_resume_offset"]
