"""ZIP backend on the v2 ABC.

Layout this code assumes::

    [ local file header + payload ]*   # PK\\x03\\x04 … name | extra | [data]
    [ central directory ]              # ZipFile reads this at open (INDEXED listing)
    [ EOCD (+ optional ZIP64) ]        # at EOF → seekable source required

Who does what:

- ``zipfile.ZipFile`` / ``ZipInfo`` — central-directory parse, listing, shared ``fp`` lock.
- Member **data** — slice the local payload, decrypt it if encrypted, and decode via the
  shared codec layer (never ``ZipExtFile``), so accelerators / rewind warnings / CRC
  verification stay uniform.
- Decrypt stages: ZipCrypto (``internal.backends.zipcrypto``, weak 1-byte check) and
  WinZip AES (method 99, ``internal.backends.zip_aes``, real method from extra field
  ``0x9901``). One password ladder for both: a bounded confirm per candidate when
  several are possible (a shared CRC pass for STORED ZipCrypto).
- PKWARE Strong Encryption — refused (``UnsupportedFeatureError``).

Split/spanned multi-volume sets are rejected — rejoin first (see ``format-zip``):
Info-ZIP ``.zNN`` / final ``.zip`` (EOCD disk fields), 7-Zip ``.zip.NNN``, and
ZIP64 locator ``disks > 1``.
"""

from __future__ import annotations

import io
import lzma
import stat
import struct
import zipfile
import zlib
from collections.abc import Callable
from contextlib import AbstractContextManager, contextmanager
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import (
    IO,
    TYPE_CHECKING,
    BinaryIO,
    Iterator,
    Literal,
    Mapping,
    NoReturn,
    TypeVar,
    cast,
)

from archivey.config import AcceleratorMode, ArchiveyConfig
from archivey.cost import (
    AccessCost,
    CostReceipt,
    ListingCost,
    StreamCapability,
)
from archivey.diagnostics import (
    DiagnosticCode,
    EncryptedVerificationContext,
    MemberTimestampContext,
    NameEncodingContext,
    raw_name_to_base64,
)
from archivey.exceptions import (
    ArchiveyError,
    ArchiveyUsageError,
    CorruptionError,
    EncryptionError,
    StreamNotSeekableError,
    TruncatedError,
    UnsupportedFeatureError,
    raw_message_of,
)
from archivey.internal.backends.zip_aes import (
    WinZipAesInfo,
    open_winzip_aes_member,
    parse_winzip_aes_extra,
)
from archivey.internal.backends.zip_detect import (
    ZIP_MULTI_VOLUME_MSG,
    is_zip_split_segment_name,
    validate_zip_local_header,
)
from archivey.internal.backends.zipcrypto import (
    ZIPCRYPTO_HEADER_LEN,
    ZipCryptoDecryptStream,
    keys_after_header,
    parallel_plaintext_crc32,
    password_matches_check_byte,
)
from archivey.internal.base_reader import BaseArchiveReader, ReadBackend
from archivey.internal.config import stream_config_from_archivey
from archivey.internal.diagnostics_collector import DiagnosticCollector
from archivey.internal.logs import backends as logger
from archivey.internal.logs import integrity as integrity_logger
from archivey.internal.naming import (
    emit_member_name_normalized,
    normalize_member_name,
)
from archivey.internal.open_site import OpenSite
from archivey.internal.password import (
    _PasswordCandidates,
    _PasswordCandidatesExhausted,
    wrong_password_error,
)
from archivey.internal.password_confirm import (
    PASSWORD_CONFIRM_PREFIX_BYTES,
    REJECTING_CODECS,
    PasswordConfirmPlan,
    PasswordConfirmVerdict,
    UnverifiedPasswordReadWatch,
    first_crc_match,
    plan_password_confirm,
    run_password_confirm_plan,
)
from archivey.internal.registry import register_reader
from archivey.internal.source import ArchiveSource
from archivey.internal.streams.archive_stream import ArchiveStream
from archivey.internal.streams.codecs import (
    Codec,
    CodecParams,
    open_codec_stream,
)
from archivey.internal.streams.streamtools import (
    DelegatingStream,
    SharedView,
    SlicingStream,
    read_exact,
)
from archivey.internal.streams.verify import VerifyingStream
from archivey.internal.timestamps import (
    TimestampIssue,
    filetime_to_datetime,
    unix_to_datetime,
)
from archivey.internal.windows_reparse import FILE_ATTRIBUTE_REPARSE_POINT
from archivey.terminal import quoted
from archivey.types import (
    EXTRA_IS_REPARSE_POINT,
    ArchiveFormat,
    ArchiveInfo,
    ArchiveInfoExtra,
    ArchiveMember,
    CompressionAlgorithm,
    CompressionMethod,
    CreateSystem,
    HashAlgorithm,
    MagicSignature,
    MemberExtra,
    MemberStreams,
    MemberType,
    crc32_digest,
)

if TYPE_CHECKING:
    from _typeshed import WriteableBuffer

# Comment decoding: try UTF-8 first, else fall back to cp437 (the ZIP appnote default,
# which maps every byte and therefore never fails — no further fallbacks are reachable).
_ZIP_ENCODINGS = ("utf-8", "cp437")

# bz2 uses OSError for this decoder-specific failure. Match the complete message so an
# unrelated filesystem/source OSError remains a genuine I/O error and propagates unchanged.
_BZIP2_INVALID_DATA = "Invalid data stream"

# ZIP general-purpose bit 3: data descriptor follows the member; verification byte is
# then the high byte of the DOS time rather than of the CRC-32.
_ZIP_MASK_USE_DATA_DESCRIPTOR = 0x8

# ZIP general-purpose bit 0: the member is encrypted.
_ZIP_MASK_ENCRYPTED = 0x1
# PKWARE Strong Encryption (APPNOTE §7): general-purpose bit 6 on an encrypted member
# (bit 0 is set with it), with the algorithm in extra field 0x0017. Either marks the
# member; archivey does not implement the algorithm.
_ZIP_MASK_STRONG_ENCRYPTION = 0x40
_ZIP_EXTRA_STRONG_ENCRYPTION = 0x0017
# Archive extra data record: written in front of a central directory that PKWARE
# Strong Encryption has encrypted (APPNOTE §4.3.11, §7.3).
_ZIP_ARCHIVE_EXTRA_DATA_SIG = b"PK\x06\x08"
_STRONG_ENCRYPTION_MSG = (
    "PKWARE Strong Encryption is not supported (only ZipCrypto and WinZip AES are)"
)

# Classic EOCD ``this_disk`` / ``cd_start_disk`` use 0xFFFF to mean "see ZIP64 EOCD",
# not disk 65535. A naive ``!= 0`` check would refuse legitimate ZIP64 archives.
_ZIP64_DISK_SENTINEL = 0xFFFF

# ZIP compression-method id -> our codec algorithm. Unknown ids map to UNKNOWN rather than
# raising, matching the open-ended CompressionAlgorithm contract.
_ZIP_COMPRESSION_ALGOS: dict[int, CompressionAlgorithm] = {
    0: CompressionAlgorithm.STORED,
    8: CompressionAlgorithm.DEFLATE,
    9: CompressionAlgorithm.DEFLATE64,
    12: CompressionAlgorithm.BZIP2,
    14: CompressionAlgorithm.LZMA,
    93: CompressionAlgorithm.ZSTD,
    98: CompressionAlgorithm.PPMD,
}

# Shared compression tuples for the common ZIP methods — avoid per-member
# CompressionMethod construction on the open+list hot path (perf review H3/Q1).
_ZIP_COMPRESSION_TUPLES: dict[int, tuple[CompressionMethod, ...]] = {
    method_id: (CompressionMethod(algo=algo),)
    for method_id, algo in _ZIP_COMPRESSION_ALGOS.items()
}

# ZIP method id -> shared codec-layer Codec for member decode, after any decrypt stage.
_ZIP_METHOD_CODECS: dict[int, Codec] = {
    0: Codec.STORED,
    8: Codec.DEFLATE,
    9: Codec.DEFLATE64,
    12: Codec.BZIP2,
    14: Codec.LZMA,  # after peeling the ZIP LZMA header (see _open_codec_member)
    93: Codec.ZSTD,
    98: Codec.PPMD,  # after peeling the ZIP PPMd8 header
}

# Local name/extra lengths are uint16; 65535 is the format maximum, so a separate
# cap cannot fire (S1-F2). Absurd *offsets* are this bound, same discipline as
# the native parsers.
_MAX_DATA_OFFSET = 1 << 40

# stdlib exposes no public decoder for a raw LZMA1 property blob → filter dict; zipfile and
# the 7z reader rely on the same private helper.
_raw_decode_filter_properties = getattr(lzma, "_decode_filter_properties", None)
if _raw_decode_filter_properties is None:  # pragma: no cover
    raise ImportError(
        "This Python's `lzma` module no longer exposes `_decode_filter_properties`, which "
        "archivey needs to decode ZIP LZMA (method 14) member properties. "
        "Please report this to archivey (with your Python version)."
    )
_decode_filter_properties: Callable[[int, bytes], dict] = _raw_decode_filter_properties

# ZIP create-system values whose entries use "\" as a path separator (DOS/Windows family).
# For these, a stored backslash is a separator; for Unix/other entries it is a literal
# filename character (see the minimal-name-normalization change / archive-data-model spec).
_BACKSLASH_SEPARATOR_SYSTEMS: frozenset[CreateSystem] = frozenset(
    {
        CreateSystem.FAT,
        CreateSystem.OS2_HPFS,
        CreateSystem.WINDOWS_NTFS,
        CreateSystem.VFAT,
    }
)
# ZIP create-system values whose `external_attr` low word is a Win32 DOS attribute word
# (FILE_ATTRIBUTE_*). Deliberately a sibling of _BACKSLASH_SEPARATOR_SYSTEMS rather than
# the same object: the two answer different questions about the same family and are free
# to diverge — a creator could spell paths the DOS way without recording DOS attributes.
_DOS_ATTRIBUTE_SYSTEMS: frozenset[CreateSystem] = frozenset(
    {
        CreateSystem.FAT,
        CreateSystem.OS2_HPFS,
        CreateSystem.WINDOWS_NTFS,
        CreateSystem.VFAT,
    }
)
# Hosts whose stored creation time is a birth time. The same four as
# _DOS_ATTRIBUTE_SYSTEMS today, kept apart on purpose: a host added there for its
# attribute or separator rules is not a birth-time host until someone says so, since
# its creation time would otherwise flow into ``created``.
_ZIP_BIRTH_TIME_HOSTS: frozenset[CreateSystem] = frozenset(
    {
        CreateSystem.FAT,
        CreateSystem.OS2_HPFS,
        CreateSystem.WINDOWS_NTFS,
        CreateSystem.VFAT,
    }
)
_CREATE_SYSTEM_BY_VALUE: dict[int, CreateSystem] = {
    member.value: member for member in CreateSystem
}


# stdlib zipfile's wording when its handle is gone; archivey's own guards raise the same
# message so the two are indistinguishable to a caller.
_CLOSED_ARCHIVE_MESSAGE = "Attempt to use ZIP archive that was already closed"


