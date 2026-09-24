"""Helpers for a command-line front end: terminal-safe display and enum spellings.

``archivey``'s own CLI (``archivey.cli``) is built on the public API only, and these are
the pieces of it that are not the library's core surface but that another front end
would need too. They are deliberately **not** re-exported from :mod:`archivey`; import
them from here::

    from archivey.cli_helpers import coerce_enum, display_path, escape_control_chars

**Stability.** Everything in ``__all__`` is public API with the same compatibility
promise as the names in :mod:`archivey`: signatures and behaviour change only through a
deliberate, documented break. Names starting with ``_`` are not covered.

Two groups:

- **Terminal-safe display** — :func:`escape_control_chars`, :func:`display_path`,
  :func:`quoted`. Archive member names are attacker-controlled, and so is anything built
  from them. See the section comment below.
- **Enum argument spellings** — :func:`coerce_enum`, :func:`coerce_enum_collection`,
  :func:`normalize_spelling`: the one place the library and the CLI turn a string such
  as ``"skip"`` or ``"blocked-member"`` into an enum member, so both accept the same
  vocabulary.

The library itself uses these too (:mod:`archivey.exceptions` escapes its own messages,
every public entry point coerces its enum arguments), so this module sits below
everything else and imports nothing from archivey at import time.
"""

from __future__ import annotations

import functools
import os
from collections.abc import Iterable
from enum import Enum
from pathlib import PurePath
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from archivey.exceptions import ArchiveyUsageError

__all__ = [
    "coerce_enum",
    "coerce_enum_collection",
    "display_path",
    "escape_control_chars",
    "normalize_spelling",
    "quoted",
]

# ---------------------------------------------------------------------------------------
# Terminal-safe display
#
# Archive member names are attacker-controlled, and so is anything built from them —
# destination paths, link targets, the messages that embed them. A name carrying
# ``\x1b[2K\r`` can erase the line reporting it and author what the operator reads
# instead. GNU ``ls`` and ``tar`` quote for the same reason.
# ---------------------------------------------------------------------------------------

# surrogateescape maps an undecodable byte 0xNN to U+DCNN, and only ever lands in this
# range. A lone surrogate arriving by any other route is escaped as itself, not reversed
# into a byte it never came from.
_SURROGATE_ESCAPE_START = 0xDC80
_SURROGATE_ESCAPE_END = 0xDCFF


def escape_control_chars(text: str) -> str:
    """Backslash-escape non-printable characters in attacker-controlled text.

    Rendering of each escape is delegated to :func:`repr`, whose escape set is exactly
    ``not str.isprintable()`` (verified across the whole code space) and whose spellings
    — ``\\n``, ``\\x1b``, ``\\u202e``, ``\\U0001d173`` — are the ones a Python developer
    already reads. Only two characters are handled here: backslash, which ``repr`` would
    escape but which we must escape ourselves since we are not emitting quotes, and a
    surrogateescaped byte, which is the one place we deliberately differ.

    A surrogateescaped byte renders as the **underlying octet** (``\\x80``) rather than
    ``repr``'s ``\\udc80``: the name never contained U+DC80: that code point is an
    artifact of decoding a byte that was not valid in the declared encoding, and the
    operator cares about the byte.

    Printable characters pass through, so ordinary ``café`` and ``日本語.txt`` member
    names stay readable — which rules out :func:`ascii` and the ``unicode_escape`` codec,
    and ``str.encode(errors="backslashreplace")`` is unusable here for the opposite
    reason: error handlers fire only on *unencodable* characters, so ``\\x1b`` — the byte
    this exists to defend against — would pass through raw.

    Backslash is escaped, without which the form would be ambiguous: a member literally
    named ``a\\nb`` would render identically to one containing a newline. The cost is
    that a native Windows path passed through this doubles its separators; callers
    holding a path should render it ``/``-separated first.

    **Not fully lossless.** A real C1 character and a surrogateescaped byte of the same
    value collide: both ``U+009B`` and ``U+DC9B`` render as ``\\x9b``, as do ``U+00A0``
    and byte ``0xA0``. Distinguishing them needs a separate notation for
    surrogateescaped bytes; the guarantee here is that the output is **inert** and that
    no character is silently dropped, not that the input is uniquely recoverable.
    """
    out: list[str] = []
    for ch in text:
        if ch == "\\":
            out.append("\\\\")
        elif ch.isprintable():
            out.append(ch)
        elif _SURROGATE_ESCAPE_START <= ord(ch) <= _SURROGATE_ESCAPE_END:
            out.append(f"\\x{ord(ch) & 0xFF:02x}")
        else:
            # repr of a single non-printable char is always single-quoted (both quote
            # characters are printable, so its quote-escaping never fires): strip them.
            out.append(repr(ch)[1:-1])
    return "".join(out)


