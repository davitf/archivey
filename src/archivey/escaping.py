"""Escaping of attacker-controlled text for safe terminal display.

Archive member names are attacker-controlled, and so is anything built from them —
destination paths, link targets, the messages that embed them. A name carrying
``\\x1b[2K\\r`` can erase the line reporting it and author what the operator reads
instead. GNU ``ls`` and ``tar`` quote for the same reason.

This module is deliberately dependency-free: :mod:`archivey.exceptions` escapes its
own messages with it, and ``archivey.cli`` escapes member names for display, so it
has to sit below both without importing either.

**Internal by convention.** Nothing here is exported from :mod:`archivey`, and none of
it carries a stability promise — :func:`quoted` and :func:`display_path` in particular
are call-site discipline for archivey's own messages (see
:class:`~archivey.exceptions.ArchiveyError`) rather than an API for callers. If you are
embedding archivey and need to render a member name safely in your own output,
:func:`escape_control_chars` is the one to ask for; say so on the tracker and it can be
promoted deliberately, with a requirement behind it.
"""

from __future__ import annotations

import os
from pathlib import PurePath

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