def _closed_archive_error() -> ArchiveyUsageError:
    """The handle under a live reader was closed — a lifecycle fault, not damage.

    Deliberately not a ``CorruptionError``: the archive bytes are fine, so reporting
    damage sends the caller hunting a bad file. This is the underlying-handle variant of
    using a reader after ``close()``, which already raises ``ArchiveyUsageError``.
    """
    return ArchiveyUsageError(_CLOSED_ARCHIVE_MESSAGE)


# Raw exceptions a ZIP member open can raise before the codec layer takes over (local
# header parse, raw payload view, decrypt-stage header, codec open) that
# _translate_exception maps to typed ArchiveyErrors. Declared once so those catch sites
# cannot drift apart.
_ZIP_MEMBER_READ_ERRORS: tuple[type[Exception], ...] = (
    zipfile.BadZipFile,
    RuntimeError,
    io.UnsupportedOperation,
    NotImplementedError,
    zlib.error,
    lzma.LZMAError,
    UnicodeDecodeError,
    ValueError,
    OSError,
)


def _decode_with_fallback(data: bytes) -> str:
    for encoding in _ZIP_ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise AssertionError("unreachable: cp437 decodes every byte")


_T = TypeVar("_T")

# Methods whose decoder rejects random input (the confirm ladder's codec rung, for
# ZipCrypto and WinZip AES alike): a wrong key dies inside the plaintext prefix, so
# confirmation stops there rather than walk a large member to its CRC or HMAC. Derived
# from REJECTING_CODECS, which the tests re-measure; STORED and PPMd are not in it and
# walk to the CRC or HMAC. The compressed-input half of the budget needs no cap of its
# own here: DEFLATE, Deflate64, LZMA and Zstandard are stream codecs, and bzip2
# produces output after one block, whose compressed size its format bounds.
_ZIP_REJECTING_METHODS = frozenset(
    method for method, codec in _ZIP_METHOD_CODECS.items() if codec in REJECTING_CODECS
)


def _is_candidate_integrity_failure(
    exc: BaseException, *, payload_complete: Callable[[], bool]
) -> bool:
    """Whether ``exc`` can be caused by a wrong key that passed the cheap check.

    A wrong key decrypts to garbage, which the codec rejects or the CRC check at EOF
    catches (``CorruptionError``). A decoder that runs out of garbage before the
    declared size says ``TruncatedError`` too, so that counts when the member's whole
    payload is in the file (``payload_complete()``, asked only then: it costs a seek);
    otherwise the file really is short.
    The local header is not encrypted, so a damaged one is never the key's doing: it
    raises before decryption starts.
    """
    if isinstance(exc, TruncatedError):
        return payload_complete()
    return isinstance(exc, CorruptionError)


#: A lone ZipCrypto password passed the one-byte check, and the data then failed its
#: integrity check. Either the password is wrong (one in 256 wrong ones pass that byte)
#: or the member is damaged; nothing in the archive can tell the two apart.
_UNCONFIRMED_PASSWORD_FAILURE = (
    "The data failed its integrity check with this password; the password may be "
    "wrong (ZipCrypto checks only one byte of it before decrypting), or the encrypted "
    "member may be corrupt"
)


# Set on the ``EncryptionError`` that says the password may be wrong *or* the member
# corrupt, so a caller inside the reader can tell it from "no correct password".
_UNVERIFIED_DATA_MARK = "_archivey_zip_unverified_data"


def _unverified_data_error(message: str) -> EncryptionError:
    """The ``EncryptionError`` for data that failed integrity under an unconfirmed password.

    A plain ``EncryptionError`` to the caller, carrying a mark instead of a subclass
    (the same reasoning as ``wrong_password_error``). The symlink hook reads the mark,
    so it does not report a damaged target as a missing password.
    """
    error = EncryptionError(message)
    setattr(error, _UNVERIFIED_DATA_MARK, True)
    return error


def _is_unverified_data_error(error: BaseException) -> bool:
    return getattr(error, _UNVERIFIED_DATA_MARK, False) is True


class _UnconfirmedZipCryptoStream(DelegatingStream):
    """A ZipCrypto member opened with one password that only its check byte vouched for.

    A wrong password shows up here as a CRC mismatch at the end of the member, or a
    decoder error part way into a compressed one. Both would otherwise read as a
    damaged archive, so they are reported the way the multi-password path reports the
    same ambiguity: as an ``EncryptionError`` that names both causes. The inner stream
    is the decoded member with its CRC verifier, so both arrive as ``ArchiveyError``
    (see :func:`_is_candidate_integrity_failure`).
    """

    def __init__(
        self, inner: BinaryIO, *, payload_complete: Callable[[], bool]
    ) -> None:
        self._payload_complete = payload_complete
        super().__init__(inner)

    def read(self, n: int = -1, /) -> bytes:
        try:
            return super().read(n)
        except Exception as exc:  # noqa: BLE001 - classified, then re-raised
            self._reraise(exc)

    def readinto(self, b: WriteableBuffer, /) -> int:
        # Overridden too, so the zero-copy passthrough stays on and still translates.
        try:
            return super().readinto(b)
        except Exception as exc:  # noqa: BLE001 - classified, then re-raised
            self._reraise(exc)

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        # A forward seek decrypts and decodes what it skips.
        try:
            return super().seek(offset, whence)
        except Exception as exc:  # noqa: BLE001 - classified, then re-raised
            self._reraise(exc)

    def _reraise(self, exc: Exception) -> NoReturn:
        if _is_candidate_integrity_failure(
            exc, payload_complete=self._payload_complete
        ):
            raise _unverified_data_error(_UNCONFIRMED_PASSWORD_FAILURE) from exc
        raise exc


def _zip_timestamps(
    info: zipfile.ZipInfo,
) -> tuple[
    datetime | None,
    datetime | None,
    datetime | None,
    datetime | None,
    list[TimestampIssue],
]:
    """Return ``(modified, accessed, ntfs_ctime, ut_ctime, issues)`` for a member.

    Sources, lowest to highest precedence (each layer overrides only the times it
    actually carries):

    1. The DOS ``date_time``: a 2-second-granularity local wall-clock modification time
       (naive ``datetime``; ``None`` for the "no timestamp" sentinel year 1980).
    2. An NTFS extra field (0x000A): three 64-bit FILETIMEs (modification, access,
       creation) in 100 ns UTC ticks since 1601; zero means "not set". Written by
       Windows tools (e.g. 7-Zip).
    3. An Extended Timestamp extra field (0x5455): real Unix timestamps, its flags byte
       signaling which of modification/access/"creation" follow (in that order), each a
       signed 32-bit Unix time interpreted as UTC. The central directory typically
       carries only the modification time even when the flags advertise more.

    The two "creation" times come back raw, apart from each other: ``ntfs_ctime``
    from the NTFS field and ``ut_ctime`` from the Extended Timestamp's third time.
    Whether either is a birth time depends on the writer, not the field, so the caller
    decides (``_zip_created``).
    """
    issues: list[TimestampIssue] = []
    if info.date_time == (1980, 0, 0, 0, 0, 0):
        modified: datetime | None = None
    else:
        try:
            modified = datetime(*info.date_time)
        except ValueError:
            issues.append(
                TimestampIssue(
                    field="date_time",
                    source="dos",
                    value_repr=repr(info.date_time),
                    message=(
                        f"Invalid ZIP date_time for {quoted(info.filename)}: "
                        f"{info.date_time!r}"
                    ),
                )
            )
            modified = None
    accessed: datetime | None = None
    ntfs_ctime: datetime | None = None
    ut_ctime: datetime | None = None

    # One scan collecting both timestamp extra fields, applied afterwards in precedence
    # order (NTFS below Extended Timestamp) regardless of their order in the blob.
    # Empty extra is the common case (zipfile.writestr / many tools) — skip the scan.
    ntfs_field: bytes | None = None
    ut_field: bytes | None = None
    extra = info.extra or b""
    if not extra:
        return modified, accessed, ntfs_ctime, ut_ctime, issues
    pos = 0
    while pos + 4 <= len(extra):
        tag, length = struct.unpack("<HH", extra[pos : pos + 4])
        field = extra[pos + 4 : pos + 4 + length]
        if tag == 0x000A and ntfs_field is None:
            ntfs_field = field
        elif tag == 0x5455 and field and ut_field is None:
            ut_field = field
        pos += 4 + length

    if ntfs_field is not None:
        # Layout: 4 reserved bytes, then (tag, size) attributes; tag 1 carries the three
        # FILETIMEs. Malformed/short fields are skipped rather than failing the listing.
        cursor = 4
        while cursor + 4 <= len(ntfs_field):
            attr_tag, attr_size = struct.unpack_from("<HH", ntfs_field, cursor)
            cursor += 4
            if (
                attr_tag == 0x0001
                and attr_size >= 24
                and cursor + 24 <= len(ntfs_field)
            ):
                mtime, atime, ctime = struct.unpack_from("<QQQ", ntfs_field, cursor)
                for value, field_name in (
                    (mtime, "mtime"),
                    (atime, "atime"),
                    (ctime, "ctime"),
                ):
                    dt, issue = filetime_to_datetime(
                        value, info.filename, field=field_name
                    )
                    if issue is not None:
                        issues.append(issue)
                    if dt is None:
                        continue
                    if field_name == "mtime":
                        modified = dt
                    elif field_name == "atime":
                        accessed = dt
                    else:
                        ntfs_ctime = dt
                break
            cursor += attr_size

    if ut_field is not None:
        flags = ut_field[0]
        cursor = 1
        for bit, ut_name in ((0x01, "mtime"), (0x02, "atime"), (0x04, "ctime")):
            if flags & bit and cursor + 4 <= len(ut_field):
                ts = int.from_bytes(
                    ut_field[cursor : cursor + 4], "little", signed=True
                )
                cursor += 4
                # A signed field: a pre-1970 date is legitimate, and unix_to_datetime
                # reads it the same on every platform.
                when = unix_to_datetime(ts)
                if when is None:
                    issues.append(
                        TimestampIssue(
                            field=ut_name,
                            source="extended",
                            value_repr=repr(ts),
                            message=(
                                f"Invalid ZIP extended timestamp for "
                                f"{quoted(info.filename)}: {ts!r}"
                            ),
                        )
                    )
                    continue
                if bit == 0x01:
                    modified = when
                elif bit == 0x02:
                    accessed = when
                else:
                    ut_ctime = when

    return modified, accessed, ntfs_ctime, ut_ctime, issues


