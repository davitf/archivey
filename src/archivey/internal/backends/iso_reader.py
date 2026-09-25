"""ISO 9660 backend on the v2 ABC, backed by the optional ``pycdlib`` library (``[recommended]``).

Image layout (what ``pycdlib`` addresses)::

    Sector 16+ : volume descriptors (PVD at 16 * 2048 = 32768 bytes)
    Directory records form the tree; file data at extent * block_size

Namespaces — auto-select richest available (Rock Ridge > Joliet > plain) and record
the choice in ``ArchiveInfo.extra["iso.namespace"]``:

- Rock Ridge — POSIX mode/uid/gid and symlinks
- Joliet / plain — those fields are ``None``; plain names are upper-case 8.3 with a
  ``;version`` suffix

The directory tree lives in the header region → ``INDEXED`` listing and ``DIRECT``
random access. A non-seekable source is rejected (trailing metadata; no streaming).
Write is out of scope (``UnsupportedOperationError``). ``pycdlib`` uses absolute
offsets, so the image must start at ``tell() == 0`` — ``open_archive`` wraps
mid-positioned streams (stream-position contract). A compressed ``.iso.xz`` is a
single-file compressor wrapping the image, not mounted here.

**Process-global side effect.** Importing this module (which ``import archivey`` does
eagerly, to register backends) installs a directory-cycle guard *into pycdlib's own
namespace* by replacing ``pycdlib.pycdlib.collections`` with a proxy whose ``deque``
tracks visited extents — see :func:`_install_pycdlib_directory_cycle_guard`. It is
confined to pycdlib and transparent on well-formed images, but a program that also uses
pycdlib directly in the same process will see archivey's guarded ``deque`` there too. This
is a deliberate trade to stop a crafted/cyclic ISO from hanging the walk forever; see
``dev-docs/known-issues.md``.
"""

from __future__ import annotations

import importlib
import re
import stat
import struct
import threading
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from types import ModuleType
from typing import TYPE_CHECKING, BinaryIO, Iterator, Mapping, TypeGuard, cast

if TYPE_CHECKING:
    from pycdlib.dates import DirectoryRecordDate, VolumeDescriptorDate
    from pycdlib.dr import DirectoryRecord
    from pycdlib.pycdlibio import PyCdlibIO
    from pycdlib.rockridge import RockRidge

from archivey.config import ArchiveyConfig
from archivey.cost import (
    AccessCost,
    CostReceipt,
    ListingCost,
    StreamCapability,
)
from archivey.diagnostics import DiagnosticCode, MemberHeaderRecordContext
from archivey.exceptions import (
    ArchiveyError,
    CorruptionError,
    PackageNotInstalledError,
    UnsupportedFeatureError,
)
from archivey.internal.base_reader import (
    BaseArchiveReader,
    ReadBackend,
    reject_start_offset,
)
from archivey.internal.diagnostics_collector import DiagnosticCollector
from archivey.internal.naming import emit_member_name_normalized, normalize_member_name
from archivey.internal.open_site import OpenSite
from archivey.internal.password import _PasswordCandidates
from archivey.internal.registry import register_reader
from archivey.internal.source import ArchiveSource
from archivey.internal.streams.archive_stream import ArchiveStream
from archivey.internal.streams.streamtools import (
    DelegatingStream,
    LockedStream,
)
from archivey.terminal import quoted
from archivey.types import (
    ArchiveFormat,
    ArchiveInfo,
    ArchiveInfoExtra,
    ArchiveMember,
    CompressionAlgorithm,
    CompressionMethod,
    MagicSignature,
    MemberExtra,
    MemberStreams,
    MemberType,
    MissingComponent,
)

_PYCDLIB_REQUIREMENT = MissingComponent(
    "pycdlib", "pip install archivey[recommended]", ("iso",)
)


# pycdlib is an optional *runtime* dependency ([recommended] extra): absent in the zero-dep
# core / core-only install. Resolve it dynamically (like the codec layer's optional packages) so the
# module still imports there and absence becomes a clean PackageNotInstalledError. (Typing
# uses the TYPE_CHECKING import above; the dev group carries pycdlib so the checkers resolve it.)
def _optional(name: str) -> ModuleType | None:
    try:
        return importlib.import_module(name)
    except (
        ImportError
    ):  # pragma: no cover - the absent path runs in the core-only CI leg
        return None


pycdlib = _optional("pycdlib")
_pycdlib_exc = _optional("pycdlib.pycdlibexception")
_pycdlib_core = _optional("pycdlib.pycdlib")
_pycdlib_io = _optional("pycdlib.pycdlibio")
_pycdlib_dr = _optional("pycdlib.dr")
_pycdlib_dates = _optional("pycdlib.dates")
_PYCDLIB_CYCLE_GUARD_INSTALLED = False


