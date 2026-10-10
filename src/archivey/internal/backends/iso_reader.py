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
Write is out of scope (``UnsupportedFeatureError``). ``pycdlib`` uses absolute
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
``dev-docs/formats/iso.md`` §4. The guard covers the UDF walk too. The same import wraps
four pycdlib methods and two pycdlib functions:

- ``pycdlib.rockridge.RockRidge.parse`` (:func:`_install_pycdlib_system_use_filter`),
  which filters each System Use area and refuses a Rock Ridge ``CE`` area past its
  block before pycdlib reads it;
- ``pycdlib.dr.DirectoryRecord.parse`` (:func:`_install_pycdlib_record_counter`), which
  weighs what pycdlib parses against ``ListingLimits``;
- ``pycdlib.pycdlib.PyCdlib._parse_path_table`` and
  ``pycdlib.path_table_record.PathTableRecord.parse``
  (:func:`_install_pycdlib_path_table_bound`), which bound a path table by the image
  and the same budget before pycdlib reads it, and count its entries as pycdlib parses
  them;
- ``pycdlib.udf.parse_file_ident`` and ``pycdlib.udf.parse_file_entry``
  (:func:`_install_pycdlib_udf_counter`), which weigh the UDF tree against the same
  budget.

Every wrapper acts only inside this module's own ``open_fp`` call, so other users of
pycdlib see no change.
"""

from __future__ import annotations

import importlib
import io
import re
import stat
import struct
import threading
import zlib
from collections.abc import Iterable, Iterator, Mapping
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta, timezone
from types import ModuleType
from typing import (
    TYPE_CHECKING,
    BinaryIO,
    NamedTuple,
    TypeGuard,
    cast,
)

if TYPE_CHECKING:
    from _typeshed import WriteableBuffer
    from pycdlib.dates import DirectoryRecordDate, VolumeDescriptorDate
    from pycdlib.dr import DirectoryRecord
    from pycdlib.inode import Inode
    from pycdlib.path_table_record import PathTableRecord
    from pycdlib.pycdlib import PyCdlib
    from pycdlib.pycdlibio import PyCdlibIO
    from pycdlib.rockridge import RockRidge, RockRidgeEntries, RRCERecord
    from pycdlib.udf import UDFFileEntry, UDFFileIdentifierDescriptor

from archivey.config import ArchiveyConfig, ListingLimits
from archivey.cost import (
    AccessCost,
    CostReceipt,
    ListingCost,
    StreamCapability,
)
from archivey.diagnostics import (
    DiagnosticCode,
    MemberHeaderRecordContext,
    NameEncodingContext,
    raw_name_to_base64,
)
from archivey.exceptions import (
    ArchiveyError,
    CorruptionError,
    PackageNotInstalledError,
    ResourceLimitError,
    TruncatedError,
    UnsupportedFeatureError,
)
from archivey.internal.base_reader import (
    BaseArchiveReader,
    ReadBackend,
    reject_start_offset,
)
from archivey.internal.diagnostics_collector import DiagnosticCollector
from archivey.internal.listing_limits import check_metadata_budget
from archivey.internal.naming import emit_member_name_normalized, normalize_member_name
from archivey.internal.open_site import OpenSite
from archivey.internal.password import _PasswordCandidates
from archivey.internal.registry import register_reader
from archivey.internal.source import ArchiveSource
from archivey.internal.streams.archive_stream import ArchiveStream
from archivey.internal.streams.streamtools import (
    DelegatingStream,
    LockedStream,
    resolve_seek,
)
from archivey.internal.timestamps import TimestampIssue
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
_pycdlib_inode = _optional("pycdlib.inode")
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
    """Prevent pycdlib from hanging on cyclic ISO, Joliet or UDF directory trees.

    pycdlib walks directory trees with a plain ``collections.deque`` and no visit tracking,
    so corrupt directory records that close a cycle (a child extent pointing back at an
    ancestor) loop forever — in any namespace ``open_fp`` walks (plain ISO 9660 PVD, Rock
    Ridge PVD, Joliet SVD, …). The mutation harness found a Joliet case on ``basic-iso``; the
    same mechanism reproduces on plain and Rock Ridge trees (see
    ``test_pycdlib_directory_cycle_does_not_hang``). The UDF walk
    (``_walk_udf_directories``) uses the same ``deque`` for UDF File Entries, and a File
    Identifier naming an ancestor's ICB loops it the same way, allocating as it goes
    (``test_pycdlib_udf_directory_cycle_does_not_hang``).

    The guard is a ``deque`` subclass that tracks the extents scheduled on *that instance*
    and skips re-enqueueing one already seen — valid trees never revisit an extent, so this
    is transparent on well-formed images and no-ops entirely for deques that hold anything
    other than directory records or UDF File Entries. Directory records and UDF File Entries
    are tracked in separate sets: their extents are numbered in different spaces. Because
    the visit sets live on the instance (not a per-walk closure), the subclass is installed
    **once, permanently**, confined to pycdlib's ``collections`` reference: no per-walk swap,
    no shared mutable state, and concurrent ISO opens on separate threads never interfere
    (each walk builds its own deque instance).
    """
    if pycdlib is None:
        return
    global _PYCDLIB_CYCLE_GUARD_INSTALLED
    if _PYCDLIB_CYCLE_GUARD_INSTALLED:
        return

    import collections

    import pycdlib.pycdlib as pcd_module
    from pycdlib import dr as dr_mod
    from pycdlib import udf as udf_mod

    real_deque = collections.deque

    class _ExtentGuardedDeque(real_deque):
        """A ``deque`` that drops a directory record or UDF File Entry already scheduled."""

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
            self._visited_udf_extents: set[int] = {
                item.extent_location()
                for item in items
                if isinstance(item, udf_mod.UDFFileEntry)
            }

        def append(self, item: object) -> None:
            if isinstance(item, dr_mod.DirectoryRecord):
                visited = self._visited_extents
            elif isinstance(item, udf_mod.UDFFileEntry):
                visited = self._visited_udf_extents
            else:
                super().append(item)
                return
            extent = item.extent_location()
            if extent in visited:
                return
            visited.add(extent)
            super().append(item)

    # setattr (not a direct assignment) so the type checkers don't flag the deliberate
    # module -> proxy substitution against pcd_module.collections's declared Module type.
    setattr(
        pcd_module,
        "collections",
        _DequeGuardedCollections(collections, _ExtentGuardedDeque),
    )
    _PYCDLIB_CYCLE_GUARD_INSTALLED = True


_install_pycdlib_directory_cycle_guard()


# The System Use entries pycdlib parses (``RockRidge.parse``). It raises on any other
# tag, although SUSP 1.12 §5 has a reader ignore an entry it does not recognize.
_PYCDLIB_SUSP_TAGS = frozenset(
    {
        b"SP", b"RR", b"CE", b"PX", b"PD", b"ST", b"ER", b"ES", b"PN", b"SL", b"NM",
        b"CL", b"PL", b"RE", b"TF", b"SF", b"AL",
    }
)  # fmt: skip


class _ZisofsEntry(NamedTuple):
    """A Rock Ridge ``ZF`` or ``Z2`` entry: the file's data is zisofs-compressed."""

    tag: bytes
    version: int
    algorithm: bytes
    header_size: int
    log2_block_size: int
    # ``None`` when the entry is too short to hold the fields; it is then refused.
    uncompressed_size: int | None


class _SystemUseNotes:
    """What the System Use filter set aside while ``open_fp`` parsed one image.

    Keyed by ``id`` of the ``RockRidge`` the entries belong to; the object itself is
    kept alongside, so the id cannot be reused while the reader holds the notes.
    ``RockRidge`` has ``__slots__``, so nothing can be stored on it. ``iso`` gives the
    logical block size a ``CE`` area must fit in.
    """

    def __init__(self, iso: PyCdlib) -> None:
        self.zisofs: dict[int, tuple[object, _ZisofsEntry]] = {}
        self.dropped: dict[int, tuple[object, str]] = {}
        self._iso = iso

    def check_continuation_entry(self, ce: RRCERecord | None) -> None:
        """Refuse a Rock Ridge ``CE`` entry whose area runs past its block, before the read.

        pycdlib reads the area a ``CE`` entry names right after parsing the System Use
        area that holds it, for the length the entry declares (up to 4 GiB, clamped
        only to the image), and only then refuses an area that does not fit in one
        logical block (``track_rr_ce_entry``). From 1.21 it follows the chain: an area
        may end in a ``CE`` naming a further area, read the same way. This runs on
        every area pycdlib parses, the record's own and each continuation, so every
        link is refused between the parse that names it and the read. The Linux kernel
        refuses the same entry (``rock_continue``).

        The block is the one pycdlib checks against: the primary volume descriptor's,
        which pycdlib copies to ``logical_block_size`` before it parses any directory
        record, whichever descriptor's tree it is walking.
        """
        if ce is None:
            return
        block_size = cast(int, getattr(self._iso, "logical_block_size"))
        if ce.offset_cont_area + ce.len_cont_area > block_size:
            raise CorruptionError(
                "Error reading ISO image: Rock Ridge continuation area of "
                f"{ce.len_cont_area} bytes at offset {ce.offset_cont_area} of block "
                f"{ce.bl_cont_area} runs past the {block_size}-byte block"
            )

    def filter(self, rock_ridge: object, record: bytes, skip: int) -> bytes:
        """``record`` with the entries pycdlib would refuse the whole image over removed.

        An entry of a type pycdlib does not know is left out, whatever its version, as
        SUSP has a reader ignore it; a ``ZF`` entry (zisofs, or zisofs2 at version 2)
        or a ``Z2`` entry (zisofs2 under the tag libisofs offers for kernels that
        misread a version 2 ``ZF``) is kept here first, even when it is too short to
        parse, so the file is refused rather than read as its compressed bytes. The first entry whose own header is malformed (a length
        under 4 or past the area, or a version other than 1 on a type pycdlib parses) ends
        the area there, and the reason is kept for a diagnostic: everything after it is
        read at an offset that cannot be trusted. genisoimage writes such an area for a
        long symlink target (from about 400 bytes with 1.1.11): the ``SL`` length wraps past 255, and the
        next entry is read from inside the target text. ``isoinfo`` and ``xorriso`` stop
        at the same place. Entries after ``ST`` are not entries and are left out.

        Anything else pycdlib refuses, it still refuses, and the image fails to open as
        before. The ``skip`` bytes before the first entry are pycdlib's to interpret.
        """
        out = bytearray(record[:skip])
        offset = skip
        end = len(record)
        while offset < end:
            left = end - offset
            if left < 4:
                if left != 1 or record[offset] != 0:  # one zero byte pads the area
                    self._drop(rock_ridge, f"{left} stray byte(s) at the end")
                else:
                    out.append(0)
                break
            tag = bytes(record[offset : offset + 2])
            length = record[offset + 2]
            version = record[offset + 3]
            if (
                length < 4
                or length > left
                or (version != 1 and tag in _PYCDLIB_SUSP_TAGS)
            ):
                self._drop(
                    rock_ridge,
                    f"entry {tag!r} at offset {offset} has version {version} and "
                    f"length {length} with {left} byte(s) left",
                )
                break
            entry = record[offset : offset + length]
            if tag in _PYCDLIB_SUSP_TAGS:
                out += entry
            elif tag in (b"ZF", b"Z2") and length < 16:
                self.zisofs[id(rock_ridge)] = (
                    rock_ridge,
                    _ZisofsEntry(tag, version, b"", 0, 0, uncompressed_size=None),
                )
            elif tag in (b"ZF", b"Z2"):
                self.zisofs[id(rock_ridge)] = (
                    rock_ridge,
                    _ZisofsEntry(
                        tag=tag,
                        version=version,
                        algorithm=bytes(entry[4:6]),
                        header_size=entry[6] * 4,
                        log2_block_size=entry[7],
                        # Version 1 stores it both-endian in 32 bits, zisofs2
                        # little-endian in 64.
                        uncompressed_size=int.from_bytes(
                            entry[8 : 12 if version == 1 else 16], "little"
                        ),
                    ),
                )
            offset += length
            if tag == b"ST":
                break
        return bytes(out)

    def _drop(self, rock_ridge: object, reason: str) -> None:
        # The first reason wins: a continuation area parsed after it would report the
        # same record twice.
        self.dropped.setdefault(id(rock_ridge), (rock_ridge, reason))

    def zisofs_entry(self, rock_ridge: object) -> _ZisofsEntry | None:
        noted = self.zisofs.get(id(rock_ridge))
        return None if noted is None else noted[1]

    def dropped_reason(self, rock_ridge: object) -> str | None:
        noted = self.dropped.get(id(rock_ridge))
        return None if noted is None else noted[1]


