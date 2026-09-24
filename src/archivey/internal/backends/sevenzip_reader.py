"""Native 7z reader backend (``BaseArchiveReader`` wiring).

Module split:

- :mod:`.sevenzip_methods` — method-id registry / :class:`MethodKind`
- :mod:`.sevenzip_parser` — signature + header property tree → :class:`SevenZipArchive`
- :mod:`.sevenzip_pipeline` — folder coder plan/execute + encoded-header decode
- this module — passwords, member list, solid-folder demux, CRC/encryption mapping

Open path: signature → ``parse_header_block`` → (one encoded-header layer) →
``materialize_archive`` → list members. Member open folds the folder's packed
slice through :func:`open_folder_pipeline`; solid folders use
:class:`~archivey.internal.streams.streamtools.solid.SolidBlockReader` so one
decode serves consecutive files.

Password bytes for the 7z KDF are UTF-16LE (:func:`_password_to_kdf_bytes`). An
empty header after decrypt is treated as a wrong password (never a silent empty
listing) — threat-model O8. Separately, store/AES members with no folder digest
and no member CRC emit ``DIGEST_UNVERIFIABLE`` (decryption cannot be authenticated).
"""

from __future__ import annotations

import io
import re
import stat
import zlib
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import BinaryIO, ContextManager

from archivey.config import ArchiveyConfig
from archivey.cost import AccessCost, CostReceipt, ListingCost, StreamCapability
from archivey.diagnostics import DiagnosticCode, DigestContext, MemberTimestampContext
from archivey.escaping import quoted
from archivey.exceptions import (
    ArchiveyError,
    CorruptionError,
    EncryptionError,
    PackageNotInstalledError,
    ResourceLimitError,
    StreamNotSeekableError,
    TruncatedError,
    UnsupportedFeatureError,
    raw_message_of,
)
from archivey.internal.backends.sevenzip_aes import SevenZipKeyCache
from archivey.internal.backends.sevenzip_methods import is_aes
from archivey.internal.backends.sevenzip_parser import (
    EncodedHeader,
    PlainHeader,
    SevenZipArchive,
    SevenZipFileRecord,
    SevenZipFolder,
    compression_method_for_coder,
    empty_archive,
    find_signature_offset,
    folder_is_encrypted,
    materialize_archive,
    parse_header_block,
    read_signature_and_next_header,
)
from archivey.internal.backends.sevenzip_pipeline import (
    decode_encoded_header,
    decode_folder_to_bytes,
    encoded_header_needs_password,
    open_folder_pipeline,
    parse_decoded_header,
)
from archivey.internal.base_reader import BaseArchiveReader, ReadBackend
from archivey.internal.config import (
    KeyDerivationBudget,
    stream_config_from_archivey,
)
from archivey.internal.diagnostics_collector import DiagnosticCollector
from archivey.internal.logs import backends as logger
from archivey.internal.logs import integrity as integrity_logger
from archivey.internal.naming import (
    emit_member_name_normalized,
    infer_member_name_from_archive,
    normalize_member_name,
)
from archivey.internal.open_site import OpenSite
from archivey.internal.password import (
    _PasswordCandidates,
    _PasswordCandidatesExhausted,
    _WrongPassword,
)
from archivey.internal.registry import register_reader
from archivey.internal.sevenzip_detect import validate_sevenzip_signature_header
from archivey.internal.source import ArchiveSource
from archivey.internal.streams.archive_stream import ArchiveStream
from archivey.internal.streams.crypto import _AesCbcTruncatedError
from archivey.internal.streams.streamtools import (
    ReadableStream,
    SharedSource,
    SlicingStream,
    SolidBlockReader,
    is_seekable,
    skip_forward,
)
from archivey.internal.timestamps import TimestampIssue, filetime_to_datetime
from archivey.types import (
    EXTRA_IS_REPARSE_POINT,
    ArchiveFormat,
    ArchiveInfo,
    ArchiveInfoExtra,
    ArchiveMember,
    CompressionMethod,
    CreateSystem,
    HashAlgorithm,
    MagicSignature,
    MemberExtra,
    MemberStreams,
    MemberType,
    crc32_digest,
)

_WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT = 0x400


def _is_windows_reparse_point(attrs: int | None) -> bool:
    """True when 7z's attribute word flags this entry as a Windows reparse point.

    A Unix-written member carries its mode in the high word, and a POSIX symlink is not
    a reparse point, so the low word's `0x400` is only read when the high word does not
    already say `S_IFLNK`. "Flagged as" is the whole claim: the reparse *tag*, which
    separates a junction from a Windows symlink, is in the member's data, not here.
    """
    return (
        attrs is not None
        and not stat.S_ISLNK(attrs >> 16)
        and bool(attrs & _WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT)
    )


_SEVENZIP_STEM_SUFFIX_RE = re.compile(r"\.7z(?:\.\d{3})?$", re.IGNORECASE)
# Drain/CRC step for encrypted-folder password confirm. 7z AES has no check
# value, so a candidate is judged by decoding and CRCing; this keeps peak
# memory at one chunk instead of the whole folder (same size as the sized
# drain in ``verify.py`` and ZipCrypto's parallel CRC).
_PASSWORD_CONFIRM_CHUNK = 65536


