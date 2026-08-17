"""Type validation for the public ``format=`` arguments.

Four public entry points take a format: :func:`~archivey.open_archive`,
:func:`~archivey.extract` and :func:`~archivey.format_availability` accept an
:class:`~archivey.ArchiveFormat`, and :func:`~archivey.open_stream` also accepts a
:class:`~archivey.StreamFormat` because a raw compressed stream genuinely has no
container. Both type checkers reject a wrong-typed call over ``src/``, so this module
exists for the caller those checkers never see — an untyped project, or a caller who
holds the ``StreamFormat`` that ``open_stream`` legitimately wanted and passes it on.

Given the wrong type anyway, each entry point used to invent something rather than
refuse: ``format_availability()`` returned a fabricated record whose ``format`` field
violated its own declared type, ``open_archive()`` / ``extract()`` let an
``AttributeError`` naming a private attribute cross the public boundary, and
``open_stream()`` discarded the argument and auto-detected. Validating here makes all
four answer the same way — :class:`~archivey.ArchiveyUsageError`, which sits outside
``ArchiveyError`` (ADR 0012) so ``except ArchiveyError`` cannot swallow a caller bug.

The message is the point as much as the type is: a ``StreamFormat`` is named alongside
the ``ArchiveFormat`` pairs that contain it, because the caller who reached here made
an understandable mistake and one message should end it.
"""

from __future__ import annotations

from archivey.exceptions import ArchiveyUsageError
from archivey.types import _FORMAT_NAMES, ArchiveFormat, ContainerFormat, StreamFormat

__all__ = ["check_archive_format", "check_stream_or_archive_format"]


def check_archive_format(value: object, *, call: str, allow_none: bool = True) -> None:
    """Raise ``ArchiveyUsageError`` unless ``value`` is an ``ArchiveFormat``.

    ``allow_none`` covers the entry points whose ``format=`` defaults to ``None``
    (auto-detect); ``format_availability()`` has no such default and takes one.
    """
    if isinstance(value, ArchiveFormat) or (value is None and allow_none):
        return
    _reject(value, call=call, expected="an ArchiveFormat")


def check_stream_or_archive_format(value: object, *, call: str) -> None:
    """Raise ``ArchiveyUsageError`` unless ``value`` is one of the two format types.

    ``open_stream``'s wider argument is deliberate, not an inconsistency to iron out:
    the thing it opens has no container, so the codec alone identifies it.
    """
    if value is None or isinstance(value, (ArchiveFormat, StreamFormat)):
        return
    _reject(value, call=call, expected="a StreamFormat or an ArchiveFormat")


def _reject(value: object, *, call: str, expected: str) -> None:
    detail = ""
    if isinstance(value, StreamFormat):
        detail = (
            " A StreamFormat is only the codec half of an ArchiveFormat's "
            "(container, stream) pair" + _pair_hint(value) + "."
        )
    raise ArchiveyUsageError(
        f"{call} takes {expected}, but got {_describe(value)}.{detail}"
    )


def _describe(value: object) -> str:
    if isinstance(value, StreamFormat):
        # Not repr(): `<StreamFormat.ZSTD: 'zst'>` buries the name the caller typed.
        return f"StreamFormat.{value.name}"
    if value is None:
        return "None"
    return f"{value!r} ({type(value).__name__})"


def _pair_hint(stream: StreamFormat) -> str:
    """Return the ``", so pass …"`` clause naming the pairs built on this codec."""
    if stream is StreamFormat.UNCOMPRESSED:
        # The "no codec" marker rather than a codec: every container-only format pairs
        # with it, so there is no short list of pairs worth naming.
        return ""

    # Read off the predefined names rather than a second table, so a new codec's pair
    # appears here the day it is declared in ``types``. Raw stream before tar: the
    # bare compressor is the likelier intent for a caller holding a StreamFormat.
    candidates = sorted(
        (
            fmt
            for fmt in _FORMAT_NAMES
            if fmt.stream is stream
            and fmt.container in (ContainerFormat.RAW_STREAM, ContainerFormat.TAR)
        ),
        key=lambda fmt: fmt.container is not ContainerFormat.RAW_STREAM,
    )
    if not candidates:
        return ""

    described = [
        f"ArchiveFormat.{fmt.display_name}"
        + (
            f" (a raw .{fmt.file_extension()} stream)"
            if fmt.container is ContainerFormat.RAW_STREAM
            else " (a tar compressed with it)"
        )
        for fmt in candidates
    ]
    return ", so pass the pair instead: " + " or ".join(described)
