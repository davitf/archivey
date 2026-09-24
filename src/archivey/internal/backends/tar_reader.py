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

Note: after ``getmembers()`` / a walk, ``tarfile`` has typically already consumed the
*first* trailer zero-block; the EOF probe therefore inspects the *next* 512 bytes.
"""

from __future__ import annotations

import stat
import tarfile
import threading
from datetime import datetime, timezone
from io import BytesIO
from typing import BinaryIO, Iterator, Literal, Mapping, cast

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
    MemberTimestampContext,
)
from archivey.escaping import quoted
from archivey.exceptions import (
    ArchiveyError,
    CorruptionError,
    ReadError,
    TruncatedError,
)
from archivey.internal.base_reader import (
    BaseArchiveReader,
    ReadBackend,
    reject_start_offset,
)
from archivey.internal.config import stream_config_from_archivey
from archivey.internal.diagnostics_collector import DiagnosticCollector
from archivey.internal.logs import backends as backends_logger
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
from archivey.internal.streams.streamtools import (
    DEFAULT_UNKNOWN_LENGTH_READ_STEP,
    LockedStream,
    ReadOnlyIOStream,
    ensure_binaryio,
    ensure_bufferedio,
    read_within_reach,
)
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
    """Parse a PAX ``atime``/``ctime`` (float Unix seconds) into a tz-aware UTC datetime.

    ``tarfile`` folds the PAX ``mtime`` into ``TarInfo.mtime`` itself, but leaves the
    access/creation times only in ``pax_headers``; surface them here for completeness.
    """
    raw = info.pax_headers.get(key)
    if raw is None:
        return None
    try:
        return datetime.fromtimestamp(float(raw), tz=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


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
        self._inner.seek(offset, whence)
        self._pos = self._inner.tell()
        return self._pos

    def tell(self, /) -> int:
        return self._pos

    def seekable(self) -> bool:
        return self._inner.seekable()

    def close(self) -> None:
        # No-op: the reader owns the wrapped stream's lifetime (``_owned_stream``); a
        # stray tarfile call must not tear the shared handle down early.
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
        # The decompression stream of a compressed tar, which this reader builds and so
        # must close. tarfile is always handed ``fileobj=``, so it never owns what it
        # reads; the source itself closes with the reader.
        self._owned_stream: BinaryIO | None = None
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
            with self._handle_guard():
                self._tar = self._open_tarfile(
                    source, format, streaming, member_streams=member_streams
                )
        except tarfile.TarError as exc:
            # Only tarfile's own (format) errors are translated; a genuine OSError from the
            # underlying handle propagates unchanged (see error-handling: "Genuine runtime
            # and I/O errors are not reclassified").
            # Release before re-raising: the exception traceback keeps this frame alive and
            # would otherwise pin the owned fp until the caller drops the exception
            # (inventory/fuzz catch-and-continue loops).
            self._release_owned_stream()
            raise self._translate_open_error(exc) from exc
        except BaseException:
            self._release_owned_stream()
            raise

    def _release_owned_stream(self) -> None:
        """Close a stream this reader opened, if any. Safe to call more than once."""
        if self._owned_stream is not None:
            self._owned_stream.close()
            self._owned_stream = None

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
                config=stream_config_from_archivey(
                    self._config,
                    streaming=streaming,
                    seekable=MemberStreams.SEEKABLE in member_streams,
                ),
                stamp=lambda exc: self._stamp_error_context(exc),
                collector=self._diagnostics_collector,
            )
            # tarfile can mis-handle a short read() (fewer bytes than requested) from a
            # decompressor; a BufferedReader in front guarantees full-sized reads.
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
            errorlevel=1,  # raise on fatal read errors (truncation/corruption surface below)
            encoding=self._encoding,  # None → tarfile applies its utf-8 default
        )

    def _translate_open_error(self, exc: Exception) -> ArchiveyError:
        translated = self._translate_exception(exc)
        if translated is not None:
            self._stamp_error_context(translated)
            return translated
        err = CorruptionError(f"Could not open TAR archive: {exc!r}")
        self._stamp_error_context(err)
        return err

    def _translate_exception(self, exc: Exception) -> ArchiveyError | None:
        if isinstance(exc, tarfile.ReadError):
            text = str(exc).lower()
            if "end of data" in text or "truncat" in text or "empty file" in text:
                return TruncatedError(f"TAR archive is truncated: {exc!r}")
            return CorruptionError(f"Error reading TAR archive: {exc!r}")
        if isinstance(exc, EOFError):
            return TruncatedError(f"TAR archive is truncated: {exc!r}")
        return None

    def _iter_members(self) -> Iterator[ArchiveMember]:
        if self._streaming:
            yield from self._iter_members_progressive()
            return
        # The error boundary sits OUTSIDE the handle guard, so translation/stamping
        # never run under the shared-fileobj lock. An exception the translator does
        # not recognize (a genuine OSError from the source) propagates unchanged.
        with self._translated_errors():
            # Pinned-library audit: TarFile.getmembers() drives seek/tell/read through
            # _load()/next() on the shared fileobj — must run under the handle lock.
            with self._handle_guard():
                members = self._tar.getmembers()  # forces the full header scan
                # Snapshot the EOF probe now, while the handle sits just past the scan and
                # before any member extraction can move it.
                self._capture_eof_probe(members)
        for index, info in enumerate(members):
            yield self._to_member(info, index)
        self._verify_tar_eof()

    def _iter_members_progressive(self) -> Iterator[ArchiveMember]:
        """Forward-only member walk — never calls ``getmembers()``.

        Yields bare members; the base's shared progressive pass stamps ids and resolves
        backward links. Shared-handle ops run under ``_handle_lock`` when present.
        """
        with self._translated_errors():
            if self._handle_lock is not None:
                # Hold the lock only around each next() so a yielded consumer can open
                # the current member without deadlock (streaming is single-owner).
                tar_iter = iter(self._tar)
                index = 0
                while True:
                    with self._handle_lock:
                        try:
                            info = next(tar_iter)
                        except StopIteration:
                            break
                    yield self._to_member(info, index)
                    index += 1
            else:
                for index, info in enumerate(self._tar):
                    yield self._to_member(info, index)
        self._verify_tar_eof()

    def _iter_with_data(self) -> Iterator[tuple[ArchiveMember, ArchiveStream | None]]:
        if not self._streaming:
            yield from super()._iter_with_data()
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

    def _capture_eof_probe(self, members: list[tarfile.TarInfo]) -> None:
        """Snapshot whether tarfile stopped the header scan on a *rejected* (non-null)
        header block, using the random-access EOF probe.

        After ``getmembers()`` / ``_load()``, ``TarFile.next()`` has always attempted one
        more header read before returning ``None``, so the probe's ``last_read`` *is* the
        block tarfile stopped on — independent of the live handle position (later member
        extraction may seek away) and independent of ``offset_data + roundup(size)``
        (wrong for GNU sparse, where logical size ≫ packed size). A full non-null block
        there is a rejected header, including when it is the archive's final block (which
        the trailing-block check in :meth:`_verify_tar_eof` reads past and cannot see).
        """
        self._eof_header_rejected = False
        probe = self._eof_probe_stream
        if probe is None or not members:
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
        error. It is not trailing tar data either, so it is not reported as that.
        """
        fileobj = self._tar.fileobj
        if fileobj is None:
            return
        offset = 0
        while offset < _MAX_TRAILING_SCAN:
            want = min(_TRAILING_SCAN_CHUNK, _MAX_TRAILING_SCAN - offset)
            try:
                with self._translated_errors(), self._handle_guard():
                    chunk = fileobj.read(want)
            except ReadError:
                return
            if not chunk:
                return
            stripped = chunk.lstrip(b"\x00")
            if stripped:
                self._emit_trailing_data(
                    observed_bytes=offset + (len(chunk) - len(stripped))
                )
                return
            offset += len(chunk)

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

        link_target = (
            info.linkname
            if member_type in (MemberType.SYMLINK, MemberType.HARDLINK)
            else None
        )

        # tarfile folds a PAX mtime (sub-second/timezone) into TarInfo.mtime already, so this
        # one field honors both the standard ustar mtime and the PAX override. A hostile
        # out-of-range value (e.g. a crafted PAX mtime beyond datetime's range) must not
        # sink the whole listing, so it degrades to None like _pax_time does.
        mtime_invalid = False
        try:
            modified = datetime.fromtimestamp(info.mtime, tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            mtime_invalid = True
            modified = None

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
            mode=stat.S_IMODE(info.mode),
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
        created = _pax_time(info, "ctime")
        if created is not None:
            member.created = created
        if info.uname:
            member.uname = info.uname
        if info.gname:
            member.gname = info.gname
        if link_target is not None:
            member.link_target = link_target
        if info.type == tarfile.GNUTYPE_SPARSE:
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
