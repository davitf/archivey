"""Member include/exclude filters for CLI verbs (fnmatch)."""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Callable, Sequence
from typing import TextIO

from archivey.cli.format import escape_path
from archivey.diagnostics import MemberListReport
from archivey.types import ArchiveMember


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


class MemberSelection:
    """The positional includes and ``--exclude`` patterns of one command.

    Calling it is the ``members=`` predicate: a member is selected when it matches any
    include (or none are given) and matches no exclude. Includes and excludes match the
    same way (see :class:`_Pattern`). Each call also records which includes matched and
    whether anything was selected, so the pass that runs the predicate is also the one
    that finds unmatched patterns. Without an index, a second pass to find them would
    decompress the archive again. Calling it twice for one member records nothing new,
    so it can see a free index first and then the run's own pass.
    ``backslash_is_separator`` defaults to whether this is Windows.
    """

    def __init__(
        self,
        includes: Sequence[str] | None,
        excludes: Sequence[str] | None,
        *,
        backslash_is_separator: bool | None = None,
    ) -> None:
        # dict.fromkeys: a repeated include is matched, and reported, once.
        self._includes = _compile(
            list(dict.fromkeys(includes or ())),
            backslash_is_separator=backslash_is_separator,
        )
        self._excludes = _compile(
            excludes, backslash_is_separator=backslash_is_separator
        )
        self._include_hit = [False] * len(self._includes)
        self._included_any = False
        self._selected_any = False
        self._settled = False

    @property
    def predicate(self) -> Callable[[ArchiveMember], bool] | None:
        """This selection as a ``members=`` argument; ``None`` when no pattern was
        given, so every member is processed."""
        return self if self._includes or self._excludes else None

    def __call__(self, member: ArchiveMember) -> bool:
        name = member.name
        included = not self._includes
        for index, pattern in enumerate(self._includes):
            # Once the member is in, only the includes not yet seen to match are left
            # to check.
            if included and self._include_hit[index]:
                continue
            if pattern.matches(name):
                included = True
                self._include_hit[index] = True
        if not included:
            return False
        self._included_any = True
        if any(pattern.matches(name) for pattern in self._excludes):
            return False
        self._selected_any = True
        return True

    def unmatched_includes(self) -> list[str]:
        """The includes that matched no member offered so far, in command-line order."""
        return [
            pattern.text
            for pattern, hit in zip(self._includes, self._include_hit, strict=True)
            if not hit
        ]

    @property
    def selects_nothing(self) -> bool:
        """Whether the command must fail because the patterns select nothing.

        True when every include matched nothing, or when ``--exclude`` removed every
        member the includes (or, with no include, the archive) offered. An archive
        with no members and only ``--exclude`` patterns is not such a case: the
        patterns removed nothing.
        """
        if self._selected_any:
            return False
        return bool(self._includes) or self._included_any

    def settle_from(
        self, listing: MemberListReport, *, err: TextIO, dest_hint: bool = False
    ) -> list[ArchiveMember]:
        """Offer every member of ``listing``; return the members it selects.

        A complete listing settles the patterns: this prints the warnings
        (:meth:`report`) and sets :attr:`settled`. A listing that ends in damage
        holds only the members before the damage, so it cannot say that a pattern
        matches nothing. It settles nothing and prints nothing. A verb with a pass of
        its own judges the patterns after that pass; ``list`` has no other pass and
        prints the listing error instead.
        """
        selected = [member for member in listing if self(member)]
        if listing.error is None:
            self._settled = True
            self.report(err=err, dest_hint=dest_hint)
        return selected

    @property
    def settled(self) -> bool:
        """Whether a complete listing judged the patterns (:meth:`settle_from`)."""
        return self._settled

    def report(self, *, err: TextIO, dest_hint: bool = False) -> bool:
        """Warn about what the members offered so far say of the patterns; return
        :attr:`selects_nothing`."""
        _warn_unmatched_includes(
            self.unmatched_includes(), err=err, dest_hint=dest_hint
        )
        if self._included_any and not self._selected_any:
            matched = " the patterns matched" if self._includes else ""
            print(
                f"warning: no members selected: --exclude removed every member{matched}",
                file=err,
            )
        return self.selects_nothing


def _warn_unmatched_includes(
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