def _zip_created(
    create_system: CreateSystem,
    ntfs_ctime: datetime | None,
    ut_ctime: datetime | None,
) -> tuple[datetime | None, datetime | None]:
    """Split a member's stored creation time into ``(created, ctime)``.

    The writer's host decides what the time means, not the field that carries it: on
    Linux and macOS, 7-Zip and p7zip fill the NTFS creation FILETIME from st_ctime and
    libarchive fills the Extended Timestamp's third time from it; on Windows the same
    writers store the birth time. A FAT, OS/2, NTFS or VFAT host stores a birth time.
    Any other host, unknown included, has its time reported as ``ctime``
    and ``created`` left None, as RAR does for an unknown ``host_os``. Measured per writer
    and OS in dev-docs/investigations/writer-timestamp-slots.md.

    One measured writer loses a birth time this way: libarchive on Windows stamps host
    3 but stores the birth time. It lands in ``ctime``, so ``created`` can
    miss a birth time but never holds st_ctime.

    The Extended Timestamp wins when both are present, the same precedence
    ``_zip_timestamps`` gives it for the other times.
    """
    stored = ut_ctime if ut_ctime is not None else ntfs_ctime
    if create_system in _ZIP_BIRTH_TIME_HOSTS:
        return stored, None
    return None, stored


def _is_windows_reparse_point(
    info: zipfile.ZipInfo, create_system: CreateSystem
) -> bool:
    """True when this entry is flagged as a Windows reparse point (handbook §2.2.1).

    The bit lives in the low (DOS attribute) word of ``external_attr``, and only a
    DOS/Windows creator puts a Win32 attribute word there. Every other creator writes
    whatever its own platform records — a Unix creator's authority is the mode in the
    high word — so bit ``0x400`` outside :data:`_DOS_ATTRIBUTE_SYSTEMS` is somebody
    else's bit and is not read.

    "Flagged as" is the whole claim: the bit says the entry was a reparse point on the
    source filesystem, not that the archive carries the reparse buffer or that the tag
    named a link. What the data turns out to be decides that, in
    ``BaseArchiveReader._apply_reparse_data``.
    """
    return create_system in _DOS_ATTRIBUTE_SYSTEMS and bool(
        info.external_attr & FILE_ATTRIBUTE_REPARSE_POINT
    )


