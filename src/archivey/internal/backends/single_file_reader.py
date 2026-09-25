"""Single-file compressor backend — one multi-format reader for every standalone codec.

A bare ``.gz`` / ``.bz2`` / ``.xz`` / ``.zst`` / ``.lz4`` / ``.lz`` (lzip) /
``.lzma`` (LZMA Alone) / ``.zz`` (zlib) / ``.br`` (brotli) / ``.Z`` (unix-compress)
stream is presented as a **one-member pseudo-archive**: exactly one ``FILE`` member
whose name is inferred by stripping the codec extension (or ``.uncompressed`` when
there is no known suffix). There is no member table — size/mtime/crc come from
per-codec :class:`~archivey.internal.streams.codecs.MetadataContext` hooks when cheap
(gzip FNAME/mtime, xz/lzip size, …); otherwise they are filled/verified on read via
the shared codec layer.

The backend is codec-agnostic; adding a standalone codec is "add codec + enum +
detection" — no new backend class (see ``format-single-file-compressors``). Basic
ZST/LZ4 read is already here; remaining seekable-index / accelerator work for those
codecs is tracked under Phase 8 in ``PLAN.md``.
"""

from __future__ import annotations

import io
from collections.abc import Callable
from dataclasses import replace
from typing import BinaryIO, Iterator, TypeVar

from archivey.config import ArchiveyConfig
from archivey.cost import (
    AccessCost,
    CostReceipt,
    ListingCost,
    StreamCapability,
)
from archivey.exceptions import ArchiveyError, StreamNotSeekableError
from archivey.internal.base_reader import (
    BaseArchiveReader,
    ReadBackend,
    reject_start_offset,
)
from archivey.internal.config import AcceleratorMode, stream_config_from_archivey
from archivey.internal.diagnostics_collector import DiagnosticCollector
from archivey.internal.naming import infer_member_name_from_archive
from archivey.internal.open_site import OpenSite
from archivey.internal.password import _PasswordCandidates
from archivey.internal.registry import register_reader
from archivey.internal.source import ArchiveSource
from archivey.internal.streams.archive_stream import ArchiveStream
from archivey.internal.streams.codecs import (
    SINGLE_FILE_CODECS,
    Codec,
    MetadataContext,
    open_codec_stream,
    resolve_codec,
    stream_codec_for_format,
)
from archivey.internal.streams.decompressor_stream import DecompressorStream
from archivey.internal.streams.lzip import peek_index_summary
from archivey.internal.streams.streamtools import (
    SharedSource,
    SlicingStream,
    read_exact,
)
from archivey.types import (
    ArchiveFormat,
    ArchiveInfo,
    ArchiveMember,
    MemberStreams,
    MemberType,
)

_T = TypeVar("_T")

# Lowercased standalone-compression extensions, for the "strip vs append .uncompressed" rule
# in member-name inference. Sourced from the codec objects. (The combined `tar.gz`/`.tgz`
# names are a format-detection concern, not single-file naming.)
_COMPRESSION_EXTS: frozenset[str] = frozenset(
    ext.lower() for c in SINGLE_FILE_CODECS for ext in c.extensions
)


def _infer_member_name(archive_name: str | None) -> str:
    """Infer the single member's name from the source filename (see the spec)."""
    return infer_member_name_from_archive(
        archive_name, strip_suffixes=_COMPRESSION_EXTS
    )


