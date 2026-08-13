"""Public extraction value types: the ``ExtractionPolicy`` / ``OverwritePolicy`` /
``OnError`` / ``ExtractionStatus`` enums, the ``ExtractionProgress`` / ``ExtractionResult``
dataclasses, and the ``members`` / ``filter`` type aliases.

These types are part of the public API (re-exported from ``archivey``), but they live
under ``internal/`` so the public ``reader.py`` / ``core.py`` surface and the internal
``ExtractionCoordinator`` can share them without an import cycle. They carry no behavior
beyond being enums / dataclasses; the extraction logic lives in
``internal/extraction.py`` and ``internal/filters.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Collection

from archivey.exceptions import ArchiveyError
from archivey.types import ArchiveMember

# Shared selector / filter aliases, defined in this leaf module so both the public
# reader.py signature and the internal coordinator can import them without a cycle.
#
# ``MemberSelectorArg`` — which members to extract: a collection of names / ArchiveMembers,
# a predicate, or ``None`` (= all). The collection form is normalized to a predicate by the
# shared ``normalize_member_selector`` helper (also used by ``stream_members``).
MemberSelectorArg = (
    Collection["str | ArchiveMember"] | Callable[[ArchiveMember], bool] | None
)
# ``MemberFilter`` — a per-member sanitize/rename hook run after the safety checks and
# policy transform; returns a ``.replace()``d copy, or ``None`` to skip the member.
MemberFilter = Callable[[ArchiveMember], "ArchiveMember | None"]


class ExtractionPolicy(Enum):
    """How much of an archive member to trust when writing it to the destination.

    The universal path/symlink/special-file safety checks are enforced under **all**
    policies (see ``safe-extraction``). Beyond those, the policy governs two dimensions:
    the permission/ownership transform applied before a member is written, and the
    cross-platform name safety keyed off it — collision determinism (O2), reserved/mangled
    name rejection (O3/O4), portable-name normalization (O7), and rejection of
    **deceptive** names (bidi overrides). ``STRICT`` is portable-by-default; ``TRUSTED``
    defers to the local OS (faithful bytes, no name rejection or rewrite). See
    ``dev-docs/decisions/0013-cross-platform-name-safety-policies.md``.

    What ``TRUSTED`` does **not** relax: anything where the write itself is unsafe — a
    name that escapes the destination, carries a NUL, or names a device node. Those are
    universal. It *does* extract a name built to display as something else
    (``evil<U+202E>gnp.exe``), which ``STRICT``/``STANDARD`` refuse with
    ``DeceptiveNameError``: such a member lands inside the destination under exactly its
    stored bytes, so the risk is to a human reading the directory afterwards, not to the
    filesystem. Choosing ``TRUSTED`` accepts that, which is what makes faithful
    round-tripping possible. See
    ``dev-docs/decisions/0017-bidi-override-rejection-is-policy-keyed.md``.
    """

    STRICT = "strict"  # default; untrusted archives
    STANDARD = "standard"  # moderate trust; e.g. your own older archives
    TRUSTED = (
        "trusted"  # apply stored mode (and uid/gid as root); path safety still enforced
    )


class OverwritePolicy(Enum):
    """What to do when a destination entry already exists where a member would be written.

    ``ERROR`` raises an ``ExtractionError`` for the member, which is then a per-member
    failure governed by the ``OnError`` policy — ``OnError.STOP`` re-raises and halts,
    ``OnError.CONTINUE`` records a ``FAILED`` ``ExtractionResult`` and proceeds. ``SKIP``
    is not an error: it records a ``NOT_OVERWRITTEN`` result regardless of ``OnError``.
    """

    ERROR = "error"  # existing entry -> ExtractionError (then handled per OnError)
    SKIP = "skip"  # silently skip existing entries (records NOT_OVERWRITTEN)
    REPLACE = (
        "replace"  # unlink the existing entry, then create fresh (never write-through)
    )
    RENAME = "rename"  # write a colliding entry under a derived "name (N)" spelling


class OnError(Enum):
    """What to do when an individual member cannot be extracted.

    Governs per-member *failures* only (corrupt/truncated/undecodable data,
    write ``OSError``, overwrite ``ERROR``, etc.). A policy ``BLOCKED`` outcome
    (``FilterRejectionError`` from a universal path-safety check or a policy
    filter) is always recorded and continued, under either value. Aborting the
    whole extraction on the first unsafe member is ``AbortOn.BLOCKED_MEMBER``,
    an independent opt-in that applies under either ``OnError`` value.
    """

    STOP = "stop"  # default: raise the first member failure and halt
    CONTINUE = "continue"  # record the failure, clean up, proceed to the next member


class AbortOn(str, Enum):
    """Events that abort the whole extraction the first time they occur.

    Passed as ``abort_on=`` to ``extract()`` / ``extract_all()`` (a collection; empty by
    default). Independent of :class:`OnError` and of ``DiagnosticPolicy``: an event named
    here aborts whatever those are set to, and one not named here never aborts.

    Abort is immediate — the triggering member's partial output is removed, no later
    member is processed, and **no** ``ExtractionReport`` is returned. Output already
    written for earlier members stays on disk, matching ``OnError.STOP``: an abort stops
    the run, it does not roll it back.

    There is deliberately no member for extraction *failures*: ``OnError.STOP`` already
    means "raise on the first failure".
    """

    # A member blocked by a universal path-safety check or a policy filter. Re-raises the
    # underlying FilterRejectionError, matching OnError.STOP's propagate-the-original.
    BLOCKED_MEMBER = "blocked_member"
    # A second member resolving to an already-written collision key (non-TRUSTED only).
    # Fires on EVERY such collision, whatever OverwritePolicy resolution follows —
    # replaced, skipped, errored or renamed. The trigger is the collision, not its
    # outcome. Raises NameCollisionError.
    NAME_COLLISION = "name_collision"
    # A name rewritten to its portable spelling. A narrow escape hatch for callers who
    # refuse any on-disk name differing from the archive's — mirroring tools, forensic
    # extracts, byte-fidelity checks — and NOT part of ordinary strict extraction: no
    # preset or policy level implies it. To *audit* rewrites read
    # ``ExtractionResult.presented_name``; set this only to make them fatal. Raises
    # NameRewrittenError.
    NAME_SANITIZED = "name_sanitized"


class ExtractionStatus(str, Enum):
    """The outcome recorded for a single member in its :class:`ExtractionResult`."""

    EXTRACTED = "extracted"
    NOT_OVERWRITTEN = "not_overwritten"  # existing dest left under OverwritePolicy.SKIP
    SUPERSEDED = "superseded"  # non-current entry (a later same-name entry overwrites)
    # Written, then its destination was replaced by a LATER member under
    # OverwritePolicy.REPLACE. Revised retroactively when the collision is detected, so
    # a case-insensitive merge is visible in ``results`` instead of two members both
    # reporting EXTRACTED at one path. Completes the family: NOT_OVERWRITTEN kept an
    # existing destination and never wrote; SUPERSEDED never wrote; OVERWRITTEN wrote
    # and lost the destination afterwards.
    OVERWRITTEN = "overwritten"
    BLOCKED = "blocked"  # blocked by a safety filter (universal or policy check)
    FAILED = "failed"  # error while extracting (corrupt data, ratio bomb, write error)


@dataclass
class ExtractionProgress:
    """Progress snapshot for the ``on_progress`` callback.

    For FILE members the callback MAY fire multiple times as bytes are written
    (about once per copy chunk); each member still gets a terminal report with
    ``member_bytes_written`` equal to the member's size (or the final observed
    byte count when size is unknown). Directories, symlinks, and hardlinks
    produce a single report with ``member_bytes_written == 0``.
    """

    member: ArchiveMember
    bytes_written: int  # cumulative bytes written across the whole call so far
    total_bytes_estimated: int | None  # None if the archive carries no size info
    members_done: int
    members_total: int | None  # None when the count would require a full scan
    member_bytes_written: int  # output bytes written for the current member so far
    # Completed-outcome tallies so far (exclude the in-flight member on intra-member
    # reports). ``members_done`` still counts every processed member; these split
    # successful writes from policy blocks for stop-path reporting.
    members_extracted: int
    members_blocked: int


@dataclass(frozen=True)
class ExtractionResult:
    """One entry per member processed, returned from ``extract()`` / ``extract_all()``.

    Frozen outcome structure (``path`` / ``status`` / ``error`` cannot be replaced after
    construction). ``member`` still refers to the live mutable :class:`ArchiveMember`
    whose late-bound metadata may be filled in place.
    """

    member: ArchiveMember
    path: Path | None  # the written path, or None if the member was not written
    status: ExtractionStatus
    # Populated for FAILED/BLOCKED when the run continues past that member (including
    # STOP + policy block). An OSError when the failure is a filesystem read/write
    # error on this member (not translated to an ArchiveyError).
    error: ArchiveyError | OSError | None = None
    # The destination the coordinator intended before overwrite/rename resolution. For an
    # ordinary write it equals ``path``; under ``OverwritePolicy.RENAME`` a collided member
    # is written to a derived name, so ``requested_path != path and status == EXTRACTED``
    # marks the rename; a collision resolved by SKIP/ERROR sets ``requested_path`` with
    # ``path=None``. ``None`` for members that never reached destination resolution.
    # On an OVERWRITTEN result it retains the destination the member did write to, so a
    # caller can join the pair to the replacing member's ``path``.
    requested_path: Path | None = None
    # The member's full relative name BEFORE the portable-name rewrite (O3 trailing
    # dot/space strip, O7 percent-escape), or ``None`` when no rewrite occurred. Distinct
    # from ``member.name`` (the archive's spelling) and from ``path`` (the final on-disk
    # spelling): a caller ``filter`` rename followed by a portable rewrite produces three
    # spellings, and only this field records the middle one.
    presented_name: str | None = None
    # Set together, and only when one failed hardlink source causes N FAILED link results:
    # those N results share one group id and carry ``failure_group_size=N``. The id is
    # opaque — compare for equality to join a group; do not rely on ordering, format, or
    # cross-run stability.
    failure_group_id: str | None = None
    failure_group_size: int | None = None
    # The already-written destination this member collided with, or ``None`` when nothing
    # this run held the name. Set for every resolution — SKIP, ERROR, RENAME and a
    # REPLACE merge alike — so "was this a collision, and with what?" is answered by the
    # result itself rather than reconstructed by re-deriving collision keys across the
    # report. Only a collision with a member of THIS run is recorded: an obstacle that was
    # already on disk before extraction started leaves this ``None`` (the destination is
    # still in ``requested_path``). Join to the blocking member by plain path equality
    # against another result's ``path`` — or its ``requested_path`` when that member was
    # itself later revised to ``OVERWRITTEN`` and no longer holds a live path.
    collided_with: Path | None = None
