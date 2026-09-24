"""Directory pseudo-backend: presents a filesystem directory as an ArchiveReader.

Uniformity principle (`format-directory`): this reader is never more lenient than
archive readers. Declared member-stream capabilities (`MemberStreams.CONCURRENT` /
`SEEKABLE`) gate concurrent opens and seekability here exactly as for ZIP/TAR/ISO —
the directory backend exists to keep archive-vs-directory code uniform, not to offer a
more permissive escape hatch.
"""

from __future__ import annotations

import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Mapping

from archivey.config import ArchiveyConfig
from archivey.cost import (
    AccessCost,
    CostReceipt,
    ListingCost,
    StreamCapability,
)
from archivey.diagnostics import DiagnosticCode, ScanRaceContext
from archivey.escaping import quoted
from archivey.internal.base_reader import (
    BaseArchiveReader,
    ReadBackend,
    reject_start_offset,
)
from archivey.internal.diagnostics_collector import DiagnosticCollector
from archivey.internal.logs import backends as logger
from archivey.internal.open_site import OpenSite
from archivey.internal.password import _PasswordCandidates
from archivey.internal.registry import register_reader
from archivey.internal.source import ArchiveSource
from archivey.internal.streams.archive_stream import ArchiveStream
from archivey.types import (
    EXTRA_IS_JUNCTION,
    EXTRA_IS_REPARSE_POINT,
    ArchiveFormat,
    ArchiveInfo,
    ArchiveMember,
    MagicSignature,
    MemberExtra,
    MemberStreams,
    MemberType,
)


def _stat_datetime(ts: float) -> datetime | None:
    """A stat timestamp as an aware UTC datetime, or ``None`` when out of range.

    ``datetime.fromtimestamp`` raises ``ValueError``/``OverflowError`` on POSIX and
    ``OSError`` on Windows (its ``gmtime()`` rejects pre-1970/huge values) for
    out-of-range inputs, which a network/FUSE filesystem can genuinely report.
    """
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def _link_extra(member_type: MemberType, is_junction: bool) -> MemberExtra:
    """The junction / reparse-point keys for a live filesystem entry.

    On Windows a symlink *is* a reparse point — there is no other kind — so the flag
    follows from the platform rather than from anything stored. Elsewhere a symlink is
    a POSIX one and neither key applies, junctions included: `os.DirEntry.is_junction`
    only reports true on Windows.
    """
    extra = MemberExtra()
    if is_junction:
        extra[EXTRA_IS_JUNCTION] = True
    if member_type == MemberType.SYMLINK and os.name == "nt":
        extra[EXTRA_IS_REPARSE_POINT] = True
    return extra


def _is_junction(entry: os.DirEntry[str]) -> bool:
    """True if a scandir entry is a Windows NTFS junction.

    ``os.DirEntry.is_junction()`` only exists on Python 3.12+; on older interpreters
    (and on every non-Windows platform, where junctions don't exist) this returns False.
    """
    is_junction = getattr(entry, "is_junction", None)
    return bool(is_junction()) if is_junction is not None else False


