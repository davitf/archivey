"""Native RAR metadata parser (RAR 1.5 / 2.x / 3.x through RAR5).

Parses archive headers into :class:`RarArchive` / :class:`RarMemberInfo` without
decompressing member data and without importing ``rarfile``. Header encryption uses
:mod:`archivey.internal.streams.crypto` (never ``cryptography`` directly).

On-disk layout this module walks::

    [ optional SFX stub ][ magic ]
        RAR 1.5–3.x:  b"Rar!\\x1a\\x07\\x00"      → RarArchive.version == 4
        RAR5:         b"Rar!\\x1a\\x07\\x01\\x00"  → RarArchive.version == 5
    [ block sequence … until ENDARC ]
        RAR3 family: MARK / MAIN / FILE / … / ENDARC
          each: CRC16 | type | flags | header_size | [body] | [packed add_size]
        RAR5: vint-framed MAIN / FILE / SERVICE / ENCRYPTION / ENDARC
          optional encryption block before encrypted headers
          optional MAIN locator extra → QO SERVICE (FILE header copies after the members)
    FILE rows point at packed bytes after the header (``data_offset``).
    When a stored unencrypted QO is reachable from the locator, listing parses
    those copies, seeks back to after MAIN, and walks. A FILE whose offset is
    in QO is emitted from the copy and skipped (consecutive cached spans chain
    in memory, then one seek). ``CMT`` after MAIN is a normal SERVICE on that
    walk. FILE after QO is kept.

``RarArchive.version`` uses **4 for the whole RAR3-on-disk family** (1.5/2.x/3.x) and
**5 for RAR5** — not "RAR 4.x product version". Multi-volume sets are merged by
:func:`parse_rar_volumes` (split sizes/CRC; offsets rebased for
:class:`~archivey.internal.volumes.ConcatenatedFile`). Member **payload** decode is
``unrar`` via :mod:`.rar_unrar` / :mod:`.rar_reader`.

RAR3 SHA-1 / string-to-key and Unicode filename decompression are adapted from
``rarfile`` 4.3 (https://github.com/markokr/rarfile), Copyright (c) 2005-2024
Marko Kreen, used under the ISC License (see the notice at the bottom of this file).
"""

from __future__ import annotations

import functools
import hashlib
import hmac
import io
import struct
import zlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import pbkdf2_hmac
from typing import BinaryIO, Protocol

from archivey.config import ListingLimits
from archivey.escaping import quoted
from archivey.exceptions import (
    CorruptionError,
    EncryptionError,
    PackageNotInstalledError,
    ResourceLimitError,
    TruncatedError,
    UnsupportedFeatureError,
    raw_message_of,
)
from archivey.internal.config import KeyDerivationBudget
from archivey.internal.sfx import SFX_MAX, describe_scan_miss, scan_for_magic
from archivey.internal.streams.crypto import AesParams, open_aes_decrypt_stage
from archivey.internal.streams.streamtools import read_exact
from archivey.internal.timestamps import filetime_to_datetime


class _Readable(Protocol):
    """Header-walk surface: sequential ``read`` plus a ciphertext ``tell``.

    Both the archive ``BinaryIO`` and :class:`_HeaderDecryptStream` provide this.
    It is not ``BinaryIO``: the decrypt stream is not an ``IOBase`` (no
    ``readinto`` / ``close`` / ``writable``), and the walk never seeks it —
    packed-data skips go through the underlying ``source`` so AES-CBC state is
    not asked to reposition. streamtools bases either close the inner stream
    or have no ciphertext ``tell``.
    """

    def read(self, n: int = -1, /) -> bytes: ...
    def tell(self) -> int: ...


# ---------------------------------------------------------------------------
# Magics / limits
# ---------------------------------------------------------------------------

RAR_ID = b"Rar!\x1a\x07\x00"
RAR5_ID = b"Rar!\x1a\x07\x01\x00"
_RAR_MAX_PASSWORD = 127
_RAR_MAX_KDF_SHIFT = 24
# RAR3's fixed key-derivation cost: 16 x 0x4000 SHA-1 rounds in ``_rar3_s2k``.
_RAR3_KDF_ROUNDS = 16 * 0x4000
_RAR5_MAX_HEADER = 2 * 1024 * 1024
# Most FILE extras are a handful of records (encryption, hash, time, version,
# redir, owner). Each skipped record is one retained tuple plus one diagnostic,
# and ``max_members`` cannot see that — it is one member. After this many the
# extra area is junk and the walk stops. Structural, not a ListingLimits
# field: listing limits stay out of this parser, and a caller cannot usefully
# raise a "more skipped extras" budget.
_MAX_SKIPPED_HEADER_RECORDS = 16
# SERVICE headers (``CMT``, ``QO``) are not members, so ``max_members`` never
# counts them and a damaged one is retained for the reader to report. Both halves
# of that are attacker-controlled: a 3.4 MB archive of nothing but damaged SERVICE
# headers retained 80 MB before this cap. Counting them as members instead would
# refuse an archive ``unrar`` lists, which is the opposite of this walk's posture,
# so they get their own cap and the overflow is reported as a count. Structural
# for the same reason as the cap above.
_MAX_DAMAGED_SERVICE_HEADERS = 16
# BytesIO/file seek offsets must fit in a C ssize_t; hostile RAR5 vints can exceed that.
_MAX_SEEK = (1 << 63) - 1
# Same default as ListingLimits.max_members. None is the explicit UNLIMITED opt-out.
_DEFAULT_MAX_MEMBERS = ListingLimits().max_members

# RAR3 block types
_RAR3_MARK = 0x72
_RAR3_MAIN = 0x73
_RAR3_FILE = 0x74
_RAR3_OLD_COMMENT = 0x75
_RAR3_SUB = 0x7A
_RAR3_ENDARC = 0x7B

# RAR3 MAIN flags
_RAR3_MAIN_VOLUME = 0x0001
_RAR3_MAIN_COMMENT = 0x0002
_RAR3_MAIN_SOLID = 0x0008
_RAR3_MAIN_PASSWORD = 0x0080
_RAR3_MAIN_ENCRYPTVER = 0x0200

# RAR3 FILE flags
_RAR3_FILE_SPLIT_BEFORE = 0x0001
_RAR3_FILE_SPLIT_AFTER = 0x0002
_RAR3_FILE_PASSWORD = 0x0004
_RAR3_FILE_COMMENT = 0x0008
_RAR3_FILE_SOLID = 0x0010
_RAR3_FILE_DIRECTORY = 0x00E0
_RAR3_FILE_LARGE = 0x0100
_RAR3_FILE_UNICODE = 0x0200
_RAR3_FILE_SALT = 0x0400
_RAR3_FILE_VERSION = 0x0800
_RAR3_FILE_EXTTIME = 0x1000
_RAR3_LONG_BLOCK = 0x8000

_RAR3_OS_UNIX = 3
_RAR3_M0 = 0x30

# RAR5 block types / flags
_RAR5_MAIN = 1
_RAR5_FILE = 2
_RAR5_SERVICE = 3
_RAR5_ENCRYPTION = 4
_RAR5_ENDARC = 5

_RAR5_FLAG_EXTRA = 0x01
_RAR5_FLAG_DATA = 0x02
_RAR5_FLAG_SPLIT_BEFORE = 0x08
_RAR5_FLAG_SPLIT_AFTER = 0x10

_RAR5_MAIN_ISVOL = 0x01
_RAR5_MAIN_HAS_VOLNR = 0x02
_RAR5_MAIN_SOLID = 0x04

_RAR5_FILE_ISDIR = 0x01
_RAR5_FILE_HAS_MTIME = 0x02
_RAR5_FILE_HAS_CRC32 = 0x04

_RAR5_COMPR_SOLID = 0x40

_RAR5_ENC_HAS_CHECKVAL = 0x01
_RAR5_XENC_CHECKVAL = 0x01
_RAR5_XENC_TWEAKED = 0x02
_RAR5_XENC_AES256 = 0

_RAR5_XFILE_ENCRYPTION = 1
_RAR5_XFILE_HASH = 2
_RAR5_XFILE_TIME = 3
_RAR5_XFILE_VERSION = 4
_RAR5_XFILE_REDIR = 5
_RAR5_XFILE_OWNER = 6

# Names for the FHEXTRA record types, for the diagnostic raised when one of them
# is malformed and dropped. The numeric type travels with it, so a record this
# map does not name is still identifiable.
_RAR5_XNAMES: dict[int, str] = {
    _RAR5_XFILE_ENCRYPTION: "encryption",
    _RAR5_XFILE_HASH: "hash",
    _RAR5_XFILE_TIME: "time",
    _RAR5_XFILE_VERSION: "version",
    _RAR5_XFILE_REDIR: "redir",
    _RAR5_XFILE_OWNER: "owner",
}

_RAR5_MHEXTRA_LOCATOR = 1
_RAR5_MHEXTRA_LOCATOR_QLIST = 0x01
_RAR5_QO_NAME = "QO"
_RAR5_CMT_NAME = "CMT"
# Stored QO is copies of FILE headers (~50–200 B each; measured 55 B/member on
# `rar a -m0 -qo+`, RAR 7.00). 16 MiB covers roughly 300k members — below the
# 1 048 576 parser ceiling, so a very large archive silently falls back to the
# FILE walk (same listing, more seeks). The cap is a read-into-memory bound so a
# hostile packed-size cannot force a 2 GiB read.
_RAR5_QO_PAYLOAD_MAX = 16 * 1024 * 1024

_RAR5_XTIME_UNIXTIME = 0x01
_RAR5_XTIME_HAS_MTIME = 0x02
_RAR5_XTIME_HAS_CTIME = 0x04
_RAR5_XTIME_HAS_ATIME = 0x08
_RAR5_XTIME_UNIXTIME_NS = 0x10

_RAR5_XHASH_BLAKE2SP = 0

_RAR5_XREDIR_UNIX_SYMLINK = 1
_RAR5_XREDIR_WINDOWS_SYMLINK = 2
_RAR5_XREDIR_WINDOWS_JUNCTION = 3
_RAR5_XREDIR_HARD_LINK = 4
_RAR5_XREDIR_FILE_COPY = 5

_RAR5_ENDARC_NEXT_VOLUME = 0x01

_RAR3_ENDARC_NEXT_VOLUME = 0x0001

_RAR5_OS_WINDOWS = 0
_RAR5_OS_UNIX = 1

_S_BLK_HDR = struct.Struct("<HBHH")
_S_FILE_HDR = struct.Struct("<LLBLLBBHL")
_S_COMMENT_HDR = struct.Struct("<HBBH")
_S_LONG = struct.Struct("<L")
_S_SHORT = struct.Struct("<H")

_TRY_ENCODINGS = ("utf8", "utf-16le", "windows-1252")


def rar3_main_crc_end(flags: int) -> int:
    """Exclusive end of MAIN-header CRC coverage, from the start of the block.

    Shared with the SFX hit validator so a flag-walk change cannot silently
    desync detection from the parser.
    """
    pos = _S_BLK_HDR.size
    if flags & _RAR3_LONG_BLOCK:
        pos += 4
    pos += 6
    if flags & _RAR3_MAIN_ENCRYPTVER:
        pos += 1
    return pos


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RarEncryptionInfo:
    algo: int
    flags: int
    kdf_count: int
    salt: bytes
    iv: bytes | None  # file-level has IV; header enc block may not
    check_value: bytes | None


@dataclass(slots=True)
class _Rar3Comment:
    """Compressed old-style comment awaiting ``unrar`` in the reader layer."""

    packed: bytes
    unpacked_size: int
    extract_version: int
    compress_type: int
    flags: int
    crc16: int