@dataclass(frozen=True)
class _MemberRaw:
    record: SevenZipFileRecord
    folder_index: int | None
    file_in_folder: int | None


def _password_to_kdf_bytes(password: bytes) -> bytes:
    try:
        return password.decode("utf-8").encode("utf-16le")
    except UnicodeDecodeError:
        return password


def _infer_nameless_member_name(archive_name: str | None) -> str:
    return infer_member_name_from_archive(
        archive_name, strip_suffix_re=_SEVENZIP_STEM_SUFFIX_RE
    )


def _folder_position(member: ArchiveMember) -> int:
    """``member``'s index among its folder's members (its data order in the folder)."""
    raw = member._raw
    assert isinstance(raw, _MemberRaw) and raw.file_in_folder is not None
    return raw.file_in_folder


def _member_stream_size(member: ArchiveMember) -> int:
    return member.size if member.size is not None else 0


def _crc_exactly(
    stream: ReadableStream,
    nbytes: int,
    *,
    chunk_size: int = _PASSWORD_CONFIRM_CHUNK,
) -> int:
    """Read ``nbytes`` from ``stream``, folding CRC32.

    Raise ``EncryptionError`` on a short read. Peak extra memory is one chunk.
    """
    remaining = nbytes
    crc = 0
    # `> 0`, not truthiness: a stream that over-returns would drive `remaining`
    # negative, and `read(negative)` is read-everything — the whole-folder gather
    # this function exists to avoid.
    while remaining > 0:
        chunk = stream.read(min(chunk_size, remaining))
        if not chunk:
            raise _WrongPassword("Wrong password or corrupt 7z folder")
        crc = zlib.crc32(chunk, crc)
        remaining -= len(chunk)
    return crc


def _verify_decoded_folder(
    folder: SevenZipFolder,
    stream: ReadableStream,
    *,
    expected_size: int,
    member_digests: list[tuple[int, int | None]] | None = None,
) -> None:
    """Raise ``EncryptionError`` when decoded folder bytes fail CRC checks.

    Reads incrementally so peak memory is O(chunk), not O(folder). A stream
    that ends early still fails, including the no-anchor case (no folder
    digest and CRC-less members): that case accepts only after a full-length
    drain, matching 7-Zip's best-effort decrypt.
    """
    if folder.digest_defined:
        actual = _crc_exactly(stream, expected_size)
        expected = (folder.crc if folder.crc is not None else 0) & 0xFFFFFFFF
        if actual & 0xFFFFFFFF != expected:
            raise _WrongPassword("Wrong password or corrupt 7z folder")
        return
    if not member_digests:
        _crc_exactly(stream, expected_size)
        return
    # The per-member walk covers `expected_size` by construction: the caller derives
    # it from these same member sizes (`_folder_members_total_size`). Pinned here because
    # nothing else records the coupling now the whole-folder length check is gone.
    assert expected_size == sum(size for size, _ in member_digests), (
        "member digest sizes must sum to the folder unpack size"
    )
    for size, raw_expected in member_digests:
        actual = _crc_exactly(stream, size)
        if raw_expected is None:
            continue
        if actual & 0xFFFFFFFF != raw_expected & 0xFFFFFFFF:
            raise _WrongPassword("Wrong password or corrupt 7z folder")