class ZipReader(BaseArchiveReader):
    """Reads a ZIP archive via stdlib ``zipfile``."""

    _SUPPORTS_RANDOM_ACCESS = True
    _MEMBER_LIST_UPFRONT = True

    def __init__(
        self,
        source: ArchiveSource,
        streaming: bool,
        passwords: _PasswordCandidates | None,
        encoding: str | None,
        archive_name: str | None,
        config: ArchiveyConfig,
        collector: DiagnosticCollector | None = None,
        member_streams: MemberStreams = MemberStreams(0),
        open_site: OpenSite | None = None,
        start_offset: int = 0,
    ) -> None:
        super().__init__(
            ArchiveFormat.ZIP,
            streaming,
            archive_name,
            config,
            collector=collector,
            member_streams=member_streams,
            open_site=open_site,
        )
        self._source = source
        self._passwords = passwords or _PasswordCandidates()
        self._encoding = encoding
        self._stream_config = stream_config_from_archivey(
            self._config,
            streaming=streaming,
            seekable=MemberStreams.SEEKABLE in member_streams,
        )
        # No reader-level handle lock: member data never goes through ZipFile.open /
        # ZipExtFile. Every member reads through a SharedView under zipfile's own
        # ZipFile._lock, one read at a time, so independent members still decode in
        # parallel; closing the archive takes the same lock (_close_archive).
        # >1 when the source is a joined volume set. 7-Zip's ``-v`` on a ZIP is a raw
        # byte split, so the join is an ordinary ZIP and the count is reported rather
        # than acted on. The source states it, so no wrapper between the boundary and
        # here can hide it.
        self._volume_count: int = source.volume_count

        if not source.seekable():
            raise StreamNotSeekableError(
                "ZIP archives cannot be read from a non-seekable source: the central "
                "directory lives at the end of the file.",
                archive_name=archive_name,
            )

        # A split-set segment stdlib zipfile cannot read. A joined set arrives here
        # still carrying part one's name, so the name alone does not decide it: what
        # is refused is a segment that was *not* rejoined. Numbered ``.zip.NNN``
        # incompleteness is ``TruncatedError`` in ``open_archive``; this backstop
        # is Info-ZIP's spanned ``.zNN`` (and any ``.zip.NNN`` that skipped that
        # refuse).
        if self._volume_count == 1 and is_zip_split_segment_name(archive_name):
            raise UnsupportedFeatureError(
                ZIP_MULTI_VOLUME_MSG,
                archive_name=archive_name,
                source_format=ArchiveFormat.ZIP,
            )

        # A plain file goes to zipfile as its path: zipfile opens and closes its own
        # handle, and reads it with no archivey frame in between. That is safe for the
        # bound too: zipfile sizes its central-directory read from the end record's own
        # position and refuses a directory that would start before offset 0, so no
        # declared size can exceed the file. Measurement and a start offset need a
        # handle archivey controls, so those read through the source.
        zip_source: Path | BinaryIO = source
        if source.path is not None and not (self._measure or start_offset):
            zip_source = source.path
        if self._measure or start_offset:
            # zipfile never closes a file object it was handed; the reader closes the
            # source, and the counter and slice over it close nothing of the caller's.
            handle = self._track_source_seeks(source)
            if start_offset:
                # stdlib zipfile finds the central directory from the tail and
                # self-adjusts past a stub on its own, but "adjust past whatever
                # precedes the EOCD" is not the same promise as "the archive starts
                # here": a stub carrying its own EOCD-shaped bytes would move the
                # answer. Slicing makes the payload the whole world, which is what
                # start_offset means.
                handle = SlicingStream(handle, start=start_offset)
            zip_source = handle

        try:
            # `metadata_encoding` (3.11+) decodes names stored without the UTF-8 flag with
            # the caller's encoding instead of the cp437 default (UTF-8-flagged names are
            # unaffected). Reading the central directory here decodes every member name.
            self._archive: zipfile.ZipFile = zipfile.ZipFile(
                zip_source, "r", metadata_encoding=encoding
            )
        except zipfile.BadZipFile as exc:
            if _looks_like_multivolume(exc):
                raise UnsupportedFeatureError(
                    ZIP_MULTI_VOLUME_MSG,
                    archive_name=archive_name,
                    source_format=ArchiveFormat.ZIP,
                ) from exc
            if _central_directory_looks_encrypted(
                source if isinstance(zip_source, Path) else zip_source
            ):
                raise UnsupportedFeatureError(
                    f"{_STRONG_ENCRYPTION_MSG}; this archive's central directory "
                    "appears to be encrypted with it",
                    archive_name=archive_name,
                    source_format=ArchiveFormat.ZIP,
                ) from exc
            # stdlib often says "File is not a zip file" for truncated/corrupt
            # archives that already matched ZIP magic — prefer actionable prose.
            detail = str(exc).strip() or exc.__class__.__name__
            if "not a zip file" in detail.lower():
                detail = "truncated or corrupt ZIP (central directory unreadable)"
            raise CorruptionError(
                f"Could not open ZIP archive: {detail}",
                archive_name=archive_name,
                source_format=ArchiveFormat.ZIP,
            ) from exc
        except UnicodeDecodeError as exc:
            # A member name failed to decode while reading the central directory: either a
            # UTF-8-flagged entry whose stored bytes are corrupt, or a wrong explicit
            # `encoding=`. Both are surfaced as a typed error (never a raw UnicodeDecodeError);
            # the message points at the encoding when the caller supplied one.
            hint = (
                f" (with encoding={encoding!r}; the stored bytes may use a different encoding)"
                if encoding is not None
                else ""
            )
            raise CorruptionError(
                f"Could not decode a ZIP member name{hint}: {exc!r}",
                archive_name=archive_name,
                source_format=ArchiveFormat.ZIP,
            ) from exc
        except NotImplementedError as exc:
            # zipfile rejects an unsupported "version needed to extract" (e.g. a mutated
            # "zip file version 8.4") while reading the central directory. Recognized but
            # unhandled -> UnsupportedFeatureError, not a raw NotImplementedError.
            raise UnsupportedFeatureError(
                f"Unsupported ZIP version or feature: {exc!r}",
                archive_name=archive_name,
                source_format=ArchiveFormat.ZIP,
            ) from exc

        # Stdlib parses EOCD disk fields and never checks them. Info-ZIP's final
        # ``.zip`` part of a split set carries this_disk / cd_start_disk = last volume
        # index while still listing cleanly — refuse here before a convincing listing
        # turns a later local-header miss into CorruptionError.
        fp = self._archive.fp
        if fp is not None and _classic_eocd_declares_split(fp):
            self._archive.close()
            raise UnsupportedFeatureError(
                ZIP_MULTI_VOLUME_MSG,
                archive_name=archive_name,
                source_format=ArchiveFormat.ZIP,
            )

    def _translate_exception(self, exc: Exception) -> ArchiveyError | None:
        if isinstance(exc, zipfile.BadZipFile):
            return CorruptionError(f"Error reading ZIP archive: {exc!r}")
        if isinstance(exc, RuntimeError):
            text = str(exc).lower()
            if "password required" in text:
                return EncryptionError("Password required to read this ZIP member")
            if "bad password" in text:
                return wrong_password_error("Wrong password for this ZIP member")
        if isinstance(exc, io.UnsupportedOperation) and "seek" in str(exc):
            return StreamNotSeekableError("ZIP archives require a seekable source")
        if isinstance(exc, NotImplementedError):
            # zipfile raises NotImplementedError for a compress_type / flag combination it
            # cannot decode — an unsupported *method* ("compression method 99"), but also a
            # corrupt entry whose mutated flags select an unimplemented mode ("compressed
            # patched data (flag bit 5)"). Either way the member is unreadable here.
            return UnsupportedFeatureError(f"Unsupported ZIP entry feature: {exc!r}")
        if isinstance(exc, (zlib.error, lzma.LZMAError)):
            # Corruption inside a member body: stdlib zipfile surfaces the codec's own error
            # (zlib.error "invalid distance too far back", lzma.LZMAError "Corrupt input
            # data") rather than BadZipFile for a deflate/bzip2/LZMA member.
            return CorruptionError(f"Error decompressing ZIP member: {exc!r}")
        if isinstance(exc, ValueError):
            # A corrupt local-header offset makes stdlib zipfile seek to a bad position
            # ("negative seek value -N") before reading the member. That is archive
            # corruption, surfaced as a typed error rather than a raw ValueError.
            # The closed-handle ValueError is *not* corruption and is carved out ahead of
            # this arm, in _reraise_member_error (it cannot be returned from here:
            # ArchiveyUsageError is deliberately not an ArchiveyError).
            return CorruptionError(f"Corrupt ZIP member offset/structure: {exc!r}")
        if isinstance(exc, OSError) and str(exc) == _BZIP2_INVALID_DATA:
            # The stdlib bz2 decompressor signals a corrupt bzip2 member body as
            # OSError("Invalid data stream") (a bz2 quirk). Message-scoped so a genuine I/O
            # OSError still propagates unchanged (error-handling: I/O is not reclassified).
            return CorruptionError(f"Corrupt bzip2 ZIP member: {exc!r}")
        if isinstance(exc, UnicodeDecodeError):
            # zipfile re-reads and re-decodes the member name from the local file header
            # when opening a member; a corrupt local header with non-UTF-8 name bytes
            # raises this. It is a bad-archive signal, not a caller/runtime error.
            return CorruptionError(f"Corrupt ZIP entry name in local header: {exc!r}")
        if isinstance(exc, EOFError):
            # Short input, from any decoder a member read reaches. The codec layer maps
            # its own EOFError and no ZIP path is known to raise a bare one now; this
            # translator sees every exception a member stream raises, so a new source
            # still reads as truncation rather than escaping untyped.
            return TruncatedError(f"Truncated ZIP member data: {exc!r}")
        return None

    def _iter_members(self) -> Iterator[ArchiveMember]:
        # The position is passed down because it is the id `_register_member` stamps,
        # so a diagnostic raised while typing can name the member before it has an id.
        for index, info in enumerate(self._archive.infolist()):
            yield self._to_member(info, index)

    def _sniff_unflagged_name(
        self, raw_name: bytes, cp437_decoded: str
    ) -> tuple[str, str | None]:
        """Decode an unflagged ZIP name (no explicit ``encoding=``): prefer valid UTF-8, else
        the configured legacy fallback (default cp437).

        Returns ``(name, inferred_encoding)`` where ``inferred_encoding`` is the encoding used
        only when it overrode the cp437 APPNOTE default (for the diagnostic), else ``None``.
        UTF-8 is self-validating, so a clean decode is strong evidence the bytes are UTF-8;
        legacy bytes that are coincidentally valid UTF-8 are the documented residual risk.
        Pure ASCII (and any other bytes that decode identically under UTF-8 and cp437) is
        not an override — both encodings agree, so no diagnostic.
        """
        try:
            utf8_decoded = raw_name.decode("utf-8")
        except UnicodeDecodeError:
            fallback = self._config.zip_unflagged_fallback_encoding
            if fallback.lower().replace("-", "").replace("_", "") in {
                "cp437",
                "437",
                "ibm437",
            }:
                return cp437_decoded, None
            try:
                return raw_name.decode(fallback, errors="surrogateescape"), fallback
            except LookupError:
                # An unknown fallback encoding name: keep the cp437 decode rather than fail.
                return cp437_decoded, None
        if utf8_decoded == cp437_decoded:
            return utf8_decoded, None
        return utf8_decoded, "utf-8"

    def _to_member(self, info: zipfile.ZipInfo, index: int) -> ArchiveMember:
        full_mode = info.external_attr >> 16
        is_unix = info.create_system == 3
        # Permission bits only; None when no usable Unix mode was stored.
        mode = (
            stat.S_IMODE(full_mode) if (info.external_attr != 0 and is_unix) else None
        )

        create_system = _CREATE_SYSTEM_BY_VALUE.get(
            info.create_system, CreateSystem.UNKNOWN
        )

        # A Windows reparse point (symlink or junction) is marked by a DOS attribute
        # bit in the low word, and 7-Zip's `-snl` is the only common writer that sets
        # it. Checked before is_dir(): a directory reparse point carries both bits and
        # is a link, not a directory — its trailing "/" is then dropped by
        # normalize_member_name, which is how 7z already presents the same member.
        # The type is provisional: the data decides, in `_apply_reparse_data`, whether
        # the entry really holds a link buffer, and a member that does not goes back to
        # the type below.
        is_reparse_point = _is_windows_reparse_point(info, create_system)

        if info.is_dir():
            fallback_type = MemberType.DIRECTORY
        elif is_unix and stat.S_ISLNK(full_mode):
            fallback_type = MemberType.SYMLINK
        else:
            fallback_type = MemberType.FILE
        member_type = MemberType.SYMLINK if is_reparse_point else fallback_type
        # Convert "\" to "/" only for DOS/Windows-origin entries (where it is a separator);
        # a Unix (or other) entry keeps a backslash as a literal filename character.
        backslash_is_separator = create_system in _BACKSLASH_SEPARATOR_SYSTEMS

        # Use orig_filename, not filename: stdlib zipfile rewrites filename in a
        # platform-dependent way (it replaces os.sep -> "/" on Windows and truncates at a
        # null byte), whereas orig_filename is the raw decoded name, identical on every OS.
        # Archivey's own backslash_is_separator / extraction checks are the single authority.
        decoded = info.orig_filename
        is_utf8_flagged = bool(info.flag_bits & 0x800)
        # raw_name recovers the stored bytes by re-encoding the SAME source as name
        # (decoded == orig_filename) with the codec zipfile decoded with: UTF-8 when the
        # entry's UTF-8 flag is set, else the caller's metadata encoding (when given) or
        # zipfile's cp437 default. Using orig_filename keeps name and raw_name consistent.
        raw_name = decoded.encode(
            "utf-8" if is_utf8_flagged else (self._encoding or "cp437"),
            errors="surrogateescape",
        )
        # Many tools write UTF-8 names without setting the UTF-8 flag (APPNOTE says cp437),
        # so cp437 would yield mojibake. With no authoritative signal (flag clear AND no
        # explicit encoding=), prefer UTF-8 when the stored bytes are valid UTF-8, else a
        # configurable legacy fallback. A set flag or explicit encoding= is honored as-is.
        # ASCII bytes decode identically under UTF-8 and cp437 — skip the sniff.
        name_source = decoded
        inferred_encoding: str | None = None
        if not is_utf8_flagged and self._encoding is None and not raw_name.isascii():
            name_source, inferred_encoding = self._sniff_unflagged_name(
                raw_name, decoded
            )
        name = normalize_member_name(
            name_source, member_type, backslash_is_separator=backslash_is_separator
        )

        aes_info = (
            parse_winzip_aes_extra(info.extra) if info.compress_type == 99 else None
        )
        if aes_info is not None:
            # Method 99 is a wrapper; surface the underlying compression algorithm.
            algo = _ZIP_COMPRESSION_ALGOS.get(
                aes_info.actual_method, CompressionAlgorithm.UNKNOWN
            )
            compression: tuple[CompressionMethod, ...] = (CompressionMethod(algo=algo),)
        else:
            compression = _ZIP_COMPRESSION_TUPLES.get(
                info.compress_type,
                (CompressionMethod(algo=CompressionAlgorithm.UNKNOWN),),
            )

        modified, accessed, ntfs_ctime, ut_ctime, ts_issues = _zip_timestamps(info)
        created, ctime = _zip_created(create_system, ntfs_ctime, ut_ctime)
        # Surface the central-directory CRC-32 as a stored digest (archive-data-model:
        # HashAlgorithm.CRC32 → 4 big-endian bytes), so a dedupe pass can key on it
        # without decompressing (VISION "hashes without decompression"). Only for FILE and
        # SYMLINK members, which have data: a directory's stored CRC is a meaningless 0.
        # AE-2 stores CRC as 0 and relies on the HMAC — do not surface a fake crc32.
        hashes: dict[HashAlgorithm, bytes] = {}
        if member_type in (MemberType.FILE, MemberType.SYMLINK):
            if aes_info is None or not aes_info.is_ae2:
                hashes = {HashAlgorithm.CRC32: crc32_digest(info.CRC)}
        extra = MemberExtra({"zip.compress_type": info.compress_type})
        if is_reparse_point:
            # From the attribute bit alone, so it is known while listing and stays true
            # even when the data turns out not to be a link buffer and the member is
            # re-typed. `is_junction` needs the tag inside that data, and is set later.
            extra[EXTRA_IS_REPARSE_POINT] = True
        if aes_info is not None:
            extra["zip.aes_vendor_version"] = aes_info.vendor_version
            extra["zip.aes_strength"] = aes_info.strength
            extra["zip.aes_actual_method"] = aes_info.actual_method
        # Skip defaulted None/False kwargs on the listing hot path (perf review L2).
        member = ArchiveMember(
            type=member_type,
            name=name,
            raw_name=raw_name,
            size=info.file_size,
            compressed_size=info.compress_size,
            compression=compression,
            hashes=hashes,
            extra=extra,
            _raw=info,  # carry the ZipInfo so _open_member needs no name/id lookup table
        )
        if modified is not None:
            member.modified = modified
        if accessed is not None:
            member.accessed = accessed
        if created is not None:
            member.created = created
        if ctime is not None:
            member.ctime = ctime
        if mode is not None:
            member.mode = mode
        if info.flag_bits & _ZIP_MASK_ENCRYPTED:
            member.is_encrypted = True
        if info.comment:
            member.comment = _decode_with_fallback(info.comment)
        if create_system is not None:
            member.create_system = create_system
        # Each report below names the member by its position in the walk, because
        # registration has not stamped `_member_id` yet and stamps that same position.
        if inferred_encoding is not None:
            self._diagnostics_collector.emit(
                code=DiagnosticCode.MEMBER_NAME_ENCODING_INFERRED,
                message=(
                    f"ZIP member name decoded as {inferred_encoding!r} rather than the "
                    f"cp437 default (UTF-8 flag not set): {quoted(member.name)}"
                ),
                context=NameEncodingContext(
                    archive_name=self._archive_name,
                    member_name=member.name,
                    member_id=index,
                    raw_name_base64=raw_name_to_base64(member.raw_name),
                    inferred_encoding=inferred_encoding,
                    declared_encoding="cp437",
                ),
                member=member,
                attach_to_member=True,
                logger=logger,
            )
        emit_member_name_normalized(
            self._diagnostics_collector,
            member=member,
            presented_name=decoded,
            archive_name=self._archive_name,
            member_id=index,
            # A directory reparse point is stored with the directory convention's
            # trailing "/" and is still a link, so normalization drops the slash. Only
            # this backend knows that, so only this backend says so.
            link_stored_as_directory=is_reparse_point and info.is_dir(),
        )
        if is_reparse_point and info.file_size == 0:
            # A writer that stores no data for a reparse point has recorded no target
            # for it, and that is knowable from the header alone — no read, and so no
            # dependence on this being a seekable pass. Deciding it here rather than in
            # the link-target hook is what makes streaming agree: that hook runs at EOF,
            # after extraction has already decided what to do with the member, which
            # left a 7-Zip junction raising instead of taking the recorded outcome.
            self._apply_reparse_data(
                member, b"", fallback_type=fallback_type, member_id=index
            )
        for issue in ts_issues:
            self._diagnostics_collector.emit(
                code=DiagnosticCode.MEMBER_TIMESTAMP_INVALID,
                message=issue.message,
                context=MemberTimestampContext(
                    archive_name=self._archive_name,
                    member_name=member.name,
                    member_id=index,
                    field=issue.field,
                    source=issue.source,
                    value_repr=issue.value_repr,
                ),
                member=member,
                attach_to_member=True,
                logger=logger,
            )
        return member

    def _zipcrypto_check_byte(self, info: zipfile.ZipInfo) -> int:
        if info.flag_bits & _ZIP_MASK_USE_DATA_DESCRIPTOR:
            # zipfile stores the DOS time in the private ``_raw_time`` attribute and uses
            # its high byte as the ZipCrypto check byte when a data descriptor is present.
            # Fail LOUD if a future Python drops the attribute: a silent 0 fallback would
            # make every candidate fail the 1-byte check, misreporting correct passwords
            # as wrong for data-descriptor members (same policy as the loud import-time
            # bind of lzma._decode_filter_properties in the 7z reader).
            raw_time = getattr(info, "_raw_time", None)
            if raw_time is None:
                raise RuntimeError(
                    "This Python's `zipfile` no longer exposes `ZipInfo._raw_time`, "
                    "which archivey needs to verify ZipCrypto passwords for "
                    "data-descriptor members. Please report this to archivey "
                    "(with your Python version)."
                )
            return (int(raw_time) >> 8) & 0xFF
        return (info.CRC >> 24) & 0xFF

    def _zipfile_lock(self) -> AbstractContextManager[object]:
        # stdlib ZipFile serializes fp access via a private lock; typeshed omits it.
        return getattr(self._archive, "_lock")

    @contextmanager
    def _ciphertext_body_stream(
        self,
        info: zipfile.ZipInfo,
    ) -> Iterator[BinaryIO]:
        """Yield a :class:`SlicingStream` over the ZipCrypto ciphertext body.

        The view starts after the 12-byte encryption header and covers the rest of the
        member's compressed payload. Held under ``ZipFile``'s lock with the archive
        position restored on exit; never buffers the whole member.
        """
        zf = self._archive
        header_len = 12
        body_len = max(0, info.compress_size - header_len)

        with self._zipfile_lock():
            fp = zf.fp
            if fp is None:
                raise _closed_archive_error()
            saved = fp.tell()
            try:
                fp.seek(info.header_offset)
                fheader = read_exact(fp, 30)
                if len(fheader) != 30 or fheader[:4] != b"PK\x03\x04":
                    raise zipfile.BadZipFile("Bad magic number for file header")
                name_len, extra_len = struct.unpack_from("<HH", fheader, 26)
                body_start = info.header_offset + 30 + name_len + extra_len + header_len
                # typeshed types ZipFile.fp as IO[bytes], not BinaryIO; it is the
                # binary file ZipFile read its directory from.
                yield SlicingStream(
                    cast("BinaryIO", fp), start=body_start, length=body_len
                )
            finally:
                fp.seek(saved)

    def _read_zipcrypto_header(self, info: zipfile.ZipInfo) -> bytes:
        """Return the 12-byte ZipCrypto header ciphertext for ``info``."""
        zf = self._archive
        with self._zipfile_lock():
            fp = zf.fp
            if fp is None:
                raise _closed_archive_error()
            saved = fp.tell()
            try:
                fp.seek(info.header_offset)
                fheader = read_exact(fp, 30)
                if len(fheader) != 30 or fheader[:4] != b"PK\x03\x04":
                    raise zipfile.BadZipFile("Bad magic number for file header")
                name_len, extra_len = struct.unpack_from("<HH", fheader, 26)
                read_exact(fp, name_len + extra_len)
                header = read_exact(fp, 12)
                if len(header) != 12:
                    # Same short-header case ZipFile.open surfaces as IndexError.
                    raise TruncatedError(
                        "Truncated ZipCrypto header",
                        archive_name=self._archive_name,
                        source_format=ArchiveFormat.ZIP,
                    )
                return header
            finally:
                fp.seek(saved)

    def _local_data_region(self, info: zipfile.ZipInfo) -> tuple[int, int]:
        """Return ``(data_start, compress_size)`` for ``info`` from its local file header.

        Parses only the fixed 30-byte local header plus the local name/extra lengths
        (central-directory extra can differ). Rejects truncated/bad magic headers,
        a local name that disagrees with the CDH, and a data offset past
        ``_MAX_DATA_OFFSET``. Name/extra lengths are uint16; 65535 is legal, so they
        are not capped separately.
        """
        zf = self._archive
        with self._zipfile_lock():
            fp = zf.fp
            if fp is None:
                raise _closed_archive_error()
            saved = fp.tell()
            try:
                fp.seek(info.header_offset)
                fheader = read_exact(fp, 30)
                if len(fheader) != 30 or fheader[:4] != b"PK\x03\x04":
                    raise zipfile.BadZipFile("Bad magic number for file header")
                name_len, extra_len = struct.unpack_from("<HH", fheader, 26)
                # General-purpose flag bit 11: UTF-8 filename (APPNOTE).
                gp_flags = struct.unpack_from("<H", fheader, 6)[0]
                local_name = read_exact(fp, name_len)
                if len(local_name) != name_len:
                    raise zipfile.BadZipFile("Truncated file header")
                if gp_flags & 0x800:
                    fname_str = local_name.decode("utf-8")
                else:
                    fname_str = local_name.decode(self._encoding or "cp437")
                if fname_str != info.orig_filename:
                    raise zipfile.BadZipFile(
                        "File name in directory %r and header %r differ."
                        % (info.orig_filename, local_name)
                    )
                data_start = info.header_offset + 30 + name_len + extra_len
                if data_start < 0 or data_start > _MAX_DATA_OFFSET:
                    raise zipfile.BadZipFile(
                        f"Absurd local-header data offset: {data_start}"
                    )
                # Mirror stdlib zipfile's overlap guard (ZipFile.open): a member whose
                # compressed payload extends past the next entry's start is a zip bomb.
                end_offset = getattr(info, "_end_offset", None)
                if (
                    end_offset is not None
                    and data_start + max(0, info.compress_size) > end_offset
                ):
                    raise zipfile.BadZipFile(
                        f"Overlapped entries: {info.orig_filename!r} (possible zip bomb)"
                    )
                return data_start, max(0, info.compress_size)
            finally:
                fp.seek(saved)

    def _raw_member_stream(self, info: zipfile.ZipInfo) -> BinaryIO:
        """Locked :class:`SharedView` over the member's raw compressed payload."""
        data_start, length = self._local_data_region(info)
        fp = self._archive.fp
        if fp is None:
            raise _closed_archive_error()

        def _check_open() -> None:
            if self._archive.fp is None:
                raise _closed_archive_error()

        # typeshed types ZipFile.fp as IO[bytes], not BinaryIO (as in
        # _ciphertext_body_stream).
        return SharedView(
            cast("BinaryIO", fp),
            start=data_start,
            length=length,
            lock=self._zipfile_lock(),
            check_open=_check_open,
        )

    def _zip_lzma_params(self, raw: BinaryIO) -> CodecParams:
        """Peel the ZIP method-14 LZMA header and return RAW LZMA1 :class:`CodecParams`."""
        # version (2) + properties size (2) + properties
        header = read_exact(raw, 4)
        if len(header) != 4:
            raise TruncatedError(
                "Truncated ZIP LZMA header",
                archive_name=self._archive_name,
                source_format=ArchiveFormat.ZIP,
            )
        props_size = struct.unpack_from("<H", header, 2)[0]
        if props_size > 256:
            raise CorruptionError(
                f"Absurd ZIP LZMA properties size: {props_size}",
                archive_name=self._archive_name,
                source_format=ArchiveFormat.ZIP,
            )
        props = read_exact(raw, props_size)
        if len(props) != props_size:
            raise TruncatedError(
                "Truncated ZIP LZMA properties",
                archive_name=self._archive_name,
                source_format=ArchiveFormat.ZIP,
            )
        filters = [_decode_filter_properties(lzma.FILTER_LZMA1, props)]
        return CodecParams(filters=filters)

    def _zip_ppmd_params(self, raw: BinaryIO) -> CodecParams:
        """Peel the ZIP method-98 2-byte PPMd8 header into :class:`CodecParams`."""
        header = read_exact(raw, 2)
        if len(header) != 2:
            raise TruncatedError(
                "Truncated ZIP PPMd header",
                archive_name=self._archive_name,
                source_format=ArchiveFormat.ZIP,
            )
        word = struct.unpack("<H", header)[0]
        order = (word & 0xF) + 1
        mem_mb = ((word >> 4) & 0xFF) + 1
        restore = (word >> 12) & 0xF
        if order < 2 or order > 64 or mem_mb < 1:
            raise CorruptionError(
                f"Invalid ZIP PPMd header parameters: order={order} mem_mb={mem_mb}",
                archive_name=self._archive_name,
                source_format=ArchiveFormat.ZIP,
            )
        return CodecParams(
            ppmd_order=order,
            ppmd_mem_size=mem_mb * 1024 * 1024,
            ppmd_restore_method=restore,
        )

    def _member_codec(
        self, method: int, member_name: str, *, suffix: str = ""
    ) -> Codec:
        codec = _ZIP_METHOD_CODECS.get(method)
        if codec is None:
            raise UnsupportedFeatureError(
                f"Unsupported ZIP compression method {method}{suffix}",
                archive_name=self._archive_name,
                member_name=member_name,
                source_format=ArchiveFormat.ZIP,
            )
        return codec

    def _decode_body(
        self,
        info: zipfile.ZipInfo,
        member: ArchiveMember | None,
        body: BinaryIO,
        *,
        method: int,
        codec: Codec,
        member_name: str,
        sequential_body: bool = False,
    ) -> ArchiveStream:
        """Decode a member body through the shared codec layer.

        ``body`` is the member's data as the codec sees it: the raw payload of an
        unencrypted member, or the plaintext out of a decrypt stage. Every member,
        encrypted or not, decodes here. Failures raise as ``ArchiveyError``.

        ``sequential_body`` marks a body that seeks only by re-reading from its start
        (the ZipCrypto stage). ``AUTO`` accelerators then stay off: they read their
        input at scattered offsets, and every step back would decrypt the member again.
        """
        size = member.size if member is not None else info.file_size
        config = replace(self._stream_config, expected_decompressed_size=size)
        if sequential_body:
            config = replace(
                config,
                use_rapidgzip=_sequential_accelerator(config.use_rapidgzip),
                use_indexed_bzip2=_sequential_accelerator(config.use_indexed_bzip2),
            )
        try:
            params = CodecParams()
            if method == 14:  # ZIP LZMA
                params = self._zip_lzma_params(body)
            elif method == 98:  # ZIP PPMd8
                params = self._zip_ppmd_params(body)
                # Bound PPMd decode to the member size when known (defensive; PPMd8
                # usually has an end mark, but max_length still matches py7zr practice).
                if size is not None and size >= 0:
                    params = replace(params, unpack_size=size)
            return open_codec_stream(
                codec,
                body,
                config=config,
                params=params,
                seekable=self._stream_config.seekable,
                collector=self._diagnostics_collector,
            )
        except BaseException as exc:
            body.close()
            if isinstance(exc, _ZIP_MEMBER_READ_ERRORS):
                self._reraise_member_error(exc, member_name)
            raise

    def _verified_member_stream(
        self,
        decoded: BinaryIO,
        info: zipfile.ZipInfo,
        member: ArchiveMember | None,
        member_name: str,
    ) -> ArchiveStream:
        """The public member stream, with the stored CRC and size fused in."""
        hashes: Mapping[HashAlgorithm, bytes] = (
            member.hashes if member is not None else {}
        )
        size = member.size if member is not None else info.file_size
        if hashes or size is not None:
            return self._wrap_member_stream(
                decoded,
                member_name,
                size=size,
                expected_hashes=hashes,
                expected_size=size,
                verify_member=member,
            )
        return self._wrap_member_stream(decoded, member_name, size=size)

    def _open_codec_member(
        self,
        info: zipfile.ZipInfo,
        member: ArchiveMember | None,
        *,
        member_name: str,
    ) -> ArchiveStream:
        """Decode an unencrypted ZIP member through the shared codec layer."""
        codec = self._member_codec(info.compress_type, member_name)
        decoded = self._decode_body(
            info,
            member,
            self._open_raw_payload(info, member_name),
            method=info.compress_type,
            codec=codec,
            member_name=member_name,
        )
        return self._verified_member_stream(decoded, info, member, member_name)

    def _payload_is_complete(self, info: zipfile.ZipInfo, member_name: str) -> bool:
        """Whether the file holds every byte of ``info``'s declared payload."""
        try:
            data_start, length = self._local_data_region(info)
        except _ZIP_MEMBER_READ_ERRORS as exc:
            self._reraise_member_error(exc, member_name)
        with self._zipfile_lock():
            fp = self._archive.fp
            if fp is None:
                raise _closed_archive_error()
            saved = fp.tell()
            try:
                return data_start + length <= fp.seek(0, io.SEEK_END)
            finally:
                fp.seek(saved)

    def _open_raw_payload(self, info: zipfile.ZipInfo, member_name: str) -> BinaryIO:
        """:meth:`_raw_member_stream`, with a damaged local header raised translated."""
        try:
            return self._raw_member_stream(info)
        except _ZIP_MEMBER_READ_ERRORS as exc:
            self._reraise_member_error(exc, member_name)

    def _zipcrypto_stage(
        self, info: zipfile.ZipInfo, member_name: str
    ) -> Callable[[bytes], BinaryIO]:
        """Return ``stage(password)``: the member's plaintext under ZipCrypto.

        ``stage`` checks the header's check byte (the cheap key check, 2⁻⁸) and raises
        the wrong-password ``EncryptionError`` when it does not match.
        """
        check_byte = self._zipcrypto_check_byte(info)

        def stage(password: bytes) -> BinaryIO:
            raw = self._open_raw_payload(info, member_name)
            try:
                header = read_exact(raw, ZIPCRYPTO_HEADER_LEN)
            except BaseException as exc:
                raw.close()
                if isinstance(exc, _ZIP_MEMBER_READ_ERRORS):
                    self._reraise_member_error(exc, member_name)
                raise
            if len(header) != ZIPCRYPTO_HEADER_LEN:
                raw.close()
                truncated = TruncatedError(
                    "Truncated ZipCrypto header",
                    archive_name=self._archive_name,
                    source_format=ArchiveFormat.ZIP,
                )
                self._stamp_error_context(truncated, member_name)
                raise truncated
            keys, check = keys_after_header(password, header)
            if check != check_byte:
                raw.close()
                raise wrong_password_error("Wrong password for this ZIP member")
            return ZipCryptoDecryptStream(
                raw, keys, length=max(0, info.compress_size - ZIPCRYPTO_HEADER_LEN)
            )

        return stage

    def _winzip_aes_stage(
        self, info: zipfile.ZipInfo, aes: WinZipAesInfo, member_name: str
    ) -> Callable[[bytes], BinaryIO]:
        """Return ``stage(password)``: the member's plaintext under WinZip AES.

        ``stage`` checks the two-byte ``pw_verify`` (the cheap key check, 2⁻¹⁶); the
        HMAC runs when a read reaches the end of the ciphertext.
        """

        def stage(password: bytes) -> BinaryIO:
            raw = self._open_raw_payload(info, member_name)
            try:
                return open_winzip_aes_member(
                    raw,
                    aes=aes,
                    password=password,
                    compress_size=info.compress_size,
                )
            except BaseException:
                raw.close()
                raise

        return stage

    def _open_encrypted_member(
        self,
        info: zipfile.ZipInfo,
        member: ArchiveMember | None,
        *,
        member_name: str,
    ) -> ArchiveStream:
        """Open a ZipCrypto or WinZip AES member: decrypt stage, then the codec layer.

        Both schemes share one password ladder (``password_confirm``). Their cheap key
        checks (ZipCrypto's check byte, 2⁻⁸; WinZip AES's ``pw_verify``, 2⁻¹⁶) admit
        some wrong passwords, so:

        - **One possible password:** accept it on the cheap check. The CRC (or HMAC)
          at EOF is the real test, and a stream abandoned before it reports
          ``ENCRYPTED_MEMBER_UNVERIFIED``.
        - **Several, STORED ZipCrypto:** nothing but the whole-member CRC can tell
          them apart; one shared ciphertext pass decides (:meth:`_open_stored_confirmed`).
        - **Several, otherwise:** each candidate runs a bounded confirm: the CRC when
          it is in reach, codec rejection for a codec that rejects random input, and
          for WinZip AES the HMAC, which covers the whole member.
        """
        crc_anchor: int | None = info.CRC
        hmac_anchor = False
        if info.compress_type == 99:
            aes = parse_winzip_aes_extra(info.extra)
            if aes is None:
                raise UnsupportedFeatureError(
                    "ZIP compression method 99 without a valid WinZip AES (0x9901) "
                    "extra field",
                    archive_name=self._archive_name,
                    member_name=member_name,
                    source_format=ArchiveFormat.ZIP,
                )
            method = aes.actual_method
            codec = self._member_codec(method, member_name, suffix=" under WinZip AES")
            stage = self._winzip_aes_stage(info, aes, member_name)
            hmac_anchor = True
            if aes.is_ae2:
                # AE-2 stores no CRC (the field is zero); the HMAC is the only check.
                crc_anchor = None
        else:
            method = info.compress_type
            codec = self._member_codec(method, member_name)
            stage = self._zipcrypto_stage(info, member_name)

        def decode_body(body: BinaryIO) -> ArchiveStream:
            return self._decode_body(
                info,
                member,
                body,
                method=method,
                codec=codec,
                member_name=member_name,
                sequential_body=not hmac_anchor,
            )

        def payload_complete() -> bool:
            return self._payload_is_complete(info, member_name)

        if not self._passwords.is_ambiguous():
            return self._open_encrypted_unconfirmed(
                info,
                member,
                member_name,
                stage,
                decode_body,
                zipcrypto=not hmac_anchor,
                payload_complete=payload_complete,
            )
        if not hmac_anchor and method == zipfile.ZIP_STORED:
            winner = self._open_stored_confirmed(info, member, member_name=member_name)
            return self._verified_member_stream(
                decode_body(stage(winner)), info, member, member_name
            )
        return self._open_encrypted_confirmed(
            info,
            member,
            member_name,
            stage,
            decode_body,
            payload_complete=payload_complete,
            method=method,
            crc_anchor=crc_anchor,
            hmac_anchor=hmac_anchor,
        )

    def _open_encrypted_unconfirmed(
        self,
        info: zipfile.ZipInfo,
        member: ArchiveMember | None,
        member_name: str,
        stage: Callable[[bytes], BinaryIO],
        decode_body: Callable[[BinaryIO], ArchiveStream],
        *,
        zipcrypto: bool,
        payload_complete: Callable[[], bool],
    ) -> ArchiveStream:
        """Open with the one possible password, accepted on its cheap key check."""
        if not zipcrypto:
            try:
                decoded: BinaryIO = self._finish_password_attempt(
                    member,
                    member_name,
                    lambda password: decode_body(stage(password)),
                    ambiguous_holder=None,
                )
            except _ZIP_MEMBER_READ_ERRORS as exc:
                self._reraise_member_error(exc, member_name)
            decoded = self._watch_unverified(
                decoded,
                info,
                member,
                member_name,
                check="weak_open_check",
                seek_forfeits=False,
            )
            return self._verified_member_stream(decoded, info, member, member_name)

        size = member.size if member is not None else info.file_size
        hashes: Mapping[HashAlgorithm, bytes] = (
            member.hashes if member is not None else {}
        )

        def decrypt(password: bytes) -> BinaryIO:
            body = stage(password)
            try:
                decoded = decode_body(body)
            except ArchiveyError as exc:
                # The LZMA or PPMd header decrypted to nonsense: the same ambiguity
                # as a failure further in.
                if not _is_candidate_integrity_failure(
                    exc, payload_complete=payload_complete
                ):
                    raise
                raise _unverified_data_error(_UNCONFIRMED_PASSWORD_FAILURE) from exc
            # The verifier sits inside the translation, so a CRC mismatch is reported
            # as the ambiguity it is rather than as plain corruption.
            return _UnconfirmedZipCryptoStream(
                VerifyingStream(
                    decoded,
                    hashes,
                    expected_size=size,
                    collector=self._diagnostics_collector,
                    member=member,
                    archive_name=self._archive_name,
                ),
                payload_complete=payload_complete,
            )

        stream = self._finish_password_attempt(
            member, member_name, decrypt, ambiguous_holder=None
        )
        # Only the check byte vouched for this password; the CRC at EOF is the check.
        stream = self._watch_unverified(
            stream,
            info,
            member,
            member_name,
            check="weak_open_check",
            seek_forfeits=True,
        )
        return self._wrap_member_stream(stream, member_name, size=size)

    def _open_encrypted_confirmed(
        self,
        info: zipfile.ZipInfo,
        member: ArchiveMember | None,
        member_name: str,
        stage: Callable[[bytes], BinaryIO],
        decode_body: Callable[[BinaryIO], ArchiveStream],
        *,
        payload_complete: Callable[[], bool],
        method: int,
        crc_anchor: int | None,
        hmac_anchor: bool,
    ) -> ArchiveStream:
        """Pick among several possible passwords with a bounded confirm per candidate."""
        # The central directory's size, which the plan trusts as zipfile always did;
        # the fused verifier on the caller's stream checks it at EOF.
        size = info.file_size
        codec_rejects = method in _ZIP_REJECTING_METHODS
        # One substream, the member itself: its CRC is the anchor when the member fits
        # the prefix, and codec rejection decides a larger one.
        plan = plan_password_confirm(
            [(size, crc_anchor)],
            None,
            budget=PASSWORD_CONFIRM_PREFIX_BYTES,
            codec_rejects=codec_rejects,
        )
        # WinZip AES authenticates the whole ciphertext, so reading a member to its end
        # confirms the password (an 80-bit HMAC) whether or not it has a CRC. Take that
        # walk wherever a CRC walk would be taken: the member fits the budget, or its
        # codec cannot reject a wrong key on its own.
        reads_to_hmac = (
            hmac_anchor
            and not plan.confirms
            and (size <= PASSWORD_CONFIRM_PREFIX_BYTES or not codec_rejects)
        )
        if reads_to_hmac:
            plan = PasswordConfirmPlan(
                ((size, crc_anchor),),
                None,
                confirms=True,
                bounded=size <= PASSWORD_CONFIRM_PREFIX_BYTES,
            )
        ambiguous_holder: list[EncryptionError] = []

        def candidate_failed(cause: Exception | None) -> EncryptionError:
            failure = EncryptionError(
                "Password candidate failed integrity validation for this ZIP member"
            )
            if not ambiguous_holder:
                ambiguous_holder.append(failure)
            if cause is not None:
                failure.__cause__ = cause
            return failure

        def decrypt(password: bytes) -> tuple[ArchiveStream, PasswordConfirmVerdict]:
            # A damaged local header, a short encryption header and a failed cheap
            # check all raise from the stage, before anything below can mistake them
            # for a wrong key's garbage.
            body = stage(password)
            probe: ArchiveStream | None = None
            try:
                probe = decode_body(body)
                verdict = run_password_confirm_plan(probe, plan)
                if verdict is not PasswordConfirmVerdict.REJECTED and reads_to_hmac:
                    # The read that finds the end is the one that checks the HMAC.
                    if probe.read(1):
                        verdict = PasswordConfirmVerdict.REJECTED
            except ArchiveyError as exc:
                if not _is_candidate_integrity_failure(
                    exc, payload_complete=payload_complete
                ):
                    raise
                raise candidate_failed(exc) from exc
            finally:
                if probe is not None:
                    probe.close()
            if verdict is PasswordConfirmVerdict.REJECTED:
                raise candidate_failed(None)
            # Fresh stream for the caller: nothing decoded here is handed out.
            return decode_body(stage(password)), verdict

        def promote_candidate_password(
            accepted: tuple[ArchiveStream, PasswordConfirmVerdict],
        ) -> bool:
            # Decides whether the accepted candidate password joins known-good. This
            # path runs only for an ambiguous candidate set, so a survivor with no
            # confirming anchor stays out.
            _, verdict = accepted
            return verdict is PasswordConfirmVerdict.CONFIRMED

        decoded, verdict = self._finish_password_attempt(
            member,
            member_name,
            decrypt,
            ambiguous_holder=ambiguous_holder,
            promote=promote_candidate_password,
        )
        stream: BinaryIO = decoded
        if verdict is not PasswordConfirmVerdict.CONFIRMED:
            stream = self._watch_unverified(
                decoded,
                info,
                member,
                member_name,
                check="confirm_budget_exhausted",
                seek_forfeits=not hmac_anchor,
            )
        return self._verified_member_stream(stream, info, member, member_name)

    def _reraise_member_error(self, exc: Exception, member_name: str) -> NoReturn:
        """Translate a raw member-read error, stamp it with member context, and raise.

        Thin wrapper over the shared base boundary: an ``EncryptionError`` is raised
        without member stamping (it carries its own message and must not be
        reclassified). Shared by the member-open and compressed-confirm decrypt paths
        so their translate/stamp/raise tail stays identical.

        The closed-handle ``ValueError`` is intercepted here rather than in
        ``_translate_exception``, which can only return an ``ArchiveyError``; a lifecycle
        fault is deliberately not one.
        """
        if isinstance(exc, ValueError) and _CLOSED_ARCHIVE_MESSAGE in str(exc):
            raise _closed_archive_error() from exc
        self._raise_translated(exc, member_name, stamp_encryption=False)

    def _open_stored_confirmed(
        self,
        info: zipfile.ZipInfo,
        member: ArchiveMember | None,
        *,
        member_name: str,
    ) -> bytes:
        """Resolve the password for a STORED ZipCrypto member, in four phases.

        A STORED member has no decompressor to reject a wrong key, and ZipCrypto's 1-byte
        header check admits ~1/256 of wrong passwords, so a full CRC pass over the member's
        plaintext is the only way to disambiguate. To keep that pass single and bounded:

        1. **Collect survivors** — run every static candidate through the cheap 1-byte
           check; keep the ~1/256 that pass.
        2. **Disambiguate** — one shared ciphertext pass computes every survivor's plaintext
           CRC-32 in constant memory; the first CRC match wins.
        3. **Provider fallback** — if no static candidate won, ask the provider one password
           at a time (cheap check, then a per-candidate CRC pass), until one wins or it stops.
        4. **Resolve outcome** — return the winner, which the caller opens fresh;
           otherwise raise the most specific error (integrity-ambiguous /
           password-required / wrong-password).
        """
        ambiguous_failure: EncryptionError | None = None
        check_byte = self._zipcrypto_check_byte(info)
        expected_crc = info.CRC & 0xFFFFFFFF

        with self._translated_errors(member_name):
            header = self._read_zipcrypto_header(info)

        def weak_ok(password: bytes) -> bool:
            return password_matches_check_byte(password, header, check_byte)

        def disambiguate(survivors: list[bytes]) -> bytes | None:
            nonlocal ambiguous_failure
            if not survivors:
                return None
            # No decompressor to reject garbage: one shared ciphertext pass computes
            # every survivor's plaintext CRC-32 in constant memory.
            with self._ciphertext_body_stream(info) as body:
                crcs = parallel_plaintext_crc32(survivors, header, body)
            winner = first_crc_match(expected_crc, crcs)
            if winner is None:
                failure = EncryptionError(
                    "Password candidate failed integrity validation for this ZIP member"
                )
                if ambiguous_failure is None:
                    ambiguous_failure = failure
            return winner

        # Phase 1 — collect the static candidates that pass the cheap 1-byte check.
        tried: set[bytes] = set()
        survivors: list[bytes] = []
        for password in self._passwords.iter_candidates():
            tried.add(password)
            if weak_ok(password):
                survivors.append(password)

        # Phase 2 — one shared CRC pass over the survivors.
        winner = disambiguate(survivors)

        # Phase 3 — provider fallback: ask, cheap-check, per-candidate CRC pass, repeat.
        attempt = 1
        while winner is None and self._passwords.has_provider():
            try:
                password = self._passwords.ask_provider(member, attempt)
            except EncryptionError as exc:
                self._stamp_error_context(exc, member_name)
                raise
            if password is None:
                break
            if password in tried:
                break
            tried.add(password)
            if weak_ok(password):
                winner = disambiguate([password])
            attempt += 1

        # Phase 4 — resolve the outcome (winner, else the most specific error).
        if winner is not None:
            self._passwords.record_success(winner)
            return winner

        if ambiguous_failure is not None:
            ambiguous = _unverified_data_error(
                "No password candidate produced integrity-verified data for "
                "this ZIP member; the password(s) may be wrong, or "
                "the encrypted member may be corrupt"
            )
            self._stamp_error_context(ambiguous, member_name)
            raise ambiguous from ambiguous_failure

        # A provider that exists but returned None without yielding a candidate
        # must not read as "wrong password" (cli-product P7 / #131 D8 residue).
        if not tried:
            required = EncryptionError("Password required to read this ZIP member")
            self._stamp_error_context(required, member_name)
            raise required
        wrong = wrong_password_error("Wrong password for this ZIP member")
        self._stamp_error_context(wrong, member_name)
        raise wrong

    def _watch_unverified(
        self,
        stream: BinaryIO,
        info: zipfile.ZipInfo,
        member: ArchiveMember | None,
        member_name: str,
        *,
        check: Literal["weak_open_check", "confirm_budget_exhausted"],
        seek_forfeits: bool,
    ) -> BinaryIO:
        """Report ``ENCRYPTED_MEMBER_UNVERIFIED`` if ``stream`` is abandoned before EOF.

        For a password that a check weaker than the member's digest accepted: a wrong
        ZipCrypto password that passes the check byte decrypts to readable garbage,
        and only the CRC at EOF notices. ``seek_forfeits`` is False for a WinZip AES
        member: its HMAC survives seeks, so only a read reaching the end counts.
        """

        def report(reason: str) -> None:
            missed = (
                "gave up its integrity check by seeking"
                if reason == "seek"
                else "was closed before its integrity check was reached"
            )
            self._diagnostics_collector.emit(
                code=DiagnosticCode.ENCRYPTED_MEMBER_UNVERIFIED,
                message=(
                    f"Encrypted ZIP member {quoted(member_name)} {missed}, and the "
                    f"password was accepted on a weaker check: the bytes read may have "
                    f"been decrypted with a wrong password."
                ),
                context=EncryptedVerificationContext(
                    archive_name=self._archive_name,
                    member_name=member_name,
                    member_id=member._member_id if member is not None else None,
                    check=check,
                    reason=reason,
                ),
                member=member,
                logger=integrity_logger,
            )

        return UnverifiedPasswordReadWatch(
            stream,
            size=info.file_size,
            on_unverified=report,
            seek_forfeits=seek_forfeits,
        )

    def _finish_password_attempt(
        self,
        member: ArchiveMember | None,
        member_name: str,
        decrypt: Callable[[bytes], _T],
        *,
        ambiguous_holder: list[EncryptionError] | None,
        promote: Callable[[_T], bool] | None = None,
    ) -> _T:
        try:
            return self._passwords.attempt(member, decrypt, promote=promote)
        except _PasswordCandidatesExhausted as exc:
            ambiguous_failure = ambiguous_holder[0] if ambiguous_holder else None
            if ambiguous_failure is not None:
                ambiguous = _unverified_data_error(
                    "No password candidate produced integrity-verified data for "
                    "this ZIP member; the password(s) may be wrong, or "
                    "the encrypted member may be corrupt"
                )
                self._stamp_error_context(ambiguous, member_name)
                raise ambiguous from ambiguous_failure
            if exc.last_error is not None:
                last_error = exc.last_error
                self._stamp_error_context(last_error, member_name)
                raise last_error from last_error.__cause__
            required = EncryptionError(raw_message_of(exc))
            self._stamp_error_context(required, member_name)
            raise required from None
        except EncryptionError as exc:
            self._stamp_error_context(exc, member_name)
            raise

    def _ensure_link_target(self, member: ArchiveMember) -> None:
        if member.type != MemberType.SYMLINK or member.link_target is not None:
            return
        info = member._raw
        assert isinstance(info, zipfile.ZipInfo), (
            "ZIP member is missing its ZipInfo handle"
        )
        if _uses_strong_encryption(info):
            # No password opens it here, so the target is out of reach, not missing.
            self._emit_link_target_unavailable(
                member,
                reason="target_data_encrypted",
                message=(
                    f"The symlink target of {quoted(member.name)} is encrypted with "
                    f"PKWARE Strong Encryption, which archivey does not support; "
                    f"leaving link_target unset."
                ),
                target_in_archive=True,
            )
            return
        # A Windows reparse point stores a REPARSE_DATA_BUFFER rather than a bare
        # path, and that buffer is where the junction tag lives. Decoding it as UTF-8
        # would report ~92 bytes of binary as this member's link target.
        create_system = _CREATE_SYSTEM_BY_VALUE.get(
            info.create_system, CreateSystem.UNKNOWN
        )
        is_reparse_point = _is_windows_reparse_point(info, create_system)
        # What the member would be if its data turns out not to be a link buffer —
        # the same test `_to_member` used before the reparse bit overrode it.
        fallback_type = MemberType.DIRECTORY if info.is_dir() else MemberType.FILE
        # The zero-data case does not appear here: `_to_member` settles it while the
        # member is being typed, so this hook is never reached for one.
        # A symlink's target is its (possibly encrypted) file data. Listing must stay
        # usable without a password, so a missing/wrong password, or data that fails
        # its check under an unconfirmed ZipCrypto password, leaves link_target
        # unset (following the link later fails with LinkTargetNotFoundError); other
        # errors surface translated like any member-read error.
        # The read is capped (`_read_link_target_data`): the data is compressed, so an
        # uncapped read let a few hundred KiB of archive decode to gigabytes here.
        try:
            data = self._read_link_target_data(
                member,
                lambda: self._open_member(member),
                is_reparse_point=is_reparse_point,
            )
            if data is None:
                return
            if is_reparse_point:
                self._apply_reparse_data(member, data, fallback_type=fallback_type)
            else:
                member.link_target = data.decode("utf-8", errors="surrogateescape")
        except EncryptionError as exc:
            if _is_unverified_data_error(exc):
                # Only ZipCrypto's check byte vouched for the password, and the data
                # then failed: a wrong password or a damaged member, and nothing here
                # can say which.
                reason = "password_or_damage"
                message = (
                    f"The symlink target of {quoted(member.name)} failed its integrity "
                    f"check; the password may be wrong or the member may be corrupt. "
                    f"Leaving link_target unset."
                )
            else:
                reason = "password_required"
                message = (
                    f"Cannot read the symlink target of {quoted(member.name)} without "
                    f"the correct password; leaving link_target unset."
                )
            self._emit_link_target_unavailable(
                member,
                reason=reason,
                message=message,
                # The archive does carry the target; it is locked, not missing. So this
                # member fails the way the encrypted file next to it does, rather than
                # disappearing from the output under a status that reads as success.
                target_in_archive=True,
            )

    def _open_member(self, member: ArchiveMember) -> ArchiveStream:
        # The member carries its own ZipInfo (`_raw`), so data access needs no name/id map
        # — and a duplicate member name can't resolve to the wrong entry.
        info = member._raw
        assert isinstance(info, zipfile.ZipInfo), (
            "ZIP member is missing its ZipInfo handle"
        )
        if _uses_strong_encryption(info):
            raise UnsupportedFeatureError(
                _STRONG_ENCRYPTION_MSG,
                archive_name=self._archive_name,
                member_name=member.name,
                source_format=ArchiveFormat.ZIP,
            )
        # Every member reads as raw payload -> decrypt stage (if encrypted) -> codec
        # layer -> fused CRC/size verify. Bit 0 is set on WinZip AES members too.
        if info.compress_type == 99 or info.flag_bits & _ZIP_MASK_ENCRYPTED:
            return self._open_encrypted_member(info, member, member_name=member.name)
        return self._open_codec_member(info, member, member_name=member.name)

    def _get_archive_info(self) -> ArchiveInfo:
        comment = self._archive.comment
        cost = CostReceipt(
            listing_cost=ListingCost.INDEXED,
            access_cost=AccessCost.DIRECT,
            stream_capability=StreamCapability.SEEKABLE,
            solid_block_count=None,
        )
        info_extra = ArchiveInfoExtra({"zip.volume_count": self._volume_count})
        return ArchiveInfo(
            format=ArchiveFormat.ZIP,
            format_version=None,
            is_solid=False,  # ZIP is never solid: each member has an independent offset
            member_count=len(self._archive.infolist()),
            comment=_decode_with_fallback(comment) if comment else None,
            is_encrypted=False,  # ZIP has per-member encryption, not header-level
            # True for a rejoined 7-Zip `.zip.NNN` set: it arrived as several files,
            # which is what a caller checking this wants to know. It says nothing
            # about the ZIP structure — the join is a plain single-disk archive.
            is_multivolume=self._volume_count > 1,
            cost=cost,
            extra=info_extra,
        )

    def _close_archive(self) -> None:
        # Wait out an in-flight member read before the handle closes; a later read then
        # fails its SharedView check_open with the closed-archive error.
        with self._zipfile_lock():
            self._archive.close()