class DirectoryReader(BaseArchiveReader):
    """Reads a filesystem directory as an archive."""

    _SUPPORTS_RANDOM_ACCESS = True
    # A filesystem directory has no O(1) upfront index: enumerating members is an
    # os.scandir walk (a scan). So this is False (like plain TAR) — members_report_if_available()
    # returns None rather than triggering an uncached walk on every call, and the walk only runs
    # once under the materialization election (which also removes the free-threaded cache race on
    # _uname_cache/_gname_cache). See format-directory.
    _MEMBER_LIST_UPFRONT = False

    def __init__(
        self,
        root: Path,
        streaming: bool,
        archive_name: str | None,
        config: ArchiveyConfig,
        collector: DiagnosticCollector | None = None,
        member_streams: MemberStreams = MemberStreams(0),
        open_site: OpenSite | None = None,
    ) -> None:
        super().__init__(
            ArchiveFormat.DIRECTORY,
            streaming,
            archive_name,
            config,
            collector=collector,
            member_streams=member_streams,
            open_site=open_site,
        )
        self._root = root
        # uid/gid -> name caches: most entries in a tree share an owner/group, and
        # pwd/grp lookups hit the system database (nss) on every call, so we memoize.
        self._uname_cache: dict[int, str | None] = {}
        self._gname_cache: dict[int, str | None] = {}

    def _iter_members(self) -> Iterator[ArchiveMember]:
        # An explicit stack rather than recursion: a tree deeper than the interpreter's
        # recursion limit (easy to produce by extracting an archive of `a/a/a/…/f`)
        # must list like any other, and a member is yielded straight to the caller
        # instead of being passed up one generator frame per level of depth.
        #
        # Order is a depth-first preorder: at each level the non-directory entries
        # (sorted by name), then each subdirectory's own member followed by its whole
        # subtree. Subdirectories are pushed in reverse so they pop in name order.
        pending: list[tuple[ArchiveMember, Path]] = []
        yield from self._scan_level(self._root, "", pending)
        while pending:
            member, path = pending.pop()
            yield member
            yield from self._scan_level(path, member.name, pending)

    def _scan_level(
        self,
        directory: Path,
        rel_prefix: str,
        pending: list[tuple[ArchiveMember, Path]],
    ) -> Iterator[ArchiveMember]:
        """Yield one directory's non-directory entries; push its subdirectories.

        The subdirectories go onto ``pending`` (the walk's stack, see `_iter_members`)
        in reverse name order, so the caller pops them in name order. They are pushed
        only when this generator is exhausted: a caller that stops early (``break``,
        ``islice``, a peek with ``next``) leaves them off the stack and silently loses
        those subtrees, so drain it before reading ``pending``.
        """
        # os.scandir yields DirEntry objects whose stat() is cached, so we avoid a
        # separate os.stat()/os.lstat() syscall per entry.
        #
        # Errors: a live filesystem can change under the walk, so an entry (or a whole
        # subdirectory) that vanished between being listed and being inspected is
        # skipped with a warning — a race, not an error. Every other OSError (permission
        # denied, I/O failure) propagates unchanged: silently dropping entries would
        # present an incomplete listing as complete (see the design authority in
        # `openspec/project.md` — no silent guesses — and `error-handling`'s rule that
        # genuine I/O errors are never swallowed or reclassified).
        try:
            with os.scandir(directory) as it:
                entries = sorted(it, key=lambda e: e.name)
        except FileNotFoundError:
            relative = rel_prefix.rstrip("/") or "."
            self._diagnostics_collector.emit(
                code=DiagnosticCode.SCAN_DIRECTORY_VANISHED,
                message=f"Directory vanished during scan, skipping: {quoted(str(directory))}",
                context=ScanRaceContext(
                    archive_name=self._archive_name,
                    relative_path=relative,
                    entry_kind="directory",
                ),
                logger=logger,
            )
            return

        # Emit all non-directory entries at this level now; the subdirectories are
        # collected and handed to the walk's stack, so a directory's own files come
        # before anything inside its children.
        subdirs: list[tuple[ArchiveMember, Path]] = []
        for entry in entries:
            rel_path = rel_prefix + entry.name
            # `stat` and `readlink` race the same window on the same entry — listed by
            # scandir, gone before we inspect it — so both sit inside the one guard.
            #
            # The type comes from that `lstat`, not from scandir's snapshot, so an entry
            # replaced in the window (a symlink swapped for a file) is typed as what is
            # there now and `readlink` only runs on something that is a link. Only the
            # junction test still reads the entry: a junction's reparse tag is not in
            # `st_mode`, which reports it as a directory.
            try:
                st = entry.stat(follow_symlinks=False)
                is_symlink = stat.S_ISLNK(st.st_mode)
                is_junction = not is_symlink and _is_junction(entry)
                link_target = (
                    self._read_link_target(entry.path)
                    if is_symlink or is_junction
                    else None
                )
            except FileNotFoundError:
                self._diagnostics_collector.emit(
                    code=DiagnosticCode.SCAN_ENTRY_VANISHED,
                    message=f"Entry vanished during scan, skipping: {quoted(entry.path)}",
                    context=ScanRaceContext(
                        archive_name=self._archive_name,
                        relative_path=rel_path,
                        entry_kind="entry",
                    ),
                    logger=logger,
                )
                continue

            if is_symlink:
                yield self._make_member(rel_path, st, MemberType.SYMLINK, link_target)
            elif is_junction:
                # A Windows NTFS junction points at a directory but is a reparse point,
                # not a real subtree to walk — surface it as a symlink-like leaf (flagged
                # via extra[EXTRA_IS_JUNCTION]) and do NOT recurse through it.
                yield self._make_member(
                    rel_path,
                    st,
                    MemberType.SYMLINK,
                    link_target,
                    is_junction=True,
                )
            elif stat.S_ISDIR(st.st_mode):
                member = self._make_member(
                    rel_path + "/", st, MemberType.DIRECTORY, None
                )
                subdirs.append((member, Path(entry.path)))
            elif stat.S_ISREG(st.st_mode):
                yield self._make_member(rel_path, st, MemberType.FILE, None)
            else:
                yield self._make_member(rel_path, st, MemberType.OTHER, None)

        pending.extend(reversed(subdirs))

    @staticmethod
    def _read_link_target(path: str) -> str:
        """The symlink/junction target, with separators normalized like member names.

        On Windows ``os.readlink`` returns the target with ``\\`` separators; convert
        them to ``/`` so link targets live in the same namespace as member names (where
        the separator conversion is likewise applied only for Windows-origin paths — on
        POSIX a backslash is a literal filename character and is kept).
        """
        target = os.readlink(path)
        if os.name == "nt":
            target = target.replace("\\", "/")
        return target

    def _make_member(
        self,
        name: str,
        st: os.stat_result,
        member_type: MemberType,
        link_target: str | None,
        is_junction: bool = False,
    ) -> ArchiveMember:
        # `name` is built from live filesystem entries (already "/"-separated, no
        # "."/".."/leading-slash components), so it is normalize_member_name()-clean by
        # construction — unlike names decoded from an archive, which every real backend
        # must route through that helper.
        #
        # Timestamps are guarded like every backend's (see internal/timestamps.py): a
        # network/FUSE filesystem can report out-of-range values, and on Windows even
        # tz-aware fromtimestamp raises OSError for them — one bad file must not sink
        # the whole walk.
        modified = _stat_datetime(st.st_mtime)
        accessed = _stat_datetime(st.st_atime)
        # st_birthtime is the true creation time but only exists on some platforms
        # (macOS/BSD, Windows, recent Linux); st_ctime is metadata-change time on
        # Unix, NOT creation, so we never use it for `created`. Hence the getattr.
        birthtime = getattr(st, "st_birthtime", None)
        created = _stat_datetime(birthtime) if birthtime is not None else None

        # os.stat_result always defines st_uid/st_gid (both 0 on Windows), so no
        # getattr guard is needed.
        uid = st.st_uid
        gid = st.st_gid

        size = st.st_size if member_type == MemberType.FILE else None

        return ArchiveMember(
            type=member_type,
            name=name,
            raw_name=name.encode("utf-8", errors="surrogateescape"),
            size=size,
            compressed_size=size,  # no compression
            modified=modified,
            accessed=accessed,
            created=created,
            mode=stat.S_IMODE(st.st_mode),
            uid=uid,
            gid=gid,
            uname=self._lookup_uname(uid),
            gname=self._lookup_gname(gid),
            link_target=link_target,
            extra=_link_extra(member_type, is_junction),
        )

    def _lookup_uname(self, uid: int) -> str | None:
        if uid not in self._uname_cache:
            try:
                import pwd

                self._uname_cache[uid] = pwd.getpwuid(uid).pw_name
            except (ImportError, KeyError):
                self._uname_cache[uid] = None
        return self._uname_cache[uid]

    def _lookup_gname(self, gid: int) -> str | None:
        if gid not in self._gname_cache:
            try:
                import grp

                self._gname_cache[gid] = grp.getgrgid(gid).gr_name
            except (ImportError, KeyError):
                self._gname_cache[gid] = None
        return self._gname_cache[gid]

    def _open_member(self, member: ArchiveMember) -> ArchiveStream:
        full_path = self._root / member.name
        # Wrapped like every backend's member stream (the uniform-handle contract): the
        # directory backend has no translator (a genuine OSError propagates unchanged),
        # but the caller still gets the same ArchiveStream handle type — with its `size`
        # advertisement — as for any other format.
        raw = open(full_path, "rb")  # noqa: SIM115
        return self._wrap_member_stream(raw, member.name, size=member.size)

    def _get_archive_info(self) -> ArchiveInfo:
        cost = CostReceipt(
            # A directory has no O(1) index; listing walks the tree header-to-header
            # (an os.scandir walk) without decompressing, exactly like a plain TAR.
            listing_cost=ListingCost.REQUIRES_SCANNING,
            access_cost=AccessCost.DIRECT,
            stream_capability=StreamCapability.SEEKABLE,
            solid_block_count=None,
        )
        return ArchiveInfo(
            format=ArchiveFormat.DIRECTORY,
            format_version=None,
            is_solid=False,
            member_count=None,  # unknown until walked
            comment=None,
            is_encrypted=False,
            is_multivolume=False,
            cost=cost,
        )

    def _close_archive(self) -> None:
        pass  # nothing to close for filesystem directory