class _DequeGuardedCollections:
    """Proxy over the real ``collections`` module that overrides only ``deque``.

    Installed once as ``pycdlib.pycdlib.collections`` so the extent-cycle guard is confined
    to pycdlib's own namespace — no other code in the process ever sees a patched ``deque``,
    unlike a global ``collections.deque`` swap. Every other attribute delegates to the real
    module.
    """

    def __init__(self, real: ModuleType, deque_cls: type) -> None:
        self._real = real
        self.deque = deque_cls

    def __getattr__(self, name: str) -> object:
        # Reached only for attributes not set in __init__ (i.e. everything but ``deque``).
        return getattr(self._real, name)


def _install_pycdlib_directory_cycle_guard() -> None:
    """Prevent pycdlib from hanging on cyclic ISO/Joliet directory trees.

    pycdlib walks directory trees with a plain ``collections.deque`` and no visit tracking,
    so corrupt directory records that close a cycle (a child extent pointing back at an
    ancestor) loop forever — in any namespace ``open_fp`` walks (plain ISO 9660 PVD, Rock
    Ridge PVD, Joliet SVD, …). The mutation harness found a Joliet case on ``basic-iso``; the
    same mechanism reproduces on plain and Rock Ridge trees (see
    ``test_pycdlib_directory_cycle_does_not_hang``).

    The guard is a ``deque`` subclass that tracks the directory extents scheduled on *that
    instance* and skips re-enqueueing one already seen — valid trees never revisit an extent,
    so this is transparent on well-formed images and no-ops entirely for deques that hold
    anything other than directory records. Because the visit set lives on the instance (not a
    per-walk closure), the subclass is installed **once, permanently**, confined to pycdlib's
    ``collections`` reference: no per-walk swap, no shared mutable state, and concurrent ISO
    opens on separate threads never interfere (each walk builds its own deque instance).
    """
    if pycdlib is None:
        return
    global _PYCDLIB_CYCLE_GUARD_INSTALLED
    if _PYCDLIB_CYCLE_GUARD_INSTALLED:
        return

    import collections

    import pycdlib.pycdlib as pcd_module
    from pycdlib import dr as dr_mod

    real_deque = collections.deque

    class _ExtentGuardedDeque(real_deque):
        """A ``deque`` that drops a directory record whose extent it has already scheduled."""

        def __init__(
            self, iterable: Iterable[object] = (), maxlen: int | None = None
        ) -> None:
            items = list(iterable)
            super().__init__(items, maxlen)
            # Seed from the initial contents (which bypass ``append``) so a cycle back to a
            # root/seed extent is caught too.
            self._visited_extents: set[int] = {
                item.extent_location()
                for item in items
                if isinstance(item, dr_mod.DirectoryRecord)
            }

        def append(self, dir_record: object) -> None:
            if isinstance(dir_record, dr_mod.DirectoryRecord):
                extent = dir_record.extent_location()
                if extent in self._visited_extents:
                    return
                self._visited_extents.add(extent)
            super().append(dir_record)

    # setattr (not a direct assignment) so the type checkers don't flag the deliberate
    # module -> proxy substitution against pcd_module.collections's declared Module type.
    setattr(
        pcd_module,
        "collections",
        _DequeGuardedCollections(collections, _ExtentGuardedDeque),
    )
    _PYCDLIB_CYCLE_GUARD_INSTALLED = True


_install_pycdlib_directory_cycle_guard()

# Exceptions that mean "this ISO structure is bad", translated to CorruptionError. A
# genuine OSError from the underlying handle (file not found, permission, physical media
# error) is unrelated to ISO decoding and MUST propagate unchanged (see error-handling:
# "Genuine runtime and I/O errors are not reclassified"). pycdlib raises its own
# pycdlib wraps *most* format errors in PyCdlibException, but it is not hardened against
# crafted/truncated input: fuzzing surfaces bare IndexError, struct.error, UnicodeDecodeError,
# AttributeError ("'NoneType' object has no attribute …"), KeyError, and ValueError raised deep
# in its header/path-table/directory-record parsing. At the pycdlib call boundary (this backend
# never does its own attribute access or indexing on pycdlib internals) every one of these means
# "this ISO structure is corrupt", so all are translated to CorruptionError — never a raw
# exception. A genuine OSError from the underlying handle (file not found, permission, media
# error) is deliberately NOT in this set: it is real I/O and MUST propagate unchanged (see
# error-handling: "Genuine runtime and I/O errors are not reclassified"). Built defensively so
# the module imports without pycdlib.
_PYCDLIB_ERRORS: tuple[type[Exception], ...] = (
    (_pycdlib_exc.PyCdlibException,) if _pycdlib_exc is not None else ()
) + (IndexError, struct.error, UnicodeDecodeError, AttributeError, KeyError, ValueError)


# Trailing ";1"/";42" version suffix on a plain ISO 9660 file identifier.
_VERSION_SUFFIX = re.compile(r";(\d+)$")