def _find_classic_eocd(fp: IO[bytes]) -> tuple[bytes, int, int] | None:
    """Locate the classic end-of-central-directory record in ``fp``'s tail.

    Returns ``(tail, idx, file_size)``: the buffer read from the end of the file, the
    signature's index within it, and the file size (so the record's absolute position is
    ``file_size - len(tail) + idx``). ``None`` when the file is too short or holds no
    signature. ``fp``'s position is restored.

    Uses the same last-occurrence ``rfind`` for ``PK\\x05\\x06`` that stdlib
    ``zipfile._EndRecData`` does, so callers inspect the EOCD stdlib actually parsed: a
    decoy signature earlier in the file (or in the comment) cannot make the two disagree
    about which record is real. Callers bound-check the fields they unpack.
    """
    pos = fp.tell()
    try:
        fp.seek(0, io.SEEK_END)
        size = fp.tell()
        if size < 22:
            return None
        window = min(size, (1 << 16) + 22)
        fp.seek(size - window)
        tail = fp.read(window)
        idx = tail.rfind(b"PK\x05\x06")
        if idx < 0:
            return None
        return tail, idx, size
    finally:
        fp.seek(pos)


def _classic_eocd_declares_split(fp: IO[bytes]) -> bool:
    """True when the classic EOCD names a real non-zero disk.

    Reads only the two uint16 fields at EOCD+4/+6. ``0xFFFF`` is the ZIP64
    sentinel ("value lives in the ZIP64 EOCD"), not disk 65535 — skip it so a
    legitimate ZIP64 archive is not refused. ZIP64 multi-disk sets are already
    caught via the locator path (``_looks_like_multivolume``).
    """
    found = _find_classic_eocd(fp)
    if found is None:
        return False
    tail, idx, _size = found
    if idx + 8 > len(tail):
        return False
    this_disk, cd_start_disk = struct.unpack_from("<HH", tail, idx + 4)
    return _disk_field_is_split(this_disk) or _disk_field_is_split(cd_start_disk)


