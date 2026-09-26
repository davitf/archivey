"""Type validation for the public arguments that carry an object, not an enum.

This module holds the reusable half of the object-argument refusals. The rest live
beside their arguments:

* :mod:`archivey.internal.format_args` — ``format=``, which *coerces* rather than
  only refusing: ``format="zip"`` resolves to ``ArchiveFormat.ZIP``, and only an
  unrecognised value is an ``ArchiveyUsageError``.
* :mod:`archivey.config` — ``*Limits`` fields and ``ArchiveyConfig``'s own fields
* :mod:`archivey.internal.selection` — ``members=``
* :mod:`archivey.internal.password` — ``password=``
* :meth:`archivey.reader.ArchiveReader.open` — the member argument (needs the
  reader to tell "wrong type" from "not this reader's member")

What this module covers: ``config=``, ``limits=``, ``encoding=``, and the
``on_progress=`` / ``filter=`` callbacks. There is no useful conversion from a
wrong-typed one of these, so the answer is an error.

Every check answers with :class:`~archivey.ArchiveyUsageError`, which sits outside
``ArchiveyError`` (ADR 0012) so a caller's ``except ArchiveyError`` cannot swallow a
caller bug.

What this module is for is narrower than "validate everything". The error contract
already permits a short list of raw exceptions to reach a caller — ``KeyError`` for
an unknown member name, ``TypeError`` for ``len()``/``in``, ``io.UnsupportedOperation``
for an unsupported ``seek``, ``ValueError`` for I/O on a closed stream, and ``OSError``
unchanged. Those stay. What does not is an exception that crosses the boundary
**naming a private attribute of ours**: ``'str' object has no attribute
'diagnostic_policy'`` tells the caller who passed ``config="strict"`` nothing about
what they did wrong, and leaks an internal field name while failing to. That is the
"no internal leakage" rule in ``CONTRIBUTING.md``, and it is the shape every check
here exists to close.

The checks run at the **entry point**, not at the point of use. On ``config=`` that is
a message-quality choice; on ``limits=`` and the ``*Limits`` field types it is load
bearing, because the attribute those arguments are missing is not read until the
listing or the extraction is already under way.
"""

from __future__ import annotations

import codecs

from archivey.exceptions import ArchiveyUsageError

__all__ = [
    "check_callable",
    "check_config",
    "check_encoding",
    "check_extraction_limits",
    "check_instance",
    "describe_value",
]


def describe_value(value: object, *, expected: type | None = None) -> str:
    """Render ``value`` for a usage-error message: the value, then its type.

    A class object is described as the class rather than as an instance of ``type``,
    because ``config=ArchiveyConfig`` (the constructor, unparenthesised) is a common
    enough slip that the message should name it back. The "did you mean" hint is
    offered only when ``expected`` says an instance of that class would have been
    accepted: suggesting ``ListingLimits()`` to a caller who passed ``ListingLimits``
    where an ``ExtractionLimits`` belongs sends them to a second usage error.
    """
    if isinstance(value, type):
        if expected is not None and issubclass(value, expected):
            return (
                f"the {value.__name__} class itself (did you mean {value.__name__}()?)"
            )
        return f"the {value.__name__} class itself"
    if value is None:
        return "None"
    return f"{value!r} ({type(value).__name__})"


def check_instance(
    value: object,
    expected: type,
    *,
    call: str,
    allow_none: bool = True,
) -> None:
    """Raise ``ArchiveyUsageError`` unless ``value`` is an ``expected`` instance.

    ``allow_none`` covers the arguments whose default is ``None``, meaning "use the
    library default". It is not the common case any more: ``ArchiveyConfig``'s own
    fields all have real defaults, so ``None`` there is a wrong value rather than a
    way of asking for one.
    """
    if isinstance(value, expected) or (value is None and allow_none):
        return
    raise ArchiveyUsageError(
        f"{call} takes {_article(expected.__name__)} {expected.__name__}"
        f"{' or None' if allow_none else ''}, "
        f"but got {describe_value(value, expected=expected)}."
    )


def check_config(value: object, *, call: str) -> None:
    """Validate a ``config=`` argument.

    Deferred import: :mod:`archivey.config` imports :mod:`archivey.diagnostics`, and
    this module is reached from the internals that both of those sit above.
    """
    from archivey.config import ArchiveyConfig

    check_instance(value, ArchiveyConfig, call=call)


def check_extraction_limits(value: object, *, call: str) -> None:
    """Validate a ``limits=`` argument. Deferred import, as in :func:`check_config`."""
    from archivey.config import ExtractionLimits

    check_instance(value, ExtractionLimits, call=call)


def check_callable(value: object, *, call: str) -> None:
    """Raise ``ArchiveyUsageError`` unless ``value`` is callable or ``None``.

    A non-callable here is otherwise found only when the first member is reported,
    which on ``on_progress=`` means after output has already been written.
    """
    if value is None or callable(value):
        return
    raise ArchiveyUsageError(
        f"{call} takes a callable or None, but got {describe_value(value)}."
    )


def check_encoding(value: object, *, call: str, allow_none: bool = True) -> None:
    """Raise ``ArchiveyUsageError`` unless ``value`` names a usable byte codec.

    This is the one check here that is about a *value* rather than a type, and it is
    worth the lookup because all three ways of getting it wrong failed differently and
    none of them named the argument:

    * an unregistered name (``"utf8-"``, a typo) raised ``LookupError``;
    * a text-only codec (``"rot13"``, ``"base64"``) raised a different ``LookupError``,
      several frames further in, only once a name was actually decoded;
    * a non-string (``0``) was **silently ignored** and the backend auto-detected.

    ``codecs.lookup`` resolves aliases, so the check accepts exactly what the decode
    later accepts. The ``_is_text_encoding`` flag is what ``bytes.decode`` itself tests
    — a codec without it transforms text and cannot decode a member name.
    """
    if value is None and allow_none:
        return
    if not isinstance(value, str):
        raise ArchiveyUsageError(
            f"{call} takes a codec name as a str"
            f"{' or None' if allow_none else ''}, but got {describe_value(value)}."
        )
    # The advice has to follow ``allow_none`` too. On a field that refuses ``None``,
    # telling the caller to pass it sends them from one usage error to the next.
    advice = "Pass a codec name such as 'utf-8', 'cp437' or 'latin-1'" + (
        ", or None to let the backend choose." if allow_none else "."
    )
    try:
        info = codecs.lookup(value)
    except LookupError:
        raise ArchiveyUsageError(
            f"{call} got {value!r}, which is not a codec Python knows. {advice}"
        ) from None
    if not info._is_text_encoding:
        raise ArchiveyUsageError(
            f"{call} got {value!r}, which is a byte-to-byte transform rather than a "
            f"character encoding, so it cannot decode a member name. {advice}"
        )


def _article(name: str) -> str:
    return "an" if name[:1].upper() in "AEIOU" else "a"
