"""Seek-point plumbing shared by the decompressing streams.

``ask_resume_offset`` is the rewind-cost query used by
``ArchiveStream._maybe_warn_rewind``. This is an archivey concept — seek-point tables
on decompressing streams. Implementations:

- own a table — ``DecompressorStream``, ``_AcceleratorStream``
- preserve that offset space — ``ArchiveStream``, ``OutputCountingStream``,
  ``VerifyingStream``, ``_GzipTruncationCheckStream``
- translate the offset space — ``AesDecryptStream`` (ciphertext to plaintext),
  ``SlicingStream`` (contiguous window; ``SharedView`` inherits it)

``DelegatingStream`` does not grow it.

The duck-typing helper itself is generic ``getattr`` plumbing, so it lives in
:mod:`archivey.internal.streams.streamtools.binaryio` and this module
re-exports it as the archivey-facing name.

:class:`ResumeReachedStreamEnd` is the signal both mid-stream resume decoders
(``deflate_resume`` and ``bzip2_resume``) raise when they cannot give a verdict.
"""

from __future__ import annotations

from archivey.internal.streams.streamtools.binaryio import ask_resume_offset

__all__ = ["ResumeReachedStreamEnd", "ask_resume_offset"]


class ResumeReachedStreamEnd(Exception):
    """A decode resumed mid-stream reached the end of its stream. The stream's checksum
    covers output from before the resume point, which this decode cannot check, so the
    caller decodes again from the start.

    Raised by ``DeflateResumeDecoder`` (the gzip CRC-32 or zlib Adler-32) and by
    ``Bzip2ResumeDecoder`` (the combined CRC, whose failure ``bz2`` reports the same
    way as a damaged block, so that decoder raises this on either).

    It is control flow, not an error a caller may see: it is not an ``ArchiveyError``.
    Only ``_StdlibOnAcceleratorError`` in ``codecs.py`` adds resume points to a
    decoder, and every call of its that can decode (``read`` and ``seek``; ``readinto``
    and ``readall`` go through ``read``) catches it. A new caller of either decoder
    must catch it the same way.
    """
