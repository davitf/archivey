"""TAR backend on the v2 ABC, over archivey's own header parser (``tar_parser.py``).

On-disk layout::

    [ 512-byte header ][ file data, padded to 512 ]*
    [ 512 zero bytes ][ 512 zero bytes ]   # two null end-of-archive trailers

There is no central directory: listing is a header walk (``REQUIRES_SCANNING``) or a
progressive forward pass. Compressed forms (``.tar.gz`` / …) wrap the same layout in
a stream codec and behave as **solid** for random member opens.

Random-access reading (``streaming=False``) needs a seekable source (decompressing
first for a compressed tar) and opens any member on demand: a member's stream is a
view over its data area. Forward-only (``streaming=True``) walks one progressive pass,
including on a non-seekable source, via ``_iter_with_data()`` / ``stream_members()``;
a member's data is read through the walker, which skips what the consumer left.

After a full walk, :meth:`TarReader._verify_tar_eof` checks the end, from the
:class:`~archivey.internal.backends.tar_parser.TarEnd` the walk stopped on:

- A header that does not parse → ``CorruptionError``, in both access modes and
  whatever follows it.
- A missing two-block null trailer → ``ARCHIVE_EOF_MARKER_MISSING``.
- A trailer whose first block is zero and whose second is not, after at least one
  member → ``ARCHIVE_EOF_MARKER_MISSING`` (``expected_marker="second_zero_block"``).
  With no member before the zero block it is ``CorruptionError``, so a file that is
  not a tar does not open as an empty one.
- A non-zero byte within ``_MAX_TRAILING_SCAN`` bytes of a complete trailer, or of a
  damaged second trailer block → ``ARCHIVE_TRAILING_DATA``. Zero padding passes.

Both codes follow the diagnostic policy like any other: a caller who wants either to
fail sets it to ``RAISE`` (``DiagnosticPolicy.strict()`` does so for both).
"""

from __future__ import annotations

import stat
import threading
from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, nullcontext
from dataclasses import replace
from datetime import datetime
from typing import BinaryIO, Literal, cast

from archivey.config import ArchiveyConfig
from archivey.cost import (
    AccessCost,
    CostReceipt,
    ListingCost,
    StreamCapability,
)
from archivey.diagnostics import (
    ArchiveEofContext,
    DiagnosticCode,
    DigestContext,
)
from archivey.exceptions import (
    ArchiveyError,
    CorruptionError,
    ReadError,
    TruncatedError,
    UnsupportedFeatureError,
)
from archivey.internal.backends.tar_parser import (
    BLOCKSIZE,
    SPARSE_ENTRY_BYTES,
    Budget,
    NameSource,
    PaxValue,
    TarEnd,
    TarEndKind,
    TarEntry,
    TarWalker,
    validate_sparse_map,
)
from archivey.internal.base_reader import (
    BaseArchiveReader,
    ReadBackend,
    _ProgressivePassIterator,
    reject_start_offset,
)
from archivey.internal.config import stream_config_from_archivey
from archivey.internal.diagnostics_collector import DiagnosticCollector
from archivey.internal.file_copy_pass import DEFAULT_FILE_COPY_PASS, FileCopyPass
from archivey.internal.logs import backends as backends_logger
from archivey.internal.logs import integrity as integrity_logger
from archivey.internal.naming import emit_member_name_normalized, normalize_member_name
from archivey.internal.open_site import OpenSite
from archivey.internal.password import _PasswordCandidates
from archivey.internal.registry import register_reader
from archivey.internal.source import ArchiveSource
from archivey.internal.streams.archive_stream import ArchiveStream
from archivey.internal.streams.codecs import (
    SINGLE_FILE_CODECS,
    codec_for_stream_format,
    open_codec_stream,
)
from archivey.internal.streams.decompressor_stream import _StreamChecksumError
from archivey.internal.streams.streamtools import (
    LockedStream,
    SharedView,
    SparseStream,
    ensure_bufferedio,
)
from archivey.internal.timestamps import TimestampIssue, unix_to_datetime
from archivey.terminal import quoted
from archivey.types import (
    ArchiveFormat,
    ArchiveInfo,
    ArchiveMember,
    CompressionAlgorithm,
    CompressionMethod,
    ContainerFormat,
    MagicSignature,
    MemberExtra,
    MemberStreams,
    MemberType,
    StreamFormat,
    _ReadOnlyDict,
)

# Read size for the trailing-bytes scan. The tail past the trailer is unbounded (a
# concatenated archive, a padded record, arbitrary junk), so it is consumed in chunks
# rather than with one read().
_TRAILING_SCAN_CHUNK = 64 * 1024

# How far past the trailer the trailing-bytes scan looks before it stops. An effort
# bound, not a ceiling: nothing is refused when it is reached, the scan only stops
# looking. `tar` pads to 10 KiB records by default, so a concatenated archive's header
# lands within ~10 KiB of the trailer in the ordinary case; this is a hundred times
# that. Measured on a gzipped tar with an all-zero tail (the worst case, since the scan
# stops at the first non-zero byte), 1 MiB costs ~5 ms. A constant rather than a config
# field: promoting it later is backward compatible, demoting a field is not, and on
# ``ListingLimits`` a ``None`` would have to mean "scan to EOF", inverting what ``None``
# means on every other field there.
_MAX_TRAILING_SCAN = 1 * 2**20

# Stream formats that carry, or can carry, a checksum over the whole decoded stream
# or its last block, which is checked only when the stream's end is read. For these a
# trailing scan that stops at ``_MAX_TRAILING_SCAN`` leaves that checksum unchecked, and
# says so.
_STREAMS_WITH_CHECKSUM = frozenset(
    (
        StreamFormat.GZIP,
        StreamFormat.BZIP2,
        StreamFormat.XZ,
        StreamFormat.ZSTD,
        StreamFormat.LZ4,
        StreamFormat.LZIP,
        StreamFormat.ZLIB,
    )
)

# The subset whose failed check reads, to archivey, like any other decode failure: the
# bzip2 block CRC and the xz block check. For these, a tail that fails to decode in the
# trailing scan may be a failed check over members already read, and says so.
_STREAMS_WITH_UNTYPED_CHECKSUM = frozenset((StreamFormat.BZIP2, StreamFormat.XZ))