@dataclass(slots=True)
class RarMemberInfo:
    filename: str
    orig_filename: bytes | None
    file_size: int
    compress_size: int
    compress_type: int | None  # 0x30..0x35
    crc32: int | None
    blake2sp_hash: bytes | None
    mtime: datetime | None  # RAR4 naive; RAR5 aware UTC
    ctime: datetime | None  # same tz; Unix RARLAB writer stores st_ctime, not birth
    atime: datetime | None  # same tz; RAR5 0x03 HAS_ATIME / RAR3 EXTTIME after ctime
    mode: int | None
    host_os: int | None
    flags: int
    file_redir: tuple[int, int, str] | None  # type, flags, target
    file_encryption: RarEncryptionInfo | None
    header_offset: int
    header_size: int
    data_offset: int
    extract_version: int | None
    file_solid: bool
    is_directory: bool
    is_symlink: bool  # RAR4 unix mode or RAR5 redir symlink types
    is_hardlink_or_copy: bool  # RAR5 HARD_LINK or FILE_COPY
    is_encrypted: bool
    volume_index: int
    split_before: bool
    split_after: bool
    comment: str | _Rar3Comment | None = None
    spanned_volumes: bool = False
    # WinRAR ``-ver`` history: RAR5 FHEXTRA_VERSION vint, or RAR3 ``FILE_VERSION``
    # (``;n`` stripped from ``filename``). ``None`` / ``0`` = live revision.
    file_version: int | None = None
    # RAR5 FHEXTRA records that were malformed and dropped, as
    # ``(record_name, record_type, reason)``. Empty for every well-formed archive,
    # and the shared empty tuple keeps that case at one slot rather than an object:
    # the listing bound is expressed in members, so per-member retained bytes are
    # load-bearing. Capped at ``_MAX_SKIPPED_HEADER_RECORDS`` so a crafted extra
    # area cannot retain one tuple per attacker byte. The reader turns each entry
    # into a ``MEMBER_HEADER_RECORD_SKIPPED`` diagnostic.
    skipped_header_records: tuple[tuple[str, int | None, str], ...] = ()
    # Why the extra-area walk gave up with area still unread, in the words the
    # diagnostic uses, or ``None`` when the header was read to the end. The list
    # above is then what was read rather than all there was. There is no count of
    # the rest: counting it would mean walking it, which is the cost the cap avoids.
    header_walk_stop_reason: str | None = None

    @property
    def skipped_header_records_truncated(self) -> bool:
        """True when the extra-area walk gave up with area still unread."""
        return self.header_walk_stop_reason is not None

    @property
    def encryption_unknown(self) -> bool:
        """True when the header stopped before encryption could be ruled out.

        The extra-area walk gave up with area still unread, and no encryption
        record had been seen — so an unread record may be the one that says this
        member is ciphertext. Distinct from :attr:`is_encrypted`, which is what
        the header actually said, because the two are acted on differently: a
        member that is *known* encrypted is presented with its parameters and
        needs a password, while one that is merely unknown is presented as
        encrypted (a wrong answer here is worse than a missing one) but asserts
        nothing about the archive it sits in.
        """
        return self.skipped_header_records_truncated and not self.is_encrypted

    def needs_password(self) -> bool:
        return self.is_encrypted

    def is_payload_file(self) -> bool:
        """True if ``unrar p`` emits this member's bytes (regular file, not dir/link/redir)."""
        return not (self.is_directory or self.is_symlink or self.is_hardlink_or_copy)

    def is_file_version_history(self) -> bool:
        """True for a prior ``-ver`` revision (presented as ``path;n``)."""
        return self.file_version is not None and self.file_version != 0


@dataclass(slots=True, frozen=True)
class DamagedServiceHeader:
    """A SERVICE header whose extra-area walk dropped a record or gave up.

    Not a :class:`RarMemberInfo`: a service header is not a member, nothing lists
    it, and keeping the whole parse of one both says otherwise and retains far more
    than the reader reads. These three fields are what the reader reports.
    """

    #: ``CMT``, ``QO`` — the header's own name, not a member name.
    name: str
    skipped_header_records: tuple[tuple[str, int | None, str], ...]
    header_walk_stop_reason: str | None


@dataclass(slots=True)
class RarArchive:
    version: int  # 4 = RAR3-on-disk family (1.5/2/3); 5 = RAR5 (not "RAR 4.x")
    is_solid: bool
    has_header_encryption: bool
    comment: str | _Rar3Comment | None
    members: list[RarMemberInfo]
    sfx_offset: int
    is_volume: bool
    needs_next_volume: bool = False
    #: SERVICE headers (``CMT``, ``QO``) whose extra-area walk dropped a record or
    #: gave up, in file order, at most ``_MAX_DAMAGED_SERVICE_HEADERS`` of them.
    #: They are not members, so nothing lists them and the reader's per-member
    #: diagnostics never see them — yet the same leniency applies to their headers,
    #: and the argument that leniency is not silent rests on a diagnostic being
    #: emitted. The reader emits from this at open.
    damaged_service_headers: list[DamagedServiceHeader] = field(default_factory=list)
    #: How many damaged SERVICE headers the cap above kept out of that list. The
    #: reader reports the count, so hitting the cap is itself never silent.
    damaged_service_headers_omitted: int = 0


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def parse_rar_archive(
    source: BinaryIO,
    *,
    password: str | bytes | None = None,
    use_qo: bool = True,
    max_members: int | None = _DEFAULT_MAX_MEMBERS,
    kdf_cache: RarKdfCache | None = None,
) -> RarArchive:
    """Parse from current position (archive start). Source must be seekable.

    ``use_qo=False`` forces the FILE-header walk even when MAIN's locator
    points at a usable QO. Tests compare the two listings; production always
    leaves the default.

    ``max_members`` is ``ListingLimits.max_members`` from the reader config
    (``None`` = ``ListingLimits.UNLIMITED``). Omitting it uses the same default
    as ``ListingLimits()``; pass ``None`` to lift the bound. RAR has no
    header-size analogue, so ``None`` can walk until memory is exhausted.

    ``kdf_cache`` holds the header-key derivations. ``RarReader`` passes its own,
    so a member's PswCheck reuses the header's; without one the parse makes a
    fresh cache.
    """
    return _parse_rar_volume(
        source,
        password=password,
        kdf_cache=kdf_cache if kdf_cache is not None else RarKdfCache(),
        volume_index=0,
        allow_continuation=False,
        use_qo=use_qo,
        max_members=max_members,
    )


def parse_rar_volumes(
    volumes: Sequence[BinaryIO],
    *,
    password: str | bytes | None = None,
    use_qo: bool = True,
    max_members: int | None = _DEFAULT_MAX_MEMBERS,
    kdf_cache: RarKdfCache | None = None,
) -> RarArchive:
    """Parse an ordered multi-volume RAR set, merging split members across volumes.

    Each volume is an independent seekable stream positioned at its start. Member
    ``header_offset`` / ``data_offset`` values are adjusted to a concatenated byte
    space (volume 0 at 0, volume 1 after volume 0's size, …) so a
    :class:`~archivey.internal.volumes.ConcatenatedFile` can serve stored reads.

    ``max_members`` is the same listing budget as :func:`parse_rar_archive`.
    Each volume is capped independently, and the merged table is capped again
    so two volumes that are each under the budget cannot together exceed it.

    Every volume shares one ``kdf_cache`` (the caller's, or a fresh one): each
    volume of a header-encrypted set carries its own encryption record, normally
    with the same salt, and would otherwise derive the same keys again.
    """
    if not volumes:
        raise ValueError("at least one RAR volume is required")
    if kdf_cache is None:
        kdf_cache = RarKdfCache()

    merged: RarArchive | None = None
    base_offset = 0
    for index, volume in enumerate(volumes):
        part = _parse_rar_volume(
            volume,
            password=password,
            kdf_cache=kdf_cache,
            volume_index=index,
            allow_continuation=index > 0,
            use_qo=use_qo,
            max_members=max_members,
        )
        # Reject sets that do not start at volume 1.
        if index == 0 and (
            (part.members and part.members[0].split_before)
            or any(m.split_before and m.volume_index == 0 for m in part.members)
        ):
            raise UnsupportedFeatureError(
                "Need first volume of multi-volume RAR archive"
            )

        for member in part.members:
            member.header_offset += base_offset
            member.data_offset += base_offset

        if merged is None:
            merged = part
        else:
            if part.version != merged.version:
                raise CorruptionError(
                    f"RAR volume version mismatch: {merged.version} vs {part.version}"
                )
            merged.is_solid = merged.is_solid or part.is_solid
            merged.has_header_encryption = (
                merged.has_header_encryption or part.has_header_encryption
            )
            if part.comment and not merged.comment:
                merged.comment = part.comment
            merged.is_volume = True
            # Damaged SERVICE headers are per volume and the merge is field by
            # field, so leaving this out made a damaged header past volume 1
            # silent — the archive opened clean and nothing said a header could
            # not be finished. The cap applies to the merged list for the same
            # reason it applies to one volume's.
            for damaged in part.damaged_service_headers:
                if not _append_damaged_service_header(
                    merged.damaged_service_headers, damaged
                ):
                    merged.damaged_service_headers_omitted += 1
            merged.damaged_service_headers_omitted += (
                part.damaged_service_headers_omitted
            )
            for member in part.members:
                if member.split_before and merged.members:
                    _merge_split_member(merged.members[-1], member)
                else:
                    _append_member(merged.members, member, max_members=max_members)

        # Size of this volume for absolute offset adjustment.
        pos = volume.tell()
        end = volume.seek(0, 2)
        volume.seek(pos)
        base_offset += end

        if part.needs_next_volume:
            if index + 1 >= len(volumes):
                raise TruncatedError(
                    "Incomplete RAR multi-volume set: end of archive expects another volume"
                )
            continue

        # Archive is complete; ignore trailing unused volume paths if any were listed.
        merged.needs_next_volume = False
        return merged

    assert merged is not None
    if merged.needs_next_volume:
        raise TruncatedError(
            "Incomplete RAR multi-volume set: end of archive expects another volume"
        )
    return merged


def _append_damaged_service_header(
    headers: list[DamagedServiceHeader],
    source: RarMemberInfo | DamagedServiceHeader,
) -> bool:
    """Retain a damaged SERVICE header, or report that the cap turned it away.

    Takes the parsed header from the walk or the already-narrowed record from
    another volume, and narrows it here rather than at the call sites, so nothing
    is built for a header the cap is about to turn away. The caller counts what
    this refuses: nothing is dropped silently, because the reader reports the
    count alongside the headers it does describe.
    """
    if len(headers) >= _MAX_DAMAGED_SERVICE_HEADERS:
        return False
    headers.append(
        source
        if isinstance(source, DamagedServiceHeader)
        else DamagedServiceHeader(
            name=source.filename,
            skipped_header_records=source.skipped_header_records,
            header_walk_stop_reason=source.header_walk_stop_reason,
        )
    )
    return True


def _append_member(
    members: list[RarMemberInfo],
    member: RarMemberInfo,
    *,
    max_members: int | None,
) -> None:
    # ``None`` is ListingLimits.UNLIMITED: no count bound. RAR walks sequentially
    # and has no header-size analogue, so UNLIMITED can allocate until OOM.
    # Refuse the member that would make len == max_members + 1 — same bound as
    # ListingLimitTracker._check_members (count > max_members after increment).
    if max_members is not None and len(members) >= max_members:
        raise ResourceLimitError(
            f"Listing limit reached: max_members={max_members} "
            f"(registered {len(members) + 1} members)"
        )
    members.append(member)


def _parse_rar_volume(
    source: BinaryIO,
    *,
    password: str | bytes | None,
    kdf_cache: RarKdfCache,
    volume_index: int,
    allow_continuation: bool,
    use_qo: bool = True,
    max_members: int | None,
) -> RarArchive:
    """Parse one volume — one seekable source — into a :class:`RarArchive`.

    ``parse_rar_archive`` calls this for a single-file archive (volume index 0).
    ``parse_rar_volumes`` calls it once per volume and merges split members.
    ``volume_index`` is the 0-based position in that set, not a RAR format
    version: RAR3-on-disk is ``archive.version == 4``, RAR5 is ``5``.
    ``allow_continuation`` is False on the first volume so a ``split_before``
    member is refused there ("Need first volume") rather than listed as a
    fragment.
    """
    start = source.tell()
    version, sfx_offset = _find_sfx_header(source, start)
    source.seek(start + sfx_offset)
    if version == 5:
        archive = _parse_rar5(
            source,
            password=password,
            kdf_cache=kdf_cache,
            sfx_offset=sfx_offset,
            volume_index=volume_index,
            use_qo=use_qo,
            max_members=max_members,
        )
    else:
        archive = _parse_rar3(
            source,
            password=password,
            kdf_cache=kdf_cache,
            sfx_offset=sfx_offset,
            volume_index=volume_index,
            max_members=max_members,
        )
    if (
        not allow_continuation
        and archive.members
        and archive.members[0].split_before
        and volume_index == 0
    ):
        raise UnsupportedFeatureError("Need first volume of multi-volume RAR archive")
    return archive


# ---------------------------------------------------------------------------
# SFX detection
# ---------------------------------------------------------------------------


