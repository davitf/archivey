"""TAR backend on the v2 ABC, backed by the stdlib ``tarfile`` module.

On-disk layout (ustar/pax/gnu as ``tarfile`` sees it)::

    [ 512-byte header ][ file data, padded to 512 ]*
    [ 512 zero bytes ][ 512 zero bytes ]   # two null end-of-archive trailers

There is no central directory: listing is a header scan (``REQUIRES_SCANNING``) or a
progressive forward pass. Compressed forms (``.tar.gz`` / …) wrap the same layout in
a stream codec and behave as **solid** for random member opens.

Random-access reading (``streaming=False``) needs a seekable source (decompressing
first for a compressed tar) and opens any member on demand. Forward-only
(``streaming=True``) walks one progressive pass — including on a non-seekable source —
via ``_iter_with_data()`` / ``stream_members()``.

After a full scan or streaming pass, :meth:`_verify_tar_eof` checks the end:

- A rejected (non-null) header where ``tarfile`` stopped → ``CorruptionError``.
- A missing two-block null trailer → ``ARCHIVE_EOF_MARKER_MISSING``.
- A non-zero byte within ``_MAX_TRAILING_SCAN`` bytes of a *complete* trailer →
  ``ARCHIVE_TRAILING_DATA``. Zero padding passes.

Both codes follow the diagnostic policy like any other: a caller who wants either to
fail sets it to ``RAISE`` (``DiagnosticPolicy.strict()`` does so for both).

Note: after the header walk, ``tarfile`` has typically already consumed the
*first* trailer zero-block; the EOF probe therefore inspects the *next* 512 bytes.
"""

from __future__ import annotations

import stat
import tarfile
import threading
from contextvars import ContextVar, Token
from dataclasses import replace
from datetime import datetime
from io import SEEK_SET, BytesIO
from typing import BinaryIO, ContextManager, Iterator, Literal, Mapping, Self, cast

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
    MemberTimestampContext,
)
from archivey.exceptions import (
    ArchiveyError,
    CorruptionError,
    ReadError,
    ResourceLimitError,
    TruncatedError,
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
    _StreamChecksumError,
    codec_for_stream_format,
    open_codec_stream,
)
from archivey.internal.streams.streamtools import (
    DEFAULT_UNKNOWN_LENGTH_READ_STEP,
    LockedStream,
    ReadOnlyIOStream,
    ensure_binaryio,
    ensure_bufferedio,
    read_within_reach,
)
from archivey.internal.timestamps import unix_to_datetime
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
)

# Read size for the trailing-bytes scan. The tail past the trailer is unbounded (a
# concatenated archive, a padded record, arbitrary junk), so it is consumed in chunks
# rather than with one read().
_TRAILING_SCAN_CHUNK = 64 * 1024

# Read size for reading through the rest of a member's data on a forward-only walk
# (``TarReader._read_through_member_data``): a member can be any size, so it is read in
# chunks, never whole.
_READ_THROUGH_CHUNK = 64 * 1024

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

# Headers the random-access walk parses per handle-lock hold. Large enough that the
# walk runs as a dense pass (one header per hold measured about 1.3x slower on a
# 100 000-member listing), small enough that a partial batch is cheap to hold.
_HEADER_BATCH = 1024

# The largest position a seek can name. A file offset (``off_t``) and a ``BytesIO``
# position (``ssize_t`` on a 64-bit build) are signed 64-bit integers, so a target
# past this is not a place in any file: only an archive's size field can produce it.
_MAX_SEEK_OFFSET = 2**63 - 1

# What one GNU sparse map entry weighs against ``max_metadata_bytes``: the 24 bytes
# (two 12-byte numbers) the old GNU header spends on an entry. The retained
# ``(offset, numbytes)`` tuple in its list costs Python more than this (about 60 to
# 100 bytes), so the weight is an estimate on the low side, in keeping with a cap
# that counts text and not allocator bytes. The map is retained on the member's
# ``TarInfo``, which reads the data through it, and a PAX sparse 1.0 map lives in the
# data area, so no header text the cap already weighs stands in for it.
_SPARSE_ENTRY_BYTES = 24


def _sparse_map(info: tarfile.TarInfo) -> list[tuple[int, int]] | None:
    """``info.sparse``, typed as what tarfile stores there (typeshed says ``bytes``)."""
    return cast("list[tuple[int, int]] | None", info.sparse)


def _sparse_map_bytes(info: tarfile.TarInfo) -> int:
    """What ``info``'s retained sparse map weighs against ``max_metadata_bytes``."""
    sparse = _sparse_map(info)
    return len(sparse) * _SPARSE_ENTRY_BYTES if sparse else 0


def _header_text_bytes(info: tarfile.TarInfo) -> int:
    """Header text a member built from ``info`` retains, counted low.

    Counts only fields the base also weighs against ``max_metadata_bytes``, and the
    base weighs them at least as heavily (it adds ``raw_name`` and counts non-ASCII
    four to a character), so once this sum passes the cap the base has refused. The
    walk uses it to stop parsing where the byte cap would, without the base's running
    total. ``linkname`` counts only on a link: on any other member
    ``_drop_unweighed_link_name`` has already cleared it, so it is not retained.
    PAX records count keyword and value, as the base weighs both in
    ``extra["tar.pax_headers"]``. A sparse map counts what
    ``TarReader._register_member`` adds for it.
    """
    total = len(info.name) + len(info.uname) + len(info.gname)
    if info.issym() or info.islnk():
        total += len(info.linkname)
    for keyword, value in info.pax_headers.items():
        total += len(keyword) + len(value)
    return total + _sparse_map_bytes(info)


# Header types whose data ``tarfile`` reads whole into memory as header text, with one
# ``read(size)`` of the size the header declares: PAX extended (``x``, and Solaris
# ``X``) and global (``g``) headers, and GNU long names and link names (``L`` / ``K``).
_HEADER_DATA_TYPES = frozenset(
    (
        tarfile.XHDTYPE,
        tarfile.XGLTYPE,
        tarfile.SOLARIS_XHDTYPE,
        tarfile.GNUTYPE_LONGNAME,
        tarfile.GNUTYPE_LONGLINK,
    )
)