# Set only while this module's ``open_fp`` runs, so the filter applies to archivey's
# own opens and a program using pycdlib directly keeps pycdlib's behaviour.
_SYSTEM_USE_NOTES: ContextVar[_SystemUseNotes | None] = ContextVar(
    "archivey_iso_system_use_notes", default=None
)


def _inside_our_open() -> bool:
    """Whether this thread is inside ``IsoReader``'s own ``open_fp`` call.

    The structural checks the pycdlib hooks make (a path table past the image, a
    ``CE`` area past its block) are not limits, so they key on this rather than on
    ``_PARSE_BUDGET``, which is ``None`` under ``ListingLimits.UNLIMITED``.
    """
    return _SYSTEM_USE_NOTES.get() is not None


_PYCDLIB_SYSTEM_USE_FILTER_INSTALLED = False


def _install_pycdlib_system_use_filter() -> None:
    """Filter ``RockRidge.parse``'s input and refuse an out-of-block ``CE``, in our opens.

    pycdlib parses every Rock Ridge area inside ``open_fp`` and fails the whole image on
    the first entry it cannot parse, so one odd record costs every member: the area goes
    through :meth:`_SystemUseNotes.filter` first. After each parse, the ``CE`` entry it
    found, in the record's own area or in a continuation area, goes through
    :meth:`_SystemUseNotes.check_continuation_entry`, which refuses an area that does
    not fit in its logical block before pycdlib reads it. The patch is installed once,
    on pycdlib's class, and does nothing unless ``_SYSTEM_USE_NOTES`` is set, which only
    ``IsoReader`` does, around its own ``open_fp`` call.
    """
    global _PYCDLIB_SYSTEM_USE_FILTER_INSTALLED
    if pycdlib is None or _PYCDLIB_SYSTEM_USE_FILTER_INSTALLED:
        return
    from pycdlib import rockridge as rr_mod

    original = rr_mod.RockRidge.parse

    def parse(
        self: RockRidge,
        record: bytes,
        is_first_dir_record_of_root: bool,
        bytes_to_skip: int,
        continuation: bool,
        dr_name: bytes,
    ) -> None:
        budget = _PARSE_BUDGET.get()
        if budget is not None and continuation:
            # The area inside the record was weighed with the record; a continuation
            # area is weighed each time it is parsed, as pycdlib parses a shared one
            # again for every record that names it.
            budget.add_continuation(len(record))
        notes = _SYSTEM_USE_NOTES.get()  # set only inside our own open_fp
        if notes is not None:
            record = notes.filter(self, record, bytes_to_skip)
        original(
            self,
            record,
            is_first_dir_record_of_root,
            bytes_to_skip,
            continuation,
            dr_name,
        )
        if notes is not None:
            # pycdlib keeps the CE of a continuation area in ``ce_entries`` (``None``
            # until one is parsed, from 1.20) and the record's own in ``dr_entries``.
            entries = self.ce_entries if continuation else self.dr_entries
            notes.check_continuation_entry(
                None if entries is None else entries.ce_record
            )

    setattr(rr_mod.RockRidge, "parse", parse)
    _PYCDLIB_SYSTEM_USE_FILTER_INSTALLED = True


_install_pycdlib_system_use_filter()


class _ParseBudget:
    """What pycdlib has parsed inside one ``open_fp``, weighed against ``ListingLimits``.

    pycdlib builds every directory tree of the image inside ``open_fp``, hundreds of
    bytes of Python objects per record, before archivey lists anything. The hooks
    below count as it parses, so an over-limit image is refused early instead of
    after the whole tree is built.

    Counts are per volume descriptor, because a listing shows one tree (Rock Ridge or
    plain ISO 9660 from the PVD, or Joliet from its SVD), and an image with both has
    each file twice. Within a tree the counts are a superset of the listing: every
    record but ``.`` and ``..`` counts as a member, including the extra records of a
    multi-extent file and the ``rr_moved`` scaffolding the listing hides. The bytes are
    the directory records as stored, System Use areas included, plus each Rock Ridge
    continuation area every time pycdlib parses it, plus the tree's little- and
    big-endian path tables, which pycdlib reads whole and parses into one object per
    record (at least 8 bytes each). That is more than the text a listing keeps.

    The UDF tree, when the image has one, is counted as one more tree, although
    archivey does not list it: pycdlib parses it inside ``open_fp`` all the same. Every
    File Identifier but the parent entry counts as a member, and the bytes are each
    File Identifier as stored plus each File Entry's fixed part, extended attributes and
    allocation descriptors as the entry declares them, weighed before pycdlib parses it
    into one object per descriptor.
    """

    def __init__(self, limits: ListingLimits) -> None:
        self._limits = limits
        # Keyed by ``id`` of the volume descriptor; pycdlib keeps every descriptor it
        # walks for the life of the ``PyCdlib`` object, so an id is not reused here.
        self._members: dict[int, int] = {}
        self._bytes: dict[int, int] = {}
        # The tree of the record parsed last: pycdlib parses a record's continuation
        # area right after the record, and ``RockRidge.parse`` is not told the tree.
        self._tree = 0
        # Entries pycdlib has parsed from the path table it is parsing now.
        self._path_table_entries = 0

    def add_record(self, vd: object, nbytes: int) -> None:
        self._tree = id(vd)
        self._add_bytes(nbytes)

    def add_member(self) -> None:
        count = self._members.get(self._tree, 0) + 1
        self._members[self._tree] = count
        max_members = self._limits.max_members
        if max_members is not None and count > max_members:
            raise ResourceLimitError(
                f"Listing limit reached: max_members={max_members} "
                f"({self._tree_name()} holds more than {max_members} records)"
            )

    def add_udf_bytes(self, nbytes: int) -> None:
        self._tree = _UDF_TREE
        self._add_bytes(nbytes)

    def add_udf_member(self) -> None:
        self._tree = _UDF_TREE
        self.add_member()

    def add_continuation(self, nbytes: int) -> None:
        self._add_bytes(nbytes)

    def add_path_table(self, vd: object, nbytes: int) -> None:
        # Called before pycdlib reads the table, so an over-budget size is refused
        # before the read and the parse, not after.
        self._tree = id(vd)
        self._path_table_entries = 0
        self._add_bytes(nbytes)

    def add_path_table_entry(self) -> None:
        """Count one path-table entry pycdlib parsed against ``max_members``.

        Every entry names a directory, and every directory but the root is a member,
        so a table of more than ``max_members + 1`` entries indexes a tree the
        listing would refuse anyway; an image the listing accepts never reaches this.
        Counting as pycdlib parses bounds what one table costs by the member cap
        (about 230 bytes an entry) rather than by its size (29 times it for 8-byte
        entries), since ``max_metadata_bytes`` alone let a table of the whole budget
        through.
        """
        self._path_table_entries += 1
        max_members = self._limits.max_members
        if max_members is not None and self._path_table_entries > max_members + 1:
            raise ResourceLimitError(
                f"Listing limit reached: max_members={max_members} "
                f"(ISO path table holds more than {max_members + 1} directories)"
            )

    def _add_bytes(self, nbytes: int) -> None:
        total = self._bytes.get(self._tree, 0) + nbytes
        self._bytes[self._tree] = total
        check_metadata_budget(
            self._limits,
            total,
            detail=(
                f"UDF directory tree has {total} bytes of File Identifiers and "
                "File Entries"
                if self._tree == _UDF_TREE
                else f"ISO directory tree has {total} bytes of path tables and "
                "directory records"
            ),
        )

    def _tree_name(self) -> str:
        return "UDF directory tree" if self._tree == _UDF_TREE else "ISO directory tree"