class SingleFileReader(BaseArchiveReader):
    """Presents one standalone compressed stream as a one-member archive.

    Source shapes at open:

    - Path → reopen per ``open()`` (independent FDs; concurrent opens stay isolated)
    - Seekable stream → :class:`SharedSource` views from position 0
    - Non-seekable → one pending stream; first open consumes it

    Seekable sources may probe-open at init so format/size errors surface at
    ``open_archive`` time rather than first ``read``.
    """

    _SUPPORTS_RANDOM_ACCESS = True
    _MEMBER_LIST_UPFRONT = True

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
        self._stream_codec = stream_codec_for_format(format.stream)
        self._codec = self._stream_codec.codec
        self._seekable = source.seekable()

        # A non-seekable source cannot be randomly accessed, so engaging a random-access
        # accelerator (rapidgzip) is pointless — and would in fact fail at *open*: rapidgzip
        # needs either a seekable stream or a real OS fileno, and a non-seekable source
        # offers neither (so it raises StreamNotSeekableError).
        # Keep the codec sequential for such a source regardless of the archive's streaming flag.
        # Declared seek demand (MemberStreams.SEEKABLE) also gates accelerator AUTO resolution.
        seek_declared = MemberStreams.SEEKABLE in member_streams
        self._codec_config = stream_config_from_archivey(
            self._config,
            streaming=self._streaming or not self._seekable,
            seekable=seek_declared and self._seekable,
        )

        # Metadata probes answer a different question than member streams, so they get
        # their own config. `seekable_members` declares what the caller wants to do with
        # the *member stream*; the xz index and lzip trailer are bounded backward peeks
        # that hand nobody a stream, so reading them is decided by the source's shape.
        # Gating them on the declaration made the same .xz report size=None on a plain
        # open and 44 with the flag — a capability flag changing metadata. Accelerators
        # stay OFF: a probe reads no member data, so an accelerator's startup cost buys
        # nothing the native index does not already have.
        self._metadata_config = replace(
            stream_config_from_archivey(
                self._config, streaming=False, seekable=self._seekable
            ),
            use_rapidgzip=AcceleratorMode.OFF,
            use_indexed_bzip2=AcceleratorMode.OFF,
        )

        # The compressed-source header, read at most once and cached (only the gzip metadata
        # hook needs it; codecs without header metadata never trigger a read). See _peek_header.
        self._header_cache: bytes | None = None
        self._member = self._build_member(archive_name)

        # Concurrent/re-entrant member open (no ``_first_stream`` scratch):
        # - Path source: each open hands the path to the codec (independent FD; keeps
        #   path-only accelerator features such as the rapidgzip ISIZE truncation
        #   backstop). Concurrent opens are naturally isolated — same shape as ZIP path.
        # - Seekable stream: SharedSource.view(0) + a fresh codec per open, so interleaved
        #   opens never clobber the single shared handle position.
        # - Non-seekable: one forward pass; a second open fails loudly once consumed.
        self._shared: SharedSource | None = None
        self._pending_stream: ArchiveStream | None = None
        if self._seekable and (source.path is None or self._measure):
            # A stream source always shares its one handle, handing out views. A path
            # source normally hands the path to the codec (independent FDs) and shares
            # only under measurement, so the source's seeks are visible.
            self._shared = SharedSource(source, wrap_handle=self._seek_handle_wrapper())
        if not self._seekable:
            # Non-seekable: validation waits for the first read (see _validate_at_open).
            self._pending_stream = self._open_codec_stream()

    def _validate_at_open(self) -> None:
        """Decode one byte, so a source that is not the claimed codec fails at open.

        Constructing a codec stream checks nothing, because every codec checks its header
        on the first read. One byte is the whole guarantee; a corrupt tail still fails on
        read. An empty result is a valid empty stream: each decoder raises on input too
        short to hold its own header, and the accelerated bzip2 path hands an empty result
        to the stdlib engine to confirm it. The probe stream is not cached; every
        ``_open_member`` builds a fresh one. The error names no member, since nobody asked
        for one yet.

        A non-seekable source is not probed: the read would consume a byte of the one
        pass the first ``open_member`` hands out, so it still fails on that read.
        """
        if not self._seekable:
            return
        with self._open_codec_stream(attribute_member=False) as probe:
            probe.read(1)

    def _build_member(self, archive_name: str | None) -> ArchiveMember:
        member = ArchiveMember(
            type=MemberType.FILE,
            name=_infer_member_name(archive_name),
            raw_name=None,
            size=None,  # filled per-codec below where cheaply known
            compressed_size=self._probe_compressed_size(),
            modified=None,
        )
        # Per-codec metadata extraction lives on the codec object (gzip FNAME/mtime, xz/lzip
        # decompressed size); the reader stays codec-agnostic and just supplies the
        # source-reading hooks the extractor may need (the base method is a no-op).
        self._stream_codec.extract_metadata(self._metadata_context(), member)
        return member

    # --- metadata helpers ----------------------------------------------------------------

    def _metadata_context(self) -> MetadataContext:
        return MetadataContext(
            peek_header=self._peek_header,
            probe_decompressed_size=self._probe_decompressed_size,
            probe_lzip_index=self._probe_lzip_index,
        )

    def _peek_header(self, length: int) -> bytes:
        """The first ``length`` bytes of the compressed source, read once and cached.

        The first call (or one needing more than is cached) reads the source a single time;
        later calls serve from the cache without re-opening or re-seeking. For a non-seekable
        source this reuses the prefix detection already buffered in the source's replay
        prefix; for a path it opens a fresh handle; for a seekable stream it reads and rewinds
        once.
        """
        if self._header_cache is None or len(self._header_cache) < length:
            self._header_cache = self._read_source_prefix(length)
        return self._header_cache[:length]

    def _with_seekable_source(self, fn: Callable[[BinaryIO], _T | None]) -> _T | None:
        """Run ``fn`` on a seekable handle over the source; restore stream position.

        Path sources get a fresh FD. Seekable streams are passed through with the caller's
        position restored afterward. Non-seekable sources return ``None`` without calling
        ``fn`` (never forces a decode pass).
        """
        src = self._source
        assert src is not None
        try:
            if src.path is not None:
                with open(src.path, "rb") as f:
                    return fn(f)
            if src.seekable():
                pos = src.tell()
                try:
                    return fn(src)
                finally:
                    src.seek(pos)
        except OSError:
            return None
        return None

    def _probe_compressed_size(self) -> int | None:
        """Byte length of the compressed source, when one ``SEEK_END`` can answer it.

        Any seekable source answers this, which is the same rule the trailer/CRC probes
        beside it already follow. Gating it on ``isinstance(source, Path)`` made the same
        archive report an ``int`` from disk and ``None`` from an identical ``BytesIO``.
        A missing path raises ``OSError`` inside ``_with_seekable_source`` and comes back
        as ``None``, as before.

        The absolute ``SEEK_END`` is the member's length, not the handle's, because the
        source reaching a reader is already normalized to begin at offset 0 — a caller
        stream handed in at its current ``tell()`` arrives here sliced. Worth stating:
        read locally, ``seek(0, SEEK_END)`` on a "passed through" caller stream looks
        like it would over-report by the start offset.
        """
        return self._with_seekable_source(lambda f: f.seek(0, io.SEEK_END))

    def _read_source_prefix(self, length: int) -> bytes:
        src = self._source
        assert src is not None  # always set in __init__
        if src.path is not None:
            with open(src.path, "rb") as f:
                return f.read(length)
        if not src.seekable():
            return src.peek(length)
        # open_archive normalizes the origin (a mid-positioned stream is rebased so
        # tell() == 0 at the archive's first byte), so 0 is the archive start.
        pos = src.tell()
        src.seek(0)
        data = read_exact(src, length)
        src.seek(pos)
        return data

    def _probe_lzip_index(self) -> tuple[int, int] | None:
        """Decompressed size + combined CRC-32 from one seekable lzip index scan.

        The source only has to *be* seekable, not be a path and not have the caller's
        ``seekable_members`` declaration: ``_with_seekable_source`` gives the probe a
        handle either way and returns ``None`` for a non-seekable source, which is the
        only gate this needs. Returns ``None`` when the index is unavailable or corrupt.
        """
        if self._codec is not Codec.LZIP:
            return None

        def probe(f: BinaryIO) -> tuple[int, int] | None:
            size = f.seek(0, io.SEEK_END)
            if size < 26:  # header(6) + trailer(20)
                return None
            try:
                return peek_index_summary(f, size)
            except ArchiveyError:
                return None

        return self._with_seekable_source(probe)

    def _probe_decompressed_size(self) -> int | None:
        """Decompressed size from the stream index/trailer, when cheaply available.

        Needs a seekable source, not a path and not the caller's ``seekable_members``
        declaration: ``_with_seekable_source`` opens a fresh handle for a path and
        restores a caller stream's position afterwards, and ``_metadata_config`` asks the
        codec for its index because the *source* can seek. The codec is opened over a
        non-owning :class:`SlicingStream` view, so closing the probe's decompressor never
        closes a stream the caller owns.
        """

        # Deliberately no collector here: a degraded index reports into
        # resolve_collector's throwaway (a WARNING line and nothing else). This asks a
        # metadata question and answers size=None when the index is unreadable. The
        # member stream reports the same index into the reader's collector when a
        # caller seeks; reporting here too would count one file twice, and under
        # strict() would refuse the open for a caller who never seeks. The cost is a
        # second WARNING line for that file.
        def probe(f: BinaryIO) -> int | None:
            try:
                backend = resolve_codec(self._codec, self._metadata_config)
                stream = backend.open(SlicingStream(f, start=0))
            except (ArchiveyError, OSError, ValueError):
                return None
            try:
                if isinstance(stream, DecompressorStream):
                    return stream.try_get_size()
                return None
            finally:
                stream.close()

        return self._with_seekable_source(probe)

    # --- reader hooks --------------------------------------------------------------------

    def _iter_members(self) -> Iterator[ArchiveMember]:
        yield self._member

    def _open_codec_stream(self, *, attribute_member: bool = True) -> ArchiveStream:
        """Open a fresh decompression stream over the source.

        ``attribute_member=False`` leaves ``member_name`` off errors the stream raises, for
        the open-time probe that no caller asked for.

        A seekable stream source goes through a whole-source ``SharedSource`` view so
        concurrent / re-entrant opens never clobber the shared handle's position. A path
        source is passed through as a path (the codec opens an independent handle — the
        same concurrent-open shape as ZIP path-source). A non-seekable source is read
        once, forward-only.
        """
        member_name = self._member.name if attribute_member else None

        def stamp(exc: ArchiveyError) -> None:
            self._stamp_error_context(exc, member_name)

        if self._shared is not None:
            # Whole-source view + fresh codec per open (no per-member byte range for a
            # single-file archive). The view is non-owning; the SharedSource outlives it.
            view = self._shared.view(0)
            counted = self._wrap_compressed_input(view)
            raw = open_codec_stream(
                self._codec,
                counted,
                config=self._codec_config,
                stamp=stamp,
                collector=self._diagnostics_collector,
            )
        else:
            src = self._source
            assert src is not None  # always set in __init__
            codec_source: str | BinaryIO
            if src.path is not None:
                codec_source = str(src.path)
            else:
                # Count compressed bytes pulled from a non-seekable stream so the live
                # ratio guard has a denominator (a path / seekable stream keeps its cheap
                # static size).
                codec_source = self._wrap_compressed_input(src)
            raw = open_codec_stream(
                self._codec,
                codec_source,
                config=self._codec_config,
                stamp=stamp,
                collector=self._diagnostics_collector,
            )
        # Wrap so the handle carries the reader's diagnostic collector/operation id.
        # (open_codec_stream already returns an ArchiveStream; nesting is fine.)
        return self._wrap_member_stream(raw, member_name, size=self._member.size)

    def _open_member(self, member: ArchiveMember) -> ArchiveStream:
        if self._seekable:
            # Reentrant: every open builds a fresh codec (path → independent FD; stream →
            # SharedSource view). No per-open scratch on self.
            return self._open_codec_stream()

        # Non-seekable: serve the one-shot stream opened at init; a second open fails loudly.
        if self._pending_stream is not None:
            stream = self._pending_stream
            self._pending_stream = None
            return stream
        err = StreamNotSeekableError(
            "Cannot open this member again: the source is non-seekable and its "
            "single decompression pass has already been consumed. Buffer the "
            "source to disk or a BytesIO to re-read it.",
        )
        self._stamp_error_context(err, member.name)
        raise err

    def _get_archive_info(self) -> ArchiveInfo:
        cost = CostReceipt(
            listing_cost=ListingCost.INDEXED,  # exactly one member, always
            access_cost=AccessCost.DIRECT,  # one member -> no solid-block dependency
            stream_capability=(
                StreamCapability.SEEKABLE
                if self._seekable
                else StreamCapability.FORWARD_ONLY
            ),
            solid_block_count=None,
        )
        return ArchiveInfo(
            format=self._format,
            format_version=None,
            is_solid=False,
            member_count=1,
            comment=None,
            is_encrypted=False,
            is_multivolume=False,
            cost=cost,
        )

    def _close_archive(self) -> None:
        # Close an unserved non-seekable pending stream. The SharedSource is deliberately
        # NOT closed: it owns nothing (only stream sources are wrapped, and the caller
        # owns those), and marking it closed would poison still-open member streams —
        # which are the caller's to close and, over the rapidgzip accelerator, ABORT the
        # process if their source dies underneath them (rapidgzip 0.16 raises C++
        # std::invalid_argument through terminate() when a Python-file callback raises —
        # on read, close, and the GC-time guard alike; see dev-docs/known-issues.md). Reads
        # after the *caller* closes their source surface as a typed error via the
        # ArchiveStream closed-handle mapping (stdlib codec paths).
        if self._pending_stream is not None:
            self._pending_stream.close()
            self._pending_stream = None


class SingleFileBackend(ReadBackend):
    """One backend serving every standalone single-file compressor.

    Only the format list is declared here, derived from the codec objects. The detection
    tables (magic, extensions, content probes) are not duplicated onto this backend — the
    detector reads them straight from ``STREAM_CODECS`` via the registry — so adding a
    standalone codec is a single ``StreamCodec`` subclass (see ``compressed-streams`` /
    ``format-detection``).
    """

    FORMATS: tuple[ArchiveFormat, ...] = tuple(
        c.single_file_format
        for c in SINGLE_FILE_CODECS
        if c.single_file_format is not None
    )
    # A compressed stream decodes front-to-back, so streaming=True works on a
    # non-seekable source. Random access always needs a seekable source.
    SUPPORTS_STREAMING_NON_SEEKABLE = True

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
    ) -> SingleFileReader:
        reject_start_offset(start_offset, format, archive_name)
        # `format` is the resolved single-file format (from detection or the caller); its
        # stream codec is exactly what to decompress with — no re-inspection needed.
        return SingleFileReader(
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


register_reader(SingleFileBackend)
