"""Path helpers for a front end: destination naming rules, and member selection by glob.

A front end that moves or renames what extraction wrote should name things the way
extraction does, so the result is the one a direct extraction would have given.
archivey's own CLI moves a single top-level directory up out of its wrapper folder
with this rule.

The library's ``members=``, ``get()`` and ``open()`` match stored names exactly. A caller
who wants shell-style patterns passes :func:`match_glob` as ``members=``.

**Public, not re-exported.** Import from here (``from archivey.paths import
numbered_name``); these names are not in :mod:`archivey`. Everything in ``__all__``
carries the same compatibility promise as ``archivey.__all__``.

- :func:`numbered_name` — the ``name (N)`` spelling of an
  ``OverwritePolicy.RENAME`` rename.
- :func:`match_glob` — a ``members=`` predicate from glob patterns, where ``*`` stays
  inside one folder and ``**`` matches any depth.
"""

from __future__ import annotations

import re
from pathlib import PurePath
from typing import TYPE_CHECKING

from archivey.exceptions import _UsageValueError

if TYPE_CHECKING:
    from collections.abc import Callable

    from archivey.types import ArchiveMember

__all__ = ["match_glob", "numbered_name"]


def numbered_name(name: str, n: int, *, is_dir: bool) -> str:
    """Return ``name`` with the counter ``n`` added, as ``OverwritePolicy.RENAME`` spells it.

    When a member's destination is taken, ``OverwritePolicy.RENAME`` writes it under the
    first free name of this form, counting from ``n=1``. A file keeps its extension:
    the counter goes before the final suffix. A directory has no suffix, so the counter
    goes after the whole name.

    ``name`` is a single path component, not a path: only its final suffix moves.

    >>> numbered_name("photo.jpg", 1, is_dir=False)
    'photo (1).jpg'
    >>> numbered_name("notes.tar.gz", 2, is_dir=False)
    'notes.tar (2).gz'
    >>> numbered_name("photos.2024", 1, is_dir=True)
    'photos.2024 (1)'

    Args:
        name: The name that is taken.
        n: The counter, ``1`` or more.
        is_dir: Whether ``name`` is a directory.

    Returns:
        The candidate name. This function does not look at the filesystem: the caller
        checks whether the candidate is free and tries ``n + 1`` when it is not.
    """
    if is_dir:
        return f"{name} ({n})"
    path = PurePath(name)
    return f"{path.stem} ({n}){path.suffix}"


def match_glob(*patterns: str) -> Callable[[ArchiveMember | str], bool]:
    """Return a ``members=`` predicate that selects the members matching any of ``patterns``.

    >>> with archivey.open_archive("site.zip") as archive:  # doctest: +SKIP
    ...     archive.extract_all("out/", members=match_glob("docs/**", "*.md"))

    The patterns are matched against the whole member name, ``/``-separated, and
    case-sensitively:

    - ``*`` matches any run of characters inside one folder: ``*.md`` selects
      ``README.md`` but not ``docs/guide.md``.
    - ``?`` matches one character other than ``/``.
    - ``[abc]``, ``[a-z]`` and ``[!abc]`` match one character from, or not from, the set,
      never ``/``. ``[*]`` matches a literal ``*``.
    - ``**`` as a whole path component matches any number of folders, including none:
      ``**/*.md`` selects ``README.md`` and ``docs/api/guide.md``, and ``docs/**``
      selects the ``docs/`` folder and everything under it. Elsewhere, ``**`` is ``*``.

    ``*`` and ``?`` match a leading ``.`` too, so hidden files are not skipped.

    A directory member is matched by its name without the trailing ``/``, so ``docs/*``
    selects the folder ``docs/api/`` as well as the file ``docs/index.md``. A pattern that
    ends in ``/`` selects only directories: ``*/`` selects every top-level folder.

    A ``\\`` in a pattern is an ordinary character, on Windows too, because member names
    use ``/`` and a TAR member name can contain a ``\\``.

    A pattern that matches nothing is not reported: ``MEMBER_SELECTOR_UNMATCHED`` covers
    only the names in the collection form of ``members=``.

    archivey's command-line tool reads its patterns differently on purpose: there ``*``
    crosses ``/`` and ``docs`` selects the folder and everything in it, as ``tar`` does.
    The two are separate, so ``archivey extract a.zip '*.md'`` selects ``.md`` files at
    every depth while ``match_glob("*.md")`` selects top-level ones only.

    Args:
        *patterns: One or more glob patterns. A member is selected when any of them
            matches.

    Returns:
        A callable that takes an :class:`~archivey.ArchiveMember` and returns whether it
        is selected. It also takes a member name as a ``str``, where a trailing ``/``
        marks a directory.

    Raises:
        ArchiveyUsageError: No pattern was given, a pattern is empty or only ``/``, or a
            ``[...]`` set is malformed, such as the reversed range ``[z-a]``. It is also a
            ``ValueError``.
    """
    return _MemberGlob(patterns)