# The key the UDF tree is counted under in ``_ParseBudget``: pycdlib walks one UDF file
# set per image, and no ``id()`` is negative.
_UDF_TREE = -1


# Set only while this module's ``open_fp`` runs, like ``_SYSTEM_USE_NOTES``; ``None``
# elsewhere, and under ``ListingLimits.UNLIMITED``.
_PARSE_BUDGET: ContextVar[_ParseBudget | None] = ContextVar(
    "archivey_iso_parse_budget", default=None
)
_PYCDLIB_RECORD_COUNTER_INSTALLED = False


def _install_pycdlib_record_counter() -> None:
    """Weigh each ``DirectoryRecord.parse`` against ``_PARSE_BUDGET`` during our opens.

    Installed once, on pycdlib's class, like the System Use filter, and transparent
    unless ``_PARSE_BUDGET`` is set, which only ``IsoReader`` does, around its own
    ``open_fp`` call. The record's bytes are weighed before pycdlib parses them; the
    member count after, once pycdlib has said whether the record is ``.`` or ``..``.
    """
    global _PYCDLIB_RECORD_COUNTER_INSTALLED
    if pycdlib is None or _PYCDLIB_RECORD_COUNTER_INSTALLED:
        return
    from pycdlib import dr as dr_mod

    original = dr_mod.DirectoryRecord.parse

    def parse(
        self: DirectoryRecord,
        vd: object,
        record: bytes,
        parent: DirectoryRecord | None,
        xa: bool = False,
    ) -> str:
        budget = _PARSE_BUDGET.get()
        if budget is not None:
            budget.add_record(vd, len(record))
        result = original(self, vd, record, parent, xa)
        if budget is not None and not (self.is_dot() or self.is_dotdot()):
            budget.add_member()
        return result

    setattr(dr_mod.DirectoryRecord, "parse", parse)
    _PYCDLIB_RECORD_COUNTER_INSTALLED = True


_install_pycdlib_record_counter()

_PYCDLIB_PATH_TABLE_BOUND_INSTALLED = False


def _install_pycdlib_path_table_bound() -> None:
    """Bound each path table before pycdlib reads and parses it, during our opens.

    pycdlib reads the size a volume descriptor declares in one read and parses it
    into one ``PathTableRecord`` per record, at least 8 bytes each, about 29 times
    the size in memory; it does this twice per tree (little- and big-endian), before
    it walks any directory. The source bounds the read to the image, not the parse.
    A table that runs past the end of the image is ``CorruptionError``, as pycdlib's
    own parse of the short read would have it, and the size is weighed against
    ``max_metadata_bytes`` with the tree it indexes. A second wrapper, on
    ``PathTableRecord.parse``, counts each entry against ``max_members`` as pycdlib
    parses it (:meth:`_ParseBudget.add_path_table_entry`). Installed once and
    transparent outside ``IsoReader``'s ``open_fp``, like the hooks above.
    """
    global _PYCDLIB_PATH_TABLE_BOUND_INSTALLED
    if pycdlib is None or _PYCDLIB_PATH_TABLE_BOUND_INSTALLED:
        return
    import pycdlib.pycdlib as pcd_module

    original = pcd_module.PyCdlib._parse_path_table

    def parse_path_table(self: PyCdlib, ptr_size: int, extent: int) -> object:
        if _inside_our_open():  # not a limit, so it holds under UNLIMITED too
            _check_path_table(self, ptr_size, extent)
        return original(self, ptr_size, extent)

    setattr(pcd_module.PyCdlib, "_parse_path_table", parse_path_table)

    from pycdlib import path_table_record as ptr_mod

    original_entry = ptr_mod.PathTableRecord.parse

    def parse_entry(self: PathTableRecord, data: bytes) -> None:
        budget = _PARSE_BUDGET.get()
        if budget is not None:
            budget.add_path_table_entry()
        original_entry(self, data)

    setattr(ptr_mod.PathTableRecord, "parse", parse_entry)
    _PYCDLIB_PATH_TABLE_BOUND_INSTALLED = True


def _check_path_table(iso: PyCdlib, ptr_size: int, extent: int) -> None:
    """Refuse a path table past the image or over the parse budget; see above."""
    cdfp = cast("BinaryIO", getattr(iso, "_cdfp"))
    block_size = cast(int, getattr(iso, "logical_block_size"))
    position = cdfp.tell()
    image_length = cdfp.seek(0, io.SEEK_END)
    cdfp.seek(position)
    start = extent * block_size
    if start + ptr_size > image_length:
        raise CorruptionError(
            f"Error reading ISO image: the path table at block {extent} declares "
            f"{ptr_size} bytes and the image holds {max(0, image_length - start)} "
            "from there"
        )
    budget = _PARSE_BUDGET.get()
    if budget is None:
        return
    # The descriptor whose table this is; pycdlib parses the PVD's, then Joliet's.
    descriptors = [getattr(iso, "pvd"), *cast("list[object]", getattr(iso, "svds"))]
    vd = next(
        (
            vd
            for vd in descriptors
            if extent
            in (
                getattr(vd, "path_table_location_le", None),
                getattr(vd, "path_table_location_be", None),
            )
        ),
        descriptors[0],
    )
    budget.add_path_table(vd, ptr_size)


_install_pycdlib_path_table_bound()

# ECMA-167 4/14.9 (File Entry, tag 261) and 4/14.17 (Extended File Entry, tag 266):
# the offset of the little-endian L_EA field, followed by L_AD, and the size of the
# fixed part both lengths are added to.
_UDF_FILE_ENTRY_LAYOUT = {261: (168, 176), 266: (208, 216)}
_PYCDLIB_UDF_COUNTER_INSTALLED = False


def _udf_file_entry_size(icbdata: bytes) -> int:
    """The bytes pycdlib parses from one UDF File Entry, from its own length fields.

    pycdlib reads the ICB's whole extent and parses ``L_AD`` bytes of it into one
    object per allocation descriptor, so the cost is the fixed part plus ``L_EA`` plus
    ``L_AD``, capped by what was read. An all-zero entry (which pycdlib keeps as no
    entry) or any other tag costs nothing here; pycdlib refuses the other tags itself.
    """
    layout = _UDF_FILE_ENTRY_LAYOUT.get(int.from_bytes(icbdata[:2], "little"))
    if layout is None or len(icbdata) < layout[1]:
        return 0
    lengths_at, fixed = layout
    extended_attrs, alloc_descs = struct.unpack_from("<LL", icbdata, lengths_at)
    return min(len(icbdata), fixed + extended_attrs + alloc_descs)


def _install_pycdlib_udf_counter() -> None:
    """Weigh the UDF tree pycdlib parses in ``open_fp`` against ``_PARSE_BUDGET``.

    pycdlib's ``_walk_udf_directories`` calls ``pycdlib.udf.parse_file_ident`` for each
    File Identifier and ``pycdlib.udf.parse_file_entry`` for each File Entry it names;
    both are looked up on the module at each call, so wrapping them there counts
    every one. A File Entry is weighed before pycdlib parses it, from the lengths it
    declares; a File Identifier after, because its size is bounded by its own 8- and
    16-bit length fields and only the parse says whether it is the parent entry, which
    is not a member. Installed once and transparent outside ``IsoReader``'s
    ``open_fp``, like the hooks above.
    """
    global _PYCDLIB_UDF_COUNTER_INSTALLED
    if pycdlib is None or _PYCDLIB_UDF_COUNTER_INSTALLED:
        return
    from pycdlib import udf as udf_mod

    original_ident = udf_mod.parse_file_ident
    original_entry = udf_mod.parse_file_entry

    def parse_file_ident(
        data: bytes, current_extent: int, part_start: int, udf_file_entry: object
    ) -> tuple[UDFFileIdentifierDescriptor, int]:
        result = original_ident(data, current_extent, part_start, udf_file_entry)
        budget = _PARSE_BUDGET.get()
        if budget is not None:
            file_ident, nbytes = result
            budget.add_udf_bytes(nbytes)
            if not file_ident.is_parent():
                budget.add_udf_member()
        return result

    def parse_file_entry(
        icbdata: bytes,
        abs_file_entry_extent: int,
        icb_log_block_num: int,
        parent: UDFFileEntry | None,
    ) -> UDFFileEntry | None:
        budget = _PARSE_BUDGET.get()
        if budget is not None:
            budget.add_udf_bytes(_udf_file_entry_size(icbdata))
        return original_entry(icbdata, abs_file_entry_extent, icb_log_block_num, parent)

    setattr(udf_mod, "parse_file_ident", parse_file_ident)
    setattr(udf_mod, "parse_file_entry", parse_file_entry)
    _PYCDLIB_UDF_COUNTER_INSTALLED = True


_install_pycdlib_udf_counter()

# Exceptions that mean "this ISO structure is bad", translated to CorruptionError.
# pycdlib wraps *most* format errors in PyCdlibException, but it is not hardened against
# crafted/truncated input: fuzzing surfaces bare IndexError, struct.error, UnicodeDecodeError,
# AttributeError ("'NoneType' object has no attribute …"), KeyError, and ValueError raised deep
# in its header/path-table/directory-record parsing, so all are translated to CorruptionError
# rather than leaking as raw exceptions. The cost of so broad a set: this backend also reads
# pycdlib internals itself (`_nm_name`, `_rr_entry_groups`, `_times`, `_px`,
# `_check_path_table`), inside the same window, so a bug in that code (an attribute pycdlib
# no longer sets, say) is reported as a corrupt image rather than crashing. That is how an
# unguarded `ce_entries is None` on pycdlib 1.20+ showed up. We keep the broad set because
# telling a pycdlib frame from ours would need traceback inspection on every error; the
# guard is to run the ISO suites against newer pycdlib releases (`uv run --with pycdlib==X`).
# A genuine OSError from the underlying handle (file not found, permission, media error) is
# deliberately NOT in this set: it is real I/O and MUST propagate unchanged (see
# error-handling: "Genuine runtime and I/O errors are not reclassified"). Built defensively so
# the module imports without pycdlib.
_PYCDLIB_ERRORS: tuple[type[Exception], ...] = (
    (_pycdlib_exc.PyCdlibException,) if _pycdlib_exc is not None else ()
) + (IndexError, struct.error, UnicodeDecodeError, AttributeError, KeyError, ValueError)


