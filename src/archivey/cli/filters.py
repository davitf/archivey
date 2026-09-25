"""Member include/exclude filters for CLI verbs (fnmatch)."""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, TextIO

from archivey.cli.format import escape_path
from archivey.cost import StreamCapability
from archivey.types import ArchiveMember

if TYPE_CHECKING:
    from archivey.reader import ArchiveReader


class _Pattern:
    """One member pattern from the command line, as the CLI matches it.

    Member names use ``/`` and a directory's name ends in ``/``, but a person typing a
    pattern writes neither reliably. A pattern therefore matches a member when
    ``fnmatchcase`` matches the name against any of:

    - the pattern as written;
    - the pattern without a trailing ``/``, plus ``/`` (``docs`` selects ``docs/``);
    - that same base plus ``/*`` (``docs`` selects everything under ``docs/``, as
      ``tar`` does). fnmatch's ``*`` also matches ``/``, so this is recursive.

    On Windows a ``\\`` in the pattern is read as ``/``. Elsewhere it stays a literal
    character, because a TAR member name can contain one.

    This looseness is deliberate and CLI-only: the library's ``members=``, ``get()`` and
    ``open()`` match stored names exactly (maintainer decision, easier to widen later).
    Do not align the library with this.
    """

    __slots__ = ("forms", "text")

    def __init__(self, text: str, *, backslash_is_separator: bool) -> None:
        self.text = text
        pattern = text.replace("\\", "/") if backslash_is_separator else text
        base = pattern.rstrip("/")
        # dict.fromkeys: "docs/" as written is already its own "base + /" form.
        self.forms: tuple[str, ...] = (
            tuple(dict.fromkeys((pattern, base + "/", base + "/*")))
            if base
            else (pattern,)
        )

    def matches(self, name: str) -> bool:
        # fnmatchcase: deterministic across platforms (fnmatch is case-folding on Windows).
        return any(fnmatch.fnmatchcase(name, form) for form in self.forms)


def _compile(
    texts: Sequence[str] | None, *, backslash_is_separator: bool | None = None
) -> list[_Pattern]:
    if backslash_is_separator is None:
        backslash_is_separator = os.sep == "\\"
    return [
        _Pattern(text, backslash_is_separator=backslash_is_separator)
        for text in texts or ()
    ]


def member_predicate(
    includes: Sequence[str] | None,
    excludes: Sequence[str] | None,
    *,
    backslash_is_separator: bool | None = None,
) -> Callable[[ArchiveMember], bool] | None:
    """Build a ``members=`` predicate from positional includes and ``--exclude``.

    A member is selected when it matches any include (or none are given) and matches
    no exclude. Includes and excludes match the same way (see :class:`_Pattern`).
    Returns ``None`` when every member should be processed.
    ``backslash_is_separator`` defaults to whether this is Windows.
    """
    include_pats = _compile(includes, backslash_is_separator=backslash_is_separator)
    exclude_pats = _compile(excludes, backslash_is_separator=backslash_is_separator)
    if not include_pats and not exclude_pats:
        return None

    def matches(member: ArchiveMember) -> bool:
        name = member.name
        if include_pats and not any(p.matches(name) for p in include_pats):
            return False
        if exclude_pats and any(p.matches(name) for p in exclude_pats):
            return False
        return True

    return matches


def unmatched_include_patterns(
    includes: Sequence[str],
    members: Sequence[ArchiveMember],
    *,
    backslash_is_separator: bool | None = None,
) -> list[str]:
    """Return include patterns that match no member names (order preserved).

    Uses the same pattern matching as :func:`member_predicate`, over the includes
    only: ``--exclude`` is not considered, so an include whose every match is then
    excluded is not reported here.
    """
    if not includes:
        return []
    patterns = _compile(
        list(dict.fromkeys(includes)), backslash_is_separator=backslash_is_separator
    )
    hit = [False] * len(patterns)
    for member in members:
        name = member.name
        for index, pattern in enumerate(patterns):
            if not hit[index] and pattern.matches(name):
                hit[index] = True
        if all(hit):
            break
    return [
        pattern.text
        for pattern, matched in zip(patterns, hit, strict=True)
        if not matched
    ]


def warn_unmatched_includes(
    unmatched: Sequence[str],
    *,
    err: TextIO,
    dest_hint: bool = False,
) -> None:
    """Print stderr warnings for unmatched include patterns (cli-product P2 / Q3).

    When ``dest_hint`` is true and there is exactly one unmatched pattern that
    names an existing directory or ends with ``/``, append a ``-d`` suggestion
    (the unzip/7z positional-dest reflex).
    """
    for pattern in unmatched:
        extra = ""
        if (
            dest_hint
            and len(unmatched) == 1
            and (pattern.endswith("/") or os.path.isdir(pattern))
        ):
            # Strip a trailing slash for the suggested -d argument display.
            suggested = pattern.rstrip("/") or pattern
            # The pattern is the operator's own argv, but it is printed next to its
            # ``!r`` form on the same line, so it gets the same treatment.
            extra = f" (did you mean -d {escape_path(suggested)}?)"
        print(
            f"warning: pattern matched no members: {pattern!r}{extra}",
            file=err,
        )


def count_selected(
    members: Sequence[ArchiveMember],
    pred: Callable[[ArchiveMember], bool] | None,
) -> int:
    """How many members the include/exclude predicate selects."""
    if pred is None:
        return len(members)
    return sum(1 for member in members if pred(member))


def members_for_include_check(reader: ArchiveReader) -> list[ArchiveMember] | None:
    """Member list for unmatched-include / empty-selection checks, if safe.

    Prefer a cheap index. On a forward-only (streaming) reader, return ``None``
    instead of calling :meth:`~archivey.reader.ArchiveReader.members_report` —
    that would consume the sole forward pass and break a following
    ``extract_all`` / ``stream_members``. Callers then defer empty-selection
    handling to the operation outcome.
    """
    indexed = reader.members_report_if_available()
    if indexed is not None:
        return list(indexed)
    if reader.cost.stream_capability is StreamCapability.FORWARD_ONLY:
        return None
    return list(reader.members_report())