class SevenZipReader(BaseArchiveReader):
    """Reads 7z archives using the native parser and shared codec streams."""

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
            ArchiveFormat.SEVEN_Z,
            streaming,
            archive_name,
            config,
            collector=collector,
            member_streams=member_streams,
            open_site=open_site,
        )
        del encoding  # 7z stores names as UTF-16LE.
        self._source = source
        self._passwords = passwords or _PasswordCandidates()
        self._key_cache = SevenZipKeyCache(
            budget=KeyDerivationBudget(self._config.decoder_limits)
        )
        self._folder_passwords: dict[int, bytes | None] = {}
        # A symlink's target is its member data, usually mid-way through a solid
        # folder, so link bytes are read ahead of resolution, a folder at a time
        # (``format-7z``, "A 7z folder is decoded at most once for its link targets"):
        # by listing's folder sweep, or by a streaming pass as its cursor reaches the
        # link. Keyed by member id. A value that is an exception is the failed read,
        # raised again when the link resolves, so it gets the handling a direct read
        # would have got.
        self._link_data: dict[int, bytes | ArchiveyError] = {}
        # The link member a data pass is on, and how to open it from that pass's own
        # folder decode. Valid only until the pass moves on; ``extract_all`` reads an
        # accepted link through it (``_link_data_stream``).
        self._pass_link: tuple[ArchiveMember, Callable[[], ArchiveStream]] | None = None
        self._stream_config = stream_config_from_archivey(
            self._config,
            streaming=streaming,
            seekable=MemberStreams.SEEKABLE in member_streams,
        )
        if not source.seekable():
            raise StreamNotSeekableError(
                "7z archives require a seekable source: the header and packed streams "
                "are addressed by offsets.",
                archive_name=archive_name,
                source_format=ArchiveFormat.SEVEN_Z,
            )
        self._shared = SharedSource(source, wrap_handle=self._seek_handle_wrapper())
        # Every offset a 7z archive records is relative to its signature header, so the
        # whole file geometry is rebased once here instead of each seek learning about
        # stubs: ``_view`` is the only thing that knows byte 0 of the archive is not
        # byte 0 of the source. ``start_offset`` is detection's ``payload_offset``; the
        # scan on top of it is what makes forced ``format=SEVEN_Z`` work on an SFX file
        # with no detection involved.
        probe = self._shared.view(start_offset)
        try:
            self._origin = start_offset + find_signature_offset(probe)
        finally:
            probe.close()
        self._volume_count = source.volume_count
        self._archive = self._load_archive()
        self._init_folder_caches(self._archive)
        self._members = self._build_members()
        self._folder_members = self._members_by_folder()

    def _view(self, start: int, length: int | None = None) -> BinaryIO:
        """A source view whose ``start`` is measured from the signature header.

        The single place the self-extracting stub is subtracted: pack offsets, the
        next-header seek and the encoded-header slices are all signature-relative
        already, so they keep working unchanged for an archive that begins mid-file.
        """
        return self._shared.view(self._origin + start, length)

    def _load_archive(self) -> SevenZipArchive:
        """Two-phase header load: parse → decode encoded → re-parse → materialize."""
        fp = self._view(0)
        signature = read_signature_and_next_header(fp)
        if not signature.header_data:
            return empty_archive(signature)

        max_members = self._config.listing_limits.max_members
        block = parse_header_block(signature.header_data, max_members=max_members)
        header_encrypted = False
        if isinstance(block, EncodedHeader):
            header_encrypted = encoded_header_needs_password(block)
            block = self._decode_encoded_header_block(
                fp, block, max_members=max_members
            )
        assert isinstance(block, PlainHeader)
        return materialize_archive(
            signature, block, is_header_encrypted=header_encrypted
        )

    def _decode_encoded_header_block(
        self, fp: BinaryIO, encoded: EncodedHeader, *, max_members: int | None
    ) -> PlainHeader:
        def decode(password: bytes | None) -> bytes:
            return decode_encoded_header(
                fp,
                encoded,
                password=password,
                key_cache=self._key_cache,
                stream_config=self._stream_config,
                collector=self._diagnostics_collector,
            )

        if not encoded_header_needs_password(encoded):
            # Unencrypted self-copy or a hostile nested header stays CorruptionError.
            return parse_decoded_header(decode(None), max_members=max_members)

        def decrypt(password: bytes) -> PlainHeader:
            # AES header decrypt has no MAC: a wrong password yields garbage that fails
            # the codec (CorruptionError, or most often TruncatedError: wrong-key LZMA
            # usually ends short of the declared size) or property parsing, rather
            # than raising EncryptionError in decrypt. All are judged here, per
            # candidate, so a wrong first candidate moves on to the next one instead of
            # ending the attempt (D8). The cost: damaged encoded-header bytes cannot be
            # told from a wrong key, so they read as a rejected password.
            # UnsupportedFeatureError / PackageNotInstalledError from decode (hostile
            # NumCyclesPower, missing cryptography) are not about the password and
            # pass through.
            try:
                decoded = decode(_password_to_kdf_bytes(password))
            except (CorruptionError, TruncatedError) as exc:
                raise EncryptionError("Password(s) rejected for the 7z header") from exc
            try:
                plain = parse_decoded_header(decoded, max_members=max_members)
            except (
                CorruptionError,
                TruncatedError,
                UnsupportedFeatureError,
            ) as exc:
                raise EncryptionError("Password(s) rejected for the 7z header") from exc
            # O8: 7zAES has no password check value. Wrong-key garbage occasionally
            # LZMA-decodes into a header that parses with zero file records (py7zr
            # omits the encoded-header folder CRC). Legitimate writers never encrypt
            # an empty header — treat that as a rejected password.
            if not plain.files:
                raise EncryptionError("Password(s) rejected for the 7z header")
            return plain

        try:
            return self._passwords.attempt(None, decrypt)
        except _PasswordCandidatesExhausted as exc:
            # Keep required-vs-rejected (D8) but restore the header surface (R1): listing
            # needs a password is different UX from a wrong password on the header.
            if exc.message.startswith("Password required"):
                raise EncryptionError(
                    "Password required to decrypt the 7z header"
                ) from exc
            raise EncryptionError("Password(s) rejected for the 7z header") from exc

    def _init_folder_caches(self, archive: SevenZipArchive) -> None:
        """Derive per-folder indexes used by listing and open.

        Kept as one helper so unit tests that construct a reader via
        ``object.__new__`` can populate the same derived state ``__init__`` does
        without hand-maintaining each cache field.
        """
        self._folder_pack_starts = self._folder_pack_start_indices(archive)
        # Public CompressionMethod tuples are identical for every member in a
        # folder — build once (solid many-member listing hot path).
        self._folder_compression = self._build_folder_compression(archive)

    @staticmethod
    def _folder_pack_start_indices(archive: SevenZipArchive) -> list[int]:
        starts: list[int] = []
        index = 0
        for folder in archive.folders:
            starts.append(index)
            index += len(folder.packed_indices)
        return starts

    @staticmethod
    def _build_folder_compression(
        archive: SevenZipArchive,
    ) -> list[tuple[CompressionMethod, ...]]:
        """One public compression-chain tuple per folder (shared by its members).

        ``ArchiveMember.compression`` is in compress order, and a linear chain —
        one packed stream into the first coder, each coder's output bound to the
        next one's input — lists its coders in decode order, so walk them
        backwards. That is also the order 7-Zip lists a member's ``Method`` in:
        ``-mf=BCJ`` reads ``BCJ LZMA2``. Listing does not check the wiring; only
        decoding does (``_check_linear_coder_chain`` in ``plan_folder``). A folder
        outside that shape gets the same reversal as a best effort: for BCJ2,
        7-Zip writes the BCJ2 coder last, so it leads the tuple, but the three
        coders on its side streams follow in no meaningful order, and whether they
        belong in the tuple at all is not settled yet.

        A coder the registry does not know is listed as ``UNKNOWN`` rather than
        dropped, so the chain never looks shorter than it is. AES is left out: it
        is encryption, reported through ``is_encrypted``.
        """
        out: list[tuple[CompressionMethod, ...]] = []
        for folder in archive.folders:
            out.append(
                tuple(
                    compression_method_for_coder(coder)
                    for coder in reversed(folder.coders)
                    if not is_aes(coder.method)
                )
            )
        return out

    def _build_members(self) -> list[ArchiveMember]:
        # is_current is stamped by BaseArchiveReader's shared last-entry-wins pass.
        return [
            self._to_member(record, index)
            for index, record in enumerate(self._archive.files)
        ]

    def _members_by_folder(self) -> dict[int, list[ArchiveMember]]:
        grouped: dict[int, list[ArchiveMember]] = {}
        for member in self._members:
            raw = member._raw
            assert isinstance(raw, _MemberRaw)
            if raw.folder_index is not None:
                grouped.setdefault(raw.folder_index, []).append(member)
        return grouped

    def _iter_members(self) -> Iterator[ArchiveMember]:
        yield from self._members

    def _iter_with_data(self) -> Iterator[tuple[ArchiveMember, ArchiveStream | None]]:
        current_folder: int | None = None
        solid: SolidBlockReader | None = None

        def _folder_reader(
            folder_index: int, member: ArchiveMember
        ) -> SolidBlockReader:
            """Open the folder's decode pipeline, once, on the first read into it."""
            nonlocal solid
            if solid is None:
                # Count at the folder decode layer (solid invariant); member wraps
                # pass track_output=False so sequential reads are not double-counted.
                solid = SolidBlockReader(
                    self._track_decompressed(
                        self._open_folder_stream(folder_index, member)
                    )
                )
            return solid

        def _enter_folder(folder_index: int) -> None:
            nonlocal current_folder, solid
            if folder_index != current_folder:
                if solid is not None:
                    solid.close()
                    solid = None
                current_folder = folder_index

        def _open(member: ArchiveMember) -> ArchiveStream | None:
            self._pass_link = None
            raw = member._raw
            assert isinstance(raw, _MemberRaw)
            if not member.is_file:
                if member.type is MemberType.SYMLINK and raw.folder_index is not None:
                    _enter_folder(raw.folder_index)
                    self._reach_pass_link(member, raw.folder_index, _folder_reader)
                return None
            if raw.folder_index is None:
                return self._wrap_member_stream(
                    io.BytesIO(b""), member.name, size=member.size
                )
            _enter_folder(raw.folder_index)
            folder_index = raw.folder_index
            return self._member_stream_from_solid(
                lambda: _folder_reader(folder_index, member), member
            )

        def _cleanup() -> None:
            self._pass_link = None
            # A finished pass has applied what it captured; an abandoned one never will,
            # and a later read of those links opens them directly.
            self._link_data.clear()
            if solid is not None:
                solid.close()

        yield from self._drive_pass_streams(
            self._listed_members(),
            open_member=_open,
            close_previous=True,
            cleanup=_cleanup,
        )

    def _reach_pass_link(
        self,
        member: ArchiveMember,
        folder_index: int,
        folder_reader: Callable[[int, ArchiveMember], SolidBlockReader],
    ) -> None:
        """A data pass has reached ``member``, a symlink whose target is its data.

        The pass yields no stream for it, and its folder decoder only moves when a later
        member is read, so without this the bytes go by unread and EOF finalization
        would decode the folder again to get them. A streaming pass under
        ``read_link_targets`` reads them now, through its own decoder, and keeps them
        for finalization. Otherwise the pass only offers its decoder for this one
        member, which is how ``extract_all`` reads an accepted link without a second
        decode.
        """

        def opener() -> ArchiveStream:
            return self._member_stream_from_solid(
                lambda: folder_reader(folder_index, member), member
            )

        if (
            self._streaming
            and self._config.read_link_targets
            and member.link_target is None
            and not member._link_target_resolved
        ):
            self._capture_link_data(member, opener)
        else:
            self._pass_link = (member, opener)

    def _capture_link_data(
        self, member: ArchiveMember, opener: Callable[[], ArchiveStream]
    ) -> None:
        """Read ``member``'s link bytes now and keep them for its resolution.

        Reads what ``_read_link_target_data`` would (and nothing for a member it refuses
        by size), so resolving over the kept bytes answers exactly as a direct read.
        A read that fails is kept as its exception and raised again then.
        """
        member_id = member._member_id
        assert member_id is not None
        if member_id in self._link_data:
            return
        raw = member._raw
        assert isinstance(raw, _MemberRaw)
        is_reparse_point = _is_windows_reparse_point(raw.record.attributes)
        if self._link_data_refused_by_size(member, is_reparse_point=is_reparse_point):
            return
        try:
            with opener() as stream:
                data = self._read_bounded_link_data(
                    stream, is_reparse_point=is_reparse_point
                )
        except ArchiveyError as exc:
            self._link_data[member_id] = exc
            return
        self._link_data[member_id] = data

    def _prepare_link_target_reads(self, members: list[ArchiveMember]) -> None:
        """Sweep each folder holding one of ``members`` once, up to its last link.

        Link finalization calls this with every link it is about to resolve. Without
        it, each link's read decoded its folder from the start up to that link, which
        on 7-Zip's default solid layout decodes a folder once per link.
        """
        by_folder: dict[int, list[ArchiveMember]] = {}
        for member in members:
            raw = member._raw
            if (
                member.type is not MemberType.SYMLINK
                or not isinstance(raw, _MemberRaw)
                or raw.folder_index is None
                or raw.file_in_folder is None
                or member._member_id in self._link_data
            ):
                continue
            by_folder.setdefault(raw.folder_index, []).append(member)
        for folder_index, links in by_folder.items():
            self._sweep_folder_links(folder_index, links)

    def _sweep_folder_links(
        self, folder_index: int, links: list[ArchiveMember]
    ) -> None:
        """Decode ``folder_index`` once, from its start, keeping each link's bytes."""
        links.sort(key=_folder_position)
        solid: SolidBlockReader | None = None

        def folder_reader(index: int, member: ArchiveMember) -> SolidBlockReader:
            nonlocal solid
            if solid is None:
                solid = SolidBlockReader(
                    self._track_decompressed(self._open_folder_stream(index, member))
                )
            return solid

        try:
            for link in links:
                self._capture_link_data(
                    link,
                    lambda link=link: self._member_stream_from_solid(
                        lambda: folder_reader(folder_index, link), link
                    ),
                )
        finally:
            if solid is not None:
                solid.close()

    def _link_data_stream(
        self, member: ArchiveMember
    ) -> ContextManager[ReadableStream]:
        """Where ``_ensure_link_target`` reads ``member``'s bytes from.

        Bytes read ahead (a listing sweep, a streaming pass) come first; then the data
        pass sitting on this member, through its own decoder; then a direct open, which
        decodes the folder from its start (``open()`` following a link, a listing of
        one link).
        """
        member_id = member._member_id
        kept = self._link_data.pop(member_id, None) if member_id is not None else None
        if isinstance(kept, ArchiveyError):
            raise kept
        if kept is not None:
            return io.BytesIO(kept)
        pass_link = self._pass_link
        if pass_link is not None and pass_link[0] is member:
            return pass_link[1]()
        return self._open_member(member)

    def _to_member(self, record: SevenZipFileRecord, index: int) -> ArchiveMember:
        member_type = self._member_type(record)
        presented_name = record.filename
        if presented_name == "":
            presented_name = _infer_nameless_member_name(self._archive_name)
        name = normalize_member_name(
            presented_name,
            member_type,
            backslash_is_separator=True,
        )
        raw_name = record.filename.encode("utf-16le", errors="surrogateescape")
        folder_index = record.folder_index
        compression = (
            self._folder_compression[folder_index] if folder_index is not None else ()
        )
        hashes: dict[HashAlgorithm, bytes] = {}
        if record.crc32 is not None:
            hashes[HashAlgorithm.CRC32] = crc32_digest(record.crc32)
        attrs = record.attributes
        is_reparse_point = _is_windows_reparse_point(attrs)
        unix_mode = (attrs >> 16) if attrs is not None and attrs >> 16 else None
        mode = stat.S_IMODE(unix_mode) if unix_mode is not None else None
        # Folder/substream indices live on ``_raw``; skip the unused public extra
        # bag so listing-limit accounting does not walk a per-member dict.
        ts_issues: list[TimestampIssue] = []
        modified = accessed = created = None
        # Hot-path shortcut only: filetime_to_datetime also treats 0/None as unset.
        # Keep these guards equivalent to that helper so ZIP (unconditional call)
        # and 7z cannot diverge if the shared 0-handling rule ever changes.
        if record.last_write_time:
            modified, issue = filetime_to_datetime(
                record.last_write_time, presented_name, field="modified"
            )
            if issue is not None:
                ts_issues.append(issue)
        if record.last_access_time:
            accessed, issue = filetime_to_datetime(
                record.last_access_time, presented_name, field="accessed"
            )
            if issue is not None:
                ts_issues.append(issue)
        if record.creation_time:
            created, issue = filetime_to_datetime(
                record.creation_time, presented_name, field="created"
            )
            if issue is not None:
                ts_issues.append(issue)
        member = ArchiveMember(
            type=member_type,
            name=name,
            raw_name=raw_name,
            size=record.uncompressed_size,
            compressed_size=record.compressed_size,
            modified=modified,
            accessed=accessed,
            created=created,
            mode=mode,
            compression=compression,
            is_encrypted=record.is_encrypted,
            create_system=CreateSystem.UNIX
            if unix_mode is not None
            else CreateSystem.WINDOWS_NTFS,
            windows_attrs=attrs & 0xFFFF if attrs is not None else None,
            hashes=hashes,
            extra=MemberExtra({EXTRA_IS_REPARSE_POINT: True})
            if is_reparse_point
            else MemberExtra(),
            _raw=_MemberRaw(record, folder_index, record.file_in_folder),
        )
        # Every report below names the member by `index`, its position in the walk,
        # which is the id registration will stamp on it.
        emit_member_name_normalized(
            self._diagnostics_collector,
            member=member,
            presented_name=presented_name,
            archive_name=self._archive_name,
            member_id=index,
        )
        if is_reparse_point and member.size == 0:
            # A writer that stores no data for a reparse point has recorded no target
            # for it, and that is knowable from the header alone — no read, and so no
            # dependence on this being a seekable pass. Deciding it here rather than in
            # the link-target hook is what makes streaming agree: that hook runs at EOF,
            # after extraction has already decided what to do with the member, which
            # left a 7-Zip junction raising instead of taking the recorded outcome.
            self._apply_reparse_data(
                member,
                b"",
                fallback_type=self._member_type_ignoring_reparse(record),
                member_id=index,
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
                    source="ntfs",
                    value_repr=issue.value_repr,
                ),
                member=member,
                attach_to_member=True,
                logger=logger,
            )
        # Encrypted folder with no folder digest and no per-member CRC: 7zAES has no
        # password check of its own, so a wrong password cannot be detected (matches
        # 7-Zip). Surface that as DIGEST_UNVERIFIABLE rather than silently implying
        # the decryption was authenticated.
        if (
            record.is_encrypted
            and record.crc32 is None
            and folder_index is not None
            and not self._archive.folders[folder_index].digest_defined
        ):
            self._diagnostics_collector.emit(
                code=DiagnosticCode.DIGEST_UNVERIFIABLE,
                message=(
                    "Encrypted 7z member has no folder digest and no member CRC; "
                    "decryption cannot be authenticated (wrong passwords may go "
                    "undetected on store/copy streams)."
                ),
                context=DigestContext(
                    archive_name=self._archive_name,
                    member_name=member.name,
                    member_id=index,
                    algorithm="",
                    reason="no_integrity_anchor",
                ),
                member=member,
                attach_to_member=True,
                logger=integrity_logger,
            )
        return member

    def _member_type(self, record: SevenZipFileRecord) -> MemberType:
        if record.is_anti:
            return MemberType.ANTI
        attrs = record.attributes
        if attrs is not None:
            unix_mode = attrs >> 16
            if unix_mode:
                if stat.S_ISLNK(unix_mode):
                    return MemberType.SYMLINK
                if stat.S_ISDIR(unix_mode):
                    return MemberType.DIRECTORY
            if attrs & _WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT:
                # Provisional. The bit says the entry was a reparse point on the source
                # filesystem, not that the tag named a link — the tag is in the member's
                # data, so `_ensure_link_target` reads it and reverts to the type below
                # when the data turns out not to be a link buffer.
                return MemberType.SYMLINK
        return self._member_type_ignoring_reparse(record)

    def _member_type_ignoring_reparse(self, record: SevenZipFileRecord) -> MemberType:
        """What the entry is by everything except the reparse-point attribute bit."""
        return MemberType.DIRECTORY if record.is_directory else MemberType.FILE

    def _folder_pack_view(self, folder_index: int) -> BinaryIO:
        folder = self._archive.folders[folder_index]
        pack_count = len(folder.packed_indices)
        if pack_count != 1:
            raise UnsupportedFeatureError(
                "7z folders with multiple packed streams are not supported"
            )
        pack_index = self._folder_pack_starts[folder_index]
        if pack_index >= len(self._archive.pack_sizes):
            raise CorruptionError("7z folder references a missing packed stream")
        pack_offset = self._archive.pack_pos + self._archive.pack_positions[pack_index]
        pack_size = self._archive.pack_sizes[pack_index]
        return self._view(pack_offset, pack_size)

    def _folder_members_total_size(self, folder_index: int) -> int:
        """Sum of the listed members' sizes in the folder.

        Not the coder graph's unpack size (``sevenzip_parser.folder_unpack_size``).
        The two agree for any folder that has members: the parser rejects a folder
        whose substreams leave bytes unaccounted for, and skips a folder declaring
        zero substreams, which never reaches this helper. This is the one the
        per-member CRC walk is built from.
        """
        members = self._folder_members.get(folder_index, [])
        return sum(_member_stream_size(member) for member in members)

    def _open_folder_stream(
        self,
        folder_index: int,
        member: ArchiveMember | None,
        *,
        seekable: bool = False,
        track_output: bool = False,
    ) -> BinaryIO:
        folder = self._archive.folders[folder_index]
        password = self._password_for_folder(folder_index, member)
        stream = open_folder_pipeline(
            self._folder_pack_view(folder_index),
            folder,
            password=password,
            key_cache=self._key_cache,
            stream_config=self._stream_config,
            collector=self._diagnostics_collector,
            seekable=seekable,
        )
        # Random ``open()`` passes track_output=True so each from-start folder decode
        # counts; sequential ``_iter_with_data`` already wraps once around SolidBlockReader.
        if track_output:
            return self._track_decompressed(stream)
        return stream

    def _password_for_folder(
        self, folder_index: int, member: ArchiveMember | None
    ) -> bytes | None:
        folder = self._archive.folders[folder_index]
        if not folder_is_encrypted(folder):
            return None
        if folder_index in self._folder_passwords:
            return self._folder_passwords[folder_index]

        member_digests: list[tuple[int, int | None]] = []
        for folder_member in self._folder_members.get(folder_index, []):
            size = _member_stream_size(folder_member)
            raw_expected = (
                folder_member.hashes.get(HashAlgorithm.CRC32)
                if folder_member.hashes
                else None
            )
            if isinstance(raw_expected, bytes):
                expected: int | None = int.from_bytes(raw_expected, "big") & 0xFFFFFFFF
            else:
                expected = None
            member_digests.append((size, expected))

        def confirm(password: bytes) -> bytes:
            kdf_password = _password_to_kdf_bytes(password)
            stream = open_folder_pipeline(
                self._folder_pack_view(folder_index),
                folder,
                password=kdf_password,
                key_cache=self._key_cache,
                stream_config=self._stream_config,
                collector=self._diagnostics_collector,
            )
            try:
                # AES has no check value. Confirm by decoding and CRCing, in
                # chunks: materialising the folder peaked at ~3× unpack size.
                _verify_decoded_folder(
                    folder,
                    stream,
                    expected_size=self._folder_members_total_size(folder_index),
                    member_digests=member_digests,
                )
                return kdf_password
            except (
                UnsupportedFeatureError,
                PackageNotInstalledError,
                ResourceLimitError,
                _AesCbcTruncatedError,
            ):
                # Hostile NumCyclesPower / missing cryptography / a spent
                # key-derivation budget / an AES-CBC mid-block truncation must
                # not look like a wrong password.
                # Other TruncatedError (PPMd "File is truncated" on
                # wrong-key garbage) remaps below: PasswordManager.attempt
                # advances only on EncryptionError.
                raise
            except ArchiveyError as exc:
                raise _WrongPassword("Wrong password or corrupt 7z folder") from exc
            finally:
                stream.close()

        try:
            password = self._passwords.attempt(member, confirm)
        except _PasswordCandidatesExhausted as exc:
            raise EncryptionError(raw_message_of(exc)) from exc
        self._folder_passwords[folder_index] = password
        return password

    def _member_prefix(self, member: ArchiveMember) -> int:
        raw = member._raw
        assert isinstance(raw, _MemberRaw)
        if raw.folder_index is None or raw.file_in_folder is None:
            return 0
        prior = self._folder_members.get(raw.folder_index, [])[: raw.file_in_folder]
        return sum(_member_stream_size(p) for p in prior)

    def _wrap_folder_member(
        self, inner: BinaryIO, member: ArchiveMember
    ) -> ArchiveStream:
        verify = member.size is not None or bool(member.hashes)
        return self._wrap_member_stream(
            inner,
            member.name,
            size=member.size,
            track_output=False,
            expected_hashes=member.hashes if verify else None,
            expected_size=member.size if verify else None,
            verify_member=member if verify else None,
        )

    def _member_stream_from_solid(
        self, open_solid: Callable[[], SolidBlockReader], member: ArchiveMember
    ) -> ArchiveStream:
        """Hand out a stream whose folder decode and positioning run on first read.

        ``open_solid`` opens the member's 7z folder — decompressor setup, and the
        password confirmation for an encrypted folder — the first time any member of
        that folder is actually read. A folder whose members are all skipped is never
        decoded and never asks for a password. Within an opened folder,
        ``open_member(..., lazy=True)`` defers the skip past unread earlier members in
        the same way. Verification is fused into the outer ``ArchiveStream``: a
        never-opened handle skips verify on close, so unread members do not force
        either step.
        """
        prefix = self._member_prefix(member)
        size = _member_stream_size(member)
        verify = member.size is not None or bool(member.hashes)

        return self._wrap_member_stream(
            None,
            member.name,
            open_fn=lambda: open_solid().open_member(prefix, size, lazy=True),
            size=member.size,
            track_output=False,
            seekable=False,
            expected_hashes=member.hashes if verify else None,
            expected_size=member.size if verify else None,
            verify_member=member if verify else None,
        )

    def _translate_exception(self, exc: Exception) -> ArchiveyError | None:
        if isinstance(exc, EOFError):
            return TruncatedError("7z folder ended before the requested member")
        return None

    def _ensure_link_target(self, member: ArchiveMember) -> None:
        if member.type != MemberType.SYMLINK or member.link_target is not None:
            return
        raw = member._raw
        assert isinstance(raw, _MemberRaw)
        # Two kinds of member reach this point. A Unix symlink (S_ISLNK in the high
        # word of `attributes`) stores its target as plain bytes. A Windows reparse
        # point stores a REPARSE_DATA_BUFFER, whose first field is the tag that
        # separates a junction from a symlink; decoding that as UTF-8 reports the
        # buffer itself as the target, which is what this used to do.
        is_reparse_point = _is_windows_reparse_point(raw.record.attributes)
        # What the member would be if its data turns out not to be a link buffer: the
        # attribute bit is set for deduplication stubs and cloud placeholders too, and
        # those hold ordinary content that a caller should still be able to read.
        fallback_type = self._member_type_ignoring_reparse(raw.record)
        # The zero-data case does not appear here: `_to_member` settles it while the
        # member is being typed, so this hook is never reached for one.
        # The read is capped (`_read_link_target_data`): the data is compressed, so an
        # uncapped read let a small archive decode to gigabytes here.
        try:
            data = self._read_link_target_data(
                member,
                lambda: self._link_data_stream(member),
                is_reparse_point=is_reparse_point,
            )
        except EncryptionError:
            # A 7z symlink's target is its file data, so without the password there is
            # nothing to decode. Listing has to stay usable without one, so the member
            # keeps its type and the reason travels on the diagnostics channel instead
            # — silence here would make extraction skip the link with no explanation.
            self._emit_link_target_unavailable(
                member,
                reason="password_required",
                message=(
                    f"Cannot read the symlink target of {quoted(member.name)} without the "
                    f"correct password; leaving link_target unset."
                ),
                # The archive does carry the target; it is locked, not missing. So this
                # member fails the way the encrypted file next to it does, rather than
                # disappearing from the output under a status that reads as success.
                target_in_archive=True,
            )
            return
        if data is None:
            return
        if is_reparse_point:
            self._apply_reparse_data(member, data, fallback_type=fallback_type)
        else:
            member.link_target = data.decode("utf-8", errors="surrogateescape")

    def _open_member(self, member: ArchiveMember) -> ArchiveStream:
        raw = member._raw
        assert isinstance(raw, _MemberRaw)
        if raw.folder_index is None:
            return self._wrap_member_stream(
                io.BytesIO(b""), member.name, size=member.size
            )
        want_seekable = self._stream_config.seekable
        prefix = self._member_prefix(member)
        size = _member_stream_size(member)
        folder_stream = self._open_folder_stream(
            raw.folder_index,
            member,
            seekable=want_seekable,
            track_output=True,
        )
        try:
            # A seekable folder stream leaves positioning to the slice's first read
            # and to the codec (free on a stored folder, decode-and-discard inside
            # the codec otherwise), and the slice stays seekable. A forward-only
            # one is skipped to the prefix here.
            seek_to_prefix = want_seekable and is_seekable(folder_stream)
            if not seek_to_prefix:
                skip_forward(folder_stream, prefix)
            inner: BinaryIO = SlicingStream(
                folder_stream,
                start=prefix if seek_to_prefix else None,
                length=size,
                owns_inner=True,
            )
        except EOFError as exc:
            # Construction no longer seeks, so a truncated folder raises from the
            # first read (or from skip_forward), not from SlicingStream.__init__.
            # EOFError is still translated by _translate_exception; this handler
            # covers skip_forward and keeps the close-on-failure pairing.
            folder_stream.close()
            raise TruncatedError("7z folder ended before the requested member") from exc
        except BaseException:
            folder_stream.close()
            raise
        try:
            return self._wrap_folder_member(inner, member)
        except BaseException:
            inner.close()
            raise

    def _get_archive_info(self) -> ArchiveInfo:
        solid_blocks = sum(
            1 for members in self._folder_members.values() if len(members) > 1
        )
        cost = CostReceipt(
            listing_cost=ListingCost.INDEXED,
            access_cost=AccessCost.SOLID
            if self._archive.is_solid
            else AccessCost.DIRECT,
            stream_capability=StreamCapability.SEEKABLE,
            solid_block_count=solid_blocks if self._archive.is_solid else None,
        )
        info_extra = ArchiveInfoExtra({"7z.volume_count": self._volume_count})
        return ArchiveInfo(
            format=ArchiveFormat.SEVEN_Z,
            format_version=f"{self._archive.major_version}.{self._archive.minor_version}",
            is_solid=self._archive.is_solid,
            member_count=len(self._members),
            comment=self._archive.comment,
            is_encrypted=self._archive.is_header_encrypted
            or self._archive.has_encrypted_folders,
            is_multivolume=self._volume_count > 1,
            cost=cost,
            extra=info_extra,
        )

    def _close_archive(self) -> None:
        self._shared.close()


class SevenZipReadBackend(ReadBackend):
    """Backend factory for 7z archives."""

    FORMATS: tuple[ArchiveFormat, ...] = (ArchiveFormat.SEVEN_Z,)
    EXTENSIONS: Mapping[str, ArchiveFormat] = {
        ".7z": ArchiveFormat.SEVEN_Z,
        ".cb7": ArchiveFormat.SEVEN_Z,
    }
    MAGIC: tuple[MagicSignature, ...] = (
        MagicSignature(0, b"7z\xbc\xaf'\x1c", ArchiveFormat.SEVEN_Z),
    )
    SFX_MAGIC: tuple[MagicSignature, ...] = MAGIC
    SFX_HIT_VALIDATOR = staticmethod(validate_sevenzip_signature_header)
    SUPPORTS_PASSWORD = True
    SUPPORTS_STREAMING_NON_SEEKABLE = False
    OPTIONAL_DEPENDENCY = None

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
    ) -> SevenZipReader:
        del format
        return SevenZipReader(
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


register_reader(SevenZipReadBackend)

# Re-exports used by fuzz harnesses / older imports.
__all__ = [
    "SevenZipReadBackend",
    "SevenZipReader",
    "decode_folder_to_bytes",
    "open_folder_pipeline",
]