def display_path(path: str | os.PathLike[str]) -> str:
    """Render a filesystem path for a message, ``/``-separated.

    Escaping doubles backslashes, so a native Windows path interpolated raw comes out
    as ``C:\\\\Users\\\\out\\\\a.txt`` — lossless, but harder to read and to paste back.
    Rendering ``/``-separated first leaves the escape nothing to double, and any
    backslash that does survive into the output is then a character in a *name*, which
    is exactly what should be escaped.

    This is the message-side counterpart of the rule the CLI's print sites already
    follow (`cli` spec: member-derived paths are rendered relative and ``/``-separated
    before escaping).
    """
    return PurePath(os.fspath(path)).as_posix()


def quoted(text: str) -> str:
    """Delimit archive-derived text in a message **without** escaping it.

    ``f"...{name!r}..."`` in a message would escape the name, and then the message's own
    escaping (:class:`~archivey.exceptions.ArchiveyError`, :class:`
    ~archivey.diagnostics.Diagnostic`) would escape the backslashes ``repr`` introduced,
    rendering a hostile name doubly-escaped: ``EV\\\\x1b[2KIL`` where ``EV\\x1b[2KIL`` was
    meant. Quoting here and letting the message escape once produces the delimiters
    without the doubling.

    Use this — not ``!r`` — for member names, link targets and paths interpolated into a
    message that escapes itself. ``!r`` remains correct for values that are not
    archive-derived (a format, a code, an exception type) and for ``logger.*`` call
    sites, whose records are **not** escaped by the CLI and where ``%r`` is what makes an
    interpolated name inert.

    The delimiter is **chosen**, the way ``repr`` chooses it, rather than escaped: a
    name may legitimately contain a quote, and ``'it's'`` leaves a reader unable to see
    where the name ends. Choosing costs nothing, whereas escaping the quote would write
    a backslash for the message escape to double — the failure this helper exists to
    avoid. A name containing *both* quote characters falls back to ``'`` and stays
    ambiguous; that tail is unreachable without escaping.
    """
    if "'" in text and '"' not in text:
        return f'"{text}"'
    return f"'{text}'"


# ---------------------------------------------------------------------------------------
# Enum argument spellings
#
# Every public entry point that declares an :class:`~enum.Enum` parameter accepts the
# member's **value** spelled as a string, and converts it at the boundary. ``extract(dest,
# overwrite="skip")`` is the same call as ``extract(dest,
# overwrite=OverwritePolicy.SKIP)``, and an unrecognised spelling raises
# :class:`~archivey.ArchiveyUsageError` there and then, naming what would have worked.
#
# **Why coerce rather than refuse.** The CLI and a throwaway script both hold strings, and
# the CLI was already doing this by hand (``ExtractionPolicy(policy)`` in
# ``cli/extract_cmd.py``) with its own dash-to-underscore fixup for ``AbortOn``. Doing it
# in one place makes the library and the CLI accept the same vocabulary instead of two
# that drift.
#
# **Why it must happen at the boundary.** Before this coercion existed, an unrecognised value was
# not refused, it was *ignored*: the consuming code tests ``policy is
# ExtractionPolicy.TRUSTED`` and a string is never any member, so it silently took the
# other branch. ``overwrite="skip"`` reached ``_apply_overwrite_policy``, matched neither
# ``ERROR`` nor ``SKIP``, and fell through to REPLACE — deleting the file the caller asked
# to keep. ``on_error="stop"`` behaved as ``CONTINUE``. Converting at the entry point is
# what makes those unreachable; a check that happens later, or not at all, is how they
# happened.
#
# **The cost, stated plainly.** Accepting a string makes the enum **values** public API.
# ``OverwritePolicy.SKIP.value`` cannot be renamed from ``"skip"`` afterwards without
# breaking callers silently, in a way that renaming the *member* would not. The member
# names are accepted too, for the same reason and with the same consequence. That is the
# price of the CLI-friendliness this buys, and it is deliberate.
#
# Spellings accepted for a member, all verified non-colliding by
# ``tests/test_enum_arguments.py``:
#
# * its ``value`` (``"skip"``, ``"blocked_member"``, ``"auto"``)
# * its member **name** (``"SKIP"``, ``"BLOCKED_MEMBER"``)
# * either of those with any case, and with ``-`` and ``_`` used interchangeably — so the
#   ``--abort-on blocked-member`` spelling from ``--help`` also works in a script
#
# ``format=`` is not an ``Enum`` (an :class:`~archivey.ArchiveFormat` is a ``(container,
# stream)`` pair), so its own spellings live in :mod:`archivey.internal.format_args`.
# ---------------------------------------------------------------------------------------
E = TypeVar("E", bound=Enum)


def normalize_spelling(text: str) -> str:
    """Fold one spelling of an enum member to its lookup key.

    Case and the ``-``/``_`` distinction are the two differences that carry no meaning
    across our spellings: the CLI writes ``blocked-member`` where the enum value is
    ``blocked_member``, and a caller typing ``"STRICT"`` means ``"strict"``. Everything
    else is preserved, including the dots in a format extension.
    """
    return text.strip().lower().replace("-", "_")