# Trailing ";1"/";42" version suffix on an ISO 9660 or Joliet file identifier. ``\Z``,
# not ``$``: ``$`` also matches before a final newline, which would drop it from the name.
_VERSION_SUFFIX = re.compile(r";(\d+)\Z")

# How many records under a Rock Ridge directory are searched for a file that also sits
# in the Joliet tree, to find that directory's Joliet counterpart. Bounds the cost of a
# crafted image full of directories whose names are not UTF-8.
_JOLIET_SEARCH_RECORDS = 64


def _nm_name(record: DirectoryRecord) -> bytes | None:
    """The name a record's Rock Ridge ``NM`` entries spell, or ``None`` without any.

    ``RockRidge.name()`` alone does not tell: with no ``NM`` entry, which RRIP allows and
    which a System Use area cut before its ``NM`` leaves, pycdlib hands back the ISO
    9660 identifier, version suffix and all.
    """
    rr = record.rock_ridge
    if rr is None or not any(e.nm_records for e in _rr_entry_groups(rr)):
        return None
    return bytes(rr.name())


def _rr_entry_groups(rr: RockRidge) -> tuple[RockRidgeEntries, ...]:
    """The entry groups of a Rock Ridge record: its own area's, then its continuation's.

    From pycdlib 1.20, ``ce_entries`` stays ``None`` until an entry spills into a
    continuation area, so a record without one has only ``dr_entries``. On older
    pycdlib both groups always exist.
    """
    ce: RockRidgeEntries | None = rr.ce_entries
    return (rr.dr_entries,) if ce is None else (rr.dr_entries, ce)


def _strip_version(name: str, *, iso9660: bool) -> tuple[str, int | None]:
    """``name`` without its ``;N`` version, and ``N`` (``None`` without one).

    With ``iso9660`` (plain ISO 9660 identifiers), an empty extension's ``.`` goes too
    (``FOO.;1`` is ``FOO``), and a bare ``;N`` is a name, not a version: it comes back
    unchanged with ``None``. Without it (Joliet), the suffix always goes and its version
    is reported, so ``;1`` becomes ``""`` with version 1. Neither Joliet caller lets that
    empty name reach a member: ``_joliet_name`` drops it in ``_ascii_runs_match``, and
    ``_joliet_text`` only feeds extent matching.
    """
    match = _VERSION_SUFFIX.search(name)
    if match is None or (iso9660 and match.start() == 0):
        return name, None
    stem = name[: match.start()]
    if iso9660 and len(stem) > 1 and stem.endswith("."):
        stem = stem[:-1]
    return stem, int(match.group(1))


def _iso_ident_name(record: DirectoryRecord) -> bytes:
    """A record's ISO 9660 identifier, version and empty-extension dot removed."""
    # Latin-1 maps each byte to one character, and none of 0x80-0xff is a digit.
    ident = bytes(record.file_identifier()).decode("latin-1")
    return _strip_version(ident, iso9660=True)[0].encode("latin-1")


def _byte_ascii_runs(raw: bytes) -> tuple[str, ...]:
    """The ASCII text of a byte name, split where non-ASCII bytes are."""
    return tuple(run.decode("ascii") for run in re.split(rb"[\x80-\xff]+", raw))


def _decode_joliet(ident: bytes) -> str:
    """A Joliet identifier as text; never raises.

    The identifier is UTF-16BE code units, and NTFS lets a name hold a surrogate
    without its partner, so the decode uses ``surrogatepass``: the unit stays in the
    name, as the 7z reader keeps it, and extraction decides what reaches disk. A valid
    pair still decodes as one character. An odd trailing byte is not a unit and
    becomes U+FFFD.
    """
    cut = len(ident) - len(ident) % 2
    name = ident[:cut].decode("utf-16_be", errors="surrogatepass")
    return name + "\ufffd" if cut < len(ident) else name


def _text_ascii_runs(name: str) -> tuple[str, ...]:
    """The ASCII text of a decoded name, split where non-ASCII characters are."""
    return tuple(re.split(r"[^\x00-\x7f]+", name))


def _ascii_runs_match(raw: bytes, name: str) -> bool:
    """Whether a byte name and a decoded name have the same ASCII text between the rest.

    A Rock Ridge name in a single-byte legacy encoding and the Joliet name of the same
    file differ only where the legacy bytes are: ``caf\\xe9.txt`` and ``café.txt`` both
    read ``caf``, something, ``.txt``. A Joliet name cut at 64 characters, or the name of
    some other file, does not line up.
    """
    return _byte_ascii_runs(raw) == _text_ascii_runs(name)


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
    raising: a bad date field must not sink the whole listing. A date whose UTC
    form falls outside :class:`datetime`'s range is malformed too.
    """
    if date is None:
        return None
    try:
        tz = timezone(timedelta(minutes=date.gmtoffset * 15))
        if _is_long_form_date(date):
            hundredths = date.hundredthsofsecond
            value = datetime(
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
        elif _is_short_form_date(date):
            value = datetime(
                1900 + date.years_since_1900,
                date.month,
                date.day_of_month,
                date.hour,
                date.minute,
                date.second,
                tzinfo=tz,
            )
        else:
            return None
        # 0001-01-01 at +13:00 is a valid local time whose UTC form is before year
        # 1; modified_utc() would raise OverflowError on it later. No usable date.
        value.astimezone(UTC)
    except (ValueError, AttributeError, TypeError, OverflowError):
        return None
    return value


def _dr_date_fields(
    date: DirectoryRecordDate | VolumeDescriptorDate,
) -> tuple[object, ...] | None:
    """The date's stored year-to-second fields, or ``None`` for an unknown object."""
    if _is_long_form_date(date):
        return (
            date.year,
            date.month,
            date.dayofmonth,
            date.hour,
            date.minute,
            date.second,
        )
    if _is_short_form_date(date):
        return (
            date.years_since_1900,
            date.month,
            date.day_of_month,
            date.hour,
            date.minute,
            date.second,
        )
    return None


def _dr_date_is_invalid(
    date: DirectoryRecordDate | VolumeDescriptorDate | None,
) -> bool:
    """Whether a stored date is present, specified, and yet not a date.

    All zeros is ISO 9660's "not specified", like ZIP's zero DOS date, and is not
    reported. Anything else :func:`_dr_date_to_datetime` returns ``None`` for (a
    month of 13, a day of 0) is a date the image stores and archivey cannot use.
    """
    if date is None or _dr_date_to_datetime(date) is not None:
        return False
    fields = _dr_date_fields(date)
    return fields is not None and any(fields)


def _is_directory_record(obj: object) -> TypeGuard[DirectoryRecord]:
    """Whether ``obj`` is a pycdlib directory record, the handle ISO members carry."""
    return _pycdlib_dr is not None and isinstance(obj, _pycdlib_dr.DirectoryRecord)


def _continuation_chain(record: DirectoryRecord) -> list[DirectoryRecord]:
    """``record`` and every record pycdlib linked after it through ``data_continuation``.

    pycdlib links *any* record whose identifier repeats the previous one in its
    directory, and sets the multi-extent flag on the earlier record as it does, so
    this chain is only a candidate: ``IsoReader._layout`` confirms it against
    the flags as written in the image.
    """
    chain = [record]
    while chain[-1].data_continuation is not None:
        chain.append(chain[-1].data_continuation)
    return chain


class _RawDirectory(NamedTuple):
    """What a directory's records say on disc that pycdlib does not keep as written."""

    # Extents of the records whose multi-extent flag is set.
    flagged: frozenset[int]
    # Declared data length of each record whose data reaches the end of the image,
    # keyed by (extent, identifier): the records pycdlib may have clamped.
    lengths_to_end: Mapping[tuple[int, bytes], int]


def _parse_raw_directory(
    directory_data: bytes, block_size: int, image_length: int
) -> _RawDirectory:
    """The multi-extent flags and clamped lengths in a directory's data.

    Walks the raw records as ECMA-119 §9.1 lays them out: byte 0 is the record length,
    bytes 2-5 the extent and bytes 10-13 the data length (little-endian), byte 25 the
    file flags (bit 7 multi-extent), byte 32 the identifier length and the identifier
    from byte 33. A zero length byte pads to the end of the sector. Only the non-zero
    lengths that reach ``image_length`` are kept, so the result grows with the records
    pycdlib may have changed rather than with the directory. The ``>=`` is
    load-bearing: pycdlib clamps on ``>``, so every clamped length is kept, including
    one clamped to zero, and ``IsoReader._layout`` reads a zero-length miss as a
    genuinely empty file.
    """
    flagged: set[int] = set()
    lengths: dict[tuple[int, bytes], int] = {}
    offset = 0
    while offset + 33 <= len(directory_data):
        length = directory_data[offset]
        if length == 0:
            offset += block_size - offset % block_size
            continue
        extent = int.from_bytes(directory_data[offset + 2 : offset + 6], "little")
        if directory_data[offset + 25] & 0x80:
            flagged.add(extent)
        data_length = int.from_bytes(
            directory_data[offset + 10 : offset + 14], "little"
        )
        if data_length and extent * block_size + data_length >= image_length:
            ident_length = directory_data[offset + 32]
            ident = bytes(directory_data[offset + 33 : offset + 33 + ident_length])
            lengths[(extent, ident)] = data_length
        offset += length
    return _RawDirectory(frozenset(flagged), lengths)


class _Extent(NamedTuple):
    """One extent of a file: its first sector and its length as declared on disc."""

    sector: int
    length: int


def _is_entry(child: DirectoryRecord | None) -> TypeGuard[DirectoryRecord]:
    """Whether a directory's child record is an entry: not ``None``, ``.`` or ``..``."""
    return child is not None and not child.is_dot() and not child.is_dotdot()


