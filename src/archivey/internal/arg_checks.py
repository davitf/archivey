"""Type validation for the public arguments that carry an object, not an enum.

This module holds the reusable half of the object-argument refusals. The rest live
beside their arguments:

* :mod:`archivey.internal.format_args` — ``format=``, which *coerces* rather than
  only refusing: ``format="zip"`` resolves to ``ArchiveFormat.ZIP``, and only an
  unrecognised value is an ``ArchiveyUsageError``.
* :mod:`archivey.config` — ``ArchiveyConfig``'s own fields, and the ``__post_init__``
  of each ``*Limits`` dataclass (``DetectionBudget`` in
  :mod:`archivey.detection_cost` too), which call :func:`check_limit_fields` here
* :mod:`archivey.internal.selection` — ``members=``
* :mod:`archivey.internal.password` — ``password=``
* :meth:`archivey.reader.ArchiveReader.open` — the member argument (needs the
  reader to tell "wrong type" from "not this reader's member")

What this module covers: ``config=``, ``limits=``, ``encoding=``, the
``on_progress=`` / ``filter=`` callbacks, the numeric limit fields, and the empty
string as a path. There is no useful conversion from a wrong one of these, so the
answer is an error.

Every check answers with :class:`~archivey.ArchiveyUsageError`, which sits outside
``ArchiveyError`` (ADR 0012) so a caller's ``except ArchiveyError`` cannot swallow a
caller bug. The exception is :func:`check_path_not_empty`, which raises
``ValueError``: the path parameters already answer a wrong type with ``TypeError``,
and a wrong value of the right type is a ``ValueError`` (DR-15).

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
import math
from dataclasses import fields
from typing import TYPE_CHECKING

from archivey.exceptions import ArchiveyUsageError

if TYPE_CHECKING:
    from _typeshed import DataclassInstance

__all__ = [
    "check_callable",
    "check_config",
    "check_encoding",
    "check_extraction_limits",
    "check_instance",
    "check_limit",
    "check_limit_fields",
    "check_path_not_empty",
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


def check_limit(
    value: object,
    *,
    cls: str,
    field_name: str,
    allow_float: bool = False,
    allow_none: bool = True,
) -> None:
    """Validate one numeric limit field at construction.

    The guards these fields drive are all comparisons, so a wrong-typed one is not
    found until something is actually being counted — ``ListingLimits(max_members="x")``
    built fine and then failed mid-listing as ``TypeError: '>' not supported between
    instances of 'int' and 'str'``, naming neither the field nor the class. A limit is
    a promise about a future operation; checking it where the caller wrote it is the
    only place the message can still name what they wrote.

    ``bool`` is refused explicitly: it is an ``int`` subclass, so ``max_members=True``
    would otherwise pass and cap the listing at one member. The type test is spelled
    out per branch rather than parameterised, because a parameterised ``isinstance``
    narrows nothing and leaves the comparison below unprovable.

    Two further shapes are refused for the same reason the wrong type is, namely that
    they switch a guard off silently rather than loudly:

    * ``allow_none=False`` for a field that is not ``| None``. ``None`` reads as
      "disable this guard" on every other field, but ``ratio_activation_threshold``
      is read unconditionally, so a ``None`` there is a ``TypeError`` during the
      extraction rather than a disabled guard.
    * a NaN or an infinity on a float field. Every comparison against a NaN is false
      and nothing ever exceeds an infinity, so ``max_ratio=float("nan")`` constructs,
      extracts, and enforces nothing. ``None`` is the way to say that on purpose.
    """
    if value is None:
        if allow_none:
            return
        raise ArchiveyUsageError(
            f"{cls}.{field_name} is not optional and takes "
            f"{'a number' if allow_float else 'an int'}, but got None."
        )
    if isinstance(value, bool):
        number: int | float | None = None
    elif isinstance(value, int):
        number = value
    elif allow_float and isinstance(value, float):
        number = value
    else:
        number = None

    if number is None:
        raise ArchiveyUsageError(
            f"{cls}.{field_name} takes {'a number' if allow_float else 'an int'}"
            f"{' or None' if allow_none else ''}, but got {describe_value(value)}."
        )
    if isinstance(number, float) and not math.isfinite(number):
        raise ArchiveyUsageError(
            f"{cls}.{field_name} takes a finite number, but got {value!r}. A NaN "
            f"compares false against everything and an infinity is never exceeded, so "
            f"either one would leave this guard switched off without saying so; pass "
            f"None if that is what you want."
        )
    if number < 0:
        raise ArchiveyUsageError(
            f"{cls}.{field_name} cannot be negative, but got {value!r}."
            + (" Pass None to disable this guard." if allow_none else "")
        )


# The annotations a limits field may have, as (allow_float, allow_none).
_LIMIT_ANNOTATIONS: dict[str, tuple[bool, bool]] = {
    "int": (False, False),
    "int | None": (False, True),
    "float | None": (True, True),
}


def check_limit_fields(limits: DataclassInstance, *, cls: str) -> None:
    """Run :func:`check_limit` on every field of a limits dataclass, in field order.

    The limits dataclasses are ``ExtractionLimits``, ``ListingLimits``,
    ``DecoderLimits``, ``SpoolLimits`` (all in :mod:`archivey.config`) and
    ``DetectionBudget`` (:mod:`archivey.detection_cost`). This module is a leaf, so
    both can import it: ``config`` imports ``detection_cost``, so the check cannot live
    in ``config``.

    ``allow_float`` and ``allow_none`` come from the field's annotation (a string,
    under ``from __future__ import annotations``), which must be one of
    ``_LIMIT_ANNOTATIONS``. ``cls`` is passed in rather than read from
    ``type(limits)``, so a user subclass still gets the documented class in the message.
    """
    for f in fields(limits):
        flags = _LIMIT_ANNOTATIONS.get(str(f.type))
        if flags is None:
            raise AssertionError(
                f"{cls}.{f.name} is annotated {f.type!r}, which check_limit_fields "
                f"does not know; spell it as one of {sorted(_LIMIT_ANNOTATIONS)}."
            )
        allow_float, allow_none = flags
        check_limit(
            getattr(limits, f.name),
            cls=cls,
            field_name=f.name,
            allow_float=allow_float,
            allow_none=allow_none,
        )


def check_path_not_empty(value: object, *, call: str) -> None:
    """Raise ``ValueError`` if ``value`` is the empty string.

    ``Path("")`` is ``Path(".")``, so an empty string (typically an unset environment
    variable) would otherwise name the current directory: a source opens it as a
    directory archive and a destination extracts into it. ``open("")`` raises too.
    Only ``str`` is checked; an empty ``Path`` cannot be told apart from ``Path(".")``.
    """
    if isinstance(value, str) and not value:
        raise ValueError(
            f"{call} got an empty path; an empty string would name the current "
            f'directory. Pass "." to mean the current directory.'
        )