# ``(bytes left, cap)`` of ``max_metadata_bytes`` for the header ``tarfile`` is about to
# parse, or ``None`` when the cap is off. Set by the reader around each ``tarfile``
# call that parses headers (the open and each step of the walk), and read by
# ``_TarInfo._proc_member``, which ``tarfile`` calls with no way to pass it along. A
# context variable rather than state on the reader, because ``tarfile`` builds
# ``TarInfo`` objects from the class it is given and hands them no reader.
_HEADER_BUDGET: ContextVar[tuple[int, int] | None] = ContextVar(
    "_HEADER_BUDGET", default=None
)


class _HeaderBudget:
    """Sets :data:`_HEADER_BUDGET` for the ``tarfile`` calls in a ``with`` block."""

    def __init__(self, budget: tuple[int, int] | None) -> None:
        self._budget = budget
        self._token: Token[tuple[int, int] | None] | None = None

    def set(self, budget: tuple[int, int] | None) -> None:
        """Change the budget for the headers parsed after this, within the block."""
        _HEADER_BUDGET.set(budget)

    def __enter__(self) -> _HeaderBudget:
        self._token = _HEADER_BUDGET.set(self._budget)
        return self

    def __exit__(self, *exc_info: object) -> None:
        # Back to the value before the block, whatever ``set`` changed it to since.
        assert self._token is not None
        _HEADER_BUDGET.reset(self._token)


class _TarInfo(tarfile.TarInfo):
    """A ``TarInfo`` that records where its member's stored data ends, and refuses an
    extended header larger than the listing's metadata budget before reading it.

    ``tarfile`` keeps no record of how many bytes a sparse member stores: it replaces
    ``size`` with the logical size and reads the data through the sparse map, even
    where the map claims more than the member stores and the read runs on into the
    next header. :func:`_sparse_map_error` compares the map to this end.
    """

    __slots__ = ("stored_end",)

    stored_end: int
    """The offset where the member's data area ends, rounded up to whole blocks."""

    @classmethod
    def fromtarfile(cls, tarfile: tarfile.TarFile) -> Self:
        info = super().fromtarfile(tarfile)
        # ``TarFile.offset`` is where tarfile will look for the next header, which is
        # the end of this member's data area. For a header preceded by GNU long-name or
        # PAX headers this runs once per header, innermost first; the outermost call
        # runs last and sees the final offset, which a PAX ``size`` record may change.
        info.stored_end = tarfile.offset
        return info

    def _proc_member(self, tarfile: tarfile.TarFile) -> tarfile.TarInfo:
        """Refuse an extended header over the budget, then parse as ``tarfile`` does.

        ``_proc_member`` is the private call stdlib's own ``fromtarfile`` makes once
        the 512-byte header block is parsed, and tarfile's source names it as the
        method a subclass overrides. Here ``size`` is known and the header's data is
        not read yet, so a PAX header or GNU long name declaring megabytes costs the
        cap and not its own size: ``tarfile`` would read all of it in one call before
        any listing limit could weigh the result.
        """
        budget = _HEADER_BUDGET.get()
        if budget is not None and self.type in _HEADER_DATA_TYPES:
            left, cap = budget
            if self.size > left:
                raise ResourceLimitError(
                    f"Listing limit reached: max_metadata_bytes={cap} (a TAR extended "
                    f"header for {quoted(self.name)} declares {self.size} bytes, "
                    f"{max(left, 0)} left)"
                )
        # typeshed does not declare tarfile's private TarInfo._proc_member.
        return super()._proc_member(tarfile)  # pyrefly: ignore[missing-attribute]  # ty: ignore[unresolved-attribute]


def _sparse_map_error(info: tarfile.TarInfo) -> CorruptionError | None:
    """Refuse a sparse map that reads data from outside the member's own data area.

    ``tarfile`` reads the map's data chunks one after another from the start of the
    data area. A map whose chunks add up to more than the member stores reads the
    following header and members as this member's content, silently; a negative
    entry makes the read go backwards. ``tar(1)`` refuses both. The stored size is
    known only rounded up to whole blocks, so up to 511 bytes of the member's own
    zero padding can still be read as data; nothing past its data area can.

    The logical size (the GNU ``realsize`` field or ``GNU.sparse.realsize``) is held
    to the same bound as a plain size: past ``_MAX_SEEK_OFFSET`` it is no file's size,
    and tarfile's fill of the trailing hole would raise a raw ``OverflowError`` or
    ``MemoryError``.
    """
    sparse = _sparse_map(info)
    stored_end = getattr(info, "stored_end", None)
    if not sparse or stored_end is None:
        return None
    if info.size > _MAX_SEEK_OFFSET:
        return CorruptionError(
            f"TAR archive is corrupt: sparse member {quoted(info.name)} declares a "
            f"size of {info.size} bytes, past the largest offset any file can have"
        )
    stored = stored_end - info.offset_data
    total = 0
    for offset, numbytes in sparse:
        if offset < 0 or numbytes < 0:
            return CorruptionError(
                f"TAR sparse map of {quoted(info.name)} has a negative entry "
                f"(offset {offset}, {numbytes} bytes)"
            )
        total += numbytes
    if total > stored:
        return CorruptionError(
            f"TAR sparse map of {quoted(info.name)} claims {total} bytes of data, but "
            f"the member stores at most {stored}"
        )
    return None


def _raised_by_tarfile(exc: BaseException) -> bool:
    """Whether the innermost Python frame ``exc`` was raised in is stdlib ``tarfile``'s.

    ``tarfile`` parses some header values with a bare ``int()`` or tuple unpacking
    (the GNU sparse PAX records and the PAX sparse 1.0 map), so a malformed value
    escapes as a plain ``ValueError``. Where it was raised is what separates that from
    a ``ValueError`` of the stream under ``tarfile``, which is raised in that stream's
    own code and must propagate unchanged.
    """
    tb = exc.__traceback__
    if tb is None:
        return False
    while tb.tb_next is not None:
        tb = tb.tb_next
    return tb.tb_frame.f_globals.get("__name__") == tarfile.__name__


def _passes_through_tarfile(exc: BaseException) -> bool:
    """Whether any frame ``exc`` unwound through is stdlib ``tarfile``'s."""
    tb = exc.__traceback__
    while tb is not None:
        if tb.tb_frame.f_globals.get("__name__") == tarfile.__name__:
            return True
        tb = tb.tb_next
    return False


def _drop_unweighed_link_name(info: tarfile.TarInfo) -> None:
    """Clear ``linkname`` on a member that is not a link.

    A GNU long link name or PAX linkpath ahead of a header that is not a link has no
    meaning, and no listing limit weighs it. Clearing it as the header is parsed keeps
    it from being held for the rest of a batch, or on the retained ``TarInfo``.
    """
    if not (info.issym() or info.islnk()):
        info.linkname = ""


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


