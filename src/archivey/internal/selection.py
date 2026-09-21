"""Member selection normalization shared by streaming and extraction."""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable
from typing import cast

from archivey.exceptions import ArchiveyUsageError
from archivey.internal.arg_checks import describe_value
from archivey.types import ArchiveMember


def normalize_member_selector(
    members: Collection[str | ArchiveMember] | Callable[[ArchiveMember], bool] | None,
) -> Callable[[ArchiveMember], bool] | None:
    """Normalize a collection or predicate selector to a predicate."""
    if members is None:
        return None
    if callable(members):
        # ty cannot exclude a callable Collection from this union; pyrefly's
        # redundant-cast warning is not the gate.
        return cast("Callable[[ArchiveMember], bool]", members)
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
        raise ArchiveyUsageError(
            f"members= takes a collection of names or members, a predicate, or None, "
            f"but got {describe_value(members)}."
        )
    names: set[str] = set()
    identities: set[tuple[str, int]] = set()
    for entry in members:
        if isinstance(entry, ArchiveMember):
            # Match by (archive_id, member_id) identity. A member that carries no ids
            # (never registered by a reader — e.g. hand-built) is deliberately dropped:
            # it can't correspond to any real member, so it silently matches nothing.
            if entry._archive_id is not None and entry._member_id is not None:
                identities.add((entry._archive_id, entry._member_id))
        elif isinstance(entry, str):
            names.add(entry)
        else:
            # Anything else used to land in ``names``, where it could never equal a
            # member name, so the entry was dropped and the call still reported
            # success. A selector that silently selects nothing is the one outcome
            # a caller cannot distinguish from an archive that has nothing.
            raise ArchiveyUsageError(
                f"members= takes names (str) or ArchiveMembers, but one entry was "
                f"{describe_value(entry)}."
            )

    def predicate(member: ArchiveMember) -> bool:
        if member.name in names:
            return True
        if member._archive_id is not None and member._member_id is not None:
            return (member._archive_id, member._member_id) in identities
        return False

    return predicate
