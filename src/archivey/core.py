"""Public entry points: open archives and query format support.

``open_archive`` pipeline (in order): register backends → refuse a wrong-typed
``format=`` → validate streaming/concurrency → resolve source → incomplete
numbered-volume refuse (``.7z.NNN`` / ``.zip.NNN`` / ``.exe.NNN``) then Info-ZIP
``.zNN`` (both skipped when ``format=`` is an explicit non-joinable format) →
detect or accept format (a stub-only ``.exe`` / ``.sfx`` with no archive magic
follows the split first volume beside it) → other multi-volume checks → backend
capability gates (password / seekability) → normalize stream origin →
``backend.open_read(...)``.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, Callable, Collection

from archivey.config import (
    DEFAULT_ARCHIVEY_CONFIG,
    AcceleratorMode,
    ArchiveyConfig,
    ExtractionLimits,
    ListingLimits,
    PasswordInput,
)
from archivey.detection import DetectionConfidence, FormatInfo
from archivey.diagnostics import (
    DiagnosticCode,
    ExtractionReport,
    UnusedArgumentContext,
)
from archivey.exceptions import (
    ArchiveyUsageError,
    FormatDetectionError,
    StreamNotSeekableError,
    UnsupportedFeatureError,
    UnsupportedFormatError,
)
from archivey.internal.arg_checks import (
    check_callable,
    check_config,
    check_encoding,
    check_extraction_limits,
)
from archivey.internal.backends.iso_reader import refuse_raw_sector_image
from archivey.internal.backends.zip_detect import (
    ZIP_MULTI_VOLUME_MSG,
    is_zip_split_segment_name,
)
from archivey.internal.config import stream_config_from_archivey
from archivey.internal.detection import detect_format
from archivey.internal.diagnostics_collector import collector_from_config
from archivey.internal.enum_args import coerce_enum, coerce_enum_collection
from archivey.internal.format_args import (
    coerce_archive_format,
    coerce_stream_or_archive_format,
)
from archivey.internal.format_provenance import FormatProvenance
from archivey.internal.open_site import OpenSite, capture_open_site
from archivey.internal.password import _PasswordCandidates
from archivey.internal.registry import (
    format_availability,
    get_registry,
    list_known_formats,
    list_supported_formats,
)
from archivey.internal.source import ArchiveSource
from archivey.internal.streams.archive_stream import ArchiveStream
from archivey.internal.streams.codecs import codec_for_stream_format, open_codec_stream
from archivey.internal.streams.streamtools import (
    is_stream,
    raise_if_text_stream,
    raise_if_write_only_stream,
)
from archivey.internal.volumes import (
    OpenSourceInput,
    ResolvedSource,
    first_volume_for_stub,
    incomplete_lone_numbered_volume_error,
    is_sfx_stub_name,
    resolve_source,
)
from archivey.reader import ArchiveReader
from archivey.terminal import display_path
from archivey.types import (
    AbortOn,
    AbortOnStr,
    ArchiveFormat,
    ContainerFormat,
    ExtractionPolicy,
    ExtractionPolicyStr,
    ExtractionProgress,
    FormatAvailability,
    FormatSupport,
    MemberStreams,
    MissingComponent,
    OnError,
    OnErrorStr,
    OverwritePolicy,
    OverwritePolicyStr,
    StreamFormat,
)

if TYPE_CHECKING:
    from archivey.internal.diagnostics_collector import DiagnosticCollector

__all__ = [
    "DetectionConfidence",
    "FormatAvailability",
    "FormatInfo",
    "FormatSupport",
    "MissingComponent",
    "ArchiveyConfig",
    "ExtractionLimits",
    "ListingLimits",
    "AcceleratorMode",
    "DEFAULT_ARCHIVEY_CONFIG",
    "detect_format",
    "extract",
    "format_availability",
    "list_known_formats",
    "list_supported_formats",
    "open_archive",
    "open_stream",
]


def _format_provenance(
    source_path: Path | None,
    requested_format: ArchiveFormat | None,
    detected: FormatInfo | None,
    *,
    is_directory: bool = False,
) -> FormatProvenance:
    """Record where the resolved format came from, for the empty-listing check.

    ``detected is None`` means detection never ran: either the caller asserted a format,
    or the source is a directory path (which resolves to ``DIRECTORY`` before detection).
    A directory path is ``"directory"`` even with ``format=DIRECTORY`` passed: the
    filesystem decided it, and any other ``format=`` is refused before this point.
    The re-detection source is kept only for the asserted case, and only when the
    opened source is one file (``source_path``) — reopening a file cannot disturb the
    reader, while seeking a live stream back to its origin can. It is the source as
    resolution left it, not the caller's argument: a later RAR part, a followed stub
    and a joined set all open something other than the name the caller passed, and
    re-detecting that name would judge a different file.
    """
    if detected is not None:
        chosen_by = "extension" if detected.detected_by == "extension" else "content"
        # Provenance, not confidence: stamp when a probe was the sole evidence.
        probe_only = (
            detected.detected_by == "content_probe" and not detected.corroborated
        )
        return FormatProvenance(chosen_by=chosen_by, probe_only=probe_only)
    if requested_format is None or is_directory:
        return FormatProvenance(chosen_by="directory")
    return FormatProvenance(chosen_by="argument", source=source_path)


def _raise_multi_volume_not_supported(
    fmt: ArchiveFormat, archive_name: str | None
) -> None:
    raise UnsupportedFeatureError(
        f"Format {fmt!r} does not support multi-volume archives.",
        source_format=fmt,
        archive_name=archive_name,
    )


def _refuse_incomplete_numbered_volume(
    resolved: ResolvedSource,
    format: ArchiveFormat | None,
    archive_name: str | None,
) -> None:
    if resolved.volume_count != 1:
        return
    # Numbered parts are joined for ZIP and 7z. An explicit other format= (P8)
    # must be honoured or refused as a format conflict, not rewritten as a
    # missing-volume error.
    if format is not None and format not in (
        ArchiveFormat.ZIP,
        ArchiveFormat.SEVEN_Z,
    ):
        return
    error = incomplete_lone_numbered_volume_error(archive_name)
    if error is not None:
        raise error


def _refuse_lone_zip_split(
    resolved: ResolvedSource,
    format: ArchiveFormat | None,
    archive_name: str | None,
) -> None:
    if (
        resolved.volume_count == 1
        and is_zip_split_segment_name(archive_name)
        and (format is None or format == ArchiveFormat.ZIP)
    ):
        raise UnsupportedFeatureError(
            ZIP_MULTI_VOLUME_MSG,
            archive_name=archive_name,
            source_format=ArchiveFormat.ZIP,
        )


def _refuse_unjoined_volume_names(
    resolved: ResolvedSource,
    format: ArchiveFormat | None,
    archive_name: str | None,
) -> None:
    _refuse_incomplete_numbered_volume(resolved, format, archive_name)
    _refuse_lone_zip_split(resolved, format, archive_name)


def _refuse_if_stub_format_conflict(
    stub: Path, first_volume: Path, requested: ArchiveFormat
) -> None:
    try:
        info = detect_format(first_volume, follow_stub_volumes=False)
    except FormatDetectionError:
        return
    if info.format.container == requested.container:
        return
    raise ArchiveyUsageError(
        f"{display_path(stub)} has no archive magic; the split first volume "
        f"beside it is {info.format.display_name}, but format={requested!r} "
        f"was requested."
    )


def _follow_stub_volume(
    stub: Path, format: ArchiveFormat | None
) -> ResolvedSource | None:
    alt = first_volume_for_stub(stub)
    if alt is None:
        return None
    if format is not None:
        _refuse_if_stub_format_conflict(stub, alt, format)
    resolved = resolve_source(alt)
    _refuse_unjoined_volume_names(resolved, format, resolved.archive_name)
    return resolved


def open_archive(
    source: OpenSourceInput,
    *,
    format: ArchiveFormat | str | None = None,
    streaming: bool = False,
    seekable_members: bool = False,
    concurrent_members: bool = False,
    password: PasswordInput = None,
    encoding: str | None = None,
    config: ArchiveyConfig | None = None,
) -> ArchiveReader:
    """Open an archive for reading.

    ``streaming=False`` (the default) opens for random access and fails fast at open
    time on a non-seekable source. ``streaming=True`` promises forward-only, single-pass
    access (works on any source, but disables random-access methods).

    Member streams are forward-only and single-live by default. Two keyword flags opt
    into more, each unlocking one specific trap:

    - ``seekable_members=True`` — ``seek()`` on a member stream from random
      ``open()`` works. Without it, ``seek()`` raises
      ``io.UnsupportedOperation``. A backward seek may re-decompress from the
      start when there is no index or accelerator.
    - ``concurrent_members=True`` — multiple member streams may be open at once
      (coordinated first-touch materialization, then worker fan-out; draining close).
      Without it, a second overlapping ``open()`` raises ``ConcurrentAccessError``.

    ``open_stream`` uses the same vocabulary for the single-stream case
    (``open_stream(..., seekable=True)``); concurrency is meaningless there, so it has
    no counterpart. Declared concurrency does **not** gate solid open-order cost — see
    ``AccessCost`` / ``stream_members()``.

    ``streaming=True`` combined with ``concurrent_members=True`` is rejected
    (``ArchiveyUsageError``): a forward-only pass cannot fan out.

    ``config`` supplies library tuning knobs (accelerator modes, the diagnostic
    policy, default extraction limits, and listing resource limits via
    ``listing_limits``). ``None`` selects the module default
    :data:`~archivey.DEFAULT_ARCHIVEY_CONFIG`.

    The format is auto-detected from the source's magic bytes (then its extension) unless
    ``format=`` is passed explicitly. A directory path opens as a directory pseudo-archive.
    A non-seekable stream keeps the bytes detection peeked in a replay prefix that the
    backend's first reads drain, so detection never consumes bytes the backend needs.

    A seekable stream source is taken to hold the archive **starting at its current
    position**: detection peeks from there and restores the position, and the opener
    then rebases a mid-positioned stream to a zero origin so every backend sees the
    archive begin at ``tell() == 0`` (an archive embedded mid-file works uniformly,
    without manual slicing).

    ``source`` may be an ordered sequence of paths or binary streams that together form
    a multi-volume archive (7z concatenates volumes; RAR opens volume 1 and lets
    ``unrar`` resolve siblings). A length-1 sequence is treated as a single source.

    ``password`` accepts a single value, an ordered sequence of candidate passwords, or
    a provider callable. List the most likely password first — especially for 7z, where
    each wrong candidate pays an expensive key derivation.

    With multiple candidates (or a provider), formats whose per-open password check is
    weak may need a confirmation read before a candidate is accepted. For traditional
    ZipCrypto this is usually cheap: compressed members are confirmed from a bounded
    decompressed prefix. **STORED** ZipCrypto members are the niche exception — roughly
    1/256 of wrong passwords pass the one-byte open check, and with no decompressor to
    reject garbage the reader must scan the member once (CRC over every surviving
    candidate in parallel) to decide. That full pass is rare in practice (multiple
    passwords *and* a colliding wrong candidate *and* a STORED member) but can matter
    for very large stored members. With a single password there is no confirmation
    read, so a wrong ZipCrypto password that passes the one-byte check fails on the
    member's own read, as an ``EncryptionError`` saying the password may be wrong or
    the member corrupt.

    A password supplied for a *format* with no encryption at all (TAR, ISO, a
    directory, the single-file compressed streams) is **accepted, not refused** — it is
    a resource offered, not a claim about this archive. A concrete password (a string,
    bytes, or a list of them) is recorded as ``PASSWORD_ARGUMENT_UNUSED``; a provider
    callable is not, because it is only a way to ask for a password, and such a format
    never asks. That is what lets a batch job pass one keyring at every archive. The
    check is per format, made before any header is read: an unencrypted ZIP, 7z or RAR
    records nothing, because those formats can use a password. Diagnostics also log
    at ``WARNING`` by default, so a job passing a password list will log once per
    archive of a format without encryption; silence it with
    ``ArchiveyConfig(diagnostic_policy=DiagnosticPolicy(overrides={
    DiagnosticCode.PASSWORD_ARGUMENT_UNUSED: DiagnosticDisposition.IGNORE}))``, which
    keeps the count without the log line.
    """
    # Safety net for `from archivey.core import open_archive` (package __init__ also
    # imports backends so list_supported_formats works on a bare `import archivey`).
    import archivey.internal.backends  # noqa: F401

    open_site = capture_open_site()

    format = coerce_archive_format(format, call="open_archive(format=…)")
    check_config(config, call="open_archive(config=…)")
    check_encoding(encoding, call="open_archive(encoding=…)")

    if streaming and concurrent_members:
        raise ArchiveyUsageError(
            "open_archive(streaming=True) cannot be combined with "
            "concurrent_members=True: a forward-only pass has one progressive decoder "
            "and cannot fan out concurrent member streams."
        )

    # The public surface is two booleans; everything below the entry point keeps
    # working in MemberStreams flags. A concrete reader exposes the value it was
    # opened with as `reader.member_streams`; CostReceipt does not carry it.
    member_streams = MemberStreams(0)
    if seekable_members:
        member_streams |= MemberStreams.SEEKABLE
    if concurrent_members:
        member_streams |= MemberStreams.CONCURRENT

    passwords = _PasswordCandidates.from_input(password)

    effective_config = config if config is not None else DEFAULT_ARCHIVEY_CONFIG
    # Collector is created before detection so open + detect share one budget /
    # occurrence order; one-shot extract() then reads reader.diagnostics for the
    # whole call without cross-call plumbing.
    collector = collector_from_config(effective_config)
    resolved = resolve_source(source)
    slot = _SourceSlot(resolved.source)
    try:
        return _open_resolved(
            slot,
            resolved,
            format=format,
            streaming=streaming,
            passwords=passwords,
            encoding=encoding,
            config=effective_config,
            collector=collector,
            member_streams=member_streams,
            open_site=open_site,
        )
    except BaseException:
        # Once a reader exists it closes its source; until then nobody else will.
        slot.current.close()
        raise


class _SourceSlot:
    """The source an open is working on, closing each one it is replaced by."""

    def __init__(self, source: ArchiveSource) -> None:
        self.current = source

    def replace(self, source: ArchiveSource) -> ArchiveSource:
        old = self.current
        self.current = source
        old.close()
        return source


def _open_resolved(
    slot: _SourceSlot,
    resolved: ResolvedSource,
    *,
    format: ArchiveFormat | None,
    streaming: bool,
    passwords: _PasswordCandidates,
    encoding: str | None,
    config: ArchiveyConfig,
    collector: DiagnosticCollector,
    member_streams: MemberStreams,
    open_site: OpenSite | None,
) -> ArchiveReader:
    """Detect the format of a resolved source and hand it to its backend.

    ``slot`` holds the one source detection and the backend read. It changes when a
    self-extracting stub is followed to its volume or a RAR set is reopened from volume
    1; ``open_archive`` closes whatever it holds if this raises.
    """
    archive_source = slot.current
    archive_name = resolved.archive_name

    # Numbered 7-Zip parts (``.7z.NNN`` / ``.zip.NNN`` / ``.exe.NNN``) and Info-ZIP
    # ``.zNN``: middle/last parts often have no magic at offset 0, so detection
    # would raise FormatDetectionError or CorruptionError. Refuse by name first.
    # A joined set has ``volume_count > 1`` and is skipped. An explicit
    # ``format=TAR_GZ`` (etc.) is honoured or refused as a format conflict, never
    # rewritten as a volume error (P8).
    #
    # A lone numbered part names the missing siblings (``TruncatedError``). Info-ZIP
    # ``.zNN`` stays the ZIP rejoin-first ``UnsupportedFeatureError`` — those are
    # never concatenated.
    _refuse_unjoined_volume_names(resolved, format, archive_name)

    # --- Resolve format: a directory path is DIRECTORY (and a conflicting explicit
    # format= is rejected, not ignored); else caller format, else magic detect. ---
    resolved_format = format
    if archive_source.is_directory:
        # Silently overruling format= here would hand back a reader over the directory
        # tree for a caller who asserted something else -- the wrong data, succeeding.
        # Every other explicit-format conflict is refused loudly; so is this one.
        if format is not None and format != ArchiveFormat.DIRECTORY:
            assert archive_source.path is not None  # the directory form has a path
            raise ArchiveyUsageError(
                f"{archive_name or display_path(archive_source.path)} is a directory, but format="
                f"{format!r} was requested. Pass a path to an archive file, or "
                f"format=ArchiveFormat.DIRECTORY to read the directory tree."
            )
        resolved_format = ArchiveFormat.DIRECTORY

    detected: FormatInfo | None = None
    if resolved_format is None:
        # A non-seekable source keeps what detection peeks in its own replay prefix,
        # so the backend gets the same object and reads those bytes first.
        # Probe *this* file only. Public detect_format follows a stub-only exe to
        # the volume beside it; doing that here would report 7z/ZIP while still
        # handing the stub bytes to the backend.
        try:
            detected = detect_format(
                archive_source, collector=collector, follow_stub_volumes=False
            )
        except FormatDetectionError:
            stub = archive_source.path
            followed = _follow_stub_volume(stub, format) if stub is not None else None
            if followed is None:
                raise
            resolved = followed
            archive_source = slot.replace(resolved.source)
            archive_name = resolved.archive_name
            detected = detect_format(archive_source, collector=collector)
        resolved_format = detected.format
    elif archive_source.path is not None and is_sfx_stub_name(archive_source.path.name):
        # format= still follows a stub-only miss. Skipping this made
        # detect_format(p); open_archive(p, format=info.format) open the MZ
        # bytes as ZIP/7z while auto-detect joined the split set.
        stub = archive_source.path
        try:
            detect_format(stub, follow_stub_volumes=False)
        except FormatDetectionError:
            followed = _follow_stub_volume(stub, resolved_format)
            if followed is not None:
                resolved = followed
                archive_source = slot.replace(resolved.source)
                archive_name = resolved.archive_name

    # ZIP is here for 7-Zip's ``-v`` byte slices, which rejoin into an ordinary ZIP.
    # Info-ZIP's spanned sets never reach this point as a joined source (they are not
    # volume-shaped to ``discover_volume_siblings``); an explicitly passed sequence of
    # their parts is still caught by the reader's EOCD disk-field check.
    if resolved.volume_count > 1 and resolved_format.container not in (
        ContainerFormat.SEVEN_Z,
        ContainerFormat.RAR,
        ContainerFormat.ZIP,
    ):
        _raise_multi_volume_not_supported(resolved_format, archive_name)

    # RAR multi-volume: unrar needs real sibling files on disk. When resolve_source
    # concatenated an explicit path sequence, reopen volume 1 only.
    if resolved_format.container == ContainerFormat.RAR:
        volume_paths = archive_source.volume_paths
        if archive_source.joined is not None and volume_paths:
            archive_source = slot.replace(ArchiveSource.for_path(volume_paths[0]))

    # A raw CD sector image is claimed as ISO only so it can be refused by name. Ahead
    # of the availability check, so the answer does not depend on pycdlib; a
    # non-seekable source is left to the seekability refusal below.
    if resolved_format == ArchiveFormat.ISO and archive_source.seekable():
        refuse_raw_sector_image(archive_source, resolved_format, archive_name)

    registry = get_registry()
    backend_cls = registry.reader_for_format(resolved_format)

    # `password=` and `encoding=` are *resources offered for use if needed*, not
    # assertions about this archive, so a backend that cannot use one is a diagnostic
    # rather than a refusal (``archive-reading`` §"assertion vs resource"). `format=` is
    # the assertion, and it is still refused above for a directory path.
    if passwords.has_concrete_passwords() and not backend_cls.SUPPORTS_PASSWORD:
        # Every form opens alike: none is refused. Only a concrete value is recorded,
        # because a provider callable offers a password only if asked, and a format
        # with no encryption never asks. A caller (the CLI, a batch job) can then pass
        # one provider everywhere without a warning on every TAR or gzip.
        collector.emit(
            code=DiagnosticCode.PASSWORD_ARGUMENT_UNUSED,
            message=(
                f"password= was supplied for {resolved_format.display_name}, which "
                f"carries no encryption a password could unlock; it will not be used."
            ),
            context=UnusedArgumentContext(
                archive_name=archive_name,
                argument="password",
                format=resolved_format.display_name,
                reason="format carries no encryption",
            ),
        )

    if encoding is not None and not backend_cls.USES_ENCODING:
        # Only the caller's explicit encoding: an open that passed none asked for nothing.
        collector.emit(
            code=DiagnosticCode.ENCODING_ARGUMENT_UNUSED,
            message=(
                f"encoding={encoding!r} was supplied for "
                f"{resolved_format.display_name}, which decodes member names without "
                f"it; the value will not be applied."
            ),
            context=UnusedArgumentContext(
                archive_name=archive_name,
                argument="encoding",
                format=resolved_format.display_name,
                reason="backend decodes member names without a caller-supplied encoding",
            ),
        )

    # Access-mode contract: streaming=False never implicitly buffers a pipe.
    # streaming=True still needs a front-to-back format (TAR, raw codecs); trailing
    # indexes (ZIP CD, ISO) cannot.
    if not archive_source.is_directory and not archive_source.seekable():
        # Capability first, mode second: for a format that needs seek in *either* mode
        # the requested mode is not what went wrong, so both modes get the one message
        # naming the only fix. Proposing streaming=True here would send the caller into
        # a second refusal explaining the retry could never have worked.
        if not backend_cls.SUPPORTS_STREAMING_NON_SEEKABLE:
            raise StreamNotSeekableError(
                f"Format {resolved_format!r} cannot be read from a non-seekable source "
                f"in either access mode (its index/metadata is not at the front of "
                f"the stream). Buffer it to disk or a BytesIO and reopen.",
                source_format=resolved_format,
                archive_name=archive_name,
            )
        if not streaming:
            raise StreamNotSeekableError(
                f"Random access (streaming=False) requires a seekable source. Open with "
                f"streaming=True for a single forward pass over this "
                f"{resolved_format!r} stream, "
                f"or buffer it to disk or a BytesIO and reopen.",
                source_format=resolved_format,
                archive_name=archive_name,
            )

    # Mid-file seekable streams: rebase so every backend sees tell()==0 at the first
    # archive byte (done after detection, which peeked from the same origin).
    if archive_source.seekable():
        archive_source.rebase_to_current_position()

    # A self-extracting source has an executable stub before the archive; detection
    # reports where the payload starts and the backend opens there. Handed over as an
    # explicit argument rather than by slicing here, so a path source stays a path:
    # RAR needs one to hand `unrar`, and turning it into a stream would spill the whole
    # archive to a temp file for every compressed member (`_ensure_archive_path`). The
    # argument carries the same promise a slice would — see `ReadBackend.open_read`.
    payload_offset = detected.payload_offset if detected is not None else 0

    backend = backend_cls()
    reader = backend.open_read(
        archive_source,
        format=resolved_format,
        streaming=streaming,
        passwords=passwords,
        encoding=encoding,
        archive_name=archive_name,
        config=config,
        collector=collector,
        member_streams=member_streams,
        open_site=open_site,
        start_offset=payload_offset,
    )
    # An empty listing is only interesting when the bytes never confirmed the format.
    # That is known here and the listing is not, so carry it to the reader rather than
    # adding a parameter to every backend's open_read for a fact none of them reads.
    reader._format_provenance = _format_provenance(
        archive_source.path,
        format,
        detected,
        is_directory=archive_source.is_directory,
    )
    # After provenance, so an open-time decode failure carries format_unconfirmed.
    try:
        reader._validate_at_open()
    except BaseException:
        reader.close()
        raise
    return reader


def open_stream(
    source: str | Path | BinaryIO,
    *,
    format: StreamFormat | ArchiveFormat | str | None = None,
    seekable: bool = False,
    config: ArchiveyConfig | None = None,
) -> ArchiveStream:
    """Open a single-file compressed stream and return a decompressing stream.

    This is the compressed-streams entry point for a bare ``.gz`` / ``.bz2`` / ``.xz`` /
    … payload (no archive container). ``seekable`` is the same capability
    :func:`open_archive` spells ``seekable_members``; concurrency is not a concept here
    — the call returns exactly one stream — so there is no counterpart to
    ``concurrent_members``.

    ``seekable=False`` (the default) returns a forward-only stream: ``seekable()`` is
    ``False``, ``seek()`` raises ``io.UnsupportedOperation``, and no seek index or
    accelerator is instantiated. Pass ``seekable=True`` to opt into the
    seekable-decompressor-streams contract (native indexes, demand-driven accelerator
    ``AUTO``, loud slow rewinds on the non-accelerated path).

    ``format`` accepts a :class:`~archivey.StreamFormat`, a raw-stream
    :class:`~archivey.ArchiveFormat` (e.g. ``ArchiveFormat.GZ``), or ``None`` to
    auto-detect. A container format (ZIP, TAR, …) is rejected — use
    :func:`open_archive` for those.
    """
    import archivey.internal.backends  # noqa: F401

    # Before any I/O: a value of neither format type used to fall through to
    # auto-detection, which silently discards the caller's assertion.
    format = coerce_stream_or_archive_format(format, call="open_stream(format=…)")
    check_config(config, call="open_stream(config=…)")

    effective_config = config if config is not None else DEFAULT_ARCHIVEY_CONFIG
    collector = collector_from_config(effective_config)

    if isinstance(source, (str, Path)):
        path = Path(source)
        if path.is_dir():
            # Split out of the is_file() check: a directory exists, so "not found" sends
            # the caller looking for a missing file. open_archive() reads the same path
            # happily as a directory archive, which is the likely intent.
            raise ArchiveyUsageError(
                f"{display_path(path)} is a directory, not a compressed stream; "
                f"use open_archive() to read a directory tree"
            )
        if not path.exists():
            raise FileNotFoundError(f"Compressed stream not found: {path}")
        # A FIFO or device path is a non-seekable source with no path, read once
        # through the source, exactly as open_archive reads it.
        codec_input = ArchiveSource.for_path(path)
    else:
        if not is_stream(source):
            raise_if_text_stream(source)
            raise_if_write_only_stream(source)
            raise TypeError(
                f"open_stream source must be a path or binary stream, got {type(source)!r}"
            )
        # The same boundary open_archive uses: full-count, borrowed, bounded, and a
        # replay prefix for detection when the stream cannot be rewound. A codec — or a
        # seek-index accelerator reading the source itself — must not mistake a legal
        # short ``read(n)`` for a truncated stream.
        codec_input = ArchiveSource.for_stream(source)
        # The mid-stream origin contract: the payload starts where the caller left it.
        codec_input.rebase_to_current_position()
    # The returned stream owns the source from here, as a reader does: closing it closes
    # the source (which never closes the caller's object), and so does any refusal
    # before there is a stream to close.
    try:
        return _open_stream_from_source(
            codec_input, format, seekable, effective_config, collector
        )
    except BaseException:
        codec_input.close()
        raise


def _open_stream_from_source(
    codec_input: ArchiveSource,
    format: StreamFormat | ArchiveFormat | None,
    seekable: bool,
    effective_config: ArchiveyConfig,
    collector: DiagnosticCollector,
) -> ArchiveStream:
    source_is_seekable = codec_input.seekable()

    if seekable and not source_is_seekable:
        raise StreamNotSeekableError(
            "open_stream(seekable=True) requires a seekable source. Buffer the stream "
            "to disk or a BytesIO and reopen, or open with seekable=False for a "
            "forward-only pass."
        )

    stream_format = _resolve_stream_format(format, codec_input, collector)
    if stream_format is StreamFormat.UNCOMPRESSED:
        raise UnsupportedFormatError(
            "open_stream requires a compressed stream format "
            f"(got {stream_format!r}); use open_archive for uncompressed containers."
        )

    codec = codec_for_stream_format(stream_format)
    stream_config = stream_config_from_archivey(
        effective_config,
        streaming=False,
        seekable=seekable and source_is_seekable,
    )
    # A path goes to the codec as a path: it opens its own handles and can use
    # path-only accelerator features, and the source then never opens one.
    codec_source: str | BinaryIO = (
        str(codec_input.path) if codec_input.path is not None else codec_input
    )
    return open_codec_stream(
        codec,
        codec_source,
        config=stream_config,
        collector=collector,
        seekable=seekable and source_is_seekable,
        on_close=codec_input.close,
    )


def _resolve_stream_format(
    format: StreamFormat | ArchiveFormat | None,
    open_source: ArchiveSource,
    collector: DiagnosticCollector,
) -> StreamFormat:
    """Map open_stream's ``format=`` argument (or auto-detect) to a StreamFormat.

    Only ``None`` reaches the detection branch below: ``open_stream`` has already
    converted a string spelling and refused a value of neither format type
    (``coerce_stream_or_archive_format``), so falling through here means the caller
    asked for auto-detection.
    """
    if isinstance(format, StreamFormat):
        return format
    if isinstance(format, ArchiveFormat):
        if format.container is not ContainerFormat.RAW_STREAM:
            raise ArchiveyUsageError(
                f"open_stream does not accept container format {format!r}; "
                "pass a StreamFormat or a raw-stream ArchiveFormat "
                "(e.g. ArchiveFormat.GZ), or use open_archive."
            )
        return format.stream

    # The invariant the docstring states, enforced rather than described: without it a
    # future caller of this private helper would auto-detect a value it was handed,
    # which is the silent fall-through this function's boundary check exists to close.
    assert format is None, f"unvalidated format argument reached detection: {format!r}"

    detected = detect_format(open_source, collector=collector)
    if detected.format.container is not ContainerFormat.RAW_STREAM:
        raise UnsupportedFormatError(
            f"Detected {detected.format!r}, which is not a single-file compressed "
            "stream. Use open_archive for archive containers."
        )
    return detected.format.stream


def extract(
    source: OpenSourceInput,
    dest: str | Path,
    *,
    policy: ExtractionPolicy | ExtractionPolicyStr = ExtractionPolicy.STRICT,
    overwrite: OverwritePolicy | OverwritePolicyStr = OverwritePolicy.ERROR,
    on_error: OnError | OnErrorStr = OnError.STOP,
    abort_on: Collection[AbortOn | AbortOnStr] = (),
    format: ArchiveFormat | str | None = None,
    password: PasswordInput = None,
    encoding: str | None = None,
    on_progress: Callable[[ExtractionProgress], None] | None = None,
    config: ArchiveyConfig | None = None,
    limits: ExtractionLimits | None = None,
) -> ExtractionReport:
    """Open ``source``, apply safety checks, and write **all** members to ``dest``.

    The one-shot extraction API (see ``safe-extraction``). It deliberately has **no**
    member-selection parameter — selecting a subset requires the member list, which would
    force a reopen; use :meth:`ArchiveReader.extract_all` with ``members=`` on an already
    open reader instead. Extraction is safe-by-default: ``ExtractionPolicy.STRICT`` and
    ``OverwritePolicy.ERROR``, with the decompression-bomb guards active.

    A **non-seekable** stream source (a pipe, a socket) is opened in streaming mode
    automatically: extraction is a single forward pass, so it needs no random access, and
    failing fast would reject a source it can perfectly well consume. A seekable source
    keeps random-access mode — that preserves the re-readable second pass that recovers a
    hardlink whose target failed or preceded it in archive order.

    ``abort_on`` names events that end the whole call the first time they occur — a
    blocked member, a name collision, a portable-name rewrite — raising instead of
    returning a report. It is independent of ``on_error``; see
    :class:`~archivey.AbortOn`.

    Returns an :class:`~archivey.ExtractionReport` whose diagnostic summary spans
    detection, open, and extraction for this call.
    """
    # Checked and converted here rather than left to open_archive and extract_all
    # below, so a wrong-typed argument is refused before the source is resolved and
    # peeked, and the message names the call the caller actually made.
    format = coerce_archive_format(format, call="extract(format=…)")
    policy = coerce_enum(policy, ExtractionPolicy, call="extract()", param="policy=")
    overwrite = coerce_enum(
        overwrite, OverwritePolicy, call="extract()", param="overwrite="
    )
    on_error = coerce_enum(on_error, OnError, call="extract()", param="on_error=")
    abort_on = coerce_enum_collection(
        abort_on, AbortOn, call="extract()", param="abort_on="
    )
    check_config(config, call="extract(config=…)")
    check_extraction_limits(limits, call="extract(limits=…)")
    check_encoding(encoding, call="extract(encoding=…)")
    check_callable(on_progress, call="extract(on_progress=…)")

    # Peek only to choose access mode; open_archive re-resolves ``source`` (cheap: a
    # path source opens nothing until it is read).
    with resolve_source(source).source as peek_target:
        streaming = not peek_target.is_directory and not peek_target.seekable()

    with open_archive(
        source,
        format=format,
        streaming=streaming,
        password=password,
        encoding=encoding,
        config=config,
    ) as reader:
        # Reader already carries ``config`` from open_archive — do not forward again.
        report = reader.extract_all(
            dest,
            policy=policy,
            overwrite=overwrite,
            on_error=on_error,
            abort_on=abort_on,
            on_progress=on_progress,
            limits=limits,
        )
        # extract_all's report.diagnostics is extraction-only. This reader was opened
        # fresh for this call, so reader.diagnostics already spans detect+open+extract.
        return ExtractionReport(
            results=report.results,
            diagnostics=reader.diagnostics,
        )