@functools.cache
def _lookup(enum_cls: type[E]) -> dict[str, E]:
    """Build (once per class) the normalized-spelling -> member map.

    Values win over names on a tie so a class whose value spells another member's name
    resolves to the value — the spelling the CLI and the docs use. No shipping enum has
    such a tie; ``test_enum_arguments.py`` fails if one is introduced, rather than
    leaving the precedence to be discovered.
    """
    table: dict[str, E] = {}
    for member in enum_cls:
        table.setdefault(normalize_spelling(member.name), member)
    for member in enum_cls:
        value = member.value
        if isinstance(value, str):
            table[normalize_spelling(value)] = member
    return table


def _takes(enum_cls: type[Enum], also_accepts: str | None) -> str:
    """The types the caller's parameter accepts, for a wrong-type message."""
    if also_accepts:
        return f"a {also_accepts} or a {enum_cls.__name__}"
    return f"a {enum_cls.__name__}"


def _accepted(enum_cls: type[Enum]) -> str:
    """The valid spellings, for the error message. Values where there are values.

    One rendering, the library's own, at every raise site. These messages reach a
    caller who passed a bad value to a Python function; the CLI never produces them,
    because argparse ``choices=`` refuses an unknown spelling before ``run_extract``
    is called at all.
    """
    spellings = [str(m.value) if isinstance(m.value, str) else m.name for m in enum_cls]
    return ", ".join(repr(s) for s in spellings)


def coerce_enum(
    value: object,
    enum_cls: type[E],
    *,
    call: str,
    param: str,
    also_accepts: str | None = None,
) -> E:
    """Return ``value`` as a member of ``enum_cls``, or raise ``ArchiveyUsageError``.

    A member of the class passes through untouched. A string is matched against the
    spellings described in the module docstring. Anything else — including a member of a
    *different* enum, which is the mistake a type checker would have caught — is
    refused. No caller passes ``None``: the one parameter that defaults to it,
    ``detect_format(budget=)``, handles ``None`` in ``_resolve_budget`` before reaching
    here, so there is no ``allow_none`` arm to maintain.

    ``also_accepts`` names a further type the *caller's* parameter takes but this helper
    does not handle, so the wrong-type message stays true to the signature the caller
    read. ``detect_format(budget=)`` is the case: it takes a ``DetectionBudget`` object
    as well as a preset, and a message naming only the preset reads as a denial that the
    object is allowed.
    """
    if isinstance(value, enum_cls):
        return value
    if isinstance(value, Enum):
        # Checked ahead of the string branch on purpose: several of our enums mix in
        # ``str`` (``AbortOn``, ``StreamFormat``, …), so a member of the *wrong* class
        # is a string too, and would otherwise be reported as a bad spelling rather
        # than as the wrong type — which is what it is.
        raise _usage_error(
            f"{call} takes {_takes(enum_cls, also_accepts)} for {param}, but got "
            f"{type(value).__name__}.{value.name}. "
            f"Accepted: {_accepted(enum_cls)}."
        )
    if isinstance(value, str):
        member = _lookup(enum_cls).get(normalize_spelling(value))
        if member is not None:
            return member
        raise _usage_error(
            f"{call} got {value!r} for {param}, which is not a valid "
            f"{enum_cls.__name__} value. Accepted: {_accepted(enum_cls)}."
        )
    raise _usage_error(
        f"{call} takes {_takes(enum_cls, also_accepts)} (or its name as a string) for "
        f"{param}, but got {value!r} ({type(value).__name__}). "
        f"Accepted: {_accepted(enum_cls)}."
    )


def coerce_enum_collection(
    values: object,
    enum_cls: type[E],
    *,
    call: str,
    param: str,
) -> frozenset[E]:
    """Return a collection argument as a ``frozenset`` of ``enum_cls`` members.

    ``None`` and an empty collection both mean "none of them". A bare string is refused
    rather than treated as an iterable of characters: ``abort_on="blocked_member"`` is a
    plausible typo for ``abort_on=["blocked_member"]``, and iterating it would silently
    produce a nonsense set.
    """
    if values is None:
        return frozenset()
    if isinstance(values, (str, bytes)):
        raise _usage_error(
            f"{call} takes a collection of {enum_cls.__name__} for {param}, but got "
            f"the bare string {values!r}. Pass a list or set, e.g. [{values!r}]."
        )
    if not isinstance(values, Iterable):
        raise _usage_error(
            f"{call} takes a collection of {enum_cls.__name__} for {param}, but got "
            f"{values!r} ({type(values).__name__})."
        )
    return frozenset(
        coerce_enum(item, enum_cls, call=call, param=param) for item in values
    )


def _usage_error(message: str) -> ArchiveyUsageError:
    """Build the error lazily: :mod:`archivey.exceptions` imports this module."""
    from archivey.exceptions import ArchiveyUsageError

    return ArchiveyUsageError(message)