def _yield_children(
    record: DirectoryRecord, rock_ridge: bool
) -> Iterator[DirectoryRecord]:
    """A directory record's entries, as pycdlib's own ``walk()`` enumerates them.

    ``pycdlib.pycdlib._yield_children`` is private, but it is the one place pycdlib
    skips the extra records of a multi-extent file and follows Rock Ridge CL/PL
    relocation. The public ``list_children`` does the same only behind a path lookup,
    which is the step the record walk exists to avoid. The ISO tests exercise it on
    every supported pycdlib, so a rename there fails loudly rather than silently.
    """
    assert _pycdlib_core is not None
    return filter(_is_entry, _pycdlib_core._yield_children(record, rock_ridge))


# zisofs: the 16-byte header at the start of a compressed file's data, then one
# little-endian 32-bit pointer per block plus one, each an offset from the start of the
# data. Block ``i`` is the zlib stream between pointers ``i`` and ``i + 1``; two equal
# pointers stand for a block of zeros. (Linux ``fs/isofs/compress.c``, ``mkzftree``.)
_ZISOFS_MAGIC = b"\x37\xe4\x53\x96\xc9\xdb\xd6\x07"
_ZISOFS_HEADER_SIZE = 16
# The block sizes the format allows: 32, 64 and 128 KiB. One block is held at a time.
_ZISOFS_LOG2_BLOCK_SIZES = range(15, 18)


def _zisofs_refusal(entry: _ZisofsEntry) -> str | None:
    """Why a zisofs entry describes data this reader does not decode, or ``None``."""
    if entry.uncompressed_size is None:
        return f"a zisofs {entry.tag.decode('ascii')} entry too short to parse"
    if entry.tag != b"ZF":
        return f"zisofs2 under the {entry.tag.decode('ascii')} tag"
    if entry.version != 1:
        return f"zisofs version {entry.version} (only version 1 is read)"
    if entry.algorithm != b"pz":
        return f"zisofs algorithm {entry.algorithm!r} (only 'pz', zlib, is read)"
    if entry.header_size != _ZISOFS_HEADER_SIZE:
        return f"a zisofs header of {entry.header_size} bytes"
    if entry.log2_block_size not in _ZISOFS_LOG2_BLOCK_SIZES:
        return f"a zisofs block size of 2**{entry.log2_block_size} bytes"
    return None


class _ZisofsStream(io.RawIOBase):
    """Seekable decoded view of one zisofs-compressed file.

    ``inner`` is the file's data as stored. Each read inflates the block it lands in,
    capped one byte past the block size, so a crafted block that inflates further is
    detected as corruption rather than cut to fit. The zlib stream must also end,
    checksum included, exactly at the next pointer, and a span longer than any deflated
    block of the block size is refused before it is read.
    """

    def __init__(
        self, inner: BinaryIO, entry: _ZisofsEntry, stored_size: int | None
    ) -> None:
        super().__init__()
        self._inner = inner
        self._stored_size = stored_size
        header = inner.read(_ZISOFS_HEADER_SIZE)
        if len(header) < _ZISOFS_HEADER_SIZE:
            raise TruncatedError("zisofs header is cut short")
        if header[:8] != _ZISOFS_MAGIC:
            raise CorruptionError("zisofs file does not start with the zisofs magic")
        size = int.from_bytes(header[8:12], "little")
        if (
            size != entry.uncompressed_size
            or header[12] * 4 != entry.header_size
            or header[13] != entry.log2_block_size
        ):
            raise CorruptionError(
                "zisofs header disagrees with the Rock Ridge ZF entry"
            )
        self._size = size
        self._log2 = entry.log2_block_size
        self._block_size = 1 << self._log2
        # zlib's ``compressBound``: no block of this size deflates to more, so a
        # longer span is corruption and is never read into memory.
        n = self._block_size
        self._max_packed = n + (n >> 12) + (n >> 14) + (n >> 25) + 13
        self._position = 0
        self._cached_index = -1
        self._cached = b""

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._position

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        # A relative seek to before the start clamps to 0, as io.BytesIO and the other
        # member streams do. ArchiveStream.seek rejects a negative SEEK_SET and a bad
        # whence before they reach here: the ISO translator reads ValueError as
        # corruption.
        self._position = resolve_seek(
            offset, whence, pos=self._position, end=lambda: self._size
        )
        return self._position

    def readinto(self, b: WriteableBuffer, /) -> int:
        view = memoryview(b).cast("B")
        if self._position >= self._size or not len(view):
            return 0
        index = self._position >> self._log2
        block = self._block(index)
        start = self._position - (index << self._log2)
        count = min(len(view), len(block) - start)
        view[:count] = block[start : start + count]
        self._position += count
        return count

    def _block(self, index: int) -> bytes:
        if index == self._cached_index:
            return self._cached
        expected = min(self._block_size, self._size - (index << self._log2))
        self._inner.seek(_ZISOFS_HEADER_SIZE + 4 * index)
        pointers = self._inner.read(8)
        if len(pointers) < 8:
            raise TruncatedError(f"zisofs block pointer {index} is cut short")
        start = int.from_bytes(pointers[:4], "little")
        end = int.from_bytes(pointers[4:], "little")
        if (
            end < start
            or end - start > self._max_packed
            or (self._stored_size is not None and end > self._stored_size)
        ):
            raise CorruptionError(
                f"zisofs block {index} spans {start}..{end} in {self._stored_size} "
                "stored bytes"
            )
        if start == end:
            data = bytes(expected)
        else:
            self._inner.seek(start)
            packed = self._inner.read(end - start)
            if len(packed) < end - start:
                raise TruncatedError(f"zisofs block {index} is cut short")
            # One byte past the block size, so a block that inflates further is seen
            # rather than cut to fit.
            decompressor = zlib.decompressobj()
            try:
                data = decompressor.decompress(packed, expected + 1)
            except zlib.error as exc:
                raise CorruptionError(f"zisofs block {index}: {exc}") from exc
            if len(data) != expected:
                raise CorruptionError(
                    f"zisofs block {index} holds {len(data)} bytes, not {expected}"
                )
            # The stream has to end, checksum included, where the pointers say: a
            # block cut before its Adler-32 or followed by other bytes is not verified.
            if not decompressor.eof or decompressor.unused_data:
                raise CorruptionError(
                    f"zisofs block {index} does not end where its pointers say"
                )
        self._cached_index, self._cached = index, data
        return data

    def close(self) -> None:
        if not self.closed:
            # ``getattr``: ``IOBase.__del__`` closes an instance whose ``__init__``
            # raised, possibly before ``_inner`` was set.
            inner = getattr(self, "_inner", None)
            try:
                if inner is not None:
                    inner.close()
            finally:
                self._cached = b""
                super().close()


