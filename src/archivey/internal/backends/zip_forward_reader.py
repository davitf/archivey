"""ZIP read front to back with no seek: a pipe, a socket, a remote object read once.

Layout this reader walks (``zip_local_walk``)::

    [ local file header · data · data descriptor? ] × n   # member by member
    [ central directory ] [ end records ]                  # after the last member

Each member is yielded with what its local header says, and its data is read through
the walk as the caller reads it. The fields only the central directory holds (mode,
host, comment, the types those decide) arrive after the last member, so the members
already yielded are updated in place then, before the pass ends; until then each has
``member_state_final`` false. ``openspec/changes/native-zip-reader/design.md``
§"Streaming: a forward walk over local headers" is the contract.

A seekable source never comes here: :class:`~archivey.internal.backends.zip_reader.ZipReader`
reads its directory first.
"""

from __future__ import annotations

import io
import zlib
from array import array
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import fields, replace
from typing import BinaryIO, cast

from archivey.cost import AccessCost, CostReceipt, ListingCost, StreamCapability
from archivey.diagnostics import DiagnosticCode, UnlistedDataContext
from archivey.exceptions import (
    ArchiveyError,
    CorruptionError,
    EncryptionError,
    TruncatedError,
    UnsupportedFeatureError,
)
from archivey.internal.backends.zip_aes import parse_winzip_aes_extra
from archivey.internal.backends.zip_local_walk import (
    DataDescriptor,
    LocalEntry,
    LocalHeaderWalk,
    MemberData,
)
from archivey.internal.backends.zip_parser import (
    CentralEntry,
    CommentCutShort,
    DirectorySizeMismatch,
    EndRecord,
    EntryCountMismatch,
)
from archivey.internal.backends.zip_reader import (
    _ZIP_COMPRESSION_ALGOS,
    _ZIP_COMPRESSION_TUPLES,
    _ZIP_MASK_COMPRESSED_PATCHED_DATA,
    _ZIP_MASK_LZMA_EOS_MARKER,
    ZipReader,
    _is_encrypted_entry,
    _max_members_error,
    _stored_text,
    _uses_strong_encryption,
    _zip_created,
    _zip_timestamps,
    _ZipLocator,
)
from archivey.internal.base_reader import MAX_LINK_TARGET_BYTES
from archivey.internal.file_copy_pass import DEFAULT_FILE_COPY_PASS, FileCopyPass
from archivey.internal.listing_limits import member_metadata_bytes
from archivey.internal.logs import backends as logger
from archivey.internal.naming import normalize_member_name
from archivey.internal.source import ArchiveSource
from archivey.internal.streams.archive_stream import ArchiveStream
from archivey.internal.streams.codecs import open_codec_stream
from archivey.internal.streams.decompressor_stream import DataAfterEndError
from archivey.internal.streams.streamtools import ReadableStream
from archivey.internal.streams.streamtools.base import ReadOnlyIOStream
from archivey.internal.trailing_scan import first_nonzero_offset
from archivey.terminal import quoted
from archivey.types import (
    ArchiveFormat,
    ArchiveInfo,
    ArchiveInfoExtra,
    ArchiveMember,
    CompressionAlgorithm,
    CompressionMethod,
    CreateSystem,
    HashAlgorithm,
    MemberExtra,
    MemberType,
    crc32_digest,
)

# Read size when the pass reads through data nobody asked for.
_DRAIN_CHUNK = 1 << 16

# A forward pass decides nothing by seeking back, and reads each member's data once.
_ZIP_FORWARD_COST = CostReceipt(
    listing_cost=ListingCost.REQUIRES_SCANNING,  # members are found by their headers
    access_cost=AccessCost.DIRECT,  # each member's data is independent
    stream_capability=StreamCapability.FORWARD_ONLY,
    solid_block_count=None,
)

# Fields the end of the pass leaves as they are: the reader's identity stamps, and
# what the base settles itself (``is_current`` when the walk ends, links when the
# listing resolves them).
_KEPT_ON_UPDATE = frozenset(
    {
        "_member_id",
        "_archive_id",
        "_diagnostics",
        "_listing_pending",
        "_link_target_unread",
        "is_current",
        "link_target_member",
    }
)


def _central_from_local(entry: LocalEntry) -> CentralEntry:
    """A central-entry view of what ``entry``'s local header says.

    The fields only the directory holds are zero: no host, no attributes, no comment.
    It feeds the name and time decoders, which read the same fields from either."""
    header = entry.header
    return CentralEntry(
        index=entry.index,
        version_made_by=0,
        version_needed=header.version_needed,
        flags=header.flags,
        method=header.method,
        dos_time=header.dos_time,
        dos_date=header.dos_date,
        crc=header.crc,
        compressed_size=header.compressed_size,
        file_size=header.file_size,
        header_offset=entry.offset,
        disk_start=0,
        internal_attr=0,
        external_attr=0,
        name=header.name,
        extra=header.extra,
        comment=b"",
    )


