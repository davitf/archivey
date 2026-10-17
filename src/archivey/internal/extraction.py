"""The extraction coordinator and decompression-bomb tracker.

``ExtractionCoordinator`` is a **pull-based sink**: it drives the reader
(``members_report_if_available()``, ``_iter_with_data()``, ``compressed_source_size``) and
selects an algorithm, rather than a push-model helper that buffers deferred link state.
Per member it runs the universal safety check on the original, applies the policy
transform (and optional user filter) to a transient copy, and writes FILE / DIR / SYMLINK
/ HARDLINK, tracking bomb limits and per-member results.

Hardlinks (TAR ordering guarantees the source precedes its links) are resolved by one
**core** algorithm: a sequential forward pass with a conditional second pass. When a
filter/selector orphans a selected link (its source excluded), a re-readable (seekable)
source is recovered in a single second pass; a forward-only source is unrecoverable and
fails per ``OnError``. See ``safe-extraction`` / ``format-tar`` for the normative spec.

The optional *planned single pass* optimization (staging an excluded source during the
first pass when a free member list exists) is deliberately not implemented here — it is an
optimization over this core, not a correctness requirement.
"""

from __future__ import annotations

import bisect
import contextlib
import errno
import io
import os
import posixpath
import shutil
import stat
import tempfile
import uuid
from collections.abc import Callable, Collection, Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePath
from typing import TYPE_CHECKING, BinaryIO, assert_never, cast

from archivey.config import ExtractionLimits
from archivey.exceptions import (
    ArchiveyError,
    CorruptionError,
    DiagnosticRaisedError,
    ExtractionError,
    FilterRejectionError,
    LinkTargetNotFoundError,
    LinkTargetNotFoundReason,
    NameCollisionError,
    NameRewrittenError,
    ResourceLimitError,
    _UsageTypeError,
)
from archivey.internal.base_reader import MAX_LINK_TARGET_BYTES
from archivey.internal.file_copy_pass import FileCopyPass
from archivey.internal.filters import (
    POLICY_TRANSFORMS,
    SYMLINK_ESCAPES,
    SYMLINK_LOOPS,
    apply_name_policy,
    check_universal,
    collision_key,
    disk_spelled,
    reroot_absolute,
    resolve_or_raise_on_loop,
)
from archivey.internal.link_watch import LinkWatch
from archivey.internal.logs import extraction as logger
from archivey.internal.long_paths import long_path, plain
from archivey.internal.selection import CollectionSelector
from archivey.internal.windows_reparse import (
    IO_REPARSE_TAG_MOUNT_POINT,
    IO_REPARSE_TAG_SYMLINK,
)
from archivey.paths import numbered_name
from archivey.terminal import display_path, quoted
from archivey.types import (
    EXTRA_IS_FILE_COPY,
    AbortOn,
    ArchiveMember,
    ExtractionPolicy,
    ExtractionProgress,
    ExtractionResult,
    ExtractionStatus,
    MemberFilter,
    MemberType,
    NameRewrite,
    OnError,
    OverwritePolicy,
)

if TYPE_CHECKING:
    from _typeshed import WriteableBuffer

    from archivey.internal.base_reader import BaseArchiveReader
    from archivey.internal.streams.archive_stream import ArchiveStream


_CHUNK = 1024 * 1024  # 1 MiB copy chunk

# Prefix of the temp files atomic FILE writes stage in the destination directory
# (mkstemp appends a random suffix). Python-level failures unlink them; a removal the
# filesystem refuses (logged) or a hard kill (SIGKILL, power loss) can leave one:
# leftover ``.archivey-tmp-*`` files in an extraction destination are archivey's and
# are safe to delete. Documented in docs/extracting.md; keep the value and that doc in
# sync.
_TMP_PREFIX = ".archivey-tmp-"

# The fields ``_apply_metadata`` puts on disk.
_APPLIED_METADATA = ("uid", "gid", "mode", "modified")

# Prefix of the private scratch directory a dry run extracts into (see
# ``ExtractionCoordinator.run``). Removed when the run ends, like the temp files above.
_DRY_RUN_PREFIX = "archivey-dry-run-"


# A module attribute, not ``os.name`` at each use, so a test can take the Windows path
# on POSIX.
_WINDOWS = os.name == "nt"

# Whether ``os.symlink`` makes two kinds of link, one for a file target and one for a
# directory target (``target_is_directory``). Windows does, and a link of the wrong kind
# does not work: a file link to a directory cannot be entered. POSIX has one kind.
# Separate from ``_WINDOWS`` so a test can take this route alone on POSIX.
_DIRECTORY_LINKS = _WINDOWS


def _dir_mode_as_stored(mode: int) -> int:
    """The mode ``os.stat`` reports for a directory after ``os.chmod(path, mode)``.

    On Windows a mode carries only the read-only attribute, taken from the owner-write
    bit, and ``os.stat`` reports a directory as ``0o777`` or, read-only, ``0o555``.
    Comparing the member's mode as given would report every existing directory as
    having kept its mode there (``0o755`` against ``0o777``) when applying the
    member's would have changed nothing."""
    if not _WINDOWS:
        return mode
    return 0o777 if mode & stat.S_IWUSR else 0o555


# Win32 error codes matched on ``OSError.winerror``, which exists only on Windows.
_ERROR_PATH_NOT_FOUND = 3
_ERROR_INVALID_NAME = 123
_ERROR_FILENAME_EXCED_RANGE = 206
_ERROR_TOO_MANY_LINKS = 1142
_ERROR_PRIVILEGE_NOT_HELD = 1314


def _link_refused_here(exc: OSError) -> bool:
    """Whether ``os.link`` failed for a reason another path, or a copy, avoids.

    ``EXDEV``: the path is on another device. Or the file is at its link-count limit
    (``_at_link_limit``).
    """
    return exc.errno == errno.EXDEV or _at_link_limit(exc)


def _at_link_limit(exc: OSError) -> bool:
    """Whether ``os.link`` failed because the file already has as many names as the
    filesystem allows.

    ``EMLINK`` (Windows: ``winerror`` 1142, ``ERROR_TOO_MANY_LINKS``). NTFS allows 1024
    names for one file, the first name included, against 65000 on ext4. Without a copy,
    an archive POSIX extracts in full would fail on Windows at the 1025th name. A copy
    holds the same bytes.
    """
    return exc.errno == errno.EMLINK or (
        getattr(exc, "winerror", None) == _ERROR_TOO_MANY_LINKS
    )


def _symlink_problem(link_path: Path, target: str, dest_root: Path) -> str | None:
    """Why the symlink at ``link_path`` does not resolve inside ``dest_root`` now, or
    ``None`` when it does.

    Resolved through the real filesystem, so links on the way are followed. A link
    that cannot be resolved is refused: fail safe rather than crash. A symlink loop
    (found on every Python version by ``resolve_or_raise_on_loop``) is refused with
    its own message; anything else with the escape message. A loop cannot be
    resolved at all, so the loop message wins even when the target might have led
    outside ``dest_root``.
    """
    try:
        resolved = resolve_or_raise_on_loop(link_path.parent / target)
    except OSError as exc:
        return SYMLINK_LOOPS if exc.errno == errno.ELOOP else SYMLINK_ESCAPES
    if resolved == dest_root or resolved.is_relative_to(dest_root):
        return None
    return SYMLINK_ESCAPES


def _typed_os_error(
    exc: ArchiveyError | OSError, member_name: str
) -> ArchiveyError | OSError:
    """``exc``, or an ``ExtractionError`` when the archive's own name caused it.

    Two errors are properties of the name the archive chose, not of the filesystem's
    state: ``EILSEQ`` (bytes a UTF-8-only filesystem such as APFS refuses) and
    ``ENAMETOOLONG`` (a component, whole path or symlink target longer than the
    filesystem allows; the portable rewrite's ``%XX`` escapes can push a name over).
    Windows reports the same two as ``winerror`` 123 (``ERROR_INVALID_NAME``) and 206
    (``ERROR_FILENAME_EXCED_RANGE``), with errnos (``EINVAL``, ``ENOENT``) too broad to
    match on, so the Windows code is checked instead. Either can come from a link
    target as well as the name, so the messages name both. Windows also raises 1314
    (``ERROR_PRIVILEGE_NOT_HELD``) for a symlink when the process may not create one,
    which every symlink member hits the same way. The code is matched for any member,
    and today a symlink is the only write archivey makes on Windows that needs a
    privilege, so the message names symlinks as the likely cause rather than as the
    call that failed. They become a typed per-member failure. Every other ``OSError``
    stays as it is.

    A path of ``MAX_PATH`` (260) characters or more that a call took without the
    extended prefix fails with ``winerror`` 3 (``ERROR_PATH_NOT_FOUND``) unless the
    machine enables long paths. The coordinator gives such a path the prefix
    (``long_paths``), so this is the rest: a call it does not route, or a component
    longer than the filesystem allows. It is typed as too long only when the path in
    the error is that long and not in the extended form; a short missing path stays
    the ``FileNotFoundError`` it is.
    """
    if not isinstance(exc, OSError):
        return exc
    # ``winerror`` exists only on Windows; elsewhere it is None.
    winerror = getattr(exc, "winerror", None)
    if winerror == _ERROR_PATH_NOT_FOUND and _past_max_path(exc):
        winerror = _ERROR_FILENAME_EXCED_RANGE
    if exc.errno == errno.EILSEQ or winerror == _ERROR_INVALID_NAME:
        message = (
            "Member name or link target cannot be represented on the destination "
            "filesystem"
        )
    elif exc.errno == errno.ENAMETOOLONG or winerror == _ERROR_FILENAME_EXCED_RANGE:
        message = (
            "Member name or link target is too long for the destination filesystem"
        )
    elif winerror == _ERROR_PRIVILEGE_NOT_HELD:
        message = (
            "This process does not hold a privilege the write needs; on Windows, "
            "creating a symlink needs Developer Mode or an elevated process"
        )
    else:
        return exc
    error = ExtractionError(message, member_name=member_name)
    error.__cause__ = exc
    return error


# Win32's limit on a path without the extended prefix, the terminating NUL included.
_MAX_PATH = 260


def _past_max_path(exc: OSError) -> bool:
    """Whether a file name in ``exc`` is too long for Win32 without the extended
    prefix."""
    for name in (exc.filename, exc.filename2):
        if isinstance(name, str) and len(name) >= _MAX_PATH - 1 and plain(name) == name:
            return True
    return False


def _plain_os_error(exc: OSError) -> None:
    """Name the paths in ``exc`` without the extended prefix, in place."""
    for attr in ("filename", "filename2"):
        value = getattr(exc, attr, None)
        if isinstance(value, str):
            setattr(exc, attr, plain(value))


def _is_regular_file(path: Path) -> bool:
    """Whether ``path`` is a regular file itself, not a symlink to one."""
    try:
        return stat.S_ISREG(os.lstat(long_path(path)).st_mode)
    except OSError:
        return False


def _scratch_tree(
    root: Path,
) -> tuple[tuple[tuple[str, bool], ...], tuple[tuple[str, str], ...]] | None:
    """Every entry under ``root``, as (its path relative to ``root`` with ``/``
    separators, whether it is a directory and not a symlink), and every symlink, as
    (that path, its target), each sorted; ``None`` when part of the tree could not be
    read.

    Directories are listed without following links. A directory that cannot be listed,
    or a link that cannot be read, makes the whole record ``None`` rather than leaving
    it out: the CLI's hoist keeps a tree it could not fully walk, and a partial record
    would let the prediction move it.
    """
    found = []
    links = []
    pending = [root]
    try:
        while pending:
            directory = pending.pop()
            # Listed in the long-path form, so a deep directory can be read; each
            # entry's path is named plain again to be placed under ``root``.
            with os.scandir(long_path(directory)) as entries:
                for entry in entries:
                    path = Path(plain(entry.path))
                    rel = path.relative_to(root).as_posix()
                    is_dir = entry.is_dir(follow_symlinks=False)
                    found.append((rel, is_dir))
                    if entry.is_symlink():
                        links.append((rel, plain(os.readlink(entry.path))))
                    elif is_dir:
                        pending.append(path)
    except OSError:
        return None
    return tuple(sorted(found)), tuple(sorted(links))


def _file_identity(st: os.stat_result) -> tuple[int, int, int, int]:
    """What says a file is still the one this run wrote: its inode, size and mtime."""
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)


def _remove_scratch(root: Path) -> None:
    """Remove a dry run's scratch directory, whatever modes the archive gave its entries.

    A stored mode can leave a directory unwritable (or unreadable), and a file read-only,
    which Windows refuses to unlink. Each real entry is opened up first; symlinks are
    skipped, never followed. A failure is logged rather than raised: the run's own
    outcome is what the caller needs to see.
    """
    try:
        # In the long-path form throughout, so the walk can reach a deep entry.
        top = long_path(root)
        os.chmod(top, 0o700)
        for parent, dirnames, filenames in os.walk(top):
            for name in (*dirnames, *filenames):
                path = os.path.join(parent, name)
                if not os.path.islink(path):
                    os.chmod(path, 0o700 if name in dirnames else 0o600)
        shutil.rmtree(top)
    except OSError as exc:
        logger.warning(
            "Could not remove dry-run scratch directory %r: %s", str(root), exc
        )