def _uses_strong_encryption(info: zipfile.ZipInfo) -> bool:
    """True when ``info`` is a PKWARE Strong Encryption member (APPNOTE §7)."""
    if not info.flag_bits & _ZIP_MASK_ENCRYPTED:
        return False
    if info.flag_bits & _ZIP_MASK_STRONG_ENCRYPTION:
        return True
    extra = info.extra or b""
    pos = 0
    while pos + 4 <= len(extra):
        tag, length = struct.unpack_from("<HH", extra, pos)
        if tag == _ZIP_EXTRA_STRONG_ENCRYPTION:
            return True
        pos += 4 + length
    return False


def _central_directory_looks_encrypted(fp: IO[bytes]) -> bool:
    """True when an archive extra data record sits where stdlib reads the central directory.

    PKWARE Strong Encryption can encrypt the central directory itself (general-purpose
    bit 13 on the local headers). stdlib then fails with a bad central-directory magic,
    which would read as corruption. Checked only after stdlib has refused the archive.
    It is best-effort: the record is written in front of an encrypted central directory,
    but nothing else here can tell encrypted bytes from damaged ones.

    Looks only where stdlib ``_RealGetContents`` reads: the EOCD position minus the
    recorded directory size. The offset the EOCD records is not consulted: it matches
    that position whenever it is right, and in a stub-prefixed archive with stale offsets
    it points into the stub, where four arbitrary bytes would turn damage into a false
    Strong Encryption report. Classic EOCD only: a ZIP64 archive stores ``0xFFFFFFFF``
    there and keeps the real size in the ZIP64 EOCD, which this does not read, so an
    encrypted ZIP64 central directory is reported as corruption.
    """
    found = _find_classic_eocd(fp)
    if found is None:
        return False
    tail, idx, size = found
    if idx + 16 > len(tail):
        return False
    (cd_size,) = struct.unpack_from("<I", tail, idx + 12)
    start_dir = size - len(tail) + idx - cd_size
    if not 0 <= start_dir <= size - 4:
        return False
    pos = fp.tell()
    try:
        fp.seek(start_dir)
        return fp.read(4) == _ZIP_ARCHIVE_EXTRA_DATA_SIG
    finally:
        fp.seek(pos)


