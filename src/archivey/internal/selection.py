"""Member selection normalization shared by streaming and extraction."""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable
from typing import TYPE_CHECKING

from archivey.diagnostics import DiagnosticCode, SelectorUnmatchedContext
from archivey.escaping import quoted
from archivey.exceptions import ArchiveyUsageError
from archivey.internal.arg_checks import describe_value
from archivey.types import ArchiveMember

if TYPE_CHECKING:
    from archivey.internal.diagnostics_collector import DiagnosticCollector


def member_name_keys(name: str) -> tuple[str, ...]:
    """The member names that a name written by a caller or a link refers to.

    A directory member's name carries a trailing ``/`` (``normalize_member_name``), and
    neither a caller nor a stored link target is expected to write it. So a name without
    the ``/`` also refers to the directory spelling. A name that ends in ``/`` refers
    only to itself: the ``/`` says the writer meant a directory.

    Link-target lookup and ``members=`` name selection both use this rule.
    """
    if name.endswith("/"):
        return (name,)
    return (name, name + "/")


class CollectionSelector:
    """The predicate that the collection form of ``members=`` normalizes to.

    It also records which entries matched a member, so that the pass that ran it can
    report each entry that matched nothing (``MEMBER_SELECTOR_UNMATCHED``). It is not
    iterable, so ``normalize_member_selector`` passes it through as a predicate.
    """

    __slots__ = ("_entries", "_identities", "_matched", "_names", "_record")

    def __init__(
        self, entries: list[str | ArchiveMember], *, record: bool = True
    ) -> None:
        self._entries = entries
        # False for a selector nothing will report on (the extraction coordinator's
        # own identity selectors), so it skips the per-member bookkeeping.
        self._record = record
        # member name -> indexes of the str entries that select that name.
        self._names: dict[str, list[int]] = {}
        # (archive_id, member_id) -> indexes of the ArchiveMember entries with that id.
        self._identities: dict[tuple[str, int], list[int]] = {}
        self._matched: set[int] = set()
        for index, entry in enumerate(entries):
            if isinstance(entry, str):
                for key in member_name_keys(entry):
                    self._names.setdefault(key, []).append(index)
            # Match by (archive_id, member_id) identity. A member that carries no ids
            # (never registered by a reader, for example built by hand) cannot
            # correspond to any real member, so it matches nothing and is reported
            # as unmatched.
            elif entry._archive_id is not None and entry._member_id is not None:
                identity = (entry._archive_id, entry._member_id)
                self._identities.setdefault(identity, []).append(index)

    def __call__(self, member: ArchiveMember) -> bool:
        by_name = self._names.get(member.name)
        by_identity = None
        if member._archive_id is not None and member._member_id is not None:
            by_identity = self._identities.get((member._archive_id, member._member_id))
        if by_name is None and by_identity is None:
            return False
        if self._record:
            if by_name is not None:
                self._matched.update(by_name)
            if by_identity is not None:
                self._matched.update(by_identity)
        return True

    def unmatched(self) -> list[str | ArchiveMember]:
        """The entries that no member has matched so far, in the caller's order.

        An entry that the caller repeated is listed once: a name by its text, a member
        by its identity. Two hand-built members with no identity are two entries.
        """
        seen_names: set[str] = set()
        seen_identities: set[tuple[str, int]] = set()
        result: list[str | ArchiveMember] = []
        for index, entry in enumerate(self._entries):
            if index in self._matched:
                continue
            if isinstance(entry, str):
                if entry in seen_names:
                    continue
                seen_names.add(entry)
            elif entry._archive_id is not None and entry._member_id is not None:
                identity = (entry._archive_id, entry._member_id)
                if identity in seen_identities:
                    continue
                seen_identities.add(identity)
            result.append(entry)
        return result

    def report_unmatched(
        self, collector: DiagnosticCollector, archive_name: str | None
    ) -> None:
        """Emit one ``MEMBER_SELECTOR_UNMATCHED`` per entry that matched nothing.

        Call it only when every member has been offered to the selector. Before that,
        an entry that has not matched yet can still match a later member.
        """
        for entry in self.unmatched():
            if isinstance(entry, str):
                context = SelectorUnmatchedContext(
                    archive_name=archive_name, entry=entry, entry_kind="name"
                )
                message = f"members= entry {quoted(entry)} matched no member."
            else:
                context = SelectorUnmatchedContext(
                    archive_name=archive_name, entry=entry.name, entry_kind="member"
                )
                message = (
                    f"members= entry for member {quoted(entry.name)} matched no "
                    f"member: a member entry matches only the same member of this "
                    f"reader."
                )
            collector.emit(
                code=DiagnosticCode.MEMBER_SELECTOR_UNMATCHED,
                message=message,
                context=context,
            )