def _find_sfx_header(source: BinaryIO, start: int) -> tuple[int, int]:
    """Return ``(version, offset_from_start)`` for RAR4 (version 4) or RAR5.

    Scanning both ids rather than their shared ``Rar!\x1a\x07`` prefix lets the shared
    scanner resolve the version by which id matched first, so a stub containing the bare
    prefix (without a valid version byte) no longer needs a rescan loop here. A
    candidate that fails :func:`~archivey.internal.rar_detect.validate_rar_main_header`
    is skipped while a later ``VALID`` hit is sought; if none validate, the first
    identified candidate is used so a damaged payload still reaches the parser.
    """
    source.seek(start)
    # Fast path: magic at current position.
    head = read_exact(source, len(RAR5_ID))
    if head.startswith(RAR5_ID):
        return 5, 0
    if head.startswith(RAR_ID):
        return 4, 0

    source.seek(start)
    # Imported here: rar_detect imports this module for the ids / CRC helpers.
    from archivey.internal.rar_detect import validate_rar_main_header

    scan = scan_for_magic(
        source,
        (RAR5_ID, RAR_ID),
        limit=SFX_MAX,
        validator=validate_rar_main_header,
    )
    if scan.hit is not None:
        return (5 if scan.hit.needle == RAR5_ID else 4), scan.hit.candidate_origin

    raise CorruptionError(
        "Not a RAR archive: " + describe_scan_miss(scan, limit=SFX_MAX)
    )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _crc32(data: bytes | memoryview) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def _require_exact(stream: BinaryIO, n: int, what: str) -> bytes:
    if n < 0 or n > _MAX_SEEK:
        raise CorruptionError(f"Invalid {what} length: {n}")
    try:
        data = read_exact(stream, n)
    except OverflowError as exc:
        raise CorruptionError(f"Invalid {what} length: {n}") from exc
    if len(data) != n:
        raise CorruptionError(f"Unexpected EOF while reading {what}")
    return data


def _packed_span_end(data_offset: int, add_size: int) -> int:
    """First byte after a packed-data region, or CorruptionError on a hostile span."""
    if data_offset < 0 or add_size < 0:
        raise CorruptionError(
            f"Invalid RAR packed-data span: offset={data_offset}, size={add_size}"
        )
    if add_size > _MAX_SEEK - data_offset:
        raise CorruptionError(
            f"RAR packed size {add_size} at offset {data_offset} exceeds the seekable range"
        )
    return data_offset + add_size


def _seek_to(source: BinaryIO, pos: int) -> None:
    if pos < 0 or pos > _MAX_SEEK:
        raise CorruptionError(f"Invalid RAR seek offset: {pos}")
    try:
        source.seek(pos)
    except (OverflowError, OSError) as exc:
        raise CorruptionError(f"RAR packed-data seek failed at offset {pos}") from exc


def _seek_after_packed(source: BinaryIO, data_offset: int, add_size: int) -> None:
    """Skip past a packed-data region, translating hostile sizes to CorruptionError."""
    _seek_to(source, _packed_span_end(data_offset, add_size))


def load_vint(buf: bytes | bytearray | memoryview, pos: int) -> tuple[int, int]:
    # Hot path: most RAR5 vints are a single byte (< 0x80). Avoid the multi-byte
    # loop and ``min()`` on every call (listing many-member archives).
    length = len(buf)
    if pos >= length:
        raise CorruptionError("Invalid RAR5 variable-length integer")
    b = buf[pos]
    if b < 0x80:
        return b, pos + 1
    limit = pos + 11
    if limit > length:
        limit = length
    res = b & 0x7F
    ofs = 7
    pos += 1
    while pos < limit:
        b = buf[pos]
        res += (b & 0x7F) << ofs
        pos += 1
        ofs += 7
        if b < 0x80:
            return res, pos
    raise CorruptionError("Invalid RAR5 variable-length integer")


def _load_byte(buf: bytes | bytearray | memoryview, pos: int) -> tuple[int, int]:
    if pos >= len(buf):
        raise CorruptionError("Unexpected EOF while reading byte")
    return buf[pos], pos + 1


def _load_le32(buf: bytes | bytearray | memoryview, pos: int) -> tuple[int, int]:
    end = pos + 4
    if end > len(buf):
        raise CorruptionError("Unexpected EOF while reading le32")
    return _S_LONG.unpack_from(buf, pos)[0], end


def _load_bytes(
    buf: bytes | bytearray | memoryview, num: int, pos: int
) -> tuple[bytes, int]:
    end = pos + num
    if end > len(buf):
        raise CorruptionError("Unexpected EOF while reading bytes")
    return bytes(buf[pos:end]), end


def _load_vstr(buf: bytes | bytearray | memoryview, pos: int) -> tuple[bytes, int]:
    slen, pos = load_vint(buf, pos)
    return _load_bytes(buf, slen, pos)