def _sequential_accelerator(mode: AcceleratorMode) -> AcceleratorMode:
    """``AUTO`` off, for a codec input that is expensive to read out of order.

    ``ON`` stays on: the caller asked for the accelerator by name. Over a ZipCrypto
    stage its scattered reads each restart decryption from the member's start, so the
    cost grows with the square of the member size.
    """
    return AcceleratorMode.OFF if mode is AcceleratorMode.AUTO else mode


def _disk_field_is_split(value: int) -> bool:
    return value != 0 and value != _ZIP64_DISK_SENTINEL


def _looks_like_multivolume(exc: zipfile.BadZipFile) -> bool:
    text = str(exc).lower()
    return "multi" in text or "disk" in text or "spanned" in text or "split" in text


class ZipReadBackend(ReadBackend):
    """Backend factory for ZIP archives."""

    FORMATS: tuple[ArchiveFormat, ...] = (ArchiveFormat.ZIP,)
    EXTENSIONS: Mapping[str, ArchiveFormat] = {
        ".zip": ArchiveFormat.ZIP,
        ".jar": ArchiveFormat.ZIP,
        ".pyz": ArchiveFormat.ZIP,
        ".whl": ArchiveFormat.ZIP,
        ".apk": ArchiveFormat.ZIP,
        ".cbz": ArchiveFormat.ZIP,
    }
    MAGIC: tuple[MagicSignature, ...] = (
        MagicSignature(
            0, b"\x50\x4b\x03\x04", ArchiveFormat.ZIP
        ),  # standard local header
        MagicSignature(
            0, b"\x50\x4b\x05\x06", ArchiveFormat.ZIP
        ),  # empty archive (EOCD)
        MagicSignature(0, b"\x50\x4b\x07\x08", ArchiveFormat.ZIP),  # spanned marker
    )
    # Local header only. The other two ZIP magics are not useful search targets inside a
    # stub window: PK\x05\x06 would precede a local header only for an empty archive, and
    # PK\x07\x08 is the spanning marker (7-Zip's -sfx -v keeps the stub as a standalone
    # executable, not concatenated with the volumes). Neither carries fields
    # SFX_HIT_VALIDATOR can cheaply confirm — see formats/zip.md §2.1.
    SFX_MAGIC: tuple[MagicSignature, ...] = (
        MagicSignature(0, b"\x50\x4b\x03\x04", ArchiveFormat.ZIP),
    )
    SFX_HIT_VALIDATOR = staticmethod(validate_zip_local_header)
    # SUPPORTS_STREAMING_NON_SEEKABLE stays False: the central directory lives at EOF,
    # so even a forward-only pass needs a seekable source.
    SUPPORTS_PASSWORD = True  # per-member ZipCrypto/AES encryption
    USES_ENCODING = True  # zipfile metadata_encoding for non-UTF-8 names

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
    ) -> ZipReader:
        # `format` is always ZIP here (single-format backend); accepted for the uniform
        # ReadBackend signature.
        return ZipReader(
            source,
            streaming,
            passwords,
            encoding,
            archive_name,
            config,
            collector=collector,
            member_streams=member_streams,
            open_site=open_site,
            start_offset=start_offset,
        )


register_reader(ZipReadBackend)