def _member_type(info: tarfile.TarInfo) -> MemberType:
    if info.isdir():
        return MemberType.DIRECTORY
    if info.issym():
        return MemberType.SYMLINK
    if info.islnk():
        return MemberType.HARDLINK
    if info.isfile():
        return MemberType.FILE
    # Character/block devices, FIFOs, contiguous files, GNU long-name placeholders, …
    return MemberType.OTHER


# Shared across FILE/HARDLINK members — avoid per-member CompressionMethod construction.
_STORED_COMPRESSION: tuple[CompressionMethod, ...] = (
    CompressionMethod(algo=CompressionAlgorithm.STORED),
)


def _pax_time(info: tarfile.TarInfo, key: str) -> datetime | None:
    """Parse a PAX time record (float Unix seconds) into a tz-aware UTC datetime.

    ``tarfile`` folds the PAX ``mtime`` into ``TarInfo.mtime`` itself, but leaves the
    access and inode-change times, and libarchive's ``LIBARCHIVE.creationtime``
    extension keyword (not a standard PAX record), only in ``pax_headers``; surface
    them here.
    """
    raw = info.pax_headers.get(key)
    if raw is None:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        return None
    return unix_to_datetime(seconds)


class _EofProbeStream(ReadOnlyIOStream):
    """Transparent read/seek proxy over the seekable fileobj handed to stdlib
    ``tarfile`` in random-access mode, remembering the ``(offset, bytes)`` of the most
    recent ``read`` (empty reads included).

    After the header scan, that read is tarfile's attempt to parse a header at the
    end-of-archive position (``TarFile.next()`` always tries one more block before
    returning ``None``). A full non-null block there is a rejected header — including when
    it is the archive's final block, and including after a GNU sparse member whose
    logical ``size`` does not match the physical packed end. Relying on the scan's last
    read (rather than ``offset_data + roundup(size)``) avoids that false negative without
    seeking backwards, which on a compressed source would force a re-decompression.

    tarfile treats this as an external fileobj (``read``/``seek``/``tell``/``seekable``
    only) and never closes it; the reader closes what it wraps — the decompressor via
    ``_owned_stream``, the source by closing the source. It subclasses
    :class:`ReadOnlyIOStream` so it is the ``BinaryIO`` it is passed as, with no cast;
    that base also gives it ``mode == "rb"`` and no ``name``, which is what tarfile
    reads off an external fileobj.

    Over a decompressor it is the one place a read sized from the archive can be
    bounded: the source's own bound sits under the codec, not in front of ``tarfile``.
    Over the source itself (a plain tar) the source already bounds, so ``bounded=False``
    passes reads straight through rather than bounding the raw case twice.
    ``TarInfo._proc_pax`` and ``_proc_gnulong`` each issue a single ``read(self._block(self.size))`` for a PAX
    extended header or a GNU long name, where ``size`` is the 12-byte octal field of a
    ``typeflag`` ``x`` / ``L`` / ``K`` header — up to 8 GiB, and further through GNU
    base-256. ``BufferedReader.read(n)`` allocates ``n`` up front, so the allocation
    lands before the short read reveals the archive is three kilobytes. ``read`` below
    therefore asks the wrapped stream in steps rather than for the whole size at once.
    """

    # The step this backend reads in when the source's length is unknown: the
    # compressed path, whose length would cost a decompression pass to learn, and any
    # caller-supplied stream that advertises none. What the number buys, and why
    # splitting is the normal case rather than an exception, is documented once on
    # :data:`DEFAULT_UNKNOWN_LENGTH_READ_STEP`, beside the branch it governs.
    _UNKNOWN_LENGTH_READ_STEP = DEFAULT_UNKNOWN_LENGTH_READ_STEP

    def __init__(self, inner: BinaryIO, *, bounded: bool = True) -> None:
        super().__init__()
        self._inner = inner
        self._bounded = bounded
        # Offsets share tarfile's coordinate space (both anchored at the wrapped
        # stream's current position), so they compare directly to TarInfo offsets.
        self._pos = inner.tell() if inner.seekable() else 0
        self.last_read: tuple[int, bytes] = (-1, b"")

    def read(self, size: int = -1, /) -> bytes:
        offset = self._pos
        chunk = self._read_within_reach(size)
        self._pos += len(chunk)
        self.last_read = (offset, chunk)
        return chunk

    def _read_within_reach(self, size: int) -> bytes:
        """``read`` without committing to the allocation the archive asked for.

        The rule itself lives in :func:`read_within_reach`, because the source bounds
        every raw read by exactly the same one.
        """
        if not self._bounded:
            return self._inner.read(size)
        # Stepped, never clamped: the one bounded caller wraps a decompressor, whose
        # length is not a fact (a gzip ISIZE wraps past 4 GiB and can understate).
        return read_within_reach(
            self._inner, size, remaining=None, step=self._UNKNOWN_LENGTH_READ_STEP
        )

    def seek(self, offset: int, whence: int = 0, /) -> int:
        # tarfile seeks to offsets it adds up from the archive's size fields: to the
        # next header past a member, and to a sparse member's data chunks. A PAX or
        # base-256 size can put that past what a seek can take, and the stream under
        # would raise OverflowError, ValueError or OSError(EINVAL) depending on its
        # type. Refused here, so none of those is taken for a fault of the stream.
        if whence == SEEK_SET and offset > _MAX_SEEK_OFFSET:
            raise CorruptionError(
                f"TAR archive is corrupt: a size field puts data at byte {offset}, "
                "past the largest offset any file can have"
            )
        self._inner.seek(offset, whence)
        self._pos = self._inner.tell()
        return self._pos

    def tell(self, /) -> int:
        return self._pos

    def seekable(self) -> bool:
        return self._inner.seekable()

    def close(self) -> None:
        # No-op: the reader owns the wrapped stream's lifetime (``_owned_stream``); a
        # stray tarfile call must not tear the shared handle down early. So ``closed``
        # stays False for the probe's whole life. Nothing reads it: tarfile never
        # checks an external fileobj's ``closed``, and the probe never leaves this
        # module.
        pass