class _PyCdlibStream(DelegatingStream):
    """Adapt pycdlib's ``PyCdlibIO`` (a one-file context manager) onto ``DelegatingStream``.

    ``PyCdlibIO`` must be *entered* before use — its context manager sets up the read offset —
    so this enters it in ``__init__`` and relies on ``DelegatingStream.close()`` (which calls
    ``inner.close()``, the exact equivalent of ``PyCdlibIO.__exit__``) to exit it, keeping the
    enter/exit lifecycle paired. Read/seek/tell/seekable are inherited delegation.
    """

    def __init__(self, raw: PyCdlibIO) -> None:
        # PyCdlibIO is an io.RawIOBase, which is a BinaryIO at runtime but not by typeshed's
        # nominal hierarchy, so cast at the DelegatingStream boundary.
        super().__init__(cast("BinaryIO", raw))
        raw.__enter__()  # set up the read offset; close() -> inner.close() exits the context
        self._raw = raw

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        # pycdlib raises its own PyCdlibInvalidInput on a relative seek before the start,
        # which the translator must read as corruption. Resolve every seek here and
        # clamp a relative one to the origin, as BytesIO does.
        target = resolve_seek(
            offset, whence, pos=self._raw.tell(), end=lambda: self._raw.length()
        )
        return super().seek(target, io.SEEK_SET)


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
        self._encoding = encoding
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
        # The extents of each listed file that pycdlib's inode does not describe as
        # written, keyed by ``id`` of its record; ``None`` when a clamped length could
        # not be found on disc. Worked out while listing, where the image may be read
        # under the handle guard, because ``_data_inode`` runs where that guard may
        # already be held. Every other file reads through pycdlib's inode as it is.
        self._layouts: dict[int, tuple[_Extent, ...] | None] = {}
        # What each directory's records say on disc, per directory extent read.
        self._raw_directories: dict[int, _RawDirectory] = {}
        # What the System Use filter kept aside while ``open_fp`` parsed the Rock Ridge
        # areas: zisofs entries, and the areas it cut short.
        self._system_use = _SystemUseNotes(self._iso)
        # The Joliet tree's files by extent, and by extent and the ASCII runs of their
        # name, built the first time a Rock Ridge name is not UTF-8 and ``encoding=``
        # does not decode it; and the records whose name was taken from their Joliet
        # counterpart, by ``id()``, to report on the member. Unlike ``_SystemUseNotes``
        # no anchor is kept: the keys are pycdlib's parsed records, which ``self._iso``
        # holds for the reader's whole life.
        self._joliet_files: dict[int, list[DirectoryRecord]] | None = None
        self._joliet_files_by_runs: dict[
            tuple[int, tuple[str, ...]], list[DirectoryRecord]
        ] = {}
        self._joliet_named: dict[int, DirectoryRecord] = {}
        # Each Rock Ridge directory's children by stored name, per directory extent,
        # built the first time a link target is followed through it, so following
        # many targets through one wide directory stays linear.
        self._rock_ridge_children: dict[int, dict[bytes, DirectoryRecord]] = {}
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
                    # Kept for ``_data_inode``, which reads extents pycdlib has no
                    # inode for through the same handle pycdlib reads from.
                    self._iso_fp = self._track_source_seeks(source)
                    limits = config.listing_limits
                    budget = (
                        None
                        if limits.max_members is None
                        and limits.max_metadata_bytes is None
                        else _ParseBudget(limits)
                    )
                    token = _SYSTEM_USE_NOTES.set(self._system_use)
                    budget_token = _PARSE_BUDGET.set(budget)
                    try:
                        self._iso.open_fp(self._iso_fp)
                    finally:
                        _PARSE_BUDGET.reset(budget_token)
                        _SYSTEM_USE_NOTES.reset(token)
                    self._iso_opened = True
                    # pycdlib measured the image the same way inside ``open_fp``.
                    self._image_length = self._iso_fp.seek(0, 2)

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
        stem, version = _strip_version(base, iso9660=True)
        return parent + sep + stem, version

    def _version_order(self, item: tuple[str, bytes, object]) -> tuple[str, int]:
        """Sort key putting each plain-ISO name's versions in ascending order."""
        presented, version = self._split_version(item[0])
        return presented, version or 0

    def _decode_bytes_name(
        self, raw: bytes, *, rr_record: DirectoryRecord | None = None
    ) -> str:
        """Decode a Rock Ridge or plain ISO 9660 name, or a Rock Ridge link target.

        Nothing in the image says which charset these bytes are in: a Rock Ridge name
        is whatever the writer's locale was. UTF-8 is tried first; bytes that are not
        valid UTF-8 are decoded with ``encoding=`` when the caller gave one, as TAR
        does for its names, then by the name of the Joliet counterpart of the Rock Ridge
        record ``rr_record`` when one is given (``_joliet_name``), and with UTF-8 and ``surrogateescape`` otherwise.

        Decoding always returns. A codec ``open_archive`` accepted can still fail on
        these bytes (``utf-32`` on a length that is not a multiple of four, or
        ``idna``, which has no ``surrogateescape``); the name then decodes as it would
        with no ``encoding=``.
        """
        decoded = self._decode_known(raw)
        if decoded is None and rr_record is not None:
            # Only a Rock Ridge image has Rock Ridge names and link targets to fall
            # back from.
            assert self._namespace == "rock_ridge"
            decoded = self._joliet_name(rr_record, raw)
        if decoded is None:
            decoded = raw.decode("utf-8", errors="surrogateescape")
        return decoded

    def _decode_known(self, raw: bytes) -> str | None:
        """``raw`` as UTF-8, else with ``encoding=``; ``None`` when neither applies."""
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            pass
        if self._encoding is not None:
            try:
                return raw.decode(self._encoding, errors="surrogateescape")
            except UnicodeError:
                pass
        return None

    def _joliet_name(self, record: DirectoryRecord, raw: bytes) -> str | None:
        """The Joliet name of the file or directory a Rock Ridge record describes.

        Rock Ridge names carry no charset, but most images that have them also have a
        Joliet tree, whose UTF-16 names a legacy name was converted into when the image
        was written. The two trees are separate, so the counterpart is found through
        the data: a file by its extent, a directory as the Joliet parent of a file
        found under it. The Joliet name is used only when its ASCII text lines up with
        the Rock Ridge bytes (``_ascii_runs_match``); otherwise ``None``.
        """
        if not self._iso.has_joliet():
            return None
        if self._joliet_files is None:
            self._index_joliet_files()
        counterpart = (
            self._joliet_directory(record)
            if record.is_dir()
            else self._joliet_file(record, raw)
        )
        if counterpart is None:
            return None
        ident = bytes(counterpart.file_identifier())
        if len(ident) % 2:
            # A damaged Joliet name: the Rock Ridge record escapes its own bytes
            # rather than borrow a name that ``_decode_joliet`` ends with U+FFFD.
            return None
        name, _ = _strip_version(_decode_joliet(ident), iso9660=False)
        if not _ascii_runs_match(raw, name):
            return None
        self._joliet_named[id(record)] = counterpart
        return name

    def _index_joliet_files(self) -> None:
        """Index every file record of the Joliet tree; each directory read once.

        Every empty file of a genisoimage image shares one extent, so the files are
        also indexed by the ASCII runs of their name: finding the one a Rock Ridge name
        lines up with is then one lookup, not a match against each file at the extent.
        """
        files: dict[int, list[DirectoryRecord]] = {}
        by_runs = self._joliet_files_by_runs
        root = self._iso.get_record(joliet_path="/")
        seen = {root.extent_location()}
        stack = [root]
        while stack:
            for child in _yield_children(stack.pop(), False):
                extent = child.extent_location()
                if not child.is_dir():
                    files.setdefault(extent, []).append(child)
                    runs = _text_ascii_runs(self._joliet_text(child))
                    by_runs.setdefault((extent, runs), []).append(child)
                elif extent not in seen:
                    seen.add(extent)
                    stack.append(child)
        self._joliet_files = files

    def _joliet_file(
        self, record: DirectoryRecord, raw: bytes
    ) -> DirectoryRecord | None:
        """The one Joliet file at this record's extent whose name lines up with ``raw``.

        Empty files and hard links share an extent, so the name has to single one out.
        """
        candidates = self._joliet_files_by_runs.get(
            (record.extent_location(), _byte_ascii_runs(raw)), ()
        )
        return candidates[0] if len(candidates) == 1 else None

    def _joliet_directory(self, record: DirectoryRecord) -> DirectoryRecord | None:
        """The Joliet directory holding the same files as this Rock Ridge directory.

        Searches the first ``_JOLIET_SEARCH_RECORDS`` records under it, breadth first,
        for a non-empty file with exactly one Joliet record at its extent, then climbs
        that record's Joliet parents as many levels as the file sits below ``record``.
        """
        assert self._joliet_files is not None
        queue = [(record, 1)]
        visited = 0
        while queue and visited < _JOLIET_SEARCH_RECORDS:
            directory, depth = queue.pop(0)
            for child in _yield_children(directory, True):
                visited += 1
                if visited > _JOLIET_SEARCH_RECORDS:
                    break
                if child.is_dir():
                    queue.append((child, depth + 1))
                    continue
                joliet = self._joliet_files.get(child.extent_location(), ())
                if child.get_data_length() == 0 or len(joliet) != 1:
                    continue
                found: DirectoryRecord | None = joliet[0]
                for _ in range(depth):
                    found = None if found is None else found.parent
                return found if found is not None and found.is_dir() else None
        return None

    @staticmethod
    def _joliet_text(record: DirectoryRecord) -> str:
        name = _decode_joliet(bytes(record.file_identifier()))
        return _strip_version(name, iso9660=False)[0]

    def _record_name(self, record: DirectoryRecord) -> tuple[str, bytes]:
        """One directory record's own name in the selected namespace, and its bytes.

        Decoding never raises: a name that is not valid in its namespace's encoding is
        rendered (surrogateescape for the byte namespaces; for Joliet's UTF-16, a lone
        surrogate kept as that unit and an odd trailing byte as U+FFFD) rather than
        costing the listing. The bytes are the name as stored for the byte namespaces,
        and the UTF-8 of the decoded name for Joliet, a lone surrogate as its three
        bytes.
        """
        if self._namespace == "rock_ridge":
            nm = _nm_name(record)
            if nm is not None:
                return self._decode_bytes_name(nm, rr_record=record), nm
            ident = _iso_ident_name(record)
            return self._decode_bytes_name(ident), ident
        ident = bytes(record.file_identifier())
        if self._namespace == "joliet":
            name = _decode_joliet(ident)
            return name, name.encode("utf-8", errors="surrogatepass")
        return self._decode_bytes_name(ident), ident

    def _is_rr_moved(self, record: DirectoryRecord) -> bool:
        """Whether a root-level directory is Rock Ridge's ``rr_moved`` relocation parent.

        ISO 9660 caps depth at eight, so writers park deeper subtrees under a root-level
        directory and relink each one into place with CL/PL/RE records. That directory is
        the workaround's scaffolding, not part of the tree anyone archived. It is known by
        its contents rather than its name: every child is a relocated directory (its
        ``..`` carries a PL record), which is the same test pycdlib uses to hide those
        children from their parking place.
        """
        children = [c for c in record.children if _is_entry(c)]
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

    def _walk_records(self) -> Iterator[tuple[str, bytes, DirectoryRecord, bool]]:
        """Yield ``(namespace path, its bytes, directory record, superseded)`` per entry.

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
        stack: list[tuple[str, bytes, DirectoryRecord]] = [("/", b"", root)]
        while stack:
            dirpath, raw_dirpath, dir_record = stack.pop()
            dirs: list[tuple[str, bytes, DirectoryRecord]] = []
            files: list[tuple[str, bytes, DirectoryRecord]] = []
            for child in _yield_children(dir_record, use_rr):
                if (
                    use_rr
                    and dirpath == "/"
                    and child.is_dir()
                    and self._is_rr_moved(child)
                ):
                    continue
                name, raw_name = self._record_name(child)
                path = self._join(dirpath, name)
                raw_path = raw_name if dirpath == "/" else raw_dirpath + b"/" + raw_name
                (dirs if child.is_dir() else files).append((path, raw_path, child))
            if self._namespace == "iso9660":
                files.sort(key=self._version_order)
            newest: dict[str, int] = {}
            for path, _, _ in files:
                presented, version = self._split_version(path)
                if version is not None:
                    newest[presented] = max(newest.get(presented, version), version)
            for path, raw_path, record in dirs:
                yield path, raw_path, record, False
            for path, raw_path, record in files:
                presented, version = self._split_version(path)
                superseded = version is not None and version < newest[presented]
                yield path, raw_path, record, superseded
            for path, raw_path, record in reversed(dirs):
                extent = record.extent_location()
                if extent in seen_extents:
                    continue
                seen_extents.add(extent)
                stack.append((path, raw_path, record))

    def _iter_members(self) -> Iterator[ArchiveMember]:
        # Pinned-pycdlib audit (tar-concurrent-open 2.7 / concurrent-member-streams 5.4):
        # the record walk traverses in-memory parsed catalog records and reads nothing
        # from the image. ``_make_member`` can: for a repeated identifier or a file
        # whose data ends at the end of the image, ``_raw_directory`` re-reads the
        # directory's extent through ``_cdfp`` and takes the handle guard itself. The
        # Joliet name fallback reads nothing either: ``has_joliet()`` tests a parsed
        # descriptor, ``get_record(joliet_path="/")`` looks among parsed records, and
        # ``_yield_children`` on the Joliet tree walks them. Following a link target
        # (``_rock_ridge_child``) is the walk's own ``_yield_children`` over Rock Ridge
        # records already parsed, and ``_nm_name`` reads the parsed ``NM`` records
        # (``dr_entries``/``ce_entries``). Any other image read added to listing needs
        # the same guard. If a future pycdlib version gains handle access in the walk,
        # lock the complete call.
        with self._translated_errors():
            # ``index`` is each member's position in the walk, the id registration
            # stamps, so a diagnostic raised while typing can name it.
            for index, (ns_path, raw_path, record, superseded) in enumerate(
                self._walk_records()
            ):
                yield self._make_member(
                    ns_path, raw_path, record, index, superseded=superseded
                )

    def _make_member(
        self,
        ns_path: str,
        raw_path: bytes,
        record: DirectoryRecord,
        index: int,
        *,
        superseded: bool = False,
    ) -> ArchiveMember:
        rr = getattr(record, "rock_ridge", None)
        raw_mode, uid, gid = self._px(rr)

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
        raw_name = raw_path
        extra = (
            MemberExtra({"iso.version": version})
            if version is not None
            else MemberExtra()
        )

        modified, accessed, created, ctime, invalid_dates = self._timestamps(record, rr)
        mode = stat.S_IMODE(raw_mode) if raw_mode is not None else None
        link_target = self._symlink_target(member_type, record, rr)

        size = self._file_size(record) if member_type == MemberType.FILE else None
        compressed_size = size
        compression = (
            (CompressionMethod(algo=CompressionAlgorithm.STORED),)
            if member_type == MemberType.FILE
            else ()
        )
        zisofs = self._zisofs_entry(record) if member_type == MemberType.FILE else None
        if zisofs is not None:
            # Rock Ridge transparent compression: the extent holds zisofs blocks of
            # zlib data, and the ZF entry declares the size they decode to.
            size = zisofs.uncompressed_size
            # A variant this reader refuses to decode is not reported as deflate.
            algo = (
                CompressionAlgorithm.DEFLATE
                if _zisofs_refusal(zisofs) is None
                else CompressionAlgorithm.UNKNOWN
            )
            compression = (CompressionMethod(algo=algo),)

        member = ArchiveMember(
            type=member_type,
            name=name,
            raw_name=raw_name,
            size=size,
            # The stored length; for a zisofs member ``size`` is the decoded one.
            compressed_size=compressed_size,
            modified=modified,
            accessed=accessed,
            created=created,
            ctime=ctime,
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
        self._emit_system_use_cut(member, rr, index)
        # The message names the normalized name, so it is built here, not in
        # ``_timestamps``.
        for field, source, value_repr in invalid_dates:
            message = f"Invalid ISO 9660 date for {quoted(member.name)}: {value_repr}"
            issue = TimestampIssue(
                field=field, source=source, value_repr=value_repr, message=message
            )
            # ISO sends every diagnostic to the collector's default logger; so does this.
            self._emit_timestamp_invalid(member, index, issue, log=None)
        if id(record) in self._joliet_named:
            self._diagnostics_collector.emit(
                code=DiagnosticCode.MEMBER_NAME_ENCODING_INFERRED,
                message=(
                    "Rock Ridge name is not valid UTF-8; the name is taken from the "
                    f"Joliet tree: {quoted(member.name)}"
                ),
                context=NameEncodingContext(
                    archive_name=self._archive_name,
                    member_name=member.name,
                    member_id=index,
                    raw_name_base64=raw_name_to_base64(member.raw_name),
                    # No decode of the stored bytes happened: the name is another
                    # record's, and Rock Ridge declares no charset.
                    inferred_encoding="",
                    declared_encoding="",
                ),
                member=member,
                attach_to_member=True,
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

    def _zisofs_entry(self, record: DirectoryRecord) -> _ZisofsEntry | None:
        rr = getattr(record, "rock_ridge", None)
        return None if rr is None else self._system_use.zisofs_entry(rr)

    def _emit_system_use_cut(
        self, member: ArchiveMember, rr: RockRidge | None, index: int
    ) -> None:
        """Report a Rock Ridge area the System Use filter cut short, if this one was.

        What followed the malformed entry is lost, so a symlink's target, which may
        have run on past it, is withheld rather than reported cut short.
        """
        reason = None if rr is None else self._system_use.dropped_reason(rr)
        if reason is None:
            return
        self._diagnostics_collector.emit(
            code=DiagnosticCode.MEMBER_HEADER_RECORD_SKIPPED,
            message=(
                f"The Rock Ridge entries of {quoted(member.name)} are malformed "
                f"({reason}); the member is listed from the entries before that."
            ),
            context=MemberHeaderRecordContext(
                archive_name=self._archive_name,
                member_name=member.name,
                member_id=index,
                # The entry list was cut short, so no one record is named.
                record="",
                reason=reason,
                list_truncated=True,
            ),
            member=member,
            attach_to_member=True,
        )
        if member.type == MemberType.SYMLINK:
            member.link_target = None
            self._emit_link_target_unavailable(
                member,
                reason="target_record_malformed",
                message=(
                    f"The symlink target of {quoted(member.name)} may run past a "
                    "malformed Rock Ridge entry; leaving link_target unset."
                ),
                target_in_archive=True,
                member_id=index,
            )

    def _timestamps(
        self, record: DirectoryRecord, rr: RockRidge | None
    ) -> tuple[
        datetime | None,
        datetime | None,
        datetime | None,
        datetime | None,
        list[tuple[str, str, str]],
    ]:
        """Return ``(modified, accessed, created, ctime, invalid)``.

        ``ctime`` is the Rock Ridge attribute-change time (POSIX ``st_ctime``). It
        never fills ``created``, which holds only a TF creation time. ``invalid``
        lists each stored date that is not a date, as ``(field, source,
        value_repr)``, for a ``MEMBER_TIMESTAMP_INVALID`` report.
        """
        invalid: list[tuple[str, str, str]] = []

        def convert(
            date: DirectoryRecordDate | VolumeDescriptorDate | None,
            field: str,
            source: str,
        ) -> datetime | None:
            if _dr_date_is_invalid(date):
                assert date is not None
                invalid.append((field, source, repr(_dr_date_fields(date))))
            return _dr_date_to_datetime(date)

        modified: datetime | None = None
        accessed: datetime | None = None
        created: datetime | None = None
        ctime: datetime | None = None
        if rr is not None:
            # Rock Ridge TF entries carry the POSIX times (in dr_entries, or the CE
            # overflow area). A TF modification time wins over the directory-record
            # date, which cannot hold hundredths or the long form's four-digit year.
            for entries in _rr_entry_groups(rr):
                tf = getattr(entries, "tf_record", None)
                if tf is None:
                    continue
                modified = modified or convert(
                    getattr(tf, "modification_time", None), "modified", "rock_ridge"
                )
                accessed = accessed or convert(
                    getattr(tf, "access_time", None), "accessed", "rock_ridge"
                )
                created = created or convert(
                    getattr(tf, "creation_time", None), "created", "rock_ridge"
                )
                ctime = ctime or convert(
                    getattr(tf, "attribute_change_time", None), "ctime", "rock_ridge"
                )
        modified = modified or convert(
            getattr(record, "date", None), "modified", "directory_record"
        )
        return modified, accessed, created, ctime, invalid

    def _px(self, rr: RockRidge | None) -> tuple[int | None, int | None, int | None]:
        """The full POSIX mode (file-type bits included), uid and gid of a PX record.

        Only Rock Ridge carries them; Joliet and plain ISO 9660 yield ``None`` for all.
        """
        if rr is None:
            return None, None, None
        for entries in _rr_entry_groups(rr):
            px = getattr(entries, "px_record", None)
            if px is not None:
                mode = getattr(px, "posix_file_mode", None)
                # Only the mode is type-checked: ``_make_member`` calls
                # ``stat.S_IMODE`` on it, while uid and gid pass through untouched.
                return (
                    mode if isinstance(mode, int) else None,
                    getattr(px, "posix_user_id", None),
                    getattr(px, "posix_group_id", None),
                )
        return None, None, None

    def _symlink_target(
        self, member_type: MemberType, record: DirectoryRecord, rr: RockRidge | None
    ) -> str | None:
        if member_type != MemberType.SYMLINK or rr is None:
            return None
        try:
            target = rr.symlink_path()
        except _PYCDLIB_ERRORS:
            return None
        return self._decode_link_target(record, bytes(target)) if target else None

    def _decode_link_target(self, record: DirectoryRecord, target: bytes) -> str:
        """Decode a Rock Ridge link target so that it names what the member names name.

        A target that is not UTF-8 and that ``encoding=`` does not decode is followed
        through the image from the symlink's directory, one component at a time. A
        component that names a record here decodes the way that record's own name does,
        Joliet fallback included, so a link to ``caf\\xe9.txt`` reads ``café.txt`` when
        the file lists as ``café.txt``. An absolute target points outside the image and a
        component that names nothing here has no record to ask, so both are escaped.
        """
        decoded = self._decode_known(target)
        if decoded is not None:
            return decoded
        if target.startswith(b"/") or not self._iso.has_joliet():
            return target.decode("utf-8", errors="surrogateescape")
        directory: DirectoryRecord | None = record.parent
        parts: list[str] = []
        for component in target.split(b"/"):
            here = self._rock_ridge_child(directory, component)
            # ``_joliet_name`` registers ``here`` for the member's diagnostic, as the
            # walk's own ``_record_name`` does with the same record and bytes, so which
            # of the two runs first changes nothing.
            parts.append(self._decode_bytes_name(component, rr_record=here))
            directory = here if here is not None and here.is_dir() else None
        return "/".join(parts)

    def _rock_ridge_child(
        self, directory: DirectoryRecord | None, component: bytes
    ) -> DirectoryRecord | None:
        """The record ``component`` names from ``directory`` in the Rock Ridge tree."""
        if directory is None or component in (b"", b"."):
            return directory
        if component == b"..":
            return directory.parent
        extent = directory.extent_location()
        children = self._rock_ridge_children.get(extent)
        if children is None:
            children = {}
            for child in _yield_children(directory, True):
                # The name the member lists under; the first of several records
                # sharing it, as a scan finds.
                nm = _nm_name(child)
                children.setdefault(
                    nm if nm is not None else _iso_ident_name(child), child
                )
            self._rock_ridge_children[extent] = children
        return children.get(component)

    # --- data ---------------------------------------------------------------------------

    def _open_record(self, record: DirectoryRecord) -> PyCdlibIO:
        """Open a file's data from its directory record, with no path lookup.

        The same checks ``PyCdlib.open_file_from_iso`` makes once it has the record.
        """
        assert _pycdlib_exc is not None and _pycdlib_io is not None
        if not record.is_file():
            raise _pycdlib_exc.PyCdlibInvalidInput("Path to open must be a file")
        return _pycdlib_io.PyCdlibIO(
            self._data_inode(record), self._iso.logical_block_size
        )

    def _file_size(self, record: DirectoryRecord) -> int | None:
        """A file's size as its records declare it; ``None`` if that is lost.

        Records that pycdlib's inode describes as written are the common case and cost
        nothing. The rest get a layout, kept for ``_data_inode``.
        """
        if self._as_written(record):
            return record.data_length
        layout = self._layout(record)
        self._layouts[id(record)] = layout
        return None if layout is None else sum(extent.length for extent in layout)

    def _as_written(self, record: DirectoryRecord) -> bool:
        """Whether pycdlib's inode for ``record`` is the file's whole data, unchanged.

        Not so for a record with no inode (the El Torito boot catalog), a record
        pycdlib linked to another with the same identifier (possibly a multi-extent
        file), or one whose data ends exactly at the end of the image. pycdlib clamps a
        file running past the end of the image to end there, and overwrites the
        declared length with the clamped one: zero when the extent starts at the end,
        negative when it starts past it.
        """
        return (
            record.inode is not None
            and record.data_continuation is None
            and not self._reaches_image_end(record)
        )

    def _reaches_image_end(self, record: DirectoryRecord) -> bool:
        # Zero included: a file whose extent starts exactly at the cut is clamped to 0.
        start = record.extent_location() * self._iso.logical_block_size
        return start + record.data_length == self._image_length

    def _layout(self, record: DirectoryRecord) -> tuple[_Extent, ...] | None:
        """A file's extents with their lengths as declared on disc.

        A file of 4 GiB or more is stored as several records with one name, each
        flagged multi-extent but the last. ``_yield_children`` yields only the first,
        and pycdlib links the rest to it. pycdlib links two unrelated files that share
        an identifier the same way, and by then has set the flag on the first of them
        in memory, so the chain is kept only when every record but the last carries
        the flag in the image itself. Otherwise the file is its own record alone, as
        it was before multi-extent files were read.

        A record whose data ends at the end of the image takes its length from the
        directory's records on disc. One not found there keeps a length of 0 if it has
        one (an empty file whose extent sits at the end of the image), and the layout
        is ``None`` otherwise.
        """
        chain = _continuation_chain(record)
        parent = record.parent
        assert parent is not None, "a listed file record has a parent directory"
        if len(chain) > 1:
            flagged = self._raw_directory(parent).flagged
            if not all(chunk.extent_location() in flagged for chunk in chain[:-1]):
                chain = [record]
        layout: list[_Extent] = []
        for chunk in chain:
            length = chunk.data_length
            if chunk.inode is not None and self._reaches_image_end(chunk):
                declared = self._raw_directory(parent).lengths_to_end.get(
                    (chunk.extent_location(), chunk.file_ident)
                )
                if declared is not None:
                    length = declared
                elif length:
                    return None
                # Otherwise an empty file whose extent sits at the image end: only
                # non-zero lengths are recorded, and 0 is what it declares. A length
                # pycdlib clamped to 0 never lands here: pycdlib clamps on ``>`` and
                # ``_parse_raw_directory`` keeps ``>=``.
            layout.append(_Extent(chunk.extent_location(), length))
        return tuple(layout)

    def _raw_directory(self, directory: DirectoryRecord) -> _RawDirectory:
        """What ``directory``'s records say on disc.

        Reads the directory's extent again. pycdlib read the same bytes, the same
        length, inside ``open_fp``; it keeps no copy of the flags and lengths it then
        changed.
        """
        key = directory.extent_location()
        raw = self._raw_directories.get(key)
        if raw is None:
            block_size = self._iso.logical_block_size
            with self._handle_guard():
                self._iso_fp.seek(key * block_size)
                data = self._iso_fp.read(directory.get_data_length())
            raw = _parse_raw_directory(data, block_size, self._image_length)
            self._raw_directories[key] = raw
        return raw

    def _data_inode(self, record: DirectoryRecord) -> Inode:
        """The inode to read a file's data through, spanning every extent it has.

        pycdlib's own inode is used when it describes the file as written. The layout
        worked out while listing is used otherwise, never worked out again here:
        reading the directory takes the handle guard, which the caller may already
        hold. Three cases need an inode built from it: a file of 4 GiB or more, whose
        inode covers only its first extent; the El Torito boot catalog, which pycdlib
        keeps in memory and gives no inode at all, though its extent still holds its
        bytes on disc, which is what a mounted image shows; and a file pycdlib
        clamped at the end of the image.

        A multi-extent file whose extents are not back to back is refused rather than
        read as one run: every writer seen (xorriso, libarchive's fixture) lays them
        out contiguously, and reading a gap would need a chained stream over
        per-extent inodes that no image has needed yet. An inode built here stops at
        the end of the image; ``_open_member`` makes a read that stops there short a
        ``TruncatedError``.
        """
        assert _pycdlib_inode is not None
        if id(record) not in self._layouts or self._layouts[id(record)] is None:
            # Described as written, or the declared length is lost and pycdlib's
            # clamped inode is what there is to read.
            assert id(record) in self._layouts or self._as_written(record), (
                "the layout is worked out while listing"
            )
            assert record.inode is not None
            return record.inode
        layout = self._layouts[id(record)]
        assert layout is not None
        block_size = self._iso.logical_block_size
        start = layout[0].sector
        expected = start
        for index, extent in enumerate(layout):
            last = index == len(layout) - 1
            if extent.sector != expected or (not last and extent.length % block_size):
                raise UnsupportedFeatureError(
                    "ISO file stored in extents that are not contiguous; reading "
                    "it is not supported",
                    source_format=self._format,
                    archive_name=self._archive_name,
                )
            expected += extent.length // block_size
        length = sum(extent.length for extent in layout)
        available = max(0, self._image_length - start * block_size)
        inode = _pycdlib_inode.Inode()
        inode.parse(start, min(length, available), self._iso_fp, block_size)
        return inode

    def _runs_past_image_end(self, record: DirectoryRecord) -> bool:
        """Whether a file's declared data runs past the end of the image."""
        layout = self._layouts.get(id(record))
        if not layout:
            return False
        start = layout[0].sector * self._iso.logical_block_size
        return start + sum(extent.length for extent in layout) > self._image_length

    def _open_member(self, member: ArchiveMember) -> ArchiveStream:
        record = member._raw
        assert _is_directory_record(record), (
            "ISO member is missing its directory record"
        )
        # A file whose declared data runs past the end of the image reads what is
        # there, then fails at the cut: the length check is enabled for it alone.
        # Every other file keeps the bare ``size``, which enables no check.
        cut = self._runs_past_image_end(record)
        zisofs = self._zisofs_entry(record)
        if zisofs is not None:
            refusal = _zisofs_refusal(zisofs)
            if refusal is not None:
                raise UnsupportedFeatureError(
                    f"ISO member {quoted(member.name)} is stored with {refusal}; "
                    "reading it is not supported",
                    source_format=self._format,
                    archive_name=self._archive_name,
                )
            # A zisofs file cut by the end of the image fails at the block that is
            # cut, as a TruncatedError from the decoder, not by a length check.
            cut = False
        # Boundary outside the lock; _PyCdlibStream construction stays inside it so any
        # enter-time pycdlib seek/error is covered by both.
        with self._translated_errors(member.name):
            if self._handle_lock is not None:
                with self._handle_lock:
                    # Construct under the lock so enter-time pycdlib seek (and the
                    # zisofs header read) is covered.
                    stream: BinaryIO = LockedStream(
                        self._data_stream(record, zisofs, member), self._handle_lock
                    )
            else:
                stream = self._data_stream(record, zisofs, member)
        return self._wrap_member_stream(
            stream,
            member.name,
            size=member.size,
            expected_size=member.size if cut else None,
            verify_member=member if cut else None,
        )

    def _data_stream(
        self,
        record: DirectoryRecord,
        zisofs: _ZisofsEntry | None,
        member: ArchiveMember,
    ) -> BinaryIO:
        # _PyCdlibStream enters the PyCdlibIO context in its __init__.
        stream: BinaryIO = _PyCdlibStream(self._open_record(record))
        if zisofs is None:
            return stream
        try:
            return cast(
                BinaryIO,
                _ZisofsStream(stream, zisofs, member.compressed_size),
            )
        except BaseException:
            stream.close()
            raise

    def _get_archive_info(self) -> ArchiveInfo:
        cost = CostReceipt(
            listing_cost=ListingCost.INDEXED,  # directory tree lives in the header region
            access_cost=AccessCost.DIRECT,  # each extent is independently addressable
            stream_capability=StreamCapability.SEEKABLE,
            solid_block_count=None,
        )
        pvd = self._iso.pvd
        volume_id = pvd.volume_identifier.decode("ascii", errors="replace").rstrip()
        info_extra = ArchiveInfoExtra({"iso.namespace": self._namespace})
        return ArchiveInfo(
            format=self._format,
            # ISO 9660 stores no interchange level. pycdlib infers one from the
            # names it walks and reports 3 for nearly every image, so it is not
            # passed on as if it were read from the image.
            format_version=None,
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
    USES_ENCODING = True  # for Rock Ridge and plain names that are not valid UTF-8
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
