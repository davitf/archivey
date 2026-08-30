"""Format detection: ``detect_format()`` and the ``FormatInfo`` it returns.

Detection is **magic-first** (an exact magic-byte match at the expected offset →
``CERTAIN``) with an extension fallback (``GUESS``). The magic and extension tables are
not hand-maintained here: each registered backend declares its ``MAGIC`` / ``EXTENSIONS``
as data and the detector aggregates them, so a new format becomes detectable by
registering its backend (see ``format-detection`` and ``backend-registry``).

Detection never consumes bytes from the source: paths are opened and closed; seekable
streams are read and restored to their **starting position** (the archive is taken to
begin wherever the stream is positioned when handed in, so a mid-positioned stream —
e.g. an archive embedded in a larger file — detects against the right bytes); a
non-seekable stream must be wrapped in a
:class:`~archivey.internal.streams.peekable.PeekableStream` first (the opener does this),
which detection inspects via ``peek``.

Formats without an exact magic are recognized by a **content probe**: Brotli (no signature
at all) and zlib (a 2-byte header too unspecific to trust, so its probe gates on that
header before decoding). Each probe is a function the backends declare as data — for the
stream codecs, on the codec descriptor — so the detector stays format-agnostic.

The steps run strongest-signal-first: near magic → SFX scan → **far magic** → content
probes → extension. Both signals ahead of the probes are there for the same reason — a
probe is the weakest evidence archivey has, and one asked to judge arbitrary bytes will
sometimes say yes:

- A **self-extracting** archive has no archive magic at offset 0 at all: an executable
  stub comes first. When the leading bytes look executable-shaped the detector searches
  forward for the backends' ``SFX_MAGIC`` within the shared ``SFX_MAX`` window and reports
  the payload start as ``payload_offset`` (``PROBABLE`` / ``sfx_scan``) — see
  ``executable_cue`` and ``format-detection``'s "Executable-looking prefixes must not
  silently become a wrong stream format".
- **Far magic** is exact magic whose end offset lies outside the default window (today
  ISO 9660's ``CD001`` at 32 769), so it needs an extended peek taken on demand. It runs
  before the probes because a bootable or hybrid ISO reserves its first 32 KiB for
  bootloader code — the data class a probe accepts — and the exact magic was available at
  a known offset the whole time. The peek is size-gated: a source known to be smaller than
  the window never pays it. A larger one does, **including when a probe then succeeds** —
  that is a bounded read this order adds rather than moves, since a probe hit used to
  return before far magic ran (measured at ~3% of ``detect_format`` on a 2 MB stream; see
  ``detection-format-gaps`` design §Risks).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, Callable

from archivey.config import DEFAULT_ARCHIVEY_CONFIG, AcceleratorMode
from archivey.diagnostics import (
    DiagnosticCode,
    DiagnosticSummary,
    FormatConflictContext,
)
from archivey.exceptions import ArchiveyError, FormatDetectionError
from archivey.internal.diagnostics_collector import (
    DiagnosticCollector,
    collector_from_config,
)
from archivey.internal.logs import detection as logger
from archivey.internal.registry import get_registry
from archivey.internal.sfx import (
    SFX_MAX,
    ExecutableCue,
    executable_cue,
    find_magic_in_prefix,
)
from archivey.internal.streams.brotli_framing import (
    BrotliBlock,
    parse_metablock,
)
from archivey.internal.streams.peekable import DETECTION_LIMIT, PeekableStream
from archivey.internal.streams.streamtools import (
    ReadOnlyIOStream,
    is_seekable,
    read_exact,
    source_byte_size,
    source_name,
)
from archivey.types import (
    ArchiveFormat,
    ContainerFormat,
    MagicSignature,
    StreamFormat,
)

if TYPE_CHECKING:
    from archivey.config import ArchiveyConfig

# Decompressed bytes needed to see a TAR ``ustar`` signature at offset 257 (one 512-byte
# header block covers it).
_INNER_TAR_PROBE_BYTES = 512

# Upper bound on compressed input the inner-TAR probe reads from the source when the peeked
# detection prefix is too short. bzip2 is block-transform (BWT) based: it emits no output
# until a whole block is read, and a block holds up to 900 KB uncompressed (level 9), which
# for incompressible leading data compresses to just over 900 KB. 1 MiB covers a full
# worst-case first block with margin; a stream-oriented codec (gzip/xz/zstd/…) reaches the
# header region from the ordinary prefix and never triggers this larger read.
_INNER_TAR_MAX_PROBE_BYTES = 1 << 20

# Non-seekable ``read_at`` ceiling for content-probe chain walks: reaching offset N means
# buffering [0, N). 1 MiB covers a second link after a 4- or 5-nibble first block; a
# 6-nibble first block (up to 16 MiB) is declined (``None`` → cannot disprove).
_PROBE_READ_AT_MAX_OFFSET_NONSEEKABLE = 1 << 20


class DetectionConfidence(Enum):
    CERTAIN = "certain"  # exact magic-byte match at the expected offset
    PROBABLE = "probable"  # structural/content probe (inner-tar probe, SFX scan)
    # No confirmation strong enough to rely on: an extension-only guess, or a content
    # probe hit in the weak evidence class (today: extensionless Brotli whose first
    # meta-block is uncompressed/metadata).
    GUESS = "guess"


@dataclass(frozen=True)
class FormatInfo:
    """The result of :func:`detect_format` — the detected format plus how sure we are."""

    format: ArchiveFormat
    confidence: DetectionConfidence
    detected_by: str  # "magic", "extension", "content_probe", "sfx_scan"
    encoding_hint: str | None = None
    payload_offset: int = (
        0  # nonzero only for SFX archives (is-SFX == payload_offset > 0)
    )
    diagnostics: DiagnosticSummary = field(default_factory=DiagnosticSummary.empty)
    # Internal provenance for ``format_unconfirmed``: True when a matching extension or
    # an inner-TAR upgrade corroborated a content-probe claim. ``compare=False`` keeps it
    # out of the generated ``__eq__``, ``repr=False`` out of ``__repr__``; that is what
    # actually holds it outside the public ``detect_format`` contract — the field is
    # reachable but constrains nothing. Deliberate: ``False`` here is overloaded — it means
    # both "a probe with no corroboration" and "not a probe at all", so an exact magic hit
    # reads False — and a bool cannot separate those. ``probe-provenance-unconfirmed``
    # task 5.1 tracks the public evidence-set shape that could.
    corroborated: bool = field(default=False, compare=False, repr=False)


def _peek_prefix(source: str | Path | BinaryIO, length: int) -> bytes:
    """Return the source's next ``length`` bytes without consuming them.

    Paths are opened and closed; a :class:`PeekableStream` is peeked; a seekable stream
    is read from its **current position** and restored to it (the archive starts
    wherever the caller positioned the stream — see the stream-position contract in
    ``format-detection``). A raw non-seekable stream would lose the prefix, so the
    caller (the opener) must wrap it in a ``PeekableStream`` first.
    """
    if isinstance(source, (str, Path)):
        with open(source, "rb") as f:
            return read_exact(f, length)
    if isinstance(source, PeekableStream):
        return source.peek(length)
    if is_seekable(source):
        start = source.tell()
        data = read_exact(source, length)
        source.seek(start)
        return data
    # Raw non-seekable stream used standalone: reading consumes bytes the caller can no
    # longer reach. Detection still works, but the prefix is gone — the opener avoids this
    # by wrapping non-seekable sources in a PeekableStream.
    return read_exact(source, length)


def _make_probe_read_at(
    source: str | Path | BinaryIO,
    prefix: bytes,
    *,
    seekable: bool,
) -> tuple[Callable[[int, int], bytes | None], Callable[[], None]]:
    """Build the optional ``read_at`` callback for content-probe chain walks.

    Returns ``(read_at, close)``. ``read_at`` yields ``bytes`` at the given absolute
    offset (relative to the archive origin — the position detection started from),
    short/empty on EOF, or ``None`` when the caller declines (non-seekable past
    ``_PROBE_READ_AT_MAX_OFFSET_NONSEEKABLE``). Bytes already in ``prefix`` are served
    without I/O. Path sources keep one open handle for the walk; call ``close`` when
    the probe loop finishes.
    """
    path_handle: list[BinaryIO] = []

    def read_at(offset: int, length: int) -> bytes | None:
        if offset < 0 or length < 0:
            return None
        end = offset + length
        if end <= len(prefix):
            return prefix[offset:end]
        if not seekable and end > _PROBE_READ_AT_MAX_OFFSET_NONSEEKABLE:
            return None
        if isinstance(source, (str, Path)):
            if not path_handle:
                path_handle.append(open(source, "rb"))
            f = path_handle[0]
            f.seek(offset)
            return read_exact(f, length)
        if isinstance(source, PeekableStream):
            # Growing peek buffers [0, end); refuse past the non-seekable cap above.
            buf = source.peek(end)
            if offset >= len(buf):
                return b""
            return buf[offset:end]
        if is_seekable(source):
            origin = source.tell()
            try:
                source.seek(origin + offset)
                return read_exact(source, length)
            finally:
                source.seek(origin)
        # Raw non-seekable without PeekableStream: cannot reposition; decline.
        return None

    def close() -> None:
        for f in path_handle:
            f.close()
        path_handle.clear()

    return read_at, close


def _match_magic(
    data: bytes,
    magic_entries: list[MagicSignature],
) -> ArchiveFormat | None:
    """Return the format of the first exact magic signature matching ``data``."""
    for entry in magic_entries:
        if data[entry.offset : entry.offset + len(entry.magic)] == entry.magic:
            return entry.format
    return None


def _match_magic_behind_prefix(
    data: bytes,
    walks: list[tuple[ArchiveFormat, Callable[[bytes], bool]]],
) -> ArchiveFormat | None:
    """Return the format whose exact magic sits behind its own structural prefix frames.

    Each walk is arithmetic over ``data`` alone (the already-peeked prefix) and never
    triggers a larger read: a declared frame size running past those bytes is a decline.
    """
    for fmt, walk in walks:
        if walk(data):
            return fmt
    return None


def _match_extension(
    name: str | None, extension_map: dict[str, ArchiveFormat]
) -> tuple[ArchiveFormat, str] | None:
    if name is None:
        return None
    lowered = name.lower()
    # Longest extension wins so ".tar.gz" beats ".gz".
    for ext in sorted(extension_map, key=len, reverse=True):
        if lowered.endswith(ext.lower()):
            return extension_map[ext], ext
    return None


class _BoundedPeekReader(ReadOnlyIOStream):
    """A bounded, non-consuming reader over a ``peek_more`` callable.

    ``peek_more(n)`` returns the source's first ``n`` bytes without consuming them
    (idempotent, growing supersets — see :func:`_peek_prefix`). Reads walk an
    internal offset over successive peeks (caching the last buffer so growth stays
    linear), letting a codec pull exactly as much compressed input as it needs and
    never more than ``limit`` (one maximum compressor block).

    Seekable within the bound so codecs that use seek during a probe (or need a
    repositionable view of the peeked prefix) can still run; seeking never pulls
    bytes beyond ``limit``, and peeks still grow only on demand when a read needs
    more of the prefix.
    """

    def __init__(self, peek_more: Callable[[int], bytes], limit: int) -> None:
        super().__init__()
        self._peek_more = peek_more
        self._limit = limit
        self._offset = 0
        self._buf = b""

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self, /) -> int:
        return self._offset

    def seek(self, offset: int, whence: int = 0, /) -> int:
        if whence == 0:  # SEEK_SET
            new_pos = offset
        elif whence == 1:  # SEEK_CUR
            new_pos = self._offset + offset
        elif whence == 2:  # SEEK_END — end of the bounded window, not the real source
            new_pos = self._limit + offset
        else:
            raise ValueError(f"invalid whence: {whence!r}")
        if new_pos < 0:
            raise ValueError(f"negative seek position {new_pos}")
        # Cap at the bound; reads past the peeked prefix already return b"".
        self._offset = min(new_pos, self._limit)
        return self._offset

    def read(self, n: int = -1, /) -> bytes:
        end = self._limit if n < 0 else min(self._offset + n, self._limit)
        if end > len(self._buf):
            self._buf = self._peek_more(end)  # a superset of the current buffer
        chunk = self._buf[self._offset : end]
        self._offset += len(chunk)
        return chunk


def _probe_inner_tar(
    stream_format: StreamFormat,
    peek_more: Callable[[int], bytes],
) -> bool:
    """Whether decompressing the source yields a TAR (``ustar`` at offset 257).

    The codec layer decodes the compressed source and the inner ``ustar`` magic confirms a
    tarball wrapped in the compressor. The decoder reads from a bounded, non-consuming view of
    the source (:class:`_BoundedPeekReader` over ``peek_more``), so it pulls exactly as much
    compressed input as it needs to reach the TAR header region and no more: a stream-oriented
    codec (gzip/xz/zstd/…) emits output incrementally and stops after a few KiB, while a
    block-transform codec (bzip2), which emits nothing until a whole block is read, pulls up to
    one maximum block (``_INNER_TAR_MAX_PROBE_BYTES``).

    The peek reader is seekable within that bound so codecs can reposition inside
    the peeked prefix during a probe.
    Accelerators are forced ``OFF``: ``seekable=True`` must not flip AUTO rapidgzip /
    IndexedBzip2File on for a short detection peek (those paths reject incomplete
    sources and can leak raw C++ exceptions on corrupt prefixes). Prefer that over
    the older ``seekable=False`` workaround for keeping accelerators off the probe.

    Returns ``False`` (deferring the determination to open time) when the codec backend is
    absent, the source is not decodable as this codec, or the decoded output carries no TAR
    header.
    """
    # Imported here rather than at module load to avoid a detection<->codecs import cycle.
    from archivey.internal.config import StreamConfig
    from archivey.internal.streams.codecs import (
        codec_for_stream_format,
        is_codec_available,
        open_codec_stream,
    )

    try:
        codec = codec_for_stream_format(stream_format)
    except KeyError:
        return False
    if not is_codec_available(codec):
        return False

    source = _BoundedPeekReader(peek_more, _INNER_TAR_MAX_PROBE_BYTES)
    try:
        with open_codec_stream(
            codec,
            source,
            config=StreamConfig(
                streaming=True,
                seekable=True,
                use_rapidgzip=AcceleratorMode.OFF,
                use_indexed_bzip2=AcceleratorMode.OFF,
            ),
        ) as stream:
            head = stream.read(_INNER_TAR_PROBE_BYTES)
    except (ArchiveyError, OSError, ValueError):
        # Not decodable as this codec, or truncated before a full block -> not an inner tar.
        return False
    return head[257:262] == b"ustar"


def _brotli_probe_confidence(
    prefix: bytes,
    ext_match: tuple[ArchiveFormat, str] | None,
) -> DetectionConfidence:
    """PROBABLE when ``.br`` (or a TAR+Brotli extension) agrees, or first block is compressed.

    Uncompressed/metadata-first with no corroborating extension is the residual
    false-positive class and reports ``GUESS``. Confidence grades evidence strength
    only; whether a later decode failure is stamped ``format_unconfirmed`` is a
    separate provenance question (probe-only vs corroborated).
    """
    if ext_match is not None and ext_match[0].stream is StreamFormat.BROTLI:
        return DetectionConfidence.PROBABLE
    if parse_metablock(prefix).outcome is BrotliBlock.COMPRESSED:
        return DetectionConfidence.PROBABLE
    return DetectionConfidence.GUESS


def _resolve_single_file_or_tar(
    fmt: ArchiveFormat,
    base_confidence: "DetectionConfidence",
    base_detected_by: str,
    peek_more: Callable[[int], bytes],
    *,
    ext_match: tuple[ArchiveFormat, str] | None = None,
) -> FormatInfo:
    """Upgrade a single-file-compressor match to its TAR combo when the payload is a tarball.

    A ``RAW_STREAM`` compressor (``.gz``/``.bz2``/``.xz``/…) is probed for an inner TAR; on a
    hit it becomes ``(TAR, <stream>)`` (e.g. ``TAR_GZ``) reported as ``PROBABLE`` /
    ``content_probe`` (the inner-TAR test is structural, weaker than an exact magic).
    An inner-TAR upgrade is corroboration for a probe hit — two independent signals had
    to hold — so ``corroborated`` is set. Otherwise a matching extension (same stream
    format) is the corroborating signal. ``peek_more`` gives the probe a bounded,
    non-consuming view of the source to decode from.
    """
    if (
        fmt.container == ContainerFormat.RAW_STREAM
        and fmt.stream != StreamFormat.UNCOMPRESSED
    ):
        if _probe_inner_tar(fmt.stream, peek_more):
            tar_fmt = ArchiveFormat(ContainerFormat.TAR, fmt.stream)
            return FormatInfo(
                tar_fmt,
                DetectionConfidence.PROBABLE,
                "content_probe",
                corroborated=True,
            )
    corroborated = base_detected_by == "content_probe" and _extension_corroborates(
        ext_match, fmt
    )
    return FormatInfo(fmt, base_confidence, base_detected_by, corroborated=corroborated)


def _is_deferred_inner_tar(ext_fmt: ArchiveFormat, resolved: ArchiveFormat) -> bool:
    """Whether a TAR-combo extension over a bare-compressor result is a *benign* mismatch.

    ``foo.tar.gz`` (extension → ``TAR_GZ``) reported as bare ``GZ`` is the documented
    deferred case: the inner-TAR probe could not run (codec backend absent) or found no tar,
    so the bare compressor is reported and the inner-TAR determination is left to open time.
    That is not a real conflict, so it must not emit a warning.
    """
    return (
        resolved.container == ContainerFormat.RAW_STREAM
        and ext_fmt.container == ContainerFormat.TAR
        and ext_fmt.stream == resolved.stream
    )


def _extension_corroborates(
    ext_match: tuple[ArchiveFormat, str] | None,
    resolved: ArchiveFormat,
) -> bool:
    """Whether the filename agrees with ``resolved`` — the same test as "no conflict".

    Deliberately the negation of what :func:`_warn_on_conflict` warns about, sharing
    :func:`_is_deferred_inner_tar` with it, so the module gives one answer to "does the
    name agree?" rather than two. That covers the ``.br`` rule and generalizes it to every
    magic-less codec (``.lzma``, ``.zz``), plus the deferred inner-TAR case where
    ``foo.tar.br`` is reported as bare ``BROTLI`` because the inner-TAR probe could not run.

    Comparing only ``stream`` would be wrong: every container shares
    ``StreamFormat.UNCOMPRESSED``, so a ``.zip`` name would "agree" with a ``TAR`` result.
    Unreachable while every content probe is a ``RAW_STREAM`` codec, but
    ``ReadBackend.CONTENT_PROBES`` exists precisely so a container backend can register
    one, and that seam must not silently arm this.

    Contested: PR #263's analysis §6 keys the stamp on the winning candidate's
    content-evidence class, under which a matching name is retained as evidence but cannot
    promote it — so this predicate leaves the stamp path entirely if that lands, together
    with ``_brotli_probe_confidence``'s ``.br``-to-``PROBABLE`` rule, which is the same
    rule expressed twice. See ``openspec/specs/error-handling`` for the scope and why the
    two must move together.
    """
    if ext_match is None:
        return False
    ext_fmt = ext_match[0]
    return ext_fmt == resolved or _is_deferred_inner_tar(ext_fmt, resolved)


class _ConflictEvidence(Enum):
    """Which branch outranked the extension, phrased for the conflict diagnostic.

    Every branch that outranks the extension calls :func:`_warn_on_conflict`, and they do
    not all hold the same kind of evidence. Exact magic is a certainty; a content probe is
    the weakest signal archivey has, and reports ``PROBABLE`` or ``GUESS`` precisely
    because it can be wrong. Saying "magic bytes indicate X" from the probe branch
    overstates the winning evidence in the one case where a reader most needs to weigh it
    against the name we just overruled.

    Deliberately not ``FormatInfo.detected_by``: those strings are public and
    ``detection-result-surface`` renames two of them, so reusing them here would tie one
    log line's wording to a contract that is about to move.

    **So this text is load-bearing, not a nicety.** A ``detect_format`` caller can read
    ``detected_by`` off the returned ``FormatInfo``, but nobody on the ``open_archive``
    path can: that object is dropped once the reader exists, ``_format_provenance``
    collapses magic, far magic, the SFX scan and the probes to ``chosen_by="content"``
    (and is private besides), and ``FormatConflictContext`` carries only the two formats.
    The retained diagnostic outlives every one of them, so on the path where this warning
    most matters the message *is* the evidence channel. Adding a typed field is
    ``detection-result-surface``'s to make, when it reworks the values this would have to
    spell; until then, do not weaken these phrases on the grounds that the data is
    available elsewhere, because on that path it is not.
    """

    MAGIC = "magic bytes indicate"
    SFX_SCAN = "archive magic behind an executable stub indicates"
    CONTENT_PROBE = "content inspection indicates"


def _warn_on_conflict(
    collector: DiagnosticCollector,
    name: str | None,
    ext_match: tuple[ArchiveFormat, str] | None,
    resolved: ArchiveFormat,
    evidence: _ConflictEvidence,
) -> None:
    if ext_match is None:
        return
    ext_fmt, extension = ext_match
    if ext_fmt == resolved or _is_deferred_inner_tar(ext_fmt, resolved):
        return
    message = (
        f"Format conflict for {name!r}: extension suggests {ext_fmt!r} but "
        f"{evidence.value} {resolved!r}; using that result over the extension."
    )
    collector.emit(
        code=DiagnosticCode.FORMAT_EXTENSION_CONFLICT,
        message=message,
        context=FormatConflictContext(
            source_name=name,
            extension=extension,
            extension_format=repr(ext_fmt),
            detected_format=repr(resolved),
        ),
        logger=logger,
    )


def _scan_for_sfx_payload(
    entries: list[MagicSignature],
    peek_more: Callable[[int], bytes],
) -> FormatInfo | None:
    """Search the SFX window for an appended archive, as ``(format, payload_offset)``.

    ``entries`` are the backends' ``SFX_MAGIC`` declarations, so which formats can hide
    behind a stub is backend data like every other detection signal. ``PROBABLE`` rather
    than ``CERTAIN``: an exact magic found at a *searched-for* offset is a weaker claim
    than one found at the offset the format specifies.
    """
    by_needle = {entry.magic: entry.format for entry in entries}
    hit = find_magic_in_prefix(peek_more, tuple(by_needle), limit=SFX_MAX)
    if hit is None:
        return None
    offset, needle = hit
    return FormatInfo(
        by_needle[needle],
        DetectionConfidence.PROBABLE,
        "sfx_scan",
        payload_offset=offset,
    )


def detect_format(
    source: str | Path | BinaryIO,
    *,
    config: ArchiveyConfig | None = None,
    collector: DiagnosticCollector | None = None,
) -> FormatInfo:
    """Identify the archive format of ``source`` without fully opening it.

    Returns a :class:`FormatInfo`. Raises :class:`FormatDetectionError` when no magic
    pattern matches and no extension guess is available.

    ``collector``, when provided (e.g. from :func:`archivey.open_archive`), receives
    detection diagnostics into the prospective reader's shared collector. When omitted,
    a finite standalone collector is created from ``config`` (or the library default).
    """
    owned_collector = collector is None
    if owned_collector:
        effective_config = config if config is not None else DEFAULT_ARCHIVEY_CONFIG
        collector = collector_from_config(effective_config)
        detection_wm = None
    else:
        detection_wm = collector.watermark()

    info = _detect_format_body(source, collector)
    diagnostics = (
        collector.snapshot()
        if owned_collector
        else collector.snapshot(since=detection_wm)
    )
    return replace(info, diagnostics=diagnostics)


def _detect_format_body(
    source: str | Path | BinaryIO, collector: DiagnosticCollector
) -> FormatInfo:
    registry = get_registry()
    magic_entries = registry.magic_entries()
    extension_map = registry.extension_map()
    name = source_name(source)
    ext_match = _match_extension(name, extension_map)
    ext_fmt = ext_match[0] if ext_match is not None else None

    # Magic signals split by where they live: "near" ones fit in the default window; "far"
    # ones (ISO's CD001 at 32 769) need an extended peek that is only taken on demand, so the
    # common case never reads 32 KiB just to identify a ZIP/gz/tar in the first few bytes.
    near = [e for e in magic_entries if e.offset + len(e.magic) <= DETECTION_LIMIT]
    far = [e for e in magic_entries if e.offset + len(e.magic) > DETECTION_LIMIT]
    near_needed = max(
        DETECTION_LIMIT, max((e.offset + len(e.magic) for e in near), default=0)
    )
    data = _peek_prefix(source, near_needed)

    # The inner-TAR probe decodes the source through a bounded, non-consuming view built on
    # this callable: stream-oriented codecs reach the header from the first few KiB, while a
    # block codec (bzip2) may need a full block. Each peek is bounded and restores position /
    # buffers in the PeekableStream (like the prefix peek above), so a large-block .tar.bz2 is
    # not mis-reported as bare .bz2.
    def peek_more(length: int) -> bytes:
        return _peek_prefix(source, length)

    # 1. Exact magic in the default window. A single-file compressor is additionally probed
    #    for an inner TAR (so .tar.gz → TAR_GZ, not bare GZ).
    #
    #    A format whose magic may sit behind structural frames of its own — today only
    #    zstd, whose skippable frames carry no compressed data and may precede the first
    #    regular frame — declares a walk over the peeked prefix instead of a table entry,
    #    so the structural prefix alone is never claimed as the format. Still an exact
    #    magic match, and reported as one.
    magic_fmt = _match_magic(data, near)
    if magic_fmt is None:
        magic_fmt = _match_magic_behind_prefix(data, registry.magic_prefix_walks())
    if magic_fmt is not None:
        info = _resolve_single_file_or_tar(
            magic_fmt,
            DetectionConfidence.CERTAIN,
            "magic",
            peek_more,
            ext_match=ext_match,
        )
        _warn_on_conflict(
            collector, name, ext_match, info.format, _ConflictEvidence.MAGIC
        )
        return info

    # 2. Self-extracting archives: an executable stub, then real archive magic somewhere
    #    in the SFX window. This runs before the content probes rather than after, which
    #    is the whole fix: a stub is arbitrary bytes, and a probe handed arbitrary bytes
    #    sometimes says yes — a low-entropy `MZ` stub in front of a real RAR/7z/ZIP used
    #    to be reported as BROTLI, and open_archive then fabricated a single-file member.
    cue = executable_cue(data)
    if cue is not ExecutableCue.NONE:
        sfx_info = _scan_for_sfx_payload(registry.sfx_magic_entries(), peek_more)
        if sfx_info is not None:
            _warn_on_conflict(
                collector,
                name,
                ext_match,
                sfx_info.format,
                _ConflictEvidence.SFX_SCAN,
            )
            return sfx_info

    # Archive-relative reachable length, used by the far-magic size gate below and by the
    # probes' framing / completeness / chain walk. ``source_byte_size`` is total size from
    # offset 0 of the underlying object; ``read_at`` and the chain walk are relative to the
    # archive origin (current stream position). Paths open at origin 0, so the totals match;
    # for a mid-positioned seekable stream, subtract ``tell()`` so equality checks (declared
    # end with trailing bytes) use the same yardstick. Unknown → None; a short peek that
    # returned fewer bytes than requested also reveals the size of a non-seekable source
    # that ended early (< DETECTION_LIMIT).
    length = source_byte_size(source)
    if length is not None and not isinstance(source, (str, Path)):
        if is_seekable(source):
            remaining = length - source.tell()
            length = remaining if remaining >= 0 else None
    if length is None and len(data) < near_needed:
        length = len(data)

    # 3. Far magic (ISO's CD001 at offset 32 769): peek the extended 32 774-byte window on
    #    demand. This runs *before* the content probes: it is exact magic at a known offset
    #    and they are the weakest signal archivey has. A bootable or hybrid ISO reserves its
    #    first 32 KiB for bootloader code — exactly the data class the Brotli probe accepts —
    #    so consulting the probes first claimed a whole filesystem as one fabricated
    #    `*.uncompressed` member while the magic sat unread at a known offset.
    #
    #    Size-gated: a source known to be smaller than the window never pays the peek (no
    #    ISO is that small). A source of unknown length takes a short peek, matches nothing
    #    and falls through — it is never rejected solely for being too short.
    if far:
        far_needed = max(e.offset + len(e.magic) for e in far)
        if length is None or length >= far_needed:
            far_data = _peek_prefix(source, far_needed)
            far_fmt = _match_magic(far_data, far)
            if far_fmt is not None:
                _warn_on_conflict(
                    collector, name, ext_match, far_fmt, _ConflictEvidence.MAGIC
                )
                return FormatInfo(far_fmt, DetectionConfidence.CERTAIN, "magic")

    # 4. Formats without an exact magic, recognized by a content probe (Brotli decodes a
    #    prefix; zlib gates on its 2-byte header then decodes). A probe is skipped when its
    #    backend is absent, so detection falls through. A matching compressor is likewise
    #    probed for an inner TAR (so .tar.br → TAR_BROTLI).
    #
    #    Skipped entirely on a STRONG executable cue: a DOS header that really points at
    #    `PE\0\0`, or a valid ELF ident block, is not a compressed stream, and letting a
    #    probe claim one produces a fabricated `*.uncompressed` member — a silent wrong
    #    answer. A WEAK cue does not gate the probes: `MZ` alone is two bytes, and a
    #    genuine Brotli stream that happens to start with them must still be detectable.
    if cue is not ExecutableCue.STRONG:
        # Bounded read-at for the Brotli chain walk. Paths and seekable streams seek;
        # PeekableStream may grow its buffer up to the non-seekable cap.
        if isinstance(source, (str, Path)):
            probe_seekable = True
        elif isinstance(source, PeekableStream):
            probe_seekable = False
        else:
            probe_seekable = is_seekable(source)
        read_at, close_read_at = _make_probe_read_at(
            source, data, seekable=probe_seekable
        )
        try:
            for probe_fmt, probe in registry.content_probes():
                if probe(data, source_length=length, read_at=read_at):
                    confidence = DetectionConfidence.PROBABLE
                    if probe_fmt.stream is StreamFormat.BROTLI:
                        confidence = _brotli_probe_confidence(data, ext_match)
                    info = _resolve_single_file_or_tar(
                        probe_fmt,
                        confidence,
                        "content_probe",
                        peek_more,
                        ext_match=ext_match,
                    )
                    _warn_on_conflict(
                        collector,
                        name,
                        ext_match,
                        info.format,
                        _ConflictEvidence.CONTENT_PROBE,
                    )
                    return info
        finally:
            close_read_at()

    # 5. Extension-only guess.
    if ext_fmt is not None:
        return FormatInfo(ext_fmt, DetectionConfidence.GUESS, "extension")

    raise FormatDetectionError(
        "Could not detect archive format: no magic-byte match and no usable file extension.",
        archive_name=name,
    )