# What :meth:`TarReader._verify_tar_eof` found where the end-of-archive marker belongs:
# no block, a partial one, a non-null block after a zero block that ended at least one
# member (the marker is damaged, the listing whole), a header that does not parse (the
# listing is shortened), or a non-null block after a zero block with no member before
# it.
_EofFinding = Literal[
    "absent", "short", "damaged_second_block", "rejected_header", "no_member"
]

# Typeflags whose member is a file: regular (``0`` and the old NUL), contiguous (``7``)
# and old GNU sparse (``S``) all carry the file's data. Every other typeflag that is not
# a directory or a link lists as OTHER, its data skipped by size.
_FILE_TYPES = frozenset((b"0", b"\x00", b"7", b"S"))
# The random-access walk's read-ahead. Fixed rather than io's default, which Python 3.14
# raised from 8 KiB to 128 KiB: a larger read-ahead reads and decodes further past what
# the listing needs, so listing costs and source reads would differ by Python version.
_WALK_BUFFER = 8 * 1024

# GNU tar's incremental dumps store a directory as a ``D`` (dumpdir) entry, whose data
# lists the directory's contents at dump time. GNU tar extracts it as a directory; the
# list is skipped.
_DIRECTORY_TYPES = frozenset((b"5", b"D"))
_SYMLINK_TYPE = b"2"
_HARDLINK_TYPE = b"1"
# Character and block devices and FIFOs, which carry device numbers.
_DEVICE_TYPES = frozenset((b"3", b"4", b"6"))

# PAX records whose value is a name: without ``hdrcharset=BINARY`` it is UTF-8, as
# POSIX says; with it, or for a ustar or GNU field, it has no declared encoding.
_PAX_NAME_KEYS = frozenset((b"path", b"linkpath", b"uname", b"gname"))


def _member_type(entry: TarEntry) -> MemberType:
    typeflag = entry.typeflag
    if typeflag in _DIRECTORY_TYPES or entry.old_style_directory:
        return MemberType.DIRECTORY
    if typeflag == _SYMLINK_TYPE:
        return MemberType.SYMLINK
    if typeflag == _HARDLINK_TYPE:
        return MemberType.HARDLINK
    if typeflag in _FILE_TYPES:
        return MemberType.FILE
    # Character/block devices, FIFOs, multi-volume and volume headers, unknown types.
    return MemberType.OTHER


# Every compressed-tar combination the codec layer can decode: TAR composed with each
# standalone stream codec (gz/bz2/xz/zst/lz4/lzip/lzma-alone/zlib/brotli/unix-compress).
# The common ones have named ArchiveFormat constants; the rest are equal-by-value
# on-demand instances.
_TAR_COMPRESSED: tuple[ArchiveFormat, ...] = tuple(
    ArchiveFormat(ContainerFormat.TAR, codec.stream_format)
    for codec in SINGLE_FILE_CODECS
    if codec.stream_format is not None
)

# Plain TAR plus every compressed combination the codec layer can decode.
_TAR_FORMATS: tuple[ArchiveFormat, ...] = (ArchiveFormat.TAR, *_TAR_COMPRESSED)

# Canonical extensions are derived from each format (TAR -> ".tar", TAR_GZ -> ".tar.gz",
# (TAR, LZIP) -> ".tar.lz", …); only the short aliases (.tgz/.tbz/…) and `.cbt`
# (comic-book TAR) are listed by hand.
# (Built at module scope: a dict comprehension in the class body can't see class-level names.)
_TAR_EXTENSIONS: dict[str, ArchiveFormat] = {
    f".{fmt.file_extension()}": fmt for fmt in _TAR_FORMATS
}
_TAR_EXTENSIONS.update(
    {
        ".tgz": ArchiveFormat.TAR_GZ,
        ".tbz2": ArchiveFormat.TAR_BZ2,
        ".tbz": ArchiveFormat.TAR_BZ2,
        ".txz": ArchiveFormat.TAR_XZ,
        ".tzst": ArchiveFormat.TAR_ZST,
        ".tlz": ArchiveFormat(ContainerFormat.TAR, StreamFormat.LZIP),
        ".cbt": ArchiveFormat.TAR,
    }
)


# Shared across FILE/HARDLINK members — avoid per-member CompressionMethod construction.
_STORED_COMPRESSION: tuple[CompressionMethod, ...] = (
    CompressionMethod(algo=CompressionAlgorithm.STORED),
)


def _pax_text(entry: TarEntry, key: bytes) -> str | None:
    """The PAX record ``key`` in force for ``entry`` as text, or ``None`` when it is
    absent or empty (an empty value in a member's own header cancels the keyword)."""
    value = entry.pax.get(key)
    if value is None or not value.value:
        return None
    return value.value.decode("utf-8", "surrogateescape")


def _pax_time(
    entry: TarEntry, key: str, name: str
) -> tuple[datetime | None, TimestampIssue | None]:
    """Parse a PAX time record (decimal Unix seconds) into a tz-aware UTC datetime.

    Covers the standard ``mtime``, ``atime`` and ``ctime`` records and libarchive's
    ``LIBARCHIVE.creationtime`` extension keyword (not a standard PAX record).

    Returns ``(None, None)`` when the record is absent, and ``(None, TimestampIssue)``
    when it is present but not a number or outside ``datetime``'s range, so a bad
    record is reported the same way as a bad header ``mtime``.
    """
    raw = _pax_text(entry, key.encode())
    if raw is None:
        return None, None
    try:
        seconds = float(raw)
    except ValueError:
        value = None
    else:
        value = unix_to_datetime(seconds)
    if value is not None:
        return value, None
    return None, _tar_time_issue(name, key, repr(raw), pax_record=True)


# The ArchiveMember field each TAR time fills, which is what a timestamp diagnostic
# names in every format.
_TAR_TIME_FIELDS = {
    "mtime": "modified",
    "atime": "accessed",
    "ctime": "ctime",
    "LIBARCHIVE.creationtime": "created",
}


def _tar_time_issue(
    name: str, key: str, value_repr: str, *, pax_record: bool
) -> TimestampIssue:
    """The ``MEMBER_TIMESTAMP_INVALID`` finding for one TAR time field.

    ``value_repr`` is the raw PAX record for a record, and the header's ``mtime`` for
    one that ``datetime`` cannot hold.
    """
    label = f"PAX {key}" if pax_record else key
    return TimestampIssue(
        field=_TAR_TIME_FIELDS[key],
        source="tar",
        value_repr=value_repr,
        message=f"Invalid TAR {label} for {quoted(name)}: {value_repr}",
    )