def _is_long_form_date(date: object) -> TypeGuard[VolumeDescriptorDate]:
    """Whether ``date`` is pycdlib's 17-byte date, a Rock Ridge ``TF`` long form."""
    return _pycdlib_dates is not None and isinstance(
        date, _pycdlib_dates.VolumeDescriptorDate
    )


def _is_short_form_date(date: object) -> TypeGuard[DirectoryRecordDate]:
    """Whether ``date`` is pycdlib's 7-byte directory-record date."""
    return _pycdlib_dates is not None and isinstance(
        date, _pycdlib_dates.DirectoryRecordDate
    )


def _dr_date_to_datetime(
    date: DirectoryRecordDate | VolumeDescriptorDate | None,
) -> datetime | None:
    """Convert a pycdlib directory-record date or Rock Ridge ``TF`` time to a datetime.

    A directory record carries the 7-byte form (``DirectoryRecordDate``, years since
    1900). A ``TF`` record carries it too, or the 17-byte long form
    (``VolumeDescriptorDate``, a four-digit year and hundredths of a second) when its
    LONG_FORM flag is set. ``gmtoffset`` is in 15-minute units in both. Returns
    ``None`` on a missing, unspecified (all zeros) or malformed date rather than
    raising: a bad date field must not sink the whole listing.
    """
    if date is None:
        return None
    try:
        tz = timezone(timedelta(minutes=date.gmtoffset * 15))
        if _is_long_form_date(date):
            hundredths = date.hundredthsofsecond
            return datetime(
                date.year,
                date.month,
                date.dayofmonth,
                date.hour,
                date.minute,
                date.second,
                # MagicISO writes binary junk here; only 0-99 is a hundredth.
                hundredths * 10_000 if 0 <= hundredths <= 99 else 0,
                tzinfo=tz,
            )
        if not _is_short_form_date(date):
            return None
        return datetime(
            1900 + date.years_since_1900,
            date.month,
            date.day_of_month,
            date.hour,
            date.minute,
            date.second,
            tzinfo=tz,
        )
    except (ValueError, AttributeError, TypeError, OverflowError):
        return None


def _is_directory_record(obj: object) -> TypeGuard[DirectoryRecord]:
    """Whether ``obj`` is a pycdlib directory record, the handle ISO members carry."""
    return _pycdlib_dr is not None and isinstance(obj, _pycdlib_dr.DirectoryRecord)


def _yield_children(
    record: DirectoryRecord, rock_ridge: bool
) -> Iterator[DirectoryRecord | None]:
    """A directory record's children, as pycdlib's own ``walk()`` enumerates them.

    ``pycdlib.pycdlib._yield_children`` is private, but it is the one place pycdlib
    skips the extra records of a multi-extent file and follows Rock Ridge CL/PL
    relocation. The public ``list_children`` does the same only behind a path lookup,
    which is the step the record walk exists to avoid. The ISO tests exercise it on
    every supported pycdlib, so a rename there fails loudly rather than silently.
    """
    assert _pycdlib_core is not None
    return _pycdlib_core._yield_children(record, rock_ridge)


class _PyCdlibStream(DelegatingStream):
    """Adapt pycdlib's ``PyCdlibIO`` (a one-file context manager) onto ``DelegatingStream``.

    ``PyCdlibIO`` must be *entered* before use — its context manager sets up the read offset —
    so this enters it in ``__init__`` and relies on ``DelegatingStream.close()`` (which calls
    ``inner.close()``, the exact equivalent of ``PyCdlibIO.__exit__``) to exit it, keeping the
    enter/exit lifecycle paired. Read/seek/tell/seekable are inherited delegation.
    """

    def __init__(self, raw: "PyCdlibIO") -> None:
        # PyCdlibIO is an io.RawIOBase, which is a BinaryIO at runtime but not by typeshed's
        # nominal hierarchy, so cast at the DelegatingStream boundary.
        super().__init__(cast("BinaryIO", raw))
        raw.__enter__()  # set up the read offset; close() -> inner.close() exits the context


