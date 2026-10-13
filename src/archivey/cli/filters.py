"""Member include/exclude filters for CLI verbs (``archivey.paths.match_glob``)."""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from typing import TextIO

from archivey.cli.errors import CliError
from archivey.cli.exit_codes import EXIT_USAGE
from archivey.cli.format import escape_path
from archivey.diagnostics import MemberListReport
from archivey.exceptions import ArchiveyUsageError
from archivey.paths import match_glob
from archivey.types import ArchiveMember


def _compile(
    texts: Sequence[str], *, backslash_is_separator: bool | None = None
) -> list[Callable[[ArchiveMember], bool]]:
    """Build one :func:`archivey.paths.match_glob` predicate per command-line pattern.

    A pattern means the same thing on the command line as
    ``match_glob(pattern, with_contents=True)`` does as a ``members=`` argument: ``*``
    stays inside one folder, ``**`` matches any depth, and a pattern that matches a
    folder also selects everything under it, as ``tar`` does. ``docs`` and ``docs/``
    select ``docs/`` and its contents, but not a file named ``docs.txt``.

    On Windows a ``\\`` in the pattern is read as ``/``. Elsewhere it stays a literal
    character, because a TAR member name can contain one. That is the one CLI-only rule.
    A pattern ``match_glob`` refuses (an empty one, ``a//b``, a reversed range such as
    ``[z-a]``) is a usage error.
    """
    if backslash_is_separator is None:
        backslash_is_separator = os.sep == "\\"
    predicates: list[Callable[[ArchiveMember], bool]] = []
    for text in texts:
        pattern = text.replace("\\", "/") if backslash_is_separator else text
        try:
            predicates.append(match_glob(pattern, with_contents=True))
        except ArchiveyUsageError as exc:
            # match_glob's own reason, so the message names only what is wrong here.
            reason = exc.message.removeprefix("match_glob(): ")
            raise CliError(
                f"invalid member pattern: {reason}", code=EXIT_USAGE
            ) from None
    return predicates


class MemberSelection:
    """The positional includes and ``--exclude`` patterns of one command.

    Calling it is the ``members=`` predicate: a member is selected when it matches any
    include (or none are given) and matches no exclude. Includes and excludes match the
    same way (see :func:`_compile`). Each call also records which includes matched and
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
        self._include_texts = list(dict.fromkeys(includes or ()))
        self._includes = _compile(
            self._include_texts, backslash_is_separator=backslash_is_separator
        )
        self._excludes = _compile(
            excludes or (), backslash_is_separator=backslash_is_separator
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
        included = not self._includes
        for index, pattern in enumerate(self._includes):
            # Once the member is in, only the includes not yet seen to match are left
            # to check.
            if included and self._include_hit[index]:
                continue
            if pattern(member):
                included = True
                self._include_hit[index] = True
        if not included:
            return False
        self._included_any = True
        if any(pattern(member) for pattern in self._excludes):
            return False
        self._selected_any = True
        return True

    def unmatched_includes(self) -> list[str]:
        """The includes that matched no member offered so far, in command-line order."""
        return [
            text
            for text, hit in zip(self._include_texts, self._include_hit, strict=True)
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
        prints the listing error instead. An upfront index that ends in damage always
        makes that pass raise the damage at its end (``base_reader._listed_members``),
        so the deferred report only ever fires for an archive that had no index at
        all, such as a compressed TAR.
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
