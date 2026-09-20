"""Coercion for the public ``format=`` arguments.

Four public entry points take a format: :func:`~archivey.open_archive`,
:func:`~archivey.extract` and :func:`~archivey.format_availability` take an
:class:`~archivey.ArchiveFormat`, and :func:`~archivey.open_stream` also takes a
:class:`~archivey.StreamFormat` because a raw compressed stream genuinely has no
container.

Each of them accepts the format **spelled as a string** and converts it here, for the
same reason the enum arguments beside it do (see
:mod:`archivey.internal.enum_args`): the CLI and a throwaway script hold strings, and
one vocabulary shared with the CLI beats two that drift. Given something that is neither
a format nor a spelling of one, every entry point answers the same way —
:class:`~archivey.ArchiveyUsageError`, which sits outside ``ArchiveyError`` (ADR 0012)
so ``except ArchiveyError`` cannot swallow a caller bug.

An ``ArchiveFormat`` is a ``(container, stream)`` pair rather than an ``Enum``, so it
has no ``value`` to spell. Its two spellings are:

* the **file extension**, which is what a CLI user or a script author would type —
  ``"zip"``, ``"tar.gz"``, ``"7z"`` — read off :meth:`ArchiveFormat.file_extension` so
  a new format is spellable the day it is declared;
* the **attribute name** — ``"TAR_GZ"``, ``"SEVEN_Z"`` — read off ``_FORMAT_NAMES``, the
  same table ``repr`` and ``display_name`` use.

``DIRECTORY`` and ``UNKNOWN`` have no file extension, so only the name spells those.
Case is ignored and ``-``/``_`` are interchangeable, as everywhere else. No two formats
share an extension; ``tests/test_format_arguments.py`` fails if that stops being true.

``open_stream`` resolves a string against ``ArchiveFormat`` first and ``StreamFormat``
second. The ten spellings the two types share (``"gz"`` is both ``ArchiveFormat.GZ`` and
``StreamFormat.GZIP``) are the same format described at two levels, so the order picks a
representation rather than a meaning, and it picks the same one ``open_archive`` would.

The message is the point as much as the type is: a ``StreamFormat`` **object** handed to
an ``ArchiveFormat`` parameter is still refused rather than widened, and named alongside
the ``ArchiveFormat`` pairs that contain it, because the caller who reached there made
an understandable mistake and one message should end it.
"""

from __future__ import annotations

import functools
from typing import Literal, overload

from archivey.exceptions import ArchiveyUsageError
from archivey.internal.enum_args import normalize_spelling
from archivey.types import _FORMAT_NAMES, ArchiveFormat, ContainerFormat, StreamFormat

__all__ = ["coerce_archive_format", "coerce_stream_or_archive_format"]


@functools.cache
def _archive_format_spellings() -> dict[str, ArchiveFormat]:
    """Normalized spelling -> format, for the two spellings named in the module docstring.

    Names go in first and extensions second, so an extension wins a tie. None exists
    today — the test asserts that — but the extension is the documented, CLI-facing
    spelling, so it is the one to prefer if one ever appears.
    """
    table: dict[str, ArchiveFormat] = {}
    for fmt, name in _FORMAT_NAMES.items():
        table.setdefault(normalize_spelling(name), fmt)
    for fmt in _FORMAT_NAMES:
        extension = fmt.file_extension()
        if extension:
            table[normalize_spelling(extension)] = fmt
    return table


@functools.cache
def _stream_format_spellings() -> dict[str, StreamFormat]:
    table: dict[str, StreamFormat] = {}
    for member in StreamFormat:
        table.setdefault(normalize_spelling(member.name), member)
    for member in StreamFormat:
        table[normalize_spelling(member.value)] = member
    return table


def _accepted_archive_formats() -> str:
    spellings = sorted(
        fmt.file_extension() or name for fmt, name in _FORMAT_NAMES.items()
    )
    return ", ".join(repr(s) for s in spellings)


@overload
def coerce_archive_format(
    value: object, *, call: str, allow_none: Literal[False]
) -> ArchiveFormat: ...


@overload
def coerce_archive_format(
    value: object, *, call: str, allow_none: Literal[True] = True
) -> ArchiveFormat | None: ...


def coerce_archive_format(
    value: object, *, call: str, allow_none: bool = True
) -> ArchiveFormat | None:
    """Return ``value`` as an ``ArchiveFormat``, or raise ``ArchiveyUsageError``.

    ``allow_none`` covers the entry points whose ``format=`` defaults to ``None``
    (auto-detect); ``format_availability()`` has no such default and passes ``False``.
    """
    if value is None and allow_none:
        return None
    if isinstance(value, ArchiveFormat):
        return value
    # Before the string branch: StreamFormat mixes in ``str``, and it earns its own
    # message rather than being reported as an unrecognised spelling.
    if isinstance(value, StreamFormat):
        _reject_stream_format(value, call=call)
    if isinstance(value, str):
        fmt = _archive_format_spellings().get(normalize_spelling(value))
        if fmt is not None:
            return fmt
    raise ArchiveyUsageError(
        f"{call} takes an ArchiveFormat (or its name as a string), but got "
        f"{_describe(value)}. Accepted: {_accepted_archive_formats()}."
    )


def coerce_stream_or_archive_format(
    value: object, *, call: str
) -> ArchiveFormat | StreamFormat | None:
    """Return ``value`` as one of the two format types, or raise ``ArchiveyUsageError``.

    ``open_stream``'s wider argument is deliberate, not an inconsistency to iron out:
    the thing it opens has no container, so the codec alone identifies it.
    """
    if value is None:
        return None
    if isinstance(value, (ArchiveFormat, StreamFormat)):
        return value
    if isinstance(value, str):
        spelling = normalize_spelling(value)
        fmt = _archive_format_spellings().get(spelling)
        if fmt is not None:
            return fmt
        stream = _stream_format_spellings().get(spelling)
        if stream is not None:
            return stream
    raise ArchiveyUsageError(
        f"{call} takes a StreamFormat or an ArchiveFormat (or either spelled as a "
        f"string), but got {_describe(value)}. "
        f"Accepted: {_accepted_archive_formats()}."
    )


def _reject_stream_format(value: StreamFormat, *, call: str) -> None:
    raise ArchiveyUsageError(
        f"{call} takes an ArchiveFormat, but got {_describe(value)}. A StreamFormat is "
        f"only the codec half of an ArchiveFormat's (container, stream) pair"
        + _pair_hint(value)
        + "."
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