class IsoReader(BaseArchiveReader):
    """Reads an ISO 9660 image via ``pycdlib`` (Rock Ridge / Joliet / plain)."""

    _SUPPORTS_RANDOM_ACCESS = True
    _MEMBER_LIST_UPFRONT = (
        True  # the directory tree is an in-header index (O(1) listing)
    )

    def __init__(
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
    ) -> None:
        # password rejection is central: open_archive checks ReadBackend.SUPPORTS_PASSWORD.
        super().__init__(
            format,
            streaming,
            archive_name,
            config,
            collector=collector,
            member_streams=member_streams,
            open_site=open_site,
        )
        self._source = source
        # Shared-handle lock: only for CONCURRENT readers (default path takes none).
        self._handle_lock: threading.Lock | None = (
            threading.Lock() if MemberStreams.CONCURRENT in member_streams else None
        )
        if pycdlib is None:
            raise PackageNotInstalledError(
                _PYCDLIB_REQUIREMENT.message("reading ISO images"),
                archive_name=archive_name,
            )

        self._iso = pycdlib.PyCdlib()
        self._iso_opened = False
        # Boundary outside the guard; an exception the translator does not recognize
        # (a genuine OSError from the handle) propagates unchanged.
        try:
            with self._translated_errors():
                with self._handle_guard():
                    # Always the source, never ``open(str(path))``: a path pycdlib opened
                    # for itself would have nothing of ours underneath it, and the
                    # source is what bounds the reads pycdlib sizes from the image's own
                    # headers. pycdlib clamps a *file*'s ``data_length`` to the image,
                    # but not a *directory*'s: a root record declaring 4 GiB asks for 4
                    # GiB inside ``open_fp``, from a file of any size. The source turns
                    # that into a short read, which pycdlib reports as an invalid
                    # directory record and ``_translate_exception`` makes a
                    # ``CorruptionError``. pycdlib never closes a file object it was
                    # handed (``_managing_fp`` is set only by ``open()``).
                    self._iso.open_fp(self._track_source_seeks(source))
                    self._iso_opened = True

                    # Auto-select the richest namespace: Rock Ridge > Joliet > plain
                    # ISO 9660. Inside the guard, not after it: these are pycdlib calls
                    # on the image just opened, so a failure here is an image failure
                    # and belongs to the same translation and the same release.
                    if self._iso.has_rock_ridge():
                        self._namespace = "rock_ridge"
                        self._path_kw = "rr_path"
                    elif self._iso.has_joliet():
                        self._namespace = "joliet"
                        self._path_kw = "joliet_path"
                    else:
                        self._namespace = "iso9660"
                        self._path_kw = "iso_path"
        except BaseException:
            # No reader is returned for anyone to close, and the exception's traceback
            # keeps this frame alive for as long as the caller holds it (inventory/fuzz
            # catch-and-continue loops), so release pycdlib's state now. The source is
            # ``open_archive``'s to close on this path. The guard covers the whole
            # constructor body so this stays true of every failure point, not only of
            # ``open_fp``.
            self._release_archive_handles()
            raise

    def _translate_exception(self, exc: Exception) -> ArchiveyError | None:
        if _pycdlib_exc is not None and isinstance(exc, _pycdlib_exc.PyCdlibException):
            return CorruptionError(f"Error reading ISO image: {exc!r}")
        # pycdlib does not wrap every parse failure in its own exception type: a truncated
        # or crafted image can raise a bare IndexError/struct.error/ValueError from deep in
        # its header parsing (e.g. `data[offset]` off the end of a short path table). Those
        # are corruption in the ISO structure, not archivey/runtime bugs, so translate them
        # rather than letting a raw IndexError escape. (Found by the corpus mutation harness.)
        if isinstance(
            exc,
            (
                IndexError,
                struct.error,
                UnicodeDecodeError,
                AttributeError,
                KeyError,
                ValueError,
            ),
        ):
            # pycdlib choked on corrupt structure (see the _PYCDLIB_ERRORS note). Never a
            # genuine OSError — that is not in this set and propagates unchanged.
            return CorruptionError(f"Error reading ISO image: {exc!r}")
        return None

    # --- listing ------------------------------------------------------------------------

    def _join(self, dirpath: str, name: str) -> str:
        return "/" + name if dirpath == "/" else f"{dirpath}/{name}"

    def _split_version(self, ns_path: str) -> tuple[str, int | None]:
        """The path to show the caller, and the plain-ISO file version if it had one.

        The leading ``/`` is stripped. In the ``iso9660`` namespace a ``;N`` suffix is the
        file *version*: it is removed and returned, and so is the ``.`` that separates an
        empty extension (``FOO.;1`` is ``FOO``), since the two are one rule in ISO 9660.

        ``FOO.;1`` and ``FOO;1`` both map to ``("FOO", 1)``. Level 1 requires the dotted
        spelling but writers emit both, so a directory can hold the two. Where one of them
        is the newest version, the two take the one bare name and last-entry-wins makes
        only the later one current. A superseded version is presented by its stored
        spelling (see ``_make_member``), so the two stay apart there.
        """
        rel = ns_path.lstrip("/")
        if self._namespace != "iso9660":
            return rel, None
        parent, sep, base = rel.rpartition("/")
        match = _VERSION_SUFFIX.search(base)
        if match is None:
            return rel, None
        stem = base[: match.start()]
        if stem.endswith(".") and len(stem) > 1:
            stem = stem[:-1]
        if not stem:
            return rel, None
        return parent + sep + stem, int(match.group(1))

    def _version_order(self, item: tuple[str, object]) -> tuple[str, int]:
        """Sort key putting each plain-ISO name's versions in ascending order."""
        presented, version = self._split_version(item[0])
        return presented, version or 0

    def _record_name(self, record: DirectoryRecord) -> str:
        """Decode one directory record's own name in the selected namespace.

        Decoding never raises: a name that is not valid in its namespace's encoding is
        rendered (surrogateescape for the byte namespaces, U+FFFD for Joliet's UTF-16)
        rather than costing the listing.
        """
        if self._namespace == "rock_ridge" and record.rock_ridge is not None:
            return record.rock_ridge.name().decode("utf-8", errors="surrogateescape")
        ident = record.file_identifier()
        if self._namespace == "rock_ridge":
            # No Rock Ridge entries on this one record: fall back to its ISO 9660
            # identifier, version and empty-extension dot removed.
            base = ident.decode("utf-8", errors="surrogateescape")
            match = _VERSION_SUFFIX.search(base)
            if match is not None and match.start() > 0:
                base = base[: match.start()]
                if base.endswith(".") and len(base) > 1:
                    base = base[:-1]
            return base
        if self._namespace == "joliet":
            return ident.decode("utf-16_be", errors="replace")
        return ident.decode("utf-8", errors="surrogateescape")

    def _is_rr_moved(self, record: DirectoryRecord) -> bool:
        """Whether a root-level directory is Rock Ridge's ``rr_moved`` relocation parent.

        ISO 9660 caps depth at eight, so writers park deeper subtrees under a root-level
        directory and relink each one into place with CL/PL/RE records. That directory is
        the workaround's scaffolding, not part of the tree anyone archived. It is known by
        its contents rather than its name: every child is a relocated directory (its
        ``..`` carries a PL record), which is the same test pycdlib uses to hide those
        children from their parking place.
        """
        children = [
            c
            for c in record.children
            if c is not None and not c.is_dot() and not c.is_dotdot()
        ]
        if not children:
            return False
        for child in children:
            if not child.is_dir() or len(child.children) < 2:
                return False
            dotdot = child.children[1]
            rr = getattr(dotdot, "rock_ridge", None)
            if rr is None or not rr.parent_link_record_exists():
                return False
        return True

    def _walk_records(self) -> Iterator[tuple[str, DirectoryRecord, bool]]:
        """Yield ``(namespace path, directory record, superseded)`` for every entry.

        The walk follows records, not names. ``PyCdlib.walk()`` yields names, and turning
        a name back into a record with ``get_record()`` is not total: a Rock Ridge name
        holding ``/``, or two entries sharing one Rock Ridge name, makes that lookup fail
        and would cost the whole listing, and a duplicated directory name makes pycdlib
        re-walk its subtree once per duplicate. Here the path is only a rendering of the
        record. Each directory extent is descended once, so a crafted image whose records
        close a cycle cannot loop.

        Within a directory, subdirectories come first and then files, each in record
        order, except that plain ISO 9660 files are ordered by (name, version).
        ``superseded`` is true for a plain ISO 9660 file when the same directory holds
        a higher version of the same name.
        """
        root = self._iso.get_record(**{self._path_kw: "/"})
        use_rr = self._namespace == "rock_ridge"
        seen_extents = {root.extent_location()}
        stack: list[tuple[str, DirectoryRecord]] = [("/", root)]
        while stack:
            dirpath, dir_record = stack.pop()
            dirs: list[tuple[str, DirectoryRecord]] = []
            files: list[tuple[str, DirectoryRecord]] = []
            for child in _yield_children(dir_record, use_rr):
                if child is None or child.is_dot() or child.is_dotdot():
                    continue
                if (
                    use_rr
                    and dirpath == "/"
                    and child.is_dir()
                    and self._is_rr_moved(child)
                ):
                    continue
                path = self._join(dirpath, self._record_name(child))
                (dirs if child.is_dir() else files).append((path, child))
            if self._namespace == "iso9660":
                files.sort(key=self._version_order)
            newest: dict[str, int] = {}
            for path, _ in files:
                presented, version = self._split_version(path)
                if version is not None:
                    newest[presented] = max(newest.get(presented, version), version)
            for path, record in dirs:
                yield path, record, False
            for path, record in files:
                presented, version = self._split_version(path)
                yield path, record, version is not None and version < newest[presented]
            for path, record in reversed(dirs):
                extent = record.extent_location()
                if extent in seen_extents:
                    continue
                seen_extents.add(extent)
                stack.append((path, record))

    def _iter_members(self) -> Iterator[ArchiveMember]:
        # Pinned-pycdlib audit (tar-concurrent-open 2.7 / concurrent-member-streams 5.4):
        # the walk traverses in-memory parsed catalog records and does not touch _cdfp.
        # Only PyCdlibIO I/O needs the handle lock. If a future pycdlib version gains
        # handle access here, lock the complete call.
        with self._translated_errors():
            # ``index`` is each member's position in the walk, the id registration
            # stamps, so a diagnostic raised while typing can name it.
            for index, (ns_path, record, superseded) in enumerate(self._walk_records()):
                yield self._make_member(ns_path, record, index, superseded=superseded)

    def _make_member(
        self,
        ns_path: str,
        record: DirectoryRecord,
        index: int,
        *,
        superseded: bool = False,
    ) -> ArchiveMember:
        rr = getattr(record, "rock_ridge", None)
        raw_mode = self._px_mode(rr)

        if rr is not None and rr.is_symlink():
            member_type = MemberType.SYMLINK
        elif record.is_dir():
            member_type = MemberType.DIRECTORY
        elif raw_mode is not None and not stat.S_ISREG(raw_mode):
            # A Rock Ridge PX mode names a device, FIFO or socket: never a regular file,
            # whatever bytes sit at the extent.
            member_type = MemberType.OTHER
        else:
            member_type = MemberType.FILE

        # ISO 9660 / Joliet paths are POSIX-style ("/"): a backslash is a literal character.
        presented, version = self._split_version(ns_path)
        if superseded:
            # An older version is presented as its stored identifier (``FOO.;1``), as
            # RAR presents a file-version history row: a distinct name to read it by,
            # matching ``raw_name``, and ``is_current=False`` so extraction skips it.
            # The newest version takes the bare name. Keeping the stored spelling also
            # keeps ``FOO.;1`` and ``FOO;1`` apart when both are superseded.
            presented = ns_path.lstrip("/")
        name = normalize_member_name(
            presented, member_type, backslash_is_separator=False
        )
        # Always encodes: every name was decoded as UTF-8 with surrogateescape (whose
        # surrogates this handler re-encodes) or as Joliet UTF-16 with U+FFFD.
        raw_name = ns_path.lstrip("/").encode("utf-8", errors="surrogateescape")
        extra = (
            MemberExtra({"iso.version": version})
            if version is not None
            else MemberExtra()
        )

        modified, accessed, created = self._timestamps(record, rr)
        mode, uid, gid = self._posix_metadata(rr)
        link_target = self._symlink_target(member_type, rr)

        size = record.data_length if member_type == MemberType.FILE else None
        compression = (
            (CompressionMethod(algo=CompressionAlgorithm.STORED),)
            if member_type == MemberType.FILE
            else ()
        )

        member = ArchiveMember(
            type=member_type,
            name=name,
            raw_name=raw_name,
            size=size,
            compressed_size=size,  # ISO 9660 stores members uncompressed
            modified=modified,
            accessed=accessed,
            created=created,
            mode=mode,
            uid=uid,
            gid=gid,
            link_target=link_target,
            compression=compression,
            is_encrypted=False,
            extra=extra,
            is_current=not superseded,
            _raw=record,  # the directory record, so _open_member needs no path lookup
        )
        emit_member_name_normalized(
            self._diagnostics_collector,
            member=member,
            presented_name=presented,
            archive_name=self._archive_name,
            member_id=index,
        )
        if self._namespace == "rock_ridge" and rr is None:
            # The image is Rock Ridge but this record's System Use area carries none
            # (absent or damaged). The member is kept under its ISO 9660 name; what the
            # entries would have given (the long name, POSIX mode/owner, a symlink)
            # is missing, and the caller is told so.
            self._diagnostics_collector.emit(
                code=DiagnosticCode.MEMBER_HEADER_RECORD_SKIPPED,
                message=(
                    f"Directory record {quoted(presented)} carries no Rock Ridge "
                    "entries; the member is listed under its ISO 9660 name without "
                    "Rock Ridge name, mode, owner or link data."
                ),
                context=MemberHeaderRecordContext(
                    archive_name=self._archive_name,
                    member_name=name,
                    member_id=index,
                    record="rock_ridge",
                    reason="no Rock Ridge entries in the System Use area",
                ),
                member=member,
                attach_to_member=True,
            )
        return member

    def _timestamps(
        self, record: DirectoryRecord, rr: RockRidge | None
    ) -> tuple[datetime | None, datetime | None, datetime | None]:
        modified: datetime | None = None
        accessed: datetime | None = None
        created: datetime | None = None
        if rr is not None:
            # Rock Ridge TF entries carry the POSIX times (in dr_entries, or the CE
            # overflow area). A TF modification time wins over the directory-record
            # date, which cannot hold hundredths or the long form's four-digit year.
            for entries in (rr.dr_entries, rr.ce_entries):
                tf = getattr(entries, "tf_record", None)
                if tf is None:
                    continue
                modified = modified or _dr_date_to_datetime(
                    getattr(tf, "modification_time", None)
                )
                accessed = accessed or _dr_date_to_datetime(
                    getattr(tf, "access_time", None)
                )
                # Without a TF creation time, ``created`` falls back to the POSIX
                # attribute-change time (st_ctime), as RAR's Unix members do. Nothing in
                # ``extra`` marks the difference for ISO yet.
                created = (
                    created
                    or _dr_date_to_datetime(getattr(tf, "creation_time", None))
                    or _dr_date_to_datetime(getattr(tf, "attribute_change_time", None))
                )
        modified = modified or _dr_date_to_datetime(getattr(record, "date", None))
        return modified, accessed, created

    def _px_mode(self, rr: RockRidge | None) -> int | None:
        """The full POSIX mode from a Rock Ridge PX record, file-type bits included."""
        if rr is None:
            return None
        for entries in (rr.dr_entries, rr.ce_entries):
            px = getattr(entries, "px_record", None)
            if px is not None:
                mode = getattr(px, "posix_file_mode", None)
                return mode if isinstance(mode, int) else None
        return None

    def _posix_metadata(
        self, rr: RockRidge | None
    ) -> tuple[int | None, int | None, int | None]:
        # POSIX mode/uid/gid come only from a Rock Ridge PX record; Joliet/plain carry none,
        # so those namespaces correctly yield (None, None, None).
        if rr is None:
            return None, None, None
        for entries in (rr.dr_entries, rr.ce_entries):
            px = getattr(entries, "px_record", None)
            if px is None:
                continue
            raw_mode = getattr(px, "posix_file_mode", None)
            mode = stat.S_IMODE(raw_mode) if raw_mode is not None else None
            return (
                mode,
                getattr(px, "posix_user_id", None),
                getattr(px, "posix_group_id", None),
            )
        return None, None, None

    def _symlink_target(
        self, member_type: MemberType, rr: RockRidge | None
    ) -> str | None:
        if member_type != MemberType.SYMLINK or rr is None:
            return None
        try:
            target = rr.symlink_path()
        except _PYCDLIB_ERRORS:
            return None
        return target.decode("utf-8", errors="surrogateescape") if target else None

    # --- data ---------------------------------------------------------------------------

    def _open_record(self, record: DirectoryRecord) -> "PyCdlibIO":
        """Open a file's data from its directory record, with no path lookup.

        The same checks ``PyCdlib.open_file_from_iso`` makes once it has the record.
        """
        assert _pycdlib_exc is not None and _pycdlib_io is not None
        if not record.is_file():
            raise _pycdlib_exc.PyCdlibInvalidInput("Path to open must be a file")
        if record.inode is None:
            raise _pycdlib_exc.PyCdlibInvalidInput("File has no data")
        return _pycdlib_io.PyCdlibIO(record.inode, self._iso.logical_block_size)

    def _open_member(self, member: ArchiveMember) -> ArchiveStream:
        record = member._raw
        assert _is_directory_record(record), (
            "ISO member is missing its directory record"
        )
        # Boundary outside the lock; _PyCdlibStream construction stays inside it so any
        # enter-time pycdlib seek/error is covered by both.
        with self._translated_errors(member.name):
            if self._handle_lock is not None:
                with self._handle_lock:
                    raw = self._open_record(record)
                    # Construct under the lock so enter-time pycdlib seek is covered.
                    locked: BinaryIO = LockedStream(
                        _PyCdlibStream(raw), self._handle_lock
                    )
                return self._wrap_member_stream(locked, member.name, size=member.size)
            raw = self._open_record(record)
            # _PyCdlibStream enters the PyCdlibIO context in its __init__.
            stream = _PyCdlibStream(raw)
        return self._wrap_member_stream(stream, member.name, size=member.size)

    def _get_archive_info(self) -> ArchiveInfo:
        cost = CostReceipt(
            listing_cost=ListingCost.INDEXED,  # directory tree lives in the header region
            access_cost=AccessCost.DIRECT,  # each extent is independently addressable
            stream_capability=StreamCapability.SEEKABLE,
            solid_block_count=None,
        )
        pvd = self._iso.pvd
        volume_id = pvd.volume_identifier.decode("ascii", errors="replace").rstrip()
        interchange_level = getattr(self._iso, "interchange_level", None)
        info_extra = ArchiveInfoExtra({"iso.namespace": self._namespace})
        return ArchiveInfo(
            format=self._format,
            format_version=str(interchange_level) if interchange_level else None,
            is_solid=False,
            member_count=None,  # counting requires walking the tree
            comment=volume_id or None,
            is_encrypted=False,
            is_multivolume=False,
            cost=cost,
            extra=info_extra,
        )

    def _release_archive_handles(self) -> None:
        """Close what the open built: pycdlib's state over the source.

        One release path for both the failure inside ``__init__`` and the ordinary
        close, so the two cannot drift. It is called from ``__init__`` before the
        object is complete, which is why ``_iso.close()`` is gated on the flag rather
        than tried: pycdlib raises on a ``close()`` it never opened. The source itself
        is not this reader's to close here: the reader closes it after teardown, and
        ``open_archive`` closes it when the constructor raises.
        """
        if self._iso_opened:
            with self._handle_guard():
                self._iso.close()
            self._iso_opened = False

    def _close_archive(self) -> None:
        self._release_archive_handles()