def normalize_member_selector(
    members: Collection[str | ArchiveMember] | Callable[[ArchiveMember], bool] | None,
) -> Callable[[ArchiveMember], bool] | None:
    """Normalize a collection or predicate selector to a predicate.

    A ``str`` entry matches every member with that name. An entry without a trailing
    ``/`` also matches the directory spelling, so ``"dir"`` selects the member
    ``"dir/"`` (:func:`member_name_keys`). An ``ArchiveMember`` entry matches by
    identity. The collection form returns a :class:`CollectionSelector`, which also
    records the entries that matched nothing.

    A selector that is both callable and a collection is read as a collection.
    ``Collection`` is not final, so the two arms of the parameter's type can
    overlap; the tie is decided here rather than falling out of the order of
    the checks.
    """
    if members is None:
        return None
    if isinstance(members, (str, bytes)):
        # A str is a Collection of one-character strings, so `members="notes.txt"`
        # selected the set {"n", "o", "t", "e", "s", ".", "x"} — no member matched,
        # and the call reported a clean extraction of nothing. Refused rather than
        # wrapped: guessing that a string meant [string] would make the plural
        # parameter accept a singular, and the caller is one bracket from correct.
        # bytes gets no list-wrapping advice: Pass [b'notes.txt'] is itself a
        # usage error (names are str). The str branch is the common slip.
        if isinstance(members, bytes):
            raise ArchiveyUsageError(
                f"members= takes names (str) or ArchiveMembers, but got "
                f"{describe_value(members)}."
            )
        raise ArchiveyUsageError(
            f"members= takes a collection of names or members, but got "
            f"{describe_value(members)}. Pass [{members!r}] to select one member."
        )
    if not isinstance(members, Iterable):
        # Everything below is the collection arm, so only the predicate is left here.
        # Testing the collection first is what decides the tie: an object that is both
        # callable and a collection is read as a collection, which is the more
        # defensible reading of a parameter named ``members``. The precedence was
        # never an explicit decision before; it is one now.
        #
        # It is also what lets both checkers narrow this arm with no ``cast``.
        # ``callable()`` cannot drop the collection arm, because ``Collection`` is not
        # final and a subclass may define ``__call__`` — ty intersects the two instead
        # and the intersection returns ``object``. A negative ``isinstance`` does drop
        # it, so the value that reaches the return below is already a predicate.
        if not callable(members):
            raise ArchiveyUsageError(
                f"members= takes a collection of names or members, a predicate, or "
                f"None, but got {describe_value(members)}."
            )
        return members
    entries: list[str | ArchiveMember] = []
    for entry in members:
        if isinstance(entry, (str, ArchiveMember)):
            entries.append(entry)
        else:
            # Anything else used to land in ``names``, where it could never equal a
            # member name, so the entry was dropped and the call still reported
            # success. A selector that silently selects nothing is the one outcome
            # a caller cannot distinguish from an archive that has nothing.
            raise ArchiveyUsageError(
                f"members= takes names (str) or ArchiveMembers, but one entry was "
                f"{describe_value(entry)}."
            )
    return CollectionSelector(entries)
