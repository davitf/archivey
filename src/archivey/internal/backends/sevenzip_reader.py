"""Native 7z reader backend (``BaseArchiveReader`` wiring).

Module split:

- :mod:`.sevenzip_methods` — method-id registry / :class:`MethodKind`
- :mod:`.sevenzip_parser` — signature + header property tree → :class:`SevenZipArchive`
- :mod:`.sevenzip_pipeline` — folder coder plan/execute + encoded-header decode
- this module — passwords, member list, solid-folder demux, CRC/encryption mapping

Open path: signature → ``parse_header_block`` → (one encoded-header layer) →
``materialize_archive`` → list members. Member open folds the folder's packed
slices (one view per pack stream; a BCJ2 folder has four) through
:func:`open_folder_pipeline`; solid folders use
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
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, ExitStack
from dataclasses import dataclass
from typing import BinaryIO

from archivey.config import ArchiveyConfig
from archivey.cost import AccessCost, CostReceipt, ListingCost, StreamCapability
from archivey.diagnostics import (
    ArchiveEofContext,
    DiagnosticCode,
    DigestContext,
)
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
from archivey.internal.backends.sevenzip_detect import (
    validate_sevenzip_signature_header,
)
from archivey.internal.backends.sevenzip_methods import is_aes, lookup
from archivey.internal.backends.sevenzip_parser import (
    EncodedHeader,
    FolderGraph,
    PlainHeader,
    SevenZipArchive,
    SevenZipCoder,
    SevenZipFileRecord,
    SevenZipFolder,
    compression_method_for_coder,
    empty_archive,
    find_signature_offset,
    folder_is_encrypted,
    folder_unpack_size,
    materialize_archive,
    packed_streams_end,
    parse_decoded_header,
    parse_header_block,
    read_signature_and_next_header,
)
from archivey.internal.backends.sevenzip_pipeline import (
    HEADER_PASSWORD_REJECTED,
    decode_encoded_header,
    decode_folder_to_bytes,
    encoded_header_needs_password,
    open_folder_pipeline,
)
from archivey.internal.base_reader import BaseArchiveReader, ReadBackend
from archivey.internal.config import (
    KeyDerivationBudget,
    stream_config_from_archivey,
)
from archivey.internal.diagnostics_collector import DiagnosticCollector
from archivey.internal.file_copy_pass import DEFAULT_FILE_COPY_PASS, FileCopyPass
from archivey.internal.logs import backends as backends_logger
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
    wrong_password_error,
)
from archivey.internal.password_confirm import (
    PASSWORD_CONFIRM_MAX_INPUT_BYTES,
    PASSWORD_CONFIRM_PREFIX_BYTES,
    REJECTING_CODECS,
    PasswordConfirmPlan,
    PasswordConfirmVerdict,
    attempt_with_confirm,
    plan_password_confirm,
    run_password_confirm_plan,
)
from archivey.internal.registry import register_reader
from archivey.internal.source import ArchiveSource
from archivey.internal.streams.archive_stream import ArchiveStream
from archivey.internal.streams.crypto import _AesCbcTruncatedError
from archivey.internal.streams.streamtools import (
    ReadableStream,
    ReadOnlyIOStream,
    SharedSource,
    SlicingStream,
    SolidBlockReader,
    is_seekable,
    skip_forward,
)
from archivey.internal.timestamps import TimestampIssue, filetime_to_datetime
from archivey.internal.trailing_scan import first_nonzero_offset
from archivey.internal.unix_mode import UNIX_FILE_TYPE_MASK, is_special_file_mode
from archivey.internal.windows_reparse import parse_reparse_data
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
_FILE_ATTRIBUTE_UNIX_EXTENSION = 0x8000


def _written_on_unix(attrs: int | None) -> bool:
    """True when 7z's attribute word carries a real Unix mode, file type included.

    7-Zip and p7zip writing on Unix, where "Created" is filled from st_ctime, set
    FILE_ATTRIBUTE_UNIX_EXTENSION (``0x8000``) and put ``st_mode`` in the high word.
    Neither signal alone identifies the writer: ``0x8000`` is also Windows
    FILE_ATTRIBUTE_INTEGRITY_STREAM (ReFS), and Windows has attributes above
    ``0xFFFF`` (PINNED ``0x80000``, which OneDrive sets, and others) that make the
    high word non-zero. A Windows word never has ``S_IFMT`` bits there, while every
    Unix ``st_mode`` does, so the file type is the test.
    """
    return attrs is not None and bool((attrs >> 16) & UNIX_FILE_TYPE_MASK)


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


# ``\Z``, not ``$``: ``$`` also matches before a final newline, which would strip the
# suffix from ``name.7z\n`` and leave the newline in the member name.
_SEVENZIP_STEM_SUFFIX_RE = re.compile(r"\.7z(?:\.\d{3})?\Z", re.IGNORECASE)


# The folder settles a wrong key inside the confirm prefix when a REJECTING_CODECS
# codec decodes the AES output. Filters never reject: ``MethodKind.LZMA_FAMILY`` also
# holds Delta and BCJ, which is why the check is by codec and not by method kind.
def _folder_codec_rejects(folder: SevenZipFolder) -> bool:
    """Whether a decoder of the decrypted bytes rejects random input (confirm rung 3).

    Only a coder downstream of an AES coder in decode order (it reads what AES
    decrypted, directly or through other coders) sees wrong-key garbage. A codec
    that decodes before AES reads the same bytes whatever the key, so it cannot
    reject one. Listing never validates the graph, so the walk tolerates any wiring.
    """
    coders = folder.coders
    graph = FolderGraph.of(folder)
    pending = [index for index, coder in enumerate(coders) if is_aes(coder.method)]
    seen: set[int] = set()
    while pending:
        for index in graph.consumers(pending.pop()):
            if index in seen:
                continue
            seen.add(index)
            method = lookup(coders[index].method)
            if method is not None and method.codec in REJECTING_CODECS:
                return True
            pending.append(index)
    return False


def _compression_coders(folder: SevenZipFolder) -> list[SevenZipCoder]:
    """The folder's coders from its output down each coder's first input.

    Listing never validates the graph (only decoding does, in ``plan_folder``), so this
    walk tolerates any wiring: it stops at a pack stream, an unbound input, a coder
    with no input or a coder it has already visited. A graph with no single output
    falls back to the coder list reversed, which is the same order for a linear chain
    written in list order.
    """
    coders = folder.coders
    graph = FolderGraph.of(folder)
    roots = graph.roots()
    if len(roots) != 1 or any(c.num_out_streams != 1 for c in coders):
        return list(reversed(coders))
    walk: list[SevenZipCoder] = []
    seen: set[int] = set()
    index: int | None = roots[0]
    while index is not None and index not in seen:
        seen.add(index)
        walk.append(coders[index])
        first_input = graph.inputs[index][:1]
        index = graph.producer(first_input[0]) if first_input else None
    return walk


@dataclass(frozen=True)
class _MemberRaw:
    record: SevenZipFileRecord
    folder_index: int | None
    file_in_folder: int | None
    #: Where the member's bytes start in its folder's output: the sum of the earlier
    #: members' sizes in that folder. Zero for a member with no folder.
    folder_prefix: int


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


class _LazyFolder:
    """A folder's ``SolidBlockReader``, opened on the first read into it.

    It holds one folder at a time: a caller moving to another folder must ``close()``.
    """

    def __init__(self, open_folder: Callable[[int, ArchiveMember], BinaryIO]) -> None:
        self._open_folder = open_folder
        self._solid: SolidBlockReader | None = None
        self._index: int | None = None

    def get(self, folder_index: int, member: ArchiveMember) -> SolidBlockReader:
        assert self._index is None or self._index == folder_index
        if self._solid is None:
            self._solid = SolidBlockReader(self._open_folder(folder_index, member))
            self._index = folder_index
        return self._solid

    def close(self) -> None:
        if self._solid is not None:
            self._solid.close()
            self._solid = None
            self._index = None


class _ReadAheadStream(ReadOnlyIOStream):
    """A member's content when its first bytes were already read from its stream.

    Gives ``head``, then the rest of ``rest``. ``rest`` is the member's own stream,
    which verifies the member's size and CRC once it is read to its end. Owns
    ``rest``. Forward-only, like the folder decode under it.

    ``read(n)`` is full-count, as ``ArchiveStream`` requires of its inner stream: it
    returns ``n`` bytes unless the member ends (ADR 0014). A read that crosses the end
    of ``head`` takes the remainder from ``rest`` in the same call. One call is
    enough, because ``rest`` is full-count too.
    """

    def __init__(self, head: bytes, rest: BinaryIO) -> None:
        super().__init__()
        self._rest = rest
        self._head = head

    def read(self, n: int = -1, /) -> bytes:
        if self.closed:
            raise ValueError("I/O operation on closed file.")
        if not self._head:
            return self._rest.read(n)
        if n < 0:
            data = self._head + self._rest.read()
            self._head = b""
            return data
        data, self._head = self._head[:n], self._head[n:]
        if len(data) < n:
            data += self._rest.read(n - len(data))
        return data

    def close(self) -> None:
        if self.closed:
            return
        try:
            self._rest.close()
        finally:
            super().close()


class SevenZipReader(BaseArchiveReader):
    """Reads 7z archives using the native parser and shared codec streams."""

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
        # Folders whose password was accepted on an INCONCLUSIVE confirm: no checksum
        # vouched for the key, so a member stream abandoned before its digest reports
        # ``ENCRYPTED_MEMBER_UNVERIFIED``.
        self._folders_unconfirmed: set[int] = set()
        # A symlink's target is its member data, usually mid-way through a solid
        # folder, so link bytes are read ahead of resolution, a folder at a time
        # (``format-7z``, "A 7z folder is decoded at most once for its link targets"):
        # by listing's folder sweep, or by a pass in either mode as its cursor reaches
        # the link; an abandoned pass leaves its entries for a later listing or pass.
        # Keyed by member id. A value that is an exception is the failed read,
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
        self._archive_end = 0
        self._archive = self._load_archive()
        self._init_folder_caches(self._archive)
        self._members = self._build_members()
        self._folder_members = self._members_by_folder()
        self._report_trailing_data()

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
        self._archive_end = signature.end_offset
        if not signature.header_data:
            return empty_archive(signature)

        max_members = self._config.listing_limits.max_members
        block = parse_header_block(signature.header_data, max_members=max_members)
        header_encrypted = False
        if isinstance(block, EncodedHeader):
            header_encrypted = encoded_header_needs_password(block)
            self._archive_end = max(
                self._archive_end, packed_streams_end(block.streams)
            )
            block = self._decode_encoded_header_block(
                fp, block, max_members=max_members
            )
        assert isinstance(block, PlainHeader)
        self._archive_end = max(self._archive_end, packed_streams_end(block.streams))
        return materialize_archive(
            signature, block, is_header_encrypted=header_encrypted
        )

    def _report_trailing_data(self) -> None:
        """Report a non-zero byte after the end of a 7z archive.

        The end is the later of the next header's end and the last packed stream's.
        ``ARCHIVE_TRAILING_DATA`` with ``expected_marker="zeros_to_eof"``, as after a
        TAR trailer: a warning by default, refused under ``DiagnosticPolicy.strict()``
        (DR-3). Zero padding is silent under DR-3; 7-Zip warns about any tail, zeros
        included. Runs after the header has parsed, so a wrong header password or a
        damaged header is reported as that, not as this. A self-extractor's tail (the
        certificate table of a code-signed one) is reported too: it is outside the
        archive whatever wrote it.
        """
        fp = self._view(self._archive_end)
        try:
            found = first_nonzero_offset(fp)
        finally:
            fp.close()
        if found is None:
            return
        self._diagnostics_collector.emit(
            code=DiagnosticCode.ARCHIVE_TRAILING_DATA,
            message=(
                "7z archive continues past its end: a non-zero byte appears "
                f"{found} bytes after the end of its next header and packed streams. "
                "The listing does not account for it (this file may hold something "
                "appended to the archive)."
            ),
            context=ArchiveEofContext(
                archive_name=self._archive_name,
                format="7z",
                expected_marker="zeros_to_eof",
                expected_bytes=0,
                observed_bytes=found,
                observed_kind="nonzero",
            ),
            logger=backends_logger,
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
            except CorruptionError as exc:
                raise EncryptionError(HEADER_PASSWORD_REJECTED) from exc
            try:
                plain = parse_decoded_header(decoded, max_members=max_members)
            except (
                CorruptionError,
                UnsupportedFeatureError,
            ) as exc:
                raise EncryptionError(HEADER_PASSWORD_REJECTED) from exc
            # O8: 7zAES has no password check value. Wrong-key garbage occasionally
            # LZMA-decodes into a header that parses with zero file records (py7zr
            # omits the encoded-header folder CRC). Legitimate writers never encrypt
            # an empty header — treat that as a rejected password.
            if not plain.files:
                raise EncryptionError(HEADER_PASSWORD_REJECTED)
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
            raise EncryptionError(HEADER_PASSWORD_REJECTED) from exc

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

        ``ArchiveMember.compression`` is in compress order: pre-filters first, the
        packing codec last. :func:`_compression_coders` walks the coder graph from the
        folder's output down each coder's first input, which is that order. For a
        linear chain that is every coder (7-Zip's ``-mf=BCJ`` reads ``BCJ LZMA2``). For
        BCJ2 it is BCJ2, then its ``main`` branch: ``(BCJ2, LZMA2)``. The ``call``,
        ``jump`` and ``rc`` side streams are part of BCJ2, not codecs the member was
        packed with, so they are not listed.

        A coder the registry does not know is listed as ``UNKNOWN`` rather than
        dropped, so the chain never looks shorter than it is. AES is left out: it
        is encryption, reported through ``is_encrypted``.
        """
        out: list[tuple[CompressionMethod, ...]] = []
        # A non-solid archive has one folder per member, nearly all wired alike, so
        # the chain is built once per distinct wiring and the tuple shared. The key
        # holds every field the chain is derived from.
        by_wiring: dict[object, tuple[CompressionMethod, ...]] = {}
        for folder in archive.folders:
            key = (
                tuple(
                    (c.method, c.num_in_streams, c.num_out_streams, c.properties)
                    for c in folder.coders
                ),
                tuple(folder.bind_pairs),
            )
            chain = by_wiring.get(key)
            if chain is None:
                chain = by_wiring[key] = tuple(
                    compression_method_for_coder(coder)
                    for coder in _compression_coders(folder)
                    if not is_aes(coder.method)
                )
            out.append(chain)
        return out

    def _build_members(self) -> list[ArchiveMember]:
        # is_current is stamped by BaseArchiveReader's shared last-entry-wins pass.
        # One walk carries a running sum per folder, so each member's folder offset
        # costs one addition. The parser assigns a folder's members in file order,
        # which is also the order their bytes appear in the folder's output.
        folder_ends: dict[int, int] = {}
        members: list[ArchiveMember] = []
        for index, record in enumerate(self._archive.files):
            folder_index = record.folder_index
            prefix = 0
            if folder_index is not None:
                prefix = folder_ends.get(folder_index, 0)
                folder_ends[folder_index] = prefix + (record.uncompressed_size or 0)
            members.append(self._to_member(record, index, folder_prefix=prefix))
        return members

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

    def _iter_with_data(
        self, copies: FileCopyPass = DEFAULT_FILE_COPY_PASS
    ) -> Iterator[tuple[ArchiveMember, ArchiveStream | None]]:
        current_folder: int | None = None
        folder = self._lazy_folder()

        def _enter_folder(folder_index: int) -> None:
            nonlocal current_folder
            if folder_index != current_folder:
                folder.close()
                current_folder = folder_index

        def _open(member: ArchiveMember) -> ArchiveStream | None:
            self._pass_link = None
            raw = member._raw
            assert isinstance(raw, _MemberRaw)
            if not member.is_file:
                if member.type is MemberType.SYMLINK and raw.folder_index is not None:
                    _enter_folder(raw.folder_index)
                    content = self._reach_pass_link(
                        member, raw.folder_index, folder.get
                    )
                    if content is not None:
                        return self._register_public_stream(content)
                return None
            # Registered like the base class's lazy pass streams, so the pass takes
            # the one live-stream slot and is refused beside a live ``open()``.
            if raw.folder_index is None:
                return self._register_public_stream(self._empty_member_stream(member))
            _enter_folder(raw.folder_index)
            folder_index = raw.folder_index
            return self._register_public_stream(
                self._member_stream_from_solid(
                    lambda: folder.get(folder_index, member), member
                )
            )

        def _cleanup() -> None:
            self._pass_link = None
            # Captured link bytes are kept even when the pass is abandoned: the archive's
            # bytes are fixed, and a later listing or pass resolves from them instead of
            # decoding the folder again. What stays is one entry per link the pass
            # walked, each bounded by the target cap, released as each link resolves
            # (or when the reader closes).
            folder.close()

        yield from self._drive_pass_streams(
            self._listed_members(),
            open_member=_open,
            close_previous=True,
            cleanup=_cleanup,
        )

    def _lazy_folder(self) -> _LazyFolder:
        # Count at the folder decode layer (solid invariant); member wraps pass
        # track_output=False so sequential reads are not double-counted.
        return _LazyFolder(
            lambda index, member: self._track_decompressed(
                self._open_folder_stream(index, member)
            )
        )

    def _reach_pass_link(
        self,
        member: ArchiveMember,
        folder_index: int,
        folder_reader: Callable[[int, ArchiveMember], SolidBlockReader],
    ) -> ArchiveStream | None:
        """A data pass has reached ``member``, a symlink whose target is its data.

        The pass yields no stream for it, and its folder decoder only moves when a later
        member is read, so without this the bytes go by unread and EOF finalization
        would decode the folder again to get them. A pass under ``read_link_targets``
        (streaming or random access; both finalize links at the end) reads them now,
        through its own decoder, and keeps them for finalization. Otherwise the pass
        only offers its decoder for this one member, which is how ``extract_all``
        reads an accepted link without a second decode.

        Returns the member's content when those bytes show it is a file and not a link
        (see ``_capture_link_data``); the pass yields that stream with it.
        """

        def opener() -> ArchiveStream:
            return self._member_stream_from_solid(
                lambda: folder_reader(folder_index, member), member
            )

        if (
            self._config.read_link_targets
            and member.link_target is None
            and not member._link_target_resolved
        ):
            return self._capture_link_data(member, opener, in_pass=True)
        self._pass_link = (member, opener)
        return None

    def _capture_link_data(
        self,
        member: ArchiveMember,
        opener: Callable[[], ArchiveStream],
        *,
        in_pass: bool = False,
    ) -> ArchiveStream | None:
        """Read ``member``'s link bytes now and keep them for its resolution.

        Reads what ``_read_link_target_data`` would (and nothing for a member it refuses
        by size), so resolving over the kept bytes answers exactly as a direct read.
        A read that fails is kept as its exception and raised again then.

        ``in_pass``: the caller is a data pass about to yield ``member``. If the bytes
        are a reparse point's and are not a link buffer, the member is resolved now,
        which re-types it to a file, as listing does. The pass then has to yield its
        content: its decoder is past these bytes and cannot go back. So this returns
        a stream that gives the bytes already read and then the rest of the member.
        Deciding only at EOF dropped that content without an error.
        """
        member_id = member._member_id
        assert member_id is not None
        if member_id in self._link_data:
            return None
        raw = member._raw
        assert isinstance(raw, _MemberRaw)
        is_reparse_point = _is_windows_reparse_point(raw.record.attributes)
        if self._link_data_refused_by_size(member, is_reparse_point=is_reparse_point):
            return None
        with ExitStack() as owned:
            stream = owned.enter_context(opener())
            try:
                data = self._read_bounded_link_data(
                    stream, is_reparse_point=is_reparse_point
                )
            except ArchiveyError as exc:
                self._link_data[member_id] = exc
                return None
            self._link_data[member_id] = data
            if not (
                in_pass
                and is_reparse_point
                and bool(data)
                and parse_reparse_data(data) is None
            ):
                return None
            self._resolve_link_target(member)
            if member.type is not MemberType.FILE:
                # A directory-shaped entry stays a link (`_apply_reparse_data`).
                return None
            content = self._wrap_member_stream(
                _ReadAheadStream(data, stream),
                member.name,
                size=member.size,
                track_output=False,
                seekable=False,
            )
            # The returned stream owns ``stream`` now.
            owned.pop_all()
            return content

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
        folder = self._lazy_folder()
        try:
            for link in links:
                self._capture_link_data(
                    link,
                    lambda link=link: self._member_stream_from_solid(
                        lambda: folder.get(folder_index, link), link
                    ),
                )
        finally:
            folder.close()

    def _link_data_stream(
        self, member: ArchiveMember
    ) -> AbstractContextManager[ReadableStream]:
        """Where ``_ensure_link_target`` reads ``member``'s bytes from.

        Bytes read ahead (a listing sweep, a pass in either mode) come first; then the data
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

    def _to_member(
        self, record: SevenZipFileRecord, index: int, *, folder_prefix: int = 0
    ) -> ArchiveMember:
        """Build the member for ``record``.

        ``folder_prefix`` is where the member's bytes start in its folder's output,
        computed by :meth:`_build_members`; it stays 0 for a member with no folder.
        """
        member_type = self._member_type(record)
        presented_name = record.filename
        if presented_name == "":
            presented_name = _infer_nameless_member_name(self._archive_name)
        name = normalize_member_name(
            presented_name,
            member_type,
            backslash_is_separator=True,
        )
        # surrogatepass undoes the parser's decode, so a lone surrogate comes back
        # as the code unit that was stored.
        raw_name = record.filename.encode("utf-16le", errors="surrogatepass")
        folder_index = record.folder_index
        compression = (
            self._folder_compression[folder_index] if folder_index is not None else ()
        )
        hashes: dict[HashAlgorithm, bytes] = {}
        if record.crc32 is not None:
            hashes[HashAlgorithm.CRC32] = crc32_digest(record.crc32)
        attrs = record.attributes
        reparse_fallback = self._reparse_fallback_type(record)
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
        # A Unix writer (7-Zip, p7zip, libarchive on Linux and macOS) fills
        # "Created" from st_ctime, which ``created`` never holds.
        written_on_unix = _written_on_unix(attrs)
        if record.creation_time:
            created, issue = filetime_to_datetime(
                record.creation_time,
                presented_name,
                field="ctime" if written_on_unix else "created",
            )
            if issue is not None:
                ts_issues.append(issue)
        extra = (
            MemberExtra({EXTRA_IS_REPARSE_POINT: True})
            if _is_windows_reparse_point(attrs)
            else MemberExtra()
        )
        ctime = None
        if created is not None and written_on_unix:
            created, ctime = None, created
        member = ArchiveMember(
            type=member_type,
            name=name,
            raw_name=raw_name,
            size=record.uncompressed_size,
            compressed_size=record.compressed_size,
            modified=modified,
            accessed=accessed,
            created=created,
            ctime=ctime,
            mode=mode,
            compression=compression,
            is_encrypted=record.is_encrypted,
            create_system=CreateSystem.UNIX
            if unix_mode is not None
            else CreateSystem.WINDOWS_NTFS,
            windows_attrs=attrs & 0xFFFF if attrs is not None else None,
            hashes=hashes,
            extra=extra,
            _raw=_MemberRaw(record, folder_index, record.file_in_folder, folder_prefix),
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
        self._settle_empty_reparse_point(
            member, reparse_fallback=reparse_fallback, member_id=index
        )
        for issue in ts_issues:
            self._emit_timestamp_invalid(member, index, issue)
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
                if attrs & _FILE_ATTRIBUTE_UNIX_EXTENSION and is_special_file_mode(
                    unix_mode
                ):
                    # A device, FIFO or socket (7-Zip and p7zip store them with no
                    # data). Unlike the symlink and directory tests above, this one
                    # also needs 0x8000. A Windows attribute above 0xFFFF can land on
                    # a low file-type value: STRICTLY_SEQUENTIAL (0x20000000) reads as
                    # S_IFCHR, while no defined attribute reaches S_IFDIR (0x4000) or
                    # S_IFLNK (0xA000). Misreading a Windows file as a device would
                    # make it unextractable, so a high word without the flag stays
                    # FILE here.
                    return MemberType.OTHER
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

    def _reparse_fallback_type(self, record: SevenZipFileRecord) -> MemberType | None:
        """For an entry flagged as a Windows reparse point, the type it reverts to when
        its data is not a link buffer (the bit is set for deduplication stubs and cloud
        placeholders too, whose content stays readable); ``None`` for any other entry.

        A Unix mode of ``S_IFDIR`` in the high word types the entry a directory ahead of
        the bit (`_member_type`), so it has no link target to settle or read.
        """
        attrs = record.attributes
        if not _is_windows_reparse_point(attrs) or (
            attrs is not None and stat.S_ISDIR(attrs >> 16)
        ):
            return None
        return self._member_type_ignoring_reparse(record)

    def _folder_pack_views(self, folder_index: int) -> list[BinaryIO]:
        """One view per pack stream of the folder, in ``packed_indices`` order.

        A BCJ2 folder has four; its decoder reads them all at once, so each needs its
        own position on the shared source.
        """
        folder = self._archive.folders[folder_index]
        first = self._folder_pack_starts[folder_index]
        views: list[BinaryIO] = []
        for pack_index in range(first, first + len(folder.packed_indices)):
            if pack_index >= len(self._archive.pack_sizes):
                raise CorruptionError("7z folder references a missing packed stream")
            pack_offset = (
                self._archive.pack_pos + self._archive.pack_positions[pack_index]
            )
            views.append(self._view(pack_offset, self._archive.pack_sizes[pack_index]))
        return views

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
            self._folder_pack_views(folder_index),
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

        plan = self._folder_password_confirm_plan(folder_index)
        # The plan that walks to the folder's end anchor whatever the codec: it settles
        # a candidate the bounded plan could only call inconclusive, when several
        # candidates survive the bounded one.
        full_plan = self._folder_password_confirm_plan(folder_index, full=True)

        # The callbacks below run once per candidate password, inside
        # ``attempt_with_confirm``: each judges the candidate against the folder,
        # raising the wrong-password error to move on.
        def probe(candidate: bytes) -> tuple[bytes, PasswordConfirmVerdict]:
            kdf_password = _password_to_kdf_bytes(candidate)
            return kdf_password, self._confirm_folder_password(
                folder_index, kdf_password, plan
            )

        def full_check(candidate: bytes) -> tuple[bytes, PasswordConfirmVerdict]:
            kdf_password = _password_to_kdf_bytes(candidate)
            return kdf_password, self._confirm_folder_password(
                folder_index, kdf_password, full_plan
            )

        try:
            password, verdict = attempt_with_confirm(
                self._passwords,
                member,
                probe,
                None if full_plan == plan else full_check,
            )
        except _PasswordCandidatesExhausted as exc:
            raise EncryptionError(raw_message_of(exc)) from exc
        if verdict is PasswordConfirmVerdict.INCONCLUSIVE:
            self._folders_unconfirmed.add(folder_index)
        self._folder_passwords[folder_index] = password
        return password

    def _folder_password_confirm_plan(
        self, folder_index: int, *, full: bool = False
    ) -> PasswordConfirmPlan:
        """The confirm ladder's plan for one encrypted folder (rungs 2 and 3).

        7z AES has no password check value, so rung 1 is empty here and the ladder
        starts at the integrity anchor: member CRCs in substream order, then the folder
        digest. The earliest anchor covering at least 4 bytes wins; one past
        ``PASSWORD_CONFIRM_PREFIX_BYTES`` is walked only when no decoder in the chain rejects
        random input, or when ``full`` asks for the walk (several candidates survived
        the bounded plan, and only the anchor can tell them apart).
        """
        folder = self._archive.folders[folder_index]
        substreams: list[tuple[int, int | None]] = []
        for folder_member in self._folder_members.get(folder_index, []):
            raw_expected = (
                folder_member.hashes.get(HashAlgorithm.CRC32)
                if folder_member.hashes
                else None
            )
            expected = (
                int.from_bytes(raw_expected, "big") & 0xFFFFFFFF
                if isinstance(raw_expected, bytes)
                else None
            )
            substreams.append((_member_stream_size(folder_member), expected))
        tail_crc = (
            (folder.crc if folder.crc is not None else 0) & 0xFFFFFFFF
            if folder.digest_defined
            else None
        )
        # The folder digest covers the coder graph's unpack size, and the plan checks it
        # over the substream sum. The two agree because the parser rejects a folder
        # whose substreams leave bytes unaccounted for (and skips one declaring zero
        # substreams, which has no members to reach this). If they ever diverged, a
        # correct password would be reported as wrong, so the coupling is pinned here.
        assert tail_crc is None or sum(size for size, _ in substreams) == (
            folder_unpack_size(folder)
        ), "member sizes must sum to the folder unpack size"
        return plan_password_confirm(
            substreams,
            tail_crc,
            budget=PASSWORD_CONFIRM_PREFIX_BYTES,
            codec_rejects=not full and _folder_codec_rejects(folder),
        )

    def _confirm_folder_password(
        self, folder_index: int, kdf_password: bytes, plan: PasswordConfirmPlan
    ) -> PasswordConfirmVerdict:
        """Run ``plan`` over the folder decoded with ``kdf_password``.

        Raises the wrong-password ``EncryptionError`` on ``REJECTED`` so
        ``_PasswordCandidates.attempt`` moves to the next candidate. A bounded plan
        reads at most ``PASSWORD_CONFIRM_MAX_INPUT_BYTES`` of each packed stream: a
        block-transform codec can otherwise consume far more input than the plaintext
        prefix it produces. Running out of that capped input (a short read, or a decoder
        error, once the cap is spent) is not evidence about the key, so it is
        ``INCONCLUSIVE``. A mismatched anchor stays a rejection, however much input it
        took to produce.
        """
        folder = self._archive.folders[folder_index]
        sources: list[BinaryIO] = []
        capped: list[SlicingStream] = []
        first = self._folder_pack_starts[folder_index]
        for k, pack in enumerate(self._folder_pack_views(folder_index)):
            if (
                plan.bounded
                and self._archive.pack_sizes[first + k]
                > PASSWORD_CONFIRM_MAX_INPUT_BYTES
            ):
                # The cap is a whole number of AES blocks, so the cut never lands
                # mid-block. A BCJ2 folder caps each of its pack streams.
                cap = SlicingStream(pack, length=PASSWORD_CONFIRM_MAX_INPUT_BYTES)
                capped.append(cap)
                sources.append(cap)
            else:
                sources.append(pack)

        def input_ran_out() -> bool:
            return any(cap.tell() >= PASSWORD_CONFIRM_MAX_INPUT_BYTES for cap in capped)

        stream = open_folder_pipeline(
            sources,
            folder,
            password=kdf_password,
            key_cache=self._key_cache,
            stream_config=self._stream_config,
            collector=self._diagnostics_collector,
        )
        try:
            verdict = run_password_confirm_plan(
                stream, plan, input_exhausted=input_ran_out
            )
        except (
            UnsupportedFeatureError,
            PackageNotInstalledError,
            ResourceLimitError,
            _AesCbcTruncatedError,
        ):
            # Hostile NumCyclesPower / missing cryptography / a spent key-derivation
            # budget / an AES-CBC mid-block truncation must not look like a wrong
            # password. Other TruncatedError (PPMd "File is truncated" on wrong-key
            # garbage) remaps below: ``attempt`` advances only on EncryptionError.
            raise
        except ArchiveyError as exc:
            # Any decoder error once the cap is spent, not only ``TruncatedError``: a
            # decoder cut mid-block does not reliably say so (indexed_bzip2 raises a
            # bare ``RuntimeError``, mapped to ``CorruptionError``), so the error type
            # cannot tell the cut from a wrong key. A mismatched anchor can, and the
            # runner keeps that one ``REJECTED``.
            if input_ran_out():
                return PasswordConfirmVerdict.INCONCLUSIVE
            raise wrong_password_error("Wrong password or corrupt 7z folder") from exc
        finally:
            stream.close()
        if verdict is PasswordConfirmVerdict.REJECTED:
            raise wrong_password_error("Wrong password or corrupt 7z folder")
        return verdict

    def _watch_unverified(self, stream: BinaryIO, member: ArchiveMember) -> BinaryIO:
        """Wrap ``stream`` to report an abandoned read of an unconfirmed folder's member.

        Only folders accepted on an ``INCONCLUSIVE`` confirm are watched. A folder
        confirmed against a CRC needs no report: the key was checked, whatever the
        caller reads.
        """
        raw = member._raw
        assert isinstance(raw, _MemberRaw)
        if raw.folder_index not in self._folders_unconfirmed:
            return stream
        return self._watch_unverified_read(
            stream,
            member,
            size=_member_stream_size(member),
            check="confirm_budget_exhausted",
            format_label="7z",
            digest="checksum",
            why="no checksum confirmed the password",
        )

    def _member_prefix(self, member: ArchiveMember) -> int:
        raw = member._raw
        assert isinstance(raw, _MemberRaw)
        return raw.folder_prefix

    def _wrap_folder_member(
        self,
        inner: BinaryIO | None,
        member: ArchiveMember,
        *,
        open_fn: Callable[[], BinaryIO] | None = None,
        seekable: bool | None = None,
    ) -> ArchiveStream:
        verify = member.size is not None or bool(member.hashes)
        return self._wrap_member_stream(
            inner,
            member.name,
            open_fn=open_fn,
            size=member.size,
            track_output=False,
            seekable=seekable,
            expected_hashes=member.hashes if verify else None,
            expected_size=member.size if verify else None,
            verify_member=member if verify else None,
        )

    def _empty_member_stream(self, member: ArchiveMember) -> ArchiveStream:
        return self._wrap_member_stream(io.BytesIO(b""), member.name, size=member.size)

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
        return self._wrap_folder_member(
            None,
            member,
            open_fn=lambda: self._watch_unverified(
                open_solid().open_member(prefix, size, lazy=True), member
            ),
            seekable=False,
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
        # Two kinds of member reach this point: a Unix symlink (S_ISLNK in the high
        # word of `attributes`) and a Windows reparse point.
        self._link_target_from_data(
            member,
            lambda: self._link_data_stream(member),
            reparse_fallback=self._reparse_fallback_type(raw.record),
        )

    def _open_member(self, member: ArchiveMember) -> ArchiveStream:
        raw = member._raw
        assert isinstance(raw, _MemberRaw)
        if raw.folder_index is None:
            return self._empty_member_stream(member)
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
            inner = self._watch_unverified(inner, member)
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