class TarReader(BaseArchiveReader):
    """Reads a TAR archive (plain or compressed) via stdlib ``tarfile``.

    ``_SUPPORTS_RANDOM_ACCESS`` is True (seekable uncompressed / decompressed sources
    can open any member), but ``_MEMBER_LIST_UPFRONT`` is False — there is no central
    directory, so a complete list always requires a scan (or a finished stream pass).
    """

    _SUPPORTS_RANDOM_ACCESS = True
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
        self._source = source
        self._compressed = format.stream != StreamFormat.UNCOMPRESSED
        # Random-access EOF probe: set when the fileobj is wrapped (non-streaming opens),
        # snapshotted into ``_eof_header_rejected`` right after the header scan.
        self._eof_probe_stream: _EofProbeStream | None = None
        self._eof_header_rejected: bool = False
        # Whether the walk's current pull enforces the listing limits (see
        # _pull_member). True until a pull says otherwise: the first header is parsed
        # at open, where only the whole cap can bind anyway.
        self._listing_enforced = True
        # Set while a random-access stream_members() pass reads data as it walks
        # (_iter_with_data_random_access): the walk then parses one header per pull.
        self._one_header_at_a_time = False
        # The decompression stream of a compressed tar, which this reader builds and so
        # must close. tarfile is always handed ``fileobj=``, so it never owns what it
        # reads; the source itself closes with the reader.
        self._owned_stream: BinaryIO | None = None
        # The codec stream under ``_owned_stream``. ``ensure_bufferedio`` wraps it in a
        # buffer that detaches on close rather than closing it, so it is closed here
        # explicitly: left to the garbage collector, a stream held by a failed open's
        # traceback kept its rapidgzip child process running.
        self._owned_codec_stream: BinaryIO | None = None
        # Shared-handle lock: CONCURRENT readers serialize every shared-fileobj op;
        # streaming readers also take a lock (exclusive / normally uncontended) so the
        # same critical-section shape covers init, progressive walk, extractfile, EOF,
        # and close (tar-concurrent-open 2.6).
        self._handle_lock = (
            threading.Lock()
            if MemberStreams.CONCURRENT in member_streams or streaming
            else None
        )

        try:
            # tarfile parses the first member's headers as it opens.
            with self._handle_guard(), _HeaderBudget(self._header_budget(0)):
                self._tar = self._open_tarfile(
                    source, format, streaming, member_streams=member_streams
                )
        except (tarfile.TarError, ValueError, RecursionError) as exc:
            # Only tarfile's own (format) errors are translated, and the ValueError and
            # RecursionError it raises on a malformed first header (see
            # _translate_exception); anything else, and a genuine OSError from the
            # underlying handle, propagates unchanged (see error-handling: "Genuine
            # runtime and I/O errors are not reclassified").
            # Release before re-raising: the exception traceback keeps this frame alive and
            # would otherwise pin the owned fp until the caller drops the exception
            # (inventory/fuzz catch-and-continue loops).
            self._release_owned_stream()
            translated = self._translate_open_error(exc)
            if translated is None:
                raise
            raise translated from exc
        except ArchiveyError as exc:
            # Raised by this module from inside tarfile's parse (an extended header
            # over the metadata cap, an impossible seek): typed already, so only the
            # context stamp is missing.
            self._release_owned_stream()
            self._stamp_error_context(exc)
            raise
        except BaseException:
            self._release_owned_stream()
            raise

    def _release_owned_stream(self) -> None:
        """Close a stream this reader opened, if any. Safe to call more than once."""
        try:
            if self._owned_stream is not None:
                self._owned_stream.close()
                self._owned_stream = None
        finally:
            if self._owned_codec_stream is not None:
                self._owned_codec_stream.close()
                self._owned_codec_stream = None

    def _open_tarfile(
        self,
        source: ArchiveSource,
        format: ArchiveFormat,
        streaming: bool,
        *,
        member_streams: MemberStreams,
    ) -> tarfile.TarFile:
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
            # tarfile can mis-handle a short read() (fewer bytes than requested) from a
            # decompressor; a BufferedReader in front guarantees full-sized reads. The cast
            # is typeshed's split: BufferedIOBase is not BinaryIO there, but is at runtime.
            self._owned_codec_stream = stream
            self._owned_stream = cast("BinaryIO", ensure_bufferedio(stream))
            return self._tarfile_open(
                fileobj=self._wrap_eof_probe(self._owned_stream, streaming),
                streaming=streaming,
            )
        # A plain tar reads the source itself, which is full-count and bounded; the
        # probe in front of it only watches. Do NOT slurp a path into a BytesIO — that
        # would force the whole archive into memory up front.
        return self._tarfile_open(
            name=str(source.path) if source.path is not None else None,
            fileobj=self._wrap_eof_probe(
                self._track_source_seeks(source), streaming, bounded=False
            ),
            streaming=streaming,
        )

    def _wrap_eof_probe(
        self,
        fileobj: BinaryIO,
        streaming: bool,
        *,
        bounded: bool = True,
    ) -> BinaryIO:
        """Wrap a random-access fileobj so the end-of-archive check can inspect the block
        tarfile stopped on. Forward-only (streaming) opens get no probe — tarfile's
        ``_Stream`` hides its header reads and a consumed block cannot be recovered there.

        That is also why bounding a header-sized read only happens here: ``r|`` needs no
        bound, tarfile's own ``_Stream.read`` looping in ``bufsize`` chunks, and ``r:``
        is the mode that hands a raw handle through. Over a decompressor the read is
        stepped (see :class:`_EofProbeStream`); a plain tar passes ``bounded=False``: the
        source it wraps bounds its own reads.
        """
        if streaming:
            return fileobj
        probe = _EofProbeStream(fileobj, bounded=bounded)
        self._eof_probe_stream = probe
        return probe

    def _tarfile_open(
        self,
        *,
        name: str | None = None,
        fileobj: BinaryIO | None = None,
        streaming: bool = False,
    ) -> tarfile.TarFile:
        # mode="r:" reads an *uncompressed* tar stream with random access; mode="r|" is
        # forward-only (required for non-seekable sources). We feed either the raw file
        # (plain tar) or our own decompressor (compressed tar), never tarfile's native
        # r:gz/r:bz2 modes.
        mode = "r|" if streaming else "r:"
        return tarfile.open(
            name=name,
            fileobj=fileobj,
            mode=mode,
            tarinfo=_TarInfo,
            errorlevel=1,  # raise on fatal read errors (truncation/corruption surface below)
            # UTF-8 unless the caller passed encoding=. tarfile's own default,
            # tarfile.ENCODING, is the process filesystem encoding on POSIX, so the
            # same archive would list differently under a non-UTF-8 locale. tarfile
            # keeps its errors="surrogateescape" default, so undecodable bytes survive
            # as U+DC80..U+DCFF. ustar/GNU names (and uname/gname/linkname) always use
            # this codec. A PAX record is decoded strictly as UTF-8 first and falls
            # back to this codec when that fails (or for its own hdrcharset=BINARY), so
            # it reaches PAX bytes that are not UTF-8 too. There is no config-level
            # default as ZIP has: ZIP's fallback codec serves a name it sniffed as not
            # UTF-8, and TAR sniffs nothing, so encoding= per call is the override.
            encoding=self._encoding if self._encoding is not None else "utf-8",
        )

    def _translate_open_error(self, exc: Exception) -> ArchiveyError | None:
        """The typed error for ``exc`` raised while opening, or ``None`` to re-raise it.

        Every ``tarfile.TarError`` is typed: an error of the library's own the
        translator does not name still says the file is not a TAR it can read.
        """
        translated = self._translate_exception(exc)
        if translated is None:
            if not isinstance(exc, tarfile.TarError):
                return None
            translated = CorruptionError(f"Could not open TAR archive: {exc!r}")
        self._stamp_error_context(translated)
        return translated

    def _translate_exception(self, exc: Exception) -> ArchiveyError | None:
        if isinstance(exc, tarfile.ReadError):
            text = str(exc).lower()
            if "end of data" in text or "truncat" in text or "empty file" in text:
                return TruncatedError(f"TAR archive is truncated: {exc!r}")
            return CorruptionError(f"Error reading TAR archive: {exc!r}")
        if isinstance(exc, tarfile.StreamError):
            # A forward-only read that would have to go backwards ("seeking backwards
            # is not allowed"). tarfile reads a member's data chunks in the order its
            # sparse map gives them, so only a map with a negative or out-of-order
            # entry gets here.
            return CorruptionError(f"Error reading TAR archive: {exc!r}")
        if isinstance(exc, EOFError):
            return TruncatedError(f"TAR archive is truncated: {exc!r}")
        if isinstance(exc, ValueError) and _raised_by_tarfile(exc):
            # A header value tarfile parses with a bare int() or tuple unpack: a GNU
            # sparse PAX record (GNU.sparse.map / size / realsize) or a PAX sparse 1.0
            # map that is not a list of integers.
            return CorruptionError(f"Malformed TAR header value: {exc!r}")
        if isinstance(exc, RecursionError) and _passes_through_tarfile(exc):
            # tarfile parses the header after a GNU long-name/long-link or PAX header
            # from inside the call that parsed that header, so a long enough chain of
            # them exhausts the interpreter's stack. Writers emit at most a few ahead of
            # one member. The chain is not bounded before that: it has no size of its
            # own a caller could configure, and the point where it fails depends on how
            # deep the caller's stack already is.
            return CorruptionError(
                "TAR archive is corrupt: too long a chain of GNU long-name or PAX "
                "extended headers"
            )
        return None

    def _iter_members(self) -> Iterator[ArchiveMember]:
        if self._streaming:
            yield from self._iter_members_progressive()
            return
        # Headers are pulled in batches rather than through getmembers(): the base
        # counts each yielded member against ``max_members`` and ``max_metadata_bytes``,
        # and a batch never reaches past what either cap has left (see
        # _header_batch_size and _header_text_bytes), so a header bomb stops at the
        # cap plus one header instead of after tarfile has parsed and kept every
        # header in the file. Batches rather than one header per lock hold, because alternating
        # header parsing with member construction measured about 1.3x slower on an
        # ordinary 100 000-member listing; at 1 024 the difference is within noise.
        # ``iter(self._tar)`` rather than bare next() calls, because it serves headers
        # tarfile already loaded from its own list before reading more.
        #
        # A member is opened while this walk runs only in a one-pass
        # stream_members() (_iter_with_data_random_access), which parses one header
        # per batch and reads each member before the next header, so every seek goes
        # forward. Otherwise the base hands out no member of a random-access listing
        # until the walk has ended. TarFile.next() re-seeks to its own offset after a
        # read elsewhere, so the walk stays correct either way; what a read behind the
        # walk costs is a backward seek, on a compressed tar a decode from the start.
        tar_iter = iter(self._tar)
        index = 0
        ended = False
        byte_cap = self._config.listing_limits.max_metadata_bytes
        text_bytes = 0
        while not ended:
            want = self._header_batch_size(index)
            # Only the batch that crosses the byte cap is cut short. The count is a
            # lower bound on the base's, so past it the base has refused already, or
            # is not enforcing, and a batch of one would be the slow walk batching
            # exists to avoid.
            byte_stop = (
                byte_cap if byte_cap is not None and text_bytes <= byte_cap else None
            )
            batch: list[tarfile.TarInfo] = []
            failure: CorruptionError | None = None
            # The error boundary sits OUTSIDE the handle guard, so translation and
            # stamping never run under the shared-fileobj lock. An exception the
            # translator does not recognize (a genuine OSError from the source)
            # propagates unchanged.
            try:
                with self._translated_errors():
                    # Pinned-library audit: TarFile.next() drives seek/tell/read on
                    # the shared fileobj, so it runs under the handle lock.
                    with (
                        self._handle_guard(),
                        _HeaderBudget(self._header_budget(text_bytes)) as budget,
                    ):
                        for info in tar_iter:
                            _drop_unweighed_link_name(info)
                            batch.append(info)
                            text_bytes += _header_text_bytes(info)
                            budget.set(self._header_budget(text_bytes))
                            if len(batch) == want or (
                                byte_stop is not None and text_bytes > byte_stop
                            ):
                                break
                        else:
                            ended = True
                            # Snapshot the EOF probe now, while the last read is still
                            # the block tarfile stopped on.
                            self._capture_eof_probe(index + len(batch) > 0)
            except CorruptionError as exc:
                # Hand out the headers this batch already parsed first, so a
                # members_report() keeps the same salvaged prefix it would have had
                # one header at a time. These two are the only errors the base ends
                # a walk on with its prefix kept; on any other it discards the
                # listing, so there is nothing to hand out.
                failure = exc
            for info in batch:
                yield self._to_member(info, index)
                index += 1
            if failure is not None:
                raise failure
        self._verify_tar_eof()

    def _register_member(
        self,
        idx: int,
        member: ArchiveMember,
        *,
        enforce_listing_limits: bool = False,
    ) -> None:
        """Register as the base does, then weigh a sparse member's retained map.

        The map can hold millions of entries from a few kilobytes of compressed
        archive, and it stays on the member's ``TarInfo`` for the life of the listing.
        """
        super()._register_member(
            idx, member, enforce_listing_limits=enforce_listing_limits
        )
        info = member._raw
        if isinstance(info, tarfile.TarInfo):
            sparse_bytes = _sparse_map_bytes(info)
            if sparse_bytes:
                self._listing_tracker.account_retained_bytes(
                    sparse_bytes, enforce=enforce_listing_limits
                )

    def _header_budget(self, counted: int) -> tuple[int, int] | None:
        """``(bytes left, cap)`` of ``max_metadata_bytes`` for the next header parsed.

        A random-access walk the base is enforcing has ``cap - counted`` left,
        ``counted`` being the walk's low count of what it has parsed so far
        (:func:`_header_text_bytes`), so what is left is never understated. A walk the
        base is not enforcing (``stream_members()``), and a streaming walk, which
        never enforces the running total (threat-model O1), still may not parse one
        header larger than the whole cap. So may not a walk whose count is past the
        cap: the base refuses at the member that crosses it.
        """
        cap = self._config.listing_limits.max_metadata_bytes
        if cap is None:
            return None
        if self._streaming or not self._listing_enforced or counted > cap:
            return (cap, cap)
        return (cap - counted, cap)

    def _pull_member(self, *, enforce: bool) -> ArchiveMember | None:
        """Pull as the base does, noting whether it enforces the listing limits.

        The random-access walk parses headers a batch ahead of registration, so this
        is how it learns whether a header over what is left of ``max_metadata_bytes``
        would be refused anyway (``members()``) or must still list
        (``stream_members()``).
        """
        self._listing_enforced = enforce
        return super()._pull_member(enforce=enforce)

    def _iter_with_data_random_access(
        self,
    ) -> Iterator[tuple[ArchiveMember, ArchiveStream | None]]:
        """``stream_members()`` on a random-access reader, in one forward pass.

        The base's version lists every member first and then reads their data, which
        on a compressed tar decodes the stream to its end for the headers and then again
        from the start for the data. Here, when the walk has not ended yet, the walk and
        the data share one pass: the walk parses one header at a time
        (:meth:`_header_batch_size`), the member is registered and yielded, and its data
        is read from where the header left the stream, so every seek goes forward.
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

        def _open(member: ArchiveMember) -> ArchiveStream | None:
            return self._lazy_member_stream(member) if member.is_file else None

        self._one_header_at_a_time = True
        try:
            yield from self._drive_pass_streams(
                _ProgressivePassIterator(self), open_member=_open
            )
        finally:
            self._one_header_at_a_time = False

    def _extraction_listing(self) -> ContextManager[None]:
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

    def _header_batch_size(self, listed: int) -> int:
        """How many headers the random-access walk may parse next: a full batch, or
        what ``max_members`` has left plus the one header that trips it. Once that
        header is listed the cap is not being enforced (``stream_members()`` on a
        random-access reader), so the walk goes back to full batches. During a
        one-pass ``stream_members()`` it is one header, so the walk never runs ahead
        of the member whose data is being read."""
        if self._one_header_at_a_time:
            return 1
        cap = self._config.listing_limits.max_members
        if cap is None or listed > cap:
            return _HEADER_BATCH
        return min(_HEADER_BATCH, cap - listed + 1)

    def _iter_members_progressive(self) -> Iterator[ArchiveMember]:
        """Forward-only member walk — never calls ``getmembers()``.

        Yields bare members; the base's shared progressive pass stamps ids and resolves
        backward links. Every shared-handle op runs under ``_handle_lock``, which a
        streaming reader always has (the constructor creates it for ``streaming``).
        """
        lock = self._handle_lock
        assert lock is not None, "a streaming TAR reader always holds a handle lock"
        with self._translated_errors():
            # Hold the lock only around each next() so a yielded consumer can open
            # the current member without deadlock (streaming is single-owner).
            tar_iter = iter(self._tar)
            index = 0
            while True:
                with lock:
                    if index:
                        # Not before the first member: tarfile parsed its header at
                        # open, and its data is still the consumer's to read.
                        self._read_through_member_data()
                    try:
                        with _HeaderBudget(self._header_budget(0)):
                            info = next(tar_iter)
                    except StopIteration:
                        break
                yield self._to_member(info, index)
                index += 1
        self._verify_tar_eof()

    def _read_through_member_data(self) -> None:
        """Read what is left of the last member's data area, before the next header.

        A forward-only ``tarfile`` skips a member the consumer did not read by reading
        through it, but in steps sized from the member's declared size, and without
        noticing that the steps come back empty: a few kilobytes of archive declaring
        a 2**45-byte member keep it looping for hours. Reading through here first
        costs the bytes the archive actually holds, the same bytes tarfile would have
        read on an honest archive, and stops at the first short read. tarfile's own
        skip then has nothing left to do.

        ``TarFile.offset`` is where tarfile will look for the next header: the end of
        the member's data area in whole blocks, which for a sparse member is its
        stored size, not its logical one. Whatever the consumer already read through
        ``extractfile`` has moved the stream's position on, so only the rest is read.
        Reads are bounded chunks, never the whole member.
        """
        fileobj = self._tar.fileobj
        if fileobj is None:
            return
        data_end = self._tar.offset
        position = fileobj.tell()
        while position < data_end:
            want = min(_READ_THROUGH_CHUNK, data_end - position)
            got = len(fileobj.read(want))
            position += got
            if got < want:
                raise TruncatedError(
                    f"TAR archive is truncated: a member's data area ends at byte "
                    f"{data_end}, but the archive ends at byte {position}"
                )

    def _iter_with_data(
        self, copies: FileCopyPass = DEFAULT_FILE_COPY_PASS
    ) -> Iterator[tuple[ArchiveMember, ArchiveStream | None]]:
        if not self._streaming:
            yield from self._iter_with_data_random_access()
            return
        # Pull from the shared instance-held progressive pass so __iter__,
        # stream_members, and scan_members share one cursor and finalization.
        # close_previous=False: tarfile invalidates the prior extractfile handle on
        # advance; tracking previous would be incorrect.
        # The driver's finally closes the last stream inside this translation context
        # (pre-unify, only stream_members's finally closed it, outside translation).
        # Close-time faults on the final member now surface typed; the outer
        # stream_members finally still idempotently closes afterward.
        with self._translated_errors():

            def _open(member: ArchiveMember) -> ArchiveStream | None:
                if not member.is_file:
                    return None
                info = member._raw
                assert isinstance(info, tarfile.TarInfo), (
                    "TAR member is missing its TarInfo handle"
                )
                sparse_error = _sparse_map_error(info)
                if sparse_error is not None:
                    # Raised on the first read, as a random-access open raises it: a
                    # consumer that skips this member does not read the bad map, and
                    # tarfile moves on to the next header by the member's stored end.
                    def _refuse(error: CorruptionError = sparse_error) -> BinaryIO:
                        raise error

                    return self._wrap_member_stream(
                        None, member.name, open_fn=_refuse, size=member.size
                    )
                with self._handle_guard():
                    raw = self._tar.extractfile(info)
                if raw is None:
                    raw = BytesIO(b"")
                stream: BinaryIO = ensure_binaryio(raw)
                if self._handle_lock is not None:
                    stream = LockedStream(stream, self._handle_lock)
                return self._wrap_member_stream(stream, member.name, size=member.size)

            yield from self._drive_pass_streams(
                self._begin_forward_pass(),
                open_member=_open,
                close_previous=False,
            )

    def _capture_eof_probe(self, any_members: bool) -> None:
        """Snapshot whether tarfile stopped the header scan on a *rejected* (non-null)
        header block, using the random-access EOF probe.

        When ``TarFile.next()`` returns ``None`` it has always attempted one more header
        read first, so the probe's ``last_read`` *is* the block tarfile stopped on —
        independent of the live handle position (later member extraction may seek away)
        and independent of ``offset_data + roundup(size)`` (wrong for GNU sparse, where
        logical size ≫ packed size). A full non-null block there is a rejected header,
        including when it is the archive's final block (which the trailing-block check
        in :meth:`_verify_tar_eof` reads past and cannot see).
        """
        self._eof_header_rejected = False
        probe = self._eof_probe_stream
        if probe is None or not any_members:
            return
        _offset, chunk = probe.last_read
        if len(chunk) == 512 and chunk != b"\x00" * 512:
            self._eof_header_rejected = True

    def _verify_tar_eof(self) -> None:
        """Verify the two-block null end-of-archive marker and surface a rejected header
        as corruption.

        In random-access mode ``_capture_eof_probe`` has already inspected the block
        tarfile stopped on. A full non-null block there means tarfile rejected a header —
        a corrupt member header after the first, treated as a silent early end, including
        when it is the archive's *final* block — which escalates to ``CorruptionError``
        whatever the diagnostic policy says.

        Otherwise (and for forward-only streaming, which has no probe) it inspects the
        block following tarfile's stop. ``tarfile`` has already consumed the *first* null
        trailer block (stopping on it via ``EOFHeaderError`` with ``ignore_zeros=False``),
        so we only confirm the *second*: reading two blocks here would demand a third
        block of trailing zeros and wrongly flag a minimal ``tar -b1`` trailer. Two null
        blocks are valid; a non-null block is corruption (a rejected trailer/header); a
        short or empty read is a truncated or absent trailer, reported as
        ``ARCHIVE_EOF_MARKER_MISSING`` under the ordinary diagnostic policy.

        Streaming cannot see a rejected *final* header (tarfile's ``_Stream`` hides the
        block and it cannot be recovered without re-reading), so that one case surfaces as
        a missing-trailer warning there rather than corruption — see
        ``dev-docs/known-issues.md``.
        """
        if self._eof_header_rejected:
            self._emit_eof_marker(
                observed_bytes=512, observed_kind="nonzero", corrupt=True
            )
            return
        fileobj = self._tar.fileobj
        if fileobj is None:
            return
        with self._handle_guard():
            chunk = fileobj.read(512)
        if len(chunk) == 512 and chunk == b"\x00" * 512:
            self._verify_nothing_but_zeros_to_eof()
            return
        if len(chunk) == 512:
            # A non-null block where the second trailer block belongs: tarfile treated a
            # bad block as a clean end (or trailing junk followed a lone zero block).
            self._emit_eof_marker(
                observed_bytes=512, observed_kind="nonzero", corrupt=True
            )
            return
        observed_kind: Literal["absent", "short"] = (
            "absent" if len(chunk) == 0 else "short"
        )
        self._emit_eof_marker(
            observed_bytes=len(chunk), observed_kind=observed_kind, corrupt=False
        )

    def _verify_nothing_but_zeros_to_eof(self) -> None:
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
        fileobj = self._tar.fileobj
        if fileobj is None:
            return
        offset = 0
        while offset <= _MAX_TRAILING_SCAN:
            # One byte past the bound, to learn whether the stream ended there.
            want = min(_TRAILING_SCAN_CHUNK, _MAX_TRAILING_SCAN - offset) or 1
            try:
                with self._translated_errors(), self._handle_guard():
                    chunk = fileobj.read(want)
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

    def _emit_eof_marker(
        self,
        *,
        observed_bytes: int,
        observed_kind: Literal["absent", "short", "nonzero"],
        corrupt: bool,
    ) -> None:
        if corrupt:
            message = (
                "TAR archive is corrupt: a non-null block appears where the "
                "end-of-archive marker was expected. Stdlib tarfile treats a corrupt "
                "member header after the first as a clean end of archive, so a silently "
                "shortened listing surfaces here."
            )
            escalate_as: type[BaseException] | None = CorruptionError
        else:
            message = (
                "TAR archive may be truncated: missing or short end-of-archive marker "
                "block(s)."
            )
            escalate_as = None
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
                expected_marker="two_zero_blocks",
                expected_bytes=1024,
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

    def _to_member(self, info: tarfile.TarInfo, index: int) -> ArchiveMember:
        """Type one member. ``index`` is its position in the walk, the id registration
        stamps, so the diagnostics raised here can name it before it has one."""
        member_type = _member_type(info)
        # TAR is a POSIX format: a backslash is a legal filename character, not a separator.
        presented = info.name
        name = normalize_member_name(
            presented, member_type, backslash_is_separator=False
        )
        raw_name = _recover_raw_name(
            info, self._tar.encoding, self._tar.errors, self._tar.pax_headers
        )

        # The random-access walk has already dropped a non-link's linkname as it
        # parsed the header; the streaming walk parses one header at a time and
        # drops it here.
        _drop_unweighed_link_name(info)
        link_target = (
            info.linkname
            if member_type in (MemberType.SYMLINK, MemberType.HARDLINK)
            else None
        )

        # tarfile folds a PAX mtime (sub-second/timezone) into TarInfo.mtime already, so this
        # one field honors both the standard ustar mtime and the PAX override. A hostile
        # out-of-range value (e.g. a crafted PAX mtime beyond datetime's range) must not
        # sink the whole listing, so it degrades to None like _pax_time does.
        modified = unix_to_datetime(info.mtime)
        mtime_invalid = modified is None

        compression = (
            _STORED_COMPRESSION
            if member_type in (MemberType.FILE, MemberType.HARDLINK)
            else ()
        )

        extra = MemberExtra({"tar.type": info.type})
        if info.pax_headers:
            extra["tar.pax_headers"] = dict(info.pax_headers)
        if info.isdev():
            extra["tar.devmajor"] = info.devmajor
            extra["tar.devminor"] = info.devminor

        member = ArchiveMember(
            type=member_type,
            name=name,
            raw_name=raw_name,
            size=info.size if member_type == MemberType.FILE else None,
            modified=modified,
            # A GNU base-256 mode field can hold a negative or wider-than-32-bit value,
            # which stat.S_IMODE refuses with OverflowError. Masked first, as RAR does:
            # the permission bits are the low twelve, which is what S_IMODE keeps.
            mode=stat.S_IMODE(info.mode & 0o7777),
            uid=info.uid,
            gid=info.gid,
            compression=compression,
            extra=extra,
            _raw=info,  # carry the TarInfo so _open_member needs no name/id lookup table
        )
        # Skip defaulted None/False fields on the listing hot path (perf review L2).
        accessed = _pax_time(info, "atime")
        if accessed is not None:
            member.accessed = accessed
        # PAX ``ctime`` is st_ctime (inode change), never a birth time, so it is
        # ``ctime``, never ``created``.
        ctime = _pax_time(info, "ctime")
        if ctime is not None:
            member.ctime = ctime
        # libarchive writes the source's birth time, where the OS has one, as a PAX
        # extension keyword. It is the only TAR writer known to store a birth time.
        birth = _pax_time(info, "LIBARCHIVE.creationtime")
        if birth is not None:
            member.created = birth
        if info.uname:
            member.uname = info.uname
        if info.gname:
            member.gname = info.gname
        if link_target is not None:
            member.link_target = link_target
        # issparse() covers all four GNU encodings: the old ``S`` typeflag and PAX
        # sparse 0.0 / 0.1 / 1.0, which GNU tar writes under ``--format=pax`` with a
        # plain ``0`` typeflag.
        if info.issparse():
            member.is_sparse = True
        emit_member_name_normalized(
            self._diagnostics_collector,
            member=member,
            presented_name=presented,
            archive_name=self._archive_name,
            member_id=index,
        )
        if mtime_invalid:
            self._diagnostics_collector.emit(
                code=DiagnosticCode.MEMBER_TIMESTAMP_INVALID,
                message=f"Invalid TAR mtime for {quoted(info.name)}: {info.mtime!r}",
                context=MemberTimestampContext(
                    archive_name=self._archive_name,
                    member_name=member.name,
                    member_id=index,
                    field="mtime",
                    source="tar",
                    value_repr=repr(info.mtime),
                ),
                member=member,
                attach_to_member=True,
                logger=backends_logger,
            )
        return member

    def _open_member(self, member: ArchiveMember) -> ArchiveStream:
        info = member._raw
        assert isinstance(info, tarfile.TarInfo), (
            "TAR member is missing its TarInfo handle"
        )
        # Boundary outside the guard: translation/stamping never run while the
        # shared-fileobj lock is held.
        with self._translated_errors(member.name):
            sparse_error = _sparse_map_error(info)
            if sparse_error is not None:
                raise sparse_error
            with self._handle_guard():
                raw = self._tar.extractfile(info)
        if raw is None:
            # Only FILE members reach here (the base follows links/skips non-data members),
            # so a None stream means a zero-length or special entry; present an empty stream.
            raw = BytesIO(b"")
        stream: BinaryIO = ensure_binaryio(raw)
        if self._handle_lock is not None:
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
        with self._handle_guard():
            try:
                self._tar.close()
            finally:
                # tarfile never closes an external fileobj, so close the decompression
                # stream we built, even when close() raised: teardown runs once. The
                # source closes with the reader, after this.
                self._release_owned_stream()


def _recover_raw_name(
    info: tarfile.TarInfo,
    encoding: str,
    errors: str,
    global_headers: Mapping[str, str],
) -> bytes | None:
    """Recover the stored name bytes from tarfile's decoded ``info.name``.

    The codec depends on where the name came from. A ustar or GNU long-name field is
    decoded with the archive ``encoding`` and ``errors`` (surrogateescape by default), so
    encoding back with the same pair round-trips. A PAX ``path`` record is decoded
    strictly as UTF-8 (strictly with ``encoding`` when its own header block says
    ``hdrcharset=BINARY``), and only when that fails with ``encoding`` + ``errors``.

    Where the name came from is inferred, not recorded: ``info.pax_headers`` has the
    archive's global headers merged in, and tarfile keeps no per-block record. The name
    is taken as a PAX name when it equals ``pax_headers["path"]`` (a GNU long name that
    overrode an inherited global ``path`` differs from it), and ``BINARY`` is honoured
    only when it is not the inherited global value (tarfile reads ``hdrcharset`` from
    the member's own block only). A block that repeats the global ``BINARY``, or a long
    name equal to a global ``path``, is misread; both need a crafted archive and give
    different bytes only when ``encoding`` is not UTF-8.

    For a PAX name the bytes are taken as UTF-8, the spec's encoding. A name that UTF-8
    cannot encode holds surrogates, which only the fallback decode produces, so it is
    encoded back with the fallback pair. What stays ambiguous is a fallback decode under
    a codec that yields no surrogates (``latin-1``, say) of bytes that are not UTF-8: the
    string does not say which arm produced it, and the spec's reading wins.

    Returns ``None`` when no codec reproduces the name, which ``ArchiveMember.raw_name``
    documents as "could not be recovered". One unencodable name must not sink the
    listing.
    """
    try:
        from_pax = info.pax_headers.get("path") == info.name
        charset = info.pax_headers.get("hdrcharset")
        binary = charset == "BINARY" and global_headers.get("hdrcharset") != charset
        if from_pax and not binary:
            try:
                return info.name.encode("utf-8")
            except UnicodeEncodeError:
                pass
        return info.name.encode(encoding, errors)
    except UnicodeEncodeError:
        return None


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
    USES_ENCODING = True  # passed to tarfile.open(encoding=...) for name decoding

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
