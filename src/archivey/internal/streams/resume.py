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

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from archivey.internal.streams.streamtools.binaryio import ask_resume_offset

__all__ = [
    "ResumeReachedStreamEnd",
    "ask_resume_offset",
    "ask_seek_resume_offset",
    "planning_seek",
]

_PLANNING_SEEK: ContextVar[bool] = ContextVar("archivey_planning_seek", default=False)


def ask_seek_resume_offset(inner: object | None, target: int) -> int | None:
    """``ask_resume_offset`` for a caller about to seek ``inner`` to ``target``.

    The plain query reads each seek-point table as it stands, so a diagnostic never
    changes the cost it reports. A caller planning a seek wants the answer the seek
    itself would act on: a ``DecompressorStream`` builds its index on a forward seek
    (an lzip or xz trailer read) and may then jump. While this call runs, such a
    stream builds that index first (:func:`planning_seek`). The flag rides a context
    variable, so the wrappers that forward the query need no change.
    """
    with _planning_seek():
        return ask_resume_offset(inner, target)


@contextmanager
def _planning_seek() -> Iterator[None]:
    token = _PLANNING_SEEK.set(True)
    try:
        yield
    finally:
        _PLANNING_SEEK.reset(token)


def planning_seek() -> bool:
    """True inside :func:`ask_seek_resume_offset`: answer for the seek about to run."""
    return _PLANNING_SEEK.get()


class ResumeReachedStreamEnd(Exception):
    """A decode resumed mid-stream reached the end of its stream. The stream's checksum
    covers output from before the resume point, which this decode cannot check, so the
    caller decodes again from the start.

    Raised by ``DeflateResumeDecoder`` (the gzip CRC-32 or zlib Adler-32) and by
    ``Bzip2ResumeDecoder`` (the combined CRC, whose failure ``bz2`` reports the same
    way as a damaged block, so that decoder raises this on either).

    It is control flow, not an error a caller may see: it is not an ``ArchiveyError``.
    Only ``_StdlibOnAcceleratorError`` in ``codecs/stdlib_takeover.py`` adds resume points to a
    decoder, and every call of its that can decode (``read`` and ``seek``; ``readinto``
    and ``readall`` go through ``read``) catches it. A new caller of either decoder
    must catch it the same way.
    """