def _contradicts(descriptor: tuple[int, int, int], central: CentralEntry) -> bool:
    """Whether a data descriptor's CRC or sizes differ from the directory entry's.

    A 4-byte descriptor holds the size modulo 2**32 (its compressed size was matched
    in full)."""
    crc, compressed_size, file_size = descriptor
    return (
        crc != central.crc
        or compressed_size != central.compressed_size
        or file_size not in (central.file_size, central.file_size & 0xFFFF_FFFF)
    )


def _descriptor_error(
    member: ArchiveMember, descriptor: tuple[int, int, int], central: CentralEntry
) -> CorruptionError:
    crc, compressed_size, file_size = descriptor
    return CorruptionError(
        f"ZIP member {quoted(member.name)}: the data descriptor after its data "
        f"(CRC-32 {crc:08x}, {compressed_size} bytes stored, {file_size} bytes) "
        f"differs from its central directory entry (CRC-32 {central.crc:08x}, "
        f"{central.compressed_size} bytes stored, {central.file_size} bytes); the "
        "bytes the stream held for it are not the data the directory describes",
        member_name=member.name,
        source_format=ArchiveFormat.ZIP,
    )


def _renamed_error(local_name: bytes, central: CentralEntry) -> CorruptionError:
    return CorruptionError(
        f"File name in directory {quoted(_stored_text(central.name))} and header "
        f"{quoted(_stored_text(local_name))} differ.",
        source_format=ArchiveFormat.ZIP,
    )