def _open_new_file(path: Path, mode: int) -> int:
    """Create ``path``, which must not exist, for writing; return the open fd.

    ``mode`` is the creation mode, so the umask applies to it as it does to any new
    file. The flags are ``mkstemp``'s: ``O_EXCL`` fails loudly on a name that appeared
    underneath us, ``O_NOFOLLOW`` refuses a symlink planted there, and ``O_BINARY`` /
    ``O_NOINHERIT`` matter on Windows only.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    for extra in ("O_NOFOLLOW", "O_BINARY", "O_NOINHERIT"):
        flags |= getattr(os, extra, 0)
    return os.open(long_path(path), flags, mode)


class _AbortExtraction(Exception):
    """Internal carrier for an ``abort_on`` trigger fired deep in the write path.

    The public error it wraps (``NameCollisionError`` / ``NameRewrittenError``) is an
    ``ExtractionError``, so raising it directly would be caught by the per-member
    handlers and turned into a ``FAILED`` result — the opposite of an abort. This
    carrier is a plain ``Exception``, so it passes those handlers untouched (their
    ``finally`` clauses still close streams) and ``run()`` unwraps it at the top.
    """

    def __init__(self, error: ArchiveyError) -> None:
        super().__init__(str(error))
        self.error = error


class _PassedData(io.RawIOBase):
    """Stands in for a member's data that a forward pass has already passed: reading
    it raises ``error``. The write runs up to its first read, so the overwrite policy
    decides as it would with the data there (a member it would not write does not
    fail)."""

    def __init__(self, error: ArchiveyError) -> None:
        super().__init__()
        self._error = error

    def readable(self) -> bool:
        return True

    def readinto(self, b: WriteableBuffer, /) -> int:
        raise self._error


def _passed_data(error: ArchiveyError) -> BinaryIO:
    """A ``_PassedData`` that raises ``error``, as the member stream it stands in for."""
    return cast("BinaryIO", _PassedData(error))


class _AlwaysStopResourceLimitError(ResourceLimitError):
    """A cumulative/global bomb guard: halts extraction even under ``OnError.CONTINUE``.

    A subclass of ``ResourceLimitError`` so callers catching that type (or
    ``ArchiveyError``) still catch it; the coordinator uses the type to distinguish it
    from a *skippable* per-member ratio failure.
    """


# Backward-compatible alias used by older tests/imports.
_AlwaysStopExtractionError = _AlwaysStopResourceLimitError


class _CopyCounter:
    """Counts the chunks ``_copy_to_fileobj`` writes as a copy of bytes this run
    already wrote (``BombTracker.count_copy``), not as decoded output."""

    __slots__ = ("_tracker",)

    def __init__(self, tracker: BombTracker) -> None:
        self._tracker = tracker

    def count(self, chunk_bytes: int) -> None:
        self._tracker.count_copy(chunk_bytes, at_link_limit=False)


class BombTracker:
    """Cumulative byte / per-member ratio / archive-wide ratio / entry-count guards.

    Constructed once per extraction call. ``start_member()`` is called (with the
    **original** member) before each member; ``count()`` is called with each chunk
    written.
    """

    def __init__(
        self,
        max_bytes: int | None,
        max_ratio: float | None,
        # The defaults come from ExtractionLimits, the one place the limits are set.
        ratio_activation_threshold: int = ExtractionLimits.ratio_activation_threshold,
        max_entries: int | None = ExtractionLimits.max_entries,
        *,
        source: BaseArchiveReader | None = None,
        live_ratio_activation_threshold: int = (
            ExtractionLimits.live_ratio_activation_threshold
        ),
    ) -> None:
        self._max_bytes = max_bytes
        self._max_ratio = max_ratio
        self._ratio_floor = ratio_activation_threshold
        self._live_ratio_floor = live_ratio_activation_threshold
        # Archive-wide ratio denominators, taken from the reader so the coordinator just
        # hands over the source: the static outer compressed size when it is cheaply known
        # (captured once), otherwise a LIVE sample of the compressed bytes consumed from the
        # source (read fresh on each count(), since it grows as extraction proceeds).
        self._source = source
        self._compressed_source_size = (
            source.compressed_source_size if source is not None else None
        )
        self._max_entries = max_entries
        self._entry_count = 0
        self._total_bytes = 0  # decoded output, cumulative across all members
        # Bytes copied from a file this run already wrote (the hardlink copy fallback).
        # They count toward the byte cap. The part copied at the filesystem's link-count
        # limit also counts toward the archive-wide ratio (``count_copy``).
        self._copied_bytes = 0
        self._link_limit_copied_bytes = 0
        self._member_bytes = 0  # output bytes for the current member
        self._member: ArchiveMember | None = None
        # Output of members a streaming pass wrote and then took back as superseded (see
        # ``refund``). Subtracted for the byte cap only.
        self._refunded_bytes = 0

    @property
    def total_bytes(self) -> int:
        """Every byte written to the destination, decoded or copied."""
        return self._total_bytes + self._copied_bytes

    @property
    def member_bytes(self) -> int:
        return self._member_bytes

    def start_member(self, member: ArchiveMember) -> None:
        # Called with the ORIGINAL member (not a filter copy), so compressed_size and any
        # late-bound fields are accurate. Increments the entry-count guard, which — like
        # the cumulative byte guard — halts even under OnError.CONTINUE.
        self._member = member
        self._member_bytes = 0
        self._entry_count += 1
        if self._max_entries is not None and self._entry_count > self._max_entries:
            raise _AlwaysStopResourceLimitError(
                f"Entry-count limit reached: max_entries={self._max_entries} "
                f"(would write entry {self._entry_count})"
            )

    def refund(self, member_bytes: int) -> None:
        """Stop counting one member that a streaming pass took back as superseded.

        Random access never writes a shadowed duplicate, so it never counts one
        (``safe-extraction``: no bomb-limit counting for the skip). A streaming pass
        wrote it before it could know, and its entry is gone from the destination once
        the later copy replaces it, so the entry count stops counting it. Its bytes are
        gone too unless a hardlink made to it still holds them, so the caller passes
        ``member_bytes`` as 0 in that case and the byte cap keeps them. The archive-wide
        ratio counts them either way: those bytes were decoded, and a name repeated many
        times must not decode for free. ``total_bytes``, which progress reports, is not
        reduced: it counts what was written.
        """
        self._entry_count -= 1
        self._refunded_bytes += member_bytes

    def check_recorded_size(self) -> None:
        """Refuse the current member before its data is read, when its recorded size
        is over the per-member ratio.

        Called once the member is about to be decoded, after every check that could
        still skip it, so a member that would not be decoded is never refused here.
        A member's output never passes its recorded size: the member stream refuses
        a byte past it (``CorruptionError``). So a member whose recorded size is over
        the per-member ratio is one ``count()`` would refuse after decoding past the
        activation threshold, and it is refused now instead, with the same skippable
        error. The one difference is a member whose data stops short of its recorded
        size: ``count()`` might never refuse it, and the read would fail as damaged
        instead. Both fail that member alone.

        The archive-wide ratios are left to ``count()``. Refusing early on the archive's
        recorded sizes would stop the whole run on a size nothing has verified, losing
        the intact members after a damaged one; and ``count()`` already stops at the
        chunk that crosses the archive-wide limit, so an early check would save little.
        ``count()`` still runs on every chunk.
        """
        member = self._member
        if self._max_ratio is None or member is None:
            return
        size = member.size
        cs = member.compressed_size
        if size is None or not cs or cs <= 0:
            return
        if size > max(self._ratio_floor, self._max_ratio * cs):
            raise ResourceLimitError(
                f"Decompression ratio {size / cs:.0f}:1 exceeds limit "
                f"max_ratio={self._max_ratio:.0f}:1 (from recorded sizes: "
                f"{size} bytes from {cs} compressed)",
                member_name=member.name,
            )

    def count(self, chunk_bytes: int) -> None:
        """Count a chunk that is about to be written; raise instead if it may not be."""
        self._check_cumulative_bytes(chunk_bytes)
        self._total_bytes += chunk_bytes
        self._member_bytes += chunk_bytes

        # Per-member ratio: activates on THIS member's output; a per-member failure
        # (skippable under OnError.CONTINUE).
        member = self._member
        cs = member.compressed_size if member is not None else None
        if (
            self._max_ratio is not None
            and member is not None
            and self._member_bytes > self._ratio_floor
            and cs
            and cs > 0
        ):
            ratio = self._member_bytes / cs
            if ratio > self._max_ratio:
                raise ResourceLimitError(
                    f"Decompression ratio {ratio:.0f}:1 exceeds limit "
                    f"max_ratio={self._max_ratio:.0f}:1",
                    member_name=member.name,
                )
        self._check_archive_ratio()

    def count_copy(self, chunk_bytes: int, *, at_link_limit: bool) -> None:
        """Count bytes copied from a file this run already wrote, not decoded.

        A hard link that cannot be made writes a second full copy of content already
        on disk: across a device boundary, or past the filesystem's link-count limit.
        Those bytes land on the filesystem, so they count toward ``max_extracted_bytes``
        like any other write.

        A copy made at the link-count limit (``at_link_limit``) also counts toward the
        archive-wide ratio. The archive drives it: one that declares enough links to
        one member makes a copy per limit's worth of links, so a small archive could
        otherwise write far more than its size while staying under the byte cap, which
        is the case the ratio exists for (maintainer ruling, 2026-10-07). A
        cross-device copy does not: whether it happens depends on where the caller
        extracts to, not on the archive. Neither counts toward the per-member ratio,
        since a link member's compressed size says nothing about the copy's size.

        A streaming pass that writes a member again from its own earlier copy, once
        the archive's directory changed the member (``_redo_revised_member``), counts
        it here too: nothing is decoded a second time.
        """
        self._check_cumulative_bytes(chunk_bytes)
        self._copied_bytes += chunk_bytes
        if at_link_limit:
            self._link_limit_copied_bytes += chunk_bytes
            self._check_archive_ratio()

    def _check_cumulative_bytes(self, chunk_bytes: int) -> None:
        """Cumulative byte guard (always-stop), checked before ``chunk_bytes`` is written.

        Both callers count a chunk before they write it, so a refused chunk never
        reaches the destination and is not counted. The message says what is on disk
        and what the refused write would have made it.
        """
        written = self.total_bytes - self._refunded_bytes
        would_be = written + chunk_bytes
        if self._max_bytes is not None and would_be > self._max_bytes:
            raise _AlwaysStopResourceLimitError(
                f"Extraction limit reached: max_extracted_bytes={self._max_bytes} "
                f"(written {written} bytes; the next {chunk_bytes}-byte chunk would "
                f"make {would_be})"
            )

    def _check_archive_ratio(self) -> None:
        # Archive-wide ratio: activates on CUMULATIVE output; a whole-archive bomb signal
        # (always-stop). Uses the static outer compressed size when it is cheaply known,
        # otherwise a LIVE denominator — the compressed bytes consumed from the source so
        # far — which works for a streaming/pipe source whose total size is unknown. The two
        # are mutually exclusive (static when known, live otherwise), so the ratio is never
        # counted twice.
        css = self._compressed_source_size
        # Decoded output plus the copies made at the link-count limit (``count_copy``).
        output = self._total_bytes + self._link_limit_copied_bytes
        # Says which part was not decoded, so a trip on a store-only archive with many
        # hard links is not read as a decompression bomb.
        copies = self._link_limit_copied_bytes
        detail = (
            f" ({self._total_bytes} bytes decoded, plus {copies} bytes copied for hard "
            f"links past the filesystem's link-count limit)"
            if copies
            else ""
        )
        if self._max_ratio is not None and output > self._ratio_floor:
            if css and css > 0:
                if output / css > self._max_ratio:
                    raise _AlwaysStopResourceLimitError(
                        f"Archive-wide decompression ratio "
                        f"{output / css:.0f}:1 exceeds limit "
                        f"max_ratio={self._max_ratio:.0f}:1{detail}"
                    )
            elif self._source is not None and output > self._live_ratio_floor:
                # The live denominator is what has been read so far, so a blank run at
                # the start of the source reads far above the ratio it will end at once
                # the data after it arrives. Waiting for the live floor lets such a start
                # pass as it does when the whole size is known; once a pipe is read to
                # its end, ``consumed`` is that whole size and this check matches the
                # static one (codec-layer re-reads elsewhere only make it larger).
                consumed = self._source.compressed_bytes_consumed
                if consumed and consumed > 0 and output / consumed > self._max_ratio:
                    raise _AlwaysStopResourceLimitError(
                        f"Live decompression ratio "
                        f"{output / consumed:.0f}:1 exceeds limit "
                        f"max_ratio={self._max_ratio:.0f}:1{detail}"
                    )


def _report_stored_spelling(
    exc: ArchiveyError, on_disk: ArchiveMember, member: ArchiveMember
) -> None:
    """Rename ``exc`` from the spelling ``on_disk`` back to ``member``'s names.

    A check runs on the spelling that reaches disk, but an error names the member as
    it was before that spelling, so one skip does not print two names for one member.
    Each of the name and the link target moves back when the two members spell it
    differently. Two callers: after ``disk_spelled``, which writes a lone surrogate as
    its UTF-8 bytes (under ``STRICT`` and ``STANDARD`` the name policy has already
    escaped the name, so there only the link target changes back); and after the name
    policy's rewrite, where the name, the target or both can change back.
    """
    if exc.member_name == on_disk.name:
        exc.member_name = member.name
    if exc.link_target is not None and exc.link_target == on_disk.link_target:
        exc.link_target = member.link_target


def _final_members_only(
    selector: Callable[[ArchiveMember], bool],
) -> Callable[[ArchiveMember], bool]:
    """``selector`` for the members the walk will not change, and yes for the rest,
    which the pass selects provisionally (``_provisionally_selected``) and the end
    of the pass for good."""

    def select(member: ArchiveMember) -> bool:
        return not member.member_state_final or selector(member)

    return select


def _provisionally_selected(
    selector: Callable[[ArchiveMember], bool], member: ArchiveMember
) -> bool:
    """Whether the pass reads and writes a member the walk can still change, before
    the end of the pass selects it for good (``_settle_revised_members``).

    A ``members=`` collection matches any name the member can end up with, so the
    data of every member it may select is written while the stream holds it, and
    nothing is recorded as matched yet. A predicate is asked about the member as it
    is now; a member it leaves out holds a place, and its bytes, if it has any,
    cannot be written later.
    """
    if isinstance(selector, CollectionSelector):
        return selector.would_select(member, _possible_names(member.name))
    return selector(member)


def _possible_names(name: str) -> set[str]:
    """The names a member the walk has yet to settle can end with: a ZIP's
    directory can make ``\\`` a separator (a DOS host) and a provisional directory
    a file or a link, which drops the trailing ``/``."""
    names = {name, name.replace("\\", "/")}
    return names | {n[:-1] for n in names if n.endswith("/")}


def _until_listing_damage(
    reader: BaseArchiveReader,
    pairs: Iterator[tuple[ArchiveMember, ArchiveStream | None]],
    found: list[CorruptionError],
) -> Iterator[tuple[ArchiveMember, ArchiveStream | None]]:
    """``pairs``, ending quietly when the listing's own damage ends them.

    That damage is appended to ``found`` for the caller to raise once the members
    before it are done. Only the reader's walk error counts: any other error the
    pass raises propagates at once, as before. An error from the loop body never
    reaches this generator.
    """
    try:
        yield from pairs
    except CorruptionError as exc:
        if exc is not reader._walk_error:
            raise
        found.append(exc)


@dataclass
class _Orphan:
    """A selected hardlink whose source was not yet on disk, awaiting the second pass.

    ``transformed`` is the policy/filter-transformed copy from the first pass: it supplies
    the on-disk identity (mode, timestamps) when the source's content is materialized at
    this link's path (see the ``safe-extraction`` "copy supplies the identity" rule).
    ``spelled_from`` is ``transformed`` before its disk spelling, when that differs, so
    an error names it as the first pass would (``_report_stored_spelling``).
    """

    result_index: int
    original: ArchiveMember
    transformed: ArchiveMember
    dest_path: Path
    source: ArchiveMember
    spelled_from: ArchiveMember | None = None

    def report_stored_spelling(self, exc: ArchiveyError | OSError) -> None:
        if isinstance(exc, ArchiveyError) and self.spelled_from is not None:
            _report_stored_spelling(exc, self.transformed, self.spelled_from)


@dataclass(frozen=True)
class _Claim:
    """A destination claimed by a member written this run.

    The path alone was enough while a collision only had to be *detected*; recording the
    claiming member's result index as well is what lets a later ``REPLACE`` collision
    revise the earlier result to ``OVERWRITTEN`` instead of leaving two members both
    reporting ``EXTRACTED`` at one path.

    ``physical`` is where the claimed file was when it was claimed, with every parent
    resolved. The key is taken from it, and a collision is routed to it. ``path`` can
    run through a symlink the archive later repoints, so it can stop naming that file.
    """

    path: Path
    result_index: int
    physical: Path


@dataclass
class _RunState:
    """One ``run()``'s state: where it writes, what it has written, its tallies.

    ``_run`` builds a new one per run, so nothing carries over from an earlier run.
    """

    # The destination as given, and resolved.
    dest: Path
    dest_root: Path
    tracker: BombTracker
    # The reader being extracted from, for the one read ``_transform`` makes on it:
    # an accepted link's target.
    reader: BaseArchiveReader | None = None
    forward_only: bool = False
    # Progress totals; see ``_run``.
    members_total: int | None = None
    total_estimate: int | None = None
    results: list[ExtractionResult] = field(default_factory=list)
    # Running counts of EXTRACTED and BLOCKED results, kept here rather than in the
    # pass loop because a REPLACE collision, a link recheck or a streaming take-back
    # revises an *earlier* member's result and must correct the tally with it
    # (progress reports tallies of results, not of writes attempted).
    members_extracted: int = 0
    members_blocked: int = 0
    # Result index -> output bytes counted for it, for each member that reached
    # ``tracker.start_member``: what a streaming take-back refunds.
    counted: dict[int, int] = field(default_factory=dict)
    # Source member id -> the on-disk paths holding that source's content.
    source_paths: dict[int, list[Path]] = field(default_factory=dict)
    # The entries this run has written under ``dest``.
    written_paths: set[Path] = field(default_factory=set)
    # O2 collision map: casefold(NFC(relpath)) key (exact under TRUSTED) -> the claim
    # (written path + claiming member's result index). Tracks non-directory members
    # written THIS run so a second member resolving to the same key is a deterministic
    # collision on every OS (not a platform-dependent silent merge), and so a REPLACE
    # resolution can revise the earlier member's result. See _write_member /
    # dev-docs/decisions/0013.
    collision_map: dict[str, _Claim] = field(default_factory=dict)
    # The key each claimed path was claimed under (see ``_claim``).
    claim_keys: dict[Path, str] = field(default_factory=dict)
    # Claimed files by identity, (st_dev, st_ino) -> the path claimed, for a name the
    # filesystem makes a second name of one (``_alias_claim``). Filled lazily: a path
    # waits in ``unindexed_claims`` until the first lookup after it was claimed.
    claim_ids: dict[tuple[int, int], Path] = field(default_factory=dict)
    unindexed_claims: list[Path] = field(default_factory=list)
    # RENAME: the last ``N`` tried per collision key of the requested name, so the
    # next member colliding on that key resumes after it instead of rescanning from
    # ``(1)``. Cleared whenever a claim is released (a freed name may be the first
    # free one again).
    rename_next: dict[str, int] = field(default_factory=dict)
    # Directories RENAME wrote under a derived name: the path the member asked for ->
    # the path it was written to. Members inside such a directory follow it there
    # (``_follow_renamed_dirs``).
    renamed_dirs: dict[Path, Path] = field(default_factory=dict)
    # Directories this run wrote: collision key of the written path -> indices of the
    # EXTRACTED results that report it. Not collision claims (directories merge, so
    # they never go in the collision map), but keyed the same way, so a directory
    # reached through the archive's own symlink or by a case variant is one entry.
    # Kept so a REPLACE that removes an empty directory this run wrote can revise
    # that result to OVERWRITTEN.
    written_dirs: dict[str, list[int]] = field(default_factory=dict)
    # Hardlinks whose source was not on disk yet, for the second pass.
    orphans: list[_Orphan] = field(default_factory=list)
    # Superseded copies the filesystem refused to remove, by archive name, as in
    # ``_MemberState.parked``: the next member of that name parks them again.
    unremoved: dict[str, dict[Path, int]] = field(default_factory=dict)
    # Directories ``_makedirs`` created this run, as parents of what it wrote.
    created_dirs: set[Path] = field(default_factory=set)
    # Every directory this run wrote or created, by ``_physical_key`` -> where each one
    # physically is (``_physical_path``), so a member that names one under another
    # spelling is not taken for the caller's (``_is_run_directory``).
    run_dirs: dict[str, set[Path]] = field(default_factory=dict)
    # Directory members whose ownership, mode and times wait for the end of the run
    # (``_apply_directory_metadata``): ``_physical_key`` -> where the directory physically
    # is -> the directory's identity on disk (device, inode) when it was written, the
    # number of ``/`` in that physical path relative to the root (so a deeper
    # directory sorts first), and the transformed member. In each key, in the order
    # they were last written. Keyed by place, not identity: some filesystems report
    # inode 0 for every entry (``_Identity.of`` in the directory reader). By the
    # physical path, not the spelled one: two spellings through a symlink of the
    # archive's share one entry, and the metadata pass opens the directory through
    # shallower directories only, which it has not changed yet. Under a casefolded
    # key, so a removal by a case variant finds the entry (``_drop_pending_dir``).
    pending_dirs: dict[str, dict[Path, tuple[tuple[int, int], int, ArchiveMember]]] = (
        field(default_factory=dict)
    )
    # ``_physical_rel``'s resolved parents, as spelled -> relative to the root with a
    # trailing ``/``. Only a symlink created, replaced or removed changes a
    # resolution, so each of those clears it (``_note_link_change`` and
    # ``_resolutions_changed``).
    resolved_parents: dict[str, str] = field(default_factory=dict)
    # ``_hardlink_chain_end``'s memo: a hard link's ``_member_id`` -> the first
    # member on its chain that is not a HARDLINK, or ``None``.
    hardlink_ends: dict[int, ArchiveMember | None] = field(default_factory=dict)
    # Member id -> the index of the result recorded for it, so a hard link can read
    # what this run did with its source (``_source_refused``).
    result_ids: dict[int, int] = field(default_factory=dict)
    # ``_source_refused``'s memo for hard-link sources this run recorded no result
    # for (a selector or filter excluded them): member id -> whether the policy would
    # refuse it.
    refusals: dict[int, bool] = field(default_factory=dict)
    # The symlinks this run created and the paths each one's resolution depends on,
    # so a later member that changes such a path gets them rechecked.
    links: LinkWatch | None = None
    # Where the platform has two kinds of symlink (``_DIRECTORY_LINKS``): each symlink
    # this run created -> the target it was created with and whether it was made a
    # directory link, so the end of the run can remake one whose target turned out
    # the other kind (``_settle_link_kinds``).
    link_kinds: dict[Path, tuple[str, bool]] = field(default_factory=dict)
    # The archive's listing, when the run has one for free, and its names folded and
    # sorted with the set of directory members' names (``_listed_directory``, built on
    # first use).
    listed: list[ArchiveMember] | None = None
    listed_names: tuple[list[str], set[str]] | None = None
    # The stored target of each tracked symlink whose target was written differently
    # (a disk spelling, Windows separators, a dry run's scratch path), by result
    # index, so a recheck error names it as listed.
    stored_targets: dict[int, str] = field(default_factory=dict)
    # RAR file copies (``_open_written_source``). ``streaming_now`` is ``id()`` of
    # the member whose stream is being written; a file-copy source arriving then is
    # not kept by the pass (``_keep_copy_source``), and goes in ``declined``. Only
    # a declined source's file gets its identity recorded after the write
    # (``_file_identity``), by its member id and path, so a later member written
    # to the same path never vouches for it, and no other file costs a stat. A
    # copy is then copied from that file if it still matches.
    streaming_now: int | None = None
    declined: set[int] = field(default_factory=set)
    # A pass whose members can change once its walk ends (a ZIP from a pipe, whose
    # central directory comes last; ``_REVISES_YIELDED_MEMBERS``): result index -> the
    # member as the pass handled it and the member written for it (``None`` when
    # nothing was), so the end of the pass settles what changed
    # (``_settle_revised_members``). ``None`` for a member the selection or the
    # filter left out during the pass (``_hold_place``).
    revisable: dict[int, tuple[ArchiveMember, ArchiveMember | None] | None] = field(
        default_factory=dict
    )
    # Results the end of the pass took back with no outcome of their own: the filter
    # excluded the member once it was changed, so random access records nothing for
    # it. Dropped from the report when the run returns it.
    dropped: set[int] = field(default_factory=set)
    # Held places of members the selection left out, which the pass did not count
    # toward ``members_done``; the end of the pass counts the ones it writes.
    untallied: set[int] = field(default_factory=set)
    # Whether the pass ended and settled what it wrote (``_settle_revised_members``);
    # a run stopped before then never knew which provisional writes to keep.
    settled: bool = False
    # Entries that were at a destination before the run, moved aside so a member
    # the pass wrote there before the walk settled it could be taken back
    # (``_set_aside``): the destination -> where the entry is now.
    set_aside: dict[Path, Path] = field(default_factory=dict)
    written_files: dict[tuple[int, Path], tuple[int, int, int, int]] = field(
        default_factory=dict
    )


@dataclass
class _MemberState:
    """What the member being handled shares with its write path, in both directions.

    ``_run_pass`` builds a new one per member, and one for the orphan second pass,
    so nothing carries over.
    """

    # The destination the member asked for, published so the per-member error
    # handler can record it on a FAILED/BLOCKED result. A collision resolved by ERROR
    # raises out of the write, which is exactly the case where the spec still requires
    # ``requested_path`` set with ``path=None``; it has to carry the collision too.
    requested_path: Path | None = None
    collided_with: Path | None = None
    # Set by ``_transform`` when reading a link's target showed the member is not a
    # link after all, so the pass yielded it with no data stream.
    retyped: bool = False
    # Set by ``_transform`` to the member before ``disk_spelled`` when the disk
    # spelling differs, so an error raised while writing can name it as listed.
    spelled_from: ArchiveMember | None = None
    # Set by ``_prepare_destination`` when it removes an existing entry to make room.
    # Only the non-atomic paths (DIR / SYMLINK / HARDLINK) do that — a FILE write
    # lands via os.replace and never destroys the destination up front — so this is
    # how the write path knows a failure afterwards left a hole rather than the old
    # content.
    removed_existing: bool = False
    # Intra-member progress emit while copying a FILE (None when on_progress is unset
    # or the member has no streamed body).
    emit_progress: Callable[[], None] | None = None
    # Set by ``_run_pass`` for a member the walk can still change, so a caller's
    # entry it replaces is moved aside rather than removed (``_set_aside``).
    provisional: bool = False
    # Set by ``_redo_revised_member`` when the FILE body is this run's own earlier
    # copy, read back from disk: nothing is decoded, so it counts as a copy
    # (``BombTracker.count_copy``), not toward the decompression ratio.
    recopy: bool = False
    # Where that earlier copy was moved to be read from (``_open_parked_copy``).
    recopy_from: Path | None = None
    # A streaming pass's earlier copies of this member's name, left in place while it
    # is handled so it can replace one atomically: path -> index of the superseded
    # result. See ``_park_earlier_copies``.
    parked: dict[Path, int] = field(default_factory=dict)


@dataclass
class _DryRun:
    """Where a dry run extracts, and how its paths are shown under the caller's dest.

    Built by ``ExtractionCoordinator.run`` for the length of one dry run.
    """

    # The directory the pass extracts into, inside a private scratch directory, and
    # the destination the caller named, which every path the run reports is
    # translated back to (``shown``).
    scratch: Path
    shown_dest: Path
    # The scratch directory spelled as the pass is handed it, and where a path under
    # its ``os.path.abspath`` spelling is shown. A real run reports paths built from
    # ``dest`` as given, but an OSError about a staging file names it as ``tempfile``
    # spells it, through ``os.path.abspath``: absolute, ``..`` collapsed, symlinks
    # kept. Where those two spellings of dest differ, the pass is handed a spelling
    # of the scratch directory that differs from its own ``abspath`` the same way, so
    # each path can be shown in the spelling a real run would give it.
    # (``dest.resolve()`` spells nothing a real run reports; it is only for matching
    # link targets, ``dest_spellings``.)
    scratch_given: Path
    shown_abspath: Path
    # The caller's destination as the absolute path they gave and as it resolves,
    # which an absolute link target is matched against (``target_on_disk``).
    dest_spellings: tuple[PurePath, PurePath]
    # Where FILE bodies go instead of the file (``os.devnull``).
    sink: BinaryIO

    def target_on_disk(self, target: str) -> str:
        """The target a link is created with: an absolute target that names a path
        under dest is rewritten to the same path under the scratch copy.

        Matched by name against dest as given and as it resolves, not resolved itself,
        so ``..`` components and symlinks on the way are left for the checks that
        resolve the link. Any other target is returned as it is.
        """
        pure = PurePath(target)
        if not pure.anchor:
            return target
        if not pure.drive:
            # Windows root-relative (a target like \x): pathlib joins it onto the
            # link's own drive, which is dest's. A no-op elsewhere, where drive is "".
            pure = PurePath(self.dest_spellings[0].drive + target)
        for spelling in self.dest_spellings:
            if pure.is_relative_to(spelling):
                return str(self.scratch / pure.relative_to(spelling))
        return target

    def shown(self, path: Path) -> Path:
        """``path`` as the caller sees it: a scratch path under their dest, spelled as
        a real run would spell it (see ``scratch_given``)."""
        for scratch, shown in (
            (self.scratch_given, self.shown_dest),
            (self.scratch, self.shown_abspath),
        ):
            try:
                return shown / path.relative_to(scratch)
            except ValueError:
                continue
        return path

    def rebase_result(self, result: ExtractionResult) -> ExtractionResult:
        if isinstance(result.error, OSError):
            self.rebase_os_error(result.error)
        return replace(
            result,
            path=None if result.path is None else self.shown(result.path),
            requested_path=(
                None
                if result.requested_path is None
                else self.shown(result.requested_path)
            ),
            collided_with=(
                None
                if result.collided_with is None
                else self.shown(result.collided_with)
            ),
        )

    def rebase_os_error(self, exc: OSError) -> None:
        """Point a filesystem error's file names at the caller's dest, in place."""
        for attr in ("filename", "filename2"):
            value = getattr(exc, attr, None)
            if isinstance(value, str):
                setattr(exc, attr, str(self.shown(Path(value))))

    def check_creatable(self) -> None:
        """Raise what ``mkdir(parents=True)`` would raise for the absent ``shown_dest``,
        without creating it.

        The pass itself writes into the scratch directory, which exists already. Here
        the nearest part of dest that exists gets the questions ``mkdir`` would answer:
        that it can be resolved, is a directory, and can be written to. Each raises
        the error ``mkdir`` raises, naming the path it names. Whether it can be written
        to is asked with ``os.access``, which is a prediction, not the write: it checks
        the real user and group ids where ``mkdir`` uses the effective ones, the
        directory can change after it is asked, and on Windows it is not asked.
        """
        dest = self.shown_dest
        child, ancestor = dest, dest.parent
        while not os.path.lexists(long_path(ancestor)) and ancestor != ancestor.parent:
            child, ancestor = ancestor, ancestor.parent
        try:
            st = os.stat(long_path(ancestor))
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                # A dangling symlink: mkdir meets it as an existing entry.
                raise FileExistsError(
                    errno.EEXIST, os.strerror(errno.EEXIST), str(ancestor)
                ) from None
            # ELOOP and the like, met while resolving dest itself.
            raise OSError(exc.errno, exc.strerror, str(dest)) from None
        if not stat.S_ISDIR(st.st_mode):
            raise NotADirectoryError(
                errno.ENOTDIR, os.strerror(errno.ENOTDIR), str(dest)
            )
        if os.name == "nt":
            # os.access there reads the read-only attribute, which does not stop
            # creating entries in a directory.
            return
        if not os.access(long_path(ancestor), os.X_OK):
            raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(dest))
        if not os.access(long_path(ancestor), os.W_OK):
            # The first directory mkdir creates is the one it is refused.
            raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(child))


class ExtractionCoordinator:
    """Drives a single forward pass over a reader's members, writing them safely to disk."""

    def __init__(
        self,
        *,
        policy: ExtractionPolicy = ExtractionPolicy.STRICT,
        overwrite: OverwritePolicy = OverwritePolicy.ERROR,
        on_error: OnError = OnError.STOP,
        on_progress: Callable[[ExtractionProgress], None] | None = None,
        selector: Callable[[ArchiveMember], bool] | None = None,
        filter: MemberFilter | None = None,
        limits: ExtractionLimits | None = None,
        abort_on: Collection[AbortOn] = (),
        dry_run: bool = False,
    ) -> None:
        self._policy = policy
        self._dry_run = dry_run
        self._overwrite = overwrite
        self._on_error = on_error
        self._abort_on = frozenset(abort_on)
        self._on_progress = on_progress
        # A predicate, never the caller's raw ``members=``: that argument may be a
        # one-shot iterable, so only the public entry point reads it, and the caller
        # passes the normalized result here.
        self._selector = selector
        self._filter = filter
        self._limits = limits if limits is not None else ExtractionLimits()
        # Placeholders until ``run()`` builds the run's state, so the attribute is never
        # None and helpers read it unguarded. A test that drives a helper directly
        # installs its own ``_RunState``.
        self._state = _RunState(Path(), Path(), BombTracker(None, None))
        # The state of the member being handled; each pass replaces it per member.
        self._current = _MemberState()
        # Set for the length of a dry run; ``None`` on a real run.
        self._dry: _DryRun | None = None
        # Set by a dry run, from its scratch tree; see ExtractionReport.
        self.dry_run_top_level: tuple[tuple[str, bool], ...] | None = None
        self.dry_run_links: tuple[tuple[str, str], ...] | None = None
        self.dry_run_entries: tuple[tuple[str, bool], ...] | None = None

    # --- entry point ---------------------------------------------------------------

    def run(
        self, reader: BaseArchiveReader, dest: str | Path
    ) -> list[ExtractionResult]:
        """Extract into ``dest``; under ``dry_run``, into a private scratch directory.

        A dry run is the same pass, not a model of it. Directories, symlinks and
        hardlinks are created for real under the scratch directory, so every check that
        consults the filesystem (a parent resolved through an earlier member's symlink,
        a link escape, a collision) behaves as it would in ``dest``. Each FILE body is
        read, decompressed, verified and counted against the limits, then discarded:
        the file itself is created empty. The scratch directory starts empty, so the
        run shows what extracting into an empty ``dest`` would do; ``dest`` itself is
        checked the way a real run checks it and is never created. Paths in the results
        and in errors are reported under ``dest``, and the scratch directory is removed
        before this returns or raises. What a completed pass left directly under the
        scratch copy of ``dest`` is kept in ``dry_run_top_level``.

        Where the scratch tree cannot stand in for ``dest``, the run says less than a
        real one would. A link target that leaves ``dest`` is resolved outside the
        scratch tree, so one that comes back into ``dest`` through a symlink outside it,
        or climbs above the directory that holds it, is refused where a real run could
        accept it; an absolute target is matched against ``dest`` by name and is not
        affected. And everything is written to one filesystem, so a hardlink never falls
        back to a copy: the bytes such a copy counts against ``max_extracted_bytes`` in
        a ``dest`` that spans a mount point are not counted.
        """
        dest = Path(dest)
        if not self._dry_run:
            try:
                return self._run(reader, dest)
            except OSError as exc:
                _plain_os_error(exc)
                raise
        given = Path(os.path.abspath(dest))
        try:
            resolved = resolve_or_raise_on_loop(given)
        except OSError:
            resolved = given
        # Resolved, so a path built from the resolved root (``dest_root``) and one built
        # from ``dest`` translate the same way (macOS's /var -> /private/var). The pass
        # extracts into a directory named like dest, one level down, so a relative link
        # target that climbs out of dest by name (``../out/x``) comes back in.
        scratch = Path(tempfile.mkdtemp(prefix=_DRY_RUN_PREFIX)).resolve()
        work = scratch / (resolved.name or "dest")
        work_given = work
        detour: Path | None = None
        if str(dest) != str(given):
            if not dest.is_absolute():
                with contextlib.suppress(ValueError):  # Windows: on another drive
                    work_given = Path(os.path.relpath(work))
            else:
                # Spelled with ``..``: so is the scratch directory, through a sibling.
                detour = scratch / ("_" if work.name != "_" else "__")
                work_given = detour / ".." / work.name
        try:
            with open(os.devnull, "wb") as sink:
                dry = self._dry = _DryRun(
                    scratch=work,
                    shown_dest=dest,
                    scratch_given=work_given,
                    shown_abspath=given if work_given != work else dest,
                    dest_spellings=(given, resolved),
                    sink=sink,
                )
                try:
                    os.mkdir(long_path(work))
                    if detour is not None:
                        os.mkdir(long_path(detour))
                    results = self._run(reader, work_given)
                except OSError as exc:
                    _plain_os_error(exc)
                    dry.rebase_os_error(exc)
                    raise
            with contextlib.suppress(OSError):
                with os.scandir(long_path(work)) as entries:
                    self.dry_run_top_level = tuple(
                        sorted(
                            (entry.name, entry.is_dir(follow_symlinks=False))
                            for entry in entries
                        )
                    )
                if len(self.dry_run_top_level) == 1:
                    # What the CLI's hoist prediction walks; see ExtractionReport.
                    tree = _scratch_tree(work)
                    if tree is not None:
                        self.dry_run_entries, self.dry_run_links = tree
            return [dry.rebase_result(result) for result in results]
        finally:
            self._dry = None
            _remove_scratch(scratch)

    @property
    def _on_disk(self) -> Callable[[str], str] | None:
        """``check_universal``'s ``link_target_on_disk``: set on a dry run only."""
        return self._dry.target_on_disk if self._dry is not None else None

    def _link_target_on_disk(self, target: str) -> str:
        """The target a link is created with (see ``_DryRun.target_on_disk``).

        On Windows each ``/`` is written as ``\\`` first. A symlink's target is stored
        as given, and Windows resolves it with ``\\`` as the only separator, so
        ``sub/file`` would dangle there while it resolves on POSIX. Windows cannot hold
        ``/`` in a name, so the character can only mean a separator. Git for Windows
        and Node write link targets the same way.
        """
        if _WINDOWS:
            target = target.replace("/", "\\")
        return target if self._dry is None else self._dry.target_on_disk(target)

    def _shown(self, path: Path) -> Path:
        """``path`` as the caller sees it (see ``_DryRun.shown``)."""
        return path if self._dry is None else self._dry.shown(path)

    def _rebase_os_error(self, exc: OSError) -> None:
        """Point a filesystem error at the paths the caller sees, in place: without the
        long-path prefix, and under a dry run at the caller's dest."""
        _plain_os_error(exc)
        if self._dry is not None:
            self._dry.rebase_os_error(exc)

    def _run(self, reader: BaseArchiveReader, dest: Path) -> list[ExtractionResult]:
        forward_only = reader._streaming
        tracker = BombTracker(
            self._limits.max_extracted_bytes,
            self._limits.max_ratio,
            self._limits.ratio_activation_threshold,
            self._limits.max_entries,
            source=reader,
            live_ratio_activation_threshold=(
                self._limits.live_ratio_activation_threshold
            ),
        )

        selector = self._selector

        # Progress totals cover what this call will actually attempt: when a member list
        # is free (an upfront index) and a selector is given, totals count only the
        # selected members — so members_done can reach members_total and the byte
        # estimate matches the selected output. The user `filter` runs only during
        # extraction and cannot be pre-applied, so members it skips still count as
        # processed below. Streaming readers with no free list report None totals.
        members_report = reader.members_report_if_available()
        all_members = list(members_report) if members_report is not None else None
        # A free list that ends in damage holds only the members before it. What it
        # says no selected entry matches is not known, and the pass has to reach the
        # damage to raise it (see _run_pass).
        listing_damaged = (
            members_report is not None and members_report.error is not None
        )
        if all_members is not None and selector is not None:
            all_members = [m for m in all_members if selector(m)]
        # A members= collection whose entries all went through the free list is
        # settled now: report the entries that matched nothing before anything is
        # written or created, so a RAISE disposition refuses the call with nothing on
        # disk.
        # Without a free list the answer is known only at the end of the pass.
        unmatched_pending: CollectionSelector | None = None
        if isinstance(selector, CollectionSelector):
            if all_members is not None and not listing_damaged:
                selector.report_unmatched(
                    reader._diagnostics_collector, reader._archive_name
                )
            else:
                unmatched_pending = selector
        # Created only after that report, so a refusal leaves no directory behind.
        created_root = self._ensure_dest_root(dest)
        dest_root = resolve_or_raise_on_loop(dest)
        members_total = len(all_members) if all_members is not None else None
        self._state = _RunState(
            dest=dest,
            dest_root=dest_root,
            reader=reader,
            forward_only=forward_only,
            tracker=tracker,
            members_total=members_total,
            total_estimate=self._estimate_total_bytes(all_members),
            created_dirs={dest} if created_root else set(),
            # Rechecks share the entry-count bound: a hostile archive can make each
            # member recheck every link so far, and max_entries is what bounds members.
            links=LinkWatch(dest_root, self._limits.max_entries),
            listed=all_members if _DIRECTORY_LINKS else None,
        )

        # Extract-prep: every pass enforces ListingLimits as it lists, so this only
        # decides when the listing happens. Most random-access readers list everything
        # first, so nothing is written from an archive over the limits; TAR and every
        # streaming reader list as members arrive in the one pass
        # (_list_before_extraction).
        reader._list_before_extraction()

        # The pass is driven through the public stream_members(), which applies the
        # selection (skipped members never surface here — they are invisible to progress
        # and results, matching the totals above). When the totals pre-filtered the free
        # member list, that list is reused as an identity selector, so a user predicate
        # runs once per member (on the index) rather than once per pre-filter plus once
        # per yield; a stateful predicate still sees each member a single time. Without
        # a free list the predicate itself is passed through.
        #
        # Every pass the coordinator drives hands stream_members() a CollectionSelector
        # built here, with no bookkeeping. A selector that stream_members() builds from a
        # collection reports its own unmatched entries, and a collection built here is
        # the coordinator's, not the caller's (the hardlink second pass does the same).
        stream_selector = (
            None
            if selector is None
            else CollectionSelector(list(all_members), record=False)
            if all_members is not None
            else selector
        )

        # Explicit selection with a free member list: once every selected member has
        # been seen, stop the forward pass. Solid backends otherwise keep walking the
        # rest of the archive (and would skip-decode unread tails if positioning were
        # eager). Predicate selectors and streaming backends have no finite remaining
        # count, so they still drain.
        selected_total = (
            members_total
            if selector is not None
            and members_total is not None
            and not listing_damaged
            else None
        )

        try:
            self._run_pass(reader, stream_selector, selected_total)
        except _AbortExtraction as abort:
            # An abort_on trigger fired: no report is returned, and output already
            # written for earlier members stays on disk (same as OnError.STOP), except
            # what a revising pass (a ZIP read from a pipe) takes back in ``finally``
            # below: the files it wrote that could still be links. Nothing
            # partial exists for the triggering member — every trigger fires before its
            # write begins, and FILE writes land atomically in any case.
            raise abort.error from None
        finally:
            # Also when the run stops early. A stop before the end of a revising
            # pass settled every member first takes back the files that may be
            # links, so the caller's entries they replaced come back below.
            if self._state.revisable:
                self._take_back_possible_links()
            # The caller's entries a streaming pass moved aside come back first,
            # where nothing of the run's replaced them.
            if self._state.set_aside:
                self._restore_set_aside()
            # Links first: remaking one changes its directory, whose times come next.
            self._settle_link_kinds()
            # The directories the run did write end with their stored metadata, as
            # the ones of a completed run do.
            self._apply_directory_metadata()

        if unmatched_pending is not None:
            unmatched_pending.report_unmatched(
                reader._diagnostics_collector, reader._archive_name
            )
        dropped = self._state.dropped
        return [r for i, r in enumerate(self._state.results) if i not in dropped]

    def _run_pass(
        self,
        reader: BaseArchiveReader,
        stream_selector: Callable[[ArchiveMember], bool] | None,
        selected_total: int | None,
    ) -> None:
        """The forward pass and the orphan second pass, appending into the results.

        Split out of ``run()`` only so an ``abort_on`` trigger raised anywhere inside it
        has one place to unwind to; the results are filled in place because an abort
        discards them rather than returning them.
        """
        state = self._state
        results = state.results
        tracker = state.tracker
        members_done = 0
        # Archive name -> result index of the latest member of that name that went on to
        # be written (or tried). Random access stamps last-entry-wins before the pass, so
        # a shadowed copy reaches the SUPERSEDED branch below and is never recorded here.
        # A streaming pass learns of the later copy only when it arrives; see
        # ``_park_earlier_copies``.
        current_by_name: dict[str, int] = {}

        copies = FileCopyPass(keep_source=self._keep_copy_source)
        # Damage that ends the listing ends this loop, not the pass: the members listed
        # before it are all written, including the hardlinks the second pass below
        # completes, and then it is raised. A hardlink only points back, so every
        # source the second pass needs is in that prefix.
        # The pass is not bound to a name: an error out of the loop body then drops
        # it at once, which closes the open member stream (a held traceback would
        # otherwise keep it, and an unrar child, alive).
        listing_damage: list[CorruptionError] = []
        revises = reader._REVISES_YIELDED_MEMBERS
        # A member the walk can still rename or retype is selected on what it
        # becomes, so the reader yields it whatever the selection says now; the loop
        # holds its place (``_hold_place``).
        walk_selector = (
            stream_selector
            if stream_selector is None or not revises
            else _final_members_only(stream_selector)
        )
        for original, stream in _until_listing_damage(
            reader, reader._stream_members(walk_selector, copies), listing_damage
        ):
            member_started = False
            recorded_index: int | None = None
            presented_name: str | None = None
            rewrites: frozenset[NameRewrite] = frozenset()
            current = self._current = _MemberState()
            link_error: ArchiveyError | None = None
            provisional = current.provisional = (
                revises and not original.member_state_final
            )
            transformed: ArchiveMember | None = None
            if (
                provisional
                and stream_selector is not None
                and not _provisionally_selected(stream_selector, original)
            ):
                # The selection is made again on what the member becomes
                # (``_settle_revised_members``); until then it holds a place, and
                # is not counted as handled.
                self._close(stream)
                self._hold_place(original, selected_later=True)
                continue
            # What the member was when handled, if the walk can still change it.
            before = original.replace() if provisional else None
            try:
                self._park_earlier_copies(
                    original.name, current_by_name.get(original.name)
                )
                current_by_name.pop(original.name, None)
                # User filter sees every selected member (including non-current); the
                # is_current skip is hardwired after the filter and does not force a write
                # even if the filter returns the member.
                transformed, presented_name, rewrites = self._transform(original)
                if transformed is None:
                    # Filter returned None: caller-elected exclusion — no ExtractionResult
                    # (same as a selector exclusion). Still counts as processed for progress.
                    # A member the walk can still change holds a place until the
                    # filter has seen what it becomes.
                    if before is not None:
                        recorded_index = self._hold_place(original)
                elif not original.is_current:
                    recorded_index = self._append_result(
                        ExtractionResult(
                            original, None, ExtractionStatus.SUPERSEDED, None
                        )
                    )
                else:
                    result_index = recorded_index = self._append_result(
                        ExtractionResult(original, None, ExtractionStatus.FAILED, None)
                    )
                    current_by_name[original.name] = result_index
                    if not original.is_anti:
                        # Entry-count guard + ratio bookkeeping. Counted only once the
                        # selector and user filter have accepted the member (and the
                        # universal check inside _transform has passed), immediately before
                        # writing begins — so selector-skipped, filter-skipped, and rejected
                        # members create nothing on disk and do not count toward max_entries
                        # (resolved 2026-07 decision).
                        tracker.start_member(original)
                        member_started = True
                        if self._on_progress is not None:
                            # Capture members_done for intra-member reports: members fully
                            # completed *before* this one. The outcome tallies are read
                            # live: they change only when a member completes, except that
                            # this member's REPLACE can revise an earlier result first.
                            done_so_far = members_done
                            member = original

                            def emit_progress() -> None:
                                self._report_progress(
                                    member, done_so_far, tracker.member_bytes
                                )

                            current.emit_progress = emit_progress

                    written = self._write_member(
                        original, transformed, stream, result_index
                    )
                    self._set_result(result_index, written)
            except (_AlwaysStopResourceLimitError, DiagnosticRaisedError):
                raise
            except (ArchiveyError, OSError) as exc:
                spelled_from = current.spelled_from
                if isinstance(exc, ArchiveyError) and spelled_from is not None:
                    _report_stored_spelling(
                        exc, disk_spelled(spelled_from), spelled_from
                    )
                error, status = self._classify(exc, original.name)
                result = ExtractionResult(
                    original,
                    None,
                    status,
                    error,
                    requested_path=current.requested_path,
                    collided_with=current.collided_with,
                )
                if results and results[-1].member is original:
                    recorded_index = len(results) - 1
                    self._set_result(recorded_index, result)
                else:
                    recorded_index = self._append_result(result)
                self._stop_or_log(
                    exc, error, status, kind=original.type.value, name=original.name
                )
            finally:
                self._close(stream)
                if current.parked:
                    self._drop_parked_copies(original.name, recorded_index)
                link_error = self._recheck_links()
            if link_error is not None:
                raise link_error

            if member_started and recorded_index is not None:
                state.counted[recorded_index] = tracker.member_bytes
            if (
                before is not None
                and recorded_index is not None
                and recorded_index not in state.dropped
            ):
                landed = results[recorded_index].status is ExtractionStatus.EXTRACTED
                state.revisable[recorded_index] = (
                    before,
                    transformed if landed else None,
                )

            # A portable rewrite is recorded on whatever result this member ended up with,
            # including a BLOCKED/FAILED one: the rewrite happened before the outcome.
            if presented_name is not None and recorded_index is not None:
                self._set_result(
                    recorded_index,
                    replace(
                        results[recorded_index],
                        presented_name=presented_name,
                        rewrites=rewrites,
                    ),
                )

            members_done += 1
            self._report_progress(
                original,
                members_done,
                tracker.member_bytes if member_started else 0,
            )
            if selected_total is not None and members_done >= selected_total:
                break

        failed_at_end: list[tuple[int, ArchiveyError]] = []
        if state.revisable or reader._failed_at_end:
            failed_at_end = self._settle_revised_members(stream_selector, members_done)
        state.settled = True
        if failed_at_end and self._stops_on_failure():
            # The first such failure, now that every revision is made.
            raise min(failed_at_end, key=lambda found: found[0])[1]
        if listing_damage and any(
            listing_damage[0] is error for _, error in reader._failed_at_end
        ):
            # The walk raised for a member the settlement above has dealt with. Its
            # result carries the failure, unless a seekable read of the same bytes would
            # not have read the member's data either: the selection or the filter left
            # it out, the directory makes it not current, or its result carries an
            # outcome decided before any of its data was read (by the filter, a policy
            # check or the overwrite policy). Then nothing reports it, as from a file.
            listing_damage.clear()

        # Core second pass: resolve orphaned hardlinks whose (re-readable) source was
        # excluded. Only populated for a seekable source; forward-only orphans already
        # failed at the link during the main pass.
        if state.orphans:
            # Its own member state: none of the first pass's carries over, and it
            # reports no intra-member progress.
            self._current = _MemberState()
            self._resolve_orphans(reader)
        if listing_damage:
            raise listing_damage[0]

    def _park_earlier_copies(self, name: str, earlier: int | None) -> None:
        """Park every earlier non-directory copy of ``name`` still on disk for the
        member of that name now being handled (streaming only): result ``earlier``,
        taken back now, and any an earlier take-back could not remove, unless another
        member has since replaced it under its own claim. A superseded directory is
        never parked; see ``_supersede_written_copy``.

        Random access never writes a shadowed copy; a streaming pass finds out only
        when the later copy arrives. A parked path counts as free (``_occupied``,
        ``_prepare_destination``), so the member replaces it atomically under any
        overwrite policy, and ``_drop_parked_copies`` removes the rest afterwards.
        """
        state = self._state
        parked = self._current.parked
        for path, index in state.unremoved.pop(name, {}).items():
            claim = state.collision_map.get(self._claimed_key(path))
            if claim is not None and claim.path == path and claim.result_index == index:
                parked[path] = index
                self._release_claim(path)
        if earlier is not None:
            self._supersede_written_copy(earlier)

    def _supersede_written_copy(self, index: int) -> None:
        """Take back an earlier copy of a name the archive holds again (streaming only).

        The earlier result becomes ``SUPERSEDED`` here, before the later copy is
        filtered: random access supersedes it whatever then happens to the later copy.
        Its file is parked (see ``_park_earlier_copies``). Its claim, its place in the
        hardlink source lists and its bomb-limit counts are released now. A hardlink
        already made to it is its own directory entry and stays, and its bytes stay
        counted against the byte cap while it holds them. A directory is removed now if
        it is empty; one that other members were written into stays, as their parent,
        as it would in random access. An orphaned hardlink waiting on the second pass
        is dropped with the result it would fill. An error recorded on the earlier
        result is dropped with it: random access never tried that copy.
        """
        self._take_back_written(index)
        prior = self._state.results[index]
        self._set_result(
            index,
            ExtractionResult(
                prior.member,
                None,
                ExtractionStatus.SUPERSEDED,
                None,
                presented_name=prior.presented_name,
                rewrites=prior.rewrites,
            ),
        )

    def _take_back_written(self, index: int) -> None:
        """Undo what result ``index`` put on disk and counted, for a member a streaming
        pass wrote and must now handle otherwise (``_supersede_written_copy``,
        ``_redo_revised_member``); the caller records its new result.

        A file is parked in ``_current``, a directory removed when empty, and the
        claim, hardlink-source entries and bomb-limit counts are released.
        """
        state = self._state
        prior = state.results[index]
        path = prior.path
        # Whether another directory entry still holds the earlier copy's content: a
        # hardlink made to it before the later copy arrived.
        content_kept = False
        if (
            prior.status is ExtractionStatus.EXTRACTED
            and path is not None
            and path in state.written_paths
        ):
            self._release_claim(path)
            content_kept = self._forget_source_path(path)
            on_disk = long_path(path)
            if os.path.isdir(on_disk) and not os.path.islink(on_disk):
                # Random access never writes the superseded copy, so its metadata
                # is not applied, whether or not the directory stays as a parent.
                self._drop_pending_dir(path)
                with contextlib.suppress(OSError):  # not empty: members live under it
                    os.rmdir(on_disk)
                    state.written_paths.discard(path)
            else:
                state.written_paths.discard(path)
                self._current.parked[path] = index
        if index in state.counted:
            member_bytes = state.counted.pop(index)
            state.tracker.refund(0 if content_kept else member_bytes)
        state.orphans[:] = [o for o in state.orphans if o.result_index != index]

    def _open_parked_copy(self, path: Path) -> BinaryIO:
        """Open the parked copy at ``path`` to write its member again, from a temporary
        name beside it: the write may land on ``path`` itself (on Windows, a DOS name
        whose backslashes the directory makes separators), and Windows refuses to
        replace a file that is open."""
        aside = self._temp_sibling(path.parent)
        os.replace(long_path(path), long_path(aside))
        self._current.recopy_from = aside
        self._current.parked.pop(path, None)
        return open(long_path(aside), "rb")  # noqa: SIM115

    def _remove_recopy_source(self, aside: Path, name: str) -> None:
        """Remove the moved parked copy once its member is written again or failed:
        the parked copy goes either way (``_drop_parked_copies``). A removal the
        filesystem refuses is logged, not raised: this runs in a ``finally``."""
        with contextlib.suppress(OSError):
            os.chmod(long_path(aside), stat.S_IREAD | stat.S_IWRITE)
        try:
            os.unlink(long_path(aside))
        except FileNotFoundError:
            pass
        except OSError as exc:
            self._rebase_os_error(exc)
            logger.warning(
                "Could not remove %r, the earlier copy of %r: %s",
                str(self._shown(aside)),
                name,
                exc,
            )

    def _drop_parked_copies(self, name: str, result_index: int | None) -> None:
        """Remove each parked copy of ``name`` unless the member just handled, recorded
        at ``result_index``, landed on it.

        A removal the filesystem refuses is logged, not raised: this runs in a
        ``finally``, where raising would replace the member's own error. The entry is
        then still the run's own, so it goes back into ``written_paths`` (an anti-item
        can still delete it) and into the collision map (a different name on the same
        key still collides with it), and is held in ``unremoved`` for the next member
        of the same name. Its claim points at its ``SUPERSEDED`` result, which
        ``_mark_overwritten`` leaves as it is.
        """
        state = self._state
        parked, self._current.parked = self._current.parked, {}
        latest = state.results[result_index] if result_index is not None else None
        # Only a path the member wrote: an anti-item also reports EXTRACTED at its
        # name, but writes nothing there.
        landed = (
            latest.path
            if latest is not None
            and latest.status is ExtractionStatus.EXTRACTED
            and latest.path in state.written_paths
            else None
        )
        for path, index in parked.items():
            if path == landed:
                continue
            if os.path.islink(long_path(path)):
                self._note_link_change(path)
            try:
                with self._readonly_cleared(path, ours=True):
                    os.unlink(long_path(path))
            except FileNotFoundError:
                state.written_paths.discard(path)
            except OSError as exc:
                self._rebase_os_error(exc)
                logger.warning(
                    "Could not remove superseded %r: %s", str(self._shown(path)), exc
                )
                state.written_paths.add(path)
                self._claim(path, index)
                state.unremoved.setdefault(name, {})[path] = index
            else:
                state.written_paths.discard(path)

    def _settle_revised_members(
        self, selector: Callable[[ArchiveMember], bool] | None, members_done: int
    ) -> list[tuple[int, ArchiveyError]]:
        """Bring what the pass wrote in line with the members as the walk left them
        (``_REVISES_YIELDED_MEMBERS``): a ZIP from a pipe learns each member's mode,
        host and full type only from the central directory, after the last member.

        The selection is made here, on what each member became: one it no longer
        selects is taken back with no result, as random access never saw it. A member
        the selection or the filter left out during the pass held a place
        (``_hold_place``) and is handled now, if it is selected, so the filter sees
        it again; one the selection had left out is counted toward ``members_done``
        then, as the pass did not count it. A member whose type or name changed is
        handled again from the start (``_redo_revised_member``). One whose metadata
        alone changed gets it applied to what was written
        (``_reapply_revised_metadata``). A member the walk took out of the listing is
        taken back as an unselected one is. Then the failures only the end of the
        walk finds are settled (``_settle_failures_at_end``), and returned for the
        run to stop on. Nothing else is touched.
        """
        state = self._state
        reader = state.reader
        assert reader is not None
        # ``state.revisable`` keeps the members not settled yet, so a run that stops
        # part way takes back what of theirs may be a link (``_run``).
        revisable = state.revisable
        # Members inside a folder come after it, so the last are taken back first and
        # the folder is empty by the time its turn comes.
        for index in sorted(revisable, reverse=True):
            member = state.results[index].member
            if member._member_id in reader._dropped_listed or (
                selector is not None and not selector(member)
            ):
                del revisable[index]
                if index not in state.dropped:
                    self._unselect_revised_member(index)
        # The selection is final from here: a member settled so far is kept, if
        # the run stops now, as a file run keeps what it wrote before stopping.
        state.settled = True
        settling = list(revisable)
        for index in settling:
            handled = revisable.pop(index)
            member = state.results[index].member
            if handled is None:
                self._redo_revised_member(index)
                # A member the filter then drops counts too, as it does in the pass.
                if index in state.untallied:
                    members_done += 1
                    self._report_progress(
                        member, members_done, state.counted.get(index, 0)
                    )
                continue
            before, written = handled
            if index in state.dropped:
                self._redo_revised_member(index)
            elif member.type is not before.type or member.name != before.name:
                self._redo_revised_member(index)
            elif written is not None:
                self._reapply_revised_metadata(index, before, written)
        return self._settle_failures_at_end(settling, selector, members_done)

    def _settle_failures_at_end(
        self,
        indices: list[int],
        selector: Callable[[ArchiveMember], bool] | None,
        members_done: int,
    ) -> list[tuple[int, ArchiveyError]]:
        """Give the results at ``indices`` the outcome random access would, for what
        only the end of the walk finds (``design.md`` of the native ZIP reader,
        "Failures found at the end of the pass"), and return the failures, by result
        index, for the run to stop on once all of them are made.

        - A member the walk made not current is taken back, if the pass wrote it, and
          ``SUPERSEDED`` whatever its write came to: random access never writes it. A
          refusal from the filter or the policy checks, made before any write (no
          ``requested_path``), stands, as random access runs them on every copy.
        - A member the walk fails (``_failed_at_end``: its data is not what the index
          describes) is taken back and ``FAILED`` with the walk's error, if it is
          current.
        - A member the walk made current after the pass superseded it is handled
          again; its bytes were not written, so it fails unless it needs none.
        - A member the walk fails that it never yielded (an index entry with no data
          in the stream) gets a ``FAILED`` result, if it is selected
          (``_settle_missing_member``), and is reported to ``on_progress`` then, as
          ``members_done`` past the pass's count.

        Whatever was at the path stays only if a member's result there is
        ``EXTRACTED``: the bytes random access would write there have gone by.
        """
        state = self._state
        reader = state.reader
        assert reader is not None
        failures: list[tuple[int, ArchiveyError]] = []
        for index in indices:
            result = state.results[index]
            refused_before_write = (
                result.status in (ExtractionStatus.BLOCKED, ExtractionStatus.FAILED)
                and result.requested_path is None
            )
            if (
                index not in state.dropped
                and result.status is not ExtractionStatus.SUPERSEDED
                and not refused_before_write
                and not result.member.is_current
            ):
                self._current = _MemberState()
                try:
                    self._supersede_written_copy(index)
                finally:
                    if self._current.parked:
                        self._drop_parked_copies(result.member.name, None)
        failing = {id(member): error for member, error in reader._failed_at_end}
        for index in indices:
            result = state.results[index]
            member = result.member
            if index in state.dropped:
                failing.pop(id(member), None)
                continue
            error = failing.pop(id(member), None)
            if error is not None:
                if result.status is ExtractionStatus.EXTRACTED or (
                    member.is_current and result.status is ExtractionStatus.SUPERSEDED
                ):
                    self._fail_at_end(index, error)
                    failures.append((index, error))
            elif result.status is ExtractionStatus.SUPERSEDED and member.is_current:
                self._hold_place_at(index)
                self._redo_revised_member(index, damaged=True, failures=failures)
        for member, error in reader._failed_at_end:
            if id(member) not in failing:
                continue
            if selector is not None and not selector(member):
                continue
            members_done += 1
            self._settle_missing_member(member, error, failures)
            self._report_progress(member, members_done, 0)
        if self._stops_on_failure():
            # The run raises the first and drops the report: no line has a reader.
            return failures
        for index, error in failures:
            logger.warning(
                "Skipping %s %r: %s",
                state.results[index].member.type.value,
                state.results[index].member.name,
                error,
            )
        return failures

    def _settle_missing_member(
        self,
        member: ArchiveMember,
        error: ArchiveyError,
        failures: list[tuple[int, ArchiveyError]],
    ) -> None:
        """Give ``member``, which the index lists and the stream never held, the
        result random access gives it: it is handled as a held place
        (``_redo_revised_member``), so the filter, the policy checks and the overwrite
        policy run as for any member, and its data, read where the write needs it,
        fails with ``error``. A ``FAILED`` result is appended to ``failures`` for the
        caller to stop on; any other outcome stops or is logged there, as the pass
        does."""
        index = self._append_result(
            ExtractionResult(member, None, ExtractionStatus.SUPERSEDED, None)
        )
        self._state.dropped.add(index)
        self._redo_revised_member(index, missing=error, failures=failures)

    def _fail_at_end(self, index: int, error: ArchiveyError) -> None:
        """Take back what result ``index`` wrote, and record it ``FAILED``."""
        state = self._state
        prior = state.results[index]
        self._current = _MemberState()
        try:
            self._take_back_written(index)
        finally:
            if self._current.parked:
                self._drop_parked_copies(prior.member.name, None)
        self._set_result(
            index,
            ExtractionResult(
                prior.member,
                None,
                ExtractionStatus.FAILED,
                error,
                presented_name=prior.presented_name,
                rewrites=prior.rewrites,
            ),
        )

    def _unselect_revised_member(self, index: int) -> None:
        """Take back what result ``index`` wrote for a member the selection no longer
        takes, now that the walk has settled it, and leave it no result. The
        folders created for it go too, once empty: random access never wrote it."""
        state = self._state
        path = state.results[index].path
        self._current = _MemberState()
        try:
            self._take_back_written(index)
            self._hold_place_at(index)
        finally:
            if self._current.parked:
                self._drop_parked_copies(state.results[index].member.name, None)
        # One whose own member the selection also drops is removed when its turn
        # comes, which is after everything inside it.
        self._remove_created_parents(path)

    def _remove_created_parents(self, path: Path | None) -> None:
        """Remove the folders this run created as parents of ``path`` alone, now that
        what it wrote there is taken back: each while it is empty, up to the first
        one a member of the run wrote, or one the run found there."""
        state = self._state
        parent = path.parent if path is not None else None
        while parent is not None and parent != state.dest:
            if parent not in state.created_dirs or parent in state.written_paths:
                break
            try:
                os.rmdir(long_path(parent))
            except OSError:
                break
            self._forget_removed_directory(parent)
            parent = parent.parent

    def _forget_removed_directory(self, path: Path) -> None:
        """Drop what the run recorded for the directory it just removed at ``path``:
        its deferred metadata, and the records that it is the run's own."""
        state = self._state
        self._drop_pending_dir(path)
        state.created_dirs.discard(path)
        state.written_paths.discard(path)
        key = self._physical_key(path)
        recorded = state.run_dirs.get(key)
        if recorded is not None:
            recorded.discard(self._physical_path(path))
            if not recorded:
                del state.run_dirs[key]

    def _hold_place(
        self, member: ArchiveMember, *, selected_later: bool = False
    ) -> int:
        """Record a result for ``member`` that the report leaves out unless the end
        of the pass gives it an outcome (``_settle_revised_members``), and return its
        index. Nothing is kept of it but the result: the end of the pass reads the
        member the reader holds. ``selected_later`` marks a member the selection
        left out, which the pass did not count as handled (``untallied``)."""
        state = self._state
        index = self._append_result(
            ExtractionResult(member, None, ExtractionStatus.SUPERSEDED, None)
        )
        state.dropped.add(index)
        state.revisable[index] = None
        if selected_later:
            state.untallied.add(index)
        return index

    def _hold_place_at(self, index: int) -> None:
        """Turn result ``index`` back into a held place with no outcome."""
        member = self._state.results[index].member
        self._set_result(
            index, ExtractionResult(member, None, ExtractionStatus.SUPERSEDED, None)
        )
        self._state.dropped.add(index)

    def _set_aside(self, path: Path) -> None:
        """Move the caller's entry at ``path`` (a file, a symlink or an empty
        directory) to a free name beside it, for a member the walk can still change
        that is about to replace it (``_MemberState.provisional``).

        Random access writes such a member only once the selection and the filter
        have seen what it is, and only then replaces what the caller had there. A
        streaming pass writes it before, so the entry it replaces is kept until the
        end of the run (``_restore_set_aside``)."""
        aside = self._temp_sibling(path.parent)
        os.rename(long_path(path), long_path(aside))
        self._state.set_aside[path] = aside

    def _restore_set_aside(self) -> None:
        """Put back each entry ``_set_aside`` moved, unless the run's final write is
        at its path, as random access would have replaced it too; then the entry is
        removed. One whose path holds something else now is left where it was moved,
        with a warning, rather than lost.

        An entry moved aside for a parent a member implied
        (``_make_room_for_parents``) is the same: the run's directory there replaced
        it, unless that directory is gone, as when its member was not selected in the
        end (``_unselect_revised_member``).

        A run that stopped before the end of its pass never settled what it wrote
        there, so that write is taken back and the caller's entry returns: whether
        the member was selected is not known. A write the filesystem refuses to take
        back leaves the entry where it was moved, with a warning."""
        state = self._state
        set_aside, state.set_aside = state.set_aside, {}
        for path, aside in set_aside.items():
            on_disk = long_path(aside)
            try:
                if not state.settled and path in state.written_paths:
                    self._remove_unsettled(path)
                elif not state.settled and path in state.created_dirs:
                    self._remove_unsettled_tree(path)
                replaced = path in state.written_paths or path in state.created_dirs
                if replaced and not any(
                    path in unremoved for unremoved in state.unremoved.values()
                ):
                    if os.path.isdir(on_disk) and not os.path.islink(on_disk):
                        os.rmdir(on_disk)
                    else:
                        os.unlink(on_disk)
                elif os.path.lexists(long_path(path)):
                    logger.warning(
                        "Could not put back %r, moved aside while the archive's "
                        "directory was read: its place is taken. It is at %r",
                        str(self._shown(path)),
                        str(self._shown(aside)),
                    )
                else:
                    os.rename(on_disk, long_path(path))
                    self._note_link_change(path)
            except OSError as exc:
                self._rebase_os_error(exc)
                logger.warning(
                    "Could not settle %r, moved aside while the archive's directory "
                    "was read: %s",
                    str(self._shown(path)),
                    exc,
                )

    def _take_back_possible_links(self) -> None:
        """Remove each file a revising pass wrote whose member the walk could still
        make a link, when the run stops before the end of the pass settled it
        (``_settle_revised_members``).

        A ZIP from a pipe yields every member that is not a directory as a ``FILE``:
        only the central directory, after the last member, says which are symlinks.
        Until then the pass writes a link's target text as a file's content. A run
        that stops before the directory never learns which, and a link left on disk
        as a file holding its target is a wrong answer (DR-1), where random access
        would have made the link, or refused it. So every such file whose data could
        be a link's is taken back: one of at most ``MAX_LINK_TARGET_BYTES`` bytes,
        the longest target a read gives, or one that starts with a Windows symlink or
        junction reparse tag, whose buffer the directory can make a link at any
        length. A longer file stays; as a link its target is over the cap, which a
        read reports as an error and extraction never creates.

        A file the filesystem refuses to remove stays, with a warning. Nothing is
        reported for it: a stopped run returns no results. The folders made for these
        members stay, unlike those ``_unselect_revised_member`` removes: the member was
        written, so a file read stopped at the same point made and kept them too.
        """
        state = self._state
        for index, handled in state.revisable.items():
            if handled is None:
                continue
            _before, written = handled
            result = state.results[index]
            path = result.path
            if (
                written is None
                or written.type is not MemberType.FILE
                or result.status is not ExtractionStatus.EXTRACTED
                or path is None
                or path not in state.written_paths
            ):
                continue
            try:
                if not self._may_be_link_data(path):
                    continue
                self._remove_unsettled(path)
            except OSError as exc:
                self._rebase_os_error(exc)
                logger.warning(
                    "Could not remove %r, written before the archive's directory "
                    "said whether it is a symlink: %s",
                    str(self._shown(path)),
                    exc,
                )

    @staticmethod
    def _may_be_link_data(path: Path) -> bool:
        """Whether the regular file at ``path`` holds what a link member's data can
        be: a target within ``MAX_LINK_TARGET_BYTES``, or a Windows reparse buffer
        for a symlink or a junction (``_take_back_possible_links``)."""
        target = long_path(path)
        info = os.lstat(target)
        if not stat.S_ISREG(info.st_mode):
            return False
        if info.st_size <= MAX_LINK_TARGET_BYTES:
            return True
        with open(target, "rb") as f:
            head = f.read(4)
        return int.from_bytes(head, "little") in (
            IO_REPARSE_TAG_MOUNT_POINT,
            IO_REPARSE_TAG_SYMLINK,
        )

    def _remove_unsettled_tree(self, path: Path) -> None:
        """``_remove_unsettled`` for what the run wrote or created at ``path`` and
        under it, deepest first. A folder that still has entries stays.

        A failure to remove ``path`` itself propagates, as one entry left under it
        makes that removal fail: the run's directory is still there, so the caller
        must not take it as replaced (``_restore_set_aside``)."""
        state = self._state
        under = {
            p
            for p in (*state.written_paths, *state.created_dirs)
            if p.is_relative_to(path) and p != path
        }
        for p in sorted(under, key=lambda p: len(p.parts), reverse=True):
            with contextlib.suppress(OSError):
                self._remove_unsettled(p)
        self._remove_unsettled(path)

    def _remove_unsettled(self, path: Path) -> None:
        """Remove what the run wrote at ``path`` before its pass was cut short (a
        file, a link or an empty folder); a folder with entries stays."""
        state = self._state
        target = long_path(path)
        if os.path.isdir(target) and not os.path.islink(target):
            os.rmdir(target)
            self._forget_removed_directory(path)
        else:
            os.unlink(target)
            self._note_link_change(path)
        state.written_paths.discard(path)

    def _reapply_revised_metadata(
        self, index: int, before: ArchiveMember, written: ArchiveMember
    ) -> None:
        """Apply to result ``index``'s entry the metadata its member gained after it
        was written (a mode, owner or time the central directory holds).

        The policy transform runs on the member as it is now, and each field it
        changes replaces the written one, unless the caller's filter had set that
        field itself: what the filter returned wins, as it does when nothing changed.
        A directory's metadata is still waiting for the end of the run, so its entry
        there is replaced instead.
        """
        state = self._state
        result = state.results[index]
        path = result.path
        if (
            result.status is not ExtractionStatus.EXTRACTED
            or path is None
            or path not in state.written_paths
        ):
            return
        transform = POLICY_TRANSFORMS[self._policy]
        was = transform(before)
        now = transform(result.member)
        changes = {
            name: getattr(now, name)
            for name in _APPLIED_METADATA
            if getattr(written, name) == getattr(was, name)
            and getattr(now, name) != getattr(was, name)
        }
        if not changes:
            return
        revised = written.replace(**changes)
        if revised.type is MemberType.DIRECTORY:
            pending = state.pending_dirs.get(self._physical_key(path), {})
            if self._physical_path(path) in pending:
                self._defer_directory_metadata(path, revised)
        elif not os.path.islink(long_path(path)):
            self._apply_metadata(path, revised)

    def _redo_revised_member(
        self,
        index: int,
        *,
        damaged: bool = False,
        missing: ArchiveyError | None = None,
        failures: list[tuple[int, ArchiveyError]] | None = None,
    ) -> None:
        """Handle result ``index``'s member again, now that the walk has changed its
        type or name, as random access would have handled it from the start.

        The filter and the policy checks run again on the changed member
        (``safe-extraction``), and whatever the pass wrote for it is taken back first,
        so a refusal leaves nothing at its path. A file whose name changed is written
        again from the bytes already on disk; a link's target comes from its data,
        which the reader kept. A member that now needs bytes the stream has passed,
        and that were not written, fails when the write reads them (``_PassedData``),
        unless it has none (an empty file): the overwrite policy decides first, as it
        does in random access, so a member it would not write does not fail. That is
        also how a member the pass left out (a held place) is handled: its bytes were
        not written, so it can be a link, a directory or an empty file, and fails
        otherwise. Results other than ``EXTRACTED`` and ``BLOCKED`` stand: nothing was
        written for them that the change could alter.

        ``damaged``: the walk changed the member because the archive contradicts
        itself, so a failure for passed bytes is ``CorruptionError``. ``missing``: the
        member's data was never in the stream (``_settle_missing_member``), and reading
        it raises this, whatever its size. ``failures``:
        a failure is appended there for the caller to stop on, not stopped on here.
        """
        state = self._state
        result = state.results[index]
        member = result.member
        held_place = index in state.dropped
        if not held_place and result.status not in (
            ExtractionStatus.EXTRACTED,
            ExtractionStatus.BLOCKED,
        ):
            return
        current = self._current = _MemberState()
        written = result.status is ExtractionStatus.EXTRACTED
        old_path = result.path if written else None
        held: BinaryIO | None = None
        member_started = False
        presented_name: str | None = None
        rewrites: frozenset[NameRewrite] = frozenset()
        link_error: ArchiveyError | None = None
        try:
            # A BLOCKED result wrote nothing, and is released all the same: what it
            # was charged, if anything, is refunded rather than charged twice.
            self._take_back_written(index)
            transformed, presented_name, rewrites = self._transform(member)
            if transformed is None:
                # Not tallied, then dropped from the report.
                self._hold_place_at(index)
            elif held_place and not member.is_current:
                state.dropped.discard(index)
            else:
                state.dropped.discard(index)
                if transformed.type is MemberType.FILE:
                    if missing is not None:
                        held = _passed_data(missing)
                    elif old_path is not None and old_path in current.parked:
                        held = self._open_parked_copy(old_path)
                        current.recopy = True
                    elif member.size == 0:
                        held = io.BytesIO()
                    elif damaged:
                        held = _passed_data(
                            CorruptionError(
                                f"The data of {quoted(member.name)} was passed before "
                                "the archive's directory made it the current copy of "
                                "its name, and was not written",
                                member_name=member.name,
                            )
                        )
                    else:
                        held = _passed_data(
                            ExtractionError(
                                f"The data of {quoted(member.name)} was passed before "
                                "the archive's directory changed the member, and was "
                                "not written",
                                member_name=member.name,
                            )
                        )
                if not member.is_anti:
                    state.tracker.start_member(member)
                    member_started = True
                self._set_result(
                    index, ExtractionResult(member, None, ExtractionStatus.FAILED, None)
                )
                self._set_result(
                    index, self._write_member(member, transformed, held, index)
                )
        except (_AlwaysStopResourceLimitError, DiagnosticRaisedError):
            raise
        except (ArchiveyError, OSError) as exc:
            spelled_from = current.spelled_from
            if isinstance(exc, ArchiveyError) and spelled_from is not None:
                _report_stored_spelling(exc, disk_spelled(spelled_from), spelled_from)
            error, status = self._classify(exc, member.name)
            state.dropped.discard(index)
            self._set_result(
                index,
                ExtractionResult(
                    member,
                    None,
                    status,
                    error,
                    requested_path=current.requested_path,
                    collided_with=current.collided_with,
                ),
            )
            if (
                failures is not None
                and status is ExtractionStatus.FAILED
                and isinstance(error, ArchiveyError)
            ):
                failures.append((index, error))
            else:
                self._stop_or_log(
                    exc, error, status, kind=member.type.value, name=member.name
                )
        finally:
            self._close(held)
            if current.recopy_from is not None:
                self._remove_recopy_source(current.recopy_from, member.name)
            if current.parked:
                self._drop_parked_copies(member.name, index)
            link_error = self._recheck_links()
        if written and old_path is not None and old_path not in state.written_paths:
            # What an EXTRACTED result wrote is gone (a parked file only now): the
            # folders made for it alone go too, while empty. Random access makes
            # them only if the member is written there, and then they stay.
            self._remove_created_parents(old_path)
        if link_error is not None:
            raise link_error
        if member_started:
            state.counted[index] = state.tracker.member_bytes
        if presented_name is not None:
            self._set_result(
                index,
                replace(
                    state.results[index],
                    presented_name=presented_name,
                    rewrites=rewrites,
                ),
            )

    def _is_callers_entry(self, path: Path, st: os.stat_result) -> bool:
        """Whether the entry at ``path`` (``st`` its ``lstat``) was there before this
        run, rather than written or created by it."""
        state = self._state
        if path in state.written_paths or path in state.created_dirs:
            return False
        return not (stat.S_ISDIR(st.st_mode) and self._is_run_directory(path, st))

    def _occupied(self, path: Path) -> bool:
        """Whether ``path`` holds an entry, not counting a parked superseded copy."""
        return path not in self._current.parked and os.path.lexists(long_path(path))

    # --- selection / transform -----------------------------------------------------

    def _as_written(
        self, original: ArchiveMember, transformed: ArchiveMember
    ) -> ArchiveMember:
        """``transformed``, or the SYMLINK it is written as when ``original`` is a hard
        link to a symlink member, through any number of hard links.

        A hard link to a symlink is a second name for the symlink itself, which is what
        GNU tar creates. So the member is written as a symlink with the same target, read
        from its own directory, and goes through the same checks as any symlink.
        Following the chain instead looked the target up by member name, which fails
        when the symlink's target passes through another symlinked directory. It runs
        after the caller's filter, which sees the HARDLINK the archive lists, and only
        when the filter kept it a HARDLINK.
        """
        if (
            original.type is not MemberType.HARDLINK
            or transformed.type is not MemberType.HARDLINK
            or self._state.reader is None
        ):
            return transformed
        direct = self._hardlink_chain_end(self._state.reader, original)
        if (
            direct is None
            or direct.type is not MemberType.SYMLINK
            or direct.link_target is None
        ):
            return transformed
        return transformed.replace(
            type=MemberType.SYMLINK,
            link_target=direct.link_target,
            link_target_member=direct.link_target_member,
        )

    def _hardlink_chain_end(
        self, reader: BaseArchiveReader, member: ArchiveMember
    ) -> ArchiveMember | None:
        """The first member that is not a HARDLINK on ``member``'s hard-link chain, or
        ``None`` for a cycle or a dead end.

        Memoized by ``_member_id`` for every link on the walked path, as
        ``BaseArchiveReader._resolve_link`` does, so a chain of N hard links costs O(N)
        lookups in total rather than O(N²). A lookup depends only on the node it starts
        at, so the links on one path share its end, and no answer changes during the
        run: ``_hardlink_direct_target`` only looks backward.
        """
        ends = self._state.hardlink_ends
        path: list[int] = []
        on_path: set[int] = set()
        end: ArchiveMember | None
        current = member
        while True:
            if current.type is not MemberType.HARDLINK:
                end = current
                break
            member_id = current._member_id
            if member_id is None:
                end = None
                break
            if member_id in ends:
                end = ends[member_id]
                break
            if member_id in on_path:
                end = None
                break
            path.append(member_id)
            on_path.add(member_id)
            target = reader._hardlink_direct_target(current)
            if target is None:
                end = None
                break
            current = target
        for member_id in path:
            ends[member_id] = end
        return end

    def _transform(
        self, original: ArchiveMember
    ) -> tuple[ArchiveMember | None, str | None, frozenset[NameRewrite]]:
        """Policy transform and user filter on a transient copy, then the universal check
        on the result.

        Returns ``(member_to_write, presented_name, rewrites)`` — the member is ``None``
        if the user filter skipped it, ``presented_name`` is the full name before a
        safety rewrite (the absolute-name re-root or the portable-name policy) when one
        reaches disk, else ``None``, and ``rewrites`` names each safety rewrite that
        reached disk (empty exactly when ``presented_name`` is ``None``). Raises a
        ``FilterRejectionError`` on a universal violation."""
        transformed = POLICY_TRANSFORMS[self._policy](original)
        # The re-root comes before the filter so the filter sees the name that would be
        # written, as it already sees the policy's permission changes, and a filter
        # need not strip roots itself under STANDARD or TRUSTED. Whether the re-root
        # counts as a rewrite is decided after the filter, like the portable one: a
        # member the filter drops or renames never reaches disk under the re-rooted name.
        rerooted_from: str | None = None
        if self._policy is not ExtractionPolicy.STRICT:
            rerooted = reroot_absolute(transformed)
            if rerooted.name != transformed.name:
                rerooted_from = transformed.name
            transformed = rerooted
        rerooted_name = transformed.name
        # The filter runs before the universal check, so it sees every member, the
        # unsafe ones included, and can rename one to something safe. Whatever it
        # returns is what gets checked and written.
        if self._filter is not None:
            # The filter is told, as a predicate is, when the walk can still change
            # the member (``member_state_final``): it may see the member again.
            transformed._listing_pending = original._listing_pending
            filtered = self._filter(transformed)
            if filtered is None:
                return None, None, frozenset()
            if not isinstance(filtered, ArchiveMember):
                # A caller bug, so a TypeError that ends the call rather than a
                # per-member result; it names what came back, which the attribute
                # error from the first use of it did not.
                raise _UsageTypeError(
                    f"filter= must return an ArchiveMember or None, not "
                    f"{type(filtered).__name__} (for member {quoted(original.name)})"
                )
            transformed = filtered
            if transformed.name != rerooted_name:
                rerooted_from = None  # the filter chose this name; it is not a rewrite
        transformed = self._as_written(original, transformed)
        # Read after `_as_written`: a hard link to a symlink is now the SYMLINK it is
        # a second name for, which copies no bytes and gets the symlink checks below.
        kept_hardlink = (
            original.type is MemberType.HARDLINK
            and transformed.type is MemberType.HARDLINK
        )
        dest_root = self._state.dest_root
        # The checks run on the name that reaches disk; the name policy below runs on
        # the stored one, so its escape of a lone surrogate is the same on every OS.
        self._check_universal(transformed, dest_root)
        if kept_hardlink and self._source_refused(original):
            raise FilterRejectionError(
                "Hardlink target was refused",
                member_name=original.name,
                link_target=original.link_target,
            )
        reader = self._state.reader
        if reader is not None and self._needs_target_read(original, transformed):
            # A symlink whose target the format keeps in member data and nothing has
            # read yet (`read_link_targets=False`), or one whose read failed and was
            # reported as damaged, which leaves it unresolved: this read raises the
            # fault again and fails the member. The selector and the filter have now
            # both accepted it, with `link_target=None`, so reading it here is the
            # read the caller asked for (`archive-reading`, "Link targets stored as
            # member data are read only when configured").
            reader._read_link_target_on_request(original)
            if original.type is not MemberType.SYMLINK:
                # The data showed the member is not a link: a reparse-flagged member
                # whose data is no reparse buffer, re-typed to its fallback as listing
                # would have. Everything above decided on a member that did not exist,
                # so decide again on the real one, the filter included.
                self._current.retyped = True
                return self._transform(original)
            if original.link_target is not None:
                # The filter decided on a link with no target. Decide again with the
                # target, so a filter that rewrites targets (`sanitize_names`) sees
                # it, and the link gets the outcome it gets when listing read the
                # target. The target is now set, so this runs once.
                return self._transform(original)
        # Portable-name policy on the FINAL name — after the user filter, so a filter rename
        # is checked too, and TRUSTED keeps faithful bytes. Reserved names / ':' are
        # rejected; a trailing dot/space (STRICT) or non-representable byte is rewritten to a
        # portable spelling, recorded on the result so the rename is not silent.
        if rerooted_from is not None and AbortOn.NAME_SANITIZED in self._abort_on:
            raise _AbortExtraction(
                NameRewrittenError(
                    f"Absolute name re-rooted: {quoted(rerooted_from)} -> "
                    f"{quoted(transformed.name)}",
                    member_name=original.name,
                )
            )
        portable = apply_name_policy(transformed, self._policy)
        if portable is not transformed:
            # The rewrite can change which directories the path passes through: a
            # "\\" becomes a separator, and "foo. /x" becomes "foo/x". The parent
            # checked above is then not the parent written to, so check again. A
            # symlink "foo" that leaves the destination is refused here.
            try:
                self._check_universal(portable, dest_root)
            except ExtractionError as exc:
                _report_stored_spelling(exc, portable, transformed)
                raise
        on_disk = disk_spelled(portable)
        if on_disk is not portable:
            self._current.spelled_from = portable
        reroot_rewrite = (
            frozenset({NameRewrite.REROOTED})
            if rerooted_from is not None
            else frozenset()
        )
        if portable.name == transformed.name:
            return on_disk, rerooted_from, reroot_rewrite
        # The pre-rewrite spelling is the caller filter's output when there is one, which
        # is why it cannot be reconstructed from ``member.name`` and ``path`` alone.
        if AbortOn.NAME_SANITIZED in self._abort_on:
            raise _AbortExtraction(
                NameRewrittenError(
                    f"Name rewritten for portability: {quoted(transformed.name)} -> "
                    f"{quoted(portable.name)}",
                    member_name=original.name,
                )
            )
        # After a re-root, the stored name is the one the caller will recognise.
        return (
            on_disk,
            rerooted_from or transformed.name,
            reroot_rewrite | {NameRewrite.PORTABLE_NAME},
        )

    def _source_refused(self, link: ArchiveMember) -> bool:
        """Whether the member a HARDLINK gets its bytes from was refused, so the link
        is refused too.

        A hard link's target string is a member name and never becomes a path, so it
        gets no path check of its own (``check_universal``). What it can do is put a
        refused member's content on disk under the link's name: the second pass
        writes an unwritten source's bytes at the link's path. So a link is refused
        when its source is, in both access modes and at every policy.

        The source is ``link_target_member``, the end of the link chain (symlinks
        included), which is the member ``_write_hardlink`` links against or the second
        pass reads. A refused link in the middle of the chain does not refuse this
        one: its name was the unsafe part, and this link does not use that name. The
        chain ends at a SYMLINK only when that symlink has no target, and then
        nothing is refused: the link copies no bytes, and ``_write_hardlink`` fails it
        as a link to a non-file whatever happened to the symlink's own name. (A
        symlink with a target is not a source: ``_as_written`` wrote the link as that
        symlink.) Hard links point only backward, so this run has already reached the
        source. When it recorded a result for it, the result decides: ``BLOCKED`` is
        a refusal, and anything else passed the checks, under the name the caller's
        filter gave it. A filter that renames a source to an unsafe name thus refuses
        its links too, and one that rescues an unsafe source lets them through. A
        source with no result was excluded by the selector or the filter. The
        caller's filter is not run on it, since a selector-excluded member was never
        meant to reach it; instead the policy's own steps run on the source as listed
        (``_policy_refuses``). A refused source's links are refused; any other
        excluded source is recovered by the second pass, or fails the link on a
        forward-only stream (``_write_hardlink``).
        """
        state = self._state
        source = link.link_target_member
        if state.reader is None or source is None or source._member_id is None:
            # No source: `_write_hardlink` fails the link as not found.
            return False
        if source.type is MemberType.SYMLINK:
            # A targetless symlink: no bytes to refuse.
            return False
        member_id = source._member_id
        index = state.result_ids.get(member_id)
        if index is not None:
            return state.results[index].status is ExtractionStatus.BLOCKED
        refused = state.refusals.get(member_id)
        if refused is None:
            # Memoized, so N links to one excluded source run its checks once.
            refused = state.refusals[member_id] = self._policy_refuses(source)
        return refused

    def _policy_refuses(self, member: ArchiveMember) -> bool:
        """Whether this run's policy would refuse ``member`` as listed, without the
        caller's filter: the absolute-name re-root, the universal checks and the name
        policy, as ``_transform`` runs them. Only a ``FilterRejectionError`` is a
        refusal; a member whose parent cannot be resolved has failed, not been refused.
        ``member`` is the end of a link chain and not a link itself, which
        ``_as_written`` never rewrites, so this skips that step."""
        candidate = member
        if self._policy is not ExtractionPolicy.STRICT:
            candidate = reroot_absolute(candidate)
        try:
            check_universal(
                disk_spelled(candidate),
                self._state.dest_root,
                link_target_on_disk=self._on_disk,
            )
            apply_name_policy(candidate, self._policy)
        except FilterRejectionError:
            return True
        except ExtractionError:
            return False
        return False

    def _check_universal(self, member: ArchiveMember, dest_root: Path) -> None:
        """``check_universal`` on the disk spelling, reporting the stored names."""
        on_disk = disk_spelled(member)
        try:
            check_universal(on_disk, dest_root, link_target_on_disk=self._on_disk)
        except ExtractionError as exc:
            if on_disk is not member:
                _report_stored_spelling(exc, on_disk, member)
            raise

    @staticmethod
    def _needs_target_read(original: ArchiveMember, transformed: ArchiveMember) -> bool:
        """Whether a link about to be written still needs its target read.

        Only a current symlink the caller's filter left targetless and whose target
        nobody has looked for yet: a superseded member is not written, and a target the
        filter supplied is the one to write.
        """
        return (
            original.type is MemberType.SYMLINK
            and original.is_current
            and original.link_target is None
            and not original._link_target_resolved
            and transformed.link_target is None
        )

    # --- per-member write ----------------------------------------------------------

    def _write_member(
        self,
        original: ArchiveMember,
        transformed: ArchiveMember,
        stream: BinaryIO | None,
        result_index: int,
    ) -> ExtractionResult:
        requested = self._state.dest / transformed.name
        self._current.requested_path = requested
        target = self._follow_renamed_dirs(
            requested, is_dir=transformed.type == MemberType.DIRECTORY
        )

        if original.is_anti:
            return replace(
                self._apply_anti_item(original, target), requested_path=requested
            )

        dest_path, prior, collided_with = self._resolve_collision(
            original, transformed, target
        )
        # Stashed for the failure handler: an ERROR-policy collision becomes a FAILED
        # result built there, and it has to carry the collision too.
        self._current.collided_with = collided_with

        try:
            result = self._dispatch_write(
                original, transformed, stream, dest_path, result_index
            )
        except (ArchiveyError, OSError):
            # The write failed *after* clearing the destination to make room for it (only
            # reachable on the non-atomic DIR/SYMLINK/HARDLINK paths). The earlier
            # member's content is gone either way, so its result must stop advertising a
            # live path — reporting EXTRACTED for bytes that no longer exist is exactly
            # the defect this change exists to remove. This member's own FAILED result is
            # recorded by the caller's handler.
            if self._current.removed_existing:
                self._mark_overwritten(prior)
            raise

        return self._settle_placement(
            result,
            requested=requested,
            target=target,
            prior=prior,
            collided_with=collided_with,
            transformed=transformed,
            result_index=result_index,
        )

    def _settle_placement(
        self,
        result: ExtractionResult,
        *,
        requested: Path,
        target: Path,
        prior: _Claim | None,
        collided_with: Path | None,
        transformed: ArchiveMember,
        result_index: int,
    ) -> ExtractionResult:
        """``result`` with its collision recorded, once the claims and earlier results
        it affects are updated. Both passes; the caller records what it returns.

        ``requested`` is the path the member asked for, ``target`` the path collision
        resolution started from: ``requested`` moved under a renamed directory
        (``_follow_renamed_dirs``), or ``requested`` itself."""
        # Record the intended destination. A REPLACE merge into a prior path is not a
        # rename, so it reports the actual (merged) path; every other outcome reports the
        # member's own intended destination, so RENAME shows up as requested_path != path.
        if prior is not None and result.status is ExtractionStatus.EXTRACTED:
            result = replace(
                result, requested_path=result.path, collided_with=collided_with
            )
        else:
            result = replace(
                result, requested_path=requested, collided_with=collided_with
            )

        # A REPLACE that actually landed leaves the earlier member's content gone. Revise
        # that member's already-recorded result to OVERWRITTEN so the merge is visible in
        # ``results`` instead of two members both reporting EXTRACTED at one path.
        if result.status is ExtractionStatus.EXTRACTED:
            self._mark_overwritten(prior)
            if transformed.type == MemberType.DIRECTORY and result.path is not None:
                self._state.written_dirs.setdefault(
                    self._collision_key(result.path), []
                ).append(result_index)
                # Written under a derived name rather than merged into a prior path:
                # members inside it follow it there.
                if prior is None and result.path != target:
                    self._state.renamed_dirs[requested] = result.path

        self._register_collision_key(transformed, result, result_index)
        return result

    def _dispatch_write(
        self,
        original: ArchiveMember,
        transformed: ArchiveMember,
        stream: BinaryIO | None,
        dest_path: Path,
        result_index: int,
    ) -> ExtractionResult:
        written_paths = self._state.written_paths
        if transformed.type == MemberType.DIRECTORY:
            existed = self._occupied(dest_path)
            callers = self._is_callers_directory(dest_path)
            if not self._make_room_for_parents(
                transformed, dest_path
            ) or not self._prepare_destination(transformed, dest_path):
                return ExtractionResult(
                    original, None, ExtractionStatus.NOT_OVERWRITTEN, None
                )
            try:
                self._makedirs(dest_path, transformed)
            except FileExistsError as exc:
                # A non-directory took the name after the existence check, as for the
                # other landings (``_taken_after_check``). Only this call: a parent
                # that turned into a file is not this member's name being taken.
                if not self._taken_after_check(dest_path, atomic=False):
                    raise
                raise self._already_exists(dest_path, transformed) from exc
            kept_mode: int | None = None
            if callers:
                # A directory the caller already had, the destination root included
                # (a `./` member): it keeps its own mode and times. Applying the
                # member's would widen a private 0o700 directory to the policy's
                # 0o755, or to a world-writable 0o777 under STANDARD. GNU tar has
                # the same rule as --no-overwrite-dir.
                wanted = self._effective_mode(transformed)
                current = stat.S_IMODE(os.stat(long_path(dest_path)).st_mode)
                if wanted is not None and _dir_mode_as_stored(wanted) != current:
                    kept_mode = current
            else:
                self._defer_directory_metadata(dest_path, transformed)
            if not existed:
                written_paths.add(dest_path)
                self._note_run_directory(dest_path)
            return ExtractionResult(
                original,
                dest_path,
                ExtractionStatus.EXTRACTED,
                None,
                kept_mode=kept_mode,
            )

        if transformed.type == MemberType.SYMLINK:
            result = self._write_symlink(original, transformed, dest_path)
        elif transformed.type == MemberType.HARDLINK:
            result = self._write_hardlink(
                original, transformed, dest_path, result_index
            )
        elif transformed.type == MemberType.FILE:
            result = self._write_file(original, transformed, stream, dest_path)
        else:
            # MemberType.OTHER is rejected by check_universal; nothing else should
            # reach here.
            raise ExtractionError(
                f"Unsupported member type {transformed.type!r}",
                member_name=transformed.name,
            )
        if result.status is ExtractionStatus.EXTRACTED and result.path is not None:
            written_paths.add(result.path)
            links = self._state.links
            if (
                transformed.type == MemberType.SYMLINK
                and links is not None
                and transformed.link_target is not None
            ):
                on_disk = self._link_target_on_disk(transformed.link_target)
                links.track(result.path, on_disk, result_index)
                if on_disk != transformed.link_target:
                    # An error names the target as stored, not with the separators
                    # or dry-run path written to disk.
                    self._state.stored_targets[result_index] = transformed.link_target
                spelled_from = self._current.spelled_from
                if spelled_from is not None and spelled_from.link_target is not None:
                    self._state.stored_targets[result_index] = spelled_from.link_target
        return result

    def _make_room(
        self,
        original: ArchiveMember,
        transformed: ArchiveMember,
        dest_path: Path,
        *,
        atomic: bool,
    ) -> ExtractionResult | None:
        """Apply the OverwritePolicy at ``dest_path`` and create its parents; the
        result if the policy declines, else ``None``. A check that skips the member
        comes first, so it cannot remove an entry under ``OverwritePolicy.REPLACE``.
        A parent the caller holds as a non-directory is handled first, as an explicit
        directory entry there would be (``_make_room_for_parents``). That keeps the
        rule across both calls: the parent's own skip check runs before it removes
        anything, and once it has replaced the parent nothing is left at
        ``dest_path`` for ``_prepare_destination`` to skip."""
        if not self._make_room_for_parents(
            transformed, dest_path
        ) or not self._prepare_destination(transformed, dest_path, atomic=atomic):
            return ExtractionResult(
                original, None, ExtractionStatus.NOT_OVERWRITTEN, None
            )
        self._makedirs(dest_path.parent, transformed)
        return None

    def _resolve_collision(
        self,
        original: ArchiveMember,
        transformed: ArchiveMember,
        requested: Path,
    ) -> tuple[Path, _Claim | None, Path | None]:
        """Resolve an O2 name collision, returning
        ``(dest_path, redirected_from, collided_with)``.

        ``redirected_from`` is the prior claim when this member was routed onto an
        already-written destination (so the caller can revise that member's result), and
        ``None`` otherwise — including under RENAME, which writes somewhere fresh.

        ``collided_with`` is the prior path whenever a collision *event* occurred, which
        is the wider set: RENAME collides and then avoids the destination, so it reports
        a path here while reporting no redirection. It is set under exactly the condition
        the removed ``EXTRACTION_NAME_COLLISION`` diagnostic fired on — a claim held by
        this run, outside TRUSTED — so the result carries the fact the diagnostic used to
        carry. A pre-existing on-disk obstacle is not a collision event and reports
        ``None``, as the diagnostic was also silent for it.

        Directories are structural (parents recur, entries merge) and are never claimed
        in the map, so a directory member landing on a real directory merges into it.
        One landing on an entry that is not a real directory (a file or a symlink) goes
        through the same resolution as any other member: under every policy it collides
        with a claim this run holds there, so ``collided_with``, the abort and a REPLACE
        revision of the earlier member work as they do for files. Under RENAME it gets a
        ``name (N)`` too, as removing that entry would lose it, and the members inside it
        follow it there (``_follow_renamed_dirs``). For a tracked member whose key is
        already claimed THIS run, a collision is deterministic on every OS: route
        ERROR/SKIP/REPLACE to the prior path so the existing OverwritePolicy machinery
        handles it uniformly, or derive a fresh ``name (N)`` under RENAME. TRUSTED keys
        on the exact name and defers to the local OS (no collision *event*, but RENAME
        still uses the map to avoid re-deriving names). Shared by the main pass and the
        orphan second pass so deferred hardlinks honour the map too.

        Under RENAME, a parent of ``requested`` that the caller holds as a file or a
        symlink first moves ``requested`` under a derived name for that parent
        (``_rename_callers_parent``).
        """
        if self._overwrite is OverwritePolicy.RENAME:
            requested = self._rename_callers_parent(requested)
        if transformed.type == MemberType.DIRECTORY and not self._blocks_directory(
            requested
        ):
            return requested, None, None
        prior = self._state.collision_map.get(self._collision_key(requested))
        if prior is None and self._policy is not ExtractionPolicy.TRUSTED:
            # TRUSTED reports no collision event (``collided`` below), so an alias
            # found there would change nothing; the guard saves its ``lstat``.
            prior = self._alias_claim(requested)
        collided = (
            prior.path
            if prior is not None and self._policy is not ExtractionPolicy.TRUSTED
            else None
        )
        if collided is not None:
            assert prior is not None
            self._check_collision_abort(original, transformed, prior)
        if self._overwrite is OverwritePolicy.RENAME:
            if prior is not None or self._occupied(requested):
                is_dir = transformed.type == MemberType.DIRECTORY
                free = self._derive_free_name(requested, is_dir=is_dir)
                return free, None, collided
            return requested, None, None
        if collided is not None:
            assert prior is not None
            return prior.physical, prior, collided
        return requested, None, None

    def _callers_parent(self, path: Path) -> Path | None:
        """The parent of ``path`` under the destination that the caller holds as an
        entry that is not a directory (a file, or a symlink that does not resolve to
        a directory), or ``None``. Only the nearest to the destination can exist: the
        directories below it are not there yet.

        The member implies that parent as a directory, and it meets OverwritePolicy as
        an explicit directory entry would (``_make_room_for_parents``). One the run
        wrote is not the caller's: ``_makedirs`` fails the member with an error that
        names it."""
        dest = self._state.dest
        parent = path.parent
        if (
            parent == dest
            or parent in self._state.created_dirs
            or os.path.isdir(long_path(parent))
        ):
            # The common case: a directory this run made (``created_dirs`` drops
            # one the run removes), else one stat.
            return None
        try:
            parts = parent.relative_to(dest).parts
        except ValueError:
            return None
        probe = dest
        for part in parts:
            probe = probe / part
            try:
                st = os.lstat(long_path(probe))
            except (OSError, ValueError):
                return None
            if stat.S_ISDIR(st.st_mode) or (
                stat.S_ISLNK(st.st_mode) and os.path.isdir(long_path(probe))
            ):
                continue
            if probe in self._current.parked or not self._is_callers_entry(probe, st):
                return None
            return probe
        return None

    def _rename_callers_parent(self, requested: Path) -> Path:
        """``requested`` moved under a derived name for its parent that the caller
        holds as a non-directory (``_callers_parent``), as RENAME writes an explicit
        directory entry there: ``foo/x`` -> ``foo (1)/x``. The later members inside
        it follow it (``_follow_renamed_dirs``), so the name is derived once."""
        blocked = self._callers_parent(requested)
        if blocked is None:
            return requested
        renamed_dirs = self._state.renamed_dirs
        renamed = renamed_dirs.get(blocked)
        if renamed is None:
            renamed = renamed_dirs[blocked] = self._derive_free_name(
                blocked, is_dir=True
            )
        return renamed / requested.relative_to(blocked)

    def _make_room_for_parents(self, member: ArchiveMember, path: Path) -> bool:
        """Apply OverwritePolicy to a parent of ``path`` that the caller holds as a
        non-directory (``_callers_parent``), as for an explicit directory entry
        there: ERROR raises, SKIP returns False, REPLACE removes the entry and returns
        True. RENAME moved ``path`` away from it already
        (``_rename_callers_parent``)."""
        blocked = self._callers_parent(path)
        if blocked is None:
            return True
        return self._prepare_destination(member, blocked)

    def _blocks_directory(self, path: Path) -> bool:
        """Whether ``path`` holds an entry that is not a real directory: a file or a
        symlink (to a directory too, as it is never written through), which a directory
        member cannot merge into."""
        if not self._occupied(path):
            return False
        try:
            return not stat.S_ISDIR(os.lstat(long_path(path)).st_mode)
        except (OSError, ValueError):
            return False

    def _follow_renamed_dirs(self, requested: Path, *, is_dir: bool) -> Path:
        """``requested`` moved under the derived name of the nearest directory RENAME
        wrote elsewhere (``dd/f`` -> ``dd (1)/f``), or unchanged when none contains it.

        Only a directory member also matches itself: a second ``dd/`` merges into
        ``dd (1)/`` as directories always merge, which a streaming pass needs because
        the first copy is still in place, superseded, when the second arrives. A file
        that only shares the renamed directory's name is not inside it, and resolves
        its own collision from the name it asked for (``dd (2)``, not ``dd (1) (1)``)."""
        renamed_dirs = self._state.renamed_dirs
        if not renamed_dirs:
            return requested
        candidates = (requested, *requested.parents) if is_dir else requested.parents
        for ancestor in candidates:
            renamed = renamed_dirs.get(ancestor)
            if renamed is not None:
                return renamed / requested.relative_to(ancestor)
        return requested

    def _stops_on_failure(self) -> bool:
        """Whether ``OnError`` halts on a member failure.

        Exhaustive on purpose: an ``OnError`` member this does not name raises instead
        of being treated as ``CONTINUE``, which would swallow its failures.
        """
        if self._on_error is OnError.STOP:
            return True
        if self._on_error is OnError.CONTINUE:
            return False
        assert_never(self._on_error)

    def _classify(
        self, exc: ArchiveyError | OSError, member_name: str
    ) -> tuple[ArchiveyError | OSError, ExtractionStatus]:
        """The error to record for a member's failed work, and its status.

        EILSEQ and ENAMETOOLONG (and their Windows codes) come from the archive's name,
        not the filesystem's state, so they become typed failures, as does Windows
        refusing a symlink for want of privilege: see ``_typed_os_error``.
        """
        # Typed first: whether a path was too long is read from the path the call took.
        error = _typed_os_error(exc, member_name)
        if isinstance(exc, OSError):
            # Before it is recorded or logged: a dry run's error names dest. The typed
            # error keeps ``exc`` as its cause, so this reaches that too.
            self._rebase_os_error(exc)
        if isinstance(error, FilterRejectionError):
            return error, ExtractionStatus.BLOCKED
        return error, ExtractionStatus.FAILED

    def _stop_or_log(
        self,
        exc: ArchiveyError | OSError,
        error: ArchiveyError | OSError,
        status: ExtractionStatus,
        *,
        kind: str,
        name: str,
    ) -> None:
        """Raise ``error`` (``exc`` as caught) if the run stops on it, else log it."""
        # OnError governs failures only. A BLOCKED stops the run only under the
        # fail-closed AbortOn.BLOCKED_MEMBER opt-in, under either OnError value; its
        # recorded result is discarded with the rest of the report.
        if (self._stops_on_failure() and status is ExtractionStatus.FAILED) or (
            status is ExtractionStatus.BLOCKED
            and AbortOn.BLOCKED_MEMBER in self._abort_on
        ):
            if error is exc:
                raise error
            raise error from exc
        # No diagnostic: the recorded result is the whole record of this outcome (the
        # placement clause in ``diagnostics``).
        logger.warning("Skipping %s %r: %s", kind, name, error)

    def _check_collision_abort(
        self, original: ArchiveMember, transformed: ArchiveMember, prior: _Claim
    ) -> None:
        """Abort on a collision event if the caller asked to be stopped by one.

        Fires on **every** non-TRUSTED collision whatever resolution follows — replaced,
        skipped, errored or renamed — because the trigger is the collision itself, not its
        outcome. Without the opt-in a collision is not an error: OverwritePolicy resolves
        it and both members' results record what happened.
        """
        if AbortOn.NAME_COLLISION not in self._abort_on:
            return
        raise _AbortExtraction(
            NameCollisionError(
                f"Name collision with already-written "
                f"{display_path(self._shown(prior.path))}",
                member_name=original.name,
            )
        )

    def _register_collision_key(
        self, transformed: ArchiveMember, result: ExtractionResult, result_index: int
    ) -> None:
        """Claim the key for an actually-written non-directory member so later members (in
        this pass and the orphan second pass) collide against it.

        The claim carries ``result_index`` so a later REPLACE onto this destination can
        revise this member's result to OVERWRITTEN. Re-claiming a key transfers it: after
        a replacement, a third member colliding revises the second, not the first."""
        if (
            transformed.type != MemberType.DIRECTORY
            and result.status is ExtractionStatus.EXTRACTED
            and result.path is not None
        ):
            self._claim(result.path, result_index)

    def _rel_name(self, path: Path) -> str:
        """``path`` relative to the extraction root, as written, for messages."""
        return path.relative_to(self._state.dest).as_posix()

    def _claim(self, path: Path, index: int) -> None:
        """Claim ``path`` for result ``index``, keyed on where it physically is now.

        The key is remembered per path, so releasing the claim later finds it even after
        a symlink in ``path`` was repointed and ``path`` resolves somewhere else."""
        key = self._collision_key(path)
        self._state.collision_map[key] = _Claim(path, index, self._physical_path(path))
        self._state.claim_keys[path] = key
        self._state.unindexed_claims.append(path)

    def _alias_claim(self, path: Path) -> _Claim | None:
        """The claim on the file at ``path`` when ``path`` is another name the
        filesystem gives a file this run wrote under its own name; else ``None``.

        An 8.3 short name (``LONGFI~1.TXT`` for ``longfilename.txt``) is such a name on
        Windows, on a volume that makes them; so is a spelling that NTFS's upcase table
        folds and ``casefold`` does not. The collision key does not see it, so without
        this the member would meet the file as one already there: refused under
        ``ERROR``, and under ``REPLACE`` written over the earlier member while that
        member's result still says ``EXTRACTED``. Matched by identity
        (``st_dev``, ``st_ino``) with the files this run claimed, so it goes through
        the same collision handling as a case variant does. A filesystem that reports
        inode 0 cannot be matched, and an entry there stays one already there.

        Only a name that holds an entry and has no claim of its own gets here, so a
        run into an empty destination costs nothing. Each claimed path is looked at
        once, on the first lookup after it was claimed; a hit is checked against the
        claimed path again, as that path may hold a newer file by now.
        """
        if not self._occupied(path):
            return None
        try:
            st = os.lstat(long_path(path))
        except OSError:
            return None
        if st.st_ino == 0:
            return None
        state = self._state
        for claimed in state.unindexed_claims:
            with contextlib.suppress(OSError):
                cst = os.lstat(long_path(claimed))
                state.claim_ids[cst.st_dev, cst.st_ino] = claimed
        state.unindexed_claims.clear()
        claimed = state.claim_ids.get((st.st_dev, st.st_ino))
        if claimed is None or claimed == path:
            return None
        claim = state.collision_map.get(self._claimed_key(claimed))
        if claim is None or claim.path != claimed:
            return None
        try:
            if not os.path.samestat(st, os.lstat(long_path(claimed))):
                return None
        except OSError:
            return None
        return claim

    def _claimed_key(self, path: Path) -> str:
        """The key ``path`` was claimed under, or its current key if it was not."""
        key = self._state.claim_keys.get(path)
        return key if key is not None else self._collision_key(path)

    def _physical_rel(self, path: Path) -> str | None:
        """Where ``path`` is, relative to the destination root and ``/``-separated, with
        its parent resolved; ``None`` when that parent does not resolve inside the root.

        It runs several times per member, so it works on strings and caches each
        parent's answer until a symlink changes (``_resolutions_changed``)."""
        parent, name = os.path.split(os.fspath(path))
        resolved_parents = self._state.resolved_parents
        rel_parent = resolved_parents.get(parent)
        if rel_parent is None:
            root = os.fspath(self._state.dest_root)
            try:
                resolved = os.fspath(resolve_or_raise_on_loop(Path(parent)))
            except OSError:
                return None
            prefix = root if root.endswith(os.sep) else root + os.sep
            if os.path.normcase(resolved) == os.path.normcase(root):
                rel_parent = ""
            elif os.path.normcase(resolved).startswith(os.path.normcase(prefix)):
                rel_parent = resolved[len(prefix) :].replace(os.sep, "/") + "/"
            else:
                return None
            resolved_parents[parent] = rel_parent
        return rel_parent + name

    def _physical_path(self, path: Path) -> Path:
        """Where ``path`` is, under the destination as given, with its parent
        resolved (``_physical_rel``); ``path`` itself when that parent does not resolve
        inside the root."""
        rel = self._physical_rel(path)
        return self._state.dest / rel if rel is not None else path

    def _collision_key(self, path: Path) -> str:
        """The collision-map key of the entry at ``path``: where it physically is.

        The parent is resolved, so two members that reach one file by different names
        collide: ``s/f`` through the archive's own ``s -> d`` is ``d/f``. Keying on the
        name alone let both report ``EXTRACTED`` for one file. The final component is
        not followed, as a member's own destination never is. A parent that does not
        resolve inside the destination (it was checked when the member was accepted,
        so only a later change can do that) falls back to the name as written.
        """
        rel = self._physical_rel(path)
        if rel is None:
            rel = self._rel_name(path)
        return collision_key(rel, self._policy)

    def _physical_key(self, path: Path) -> str:
        """The key of the directory at ``path`` in ``run_dirs`` and ``pending_dirs``:
        where it physically is, with case and Unicode normalization folded under every
        policy.

        ``_collision_key`` folds only under ``STRICT`` and ``STANDARD``, because it
        decides which member's result a name reaches. These two maps ask whether a
        directory is one this run made, which does not depend on how the archive spelled
        it: on a case-insensitive filesystem ``D/`` and ``d/`` are one directory under
        ``TRUSTED`` too. Where the folded key covers two directories (``X/`` beside
        ``x/`` on a case-sensitive filesystem), the readers tell them apart on disk.
        """
        rel = self._physical_rel(path)
        if rel is None:
            rel = self._rel_name(path)
        return collision_key(rel, ExtractionPolicy.STANDARD)

    def _derive_free_name(self, requested: Path, *, is_dir: bool) -> Path:
        """The first ``name (N)`` (N = 1, 2, …) free both in the collision map and on disk.

        :func:`~archivey.paths.numbered_name` spells each candidate (``photo.jpg`` →
        ``photo (1).jpg``), the same spelling the CLI's single-root hoist uses.

        The search resumes after the last ``N`` this run handed out for the same
        collision key, rather than starting at 1 each time: restarting made ``k``
        members colliding on one key cost ``k²`` probes, which a hostile archive of
        case-variant names turns into hours. The counter records names handed out, not
        names taken, so a member renamed and then failing to write leaves a gap in the
        numbering (``(1)`` unused, next member gets ``(2)``). The result is still
        deterministic, which is what the spec asks. ``_release_claim`` resets the
        counters, so a name freed by an anti-item is found again."""
        parent = requested.parent
        rename_next = self._state.rename_next
        counter_key = self._collision_key(requested)
        n = rename_next.get(counter_key, 1)
        while True:
            candidate = parent / numbered_name(requested.name, n, is_dir=is_dir)
            candidate_key = self._collision_key(candidate)
            if candidate_key not in self._state.collision_map and not self._occupied(
                candidate
            ):
                rename_next[counter_key] = n + 1
                return candidate
            n += 1

    def _apply_anti_item(
        self, original: ArchiveMember, dest_path: Path
    ) -> ExtractionResult:
        """Delete what an earlier member of this run wrote at the anti-item's name.

        An exact path this run wrote wins. Otherwise the name is looked up through the
        collision map, so it matches the way every other collision does: under STRICT
        and STANDARD, an anti-item ``readme`` deletes the ``README`` this run wrote,
        which on a case-insensitive filesystem is the file it names. TRUSTED keys on the
        exact name, so there it is exact. Directories are not in the map, so a directory
        matches only by its exact path. Nothing is deleted that this run did not write.
        """
        written_paths = self._state.written_paths
        if dest_path not in written_paths:
            claim = self._state.collision_map.get(self._collision_key(dest_path))
            if claim is not None and claim.path in written_paths:
                dest_path = claim.path
        if dest_path not in written_paths:
            return ExtractionResult(
                original, dest_path, ExtractionStatus.EXTRACTED, None
            )
        try:
            st = os.lstat(long_path(dest_path))
        except FileNotFoundError:
            written_paths.discard(dest_path)
            self._release_claim(dest_path)
            return ExtractionResult(
                original, dest_path, ExtractionStatus.EXTRACTED, None
            )
        with self._readonly_cleared(dest_path):
            if stat.S_ISDIR(st.st_mode):
                os.rmdir(long_path(dest_path))
                # Its member's metadata has no directory left to go on.
                self._drop_pending_dir(dest_path)
            else:
                os.unlink(long_path(dest_path))
            if stat.S_ISLNK(st.st_mode):
                self._note_link_change(dest_path)
        written_paths.discard(dest_path)
        self._release_claim(dest_path)
        self._forget_source_path(dest_path)
        return ExtractionResult(original, dest_path, ExtractionStatus.EXTRACTED, None)

    def _release_claim(self, path: Path) -> None:
        """Drop the collision claim on ``path`` once its content is gone.

        ``written_paths`` and ``collision_map`` both track what this run put on disk, so
        an anti-item delete has to clear both or the map keeps advertising a claim for
        content that no longer exists — which a later same-key member would see as a
        collision, aborting under ``AbortOn.NAME_COLLISION`` against an empty destination
        or revising an already-deleted member to ``OVERWRITTEN``."""
        key = self._claimed_key(path)
        collision_map = self._state.collision_map
        claim = collision_map.get(key)
        if claim is not None and claim.path == path:
            del collision_map[key]
            # A freed name can be the first free one for a later RENAME again.
            self._state.rename_next.clear()

    def _write_file(
        self,
        original: ArchiveMember,
        transformed: ArchiveMember,
        stream: BinaryIO | None,
        dest_path: Path,
    ) -> ExtractionResult:
        state = self._state
        declined = self._make_room(original, transformed, dest_path, atomic=True)
        if declined is not None:
            return declined
        if stream is None and self._current.retyped:
            # The pass yielded this member as a link, with no data stream, before its
            # data showed it is a file. Random access opens it now. A forward-only pass
            # is already past that data, so the content is out of reach: fail the member
            # rather than write an empty file for one the archive carries in full.
            reader = state.reader
            if reader is None or reader._streaming:
                raise ExtractionError(
                    f"{quoted(original.name)} is flagged as a link but its data is a "
                    "file's content, which a streaming pass cannot go back for",
                    member_name=original.name,
                )
            with contextlib.closing(reader._lazy_member_stream(original)) as reopened:
                self._write_file_atomic(reopened, dest_path, transformed)
        elif (
            written_source := self._open_written_source(original, dest_path)
        ) is not None:
            # A file copy whose source this run wrote: its bytes come from that file,
            # and ``stream`` is closed unread, so nothing decodes the source again.
            # Read with no digest check, because none is needed: the source's write
            # read its stream to the end through the source's digest and size checks,
            # and a write that failed them recorded no identity to copy from.
            with written_source:
                self._write_file_atomic(written_source, dest_path, transformed)
        else:
            state.streaming_now = id(original) if stream is not None else None
            try:
                self._write_file_atomic(stream, dest_path, transformed)
            finally:
                state.streaming_now = None
        if self._dry is None and id(original) in state.declined:
            # A file that cannot be looked at now is simply not copied from.
            with contextlib.suppress(OSError):
                state.written_files[original.member_id, dest_path] = _file_identity(
                    os.stat(long_path(dest_path), follow_symlinks=False)
                )

        # Record this FILE's path under the ORIGINAL member id so later hardlinks whose
        # link_target_member is this member can os.link against it.
        state.source_paths.setdefault(original.member_id, []).append(dest_path)
        return ExtractionResult(original, dest_path, ExtractionStatus.EXTRACTED, None)

    def _keep_copy_source(self, source: ArchiveMember) -> bool:
        """Whether the pass keeps a RAR file copy's source for its copies.

        Asked when the source's first byte is decoded. Not when this run is writing the
        source from its stream: the copies then come from the file written
        (``_open_written_source``). A dry run writes empty files, so it keeps every
        source.
        """
        if self._dry is not None or id(source) != self._state.streaming_now:
            return True
        self._state.declined.add(id(source))
        return False

    def _open_written_source(
        self, original: ArchiveMember, dest_path: Path
    ) -> BinaryIO | None:
        """The file this run wrote for a RAR file copy's source, opened, or ``None``.

        ``None`` when ``original`` is not a file copy, its source was not written (a
        selector or filter dropped it, its write failed, or a later member of its name
        took its place), its sizes disagree, or the file no longer is the one written:
        another inode, size or modification time than ``written_files`` recorded for
        the source's own write (a later member written to the same path does not
        count). The check is made on the opened file, so a swap between check and read
        is caught too. The caller then reads the copy's own stream, which serves the source's
        bytes from the pass or decodes them again.
        """
        source = original.link_target_member
        if (
            self._dry is not None
            or source is None
            or not original.extra.get(EXTRA_IS_FILE_COPY)
            or source.size != original.size
        ):
            return None
        for path in self._state.source_paths.get(source.member_id, ()):
            expected = self._state.written_files.get((source.member_id, path))
            # Never the copy's own destination: the atomic write would replace the
            # file it is reading.
            if expected is None or path == dest_path:
                continue
            try:
                f = open(long_path(path), "rb")  # noqa: SIM115 - returned open
            except OSError:
                continue
            try:
                found = _file_identity(os.fstat(f.fileno()))
            except OSError:
                found = None
            if found is not None and found == expected and found[2] == original.size:
                return f
            f.close()
        return None

    def _write_symlink(
        self,
        original: ArchiveMember,
        transformed: ArchiveMember,
        dest_path: Path,
    ) -> ExtractionResult:
        target = transformed.link_target
        if target is None and original.link_target_unrecorded:
            # The archive says this is a link and records nowhere for it to point — a
            # 7-Zip-written directory symlink or junction, or a reparse buffer naming
            # nothing. There is nothing to write, and nothing here went wrong, so this
            # is a LINK_TARGET_UNAVAILABLE result rather than a per-member failure that
            # OnError.STOP would turn into an aborted extraction.
            # Checked before _make_room so a member we are not going to
            # write cannot unlink an existing destination under OverwritePolicy.REPLACE.
            return ExtractionResult(
                original, None, ExtractionStatus.LINK_TARGET_UNAVAILABLE, None
            )

        if target is None:
            # Unset for any other reason, which always means the archive records a
            # target this read could not produce. A data-stored target has been looked
            # for by now, either while listing or by `_transform` on request, so it is
            # out of reach: compressed, split across volumes, encrypted, or refused as
            # too long. Reporting that as the status above would claim success while
            # dropping a member the archive describes in full, so it stays the
            # per-member failure it was before that status existed. The reason is the
            # backend's to report: `BaseArchiveReader._emit_link_target_unavailable`.
            raise LinkTargetNotFoundError(
                "Symlink has no target",
                reason=LinkTargetNotFoundReason.UNREADABLE,
                member_name=transformed.name,
            )

        declined = self._make_room(original, transformed, dest_path, atomic=False)
        if declined is not None:
            return declined
        # A symlink is target-independent: create it even if the target was filtered out,
        # appears later, or lies outside the archive — it may dangle. Only the escape
        # check below constrains it. An os.symlink failure (unsupported FS) propagates as
        # a per-member OnError failure; no copy-the-target fallback. A dry run creates
        # an absolute target that names dest pointing into its scratch copy instead.
        on_disk = self._link_target_on_disk(target)
        try:
            if _DIRECTORY_LINKS:
                is_dir = self._link_to_directory(dest_path, target, on_disk)
                os.symlink(on_disk, long_path(dest_path), target_is_directory=is_dir)
                self._state.link_kinds[dest_path] = (on_disk, is_dir)
            else:
                os.symlink(on_disk, long_path(dest_path))
        except FileExistsError as exc:
            # The name was taken after the existence check, as for a FILE or HARDLINK
            # landing (``_swap_into_place``).
            if not self._taken_after_check(dest_path, atomic=False):
                raise
            raise self._already_exists(dest_path, transformed) from exc
        self._resolutions_changed()

        # Re-validate the symlink target AFTER creating it, resolving through the real
        # filesystem. check_universal already rejected an escaping target at
        # planning time, but that check resolves the target lexically against the tree as it
        # looked *then*. The authoritative question — where does this link actually point? —
        # can only be answered against the filesystem as it is now, because an *earlier*
        # extracted member may have planted a symlink on one of this target's path
        # components that redirects it outside dest (a "chained symlink": e.g. member 1 is
        # `sub -> /tmp/evil`, member 2 is `sub/link -> x`, so `sub/link` resolves to
        # `/tmp/evil/x`). Path.resolve() follows those on-disk links, so it catches the
        # escape the planning check cannot see. We can't do this "just before" creating the
        # link because there is no link to resolve until it exists; and resolving the *bare
        # target string* would only repeat check_universal. So: create, resolve, and unlink
        # if it escaped. A link that cannot be resolved, a symlink loop included on
        # every Python version, also counts as an escape (fail safe rather than crash).
        # This is the third of the three defense-in-depth layers named in the
        # `safe-extraction` spec ("Symlink Escape Re-Validated at Extraction Time");
        # layers 1-2 are in check_universal. A *later* member can still change what
        # this link resolves to; the caller records the link in ``links`` for that.
        problem = _symlink_problem(dest_path, on_disk, self._state.dest_root)
        if problem is not None:
            try:
                os.unlink(long_path(dest_path))
            except OSError:
                pass
            self._resolutions_changed()
            raise FilterRejectionError(
                problem,
                member_name=transformed.name,
                link_target=target,
            )

        return ExtractionResult(original, dest_path, ExtractionStatus.EXTRACTED, None)

    def _link_to_directory(self, dest_path: Path, target: str, on_disk: str) -> bool:
        """Whether the symlink at ``dest_path`` is made a directory link, where the
        platform has two kinds (``_DIRECTORY_LINKS``).

        Yes when its target is a directory now, or when the archive's listing shows
        the target is one (``_listed_directory``). A target the run has not written
        yet and the listing cannot place, as in a streaming pass, gets a file link;
        ``_settle_link_kinds`` remakes it once the run is done if it turned out a
        directory.
        """
        if os.path.isdir(long_path(os.path.join(dest_path.parent, on_disk))):
            return True
        return self._listed_directory(dest_path, target)

    def _listed_directory(self, dest_path: Path, target: str) -> bool:
        """Whether the archive's listing shows the target of a symlink at ``dest_path``
        is a directory: a directory member, or a name other members are inside.

        Matched by name, lexically from the link's place under the destination, and
        casefolded, as Windows matches names. A target that leaves the destination, or
        is absolute, is not placed. A name the policy or a filter writes elsewhere is
        missed and left to ``_settle_link_kinds``.
        """
        state = self._state
        if state.listed is None:
            return False
        if state.listed_names is None:
            # One string per member: an ancestor is found by a prefix search, so no
            # string is built for each directory a deep name passes through.
            names: list[str] = []
            dirs: set[str] = set()
            for member in state.listed:
                name = posixpath.normpath(member.name.replace("\\", "/").strip("/"))
                folded = name.casefold()
                names.append(folded)
                if member.type == MemberType.DIRECTORY:
                    dirs.add(folded)
            names.sort()
            state.listed_names = (names, dirs)
        try:
            rel_parent = dest_path.parent.relative_to(state.dest).as_posix()
        except ValueError:
            return False
        spelled = target.replace("\\", "/")
        if spelled.startswith("/"):
            return False
        named = posixpath.normpath(posixpath.join(rel_parent, spelled))
        if named == ".." or named.startswith("../"):
            return False
        folded = named.casefold()
        names, dirs = state.listed_names
        if folded in dirs:
            return True
        inside = folded + "/"
        at = bisect.bisect_left(names, inside)
        return at < len(names) and names[at].startswith(inside)

    def _settle_link_kinds(self) -> None:
        """Remake each symlink this run made of the wrong kind for what its target is
        now: a file link to a directory, or a directory link to anything else. Where
        the platform has one kind (POSIX), there is nothing to do.

        Only a link still at its path with the target it was made with is remade, so
        nothing a later member put there is touched. The new link is made under a
        temporary name and renamed into place. A failure is logged and leaves the
        link as it was, as metadata is best-effort.
        """
        state = self._state
        kinds, state.link_kinds = state.link_kinds, {}
        for path, (on_disk, is_dir) in kinds.items():
            if path not in state.written_paths:
                continue
            try:
                # Windows hands back an absolute target with the extended
                # prefix: compared plain, as it was created.
                if plain(os.readlink(long_path(path))) != on_disk:
                    continue
            except OSError:
                continue
            wanted = os.path.isdir(long_path(os.path.join(path.parent, on_disk)))
            if wanted == is_dir:
                continue
            tmp = self._temp_sibling(path.parent)
            try:
                os.symlink(on_disk, long_path(tmp), target_is_directory=wanted)
            except OSError as exc:
                self._rebase_os_error(exc)
                logger.warning(
                    "Could not remake symlink %r for its target: %s",
                    str(self._shown(path)),
                    exc,
                )
                continue
            try:
                # Not os.replace: Windows will not replace a directory link by name.
                os.unlink(long_path(path))
                try:
                    os.rename(long_path(tmp), long_path(path))
                except OSError:
                    # Put the link back as it was.
                    with contextlib.suppress(OSError):
                        os.symlink(on_disk, long_path(path), target_is_directory=is_dir)
                    raise
            except OSError as exc:
                self._rebase_os_error(exc)
                logger.warning(
                    "Could not remake symlink %r for its target: %s",
                    str(self._shown(path)),
                    exc,
                )
                with contextlib.suppress(OSError):
                    os.unlink(long_path(tmp))

    def _write_hardlink(
        self,
        original: ArchiveMember,
        transformed: ArchiveMember,
        dest_path: Path,
        result_index: int,
    ) -> ExtractionResult:
        source = original.link_target_member
        if source is None:
            raise LinkTargetNotFoundError(
                "Hardlink target not found",
                reason=LinkTargetNotFoundReason.UNRESOLVED,
                member_name=transformed.name,
                link_target=original.link_target,
            )
        if source.type is not MemberType.FILE:
            # `link_target_member` is the end of the link chain, so anything but a
            # file here (a directory, a device) cannot share an inode. A hard link to
            # a symlink whose target is known never gets here: `_as_written` writes it
            # as a symlink. Only a FILE source is recorded in `source_paths`. Without
            # this check, the member went down the branches below for a source that
            # was not written, and the error said the source was excluded.
            reason = (
                "a directory, and a hard link to a directory cannot be created"
                if source.type is MemberType.DIRECTORY
                else f"a {source.type.value}, not a regular file"
            )
            raise ExtractionError(
                f"Hardlink target {quoted(source.name)} is {reason}",
                member_name=transformed.name,
                link_target=source.name,
            )

        if source.member_id in self._state.source_paths:
            # Taken before ``_make_room``: under REPLACE a link can land on one of its
            # own source's paths, which ``_make_room`` then forgets.
            candidates = list(self._state.source_paths[source.member_id])
            declined = self._make_room(original, transformed, dest_path, atomic=True)
            if declined is not None:
                return declined
            self._place_link(source.member_id, candidates, dest_path, transformed)
            return ExtractionResult(
                original, dest_path, ExtractionStatus.EXTRACTED, None
            )

        # Source not on disk yet: either it was excluded by the selector/filter, or (in a
        # crafted/non-TAR-ordered archive) it simply appears later in archive order. The
        # second pass distinguishes the two: a source written later in this same pass is
        # just linked against; a truly excluded one is re-read and materialized.
        if self._state.forward_only:
            # Forward-only: the source's bytes already streamed past — unrecoverable. Per
            # spec this is a per-member ExtractionError handled by OnError.
            raise ExtractionError(
                "Hardlink source was excluded or replaced by a later member, and "
                "cannot be recovered on a forward-only stream",
                link_target=source.name,
                member_name=transformed.name,
            )
        # Re-readable: resolve in the second pass.
        self._state.orphans.append(
            _Orphan(
                result_index,
                original,
                transformed,
                dest_path,
                source,
                spelled_from=self._current.spelled_from,
            )
        )
        return ExtractionResult(original, None, ExtractionStatus.FAILED, None)

    # --- orphan (second pass) ------------------------------------------------------

    def _resolve_orphans(self, reader: BaseArchiveReader) -> None:
        orphans_by_source: dict[int, list[_Orphan]] = {}
        for orphan in self._state.orphans:
            orphans_by_source.setdefault(orphan.source.member_id, []).append(orphan)

        # A source that was written LATER in the first pass (a link preceding its source in
        # archive order) is already on disk: just link against it — re-reading its bytes
        # here would create an independent inode and double-count against the bomb limits.
        needed: set[int] = set()
        for source_id, group in orphans_by_source.items():
            if source_id in self._state.source_paths:
                self._link_orphan_group(group, source_id)
            else:
                needed.add(source_id)
        if not needed:
            return

        # One second forward pass over the (re-readable) source; write each needed source's
        # content to the first writable link path and os.link the rest. Driven through the
        # public stream_members() with the needed sources as an identity selector, so
        # unneeded members are never surfaced (or opened — the streams are lazy).
        # Built here for the same reason as the first pass's identity selector:
        # these entries are the coordinator's, so an unmatched one is not reported
        # as MEMBER_SELECTOR_UNMATCHED.
        needed_sources = CollectionSelector(
            [orphans_by_source[source_id][0].source for source_id in needed],
            record=False,
        )
        for member, stream in reader.stream_members(needed_sources):
            group = orphans_by_source[member.member_id]
            link_error: ArchiveyError | None = None
            try:
                self._materialize_orphan_source(member, stream, group)
            except (_AlwaysStopResourceLimitError, DiagnosticRaisedError):
                raise
            except (ArchiveyError, OSError) as exc:
                # One failed source, N failed links: the fan-out is recorded on the
                # results themselves, so a caller can tell N separate failures from one
                # failure seen N times without joining against a diagnostic. The second
                # pass records FAILED, so only OnError can stop it.
                error, status = self._classify(exc, member.name)
                # Nothing the second pass runs raises FilterRejectionError.
                assert status is ExtractionStatus.FAILED
                self._record_failure_group(group, error)
                self._stop_or_log(
                    exc,
                    error,
                    status,
                    kind="orphaned hardlink source",
                    name=member.name,
                )
            finally:
                self._close(stream)
                link_error = self._recheck_links()
            if link_error is not None:
                raise link_error
            needed.discard(member.member_id)
            if not needed:
                break  # every orphaned source is materialized; stop opening members

        # Any orphan whose source never reappeared (should not happen for a re-readable
        # source) is a per-member failure.
        for source_id in needed:
            group = orphans_by_source[source_id]
            name = group[0].source.name
            err = ExtractionError(
                "Hardlink source was not found on the second pass", member_name=name
            )
            self._record_failure_group(group, err)
            self._stop_or_log(
                err,
                err,
                ExtractionStatus.FAILED,
                kind="orphaned hardlink source",
                name=name,
            )

    def _record_failure_group(
        self, group: list[_Orphan], error: ArchiveyError | OSError
    ) -> None:
        """Record one failed hardlink source as N FAILED results sharing a group id.

        The id is opaque and process-local (``uuid4().hex``): callers compare it for
        equality to join a group and nothing more. Both fields are set together, so a
        partial fill can never look like a group."""
        group_id = uuid.uuid4().hex
        group_size = len(group)
        for orphan in group:
            self._revise_result(
                orphan.result_index,
                ExtractionResult(
                    orphan.original,
                    None,
                    ExtractionStatus.FAILED,
                    error,
                    failure_group_id=group_id,
                    failure_group_size=group_size,
                ),
            )

    def _revise_result(self, index: int, new: ExtractionResult) -> None:
        """Overwrite a recorded result, carrying forward first-pass facts it omits.

        The second pass rebuilds a result from scratch, but two facts were decided in
        the first pass (before the member was deferred) and are still true: the name
        rewrite, and the ``requested_path`` the member asked for. The rewrite is two
        fields, ``presented_name`` and the ``rewrites`` that produced it, so neither is
        carried without the other. A rebuild that does not supply them must not erase
        them — results are the sole record, so a dropped field is a fact lost rather
        than a fact reported elsewhere.
        Unlike ``_set_result`` it moves no progress tally: ``_report_progress`` is
        called only from the main member loop, which has finished by the second pass.
        """
        results = self._state.results
        prior = results[index]
        if prior.presented_name is not None and new.presented_name is None:
            new = replace(
                new, presented_name=prior.presented_name, rewrites=prior.rewrites
            )
        if prior.requested_path is not None and new.requested_path is None:
            new = replace(new, requested_path=prior.requested_path)
        results[index] = new

    def _append_result(self, new: ExtractionResult) -> int:
        """Record ``new`` as the next result, tallied, and return its index."""
        self._tally(None, new.status)
        self._state.results.append(new)
        index = len(self._state.results) - 1
        member_id = new.member._member_id
        if member_id is not None:
            self._state.result_ids[member_id] = index
        return index

    def _set_result(self, index: int, new: ExtractionResult) -> None:
        """Replace recorded result ``index`` with ``new``. The progress tallies count
        the results, so they follow the change; a report already sent is not resent."""
        results = self._state.results
        self._tally(results[index].status, new.status)
        results[index] = new

    def _tally(
        self, old: ExtractionStatus | None, new: ExtractionStatus | None
    ) -> None:
        for status, step in ((old, -1), (new, 1)):
            if status is ExtractionStatus.EXTRACTED:
                self._state.members_extracted += step
            elif status is ExtractionStatus.BLOCKED:
                self._state.members_blocked += step

    def _materialize_orphan_source(
        self,
        source_member: ArchiveMember,
        stream: BinaryIO | None,
        group: list[_Orphan],
    ) -> None:
        # Count the recovered source bytes toward the cumulative/ratio guards too.
        self._state.tracker.start_member(source_member)

        # The excluded source's content is written to the FIRST link whose destination the
        # OverwritePolicy allows writing (a SKIP over an existing entry moves on to the next
        # link), never to the source's own name — atomically (temp + os.replace), same as a
        # normal FILE, so a failure while re-reading doesn't clobber an existing entry. The
        # link's transformed copy supplies the on-disk mode/timestamps: hardlinks share one
        # inode, so the metadata must be applied to the file that carries the content. Each
        # link's destination is O2-collision-resolved against the map the main pass built
        # (a deferred link's key may have been claimed after it was orphaned).
        remaining: list[_Orphan] = []
        for index, orphan in enumerate(group):
            try:
                resolved, prior, collided_with = self._resolve_collision(
                    orphan.original, orphan.transformed, orphan.dest_path
                )
                result = self._make_room(
                    orphan.original, orphan.transformed, resolved, atomic=True
                )
                if result is None:
                    self._write_file_atomic(stream, resolved, orphan.transformed)
                    self._state.written_paths.add(resolved)
                    self._state.source_paths.setdefault(
                        source_member.member_id, []
                    ).append(resolved)
                    result = ExtractionResult(
                        orphan.original, resolved, ExtractionStatus.EXTRACTED, None
                    )
            except (ArchiveyError, OSError) as exc:
                orphan.report_stored_spelling(exc)
                raise
            result = self._settle_placement(
                result,
                requested=orphan.dest_path,
                target=orphan.dest_path,
                prior=prior,
                collided_with=collided_with,
                transformed=orphan.transformed,
                result_index=orphan.result_index,
            )
            self._revise_result(orphan.result_index, result)
            if result.status is ExtractionStatus.EXTRACTED:
                remaining = group[index + 1 :]
                break
        # Nothing remains when every link's destination already exists under SKIP:
        # then nothing was written either.
        self._link_orphan_group(remaining, source_member.member_id)

    def _link_orphan_group(self, group: list[_Orphan], source_id: int) -> None:
        """Link each orphan in ``group`` against the source content already on disk
        (recorded under ``source_id``), applying the OverwritePolicy per link (O2 collisions
        resolved against the map) and recording per-link results; failures follow
        ``OnError``. A link can replace a symlink or a directory, so the symlinks it
        may have moved are rechecked after each one, whatever its outcome."""
        for orphan in group:
            try:
                self._link_orphan(orphan, source_id)
            finally:
                link_error = self._recheck_links()
            if link_error is not None:
                raise link_error

    def _link_orphan(self, orphan: _Orphan, source_id: int) -> None:
        """One link of ``_link_orphan_group``, which rechecks symlinks after it."""
        try:
            resolved, prior, collided_with = self._resolve_collision(
                orphan.original, orphan.transformed, orphan.dest_path
            )
        except (ArchiveyError, OSError) as exc:
            orphan.report_stored_spelling(exc)
            raise
        try:
            # Taken before ``_make_room``, as in ``_write_hardlink``. An earlier link
            # of the group that failed after its ``_make_room`` can have taken the
            # last path; ``_place_link`` then fails this link as well. No archive
            # reaches that alone: the earlier link has to fail in ``os.link`` or
            # ``os.replace``, which needs the filesystem to change under the run.
            candidates = list(self._state.source_paths.get(source_id, ()))
            result = self._make_room(
                orphan.original, orphan.transformed, resolved, atomic=True
            )
            if result is None:
                self._place_link(source_id, candidates, resolved, orphan.transformed)
                self._state.written_paths.add(resolved)
                result = ExtractionResult(
                    orphan.original, resolved, ExtractionStatus.EXTRACTED, None
                )
        except (_AlwaysStopResourceLimitError, DiagnosticRaisedError):
            raise
        except (ArchiveyError, OSError) as exc:
            orphan.report_stored_spelling(exc)
            # FAILED, as for an orphaned source.
            error, status = self._classify(exc, orphan.original.name)
            # Nothing the second pass runs raises FilterRejectionError.
            assert status is ExtractionStatus.FAILED
            self._revise_result(
                orphan.result_index,
                ExtractionResult(
                    orphan.original,
                    None,
                    ExtractionStatus.FAILED,
                    error,
                    requested_path=orphan.dest_path,
                    collided_with=collided_with,
                ),
            )
            # A single link's failure, not a source fan-out: no group id.
            name = orphan.original.name
            self._stop_or_log(exc, error, status, kind="hardlink", name=name)
            return
        result = self._settle_placement(
            result,
            requested=orphan.dest_path,
            target=orphan.dest_path,
            prior=prior,
            collided_with=collided_with,
            transformed=orphan.transformed,
            result_index=orphan.result_index,
        )
        self._revise_result(orphan.result_index, result)

    @contextlib.contextmanager
    def _readonly_cleared(self, path: Path, *, ours: bool = False) -> Iterator[None]:
        """Let the block replace or remove ``path`` on Windows when this run wrote it
        read-only. ``ours`` says the caller knows this run wrote it (a parked copy
        already taken out of ``parked``).

        Windows refuses to replace or unlink a file, or remove a directory, with the
        read-only attribute (``WinError 5``), which a stored mode without write
        permission (``0o444``, ``0o555``) sets; POSIX checks only the parent directory.
        So a later member of the same name replaced the entry on POSIX and failed on
        Windows. The attribute is cleared only on a regular file or a directory this run
        wrote (or parked), or a directory it created as a parent that a later member made
        read-only: a read-only entry the caller already had stays protected, as on
        Windows before. A run applies directory modes only when it ends
        (``_apply_directory_metadata``), so a directory it wrote is read-only during
        the run only when something else made it so; the directory case is kept for
        that. The attribute belongs to the file, not the name, so it is
        put back on the other names of a file (hard links this run made) once the block
        is done, and on ``path`` itself if the block fails.
        """
        state = self._state
        st: os.stat_result | None = None
        if _WINDOWS and (
            ours
            or path in state.written_paths
            or path in state.created_dirs
            or path in self._current.parked
        ):
            with contextlib.suppress(OSError):
                st = os.lstat(long_path(path))
        if (
            st is None
            or not (stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode))
            or st.st_mode & stat.S_IWRITE
        ):
            yield
            return
        others: list[Path] = []
        if stat.S_ISREG(st.st_mode) and st.st_nlink > 1:
            identity = (st.st_dev, st.st_ino)
            for paths in state.source_paths.values():
                for other in paths:
                    with contextlib.suppress(OSError):
                        ost = os.lstat(long_path(other))
                        if other != path and (ost.st_dev, ost.st_ino) == identity:
                            others.append(other)
        os.chmod(long_path(path), st.st_mode | stat.S_IWRITE)
        try:
            yield
        except BaseException:
            with contextlib.suppress(OSError):
                # Still the same file: the block failed.
                os.chmod(long_path(path), st.st_mode)
            raise
        for other in others:
            with contextlib.suppress(OSError):
                os.chmod(long_path(other), st.st_mode)

    def _forget_source_path(self, path: Path) -> bool:
        """Stop offering ``path`` as a hardlink source: something else is going there.
        Returns whether another path still holds the content it held.

        ``source_paths`` records where each source member's content was written, and a
        later hardlink to that member is made against it. Once another member replaces
        the entry at that path, it holds that member's content (or is a symlink that
        ``os.link`` would follow), so a link made against it would get the wrong bytes.
        A source left with no path is re-read by the second pass where the source is
        seekable, as an excluded one is.
        """
        source_paths = self._state.source_paths
        content_kept = False
        for source_id, paths in list(source_paths.items()):
            if path in paths:
                paths.remove(path)
                if paths:
                    content_kept = True
                else:
                    del source_paths[source_id]
        return content_kept

    def _resolutions_changed(self) -> None:
        """Forget the cached parent resolutions: a symlink was created or removed."""
        self._state.resolved_parents.clear()

    def _note_link_change(self, path: Path) -> None:
        """Report a symlink or directory at ``path`` replaced or removed this member."""
        self._resolutions_changed()
        if self._state.links is not None:
            self._state.links.note_change(path)

    def _recheck_links(self) -> ArchiveyError | None:
        """Remove the links this run created that the member just handled made escape.

        Called once per member, before the next one is handled, so no later member
        (or a progress callback) sees an escaping link. A removed link's result
        becomes ``BLOCKED`` in place (``_set_result``).

        It runs in a ``finally``, so it returns the error that must end the run
        instead of raising it, which would replace the member's own error: the
        recheck bound running out, or ``AbortOn.BLOCKED_MEMBER`` with a link removed.
        """
        state = self._state
        links = state.links
        if links is None or not links.has_changes:
            return None
        dest_root = state.dest_root
        # The recheck asks a yes/no question; the reason is kept here, per link path,
        # so a loop a later member closed is named as a loop. ``recheck`` reports a
        # link as escaped only when this callback refused it, so every escaped link
        # has an entry: indexed, so a broken invariant raises instead of mislabelling.
        problems: dict[Path, str] = {}

        def escapes(path: Path, target: str) -> bool:
            problem = _symlink_problem(path, target, dest_root)
            if problem is not None:
                problems[path] = problem
            return problem is not None

        outcome = links.recheck(escapes)
        removed = [
            (
                link,
                f"{problems[link.path]}: a later member changed "
                "a path it resolves through",
            )
            for link in outcome.escaped
        ] + [
            (link, "Symlink removed unchecked: the recheck limit was reached")
            for link in outcome.unchecked
        ]
        if removed:
            self._resolutions_changed()
        first: FilterRejectionError | None = None
        for link, message in removed:
            prior = state.results[link.result_index]
            error = FilterRejectionError(
                message,
                member_name=prior.member.name,
                link_target=state.stored_targets.get(link.result_index, link.target),
            )
            first = first or error
            state.written_paths.discard(link.dest_path)
            self._release_claim(link.dest_path)
            if prior.status is ExtractionStatus.EXTRACTED:
                self._set_result(
                    link.result_index,
                    replace(
                        prior, path=None, status=ExtractionStatus.BLOCKED, error=error
                    ),
                )
            logger.warning("Removed symlink %r: %s", prior.member.name, error)
        if outcome.unchecked:
            return _AlwaysStopResourceLimitError(
                f"Symlink recheck limit reached: max_entries={self._limits.max_entries}"
            )
        if first is not None and AbortOn.BLOCKED_MEMBER in self._abort_on:
            return first
        return None

    def _mark_overwritten(self, prior: _Claim | None) -> None:
        """Revise a clobbered member's result to OVERWRITTEN: its content is gone.

        Called once the caller knows the earlier member lost its destination — either
        because a later REPLACE landed on it, or because a REPLACE cleared it and then
        failed. Shared by the main pass and the orphan second pass: a deferred hardlink
        can be the member that replaces an earlier write just as a first-pass member
        can."""
        if prior is None or self._overwrite is not OverwritePolicy.REPLACE:
            return
        self._revise_to_overwritten(prior.result_index)

    def _revise_removed_directory(self, removed: Path) -> None:
        """Revise the directory results this run wrote at ``removed`` to OVERWRITTEN.

        Looked up by collision key, so a result that reached the same directory through
        a symlink or a case variant is found. A casefolded key can also cover a
        different directory on a case-sensitive filesystem (``X/`` beside ``x/``), so a
        result is revised only when its own path is gone; the others stay recorded."""
        key = self._collision_key(removed)
        indices = self._state.written_dirs.pop(key, None)
        if not indices:
            return
        kept: list[int] = []
        for index in indices:
            path = self._state.results[index].path
            if path is not None and os.path.lexists(long_path(path)):
                kept.append(index)
            else:
                self._revise_to_overwritten(index)
        if kept:
            self._state.written_dirs[key] = kept

    def _revise_to_overwritten(self, index: int) -> None:
        """Revise result ``index`` to OVERWRITTEN, keeping where it had written."""
        clobbered = self._state.results[index]
        if clobbered.status is ExtractionStatus.SUPERSEDED:
            # A superseded copy still on disk (a file the filesystem would not remove, or
            # a directory other members were written into): it keeps its status.
            return
        # The clobbered member was tallied as EXTRACTED when it completed, and is
        # no longer: ``_set_result`` moves the tally, or the final report would claim
        # more extracted members than ``results`` contains.
        self._set_result(
            index,
            replace(
                clobbered,
                path=None,
                status=ExtractionStatus.OVERWRITTEN,
                requested_path=(
                    clobbered.requested_path
                    if clobbered.requested_path is not None
                    else clobbered.path
                ),
            ),
        )

    # --- filesystem helpers --------------------------------------------------------

    def _ensure_dest_root(self, dest: Path) -> bool:
        """Ensure ``dest`` is a directory to extract into, creating it if absent; under
        ``dry_run``, only refuse a ``dest`` a real run would refuse
        (``_DryRun.check_creatable``). Returns whether this call created it, or under
        ``dry_run`` whether a real run would have.

        A dest that resolves to a directory — a real directory or a symlink pointing at
        one — is reused, and members land inside the resolved target (``run`` resolves
        ``dest`` before writing). This matches ``tar -C``/``unzip -d`` and leaves the
        caller's symlink in place; the dest root is trusted (unlike archive-internal
        symlinks, which are never written through).

        A dest that exists as anything else — a regular file, a symlink to a file, a
        dangling symlink — is a hard error regardless of ``OverwritePolicy``: we never
        delete it. Extraction is not an invitation to remove a path the caller pointed at
        by mistake (e.g. a CLI given a file argument where a directory was meant).
        """
        named = dest if self._dry is None else self._dry.shown_dest
        # A real directory or a symlink resolving to one: reuse / follow.
        if os.path.isdir(long_path(named)):
            return False
        # ``lexists`` (not ``exists``) so a dangling symlink is caught here rather than
        # surfacing as a raw FileExistsError from ``mkdir`` below.
        if os.path.lexists(long_path(named)):
            raise ExtractionError(
                f"Destination exists and is not a directory: {display_path(named)}"
            )
        if self._dry is not None:
            self._dry.check_creatable()
        else:
            os.makedirs(long_path(dest), exist_ok=True)
        return True

    def _makedirs(self, path: Path, member: ArchiveMember) -> None:
        """``os.makedirs(path, exist_ok=True)`` for ``member``, typed when the archive
        itself is in the way.

        A file member ``d`` followed by ``d/f`` (or a directory ``d/y``) cannot both be
        extracted, and the conflict is in the archive, not in the filesystem. So when
        creating the directories fails and one of the components is a non-directory
        that this run wrote, the member gets an ``ExtractionError`` that names that
        component, a per-member failure like any other. A non-directory that was in
        ``dest`` before the run met ``OverwritePolicy`` before this
        (``_make_room_for_parents``), so any other failure is the filesystem's, and
        propagates unchanged.
        """
        # The components that do not exist yet: this run creates them, so a directory
        # member naming one later is not the caller's (``_is_callers_directory``).
        missing: list[Path] = []
        probe = path
        while probe != probe.parent and not os.path.lexists(long_path(probe)):
            missing.append(probe)
            probe = probe.parent
        try:
            os.makedirs(long_path(path), exist_ok=True)
        except (FileExistsError, NotADirectoryError, FileNotFoundError) as exc:
            # FileNotFoundError too: Windows can report a directory created under a file
            # as a missing path. Nearest the root first, because that component is the
            # one that stops the rest.
            for component in (*reversed(path.parents), path):
                if component in self._state.written_paths and not os.path.isdir(
                    long_path(component)
                ):
                    raise ExtractionError(
                        f"Cannot create {quoted(member.name)}: "
                        f"{quoted(self._rel_name(component))} is a "
                        "non-directory already extracted from this archive",
                        member_name=member.name,
                    ) from exc
            raise
        self._state.created_dirs.update(missing)
        for component in missing:
            self._note_run_directory(component)

    def _is_callers_directory(self, path: Path) -> bool:
        """Whether ``path`` is a directory that was there before this run: the
        destination root unless this run created it, or a directory this run neither
        wrote nor created (``_is_run_directory``). The root is decided by name, as it
        may be the caller's symlink to a directory."""
        state = self._state
        if path == state.dest:
            return path not in state.created_dirs
        try:
            st = os.lstat(long_path(path))
        except OSError:
            return False
        return stat.S_ISDIR(st.st_mode) and not self._is_run_directory(path, st)

    def _note_run_directory(self, path: Path) -> None:
        """Record that this run wrote or created the directory at ``path``."""
        self._state.run_dirs.setdefault(self._physical_key(path), set()).add(
            self._physical_path(path)
        )

    def _is_run_directory(self, path: Path, st: os.stat_result) -> bool:
        """Whether the directory at ``path`` (``st`` its ``lstat``) is one this run
        wrote or created, under any spelling: through a symlink the archive created
        (the same physical path), or a case variant on a case-insensitive filesystem
        under every policy (one recorded under the same ``_physical_key`` that is the
        same directory). Where
        the filesystem reports inode 0, a case variant cannot be told from another
        directory and is taken for the caller's, which keeps its mode."""
        recorded = self._state.run_dirs.get(self._physical_key(path))
        if not recorded:
            return False
        if self._physical_path(path) in recorded:
            return True
        if st.st_ino == 0:
            return False
        for other in recorded:
            try:
                if os.path.samestat(st, os.lstat(long_path(other))):
                    return True
            except OSError:
                continue
        return False

    def _prepare_destination(
        self, member: ArchiveMember, dest_path: Path, *, atomic: bool = False
    ) -> bool:
        """Apply the OverwritePolicy. Returns True to proceed with creation, False to
        skip (SKIP over an existing entry). Raises ExtractionError under ERROR when the
        entry exists, and under REPLACE or RENAME when it is a directory that is not
        empty. Uses lstat semantics so a dangling symlink counts as existing.

        ``atomic=True`` is used for FILE and HARDLINK writes, which land via
        ``os.replace()`` over the destination (see ``_write_file_atomic`` /
        ``_place_link``): under REPLACE this leaves an existing **file or symlink** in
        place for that atomic swap (so the old data survives until the new entry is fully
        built, and a symlink is replaced, never written through), removing only an
        existing empty **directory** first — ``os.replace`` cannot overwrite a directory.
        ``atomic=False`` (DIR / SYMLINK) keeps the plain unlink-then-create: a symlink
        must be created at its final name for the escape re-validation's cycle check, and
        a directory cannot be renamed over a file at all."""
        try:
            st = os.lstat(long_path(dest_path))
        except (OSError, ValueError):  # the same errors ``os.path.lexists`` absorbs
            return True
        existing = st.st_mode
        # Replacing a symlink or a directory can move where an earlier link resolves;
        # the member's handler rechecks the links once the member is done.
        moves_links = stat.S_ISLNK(existing) or stat.S_ISDIR(existing)
        # A parked copy of this name (``_park_earlier_copies``) skips the policy: the
        # member replaces it under any policy, as random access would never have
        # written it.
        parked = dest_path in self._current.parked
        # A member the walk can still change keeps the caller's entry it replaces
        # (``_set_aside``): the end of the pass may not select it.
        # On Windows a read-only entry of the caller's is not moved: replacing it
        # fails, as it does from a file (``_readonly_cleared``).
        aside = (
            not parked
            and self._current.provisional
            and self._is_callers_entry(dest_path, st)
            and not (_WINDOWS and not st.st_mode & stat.S_IWRITE)
        )
        if not parked:
            # A real directory recreated as a directory is fine under any policy.
            if member.type == MemberType.DIRECTORY and stat.S_ISDIR(existing):
                return True
            if self._overwrite is OverwritePolicy.ERROR:
                raise self._already_exists(dest_path, member)
            if self._overwrite is OverwritePolicy.SKIP:
                return False
            if (
                self._overwrite is not OverwritePolicy.REPLACE
                and self._overwrite is not OverwritePolicy.RENAME
            ):
                # What follows destroys the existing entry, so a policy nobody taught
                # this chain must not inherit it: an unknown member stops here.
                assert_never(self._overwrite)
            # REPLACE (RENAME members are pre-resolved to a free path, a directory
            # member too when a file or symlink holds its name, so they reach an
            # existing entry here only if it appeared after that check): never
            # write-through a symlink. For an atomic FILE write, os.replace handles a
            # file/symlink target atomically, so only a real directory must be removed
            # up front. Otherwise unlink a symlink/file (bytes never follow the link). A
            # directory is removed only when it is empty.
            if stat.S_ISDIR(existing):
                # Only an empty directory is removed, as GNU tar does without
                # --recursive-unlink. Removing a tree takes the members this run wrote
                # into it, which would then still report EXTRACTED, and the caller's
                # own files when the directory was already there.
                with os.scandir(long_path(dest_path)) as entries:
                    if next(entries, None) is not None:
                        raise ExtractionError(
                            f"Destination is a directory that is not empty: "
                            f"{display_path(dest_path)}",
                            member_name=member.name,
                        )
                self._note_link_change(dest_path)
                if aside:
                    self._set_aside(dest_path)
                else:
                    with self._readonly_cleared(dest_path):
                        os.rmdir(long_path(dest_path))
                # Its member's metadata has no directory left to go on.
                self._drop_pending_dir(dest_path)
                self._current.removed_existing = True
                # A directory member of this run that wrote it no longer has it. This is
                # not a collision event (directories are never claimed), so only the
                # result is revised; ``collided_with`` is untouched. Only under REPLACE:
                # RENAME reaches this arm only after a filesystem race, with nothing of
                # this run's here, and OVERWRITTEN is a REPLACE outcome.
                if self._overwrite is OverwritePolicy.REPLACE:
                    self._revise_removed_directory(dest_path)
                self._forget_source_path(dest_path)
                return True
        # Reached by a parked copy of this name (never a directory), and by REPLACE or
        # RENAME over an existing file or symlink.
        if moves_links:
            self._note_link_change(dest_path)
        if aside:
            self._set_aside(dest_path)
            self._current.removed_existing = True
        elif not atomic:
            with self._readonly_cleared(dest_path):
                os.unlink(long_path(dest_path))
            # A parked copy's result is already SUPERSEDED: nothing to revise.
            if not parked:
                self._current.removed_existing = True
        self._forget_source_path(dest_path)
        return True

    def _write_file_atomic(
        self, stream: BinaryIO | None, dest_path: Path, member: ArchiveMember
    ) -> None:
        """Write a FILE by streaming into a temp sibling, applying ``member``'s metadata,
        then ``os.replace()``-ing it onto ``dest_path`` — atomic, so a mid-stream failure
        never truncates or removes an existing destination (only the temp is discarded),
        and the target name never appears half-written. The temp lives in the destination
        directory so the rename stays on one filesystem. When the orphan pass materializes
        an unselected hardlink source, ``member`` is the *link's* transformed copy, so the
        file carrying the content gets the link's (policy-capped) mode, not the source's.

        Under TRUSTED, a member with no stored mode gets the mode an ordinary file
        creation would, ``0o666`` less the umask, rather than ``mkstemp``'s private
        ``0o600``. Nothing chmods it afterwards, so the temp's creation mode is the file's
        final mode. STRICT and STANDARD give a mode-less member their own default instead
        (see ``_effective_mode``), so this arm is TRUSTED's alone."""
        # Every check that could still skip the member has passed, so a member whose
        # recorded size is already over the ratio is refused before any temp file
        # exists.
        self._state.tracker.check_recorded_size()
        if self._effective_mode(member) is None:
            tmp = self._temp_sibling(dest_path.parent)
            fd = _open_new_file(tmp, 0o666)
        else:
            # mkstemp hands back an already-open fd; write straight into it (no
            # close+reopen). Its 0o600 keeps the content private until the chmod below.
            fd, tmp_name = tempfile.mkstemp(
                dir=long_path(dest_path.parent), prefix=_TMP_PREFIX
            )
            tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as dst:
                # A dry run reads and verifies the body like a real one; only the bytes
                # go nowhere, and the file is left empty.
                self._copy_to_fileobj(
                    stream,
                    dst if self._dry is None else self._dry.sink,
                    _CopyCounter(self._state.tracker)
                    if self._current.recopy
                    else self._state.tracker,
                    emit_progress=self._current.emit_progress,
                )
            self._apply_metadata(tmp, member)
            self._swap_into_place(tmp, dest_path, member)
        except BaseException:
            # os.replace consumes the temp on success; on any earlier failure remove it so
            # no .archivey-tmp-* file is left behind (the existing destination is untouched).
            try:
                os.unlink(long_path(tmp))
            except FileNotFoundError:
                pass
            except OSError as exc:
                self._rebase_os_error(exc)
                logger.warning(
                    "Could not remove %r, staged for %r: %s",
                    str(self._shown(tmp)),
                    member.name,
                    exc,
                )
            raise

    @staticmethod
    def _copy_to_fileobj(
        stream: BinaryIO | None,
        dst: BinaryIO,
        tracker: BombTracker | _CopyCounter | None,
        emit_progress: Callable[[], None] | None = None,
    ) -> None:
        """Copy ``stream`` into the already-open ``dst`` in chunks, counting each toward the
        bomb tracker. Cleanup of a partial ``dst`` on failure is the caller's job.

        When ``emit_progress`` is set, it is invoked after each full ``_CHUNK`` so callers
        can report intra-member progress (~1 callback/MiB). Short final chunks are left to
        the coordinator's terminal per-member report so sub-chunk members keep a single
        callback. Pass ``None`` (the default) for the zero-cost path.
        """
        if stream is None:
            # A FILE reaching the writer with no data stream is a backend bug, not a valid
            # empty file (a zero-byte FILE still yields a real, empty stream). Silently
            # writing an empty file here would mask that bug, so raise instead. Both FILE
            # write paths (the main pass and the orphaned-source second pass) funnel through
            # here, so this one guard covers them; it surfaces as a per-member failure via
            # the coordinator's OnError handling, attributed to the member being written.
            raise ExtractionError(
                "FILE member has no data stream to extract (backend returned stream=None)"
            )
        while True:
            chunk = stream.read(_CHUNK)
            if not chunk:
                break
            if tracker is not None:
                tracker.count(len(chunk))
            dst.write(chunk)
            # Full chunks only: the terminal report covers the final (possibly short) chunk
            # so a sub-chunk member still gets exactly one callback.
            if emit_progress is not None and len(chunk) == _CHUNK:
                emit_progress()

    def _place_link(
        self,
        source_id: int,
        candidates: list[Path],
        new_path: Path,
        member: ArchiveMember,
    ) -> None:
        """Create ``new_path`` as a hardlink to the source's content, trying
        ``candidates`` (the source's recorded on-disk paths, taken before
        ``_make_room`` cleared ``new_path``) newest first; when none takes the link for
        a reason of its own (see ``_link_refused_here``), copy from an existing path.
        Records ``new_path`` under ``source_id`` so a later same-device link can reuse
        it — which is what keeps a fan-out across one device boundary to a single copy
        per device rather than one per link, and a fan-out past the link-count limit to
        one copy per full file. When ``new_path`` already named the source's file, it
        is not recorded twice: the name existed, so ``_prepare_destination`` dropped it
        from the list (``_forget_source_path``) before this method ran.

        The first path at the link-count limit ends the search with a copy. The paths
        recorded before it are older names of the same file, of a file that filled up
        before it, or of a copy on another device, which could not take this link
        either (``EMLINK`` means the destination is on the full file's device). So
        trying each would fail too, and would cost a failed call per recorded path for
        every later link: quadratic in the number of links an archive declares. A file
        that dropped below the limit since (a later member replaced one of its names) is
        not tried again, which costs at most an extra copy.

        The copy is a real write of the source's full size, so it goes through
        ``tracker`` and counts toward ``max_extracted_bytes``; a link adds no bytes.
        It is written into a new file, not ``shutil.copy2``'d, so it carries this
        member's metadata alone, never the source file's mode.

        The link is built at a temp sibling and ``os.replace``d into place, the same way
        a FILE write lands: ``os.link`` needs a free name, so the alternative is to
        unlink an existing destination first and leave a hole if the link then fails.
        ``os.replace`` moves the link itself and never follows the entry it replaces, so
        a destination symlink is replaced rather than written through."""
        if new_path in candidates and _is_regular_file(new_path):
            # The link landed on a path that already holds its source's content (a
            # case-folded name under REPLACE). The callers' ``_make_room`` ran with
            # ``atomic=True``, which leaves an existing file for the swap rather than
            # unlinking it, so the file is still in place and there is nothing to link.
            self._state.source_paths.setdefault(source_id, []).append(new_path)
            return
        # Each path was a regular file this run wrote, and ``_forget_source_path``
        # drops one once another member replaces it. ``candidates`` was taken before
        # the callers' ``_make_room``, so the one path that can be stale here is
        # ``new_path``: the early return above takes it while it is a regular file,
        # and the check below skips it when it is not. Checked again here because
        # ``os.link`` follows a symlink (``follow_symlinks=False`` is not available on
        # every platform): a link made through one would name a file this run did not
        # write. Checked one path at a time as the loop reaches it, so the common case,
        # where the newest path links, checks one path however many links name it.
        tmp = self._temp_sibling(new_path.parent)
        try:
            copied = False
            linked = False
            at_link_limit = False
            copy_from: Path | None = None
            for candidate in reversed(candidates):
                if not _is_regular_file(candidate):
                    continue
                if copy_from is None:
                    copy_from = candidate
                try:
                    os.link(long_path(candidate), long_path(tmp))
                except OSError as exc:
                    if not _link_refused_here(exc):
                        raise
                    if _at_link_limit(exc):
                        at_link_limit = True
                        break
                    continue
                linked = True
                break
            if not linked:
                if copy_from is None:
                    raise ExtractionError(
                        "Hardlink source is no longer on disk as a regular file",
                        member_name=member.name,
                    )
                # No usable path took a link: fall back to a copy.
                # Created private when a mode follows (as mkstemp would), at the ordinary
                # creation mode when none does, the same as a FILE write.
                create_mode = (
                    0o600 if self._effective_mode(member) is not None else 0o666
                )
                with (
                    open(long_path(copy_from), "rb") as src,
                    os.fdopen(_open_new_file(tmp, create_mode), "wb") as dst,
                ):
                    while chunk := src.read(_CHUNK):
                        self._state.tracker.count_copy(
                            len(chunk), at_link_limit=at_link_limit
                        )
                        dst.write(chunk)
                copied = True
            if copied:
                # Applied before the swap, so the final name never appears with the
                # source's metadata instead of this member's.
                self._apply_metadata(tmp, member)
            self._swap_into_place(tmp, new_path, member)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(long_path(tmp))
            raise
        self._state.source_paths.setdefault(source_id, []).append(new_path)

    def _already_exists(self, dest: Path, member: ArchiveMember) -> ExtractionError:
        """The error ``OverwritePolicy.ERROR`` raises for an entry at ``dest``, from the
        existence check and from a landing that finds the name taken after it."""
        return ExtractionError(
            f"Destination already exists: {display_path(self._shown(dest))}",
            member_name=member.name,
        )

    def _taken_after_check(self, dest: Path, *, atomic: bool) -> bool:
        """Whether, under ``OverwritePolicy.ERROR``, ``dest`` is now taken by something
        that was not there at the existence check: another writer into the same folder,
        such as a second extraction. Asked by every landing whose creation refuses a
        taken name: the ``os.replace`` of a FILE or HARDLINK (``atomic``), and the
        ``os.symlink`` of a SYMLINK and the ``os.makedirs`` of a DIRECTORY.

        Under ERROR the check lets a member through only to a free name, to a copy this
        run parked (``_park_earlier_copies``), or, for a DIRECTORY member, to an existing
        directory. An atomic landing replaces a parked copy where it stands, so that
        copy does not count (``_occupied``); the other landings removed it in
        ``_prepare_destination``. ``os.makedirs(exist_ok=True)`` never raises for an
        existing directory, so a ``FileExistsError`` there means a non-directory. So
        anything a landing is refused over appeared after the check. Under the other policies a landing that fails this way is not
        handled: its error goes out as it is."""
        if self._overwrite is not OverwritePolicy.ERROR:
            return False
        return self._occupied(dest) if atomic else os.path.lexists(long_path(dest))

    def _swap_into_place(self, tmp: Path, dest: Path, member: ArchiveMember) -> None:
        """Move the staged temp ``tmp`` onto ``dest`` with ``os.replace``, the last step
        of every atomic write (``_write_file_atomic``, ``_place_link``).

        POSIX ``rename(2)`` does nothing, and reports success, when ``tmp`` and ``dest``
        are already names of the same file. That happens when ``_place_link`` links a
        file onto another of its own names (a hard link listed twice, or one REPLACE
        routes onto a link to the same file). ``dest`` then already names the right
        file, so only the temp name is left, and it is removed here. After a real
        rename the temp name is gone and the unlink finds nothing.

        On Windows the same call does not fail: the tests for this case
        (``test_link_onto_a_name_of_the_same_file_leaves_no_temp``) pass there. Whether
        Windows really renames or leaves the temp as POSIX does is not pinned; the
        unlink below covers both.

        Any error from the unlink is ignored: ``os.replace`` has already put the member
        in place, and a temp name left behind is a stray the docs call safe to delete,
        not a failed write. The unlink stays inside the ``_readonly_cleared`` block. On
        Windows that block's exit puts the read-only attribute back on the other names of
        ``dest``'s file, and in the same-file case the temp is one of them, so an unlink
        after the block would fail.

        A rename refused onto an entry that appeared after the existence check is, under
        ERROR, the clash that check reports, and raises its error
        (``_taken_after_check``). Windows refuses it with access denied (seen with two
        extractions into one folder); POSIX refuses it in a sticky directory when someone
        else owns the entry, and otherwise replaces it."""
        with self._readonly_cleared(dest):
            try:
                os.replace(long_path(tmp), long_path(dest))
            except PermissionError as exc:
                if not self._taken_after_check(dest, atomic=True):
                    raise
                raise self._already_exists(dest, member) from exc
            with contextlib.suppress(OSError):
                os.unlink(long_path(tmp))

    @staticmethod
    def _temp_sibling(parent: Path) -> Path:
        """A free ``.archivey-tmp-*`` name in ``parent`` for an atomic swap.

        Unlike ``_write_file_atomic`` this cannot use ``mkstemp``: ``os.link`` needs a
        name that does *not* exist. The destination is trusted at rest (see the threat
        model), and ``os.link`` / ``os.replace`` fail loudly on a name that appeared
        underneath us, so a uuid4 name is enough."""
        while True:
            candidate = parent / f"{_TMP_PREFIX}{uuid.uuid4().hex}"
            if not os.path.lexists(long_path(candidate)):
                return candidate

    def _defer_directory_metadata(self, path: Path, member: ArchiveMember) -> None:
        """Keep ``member``'s ownership, mode and times for the directory just made at
        ``path`` until the run ends (``_apply_directory_metadata``).

        Applied at once, a stored mode without owner write or search permission
        (``0o555``, ``0o644``) refused every member inside the directory to a non-root
        user, and each entry written inside moved the directory's mtime. GNU tar,
        bsdtar and Python's ``tarfile`` defer them the same way.
        """
        try:
            st = os.lstat(long_path(path))
        except OSError:
            return
        physical = self._physical_path(path)
        depth = self._rel_name(physical).count("/")
        pending = self._state.pending_dirs.setdefault(self._physical_key(path), {})
        # Moved to the end, so where two spellings reach one directory (a case
        # variant), the member written last is applied last.
        pending.pop(physical, None)
        pending[physical] = ((st.st_dev, st.st_ino), depth, member)

    def _drop_pending_dir(self, path: Path) -> None:
        """Drop the deferred metadata of the directory at ``path``, which is being
        removed, under whichever spelling it was written.

        Found by ``_physical_key``, under every policy: the entry at the same physical
        path is dropped, and so is one under a case variant that is gone from disk. A
        casefolded key also covers another directory on a
        case-sensitive filesystem (``X/`` beside ``x/``); that one is still there, and
        keeps its entry."""
        key = self._physical_key(path)
        pending = self._state.pending_dirs.get(key)
        if not pending:
            return
        physical = self._physical_path(path)
        for other in list(pending):
            if other == physical or not os.path.lexists(long_path(other)):
                del pending[other]
        if not pending:
            del self._state.pending_dirs[key]

    def _apply_directory_metadata(self) -> None:
        """Apply the deferred directory metadata (``_defer_directory_metadata``),
        deepest first, so a parent's mode never stops the run from reaching a
        directory inside it. Best-effort, as ``_apply_metadata`` is.

        A directory is changed only when the entry at its path is still the directory
        this run made: a later member may have replaced it, with a symlink to another
        directory for example. Each ``os.rmdir`` this run makes also drops the entry
        recorded at that path, which matters where a filesystem reports inode 0 and
        this check cannot tell two directories apart. Where the platform can, the
        directory is opened without following a symlink and changed through that
        descriptor, so nothing put at the path after the check is changed either.
        Elsewhere (Windows), or when the directory cannot be opened (a umask without
        owner read), the check is an ``lstat`` and the change goes by path.
        """
        pending = self._state.pending_dirs
        # Windows has no ``os.chown`` and takes no descriptor here.
        by_fd = hasattr(os, "chown") and all(
            f in os.supports_fd for f in (os.chmod, os.utime, os.chown)
        )
        flags = os.O_RDONLY
        for extra in ("O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC"):
            flags |= getattr(os, extra, 0)

        def is_ours(st: os.stat_result, identity: tuple[int, int]) -> bool:
            return stat.S_ISDIR(st.st_mode) and (st.st_dev, st.st_ino) == identity

        entries = [item for by_key in pending.values() for item in by_key.items()]
        for path, (identity, _depth, member) in sorted(
            entries, key=lambda item: item[1][1], reverse=True
        ):
            if by_fd:
                try:
                    fd = os.open(long_path(path), flags)
                except PermissionError:
                    pass
                except OSError:
                    continue
                else:
                    try:
                        if is_ours(os.fstat(fd), identity):
                            self._apply_metadata(fd, member)
                    finally:
                        os.close(fd)
                    continue
            try:
                if is_ours(os.lstat(long_path(path)), identity):
                    self._apply_metadata(path, member)
            except OSError:
                pass
        pending.clear()

    def _effective_mode(self, member: ArchiveMember) -> int | None:
        """The mode to give what ``member`` writes; ``None`` means the creation default.

        The policy transforms fill in a missing mode, but a user filter runs after them
        and may hand back ``mode=None``. STRICT and STANDARD still owe their documented
        default then (file ``0o644``, dir ``0o755``), not whatever the umask leaves.
        Only TRUSTED means "as stored", and a member that stored nothing gets the
        creation default.
        """
        if member.mode is not None or self._policy is ExtractionPolicy.TRUSTED:
            return member.mode
        return 0o755 if member.is_dir else 0o644

    def _apply_metadata(self, path: Path | int, member: ArchiveMember) -> None:
        """Best-effort ownership / mode / mtime. Failures are swallowed (best-effort).
        ``path`` may be an open descriptor, where the platform takes one.

        Ownership goes first: Linux ``chown`` clears setuid/setgid on a non-directory
        even when root calls it, so a ``chmod`` before it would lose exactly the bits
        TRUSTED promises to keep. GNU tar orders the two the same way for this reason.
        """
        target = path if isinstance(path, int) else long_path(path)
        # Ownership only under TRUSTED as root (STRICT/STANDARD never chown). A
        # negative id is skipped (-1 means "leave unchanged" to chown), and one past
        # uid_t raises OverflowError before any syscall: both are archive content.
        if (
            self._policy is ExtractionPolicy.TRUSTED
            and member.uid is not None
            and member.gid is not None
            and member.uid >= 0
            and member.gid >= 0
            and hasattr(os, "geteuid")
            and os.geteuid() == 0
        ):
            try:
                os.chown(target, member.uid, member.gid)
            except (OSError, OverflowError):
                pass
        mode = self._effective_mode(member)
        if mode is not None:
            try:
                os.chmod(target, mode)
            except OSError:
                pass
        if member.modified is not None:
            try:
                ts = member.modified.timestamp()
                os.utime(target, (ts, ts))
            except (OSError, ValueError, OverflowError):
                pass

    # --- progress / misc -----------------------------------------------------------

    def _estimate_total_bytes(
        self, all_members: list[ArchiveMember] | None
    ) -> int | None:
        if all_members is None:
            return None
        if any(m.is_file and m.size is None for m in all_members):
            return None
        return sum(m.size or 0 for m in all_members if m.is_file)

    def _report_progress(
        self, member: ArchiveMember, members_done: int, member_bytes_written: int
    ) -> None:
        if self._on_progress is None:
            return
        state = self._state
        self._on_progress(
            ExtractionProgress(
                member=member,
                bytes_written=state.tracker.total_bytes,
                total_bytes_estimated=state.total_estimate,
                members_done=members_done,
                members_total=state.members_total,
                member_bytes_written=member_bytes_written,
                members_extracted=state.members_extracted,
                members_blocked=state.members_blocked,
            )
        )

    @staticmethod
    def _close(stream: BinaryIO | None) -> None:
        """Close a member stream once its result is recorded.

        Best-effort: the member's result already stands, and every content verdict fired
        from a read (ADR 0014), so a close failure has no result left to change. An
        interrupt still propagates.
        """
        if stream is not None:
            try:
                stream.close()
            except Exception:  # noqa: BLE001 - teardown hygiene; the result is recorded
                pass