# The 12-byte sync pattern that opens every sector of a raw CD dump (a ``.bin`` from a
# ``.bin``/``.cue`` pair). A plain ``.iso`` holds only the 2048-byte user data of each
# sector, so it never starts with this: its first 32 KiB is the zero-filled system area.
_RAW_SECTOR_SYNC = b"\x00" + b"\xff" * 10 + b"\x00"
# 2352 bytes is a full raw sector; 2448 is the same with 96 bytes of subchannel data
# appended, which some dumpers write.
_RAW_SECTOR_SIZES = (2352, 2448)


def _describe_raw_sector_image(source: BinaryIO) -> str | None:
    """Name the layout of a raw CD sector image, or ``None`` if ``source`` is not one.

    Raw images are recognised so they can be refused by name: reading one means stripping
    every sector to its payload first, which is not implemented. Without this the same
    file fails detection outright, which sends a user looking for a corrupt file when the
    answer is to convert it.

    Everything needed is in the first sector or two. Byte 15 is the sector mode. For
    mode 2, bit ``0x20`` of the submode byte (offset 18, in the subheader) separates Form
    1, whose 2048-byte payload holds a filesystem, from Form 2, whose 2324-byte payload
    is video or audio and holds none. The sector size is where the second sync lands.
    ``source`` is left at the position it was handed in at.
    """
    want = max(_RAW_SECTOR_SIZES) + len(_RAW_SECTOR_SYNC)
    start = source.tell()
    try:
        head = source.read(want)
    finally:
        source.seek(start)
    if not head.startswith(_RAW_SECTOR_SYNC) or len(head) < 24:
        return None
    mode = head[15]
    if mode == 1:
        layout = "Mode 1"
    elif mode == 2:
        layout = "Mode 2 Form 2" if head[18] & 0x20 else "Mode 2 Form 1"
    else:
        layout = f"unknown sector mode {mode}"
    sizes = [
        size
        for size in _RAW_SECTOR_SIZES
        if head[size : size + len(_RAW_SECTOR_SYNC)] == _RAW_SECTOR_SYNC
    ]
    if sizes:
        layout += f", {sizes[0]}-byte sectors"
    return layout