def _parse_dos_time(stamp: int) -> datetime:
    sec = (stamp & 0x1F) * 2
    stamp >>= 5
    minute = stamp & 0x3F
    stamp >>= 6
    hour = stamp & 0x1F
    stamp >>= 5
    day = stamp & 0x1F
    stamp >>= 5
    month = stamp & 0x0F
    stamp >>= 4
    year = (stamp & 0x7F) + 1980
    try:
        return datetime(year, month, day, hour, minute, sec)
    except ValueError:
        month = max(1, min(month, 12))
        mday = (0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
        day = max(1, min(day, mday[month]))
        hour = min(hour, 23)
        minute = min(minute, 59)
        sec = min(sec, 59)
        return datetime(year, month, day, hour, minute, sec)


def _load_unixtime(
    buf: bytes | bytearray | memoryview, pos: int
) -> tuple[datetime | None, int]:
    secs, pos = _load_le32(buf, pos)
    try:
        return datetime.fromtimestamp(secs, timezone.utc), pos
    except (ValueError, OverflowError, OSError):
        # Hostile / out-of-range timestamps must not abort listing.
        return None, pos


def _load_windowstime(
    buf: bytes | bytearray | memoryview, pos: int
) -> tuple[datetime | None, int]:
    lo, pos = _load_le32(buf, pos)
    hi, pos = _load_le32(buf, pos)
    ticks = (hi << 32) | lo
    # Shared FILETIME helper. ticks=0 → None is that helper's ZIP unset
    # rule, accepted for RAR (do not revive 1601-01-01). Discard
    # TimestampIssue: listing still swallows out-of-range values rather
    # than emitting a diagnostic (same as Unix time).
    dt, _issue = filetime_to_datetime(ticks, "", field="mtime")
    return dt, pos


def _normalize_password_utf8(password: str | bytes) -> bytes:
    """RAR password normalization: UTF-16LE truncate → UTF-8."""
    if isinstance(password, bytes):
        pwd = password.decode("utf8")
    else:
        pwd = password
    wstr = pwd.encode("utf-16le")[: _RAR_MAX_PASSWORD * 2]
    return wstr.decode("utf-16le").encode("utf8")


def _normalize_password_utf16le(password: str | bytes) -> bytes:
    if isinstance(password, bytes):
        pwd = password.decode("utf8")
    else:
        pwd = password
    return pwd.encode("utf-16le")[: _RAR_MAX_PASSWORD * 2]


def _decode_name(raw: bytes) -> str:
    for enc in _TRY_ENCODINGS:
        try:
            return raw.decode(enc)
        except UnicodeError:
            continue
    return raw.decode("windows-1252", "replace")


def _merge_split_member(old: RarMemberInfo, new: RarMemberInfo) -> None:
    """Collapse a SPLIT_AFTER continuation into the first part (rarfile-style).

    A genuine continuation repeats the same file name and follows a part that was itself
    marked SPLIT_AFTER. Reject a continuation that names a different file or follows a
    non-split member, so a crafted split_before flag cannot silently fold an unrelated
    member's size/CRC into the previous one (and hide it from the listing).
    """
    if not old.split_after or old.filename != new.filename:
        raise CorruptionError(
            "Mismatched RAR split continuation: "
            f"{quoted(new.filename)} does not continue {quoted(old.filename)}"
        )
    old.compress_size += new.compress_size
    if new.crc32 is not None:
        old.crc32 = new.crc32
    if new.blake2sp_hash is not None:
        old.blake2sp_hash = new.blake2sp_hash
    old.split_after = new.split_after
    old.spanned_volumes = True


# ---------------------------------------------------------------------------
# Header decrypt stream (AES-CBC via crypto module)
# ---------------------------------------------------------------------------


class _HeaderDecryptStream:
    """Decrypt one encrypted RAR header with AES-CBC.

    Each encrypted header is its own CBC message: RAR3 prefixes an 8-byte salt,
    RAR5 a 16-byte IV, then ciphertext padded to 16-byte blocks. ``read`` returns
    plaintext; leftover bytes in ``_buf`` are the unread tail of the last
    decrypted block. Mid-header, that tail is still-owed plaintext. After the
    walk has consumed ``header_size``, it is AES padding — not bytes this header
    still owes. Either way ``tell`` is the ciphertext cursor (see below).

    ``tell`` is the underlying **ciphertext** cursor, including unread leftover.
    After a full header read, ``data_offset`` needs that position so the next
    salt/IV (or packed data) starts on a block boundary. Subtracting
    ``len(_buf)`` would report the logical plaintext offset and land inside the
    padding; the next header then decrypts as garbage.

    There is no ``seek``. CBC *can* reposition (``AesDecryptStream`` restarts
    from the preceding ciphertext block as IV), but the parser never needs it:
    after the header is consumed this wrapper is discarded and packed-data
    skips go through ``source``. ``tell`` is the ciphertext cursor rather than
    a plaintext offset. Do not prefetch plaintext and rewind — that is why
    :func:`_read_rar5_block` reads the size vint byte-at-a-time.

    Not :class:`~archivey.internal.streams.crypto.AesDecryptStream`. That 7z
    wrapper has ``owns_inner`` (both streams borrow) and a plaintext
    ``tell``/``seek``. What still blocks folding this class in is the header
    walk: ``header_fd`` is either the raw handle or this stream, and
    ``tell()`` means archive offset for both arms; and this wrapper sits
    mid-file unbounded, so a seeking decrypt stream would reposition the
    shared archive handle. The decrypt *stage* is shared; the pull stream is
    not.

    ``read`` rejects unbounded ``n < 0``. It has no per-read size cap: the
    caller already bounds the ask. RAR5 refuses ``hdrlen > _RAR5_MAX_HEADER``
    (2 MiB) before the body ``read_exact``; RAR3 ``header_size`` is a 16-bit
    field (max 65 535). A tighter 8 KiB cap here used to reject a well-formed
    header as ``wrong password?``.
    """

    def __init__(self, source: BinaryIO, key: bytes, iv: bytes) -> None:
        self._source = source
        self._stage = open_aes_decrypt_stage(AesParams(key=key, iv=iv))
        self._buf = bytearray()

    def tell(self) -> int:
        # Ciphertext position, not plaintext-consumed. Leftover ``_buf`` is the
        # unread tail of the last decrypted block (still-owed mid-header; AES
        # padding after ``header_size``). See the class docstring. Measured:
        # every FILE header on ``encrypted_header__.rar`` /
        # ``encrypted_header__rar4.rar`` has ``header_size % 16 != 0``, and
        # ``tell() - len(_buf)`` fails the parse.
        return self._source.tell()

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            raise CorruptionError("Unbounded read on encrypted RAR header stream")
        if n <= len(self._buf):
            out = bytes(self._buf[:n])
            del self._buf[:n]
            return out
        out = bytearray(self._buf)
        self._buf.clear()
        need = n - len(out)
        while need > 0:
            # AES-CBC advances one whole block at a time: a short read must be gathered,
            # not treated as EOF, or the remaining ciphertext decrypts against the wrong
            # IV and every later header looks corrupt.
            enc = read_exact(self._source, 16)
            if len(enc) < 16:
                break
            dec = self._stage.update(enc)
            if need >= len(dec):
                out.extend(dec)
                need -= len(dec)
            else:
                out.extend(dec[:need])
                self._buf.extend(dec[need:])
                need = 0
        return bytes(out)


# ---------------------------------------------------------------------------
# RAR3 SHA-1 / string-to-key (ported from rarfile 4.3)
# ---------------------------------------------------------------------------


class _Rar3Sha1:
    """SHA-1 plus the WinRAR 3.x KDF buffer-mutation bug.

    WinRAR's SHA-1 runs the message schedule in place on its 64-byte block
    buffer, then writes the expanded words back little-endian. ``hashlib.sha1``
    does not mutate its input, so the digest of *this* ``update`` is correct —
    and then, when ``data`` is a ``bytearray`` containing a complete SHA-1 block
    at a block boundary (``dpos`` skips the already-absorbed prefix), this class
    applies the same in-place corruption so the *next* ``update`` of a reused
    seed matches WinRAR.

    RAR3 string-to-key hashes the same ``password + salt`` seed 0x4000×16
    times. Mutation is not "the first time the seed crosses 64 bytes": it
    fires only when this ``update`` contains a complete SHA-1 block at a
    hasher block boundary (``dpos + 64 <= len(data)``). A 65-byte seed never
    satisfies that, even though ``len(data) > 64``; a 128-byte seed does on
    the first call. Seed ≤ 64 bytes (including the 8-byte salt) never enters
    ``_corrupt``. Ported from ``rarfile`` 4.3 ``Rar3Sha1``; there is no
    non-buggy caller.
    """

    _BLK_BE = struct.Struct(b">16L")
    _BLK_LE = struct.Struct(b"<16L")
    block_size = 64

    def __init__(self) -> None:
        self._md = hashlib.sha1()
        self._nbytes = 0

    def update(self, data: bytes | bytearray) -> None:
        self._md.update(data)
        bufpos = self._nbytes & 63
        self._nbytes += len(data)
        if len(data) > 64:
            # Unrar's hash_process memcpy's the first ``64 - bufpos`` bytes
            # into its own ``ctx->buffer`` and transforms *there*, so the
            # caller's prefix is never rewritten. That includes ``bufpos ==
            # 0``: a whole first block is copied internally, not "no prefix".
            # Only later ``hash_transform(state, &data[i])`` calls mutate the
            # caller's buffer in place. ``dpos`` is that first in-place block.
            dpos = self.block_size - bufpos
            while dpos + self.block_size <= len(data):
                self._corrupt(data, dpos)
                dpos += self.block_size

    def digest(self) -> bytes:
        return self._md.digest()

    def _corrupt(self, data: bytes | bytearray, dpos: int) -> None:
        if not isinstance(data, bytearray):
            raise TypeError(
                "_Rar3Sha1 needs a mutable seed: the WinRAR KDF mutates its "
                "block buffer in place, and a bytes seed silently derives a "
                "different key."
            )
        ws = list(self._BLK_BE.unpack_from(data, dpos))
        for t in range(16, 80):
            tmp = (
                ws[(t - 3) & 15]
                ^ ws[(t - 8) & 15]
                ^ ws[(t - 14) & 15]
                ^ ws[(t - 16) & 15]
            )
            ws[t & 15] = ((tmp << 1) | (tmp >> 31)) & 0xFFFFFFFF
        self._BLK_LE.pack_into(data, dpos, *ws)


def _rar3_s2k(password: str | bytes, salt: bytes) -> tuple[bytes, bytes]:
    """Derive AES-128 key + IV for RAR3 header/file encryption.

    Uses :class:`_Rar3Sha1` so a long ``password + salt`` seed is mutated between
    rounds the way WinRAR's SHA-1 mutates its block buffer.
    """
    return _rar3_key_iv(_normalize_password_utf16le(password), salt)


def _rar3_key_iv(wstr: bytes, salt: bytes) -> tuple[bytes, bytes]:
    """:func:`_rar3_s2k` for a password already in UTF-16LE."""
    seed = bytearray(wstr + salt)
    h = _Rar3Sha1()
    iv = bytearray()
    for i in range(16):
        for j in range(0x4000):
            cnt = struct.pack("<L", i * 0x4000 + j)
            h.update(seed)
            h.update(cnt[:3])
            if j == 0:
                iv.append(h.digest()[19])
    key_be = h.digest()[:16]
    key_le = struct.pack("<LLLL", *struct.unpack(">LLLL", key_be))
    return key_le, bytes(iv)


def _rar5_s2k(password: str | bytes, salt: bytes, iterations: int) -> bytes:
    """PBKDF2-HMAC-SHA256 for RAR5 (returns 32-byte AES-256 key material)."""
    return _rar5_pbkdf2(_normalize_password_utf8(password), salt, iterations)


def _rar5_pbkdf2(ustr: bytes, salt: bytes, iterations: int) -> bytes:
    """:func:`_rar5_s2k` for a password already normalized to UTF-8."""
    return pbkdf2_hmac("sha256", ustr, salt, iterations, dklen=32)


class RarKdfCache:
    """RAR key derivations, each behind a per-instance ``functools.cache``.

    RAR5's cost is the archive's choice: ``2**kdf_count`` PBKDF2 rounds, accepted up
    to ``2**24`` (about 3-4 s each). RAR3's is a fixed 2**18-round SHA-1 loop that
    runs in Python. RARLAB writes one salt per archiving run, so every volume of a
    header-encrypted set repeats the same derivation; without a cache each volume
    paid it again.

    The caches are built in ``__init__``, not with ``@functools.cache`` on a method:
    that one cache would live on the class, keyed on ``self``, and keep every reader,
    password and key alive until the process exits. ``RarReader`` holds one instance
    for its lifetime and passes it to every parse and member-side derivation, so the
    keys go when the reader does. :func:`parse_rar_archive` and
    :func:`parse_rar_volumes` run without a reader, which is why the cache is an
    object passed in; they make a fresh one per call when none is.

    The methods only normalize the password before the cache lookup: the reader
    passes ``bytes`` on the header walk and ``str`` on member reads, and both must
    hit one entry. Keyed by the normalized password, the salt and (RAR5) the round
    count, so a wrong candidate never answers for a right one and the three RAR5
    outputs (AES key, HashKey, PswCheck at ``+0``/``+16``/``+32`` rounds) stay
    distinct.

    The caches have no size bound, and need none: an entry is added only on a miss,
    and a miss runs the derivation the caller was about to run anyway, at the
    archive's declared cost. Entries cannot grow faster than the CPU work that
    produces them, so an archive that varies its salts gains no amplification over
    the per-derivation cost it already had. Two threads racing on one entry may both
    derive it, which costs time but never a wrong key. Neither this class nor the
    cache wrappers has a ``repr`` that shows passwords or keys.

    Each miss is charged to ``budget``
    (:attr:`~archivey.config.DecoderLimits.max_key_derivation_rounds`) before it
    runs: RAR5 at its PBKDF2 round count, RAR3 at its fixed ``2**18``. The charge
    sits inside the cached function, which ``functools.cache`` calls only on a miss,
    so a hit costs nothing. Without a budget the cache charges one built from the
    default limits.
    """

    __slots__ = ("_budget", "_rar3", "_rar5")

    def __init__(self, budget: KeyDerivationBudget | None = None) -> None:
        charge = budget if budget is not None else KeyDerivationBudget()

        def rar5(ustr: bytes, salt: bytes, iterations: int) -> bytes:
            charge.spend(iterations, what="RAR5 key derivation")
            return _rar5_pbkdf2(ustr, salt, iterations)

        def rar3(wstr: bytes, salt: bytes) -> tuple[bytes, bytes]:
            charge.spend(_RAR3_KDF_ROUNDS, what="RAR3 key derivation")
            return _rar3_key_iv(wstr, salt)

        self._budget = charge
        self._rar5 = functools.cache(rar5)
        self._rar3 = functools.cache(rar3)

    def rar5(self, password: str | bytes, salt: bytes, iterations: int) -> bytes:
        """:func:`_rar5_s2k`, derived once per distinct input."""
        return self._rar5(_normalize_password_utf8(password), salt, iterations)

    def rar3(self, password: str | bytes, salt: bytes) -> tuple[bytes, bytes]:
        """:func:`_rar3_s2k`, derived once per distinct input."""
        return self._rar3(_normalize_password_utf16le(password), salt)


def rar5_hash_key(
    password: str | bytes,
    salt: bytes,
    kdf_count_shift: int,
    *,
    kdf_cache: RarKdfCache | None = None,
) -> bytes:
    """Derive the RAR5 HashKey used by ``ConvertHashToMAC`` (PBKDF2 at ``(1<<kdf)+16``).

    The AES key is at ``1 << kdf_count``, HashKey at ``+16``, and PswCheck at ``+32``
    (UnRAR ``crypt5.cpp``).
    """
    if kdf_count_shift > _RAR_MAX_KDF_SHIFT:
        raise CorruptionError(f"RAR5 kdf_count too large: {kdf_count_shift}")
    derive = kdf_cache.rar5 if kdf_cache is not None else _rar5_s2k
    return derive(password, salt, (1 << kdf_count_shift) + 16)


def convert_crc_to_mac(crc: int, hash_key: bytes) -> int:
    """RAR5 ``ConvertHashToMAC`` for CRC32: XOR-fold of ``HMAC-SHA256(HashKey, crc_le4)``."""
    digest = hmac.new(
        hash_key, (crc & 0xFFFFFFFF).to_bytes(4, "little"), hashlib.sha256
    ).digest()
    result = 0
    for (word,) in struct.iter_unpack("<I", digest):
        result ^= word
    return result & 0xFFFFFFFF


def convert_blake2sp_to_mac(digest: bytes, hash_key: bytes) -> bytes:
    """RAR5 ``ConvertHashToMAC`` for BLAKE2sp: ``HMAC-SHA256(HashKey, digest32)``."""
    if len(digest) != 32:
        raise ValueError(f"BLAKE2sp digest must be 32 bytes, got {len(digest)}")
    return hmac.new(hash_key, digest, hashlib.sha256).digest()


# ---------------------------------------------------------------------------
# RAR3 Unicode filename decompressor (ported from rarfile)
# ---------------------------------------------------------------------------


def _decode_rar3_unicode_name(std_name: bytes, encdata: bytes) -> str | None:
    """Decode a RAR3 compressed Unicode name, or None if it is corrupt.

    RAR 2.9-4 store a file name as two fields separated by a NUL: an 8-bit name
    in the local/OEM charset, then a compressed UTF-16 name. The compressed form
    is a *delta against the 8-bit name*, which is why both are needed: they are
    position-aligned, character i of the Unicode name against byte i of the
    8-bit one, so ``pos`` indexes the output and ``std_name`` at the same time.

    ``encdata[0]`` is the high UTF-16 byte shared by most of the name. The rest
    is a stream of 2-bit opcodes, four per flags byte, MSB first:

      0  next byte is a low byte, high byte 0        (ASCII / Latin-1)
      1  next byte is a low byte, high byte ``hi``
      2  next two bytes are the low and high bytes   (anything else)
      3  run: the next k characters equal the 8-bit name's, optionally shifted
         by a correction byte. k is 2..129, so one opcode byte can emit 129
         code units.

    Bounded by construction: a t=0/1/2 unit costs an encoding byte and a run
    unit costs a ``std_name`` byte, so the output cannot exceed
    ``len(std_name) + len(encdata)``. (``encdata[0]`` is ``hi`` and any output
    needs a flags byte, so the live length is strictly less; there is no
    runtime cap because it cannot fire.) Overrunning either field means the
    name is corrupt: return None and let the caller fall back to the 8-bit
    field.

    Without that stop, an empty 8-bit field plus RLE-heavy ``encdata`` spent
    ~11 s of CPU at the ``uint16`` ``name_size`` ceiling (~6.7e6 code units,
    13.5 MB, discarded before any member existed). The memory was always
    bounded by ``name_size``; the CPU was not, other than the file size.
    (unrar encname.cpp, EncodeFileName::Decode; ported via rarfile.)
    """
    out = bytearray()
    pos = 0  # output character index AND index into std_name - aligned
    try:
        hi = encdata[0]
        encpos = 1
        flagbits = 0
        flags = 0
        while encpos < len(encdata):
            if flagbits == 0:
                flags = encdata[encpos]
                encpos += 1
                flagbits = 8
            flagbits -= 2
            t = (flags >> flagbits) & 3
            if t == 0:
                out += bytes((encdata[encpos], 0))
                encpos += 1
                pos += 1
            elif t == 1:
                out += bytes((encdata[encpos], hi))
                encpos += 1
                pos += 1
            elif t == 2:
                lo, c_hi = encdata[encpos], encdata[encpos + 1]
                encpos += 2
                out += bytes((lo, c_hi))
                pos += 1
            else:
                n = encdata[encpos]
                encpos += 1
                # ``n & 0x80`` selects the high-byte=hi / correction path even
                # when the correction byte is 0. Folding that into
                # ``if correction:`` would emit high-byte 0 instead of ``hi``.
                if n & 0x80:
                    correction = encdata[encpos]
                    encpos += 1
                    k = (n & 0x7F) + 2
                    run = std_name[pos : pos + k]
                    if len(run) != k:
                        raise IndexError
                    block = bytearray(2 * k)
                    if correction:
                        block[0::2] = bytes((b + correction) & 0xFF for b in run)
                    else:
                        block[0::2] = run
                    block[1::2] = bytes((hi,)) * k
                else:
                    k = n + 2
                    run = std_name[pos : pos + k]
                    if len(run) != k:
                        raise IndexError
                    block = bytearray(2 * k)
                    block[0::2] = run
                out += block
                pos += k
    except IndexError:
        return None
    return out.decode("utf-16le", "replace")


def _fix_rar3_astral_truncation(unicode_name: str, std_name: bytes) -> str:
    """Recover non-BMP (astral) characters truncated by the RAR3 Unicode name compressor.

    *Astral* is the usual Unicode term for a code point above the Basic
    Multilingual Plane — U+10000 and up, stored as a UTF-16 surrogate pair.

    RAR 2.9–4 encode filenames as compressed UTF-16, which cannot represent those
    characters: an emoji like ``😀`` (U+1F600) is stored as a single truncated
    code unit (U+F600, in the Private Use Area) instead of a surrogate pair. The
    legacy 8-bit name field carries the same name — commonly as UTF-8 — so where
    the decompressed name shows a surrogate/PUA code unit exactly ``0x10000``
    below the 8-bit name's character, prefer the 8-bit decoding. (Workaround
    ported from the v1 reader's ``get_non_corrupted_filename``.)
    """
    try:
        utf8_name = std_name.decode("utf-8")
    except UnicodeDecodeError:
        return unicode_name
    if utf8_name == unicode_name:
        return unicode_name
    for u16c, u8c in zip(unicode_name, utf8_name):
        u16 = ord(u16c)
        if (0xD800 <= u16 <= 0xDFFF or 0xE000 <= u16 <= 0xF8FF) and ord(
            u8c
        ) == u16 + 0x10000:
            return utf8_name
    return unicode_name


def _parse_rar3_old_comment_subblocks(
    hdata: bytes, pos: int
) -> str | _Rar3Comment | None:
    """Read a RAR 1.5/2.x COMMENT subblock appended to a MAIN or FILE header.

    The enclosing header's CRC ends before these subblocks, despite its
    ``header_size`` including them. Keep malformed trailing subblocks ignorable:
    older archives were previously listed after skipping this region entirely.
    """
    comment: str | _Rar3Comment | None = None
    while pos + _S_BLK_HDR.size <= len(hdata):
        _, block_type, flags, block_size = _S_BLK_HDR.unpack_from(hdata, pos)
        next_pos = pos + block_size
        pos += _S_BLK_HDR.size
        if block_size < _S_BLK_HDR.size or next_pos > len(hdata):
            break
        if block_type == _RAR3_OLD_COMMENT and pos + _S_COMMENT_HDR.size <= next_pos:
            unpacked_size, extract_version, compress_type, crc16 = (
                _S_COMMENT_HDR.unpack_from(hdata, pos)
            )
            packed = hdata[pos + _S_COMMENT_HDR.size : next_pos]
            if compress_type == _RAR3_M0 and not flags & _RAR3_FILE_PASSWORD:
                if _crc32(packed) & 0xFFFF == crc16:
                    comment = _decode_name(packed)
            else:
                comment = _Rar3Comment(
                    packed=packed,
                    unpacked_size=unpacked_size,
                    extract_version=extract_version,
                    compress_type=compress_type,
                    flags=flags,
                    crc16=crc16,
                )
        pos = next_pos
    return comment


# ---------------------------------------------------------------------------
# RAR3 / RAR4 parser
# ---------------------------------------------------------------------------


def _parse_rar3(
    source: BinaryIO,
    *,
    password: str | bytes | None,
    kdf_cache: RarKdfCache,
    sfx_offset: int,
    volume_index: int = 0,
    max_members: int | None,
) -> RarArchive:
    _require_exact(source, len(RAR_ID), "RAR3 signature")

    is_solid = False
    is_volume = False
    has_header_encryption = False
    comment: str | _Rar3Comment | None = None
    members: list[RarMemberInfo] = []
    needs_next_volume = False

    while True:
        header_fd: _Readable = source
        # RAR3 has no header password verifier: on a wrong password the decrypted block
        # header is garbage that fails the size/CRC checks below, indistinguishable from
        # corruption. When this block is encrypted, surface such failures as
        # EncryptionError so password candidates keep iterating (see _read_rar5_block).
        block_encrypted = has_header_encryption
        if has_header_encryption:
            if password is None:
                raise EncryptionError(
                    "RAR archive has encrypted headers but no password was provided"
                )
            try:
                header_fd = _rar3_decrypt_header(source, password, kdf_cache)
            except (PackageNotInstalledError, ResourceLimitError):
                # A spent key-derivation budget is not a wrong password: re-wrapped as
                # EncryptionError it would send the reader on to the next candidate.
                raise
            except Exception as exc:
                raise EncryptionError(
                    f"Failed to decrypt RAR3 headers: {raw_message_of(exc)}"
                ) from exc

        try:
            header_offset = header_fd.tell()
            buf = read_exact(header_fd, _S_BLK_HDR.size)
            if not buf:
                break
            if len(buf) < _S_BLK_HDR.size:
                raise CorruptionError("Unexpected EOF while reading RAR3 block header")

            header_crc, block_type, flags, header_size = _S_BLK_HDR.unpack_from(buf)
            if header_size < _S_BLK_HDR.size:
                raise CorruptionError(f"Invalid RAR3 header size: {header_size}")
            if header_size > _S_BLK_HDR.size:
                rest = read_exact(header_fd, header_size - _S_BLK_HDR.size)
                if len(rest) != header_size - _S_BLK_HDR.size:
                    raise CorruptionError(
                        "Unexpected EOF while reading RAR3 header body"
                    )
                hdata = buf + rest
            else:
                hdata = buf
        except (CorruptionError, TruncatedError) as exc:
            if block_encrypted:
                raise EncryptionError(
                    "Failed to decrypt RAR3 headers (wrong password?)"
                ) from exc
            raise
        # HeaderDecryptStream.tell() reports the underlying ciphertext position
        # (including AES block padding), which is the correct data_offset.
        data_offset = header_fd.tell()

        pos = _S_BLK_HDR.size
        if flags & _RAR3_LONG_BLOCK:
            add_size, pos = _load_le32(hdata, pos)
        else:
            add_size = 0

        if block_type == _RAR3_MARK:
            _seek_after_packed(source, data_offset, add_size)
            continue

        if block_type == _RAR3_MAIN:
            crc_pos = rar3_main_crc_end(flags)
            is_solid = bool(flags & _RAR3_MAIN_SOLID)
            is_volume = bool(flags & _RAR3_MAIN_VOLUME)
            if flags & _RAR3_MAIN_PASSWORD:
                has_header_encryption = True
                if password is None:
                    raise EncryptionError(
                        "RAR archive has encrypted headers but no password was provided"
                    )
            calc = _crc32(hdata[2:crc_pos]) & 0xFFFF
            if header_crc != calc:
                if block_encrypted:
                    raise EncryptionError(
                        "Failed to decrypt RAR3 headers (wrong password?)"
                    )
                raise CorruptionError(
                    f"RAR3 MAIN header CRC mismatch: expected {header_crc:#x}, got {calc:#x}"
                )
            if flags & _RAR3_MAIN_COMMENT:
                comment = _parse_rar3_old_comment_subblocks(hdata, crc_pos)
            _seek_after_packed(source, data_offset, add_size)
            continue

        if block_type == _RAR3_ENDARC:
            calc = _crc32(hdata[2:header_size]) & 0xFFFF
            if header_crc != calc:
                if block_encrypted:
                    raise EncryptionError(
                        "Failed to decrypt RAR3 headers (wrong password?)"
                    )
                raise CorruptionError("RAR3 ENDARC header CRC mismatch")
            needs_next_volume = bool(flags & _RAR3_ENDARC_NEXT_VOLUME)
            break

        if block_type in (_RAR3_FILE, _RAR3_SUB):
            # FILE header re-reads pack_size as first field when LONG_BLOCK was set.
            file_pos = pos - 4 if (flags & _RAR3_LONG_BLOCK) else pos
            member, crc_pos = _parse_rar3_file_header(
                hdata,
                file_pos,
                flags=flags,
                header_offset=header_offset,
                header_size=header_size,
                data_offset=data_offset,
                volume_index=volume_index,
                is_service=(block_type == _RAR3_SUB),
            )
            calc = _crc32(hdata[2:crc_pos]) & 0xFFFF
            if header_crc != calc:
                if block_encrypted:
                    raise EncryptionError(
                        "Failed to decrypt RAR3 headers (wrong password?)"
                    )
                raise CorruptionError(
                    f"RAR3 FILE header CRC mismatch: expected {header_crc:#x}, got {calc:#x}"
                )

            if block_type == _RAR3_FILE:
                # RAR 1.5 / 2.x use the same block layout as RAR3 for headers we
                # care about; member data is always left to RARLAB ``unrar``.
                # Do not reject extract_version ≤ 20 — that also false-positives
                # modern RAR3 archives whose stored/small members advertise
                # unp_ver=20.
                # File-version history rows (FILE_VERSION) are kept as members.
                if flags & _RAR3_FILE_COMMENT:
                    member.comment = _parse_rar3_old_comment_subblocks(hdata, crc_pos)
                if member.split_before:
                    if members:
                        _merge_split_member(members[-1], member)
                    else:
                        # Continuation without a prior part in this volume.
                        _append_member(members, member, max_members=max_members)
                else:
                    _append_member(members, member, max_members=max_members)
                if member.split_after:
                    needs_next_volume = True
            elif (
                block_type == _RAR3_SUB
                and member.filename == "CMT"
                and member.compress_type == _RAR3_M0
                and not member.is_encrypted
                and not member.split_before
                and not member.split_after
                and member.compress_size > 0
            ):
                source.seek(data_offset)
                raw = _require_exact(source, member.compress_size, "RAR3 comment")
                cmt = _decode_name(raw.split(b"\0", 1)[0])
                if member.file_solid and members:
                    members[-1].comment = cmt
                else:
                    comment = cmt

            # For a >4 GiB packed member the LONG_BLOCK ``add_size`` holds only the low
            # 32 bits; ``member.compress_size`` carries the full 64-bit size (with
            # HIGH_PACK_SIZE) so the walk skips the whole packed region and does not land
            # mid-data on the next header.
            packed_size = (
                member.compress_size if (flags & _RAR3_FILE_LARGE) else add_size
            )
            _seek_after_packed(source, data_offset, packed_size)
            continue

        # Unknown / skippable block
        _seek_after_packed(source, data_offset, add_size)

    return RarArchive(
        version=4,
        is_solid=is_solid,
        has_header_encryption=has_header_encryption,
        comment=comment,
        members=members,
        sfx_offset=sfx_offset,
        is_volume=is_volume,
        needs_next_volume=needs_next_volume,
    )


def _rar3_decrypt_header(
    source: BinaryIO, password: str | bytes, kdf_cache: RarKdfCache
) -> _HeaderDecryptStream:
    salt = _require_exact(source, 8, "RAR3 header salt")
    key, iv = kdf_cache.rar3(password, salt)
    return _HeaderDecryptStream(source, key, iv)


def _parse_rar3_file_header(
    hdata: bytes,
    pos: int,
    *,
    flags: int,
    header_offset: int,
    header_size: int,
    data_offset: int,
    volume_index: int,
    is_service: bool,
) -> tuple[RarMemberInfo, int]:
    if pos + _S_FILE_HDR.size > len(hdata):
        raise CorruptionError("Truncated RAR3 file header")
    fld = _S_FILE_HDR.unpack_from(hdata, pos)
    pos += _S_FILE_HDR.size

    compress_size = fld[0]
    file_size = fld[1]
    host_os = fld[2]
    crc32 = fld[3]
    dos_stamp = fld[4]
    extract_version = fld[5]  # UNP_VER as stored; not clamped to 15/20/29/50
    compress_type = fld[6]
    name_size = fld[7]
    mode = fld[8]

    mtime: datetime | None = _parse_dos_time(dos_stamp)

    if flags & _RAR3_FILE_LARGE:
        h1, pos = _load_le32(hdata, pos)
        h2, pos = _load_le32(hdata, pos)
        compress_size |= h1 << 32
        file_size |= h2 << 32

    name, pos = _load_bytes(hdata, name_size, pos)
    orig_filename: bytes | None
    if flags & _RAR3_FILE_UNICODE and b"\0" in name:
        nul = name.find(b"\0")
        orig_filename = name[:nul]
        decoded = _decode_rar3_unicode_name(orig_filename, name[nul + 1 :])
        if decoded is None:
            filename = _decode_name(orig_filename)
        else:
            filename = _fix_rar3_astral_truncation(decoded, orig_filename)
    elif flags & _RAR3_FILE_UNICODE:
        orig_filename = name
        filename = name.decode("utf8", "replace")
    else:
        orig_filename = name
        filename = _decode_name(name)

    filename = filename.replace("\\", "/").rstrip("/")
    is_directory = (flags & _RAR3_FILE_DIRECTORY) == _RAR3_FILE_DIRECTORY
    is_symlink = (
        not is_service
        and host_os == _RAR3_OS_UNIX
        and mode is not None
        and (mode & 0xF000) == 0xA000
    )
    file_version: int | None = None
    if flags & _RAR3_FILE_VERSION:
        filename, file_version = _rar3_split_file_version(filename)
    if is_directory:
        filename = filename + "/"

    if flags & _RAR3_FILE_SALT:
        _salt, pos = _load_bytes(hdata, 8, pos)

    ctime: datetime | None = None
    atime: datetime | None = None
    if flags & _RAR3_FILE_EXTTIME:
        xt_mtime, ctime, atime, pos = _parse_rar3_ext_time(hdata, pos, mtime)
        if xt_mtime is not None:
            mtime = xt_mtime
    # else: keep DOS mtime (spec: RAR4 naive wall-clock)

    # CRC covers through the file-header fields; old comment subblocks (if any) follow.
    crc_pos = pos if not is_service else header_size

    member = RarMemberInfo(
        filename=filename,
        orig_filename=orig_filename,
        file_size=file_size,
        compress_size=compress_size,
        compress_type=compress_type,
        crc32=crc32,
        blake2sp_hash=None,
        mtime=None if is_service else mtime,
        ctime=None if is_service else ctime,
        atime=None if is_service else atime,
        mode=mode,
        host_os=host_os,
        flags=flags,
        file_redir=None,
        file_encryption=None,
        header_offset=header_offset,
        header_size=header_size,
        data_offset=data_offset,
        extract_version=extract_version,
        file_solid=bool(flags & _RAR3_FILE_SOLID),
        is_directory=is_directory and not is_symlink,
        is_symlink=is_symlink,
        is_hardlink_or_copy=False,
        is_encrypted=bool(flags & _RAR3_FILE_PASSWORD),
        volume_index=volume_index,
        split_before=bool(flags & _RAR3_FILE_SPLIT_BEFORE),
        split_after=bool(flags & _RAR3_FILE_SPLIT_AFTER),
        file_version=file_version,
    )
    return member, crc_pos


def _rar3_split_file_version(filename: str) -> tuple[str, int | None]:
    """Split a RAR3 ``path;n`` version suffix into ``(path, n)``.

    RAR3 stores the version in the header name when ``FILE_VERSION`` is set.
    Returns ``(filename, None)`` when no trailing decimal ``;n`` is present.
    """
    stem, sep, ver = filename.rpartition(";")
    if not sep or not ver.isdigit():
        return filename, None
    return stem, int(ver)


def _parse_rar3_ext_time(
    data: bytes, pos: int, dos_mtime: datetime | None
) -> tuple[datetime | None, datetime | None, datetime | None, int]:
    """Parse RAR3 EXTTIME. Returns ``(mtime, ctime, atime, pos)``.

    Nibble order is UnRAR / rarfile, not a guess: flags>>12 mtime (refines the
    header DOS stamp), >>8 ctime, >>4 atime, low nibble arctime. arctime has no
    ``ArchiveMember`` field and is walked only so the cursor stays aligned.
    """
    flags = 0
    if pos + 2 <= len(data):
        flags = _S_SHORT.unpack_from(data, pos)[0]
        pos += 2

    mtime, pos = _parse_rar3_xtime(flags >> 12, data, pos, dos_mtime)
    ctime, pos = _parse_rar3_xtime(flags >> 8, data, pos, None)
    atime, pos = _parse_rar3_xtime(flags >> 4, data, pos, None)
    _, pos = _parse_rar3_xtime(flags, data, pos, None)
    return mtime, ctime, atime, pos


def _parse_rar3_xtime(
    flag: int,
    data: bytes,
    pos: int,
    basetime: datetime | None,
) -> tuple[datetime | None, int]:
    if not (flag & 8):
        return None, pos
    if basetime is None:
        if pos + 4 > len(data):
            return None, pos
        stamp, pos = _load_le32(data, pos)
        basetime = _parse_dos_time(stamp)

    rem = 0
    cnt = flag & 3
    for _ in range(cnt):
        if pos >= len(data):
            break
        b, pos = _load_byte(data, pos)
        rem = (b << 16) | (rem >> 8)

    if flag & 4 and basetime.second < 59:
        basetime = basetime.replace(second=basetime.second + 1)

    # Convert 100ns units to microseconds (rarfile uses nsdatetime; we keep µs).
    usec = (rem * 100) // 1000
    try:
        return basetime.replace(microsecond=min(usec, 999999)), pos
    except ValueError:
        return basetime, pos


@dataclass(slots=True)
class _Rar5HdrEnc:
    algo: int
    flags: int
    kdf_count: int
    salt: bytes
    check_value: bytes | None


def _rar5_locator_qopen_abs(
    hdata: bytes, extra_size: int, header_offset: int
) -> int | None:
    """Absolute offset of the QO SERVICE header from MAIN extra 0x01, or None.

    Locator field 0 means the reserved vint had no room for the real offset
    (technote: ignore). The stored value is the distance from MAIN to QO.
    """
    if extra_size <= 0:
        return None
    extra_start = len(hdata) - extra_size
    if extra_start < 0:
        return None
    pos = extra_start
    while pos < len(hdata) - 1:
        try:
            xsize, pos = load_vint(hdata, pos)
        except CorruptionError:
            break
        if xsize < 1 or pos + xsize > len(hdata):
            # Same rule as the FILE extra walk: one byte is the smallest legal
            # record, so a declared size of zero is a broken size rather than an
            # empty record. Stopping matters here for cost rather than
            # correctness — a zero-size record advances one byte and raises, so
            # falling through would walk a crafted MAIN extra one byte and one
            # exception at a time. Giving up costs only quick open, which is what
            # every other exit from this walk costs too.
            break
        xdata, pos = _load_bytes(hdata, xsize, pos)
        try:
            xtype, xp = load_vint(xdata, 0)
        except CorruptionError:
            continue
        if xtype != _RAR5_MHEXTRA_LOCATOR:
            continue
        try:
            flags, xp = load_vint(xdata, xp)
            if not (flags & _RAR5_MHEXTRA_LOCATOR_QLIST):
                return None
            offset, xp = load_vint(xdata, xp)
        except CorruptionError:
            return None
        if offset == 0:
            return None
        abs_off = header_offset + offset
        if abs_off < 0 or abs_off > _MAX_SEEK:
            return None
        return abs_off
    return None


def _is_stored_rar5_cmt(member: RarMemberInfo) -> bool:
    # ``encryption_unknown`` refuses for the same reason ``is_encrypted`` does:
    # this gate slices the payload straight out of the archive and decodes it as
    # text, so a header that stopped before it could rule encryption out would
    # put ciphertext in ``ArchiveInfo.comment``. Losing the comment is a missing
    # answer; that would be a wrong one.
    return (
        member.filename == _RAR5_CMT_NAME
        and member.compress_type == _RAR3_M0
        and not member.split_before
        and not member.split_after
        and member.compress_size > 0
        and not member.is_encrypted
        and not member.encryption_unknown
    )


def _decode_rar5_cmt_bytes(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("utf8", "replace")


def _parse_rar5_qo_payload(
    payload: bytes,
    qo_header_offset: int,
    volume_index: int,
    *,
    max_members: int | None,
) -> list[RarMemberInfo] | None:
    """Parse QO cache structures into FILE members, or None if unusable."""
    members: list[RarMemberInfo] = []
    pos = 0
    n = len(payload)
    # Trailing zeros: AES/size overprovisioning. Stop before them; do not try
    # to parse a CRC=0 record out of the pad.
    data_end = len(payload.rstrip(b"\0"))
    while pos < data_end:
        if n - pos < 5:
            return None
        try:
            crc, pos = _load_le32(payload, pos)
            body_size, pos_after_size = load_vint(payload, pos)
        except CorruptionError:
            return None
        size_bytes = payload[pos:pos_after_size]
        pos = pos_after_size
        if body_size <= 0 or body_size > _RAR5_MAX_HEADER or pos + body_size > n:
            return None
        body = payload[pos : pos + body_size]
        pos += body_size
        if _crc32(size_bytes + body) != crc:
            return None
        try:
            bp = 0
            # UnRAR reads this flags vint and does not act on it.
            _flags, bp = load_vint(body, bp)
            offset_from_qo, bp = load_vint(body, bp)
            data_size, bp = load_vint(body, bp)
        except CorruptionError:
            return None
        if data_size < 0 or bp + data_size > len(body):
            return None
        cached = body[bp : bp + data_size]
        if offset_from_qo <= 0 or offset_from_qo > qo_header_offset:
            return None
        original_offset = qo_header_offset - offset_from_qo
        bio = io.BytesIO(cached)
        try:
            parsed = _read_rar5_block(bio)
        except (CorruptionError, TruncatedError):
            return None
        if parsed is None:
            return None
        (
            block_type,
            block_flags,
            hdata,
            hpos,
            header_offset_rel,
            header_size,
            data_offset_rel,
            add_size,
            extra_size,
        ) = parsed
        if block_type != _RAR5_FILE:
            continue
        try:
            member = _parse_rar5_file_block(
                hdata,
                hpos,
                block_flags=block_flags,
                extra_size=extra_size,
                header_offset=original_offset + header_offset_rel,
                header_size=header_size,
                data_offset=original_offset + data_offset_rel,
                add_size=add_size,
                volume_index=volume_index,
            )
        except (CorruptionError, TruncatedError):
            return None
        _append_member(members, member, max_members=max_members)
    if not members:
        return None
    return members


def _qo_spans_consistent(
    members: list[RarMemberInfo], *, min_offset: int, qopen_abs: int
) -> bool:
    """True if QO-derived packed spans do not overlap and end before QO."""
    prev_end = min_offset
    for member in sorted(members, key=lambda m: m.header_offset):
        if member.header_offset < prev_end:
            return False
        prev_end = _packed_end(member)
    return prev_end <= qopen_abs


def _try_list_via_rar5_qo(
    source: BinaryIO,
    *,
    qopen_abs: int,
    volume_index: int,
    min_file_offset: int,
    max_members: int | None,
) -> tuple[list[RarMemberInfo], int] | None:
    """Seek to QO and parse FILE copies.

    On success the file pointer is at the end of QO's packed span (also
    returned). The caller seeks back to after MAIN and walks, skipping FILE
    offsets present in the copies. None means the caller should walk FILE
    headers with an empty skip map.
    """
    try:
        source.seek(qopen_abs)
    except (OSError, OverflowError):
        return None
    try:
        parsed = _read_rar5_block(source)
        if parsed is None:
            return None
        (
            block_type,
            block_flags,
            hdata,
            pos,
            header_offset,
            header_size,
            data_offset,
            add_size,
            extra_size,
        ) = parsed
        if block_type != _RAR5_SERVICE:
            return None
        member = _parse_rar5_file_block(
            hdata,
            pos,
            block_flags=block_flags,
            extra_size=extra_size,
            header_offset=header_offset,
            header_size=header_size,
            data_offset=data_offset,
            add_size=add_size,
            volume_index=volume_index,
        )
        if (
            member.filename != _RAR5_QO_NAME
            or member.compress_type != _RAR3_M0
            or member.is_encrypted
            # Same slice-and-parse hazard as the CMT gate above: an unsettled
            # header would have this parse a member table out of bytes that may
            # be ciphertext. Refusing costs the quick open, and the FILE walk
            # answers the same question from the headers themselves.
            or member.encryption_unknown
            or member.split_before
            or member.split_after
            or member.file_size <= 0
            or member.file_size != member.compress_size
            or member.file_size > _RAR5_QO_PAYLOAD_MAX
        ):
            return None
        source.seek(data_offset)
        payload = _require_exact(source, member.file_size, "RAR5 QO")
        qo_members = _parse_rar5_qo_payload(
            payload, header_offset, volume_index, max_members=max_members
        )
        if qo_members is None:
            return None
        if not _qo_spans_consistent(
            qo_members, min_offset=min_file_offset, qopen_abs=qopen_abs
        ):
            return None
        _seek_after_packed(source, data_offset, add_size)
        return qo_members, source.tell()
    # ResourceLimitError must propagate: an over-limit QO is not "unusable".
    except (CorruptionError, TruncatedError, OSError, OverflowError):
        return None


def _adopt_rar5_file_members(
    members: list[RarMemberInfo],
    incoming: list[RarMemberInfo],
    *,
    max_members: int | None,
) -> bool:
    """Append FILE members with the same split-merge as the header walk.

    Returns whether any member has ``split_after`` (volume continuation).
    """
    needs_next = False
    for member in incoming:
        if _emit_rar5_file_member(members, member, max_members=max_members):
            needs_next = True
    return needs_next


def _emit_rar5_file_member(
    members: list[RarMemberInfo],
    member: RarMemberInfo,
    *,
    max_members: int | None,
) -> bool:
    """Split-merge one FILE into ``members``. Returns ``split_after``."""
    if member.split_before:
        if members:
            _merge_split_member(members[-1], member)
        else:
            _append_member(members, member, max_members=max_members)
    else:
        _append_member(members, member, max_members=max_members)
    return member.split_after


def _packed_end(member: RarMemberInfo) -> int:
    return member.data_offset + member.compress_size


def _emit_and_skip_qo_run(
    source: BinaryIO,
    *,
    qo_by_off: dict[int, RarMemberInfo],
    members: list[RarMemberInfo],
    seen_file_offsets: set[int],
    max_members: int | None,
) -> bool | None:
    """If ``tell()`` is a QO FILE, emit the consecutive cached run and seek past it.

    Returns ``None`` when ``tell()`` is not a QO FILE (caller parses the block).
    Otherwise returns whether any emitted member has ``split_after``.
    """
    pos = source.tell()
    if pos not in qo_by_off:
        return None
    run: list[RarMemberInfo] = []
    visited: set[int] = set()
    while pos in qo_by_off:
        if pos in visited:
            return None
        visited.add(pos)
        member = qo_by_off[pos]
        run.append(member)
        nxt = _packed_end(member)
        if nxt <= pos:
            return None
        pos = nxt
    needs_next = _adopt_rar5_file_members(members, run, max_members=max_members)
    seen_file_offsets.update(m.header_offset for m in run)
    _seek_to(source, pos)
    return needs_next


def _parse_rar5(
    source: BinaryIO,
    *,
    password: str | bytes | None,
    kdf_cache: RarKdfCache,
    sfx_offset: int,
    volume_index: int = 0,
    use_qo: bool = True,
    max_members: int | None,
) -> RarArchive:
    _require_exact(source, len(RAR5_ID), "RAR5 signature")

    is_solid = False
    is_volume = False
    has_header_encryption = False
    comment: str | None = None
    members: list[RarMemberInfo] = []
    hdr_enc: _Rar5HdrEnc | None = None
    # Whether a check value positively confirmed the header password. When False and
    # headers are encrypted, a wrong password and genuine corruption are
    # indistinguishable (no verifier), so a decrypted-header structural failure is
    # reported as EncryptionError rather than CorruptionError (see _check_rar5_password).
    password_verified = False
    needs_next_volume = False
    seen_file_offsets: set[int] = set()
    qo_by_off: dict[int, RarMemberInfo] = {}
    damaged_service_headers: list[DamagedServiceHeader] = []
    damaged_service_headers_omitted = 0

    while True:
        header_fd: _Readable = source
        if hdr_enc is not None:
            has_header_encryption = True
            if password is None:
                raise EncryptionError(
                    "RAR archive has encrypted headers but no password was provided"
                )
            try:
                header_fd = _rar5_decrypt_header(source, hdr_enc, password, kdf_cache)
            except (PackageNotInstalledError, ResourceLimitError):
                # See the RAR3 walk: a spent budget must not read as a wrong password.
                raise
            except EncryptionError:
                raise
            except Exception as exc:
                raise EncryptionError(
                    f"Failed to decrypt RAR5 headers: {raw_message_of(exc)}"
                ) from exc

        skipped = _emit_and_skip_qo_run(
            source,
            qo_by_off=qo_by_off,
            members=members,
            seen_file_offsets=seen_file_offsets,
            max_members=max_members,
        )
        if skipped is not None:
            if skipped:
                needs_next_volume = True
            continue

        # A wrong password produces a garbage decrypted header that fails the block CRC
        # (or advertises an absurd size). Without a check value to prove the key, that is
        # indistinguishable from corruption, so surface it as EncryptionError so password
        # candidates keep iterating. A verified key means a failure here is real corruption.
        if hdr_enc is not None and not password_verified:
            try:
                parsed = _read_rar5_block(header_fd)
            except (CorruptionError, TruncatedError) as exc:
                raise EncryptionError(
                    "Failed to decrypt RAR5 headers (wrong password?)"
                ) from exc
        else:
            parsed = _read_rar5_block(header_fd)
        if parsed is None:
            break
        (
            block_type,
            block_flags,
            hdata,
            pos,
            header_offset,
            header_size,
            data_offset,
            add_size,
            extra_size,
        ) = parsed

        if block_type == _RAR5_MAIN:
            main_flags, pos = load_vint(hdata, pos)
            if main_flags & _RAR5_MAIN_HAS_VOLNR:
                volnr, pos = load_vint(hdata, pos)
                # RAR5: first volume omits the field (implicit 0); later volumes
                # store 1 for the second volume, 2 for the third, …
                if volume_index == 0 and volnr != 0:
                    raise UnsupportedFeatureError(
                        "Need first volume of multi-volume RAR archive"
                    )
                if volume_index > 0 and volnr != volume_index:
                    raise TruncatedError(
                        f"Out-of-order RAR volume: expected volume index "
                        f"{volume_index}, got {volnr}"
                    )
            elif volume_index > 0:
                raise TruncatedError(
                    f"Out-of-order RAR volume: expected volume index {volume_index}, "
                    f"got first-volume header"
                )
            is_solid = bool(main_flags & _RAR5_MAIN_SOLID)
            is_volume = bool(main_flags & _RAR5_MAIN_ISVOL)
            _seek_after_packed(source, data_offset, add_size)
            # Header-encrypted QO stores IV+ciphertext header copies and
            # file-encrypts the QO payload; reconstructed data_offset then
            # misses AES padding. Treat that QO as unreadable (FILE walk).
            if hdr_enc is None and use_qo:
                qopen_abs = _rar5_locator_qopen_abs(hdata, extra_size, header_offset)
                if qopen_abs is not None:
                    resume_pos = source.tell()
                    listed = _try_list_via_rar5_qo(
                        source,
                        qopen_abs=qopen_abs,
                        volume_index=volume_index,
                        min_file_offset=resume_pos,
                        max_members=max_members,
                    )
                    if listed is not None:
                        qo_members, _qo_end = listed
                        qo_by_off = {m.header_offset: m for m in qo_members}
                        # Back to after MAIN. CMT is a normal SERVICE on the
                        # walk; a FILE in the skip map is emitted from the copy.
                        _seek_to(source, resume_pos)
                        continue
                    _seek_to(source, resume_pos)
            continue

        if block_type == _RAR5_ENCRYPTION:
            algo, pos = load_vint(hdata, pos)
            enc_flags, pos = load_vint(hdata, pos)
            kdf_count, pos = _load_byte(hdata, pos)
            salt, pos = _load_bytes(hdata, 16, pos)
            check_value = None
            if enc_flags & _RAR5_ENC_HAS_CHECKVAL:
                check_value, pos = _load_bytes(hdata, 12, pos)
            if algo != _RAR5_XENC_AES256:
                raise UnsupportedFeatureError(
                    f"Unsupported RAR5 header encryption cipher: {algo}"
                )
            if check_value is not None and password is not None:
                password_verified = _check_rar5_password(
                    check_value, kdf_count, salt, password, kdf_cache=kdf_cache
                )
            hdr_enc = _Rar5HdrEnc(
                algo=algo,
                flags=enc_flags,
                kdf_count=kdf_count,
                salt=salt,
                check_value=check_value,
            )
            has_header_encryption = True
            if password is None:
                raise EncryptionError(
                    "RAR archive has encrypted headers but no password was provided"
                )
            _seek_after_packed(source, data_offset, add_size)
            continue

        if block_type == _RAR5_ENDARC:
            endarc_flags, _ = load_vint(hdata, pos)
            needs_next_volume = bool(endarc_flags & _RAR5_ENDARC_NEXT_VOLUME)
            break

        if block_type in (_RAR5_FILE, _RAR5_SERVICE):
            member = _parse_rar5_file_block(
                hdata,
                pos,
                block_flags=block_flags,
                extra_size=extra_size,
                header_offset=header_offset,
                header_size=header_size,
                data_offset=data_offset,
                add_size=add_size,
                volume_index=volume_index,
            )
            if block_type == _RAR5_FILE:
                # File-version history rows (extra 0x04) are kept as members.
                # QO copies are emitted in `_emit_and_skip_qo_run` before this
                # read; this branch is holes, FILE after QO, and the no-QO walk.
                if member.header_offset not in seen_file_offsets:
                    if _emit_rar5_file_member(members, member, max_members=max_members):
                        needs_next_volume = True
                    seen_file_offsets.add(member.header_offset)
            elif block_type == _RAR5_SERVICE:
                if member.skipped_header_records or member.header_walk_stop_reason:
                    if not _append_damaged_service_header(
                        damaged_service_headers, member
                    ):
                        damaged_service_headers_omitted += 1
                if _is_stored_rar5_cmt(member):
                    source.seek(data_offset)
                    raw = _require_exact(source, member.file_size, "RAR5 comment")
                    comment = _decode_rar5_cmt_bytes(raw)
            _seek_after_packed(source, data_offset, add_size)
            continue

        # Unknown block — skip data area.
        _seek_after_packed(source, data_offset, add_size)

    return RarArchive(
        version=5,
        is_solid=is_solid,
        has_header_encryption=has_header_encryption,
        comment=comment,
        members=members,
        sfx_offset=sfx_offset,
        is_volume=is_volume,
        needs_next_volume=needs_next_volume,
        damaged_service_headers=damaged_service_headers,
        damaged_service_headers_omitted=damaged_service_headers_omitted,
    )


def _read_rar5_block(
    fd: _Readable,
) -> tuple[int, int, bytes, int, int, int, int, int, int] | None:
    """Read one RAR5 block.

    Returns
    ``(type, flags, hdata, pos_after_common, header_offset, header_size,
    data_offset, add_size, extra_size)`` or ``None`` at EOF.

    Reads the size vint byte-at-a-time rather than prefetching a large window and
    seeking back: :class:`_HeaderDecryptStream` has no ``seek``, and its ``tell``
    is the ciphertext cursor. Leftover ``_buf`` is not rewind room — mid-header
    it is still-owed plaintext; after ``header_size`` it is AES padding.
    """
    header_offset = fd.tell()
    preload = 4 + 1
    head = bytearray(read_exact(fd, preload))
    if not head:
        return None
    if len(head) < preload:
        raise CorruptionError("Unexpected EOF while reading RAR5 header")
    # The header-size vint starts at byte 4 (after the 4-byte CRC). A vint is at most
    # 10 bytes; cap the continuation so a crafted run of 0x80 bytes cannot drive an
    # unbounded, O(n^2) byte-at-a-time read of the source before ``load_vint``'s own
    # 11-byte guard (which only runs *after* this loop) would reject it.
    while head[-1] & 0x80:
        if len(head) - 4 >= 10:
            raise CorruptionError(
                "Invalid RAR5 header size (variable-length integer too long)"
            )
        b = fd.read(1)
        if not b:
            raise CorruptionError("Unexpected EOF while reading RAR5 header size")
        head += b
    start_bytes = bytes(head)
    header_crc, pos = _load_le32(start_bytes, 0)
    hdrlen, pos = load_vint(start_bytes, pos)
    if hdrlen > _RAR5_MAX_HEADER:
        raise CorruptionError(f"RAR5 header too large: {hdrlen}")
    header_size = pos + hdrlen
    hdata = start_bytes + read_exact(fd, header_size - len(start_bytes))
    if len(hdata) != header_size:
        raise CorruptionError("Unexpected EOF while reading RAR5 header body")
    # Ciphertext cursor, including AES block padding. Same invariant as the
    # RAR3 walk: this is where packed data (or the next header's IV) starts.
    data_offset = fd.tell()

    if header_crc != _crc32(memoryview(hdata)[4:]):
        raise CorruptionError(f"RAR5 header CRC mismatch at offset {header_offset}")

    block_type, pos = load_vint(hdata, pos)
    block_flags, pos = load_vint(hdata, pos)
    extra_size = 0
    add_size = 0
    if block_flags & _RAR5_FLAG_EXTRA:
        extra_size, pos = load_vint(hdata, pos)
    if block_flags & _RAR5_FLAG_DATA:
        add_size, pos = load_vint(hdata, pos)
    return (
        block_type,
        block_flags,
        hdata,
        pos,
        header_offset,
        header_size,
        data_offset,
        add_size,
        extra_size,
    )


def _rar5_decrypt_header(
    source: BinaryIO,
    hdr_enc: _Rar5HdrEnc,
    password: str | bytes,
    kdf_cache: RarKdfCache,
) -> _HeaderDecryptStream:
    if hdr_enc.kdf_count > _RAR_MAX_KDF_SHIFT:
        raise CorruptionError(f"RAR5 kdf_count too large: {hdr_enc.kdf_count}")
    key = kdf_cache.rar5(password, hdr_enc.salt, 1 << hdr_enc.kdf_count)
    iv = _require_exact(source, 16, "RAR5 header IV")
    return _HeaderDecryptStream(source, key, iv)


def _check_rar5_password(
    check_value: bytes,
    kdf_count_shift: int,
    salt: bytes,
    password: str | bytes,
    *,
    kdf_cache: RarKdfCache | None = None,
) -> bool:
    """Verify the RAR5 header password against the check value.

    Returns ``True`` when the check value positively confirms the password (so a later
    header failure is genuine corruption, not a wrong password); ``False`` when the
    check value is unusable/absent (no verifier — a later failure is indistinguishable
    from a wrong password). Raises :class:`EncryptionError` when the password is
    provably wrong.
    """
    if len(check_value) != 12:
        return False
    if kdf_count_shift > _RAR_MAX_KDF_SHIFT:
        raise CorruptionError(f"RAR5 kdf_count too large: {kdf_count_shift}")
    hdr_check = check_value[:8]
    hdr_sum = check_value[8:]
    if not hmac.compare_digest(hashlib.sha256(hdr_check).digest()[:4], hdr_sum):
        return False
    kdf_count = (1 << kdf_count_shift) + 32
    derive = kdf_cache.rar5 if kdf_cache is not None else _rar5_s2k
    pwd_hash = derive(password, salt, kdf_count)
    pwd_check = bytearray(8)
    for i, v in enumerate(pwd_hash):
        pwd_check[i & 7] ^= v
    if not hmac.compare_digest(bytes(pwd_check), hdr_check):
        raise EncryptionError("Wrong password for RAR5 header encryption")
    return True


def _parse_rar5_file_block(
    hdata: bytes,
    pos: int,
    *,
    block_flags: int,
    extra_size: int,
    header_offset: int,
    header_size: int,
    data_offset: int,
    add_size: int,
    volume_index: int,
) -> RarMemberInfo:
    file_flags, pos = load_vint(hdata, pos)
    file_size, pos = load_vint(hdata, pos)
    mode, pos = load_vint(hdata, pos)

    mtime: datetime | None = None
    crc32: int | None = None
    if file_flags & _RAR5_FILE_HAS_MTIME:
        mtime, pos = _load_unixtime(hdata, pos)
    if file_flags & _RAR5_FILE_HAS_CRC32:
        crc32, pos = _load_le32(hdata, pos)

    compress_info, pos = load_vint(hdata, pos)
    host_os_raw, pos = load_vint(hdata, pos)
    orig_filename, pos = _load_vstr(hdata, pos)
    filename = orig_filename.decode("utf8", "replace").rstrip("/")

    host_os = 2 if host_os_raw == _RAR5_OS_WINDOWS else 3  # RAR_OS_WIN32 / UNIX
    compress_type = _RAR3_M0 + ((compress_info >> 7) & 7)
    file_solid = bool(compress_info & _RAR5_COMPR_SOLID)
    is_directory = bool(file_flags & _RAR5_FILE_ISDIR)
    split_before = bool(block_flags & _RAR5_FLAG_SPLIT_BEFORE)
    split_after = bool(block_flags & _RAR5_FLAG_SPLIT_AFTER)

    blake2sp_hash: bytes | None = None
    file_redir: tuple[int, int, str] | None = None
    file_encryption: RarEncryptionInfo | None = None
    file_version: int | None = None
    flags = 0
    ctime: datetime | None = None
    atime: datetime | None = None
    skipped_records: list[tuple[str, int | None, str]] = []
    # Why the walk stopped, or ``None`` if it ran to the end. Four exits reach it
    # and they are not the same fault, so the diagnostic must not name one of them
    # for all four: a single zero-size record is not "more than sixteen malformed".
    stop_reason: str | None = None

    if extra_size:
        # Walk extras until near end (allow 1 byte of padding like rarfile).
        while pos < len(hdata) - 1:
            if len(skipped_records) >= _MAX_SKIPPED_HEADER_RECORDS:
                # Still inside the loop, so bytes remain; a member whose last
                # skipped record is also its last extra never gets here.
                stop_reason = (
                    f"more than {_MAX_SKIPPED_HEADER_RECORDS} of its extra "
                    f"records were malformed"
                )
                break
            try:
                xsize, pos = load_vint(hdata, pos)
            except CorruptionError as exc:
                # ``load_vint`` does not advance ``pos`` on failure, so the
                # next record has no boundary. Stop, and say so.
                skipped_records.append(("unknown", None, raw_message_of(exc)))
                stop_reason = "an extra record's size could not be read"
                break
            if xsize < 1:
                # A record's body opens with its type vint, so one byte is the
                # smallest legal record: a type with no payload, which is what an
                # unimplemented record looks like. A declared size of zero names
                # nothing, so the size vint is wrong and the offset it puts the
                # next record at is wrong with it. It is also one attacker byte
                # per record, which is what made a crafted extra area expensive
                # rather than merely damaged. ``unrar`` 7.00 does carry on here
                # and gets a wrong answer for it — one such record in front of an
                # encrypted member and ``unrar l`` reports it as plaintext — so
                # the oracle that justifies the leniency above does not reach
                # this case.
                skipped_records.append(
                    ("unknown", None, "extra record declares a size of zero")
                )
                stop_reason = "an extra record declared a size of zero"
                break
            if pos + xsize > len(hdata):
                skipped_records.append(
                    ("unknown", None, "extra record overruns the extra area")
                )
                stop_reason = "an extra record overran the extra area"
                break
            xdata, pos = _load_bytes(hdata, xsize, pos)
            try:
                xtype, xpos = load_vint(xdata, 0)
            except CorruptionError as exc:
                # A body of the declared length whose type vint has no terminating
                # byte. The framing is intact — the next record's offset is known —
                # so this is a dropped record like any other, not a reason to stop.
                # Two attacker bytes apiece, which is what the cap at the loop head
                # keeps from becoming one retained tuple per extra byte.
                skipped_records.append(("unknown", None, raw_message_of(exc)))
                continue
            try:
                if xtype == _RAR5_XFILE_TIME:
                    mtime, ctime, atime = _parse_rar5_xtime(
                        xdata, xpos, mtime, ctime, atime
                    )
                elif xtype == _RAR5_XFILE_ENCRYPTION:
                    # Deliberately *not* skippable. Dropping this record would
                    # leave ``file_encryption`` unset and the member would list as
                    # plaintext, which is a wrong answer rather than a missing one
                    # — the failure class this library treats as the worst. A
                    # member whose encryption parameters cannot be read is not a
                    # member that can be presented at all.
                    file_encryption = _parse_rar5_file_encryption(xdata, xpos)
                    flags |= _RAR3_FILE_PASSWORD
                elif xtype == _RAR5_XFILE_HASH:
                    hash_type, xpos = load_vint(xdata, xpos)
                    if hash_type == _RAR5_XHASH_BLAKE2SP:
                        blake2sp_hash, xpos = _load_bytes(xdata, 32, xpos)
                elif xtype == _RAR5_XFILE_REDIR:
                    redir_type, xpos = load_vint(xdata, xpos)
                    redir_flags, xpos = load_vint(xdata, xpos)
                    redir_name, xpos = _load_vstr(xdata, xpos)
                    file_redir = (
                        redir_type,
                        redir_flags,
                        redir_name.decode("utf8", "replace"),
                    )
                elif xtype == _RAR5_XFILE_VERSION:
                    _vflags, xpos = load_vint(xdata, xpos)
                    file_version, xpos = load_vint(xdata, xpos)
                # OWNER / SERVICE / unknown: ignore
            except CorruptionError as exc:
                if xtype == _RAR5_XFILE_ENCRYPTION:
                    raise
                # Drop this record and keep the member. The walk already ignores a
                # record type it does not know; a *known* type it cannot parse is
                # the same amount of missing information, and refusing the archive
                # over it loses every member that parsed. ``unrar`` 7.00 lists such
                # an archive. The caller surfaces this as a diagnostic, which under
                # a strict policy raises — so strictness stays available without
                # being the default. Whatever the record would have set keeps the
                # value it had; nothing half-written is committed, because each
                # branch assigns only on its own last statement.
                skipped_records.append(
                    (_RAR5_XNAMES.get(xtype, "unknown"), xtype, raw_message_of(exc))
                )

    is_symlink = False
    is_hardlink_or_copy = False
    if file_redir is not None:
        rtype = file_redir[0]
        if rtype in (
            _RAR5_XREDIR_UNIX_SYMLINK,
            _RAR5_XREDIR_WINDOWS_SYMLINK,
            _RAR5_XREDIR_WINDOWS_JUNCTION,
        ):
            is_symlink = True
        elif rtype in (_RAR5_XREDIR_HARD_LINK, _RAR5_XREDIR_FILE_COPY):
            is_hardlink_or_copy = True

    if is_directory and not is_symlink:
        filename = filename + "/"

    return RarMemberInfo(
        filename=filename,
        orig_filename=orig_filename,
        file_size=file_size,
        compress_size=add_size,
        compress_type=compress_type,
        crc32=crc32,
        blake2sp_hash=blake2sp_hash,
        mtime=mtime,
        ctime=ctime,
        atime=atime,
        mode=mode,
        host_os=host_os,
        flags=flags,
        file_redir=file_redir,
        file_encryption=file_encryption,
        header_offset=header_offset,
        header_size=header_size,
        data_offset=data_offset,
        extract_version=50,
        file_solid=file_solid,
        is_directory=is_directory and not is_symlink,
        is_symlink=is_symlink,
        is_hardlink_or_copy=is_hardlink_or_copy,
        # What the header actually said, and only that. A member whose walk
        # stopped before the encryption record could be ruled out is not
        # "not encrypted" — it is *unknown*, which is ``encryption_unknown``
        # rather than this flag. Keeping the two apart is what stops one
        # damaged member from reporting a whole plaintext archive as encrypted.
        is_encrypted=file_encryption is not None,
        volume_index=volume_index,
        split_before=split_before,
        split_after=split_after,
        file_version=file_version,
        skipped_header_records=tuple(skipped_records),
        header_walk_stop_reason=stop_reason,
    )


def _apply_rar5_unix_ns(
    xdata: bytes, pos: int, dt: datetime | None, *, present: bool
) -> tuple[datetime | None, int]:
    """Consume a unix-ns extra when the corresponding HAS_* flag is set.

    The nsec word is present whenever HAS_* is set, even if the timestamp
    itself failed to decode — skip it and the later fields desync.
    """
    if not present:
        return dt, pos
    if pos + 4 > len(xdata):
        # Truncated ns word: keep whatever decoded, do not abort the listing.
        return dt, pos
    nsec, pos = _load_le32(xdata, pos)
    if dt is None:
        return None, pos
    try:
        return dt.replace(microsecond=min(nsec // 1000, 999999)), pos
    except ValueError:
        return dt, pos


def _parse_rar5_xtime(
    xdata: bytes,
    pos: int,
    current_mtime: datetime | None,
    current_ctime: datetime | None = None,
    current_atime: datetime | None = None,
) -> tuple[datetime | None, datetime | None, datetime | None]:
    tflags, pos = load_vint(xdata, pos)
    ldr = _load_windowstime
    if tflags & _RAR5_XTIME_UNIXTIME:
        ldr = _load_unixtime
    mtime = current_mtime
    ctime = current_ctime
    atime = current_atime
    if tflags & _RAR5_XTIME_HAS_MTIME:
        mtime, pos = ldr(xdata, pos)
    if tflags & _RAR5_XTIME_HAS_CTIME:
        ctime, pos = ldr(xdata, pos)
    if tflags & _RAR5_XTIME_HAS_ATIME:
        atime, pos = ldr(xdata, pos)
    if tflags & _RAR5_XTIME_UNIXTIME_NS:
        mtime, pos = _apply_rar5_unix_ns(
            xdata, pos, mtime, present=bool(tflags & _RAR5_XTIME_HAS_MTIME)
        )
        ctime, pos = _apply_rar5_unix_ns(
            xdata, pos, ctime, present=bool(tflags & _RAR5_XTIME_HAS_CTIME)
        )
        atime, pos = _apply_rar5_unix_ns(
            xdata, pos, atime, present=bool(tflags & _RAR5_XTIME_HAS_ATIME)
        )
    return mtime, ctime, atime


def _parse_rar5_file_encryption(xdata: bytes, pos: int) -> RarEncryptionInfo:
    algo, pos = load_vint(xdata, pos)
    flags, pos = load_vint(xdata, pos)
    kdf_count, pos = _load_byte(xdata, pos)
    salt, pos = _load_bytes(xdata, 16, pos)
    iv, pos = _load_bytes(xdata, 16, pos)
    check_value = None
    if flags & _RAR5_XENC_CHECKVAL:
        check_value, pos = _load_bytes(xdata, 12, pos)
    return RarEncryptionInfo(
        algo=algo,
        flags=flags,
        kdf_count=kdf_count,
        salt=salt,
        iv=iv,
        check_value=check_value,
    )


# ---------------------------------------------------------------------------
# ISC License notice for algorithms adapted from rarfile
# (RAR3 SHA-1 / string-to-key and Unicode filename decompression above)
# ---------------------------------------------------------------------------
#
# Copyright (c) 2005-2024 Marko Kreen <markokr@gmail.com>
#
# Permission to use, copy, modify, and/or distribute this software for any
# purpose with or without fee is hereby granted, provided that the above
# copyright notice and this permission notice appear in all copies.
#
# THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
# WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
# MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
# ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
# WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
# ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
# OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