class _PassData:
    """One member's data in a forward pass, read once, checked when it ends.

    The caller's stream and the pass itself (when it moves on to the next header) read
    through the same object, so every byte is decoded once. When the data ends, its
    CRC and size are checked against the local header, or for a bit-3 member against
    the data descriptor that follows, and a bit-3 member's sizes and CRC are filled in
    on the member.

    A member whose data could be a symlink's target (at most
    :data:`MAX_LINK_TARGET_BYTES` decoded bytes) keeps them: the directory, which says
    whether it is a link, comes after the data.
    """

    def __init__(
        self,
        reader: ZipForwardReader,
        entry: LocalEntry,
        member: ArchiveMember,
        data: MemberData,
    ) -> None:
        self._reader = reader
        self.entry = entry
        self.member = member
        self._data = data
        self._decoded: BinaryIO | None = None
        self._crc = 0
        self._size = 0
        self._capture: bytearray | None = bytearray()
        #: The data has ended and been checked, or the pass has moved past it.
        self.done = False
        #: The walk has moved on: the caller's stream reads nothing more.
        self.passed = False
        #: Set when the caller read up to the end and every check passed.
        self.verified = False

    @property
    def captured(self) -> bytes | None:
        """The member's whole decoded data, when it is short enough to be a link
        target and was read to its end; else ``None``."""
        if not self.verified or self._capture is None:
            return None
        return bytes(self._capture)

    def _deferred(self) -> bool:
        return self.entry.deferred

    def _open_decoded(self) -> BinaryIO:
        if self._decoded is not None:
            return self._decoded
        member = self.member
        header = self.entry.header
        reader = self._reader
        if _is_encrypted_entry(header.flags, header.method):
            raise UnsupportedFeatureError(
                "Reading an encrypted ZIP member from a source that cannot seek is "
                "not supported",
                archive_name=reader._archive_name,
                member_name=member.name,
                source_format=ArchiveFormat.ZIP,
            )
        if header.flags & _ZIP_MASK_COMPRESSED_PATCHED_DATA:
            raise UnsupportedFeatureError(
                "ZIP compressed patched data (general-purpose bit 5) is not supported",
                archive_name=reader._archive_name,
                member_name=member.name,
                source_format=ArchiveFormat.ZIP,
            )
        if header.method == 0:
            self._decoded = self._data
            return self._data
        codec = reader._member_codec(header.method, member.name)
        deferred = self._deferred()
        size = None if deferred else header.file_size
        loc = member._raw
        assert isinstance(loc, _ZipLocator)
        if (
            deferred
            and header.method == 14
            and not header.flags & _ZIP_MASK_LZMA_EOS_MARKER
        ):
            raise UnsupportedFeatureError(
                "A ZIP LZMA member with no end-of-stream marker and its sizes in a "
                "data descriptor cannot be read from a source that cannot seek: "
                "nothing marks where its data ends",
                archive_name=reader._archive_name,
                member_name=member.name,
                source_format=ArchiveFormat.ZIP,
            )
        # A sized member's data is its codec's input exactly, so a byte after the
        # codec's end is refused as from a file (DR-3). A bit-3 member's data runs on
        # into the descriptor, so there the same refusal is how its end is found.
        config = replace(
            reader._stream_config,
            expected_decompressed_size=size,
            refuse_input_after_end=True,
            seekable=False,
        )
        params = reader._member_codec_params(
            loc, self._data, method=header.method, size=size, keyed=False
        )
        if deferred and header.method == 98:
            # PPMd holds a sized member's input until it is all in; a bit-3 member
            # has no size to wait for, so it is fed in pieces, in a child process.
            params = replace(params, input_runs_on=True)
        self._decoded = open_codec_stream(
            codec,
            self._data,
            config=config,
            params=params,
            seekable=False,
            collector=reader._diagnostics_collector,
        )
        return self._decoded

    def read(self, n: int) -> bytes:
        """Up to ``n`` decoded bytes; ``b""`` once the data has ended and checked.

        The read that reaches the end raises ``CorruptionError`` when the data fails
        its CRC or size check."""
        chunk = self._read_chunk(n)
        if not chunk and not self.done:
            self._finish(raise_mismatch=True)
        return chunk

    def _read_chunk(self, n: int) -> bytes:
        """Up to ``n`` decoded bytes, or ``b""`` once the codec or the data ends."""
        if self.done:
            return b""
        decoded = self._open_decoded()
        try:
            chunk = decoded.read(n)
        except DataAfterEndError:
            if not self._deferred():
                raise
            # The codec ended and its input goes on: the descriptor is next.
            chunk = b""
        if chunk:
            self._crc = zlib.crc32(chunk, self._crc)
            self._size += len(chunk)
            capture = self._capture
            if capture is not None:
                if len(capture) + len(chunk) > MAX_LINK_TARGET_BYTES:
                    self._capture = None
                else:
                    capture += chunk
        return chunk

    def _finish(self, *, raise_mismatch: bool) -> None:
        """Settle the data and check what was read against the header or descriptor.

        A mismatch is the caller's to see when the caller read the data; data the
        pass read past on its own is not checked for the caller, as a skipped member
        is not in the seekable read, and only loses its kept bytes.
        """
        self.done = True
        self._close_decoded()
        header = self.entry.header
        data = self._data
        member = self.member
        if self._deferred():
            if header.method == 0:
                descriptor = data.settle()
            else:
                descriptor = data.settle(crc=self._crc, file_size=self._size)
            assert descriptor is not None
            self._reader._note_descriptor(self.entry, descriptor)
            crc, file_size = descriptor.crc, descriptor.file_size
            # The descriptor dates the end from its own position, so its compressed
            # size is the member's, past any byte the codec's layers read ahead.
            member.compressed_size = descriptor.compressed_size
            member.size = self._size
            if crc == self._crc and member.type is not MemberType.DIRECTORY:
                member.hashes = {HashAlgorithm.CRC32: crc32_digest(crc)}
        else:
            data.settle()
            crc, file_size = header.crc, header.file_size
        # A 4-byte descriptor holds the size modulo 2**32.
        size_ok = file_size == self._size or (
            self._deferred() and file_size == self._size & 0xFFFF_FFFF
        )
        error: CorruptionError | None = None
        if not size_ok:
            where = "data descriptor" if self._deferred() else "local header"
            error = CorruptionError(
                f"ZIP member {quoted(member.name)} decoded to {self._size} bytes; "
                f"its {where} says {file_size}"
            )
        elif crc != self._crc:
            error = CorruptionError(
                f"CRC-32 mismatch in ZIP member {quoted(member.name)}: stored "
                f"{crc:08x}, computed {self._crc:08x}"
            )
        if error is None:
            self.verified = True
        elif raise_mismatch:
            raise error

    def _close_decoded(self) -> None:
        decoded, self._decoded = self._decoded, None
        if decoded is not None and decoded is not self._data:
            decoded.close()

    def drain(self) -> None:
        """Read past what is left of the data, so the walk can go on.

        A member whose local header states its size is skipped without decoding,
        unless it is short enough to be a link target: the directory may yet make it
        one, and its target is these bytes. That decode is only for the target, so
        its failure loses the bytes and nothing else, as a member nobody reads costs
        nothing from a file. A bit-3 member is decoded to its end, which is the only
        way to find it, so there a failure ends the walk.
        """
        if self.done:
            return
        header = self.entry.header
        encrypted = _is_encrypted_entry(header.flags, header.method)
        deferred = self._deferred()
        if deferred:
            if encrypted and header.method != 0:
                # Only the decrypted codec finds the end of this one (3b3d).
                raise UnsupportedFeatureError(
                    "An encrypted ZIP member whose sizes are in a data descriptor "
                    "cannot be read past from a source that cannot seek: only "
                    "decrypting it finds where its data ends",
                    archive_name=self._reader._archive_name,
                    member_name=self.member.name,
                    source_format=ArchiveFormat.ZIP,
                )
            # Encrypted STORED data ends where its scan finds the descriptor, with no
            # key needed; anything else is decoded to find its end.
            skip = encrypted
        else:
            skip = encrypted or header.file_size > MAX_LINK_TARGET_BYTES
        if not skip:
            try:
                while self._read_chunk(_DRAIN_CHUNK):
                    pass
            except ArchiveyError:
                if deferred:
                    raise
                skip = True
        if skip:
            self.done = True
            self._capture = None
            self._close_decoded()
            descriptor = self._data.settle()
            if descriptor is not None:
                self._reader._note_descriptor(self.entry, descriptor)
            return
        if not self.done:
            self._finish(raise_mismatch=False)

    @property
    def uncaptured(self) -> str | None:
        """Why a drained member's bytes were not kept, when not for their length:
        ``"encrypted"`` or ``"damaged"`` (see ``ZipForwardReader._link_target_opener``).
        """
        header = self.entry.header
        if _is_encrypted_entry(header.flags, header.method):
            return "encrypted"
        if not self.verified and (
            self._deferred() or header.file_size <= MAX_LINK_TARGET_BYTES
        ):
            return "damaged"
        return None

    def abandon(self) -> None:
        """Release the decoder of a pass that will not read on."""
        self.done = True
        self._close_decoded()