class DirectoryReadBackend(ReadBackend):
    """Backend factory for directory pseudo-archives."""

    FORMATS: tuple[ArchiveFormat, ...] = (ArchiveFormat.DIRECTORY,)
    EXTENSIONS: Mapping[str, ArchiveFormat] = {}
    MAGIC: tuple[MagicSignature, ...] = ()

    def open_read(
        self,
        source: ArchiveSource,
        format: ArchiveFormat,
        streaming: bool,
        passwords: _PasswordCandidates | None,
        encoding: str | None,
        archive_name: str | None,
        config: ArchiveyConfig,
        collector: DiagnosticCollector | None = None,
        member_streams: MemberStreams = MemberStreams(0),
        open_site: OpenSite | None = None,
        start_offset: int = 0,
    ) -> DirectoryReader:
        reject_start_offset(start_offset, format, archive_name)
        # `format` is always DIRECTORY here (single-format backend); accepted for the
        # uniform ReadBackend signature. Password rejection is central (SUPPORTS_PASSWORD).
        if not source.is_directory or source.path is None:
            raise TypeError("Directory backend requires a directory path source")
        reader = DirectoryReader(
            source.path,
            streaming,
            archive_name or str(source.path),
            config,
            collector=collector,
            member_streams=member_streams,
            open_site=open_site,
        )
        # Recorded like every other backend's, so the reader's teardown closes it. A
        # directory source holds nothing open; this keeps one close path, not two.
        reader._source = source
        return reader


# Self-register at import time
register_reader(DirectoryReadBackend)