def refuse_raw_sector_image(
    source: BinaryIO, format: ArchiveFormat, archive_name: str | None
) -> None:
    """Raise ``UnsupportedFeatureError`` if ``source`` is a raw CD sector image.

    Called by ``open_archive`` for a source resolved as ISO, before the backend's
    availability check, so the refusal does not depend on pycdlib being installed: a
    caller without it would otherwise be told to install it, only to be refused after.
    """
    layout = _describe_raw_sector_image(source)
    if layout is None:
        return
    if layout.startswith("Mode 2 Form 2"):
        what = (
            "Its sectors carry video or audio, not an ISO 9660 filesystem, so there is "
            "nothing here to list."
        )
    else:
        what = (
            "Reading raw sector images is not supported; convert it to a plain .iso "
            "first (for example with bchunk or bin2iso, using the .cue sheet if there "
            "is one)."
        )
    raise UnsupportedFeatureError(
        f"The file is a raw CD sector image ({layout}), not an ISO 9660 image. {what}",
        source_format=format,
        archive_name=archive_name,
    )


class IsoReadBackend(ReadBackend):
    """Backend factory for ISO 9660 images (requires the ``[recommended]`` extra → ``pycdlib``)."""

    FORMATS: tuple[ArchiveFormat, ...] = (ArchiveFormat.ISO,)
    EXTENSIONS: Mapping[str, ArchiveFormat] = {".iso": ArchiveFormat.ISO}
    # The primary volume descriptor's "CD001" magic sits at offset 32 769; detection peeks the
    # extended 32 774-byte window on demand to find it (see internal/detection.py).
    MAGIC: tuple[MagicSignature, ...] = (
        MagicSignature(32769, b"CD001", ArchiveFormat.ISO),
        # A raw CD sector dump. Claimed as ISO so that open_archive can refuse it by
        # name (see refuse_raw_sector_image) rather than fail detection.
        MagicSignature(0, _RAW_SECTOR_SYNC, ArchiveFormat.ISO),
    )
    # SUPPORTS_STREAMING_NON_SEEKABLE stays False: pycdlib addresses the image by
    # absolute offsets (volume descriptors at 32 KiB), so even a forward-only pass
    # needs a seekable source.
    # Same requirement the reader raises from, so the hint reported by listing /
    # format_availability and the one in the open() failure cannot diverge.
    OPTIONAL_DEPENDENCY = _PYCDLIB_REQUIREMENT.name
    INSTALL_HINT = _PYCDLIB_REQUIREMENT.install_hint

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
    ) -> IsoReader:
        reject_start_offset(start_offset, format, archive_name)
        # `format` is always ISO here (single-format backend); accepted for the uniform
        # ReadBackend signature.
        return IsoReader(
            source,
            format,
            streaming,
            passwords,
            encoding,
            archive_name,
            config,
            collector=collector,
            member_streams=member_streams,
            open_site=open_site,
        )


register_reader(IsoReadBackend)