class _PassStream(ReadOnlyIOStream):
    """The caller's view of the member a forward pass is at.

    Good until the walk moves on to the next header, as a streaming TAR member's is.
    """

    def __init__(self, data: _PassData) -> None:
        super().__init__()
        self._pass_data = data

    def read(self, n: int = -1, /) -> bytes:
        if self.closed:
            raise ValueError("read from a closed ZIP member stream")
        data = self._pass_data
        if data.passed:
            raise ValueError(
                "ZIP member data is no longer readable: the walk has moved on"
            )
        if n is None or n < 0:
            chunks = []
            while chunk := data.read(_DRAIN_CHUNK):
                chunks.append(chunk)
            return b"".join(chunks)
        return data.read(n)


class ZipForwardReader(ZipReader):
    """Reads a ZIP front to back through its local headers (``LocalHeaderWalk``).

    Only ``streaming=True`` reaches it, on a source that cannot seek.
    """

    # The member list exists only once the walk has passed every member.
    _MEMBER_LIST_UPFRONT = False
    _REVISES_YIELDED_MEMBERS = True

    def _open_index(self, source: ArchiveSource, start_offset: int) -> None:
        self._fp = None
        self._walker = LocalHeaderWalk(source, offset=0, find_start=True)
        if start_offset:
            # The detector found a stub: read past it, so the walk starts at the
            # first local header and its offsets stay those of the file.
            skipped = self._walker.source.skip(start_offset)
            if skipped < start_offset:
                raise TruncatedError(
                    "ZIP stream ends inside the stub in front of the archive",
                    archive_name=self._archive_name,
                    source_format=ArchiveFormat.ZIP,
                )
        self._pass_data: _PassData | None = None
        # Local header offset -> the member built from it, for the directory to
        # update in place at the end. Emptied when the listing is discarded: those
        # members then stay as their local headers made them.
        self._by_offset: dict[int, ArchiveMember] = {}
        # Every local header offset the walk met, which the directory must account
        # for. Kept when the listing is discarded, unlike the two maps beside it:
        # without it every directory entry would look like one with no local header.
        # ``max_members`` bounds its length, as it bounds the seekable read's offsets,
        # though a set costs some 60 bytes an offset against their array's 8, for a
        # lookup per directory entry.
        self._local_offsets: set[int] = set()
        # Member id -> its whole data, when short enough to be a link target, until
        # the directory says which members are links. Weighed by the listing tracker.
        self._captured: dict[int, bytes] = {}
        # Member id -> why its bytes were not kept, when not for their length
        # (``_PassData.uncaptured``), so a link's missing target says why.
        self._uncaptured: dict[int, str] = {}
        # Local header offset -> what the data descriptor after a bit-3 member's data
        # said (CRC, compressed size, size), for the directory to confirm at the end.
        self._descriptors: dict[int, tuple[int, int, int]] = {}
        # The kept members in the directory's order, once it has been read.
        self._directory_order: list[ArchiveMember] | None = None
        # Members the directory fails at the end of the pass, in its order, with why
        # (design.md §"Failures found at the end of the pass").
        self._failed_at_end: list[tuple[ArchiveMember, CorruptionError]] = []
        self._end_record: EndRecord | None = None
        # The first header is parsed at open, so a stream that is no ZIP fails here
        # (DR-15b), as a TAR's does.
        try:
            self._first_entry: LocalEntry | None = self._walker.next_entry()
        except ArchiveyError as exc:
            self._stamp_error_context(exc)
            raise

    # -- the walk ----------------------------------------------------------------

    def _iter_members(self) -> Iterator[ArchiveMember]:
        walker = self._walker
        max_members = self._config.listing_limits.max_members
        index = 0
        entry = self._first_entry
        self._first_entry = None
        while True:
            if index:
                self._leave_member()
                with self._translated_errors():
                    entry = walker.next_entry()
            if entry is None:
                break
            if max_members is not None and index >= max_members:
                raise _max_members_error(max_members, "ZIP stream's local headers name")
            member = self._local_member(entry, index)
            self._pass_data = _PassData(
                self,
                entry,
                member,
                walker.member_data(
                    entry,
                    encrypted=_is_encrypted_entry(
                        entry.header.flags, entry.header.method
                    ),
                ),
            )
            self._by_offset[entry.offset] = member
            self._local_offsets.add(entry.offset)
            yield member
            index += 1
        self._read_directory()

    def _leave_member(self) -> None:
        """Read past the data of the member the walk is at, before the next header."""
        current = self._pass_data
        if current is None:
            return
        current.passed = True
        member = current.member
        try:
            with self._translated_errors(member.name):
                current.drain()
        finally:
            self._pass_data = None
        captured = current.captured
        if not self._listing_discarded:
            assert member._member_id is not None
            if captured is not None:
                self._captured[member._member_id] = captured
                self._listing_tracker.account_retained_bytes(
                    len(captured), enforce=False
                )
            elif (why := current.uncaptured) is not None:
                self._uncaptured[member._member_id] = why
        if self._listing_discarded:
            self._by_offset.clear()
            self._captured.clear()
            self._uncaptured.clear()

    def _local_member(self, entry: LocalEntry, index: int) -> ArchiveMember:
        """The member as its local header describes it, until the directory arrives.

        No field the directory alone holds is guessed: ``mode``, ``create_system`` and
        ``comment`` are ``None``, and the type is ``FILE`` or ``DIRECTORY`` from the
        name. A backslash is a literal character until the host says otherwise, except
        at the end of the name, which makes the member a ``DIRECTORY`` until then: a
        DOS or Windows writer marks its folders that way, and a folder the pass wrote
        as a file would stop every member inside it from being written, where a
        directory the host turns out not to make one is taken back at the end of the
        pass. Nothing is reported here; the directory's version of the member reports
        what it finds, once.
        """
        header = entry.header
        central = _central_from_local(entry)
        decoded_name = self._decode_member_name(central)
        stored_as_directory = decoded_name.text.endswith(("/", "\\"))
        member_type = MemberType.DIRECTORY if stored_as_directory else MemberType.FILE
        name = normalize_member_name(
            decoded_name.text, member_type, backslash_is_separator=False
        )
        method = header.method
        aes_info = parse_winzip_aes_extra(header.extra) if method == 99 else None
        if aes_info is not None:
            algo = _ZIP_COMPRESSION_ALGOS.get(
                aes_info.actual_method, CompressionAlgorithm.UNKNOWN
            )
            compression: tuple[CompressionMethod, ...] = (CompressionMethod(algo=algo),)
        else:
            compression = _ZIP_COMPRESSION_TUPLES.get(
                method, (CompressionMethod(algo=CompressionAlgorithm.UNKNOWN),)
            )
        modified, accessed, ntfs_ctime, ut_ctime, _issues = _zip_timestamps(
            central, name
        )
        created, ctime = _zip_created(CreateSystem.UNKNOWN, ntfs_ctime, ut_ctime)
        deferred = entry.deferred
        hashes = (
            {HashAlgorithm.CRC32: crc32_digest(header.crc)}
            if not deferred
            and member_type is MemberType.FILE
            and not (aes_info is not None and aes_info.is_ae2)
            else {}
        )
        return ArchiveMember(
            type=member_type,
            name=name,
            raw_name=decoded_name.raw_name,
            size=None if deferred else header.file_size,
            compressed_size=None if deferred else header.compressed_size,
            compression=compression,
            hashes=hashes,
            extra=MemberExtra({"zip.compress_type": method}),
            modified=modified,
            accessed=accessed,
            created=created,
            ctime=ctime,
            is_encrypted=_is_encrypted_entry(header.flags, method),
            _raw=_ZipLocator(
                index=index,
                header_offset=entry.offset,
                compressed_size=header.compressed_size,
                file_size=header.file_size,
                crc=header.crc,
                flags=header.flags,
                method=method,
                dos_time=header.dos_time,
                name=header.name,
                aes=aes_info,
                strong_encryption=_uses_strong_encryption(header.flags, header.extra),
                reparse_fallback=None,
            ),
        )

    # -- the directory -------------------------------------------------------------

    def _read_directory(self) -> None:
        """Read the directory and end records, and update the members in place.

        Each local entry is matched to its directory entry by offset, once the end
        record has said where the offsets count from. The directory is authoritative:
        a member it describes is rebuilt from it, as the seekable read builds it, and
        the yielded object takes every field of that build. The entries are charged
        against ``max_members`` as they are read, as the seekable read's directory
        walk charges them.

        The end record comes after the last entry, so the entries are held until then.
        Their names, extra fields and comments are weighed against
        ``max_metadata_bytes`` as they arrive (``_drop_held_directory``); past it only
        each entry's offset is kept, which the checks below need.

        A local entry no directory entry points to is dropped from the listing and
        reported (``ARCHIVE_UNLISTED_DATA``). Of two members with the same name, the
        later in the directory is current (``_listed_in_index_order``). A member whose
        data descriptor the directory contradicts, or whose local header names it
        differently, fails (``_failed_at_end``): the pass raises for the first, after
        a directory entry with no local entry and two entries for one local entry.
        """
        walker = self._walker
        limits = self._config.listing_limits
        max_members = limits.max_members
        max_meta = limits.max_metadata_bytes
        # Every entry's offset as stored, in directory order, and the entries
        # themselves while their text is within max_metadata_bytes. A discarded
        # listing has no member left to update, so it keeps none.
        stored_offsets = array("Q")
        entries: list[CentralEntry] | None = None if self._listing_discarded else []
        held = 0
        with self._translated_errors():
            for central in walker.directory():
                if max_members is not None and len(stored_offsets) >= max_members:
                    raise _max_members_error(max_members)
                stored_offsets.append(central.header_offset)
                if entries is None:
                    continue
                held += len(central.name) + len(central.extra) + len(central.comment)
                if max_meta is not None and held > max_meta:
                    entries = None
                    self._drop_held_directory(held)
                    continue
                entries.append(central)
            end = walker.end
            assert end is not None
        self._end_record = self._end = end
        local_offsets = self._local_offsets
        by_offset = self._by_offset
        updates: list[tuple[ArchiveMember, CentralEntry]] = []
        matched: set[int] = set()
        order: list[ArchiveMember] = []
        # Entries pointing where no local header was, and entries pointing at a local
        # header an earlier entry already took (by position in the directory).
        unmatched: list[int] = []
        shared: list[int] = []
        failed: list[tuple[int, ArchiveMember, CorruptionError]] = []
        for position, stored in enumerate(stored_offsets):
            offset = stored + end.base
            if offset not in local_offsets:
                unmatched.append(position)
                continue
            if offset in matched:
                shared.append(position)
                continue
            matched.add(offset)
            member = by_offset.get(offset)
            if member is None:
                continue
            order.append(member)
            if entries is None:
                continue
            central = entries[position]
            local_name = cast("_ZipLocator", member._raw).name
            if central.name != local_name:
                # The seekable read refuses this member when it reads its local
                # header; a forward pass has both copies only now.
                failed.append((position, member, _renamed_error(local_name, central)))
                continue
            updates.append((member, central))
            descriptor = self._descriptors.get(offset)
            if descriptor is not None and _contradicts(descriptor, central):
                failed.append(
                    (position, member, _descriptor_error(member, descriptor, central))
                )
        unlisted = sorted(local_offsets - matched)
        for member, central in updates:
            self._update_from_directory(member, central)
        if not self._listing_discarded:
            self._directory_order = order
        self._failed_at_end = [(member, error) for _, member, error in failed]
        # Only a link's bytes are read again, for its target.
        links = {
            member._member_id
            for member, _ in updates
            if member.type is MemberType.SYMLINK
        }
        self._captured = {
            member_id: data
            for member_id, data in self._captured.items()
            if member_id in links
        }
        self._uncaptured = {
            member_id: why
            for member_id, why in self._uncaptured.items()
            if member_id in links
        }
        self._report_unlisted(unlisted)
        self._keep_updated_listing_within_limits(updates)
        self._report_directory_findings(end, len(stored_offsets))
        found = first_nonzero_offset(walker.source)
        if found is not None:
            self._emit_trailing_data(found)
        self._by_offset = {}
        self._local_offsets = set()
        self._descriptors = {}
        if unmatched:
            first = unmatched[0]
            raise CorruptionError(
                f"ZIP central directory entry {self._directory_entry_label(entries, first)} "
                f"points to offset {stored_offsets[first] + end.base}, where the stream "
                f"held no local header ({len(unmatched)} such entries)",
                archive_name=self._archive_name,
                source_format=ArchiveFormat.ZIP,
            )
        if shared:
            first = shared[0]
            raise CorruptionError(
                f"ZIP central directory entry {self._directory_entry_label(entries, first)} "
                f"points to the local header at offset {stored_offsets[first] + end.base}, "
                f"which an earlier entry already describes ({len(shared)} such entries); "
                "a source that cannot seek reads that data once, for one member",
                archive_name=self._archive_name,
                source_format=ArchiveFormat.ZIP,
            )
        if failed:
            _, _, error = failed[0]
            raise error

    def _report_unlisted(self, offsets: list[int]) -> None:
        """Drop the members at ``offsets`` from the listing, and report each.

        No directory entry lists them, so the seekable read never sees them: an update
        appended to the archive without rewriting its directory, or a member planted
        between two others. Data outside any member is a warning (DR-3). A discarded
        listing kept no member to drop, so only the offset is reported.
        """
        if not offsets:
            return
        by_offset = self._by_offset
        dropped = [by_offset[offset] for offset in offsets if offset in by_offset]
        if dropped:
            for member in dropped:
                member.is_current = False
            self._drop_listed(dropped)
        for offset in offsets:
            member = by_offset.get(offset)
            name = "" if member is None else member.name
            what = "a local entry" if member is None else f"member {quoted(name)}"
            self._diagnostics_collector.emit(
                code=DiagnosticCode.ARCHIVE_UNLISTED_DATA,
                message=(
                    f"ZIP stream holds {what} at offset {offset} that the central "
                    "directory does not list; it is left out, as reading the "
                    "archive from a file leaves it out (this file may hold something "
                    "added to the archive)."
                ),
                context=UnlistedDataContext(
                    archive_name=self._archive_name,
                    format="zip",
                    member_name=name,
                    member_id=None if member is None else member._member_id,
                    offset=offset,
                    compressed_size=None if member is None else member.compressed_size,
                ),
                logger=logger,
            )

    def _note_descriptor(self, entry: LocalEntry, descriptor: DataDescriptor) -> None:
        """Keep what a bit-3 member's data descriptor said, for the directory to
        confirm (``_read_directory``)."""
        self._descriptors[entry.offset] = (
            descriptor.crc,
            descriptor.compressed_size,
            descriptor.file_size,
        )

    def _listed_in_index_order(self) -> list[ArchiveMember]:
        order = self._directory_order
        if order is None or len(order) != len(self._listed):
            return self._listed
        return order

    def _directory_entry_label(
        self, entries: list[CentralEntry] | None, position: int
    ) -> str:
        """The entry's name when its text was kept, else its position."""
        if entries is None:
            return f"#{position}"
        return quoted(self._decode_member_name(entries[position]).text)

    def _drop_held_directory(self, held: int) -> None:
        """Stop holding directory entries: their text passed ``max_metadata_bytes``.

        The seekable read builds a member from each entry as it reads it and weighs
        that; a forward pass must hold the entries until the end record, and the held
        text is what it weighs instead. It is charged to the listing, which it would
        have become: an enforcing pass raises here, and one that does not enforce the
        limits discards its listing, as ``_keep_updated_listing_within_limits`` does,
        so the members it yielded stay as their local headers made them. The links
        among them get no target: matching them needs the end record (DR-5a; the
        seekable pass reports nothing either once it discards).
        """
        self._listing_tracker.account_retained_bytes(
            held, enforce=self._progressive_enforce_listing_limits
        )
        self._discard_listing()
        self._by_offset.clear()
        self._captured.clear()
        self._uncaptured.clear()

    def _keep_updated_listing_within_limits(
        self, updates: list[tuple[ArchiveMember, CentralEntry]]
    ) -> None:
        """Discard the listing when what the directory added took it over the limits,
        in a pass that does not enforce them (``_keep_listing_within_limits``).

        The base checks after each member it yields, and the directory arrives after the
        last one, so nothing else would. The links the directory made get their
        targets first, as the end of the pass would give them (``_finalize_links``):
        a discarded listing is never finalized, and the pass cannot come back for those
        bytes. A target that fails is reported here; the seekable pass leaves that
        report to finalization, so after its discard it reports nothing (DR-5a).
        """
        if self._progressive_enforce_listing_limits or not self._listing_over_limits():
            return
        if self._config.read_link_targets:
            for member, _ in updates:
                if (
                    member.type is MemberType.SYMLINK
                    and member.link_target is None
                    and not member._link_target_resolved
                ):
                    try:
                        self._resolve_link_target(member)
                    except CorruptionError as exc:
                        self._report_damaged_link_target(member, exc)
        self._discard_listing()
        self._captured.clear()
        self._uncaptured.clear()

    def _update_from_directory(
        self, member: ArchiveMember, central: CentralEntry
    ) -> None:
        """Give ``member`` the fields the seekable read builds from ``central``."""
        member_id = member._member_id
        assert member_id is not None
        old_name = member.name
        weighed = member_metadata_bytes(member)
        fresh = self._to_member(central, member_id)
        for f in fields(ArchiveMember):
            if f.name in _KEPT_ON_UPDATE:
                continue
            setattr(member, f.name, getattr(fresh, f.name))
        member._diagnostics = member._diagnostics + fresh._diagnostics
        # The directory adds what no local header holds (a comment, its own extra
        # field); the limits weigh it when the pass is published, as they weigh the
        # seekable read's members when it lists them.
        self._listing_tracker.account_retained_bytes(
            member_metadata_bytes(member) - weighed, enforce=False
        )
        if member.name != old_name:
            self._reindex_name(member, old_name)
        if (
            member.type is MemberType.SYMLINK
            and member.link_target is None
            and not member._link_target_resolved
            and not self._listing_discarded
        ):
            member._link_target_unread = True

    def _reindex_name(self, member: ArchiveMember, old_name: str) -> None:
        listed = self._listed_by_name.get(old_name)
        if listed is not None and member in listed:
            listed.remove(member)
            if not listed:
                del self._listed_by_name[old_name]
        if not self._listing_discarded:
            self._index_member_name(member)

    def _report_directory_findings(self, end: EndRecord, read: int) -> None:
        """The end record's disagreements with the directory.

        The entry count and the comment are checked as the seekable read checks them
        (``CentralDirectoryWalk.findings``). The declared directory size is not:
        the seekable read reads only that many bytes, and reports an entry that runs
        past them (``EntryOverrun``), where this walk has read every entry before the
        end record sized them. So a size the entries do not fill is reported as such
        (``DirectorySizeMismatch``), and every entry stays listed.
        """
        walker = self._walker
        assert walker.cd_start is not None and walker.cd_end is not None
        held = walker.cd_end - walker.cd_start
        if held != end.cd_size:
            self._emit_walk_finding(
                DirectorySizeMismatch(declared=end.cd_size, held=held), None
            )
        if end.entries_declared != read:
            self._emit_walk_finding(
                EntryCountMismatch(
                    declared=end.entries_declared, read=read, zip64=end.zip64
                ),
                None,
            )
        if len(end.comment) < end.comment_declared:
            self._emit_walk_finding(
                CommentCutShort(
                    declared=end.comment_declared, available=len(end.comment)
                ),
                None,
            )

    # -- data ----------------------------------------------------------------------

    def _iter_with_data(
        self, copies: FileCopyPass = DEFAULT_FILE_COPY_PASS
    ) -> Iterator[tuple[ArchiveMember, ArchiveStream | None]]:
        # close_previous=False: moving to the next header reads past the previous
        # member's data, and its stream reads nothing after that.

        def _open(member: ArchiveMember) -> ArchiveStream | None:
            if not member.is_file:
                return None
            return self._open_member(member)

        yield from self._drive_pass_streams(
            self._begin_forward_pass(), open_member=_open, close_previous=False
        )

    def _open_member(self, member: ArchiveMember) -> ArchiveStream:
        current = self._pass_data
        if current is None or current.member is not member:
            raise ValueError(
                f"ZIP member {quoted(member.name)} is not the one the walk is at; "
                "a source that cannot seek is read once, member by member"
            )
        return self._wrap_member_stream(
            _PassStream(current), member.name, size=member.size
        )

    def _link_target_opener(
        self, member: ArchiveMember
    ) -> Callable[[], AbstractContextManager[ReadableStream]]:
        """The bytes the pass kept for the link (``_leave_member``); without them, the
        error that says why, so the target is reported as the file read reports it."""
        member_id = member._member_id
        assert member_id is not None
        captured = self._captured.get(member_id)
        why = self._uncaptured.get(member_id)

        def missing() -> ArchiveyError:
            if why == "encrypted":
                return EncryptionError(
                    f"The symlink target of {quoted(member.name)} is encrypted, and "
                    "an encrypted member cannot be read from a source that cannot "
                    "seek"
                )
            if why == "damaged":
                return CorruptionError(
                    f"The data of symlink {quoted(member.name)} failed its check as "
                    "the pass read it"
                )
            cap = MAX_LINK_TARGET_BYTES
            return CorruptionError(
                f"The symlink target of {quoted(member.name)} was not kept as the "
                f"pass read its data: it is over {cap} bytes"
            )

        @contextmanager
        def open_data() -> Iterator[ReadableStream]:
            if captured is None:
                raise missing()
            yield io.BytesIO(captured)

        return open_data

    # -- what the caller sees ------------------------------------------------------

    def _archive_comment(self) -> str | None:
        end = self._end_record
        if end is None or not end.comment:
            return None
        return self._decode_unflagged_comment(end.comment)

    def _known_member_count(self) -> int | None:
        end = self._end_record
        return None if end is None else self._walker.entries_read

    def _cost_receipt(self) -> CostReceipt:
        return _ZIP_FORWARD_COST

    def _get_archive_info(self) -> ArchiveInfo:
        return ArchiveInfo(
            format=ArchiveFormat.ZIP,
            format_version=None,
            is_solid=False,
            member_count=self._known_member_count(),
            comment=self._archive_comment(),
            is_encrypted=False,
            is_multivolume=self._volume_count > 1,
            cost=_ZIP_FORWARD_COST,
            extra=ArchiveInfoExtra({"zip.volume_count": self._volume_count}),
        )

    def _close_archive(self) -> None:
        current, self._pass_data = self._pass_data, None
        if current is not None:
            current.abandon()


__all__ = ["ZipForwardReader"]