class _MemberGlob:
    """The predicate :func:`match_glob` returns; its ``repr`` names the patterns."""

    __slots__ = ("_any", "_dirs_only", "patterns")

    def __init__(self, patterns: tuple[str, ...]) -> None:
        if not patterns:
            raise _UsageValueError("match_glob() needs at least one pattern")
        if not all(p.strip("/") for p in patterns):
            raise _UsageValueError(
                "match_glob() got an empty pattern; a pattern must name something "
                "other than '/'"
            )
        self.patterns = patterns
        # Patterns ending in "/" select directories only, so they get their own regex.
        any_kind = [p for p in patterns if not p.endswith("/")]
        dirs_only = [p.rstrip("/") for p in patterns if p.endswith("/")]
        try:
            self._any = _compile_globs(any_kind)
            self._dirs_only = _compile_globs(dirs_only)
        except re.error as exc:
            raise _UsageValueError(
                f"match_glob() got a malformed [...] set: {exc}"
            ) from None

    def __call__(self, member: ArchiveMember | str) -> bool:
        if isinstance(member, str):
            name = member
            is_dir = name.endswith("/")
        else:
            name = member.name
            # Every directory name ends in "/" except the root entry ".", which a
            # "tar cf x.tar ." archive lists; is_dir is what marks that one.
            is_dir = member.is_dir or name.endswith("/")
        if name.endswith("/"):
            name = name[:-1]
        if self._any is not None and self._any.fullmatch(name):
            return True
        return bool(
            is_dir and self._dirs_only is not None and self._dirs_only.fullmatch(name)
        )

    def __repr__(self) -> str:
        return f"match_glob({', '.join(map(repr, self.patterns))})"


def _compile_globs(patterns: list[str]) -> re.Pattern[str] | None:
    if not patterns:
        return None
    # DOTALL: a member name can contain a newline, and "**" becomes ".*".
    return re.compile("|".join(f"(?:{_glob_to_regex(p)})" for p in patterns), re.DOTALL)


def _glob_to_regex(pattern: str) -> str:
    """Translate one glob pattern into a regex for ``fullmatch`` on a ``/``-joined name."""
    out: list[str] = []
    # Whether the regex so far ends in a component, so the next component needs a "/"
    # before it. A "**" component supplies its own separators.
    after_component = False
    # A run of "**" components selects what one does; collapsing it also keeps the
    # regex from holding several optional groups that backtrack against each other.
    components: list[str] = []
    for component in pattern.split("/"):
        if not (component == "**" and components and components[-1] == "**"):
            components.append(component)
    for index, component in enumerate(components):
        last = index == len(components) - 1
        if component == "**":
            if last:
                # "a/**" also matches "a" itself; a pattern of just "**" matches anything.
                out.append("(?:/.*)?" if after_component else ".*")
            else:
                out.append("(?:/.+)?/" if after_component else "(?:.+/)?")
            after_component = False
            continue
        if after_component:
            out.append("/")
        out.append(_component_to_regex(component))
        after_component = True
    return "".join(out)


def _component_to_regex(component: str) -> str:
    out: list[str] = []
    i, n = 0, len(component)
    while i < n:
        c = component[i]
        i += 1
        if c == "*":
            # "**" inside a component is one "*", as in pathlib and glob.
            while i < n and component[i] == "*":
                i += 1
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[":
            j = i
            if j < n and component[j] == "!":
                j += 1
            if j < n and component[j] == "]":
                j += 1
            while j < n and component[j] != "]":
                j += 1
            if j >= n:
                # No closing "]": the "[" is an ordinary character, as in fnmatch.
                out.append(re.escape(c))
                continue
            body = component[i:j]
            i = j + 1
            negate = body.startswith("!")
            if negate:
                body = body[1:]
            # Escape what is special inside a regex class; keep "-" for ranges.
            body = re.sub(r"([\\\[\]^&~|])", r"\\\1", body)
            # The lookahead refuses "/"; putting "/" inside the set would join it to the
            # caller's characters ("[!-a]" would become the range "/-a").
            out.append(f"(?!/)[{'^' if negate else ''}{body}]")
        else:
            out.append(re.escape(c))
    return "".join(out)