class TarReader(BaseArchiveReader):
    """Reads a TAR archive (plain or compressed) with :class:`TarWalker`.

    A seekable source can open any member, but ``_MEMBER_LIST_UPFRONT`` is False —
    there is no central directory, so a complete list always requires a scan (or a
    finished stream pass).
    """

    # TAR has no central directory: the member list only exists after a scan, so it is not
    # "available without scanning" (listing cost is REQUIRES_SCANNING / REQUIRES_DECOMPRESSION,
    # not INDEXED). Once iterated, the base serves the cached list anyway.
    _MEMBER_LIST_UPFRONT = False

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
        self._encoding = encoding
        # The codec for header and GNU name fields that are not valid UTF-8, and for
        # PAX values that are not.
        self._codec = encoding if encoding is not None else "utf-8"
        self._source = source
        self._compressed = format.stream != StreamFormat.UNCOMPRESSED
        # Whether the walk's current pull enforces the listing limits (see
        # _pull_member). True until a pull says otherwise: the first header is parsed
        # at open, where only the whole cap can bind anyway.
        self._listing_enforced = True
        # The decompression stream of a compressed tar, or the buffer in front of the
        # source of a streaming plain tar: built by this reader, so closed by it.
        self._owned_stream: BinaryIO | None = None
        # The codec stream under ``_owned_stream``. ``ensure_bufferedio`` wraps it in a
        # buffer that detaches on close rather than closing it, so it is closed here
        # explicitly: left to the garbage collector, a stream held by a failed open's
        # traceback kept its rapidgzip child process running.
        self._owned_codec_stream: BinaryIO | None = None
        # The current walk's own buffered view in random access (see _new_walker).
        self._walker_view: BinaryIO | None = None
        # The last PAX records a member without records of its own was built from,
        # and the ``extra["tar.pax_headers"]`` every such member shares
        # (:meth:`_extra_pax_headers`).
        self._shared_pax: tuple[Mapping[bytes, PaxValue], _ReadOnlyDict] | None = None
        # Shared-handle lock: CONCURRENT readers serialize every read of the shared
        # byte stream; streaming readers also take a lock (exclusive / normally
        # uncontended) so the same critical-section shape covers the walk, member
        # reads, the end check and close.
        self._handle_lock = (
            threading.Lock()
            if MemberStreams.CONCURRENT in member_streams or streaming
            else None
        )

        try:
            self._stream = self._open_byte_stream(
                source, format, streaming, member_streams=member_streams
            )
            # The first member's headers are parsed at open, so a file that is not a
            # tar fails here (DR-15b).
            self._walker = self._new_walker()
            first = self._step(self._walker)
            self._check_first(first)
            # The walk that hands out ``first`` is the open-time one; a random-access
            # walk started over after a discarded failure builds its own.
            self._open_walk: tuple[TarWalker, TarEntry | TarEnd] | None = (
                self._walker,
                first,
            )
        except ArchiveyError as exc:
            # Release before re-raising: the exception traceback keeps this frame alive
            # and would otherwise pin the owned stream until the caller drops the
            # exception (inventory/fuzz catch-and-continue loops).
            self._release_owned_stream()
            self._stamp_error_context(exc)
            raise
        except BaseException:
            self._release_owned_stream()
            raise

    def _release_owned_stream(self) -> None:
        """Close the streams this reader opened, if any. Safe to call more than once."""
        try:
            # A random-access reader has a walk view and a streaming one has the
            # buffer; never both.
            self._close_walker_view()
            if self._owned_stream is not None:
                owned, self._owned_stream = self._owned_stream, None
                owned.close()
        finally:
            if self._owned_codec_stream is not None:
                codec, self._owned_codec_stream = self._owned_codec_stream, None
                codec.close()

    def _close_walker_view(self) -> None:
        if self._walker_view is not None:
            view, self._walker_view = self._walker_view, None
            view.close()

    def _open_byte_stream(
        self,
        source: ArchiveSource,
        format: ArchiveFormat,
        streaming: bool,
        *,
        member_streams: MemberStreams,
    ) -> BinaryIO:
        """The TAR bytes: the source for a plain tar, archivey's codec stream for a
        compressed one."""
        if self._compressed:
            codec = codec_for_stream_format(format.stream)
            codec_source: str | BinaryIO
            if source.path is not None:
                # A file goes to the codec as its path, as for a bare compressed file:
                # the codec opens its own handle and may use path-only accelerators, and
                # the static ratio applies because the size is known.
                codec_source = str(source.path)
            else:
                # A stream whose size is not known is counted, so the live
                # decompression-ratio guard can see compressed bytes consumed.
                codec_source = self._track_source_seeks(
                    self._wrap_compressed_input(source)
                )
            stream = open_codec_stream(
                codec,
                codec_source,
                # The codec stream is the whole file, so bytes after its end are
                # reported, as bytes after the TAR trailer are.
                config=replace(
                    stream_config_from_archivey(
                        self._config,
                        streaming=streaming,
                        seekable=MemberStreams.SEEKABLE in member_streams,
                    ),
                    report_trailing_data=True,
                ),
                stamp=lambda exc: self._stamp_error_context(exc),
                collector=self._diagnostics_collector,
            )
            self._owned_codec_stream = stream
            if not streaming:
                # Each walk buffers its own view (_new_walker). A second buffer here
                # would fill the first one in a loop, and a loop that reaches a damaged
                # codec tail raises and drops the bytes already decoded, though the
                # walk may need none of the tail.
                return stream
            # The walker reads 512-byte blocks: a buffer in front makes each one a copy
            # from memory, not a decoder call. The cast is typeshed's split:
            # BufferedIOBase is not BinaryIO there, but is at runtime.
            self._owned_stream = cast("BinaryIO", ensure_bufferedio(stream))
            return self._owned_stream
        stream = self._track_source_seeks(source)
        if streaming:
            # Random access buffers each walk's own view instead (_new_walker).
            self._owned_stream = cast("BinaryIO", ensure_bufferedio(stream))
            return self._owned_stream
        return stream

    def _new_walker(self) -> TarWalker:
        """A walker from the start of the archive.

        Streaming: over the reader's one forward stream, whose only reader it is
        (member data is read through it). Random access: over a buffered view of its
        own, which re-seeks the shared stream under the handle lock before each read
        of the buffer, so member reads never move the walk. A pass reads member data
        through this buffer too, so on a compressed tar the buffer's read-ahead into a
        member's data never makes a member read seek back.
        """
        if self._streaming:
            return TarWalker(self._stream, seekable=False)
        view = cast(
            "BinaryIO",
            ensure_bufferedio(
                SharedView(self._stream, 0, lock=self._io_guard()), _WALK_BUFFER
            ),
        )
        # Only one walk runs at a time, and a pass's member streams are closed when it
        # ends, so a walk started over replaces the last one's view.
        self._close_walker_view()
        self._walker_view = view
        return TarWalker(view, seekable=True)

    def _io_guard(self) -> AbstractContextManager[object]:
        return self._handle_lock if self._handle_lock is not None else nullcontext()

    def _walk_guard(self) -> AbstractContextManager[object]:
        """Held around the walker's own reads. A streaming walker reads the shared
        stream directly; a random-access one reads through a locked view."""
        return self._handle_guard() if self._streaming else nullcontext()

    def _step(self, walker: TarWalker) -> TarEntry | TarEnd:
        with self._walk_guard():
            return walker.next_entry(self._header_budget())

    def _check_first(self, first: TarEntry | TarEnd) -> None:
        """Refuse a file whose first block is not a member header or a zero block."""
        if not isinstance(first, TarEnd):
            return
        if first.kind == TarEndKind.ABSENT:
            raise TruncatedError("TAR archive is truncated: the file is empty")
        if first.kind == TarEndKind.SHORT:
            raise TruncatedError(
                f"TAR archive is truncated: it holds {first.observed_bytes} bytes, "
                "less than one header block"
            )
        if first.kind == TarEndKind.REJECTED:
            raise CorruptionError(
                f"Not a TAR archive, or a damaged one: the header at offset "
                f"{first.offset} does not parse ({first.reason})"
            )

    def _header_budget(self) -> Budget:
        """``(bytes left, cap)`` of ``max_metadata_bytes`` for the next member's headers.

        A random-access walk the base is enforcing has what the listing has not yet
        retained. A walk the base is not enforcing (``stream_members()``), and a
        streaming walk, which never enforces the running total (threat-model O1),
        still may not parse one member's headers larger than the whole cap. So may
        not a walk already past the cap: the base refuses at the member that crosses
        it.
        """
        cap = self._config.listing_limits.max_metadata_bytes
        if cap is None:
            return None
        counted = self._listing_tracker.metadata_bytes
        if self._streaming or not self._listing_enforced or counted > cap:
            return (cap, cap)
        return (cap - counted, cap)

    def _pull_member(self, *, enforce: bool) -> ArchiveMember | None:
        """Pull as the base does, noting whether it enforces the listing limits, which
        sets the budget for the next member's headers (:meth:`_header_budget`)."""
        self._listing_enforced = enforce
        return super()._pull_member(enforce=enforce)

    def _register_member(
        self,
        idx: int,
        member: ArchiveMember,
        *,
        enforce_listing_limits: bool = False,
    ) -> None:
        """Register as the base does, then weigh a sparse member's retained map.

        The map can hold millions of entries from a few kilobytes of compressed
        archive, and it stays on the member's entry for the life of the listing.
        """
        super()._register_member(
            idx, member, enforce_listing_limits=enforce_listing_limits
        )
        entry = member._raw
        if isinstance(entry, TarEntry) and entry.sparse is not None:
            self._listing_tracker.account_retained_bytes(
                len(entry.sparse) * SPARSE_ENTRY_BYTES, enforce=enforce_listing_limits
            )

    def _iter_members(self) -> Iterator[ArchiveMember]:
        """The member walk, one header per pull.

        Each member is registered (and counted against the listing limits) before the
        next header is parsed, so a header bomb stops at the member that crosses a cap.
        A random-access walk started over after a discarded failure walks again from
        the start with a walker of its own.
        """
        if self._open_walk is not None:
            walker, entry = self._open_walk
            self._open_walk = None
        else:
            walker = self._new_walker()
            with self._translated_errors():
                entry = self._step(walker)
        # A pass reads the data of the member the walk is at through this walker.
        self._walker = walker
        index = 0
        while isinstance(entry, TarEntry):
            yield self._to_member(entry, index)
            index += 1
            with self._translated_errors():
                entry = self._step(walker)
        self._verify_tar_eof(walker, entry, any_members=index > 0)

    def _iter_members_progressive(self) -> Iterator[ArchiveMember]:
        """Forward-only member walk. Yields bare members; the base's shared progressive
        pass stamps ids and resolves backward links."""
        return self._iter_members()

    def _iter_with_data_random_access(
        self,
    ) -> Iterator[tuple[ArchiveMember, ArchiveStream | None]]:
        """``stream_members()`` on a random-access reader, in one forward pass.

        The base's version lists every member first and then reads their data, which
        on a compressed tar decodes the stream to its end for the headers and then again
        from the start for the data. Here, when the walk has not ended yet, the walk and
        the data share one pass: the member is registered and yielded, and its data is
        read before the walk parses the next header, so every seek goes forward.
        Members are registered as they arrive, as the base's walk registers them, so
        ``members()`` afterwards serves the same list, and the pass links a hardlink to
        an earlier member only, as the streaming pass does.

        A pass over a listing that is already complete reads the members' data in
        archive order, which is one forward sweep too (after one seek back to the first
        member). The pass has its own cursor over the reader's one walk, so a pass
        abandoned early does not make the next one start part-way.
        """
        if self._walk_done:
            yield from super()._iter_with_data()
            return

        def _open_in_walk(member: ArchiveMember) -> ArchiveStream:
            with self._translated_errors(member.name):
                return self._open_member_stream(
                    member, defer_sparse_error=False, in_walk=True
                )

        def _open(member: ArchiveMember) -> ArchiveStream | None:
            if not member.is_file:
                return None
            return self._lazy_member_stream(member, _open_in_walk)

        yield from self._drive_pass_streams(
            _ProgressivePassIterator(self), open_member=_open
        )

    def _extraction_listing(self) -> AbstractContextManager[None]:
        """Enforce ``ListingLimits`` as members arrive in the extraction's one pass.

        The base lists the whole archive before an extraction, which on a compressed
        tar decodes the stream once for the headers and again for the data. Here the
        pass the extraction runs (:meth:`_iter_with_data_random_access`) registers each
        member with the limits enforced, so ``ResourceLimitError`` is raised at the
        member that crosses a cap, before it is written, as ``members()`` raises it. A
        listing that is already complete is the base's case.
        """
        if self._walk_done:
            return super()._extraction_listing()
        return self._enforcing_listing_limits()

    def _iter_with_data(
        self, copies: FileCopyPass = DEFAULT_FILE_COPY_PASS
    ) -> Iterator[tuple[ArchiveMember, ArchiveStream | None]]:
        if not self._streaming:
            yield from self._iter_with_data_random_access()
            return
        # Pull from the shared instance-held progressive pass so __iter__,
        # stream_members, and scan_members share one cursor and finalization.
        # close_previous=False: the walk's next header leaves the previous member's
        # stream unreadable, and the walk reads through whatever it left.
        with self._translated_errors():

            def _open(member: ArchiveMember) -> ArchiveStream | None:
                if not member.is_file:
                    return None
                return self._open_member_stream(member, defer_sparse_error=True)

            yield from self._drive_pass_streams(
                self._begin_forward_pass(),
                open_member=_open,
                close_previous=False,
            )

    def _verify_tar_eof(
        self, walker: TarWalker, end: TarEnd, *, any_members: bool
    ) -> None:
        """Check the end-of-archive marker from where the walk stopped.

        A header that does not parse (a corrupt member header after the first) means
        the listing was cut short, and that escalates to ``CorruptionError`` whatever
        the diagnostic policy says. This needs no further read, so it holds in both
        access modes whatever follows the rejected header: nothing, a zero block (a
        member whose data starts with one), or more members.

        A zero block ends the members. An ``x`` or ``L`` header right before it is
        left unused, as GNU tar 1.35 lists such an archive, with no diagnostic. Only
        the *second* marker block is checked: reading two here would demand a third
        block of trailing zeros and wrongly flag a minimal ``tar -b1`` trailer. A short
        or empty read is a truncated or absent trailer, reported as
        ``ARCHIVE_EOF_MARKER_MISSING`` under the ordinary diagnostic policy.

        A non-null block there, after a zero block with members listed, means the
        end-of-archive marker itself is damaged: every member before it is listed and
        whole, as GNU tar and 7-Zip list them with a warning, so it is
        ``ARCHIVE_EOF_MARKER_MISSING`` under the ordinary policy
        (``DiagnosticPolicy.strict()`` refuses it), and the scan past the trailer runs
        from the block after it. A zero block and then a non-null one with no member
        before them is ``CorruptionError``.
        """
        if end.kind == TarEndKind.REJECTED:
            self._emit_eof_marker("rejected_header", observed_bytes=BLOCKSIZE)
            return
        if end.kind != TarEndKind.ZERO_BLOCK:
            self._emit_eof_marker(
                "absent" if end.kind == TarEndKind.ABSENT else "short",
                observed_bytes=end.observed_bytes,
            )
            return
        stream = walker.stream_after_end()
        with self._translated_errors(), self._walk_guard():
            chunk = stream.read(BLOCKSIZE)
        if len(chunk) == BLOCKSIZE and chunk == bytes(BLOCKSIZE):
            self._verify_nothing_but_zeros_to_eof(stream)
            return
        if len(chunk) == BLOCKSIZE:
            if not any_members:
                self._emit_eof_marker("no_member", observed_bytes=BLOCKSIZE)
                return
            # One zero block, then a damaged one: the marker is damaged, not the
            # listing. The scan past it still runs, because on a compressed tar it is
            # where the codec's whole-stream checksum over the members just listed is
            # usually reached.
            self._emit_eof_marker("damaged_second_block", observed_bytes=BLOCKSIZE)
            self._verify_nothing_but_zeros_to_eof(stream)
            return
        self._emit_eof_marker(
            "absent" if len(chunk) == 0 else "short", observed_bytes=len(chunk)
        )

    def _verify_nothing_but_zeros_to_eof(self, stream: BinaryIO) -> None:
        """Report a non-zero byte within ``_MAX_TRAILING_SCAN`` bytes of the trailer.

        Without this, a complete trailer asserted only that the two trailer blocks were
        present — 4 KiB of arbitrary appended bytes passed silently. Zeros still pass,
        deliberately: writers pad to 10 KiB records routinely, so "nothing but zeros" is
        the strongest rule that does not flag what ``tar(1)`` itself writes.

        Concatenated archives are reported here, which is the intended answer — they are
        two archives and only the first was listed.

        The scan is bounded because it is not free: on a compressed tar the tail must be
        decompressed to be inspected. Past the bound it stops looking and reports
        nothing, so a second archive further out goes unseen; the bound is an effort
        limit, not a claim that the rest is zero. Read in bounded chunks: the tail may be
        arbitrarily long and must not be materialized. On a forward-only source the
        reads go past the trailer too, so a pipe held open after the tar ends blocks
        here until more bytes or EOF arrive; ``docs/gotchas.md`` says so to callers.

        A tail that fails to *decode* ends the scan quietly. On a compressed tar the
        bytes past the trailer can be a truncated gzip footer or junk after the
        compressed stream, and the codec refuses both. Every member was already read
        whole, and before this scan ran unconditionally such an archive listed without
        complaint, so a decode failure out here must not turn a good listing into an
        error. It is not trailing tar data either, so it is not reported as that. The one
        exception is a :class:`_StreamChecksumError`: a codec checksum that covers the
        whole decoded stream (gzip's CRC-32 and ISIZE, zlib's Adler-32, the zstd and lz4
        content checksums, lzip's CRC-32) failed, so the members already read are
        damaged, and that raises. A ``tar -b128`` record pads 64 KiB past the trailer,
        so this scan is often where the checksum is reached.

        bzip2 and xz check each block, and the last block's check can be reached here
        too, but neither codec reports a failed check differently from junk after the
        stream. A decode failure on those is therefore ``DIGEST_UNVERIFIABLE``, not
        silence: the members already read may be damaged, and nothing can say. When the
        codec had already read to the stream's end during the last member's read, the
        check fails there instead, as ``CorruptionError`` from that read.

        When the scan stops at its bound with the compressed stream not yet at its end,
        that checksum was never checked, and ``DIGEST_UNVERIFIABLE`` says so.
        """
        offset = 0
        while offset <= _MAX_TRAILING_SCAN:
            # One byte past the bound, to learn whether the stream ended there.
            want = min(_TRAILING_SCAN_CHUNK, _MAX_TRAILING_SCAN - offset) or 1
            try:
                with self._translated_errors(), self._walk_guard():
                    chunk = stream.read(want)
            except _StreamChecksumError:
                # The codec's whole-stream checksum covers the members already read,
                # so this is damage to them, not a tail that failed to decode.
                raise
            except ReadError:
                if self._format.stream in _STREAMS_WITH_UNTYPED_CHECKSUM:
                    self._emit_stream_checksum_unverified(
                        reason="trailing_decode_failed"
                    )
                return
            if not chunk:
                return
            if offset == _MAX_TRAILING_SCAN:
                self._emit_stream_checksum_unverified(reason="trailing_scan_limit")
                return
            stripped = chunk.lstrip(b"\x00")
            if stripped:
                self._emit_trailing_data(
                    observed_bytes=offset + (len(chunk) - len(stripped))
                )
                return
            offset += len(chunk)

    def _emit_stream_checksum_unverified(self, *, reason: str) -> None:
        """Report a compressed stream whose checksum the trailing scan could not check.

        ``reason`` is ``"trailing_scan_limit"`` when the scan stopped at its bound
        before the stream's end, and ``"trailing_decode_failed"`` when a bzip2 or xz
        tail failed to decode (see :meth:`_verify_nothing_but_zeros_to_eof`). Only
        for a codec that can carry such a checksum; the zstd, lz4 and xz ones are
        optional, so the message says "if it carries one".
        """
        stream = self._format.stream
        if stream not in _STREAMS_WITH_CHECKSUM:
            return
        if reason == "trailing_scan_limit":
            what = (
                f"continues more than {_MAX_TRAILING_SCAN} bytes past the "
                "end-of-archive marker, and the scan stopped there: the stream's "
                "checksum, if it carries one, was not checked"
            )
        else:
            what = (
                "fails to decode past the end-of-archive marker: this codec reports a "
                "failed check the same way as junk after the stream, so whether the "
                "checksum passed cannot be told"
            )
        self._diagnostics_collector.emit(
            code=DiagnosticCode.DIGEST_UNVERIFIABLE,
            message=(
                f"The {stream.value} stream around this TAR archive {what}, so damage "
                "to the members already read may go unseen."
            ),
            context=DigestContext(
                archive_name=self._archive_name,
                algorithm=f"{stream.value} stream checksum",
                reason=reason,
            ),
            logger=integrity_logger,
        )

    def _emit_trailing_data(self, *, observed_bytes: int) -> None:
        self._diagnostics_collector.emit(
            code=DiagnosticCode.ARCHIVE_TRAILING_DATA,
            message=(
                "TAR archive continues past its end-of-archive marker: a non-zero byte "
                f"appears {observed_bytes} bytes after the trailer. The listing does not "
                "account for it (this file may be two archives concatenated)."
            ),
            context=ArchiveEofContext(
                archive_name=self._archive_name,
                format="tar",
                expected_marker="zeros_to_eof",
                expected_bytes=0,
                observed_bytes=observed_bytes,
                observed_kind="nonzero",
            ),
            logger=backends_logger,
        )

    def _emit_eof_marker(self, end: _EofFinding, *, observed_bytes: int) -> None:
        """Report a missing or damaged two-zero-block end-of-archive marker.

        ``end`` says what was found where the marker belongs (see :data:`_EofFinding`).
        ``"rejected_header"`` and ``"no_member"`` are corruption whatever the policy:
        the first means a header that does not parse ended the walk and shortened the
        listing, the second that a file with no member is a zero block and then junk,
        which must not open as an empty tar. The other three follow the policy.
        ``"damaged_second_block"`` gets its own ``expected_marker``,
        ``"second_zero_block"``, so a caller can tell a whole listing from a
        shortened one by the context, not the message.
        """
        expected_marker = "two_zero_blocks"
        expected_bytes = 1024
        observed_kind: Literal["absent", "short", "nonzero"] = "nonzero"
        escalate_as: type[BaseException] | None = None
        if end == "damaged_second_block":
            message = (
                "TAR archive's end-of-archive marker is damaged: a zero block ends the "
                "members, but the block after it is not zero. Every member before it is "
                "listed."
            )
            expected_marker = "second_zero_block"
            expected_bytes = 512
        elif end == "rejected_header":
            message = (
                "TAR archive is corrupt: a member header that does not parse appears "
                "where the next header or the end-of-archive marker was expected, so "
                "the members after it are not listed."
            )
            escalate_as = CorruptionError
        elif end == "no_member":
            message = (
                "TAR archive is corrupt: it has no member, and the block after its "
                "first zero block is not zero. A file that is only a zero block and "
                "then other bytes is not shown to be a TAR archive, so it does not "
                "open as an empty one."
            )
            escalate_as = CorruptionError
        else:
            message = (
                "TAR archive may be truncated: missing or short end-of-archive marker "
                "block(s)."
            )
            observed_kind = end
        escalate_kwargs: dict[str, object] | None = None
        if escalate_as is not None:
            escalate_kwargs = {
                "source_format": self._format,
                "archive_name": self._archive_name,
            }
        self._diagnostics_collector.emit(
            code=DiagnosticCode.ARCHIVE_EOF_MARKER_MISSING,
            message=message,
            context=ArchiveEofContext(
                archive_name=self._archive_name,
                format="tar",
                expected_marker=expected_marker,
                expected_bytes=expected_bytes,
                observed_bytes=observed_bytes,
                observed_kind=observed_kind,
            ),
            logger=backends_logger,
            escalate_as=escalate_as,
            escalate_kwargs=escalate_kwargs,
        )

    def _source_stream_capability(self) -> StreamCapability:
        assert self._source is not None
        if self._source.seekable():
            return StreamCapability.SEEKABLE
        return StreamCapability.FORWARD_ONLY

    def _extra_pax_headers(self, entry: TarEntry) -> _ReadOnlyDict:
        """``extra["tar.pax_headers"]`` for ``entry``: its PAX records, the global ones
        in force included, as text.

        A read-only copy. Members that carry only the PAX global records share one,
        as the walker shares the records: one per member cost the global records once
        for every 512-byte member header. Read-only so that sharing it is invisible: a
        change made through one member cannot show on another.
        """
        if entry.has_own_pax:
            return _ReadOnlyDict(self._pax_headers_text(entry.pax))
        shared = self._shared_pax
        if shared is None or shared[0] is not entry.pax:
            shared = self._shared_pax = (
                entry.pax,
                _ReadOnlyDict(self._pax_headers_text(entry.pax)),
            )
        return shared[1]

    def _pax_headers_text(self, records: Mapping[bytes, PaxValue]) -> dict[str, str]:
        """PAX records as text. Keys and values are UTF-8; a name value under
        ``hdrcharset=BINARY`` uses the archive codec, and any value that is not valid
        UTF-8 falls back to it, with undecodable bytes kept as surrogates."""
        text: dict[str, str] = {}
        for key, entry in records.items():
            value = entry.value
            if key in _PAX_NAME_KEYS and entry.binary:
                decoded = value.decode(self._codec, "surrogateescape")
            else:
                try:
                    decoded = value.decode("utf-8")
                except UnicodeDecodeError:
                    decoded = value.decode(self._codec, "surrogateescape")
            text[key.decode("utf-8", "surrogateescape")] = decoded
        return text

    def _decode_name(
        self, raw: bytes, source: NameSource | None, binary: bool
    ) -> tuple[str, bool]:
        """A name, link name, user or group name as text, and whether the UTF-8
        reading was taken over the caller's ``encoding=``.

        A PAX value is UTF-8 unless its block says ``hdrcharset=BINARY``; one that is
        not valid UTF-8 falls back to the archive codec. A ustar or GNU field, or a
        ``BINARY`` PAX value, declares no encoding: it is UTF-8 when its bytes are
        valid UTF-8, else the caller's ``encoding=`` (UTF-8 with surrogateescape by
        default), the rule every format follows.
        """
        if source is NameSource.PAX and not binary:
            try:
                return raw.decode("utf-8"), False
            except UnicodeDecodeError:
                return raw.decode(self._codec, "surrogateescape"), False
        codec_text = raw.decode(self._codec, "surrogateescape")
        if self._encoding is None or raw.isascii():
            return codec_text, False
        try:
            utf8_text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return codec_text, False
        return utf8_text, utf8_text != codec_text

    def _to_member(self, entry: TarEntry, index: int) -> ArchiveMember:
        """Type one member. ``index`` is its position in the walk, the id registration
        stamps, so the diagnostics raised here can name it before it has one."""
        member_type = _member_type(entry)
        presented, inferred = self._decode_name(
            entry.name, entry.name_source, entry.name_binary
        )
        if entry.name_source is NameSource.PAX or member_type == MemberType.DIRECTORY:
            # A PAX path is read with its trailing slashes removed, as tarfile reads
            # it; a directory's slash is its type.
            presented = presented.rstrip("/") or presented
        # TAR is a POSIX format: a backslash is a legal filename character, not a separator.
        name = normalize_member_name(
            presented, member_type, backslash_is_separator=False
        )

        link_target: str | None = None
        if entry.linkname is not None:
            link_target, _ = self._decode_name(
                entry.linkname, entry.linkname_source, entry.linkname_binary
            )

        timestamp_issues: list[TimestampIssue] = []
        modified: datetime | None
        if entry.pax and _pax_text(entry, b"mtime") is not None:
            modified, issue = _pax_time(entry, "mtime", presented)
            if issue is not None:
                timestamp_issues.append(issue)
        else:
            # A hostile out-of-range value (a base-256 mtime beyond datetime's range)
            # must not sink the whole listing, so it degrades to None and is reported.
            modified = unix_to_datetime(entry.header.mtime)
            if modified is None:
                timestamp_issues.append(
                    _tar_time_issue(
                        presented, "mtime", repr(entry.header.mtime), pax_record=False
                    )
                )

        compression = (
            _STORED_COMPRESSION
            if member_type in (MemberType.FILE, MemberType.HARDLINK)
            else ()
        )

        # The typeflag as stored: NUL for an old-style directory.
        extra = MemberExtra({"tar.type": entry.typeflag})
        if entry.pax:
            extra["tar.pax_headers"] = self._extra_pax_headers(entry)
        if entry.typeflag in _DEVICE_TYPES:
            extra["tar.devmajor"] = entry.header.devmajor
            extra["tar.devminor"] = entry.header.devminor

        member = ArchiveMember(
            type=member_type,
            name=name,
            raw_name=entry.name,
            size=entry.size if member_type == MemberType.FILE else None,
            modified=modified,
            # A GNU base-256 mode field can hold a negative or wider-than-32-bit value,
            # which stat.S_IMODE refuses with OverflowError. Masked first, as RAR does:
            # the permission bits are the low twelve, which is what S_IMODE keeps.
            mode=stat.S_IMODE(entry.header.mode & 0o7777),
            uid=entry.uid,
            gid=entry.gid,
            compression=compression,
            extra=extra,
            _raw=entry,  # carry the entry so _open_member needs no name/id lookup table
        )
        # Skip defaulted None/False fields on the listing hot path (perf review L2).
        if entry.pax:
            accessed, issue = _pax_time(entry, "atime", presented)
            if accessed is not None:
                member.accessed = accessed
            elif issue is not None:
                timestamp_issues.append(issue)
            # PAX ``ctime`` is st_ctime (inode change), never a birth time, so it is
            # ``ctime``, never ``created``.
            ctime, issue = _pax_time(entry, "ctime", presented)
            if ctime is not None:
                member.ctime = ctime
            elif issue is not None:
                timestamp_issues.append(issue)
            # libarchive writes the source's birth time, where the OS has one, as a PAX
            # extension keyword. It is the only TAR writer known to store a birth time.
            birth, issue = _pax_time(entry, "LIBARCHIVE.creationtime", presented)
            if birth is not None:
                member.created = birth
            elif issue is not None:
                timestamp_issues.append(issue)
        uname = self._owner_name(entry.uname, entry.uname_pax)
        if uname:
            member.uname = uname
        gname = self._owner_name(entry.gname, entry.gname_pax)
        if gname:
            member.gname = gname
        if link_target is not None and member_type in (
            MemberType.SYMLINK,
            MemberType.HARDLINK,
        ):
            member.link_target = link_target
        # All four GNU encodings: the old ``S`` typeflag and PAX sparse 0.0 / 0.1 /
        # 1.0, which GNU tar writes under ``--format=pax`` with a plain ``0`` typeflag.
        if entry.sparse_format is not None:
            member.is_sparse = True
        emit_member_name_normalized(
            self._diagnostics_collector,
            member=member,
            presented_name=presented,
            archive_name=self._archive_name,
            member_id=index,
        )
        for issue in timestamp_issues:
            self._emit_timestamp_invalid(member, index, issue)
        if inferred:
            # The UTF-8 reading was taken over the caller's encoding=, which ZIP and
            # RAR 1.5-4 report the same way.
            assert self._encoding is not None
            self._emit_name_encoding_inferred(
                member,
                index,
                inferred_encoding="utf-8",
                passed_over=self._encoding,
                message=(
                    f"TAR member name decoded as 'utf-8' rather than "
                    f"{self._encoding!r} (the stored bytes are valid UTF-8): "
                    f"{quoted(member.name)}"
                ),
            )
        return member

    def _owner_name(self, header: bytes, pax: PaxValue | None) -> str:
        """``uname`` or ``gname``: a PAX record of that name overrides the header's."""
        if pax is not None:
            return self._decode_name(pax.value, NameSource.PAX, pax.binary)[0]
        return self._decode_name(header, NameSource.HEADER, False)[0]

    def _open_member(self, member: ArchiveMember) -> ArchiveStream:
        with self._translated_errors(member.name):
            return self._open_member_stream(member, defer_sparse_error=False)

    def _open_member_stream(
        self,
        member: ArchiveMember,
        *,
        defer_sparse_error: bool,
        in_walk: bool = False,
    ) -> ArchiveStream:
        """Open ``member``'s data. A bad sparse map raises here, or with
        ``defer_sparse_error`` on the first read: a forward-only consumer that skips
        the member does not read the bad map, and the walk moves on to the next
        header by the member's stored size.

        ``in_walk`` is for a random-access pass: the data is read through the walk's
        own stream, as a streaming walk reads it, which saves the re-seek a separate
        view makes on every read. The pass closes the stream before the walk moves
        on, and no other listing can run during the pass."""
        entry = member._raw
        assert isinstance(entry, TarEntry), "TAR member is missing its entry"
        if entry.sparse is not None:
            sparse_error = validate_sparse_map(
                entry.sparse, entry.size, entry.stored_size, quoted(member.name)
            )
            if sparse_error is not None:
                if not defer_sparse_error:
                    raise sparse_error

                def _refuse(
                    error: CorruptionError | UnsupportedFeatureError = sparse_error,
                ) -> BinaryIO:
                    raise error

                return self._wrap_member_stream(
                    None, member.name, open_fn=_refuse, size=member.size
                )
        stream: BinaryIO
        if self._streaming or in_walk:
            stream = self._walker.open_data(entry)
        else:
            # A view of its own: reads re-seek the shared stream under the handle lock,
            # so members, and the walk, read independently.
            stream = SharedView(
                self._stream,
                entry.data_offset,
                entry.stored_size,
                lock=self._io_guard(),
            )
        if entry.sparse is not None:
            stream = SparseStream(
                stream, entry.sparse.offsets, entry.sparse.lengths, entry.size
            )
        if self._streaming:
            assert self._handle_lock is not None
            stream = LockedStream(stream, self._handle_lock)
        return self._wrap_member_stream(stream, member.name, size=member.size)

    def _get_archive_info(self) -> ArchiveInfo:
        stream_cap = self._source_stream_capability()
        if self._compressed:
            cost = CostReceipt(
                listing_cost=ListingCost.REQUIRES_DECOMPRESSION,
                access_cost=AccessCost.SOLID,  # one compression stream over all members
                stream_capability=stream_cap,
                solid_block_count=1,
            )
        else:
            cost = CostReceipt(
                listing_cost=ListingCost.REQUIRES_SCANNING,  # walk 512-byte headers, no index
                access_cost=AccessCost.DIRECT,  # each member is at a known, independent offset
                stream_capability=stream_cap,
                solid_block_count=None,
            )
        return ArchiveInfo(
            format=self._format,
            format_version=None,
            is_solid=self._compressed,
            member_count=None,  # no central directory: a count requires a full scan
            comment=None,
            is_encrypted=False,
            is_multivolume=False,
            cost=cost,
        )

    def _close_archive(self) -> None:
        # Close what this reader built, even when a close raises: teardown runs once.
        # The source closes with the reader, after this.
        with self._walk_guard():
            self._release_owned_stream()


class TarReadBackend(ReadBackend):
    """Backend factory for TAR archives (plain and compressed)."""

    FORMATS: tuple[ArchiveFormat, ...] = _TAR_FORMATS
    EXTENSIONS: Mapping[str, ArchiveFormat] = _TAR_EXTENSIONS
    # Plain tar is recognized by the POSIX/GNU "ustar" magic at offset 257. Compressed tars
    # carry only their outer codec's magic; detection's inner-TAR probe (see internal/
    # detection.py) decompresses a prefix and finds this same signature to report TAR_GZ etc.
    MAGIC: tuple[MagicSignature, ...] = (
        MagicSignature(257, b"ustar", ArchiveFormat.TAR),
    )
    # TAR is walkable front-to-back, so streaming=True works on a non-seekable source
    # (random access always needs a seekable one — that side is format-independent).
    SUPPORTS_STREAMING_NON_SEEKABLE = True
    USES_ENCODING = True  # the fallback codec for names that are not valid UTF-8

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
    ) -> TarReader:
        reject_start_offset(start_offset, format, archive_name)
        # `format` carries the concrete (TAR, <stream>) variant the detector/caller resolved;
        # the backend uses its stream to pick the codec to decompress with.
        return TarReader(
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


register_reader(TarReadBackend)
