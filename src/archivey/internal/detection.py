"""Format detection: ``detect_format()`` and the ``FormatInfo`` it returns.

Detection is **magic-first** (an exact magic-byte match at the expected offset →
``CERTAIN``) with an extension fallback (``GUESS``). The magic and extension tables are
not hand-maintained here: each registered backend declares its ``MAGIC`` / ``EXTENSIONS``
as data and the detector aggregates them, so a new format becomes detectable by
registering its backend (see ``format-detection`` and ``backend-registry``).

Detection never consumes bytes from the source: paths keep one detection handle; seekable
streams are read forward once and restored to their **starting position** (the archive is
taken to begin wherever the stream is positioned when handed in); a non-seekable stream
is peeked through the :class:`~archivey.internal.source.ArchiveSource` the opener built,
whose replay prefix keeps the bytes for the backend. A raw non-seekable stream handed to
``detect_format`` directly loses what detection read, unless the caller buffers it.

Every front-of-source read goes through one detection-owned
:class:`~archivey.internal.detection_workspace.PrefixWorkspace` that grows monotonically —
extending the window reads only the delta; bytes already retrieved are never re-read.

Formats without an exact magic are recognized by a **content probe**: Brotli (no signature
at all) and zlib (a 2-byte header too unspecific to trust, so its probe gates on that
header before decoding). Each probe is a function the backends declare as data — for the
stream codecs, on the codec descriptor — so the detector stays format-agnostic.

The steps run strongest-signal-first: near magic → SFX scan → **far magic** → content
probes → extension. Both signals ahead of the probes are there for the same reason — a
probe is the weakest evidence archivey has, and one asked to judge arbitrary bytes will
sometimes say yes:

- A **self-extracting** archive has no archive magic at offset 0 at all: a prefix
  (executable stub or ``#!`` launcher) comes first. When the leading bytes look
  prefix-shaped the detector searches forward for the backends' ``SFX_MAGIC`` within
  the shared ``SFX_MAX`` window and reports the payload start as ``payload_offset``
  (``PROBABLE`` / ``sfx_scan``). The cue is a cost gate — avoid reading 2 MiB from
  every file — not a false-positive defence; a format-owned validator rejects a
  decoy. See ``executable_cue`` and ``format-detection``'s "Executable-looking
  prefixes must not silently become a wrong stream format".
- **Far magic** is exact magic whose end offset lies outside the default window (today
  ISO 9660's ``CD001`` at 32 769), so it needs an extended peek taken on demand. It runs
  before the probes because a bootable or hybrid ISO reserves its first 32 KiB for
  bootloader code — the data class a probe accepts — and the exact magic was available at
  a known offset the whole time. The peek is size-gated: a source known to be smaller than
  the window never pays it.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, Callable

from archivey.config import DEFAULT_ARCHIVEY_CONFIG, AcceleratorMode
from archivey.detection_cost import (
    DetectionBudget,
    DetectionBudgetPreset,
    DetectionBudgetPresetStr,
    DetectionCapability,
    DetectionCostReceipt,
    MutableDetectionCostReceipt,
    TierSkip,
    TierSkipReason,
    default_detection_budget,
)
from archivey.diagnostics import (
    DiagnosticCode,
    DiagnosticSummary,
    FormatConflictContext,
)
from archivey.exceptions import ArchiveyError, FormatDetectionError
from archivey.internal.arg_checks import check_config
from archivey.internal.detection_workspace import DETECTION_LIMIT, PrefixWorkspace
from archivey.internal.diagnostics_collector import (
    DiagnosticCollector,
    collector_from_config,
)
from archivey.internal.enum_args import coerce_enum
from archivey.internal.logs import detection as logger
from archivey.internal.registry import get_registry
from archivey.internal.sfx import (
    SFX_MAX,
    ExecutableCue,
    HitOutcome,
    HitValidator,
    ScanNeedle,
    executable_cue,
    iter_magic_in_prefix,
)
from archivey.internal.source import ArchiveSource
from archivey.internal.streams.brotli_framing import (
    BrotliBlock,
    parse_metablock,
)
from archivey.internal.streams.streamtools import (
    ReadOnlyIOStream,
    require_source,
    source_name,
)
from archivey.internal.volumes import first_volume_for_stub
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
    # Detection's own cost receipt — not merged into ``CostReceipt`` / ``ArchiveInfo.cost``.
    # Public exposure on ``FormatInfo`` is ``detection-result-surface``; kept here so tests
    # and the fuzz harness can assert the access-shape and budget invariants. It is the
    # work the whole ``detect_format`` call did, both passes when it followed a stub.
    cost_receipt: DetectionCostReceipt | None = field(
        default=None, compare=False, repr=False
    )
    unavailable_tiers: tuple[TierSkip, ...] = field(
        default=(), compare=False, repr=False
    )


class _BoundedPeekReader(ReadOnlyIOStream):
    """A bounded, non-consuming reader over a ``peek_more`` callable.

    ``peek_more(n)`` returns the candidate's first ``n`` bytes without consuming them
    (idempotent, growing supersets — a workspace candidate view). Reads walk an
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
        # Set when a read asked for bytes past ``limit`` and the window was full, so the
        # bound (not the end of the source) cut the read short.
        self.hit_limit = False

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
        past_limit = n < 0 or self._offset + n > self._limit
        end = self._limit if n < 0 else min(self._offset + n, self._limit)
        if end > len(self._buf):
            self._buf = self._peek_more(end)  # a superset of the current buffer
        if past_limit and len(self._buf) >= self._limit:
            self.hit_limit = True
        chunk = self._buf[self._offset : end]
        self._offset += len(chunk)
        return chunk


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


def _probe_inner_tar(
    stream_format: StreamFormat,
    peek_more: Callable[[int], bytes],
    workspace: PrefixWorkspace | None = None,
) -> bool:
    """Whether decompressing the source yields a TAR (``ustar`` at offset 257).

    The codec layer decodes the compressed source and the inner ``ustar`` magic confirms a
    tarball wrapped in the compressor. The decoder reads from a bounded, non-consuming view of
    the source (:class:`_BoundedPeekReader` over ``peek_more``), so it pulls exactly as much
    compressed input as it needs to reach the TAR header region and no more.

    Accelerators are forced ``OFF``: ``seekable=True`` must not flip AUTO rapidgzip /
    IndexedBzip2File on for a short detection peek. Decoder limits are lifted for the
    same reason the codec content probes lift them (``_PROBE_STREAM_CONFIG`` in
    ``codecs.py``): a capped probe would call a ``.tar.xz`` with a large dictionary a
    bare ``.xz`` even for a caller who opened it with ``DecoderLimits.UNLIMITED``. The
    read is bounded, so the dictionary cannot fill past it, but liblzma still reserves
    the declared size. The open that follows applies the caller's limits.

    With a workspace, the compressed input is bounded by the budget's
    ``max_decode_input`` (and the workspace's read ceiling) as well as by
    :data:`_INNER_TAR_MAX_PROBE_BYTES`, and the decode is charged whether it succeeds or
    fails. A probe that reaches its bound without finding a TAR header records
    ``inner_tar`` as ``BUDGET_EXHAUSTED``: the answer is "not found within budget".

    Returns ``False`` (deferring the determination to open time) when the codec backend is
    absent, the source is not decodable as this codec, or the decoded output carries no TAR
    header.
    """
    # Imported here rather than at module load to avoid a detection<->codecs import cycle.
    from archivey.internal.config import DecoderLimits, StreamConfig
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

    limit = _INNER_TAR_MAX_PROBE_BYTES
    if workspace is not None:
        budget = workspace.budget
        if budget.max_decode_input <= 0 or budget.max_decode_output <= 0:
            workspace.record_skip("inner_tar", TierSkipReason.NOT_ENABLED_BY_POLICY)
            return False
        if budget.max_decode_output < _INNER_TAR_PROBE_BYTES:
            workspace.record_skip("inner_tar", TierSkipReason.BUDGET_EXHAUSTED)
            return False
        limit = min(limit, budget.max_decode_input, workspace.read_ceiling)

    source = _BoundedPeekReader(peek_more, limit)
    head = b""
    try:
        with open_codec_stream(
            codec,
            source,
            config=StreamConfig(
                streaming=True,
                seekable=True,
                use_rapidgzip=AcceleratorMode.OFF,
                use_indexed_bzip2=AcceleratorMode.OFF,
                decoder_limits=DecoderLimits.UNLIMITED,
            ),
        ) as stream:
            head = stream.read(_INNER_TAR_PROBE_BYTES)
    except (ArchiveyError, OSError, ValueError):
        # Not decodable as this codec, or truncated before a full block -> not an inner tar.
        pass
    found = head[257:262] == b"ustar"
    if workspace is not None:
        # Charged on every path: a decode that ran and then failed did the work too.
        workspace.charge_decode(
            input_bytes=source.tell(),
            output_bytes=len(head),
        )
        if not found and source.hit_limit:
            workspace.record_skip("inner_tar", TierSkipReason.BUDGET_EXHAUSTED)
    return found


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
    base_confidence: DetectionConfidence,
    base_detected_by: str,
    peek_more: Callable[[int], bytes],
    *,
    ext_match: tuple[ArchiveFormat, str] | None = None,
    workspace: PrefixWorkspace | None = None,
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
        if _probe_inner_tar(fmt.stream, peek_more, workspace):
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
    """Whether the filename agrees with ``resolved`` — the same test as "no conflict"."""
    if ext_match is None:
        return False
    ext_fmt = ext_match[0]
    return ext_fmt == resolved or _is_deferred_inner_tar(ext_fmt, resolved)


class _ConflictEvidence(Enum):
    """Which branch outranked the extension, phrased for the conflict diagnostic."""

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
    workspace: PrefixWorkspace,
    *,
    scan_limit: int,
    validators: dict[ArchiveFormat, HitValidator],
    restrict_to_validated: bool,
) -> FormatInfo | None:
    """Search the SFX window for an appended archive, as ``(format, payload_offset)``.

    ``entries`` are the backends' ``SFX_MAGIC`` declarations. Each needle carries its
    candidate-internal offset (today all zero for ZIP/RAR/7z; TAR ``ustar`` → 257 once
    that needle lands). The returned ``payload_offset`` is the **candidate origin**, not
    the raw needle position. A hit whose format-owned validator returns anything other
    than :attr:`HitOutcome.VALID` is skipped and the scan continues — earliest
    *valid* match, not earliest needle. ``PROBABLE`` rather than ``CERTAIN``: an exact
    magic found at a *searched-for* offset is a weaker claim than one found at the
    offset the format specifies.

    ``scan_limit`` is the budget-clamped window (``min(SFX_MAX, budget.max_scan_bytes)``);
    the charge lands whether the scan hits or misses so the receipt reflects the work.

    ``restrict_to_validated`` is the shebang cue: a script is text, so magics appear
    as literals. Search only formats that have a hit validator — derived from
    ``validators``, so 7z/RAR join automatically when they register theirs. Do not
    key this off :attr:`ExecutableCue.WEAK` alone: an unconfirmed ``MZ`` / ELF stub
    is the live 7z/RAR SFX path and keeps the full needle set.
    """
    if restrict_to_validated:
        entries = [entry for entry in entries if entry.format in validators]
    by_needle = {entry.magic: entry for entry in entries}
    needles = tuple(ScanNeedle(entry.magic, entry.offset) for entry in entries)
    result: FormatInfo | None = None
    for hit in iter_magic_in_prefix(peek_more, needles, limit=scan_limit):
        entry = by_needle[hit.needle]
        validator = validators.get(entry.format)
        if validator is not None:
            view = workspace.candidate_view(hit.candidate_origin, limit=scan_limit)
            source_len = workspace.remaining_known()
            remaining = (
                None
                if source_len is None
                else max(0, source_len - hit.candidate_origin)
            )
            if validator(view, remaining) is not HitOutcome.VALID:
                continue
        result = FormatInfo(
            entry.format,
            DetectionConfidence.PROBABLE,
            "sfx_scan",
            payload_offset=hit.candidate_origin,
        )
        break
    if workspace.take_clamped_view_read():
        workspace.record_skip("sfx_scan", TierSkipReason.BUDGET_EXHAUSTED)
    # Charge the window actually examined — a miss is the expensive case.
    workspace.charge_scanned(min(workspace.buffered_length, scan_limit))
    return result


def _resolve_budget(
    budget: DetectionBudget | DetectionBudgetPreset | DetectionBudgetPresetStr | None,
) -> DetectionBudget:
    """Normalize the ``budget=`` argument, refusing anything that is neither.

    A ``DetectionBudget`` passes through. Anything else is read as a preset, including
    its name as a string — ``budget="fast"`` is the mistake to expect, because
    ``DetectionBudgetPreset.FAST.value`` *is* ``"fast"``. Before this, an unrecognised
    value was returned unchanged and failed several frames down with a bare
    ``AttributeError`` naming a budget field.
    """
    if budget is None:
        return default_detection_budget()
    if isinstance(budget, DetectionBudget):
        return budget
    preset = coerce_enum(
        budget,
        DetectionBudgetPreset,
        call="detect_format()",
        param="budget=",
        also_accepts="DetectionBudget",
    )
    return DetectionBudget.for_preset(preset)


def detect_format(
    source: str | Path | BinaryIO,
    *,
    config: ArchiveyConfig | None = None,
    collector: DiagnosticCollector | None = None,
    budget: DetectionBudget
    | DetectionBudgetPreset
    | DetectionBudgetPresetStr
    | None = None,
    follow_stub_volumes: bool = True,
) -> FormatInfo:
    """Identify the archive format of ``source`` without fully opening it.

    Returns a :class:`FormatInfo`. Raises :class:`FormatDetectionError` when no magic
    pattern matches and no extension guess is available.

    ``collector``, when provided (e.g. from :func:`archivey.open_archive`), receives
    detection diagnostics into the prospective reader's shared collector. When omitted,
    a finite standalone collector is created from ``config`` (or the library default).

    ``budget`` caps what detection may spend; the default is
    :data:`~archivey.detection_cost.BALANCED_BUDGET` (import from
    ``archivey.detection_cost`` — not yet re-exported at the package root).

    A stub-only ``.exe`` / ``.sfx`` (no archive magic) beside a 7-Zip split first
    volume is detected as that volume's format when ``follow_stub_volumes`` is true
    — the default, so ``detect_format("vol.exe")`` agrees with ``open_archive``.
    ``open_archive`` probes with this flag off, then switches the source itself.
    The returned ``cost_receipt`` and ``unavailable_tiers`` then cover both passes, the
    stub's and the volume's. Each pass runs under the full ``budget``, so that receipt
    can exceed it.
    """
    # Before anything is read: an object that is neither a path nor a binary stream
    # used to reach the prefix workspace and die there as
    # `AttributeError: 'int' object has no attribute 'read'`, while `open_archive` on
    # the same value already said "unsupported source type". Same refusal, same words.
    require_source(source)
    check_config(config, call="detect_format(config=…)")

    owned_collector = collector is None
    if owned_collector:
        effective_config = config if config is not None else DEFAULT_ARCHIVEY_CONFIG
        collector = collector_from_config(effective_config)
        detection_wm = None
    else:
        detection_wm = collector.watermark()

    resolved_budget = _resolve_budget(budget)
    # One receipt across both passes: the stub pass is usually the expensive one (a
    # strong executable cue runs the full SFX scan), so the sibling-volume answer
    # carries its cost and its skips too.
    receipt = MutableDetectionCostReceipt()
    try:
        info = _detect_format_body(source, collector, resolved_budget, receipt)
    except FormatDetectionError:
        alt = _first_volume_beside_stub(source) if follow_stub_volumes else None
        if alt is None:
            raise
        info = _detect_format_body(alt, collector, resolved_budget, receipt)
    diagnostics = (
        collector.snapshot()
        if owned_collector
        else collector.snapshot(since=detection_wm)
    )
    return replace(info, diagnostics=diagnostics)


def _first_volume_beside_stub(source: str | Path | BinaryIO) -> Path | None:
    if isinstance(source, ArchiveSource):
        if source.path is None:
            return None
        source = source.path
    if isinstance(source, (str, Path)):
        path = Path(source)
        if path.is_file():
            return first_volume_for_stub(path)
    return None


def _attach_receipt(info: FormatInfo, workspace: PrefixWorkspace) -> FormatInfo:
    return replace(
        info,
        cost_receipt=workspace.receipt,
        unavailable_tiers=workspace.skips,
    )


def _detect_format_body(
    source: str | Path | BinaryIO,
    collector: DiagnosticCollector,
    budget: DetectionBudget,
    receipt: MutableDetectionCostReceipt | None = None,
) -> FormatInfo:
    registry = get_registry()
    magic_entries = registry.magic_entries()
    extension_map = registry.extension_map()
    name = source_name(source)
    ext_match = _match_extension(name, extension_map)
    ext_fmt = ext_match[0] if ext_match is not None else None

    with PrefixWorkspace(source, budget, receipt) as workspace:
        # Record ZIP-tail policy up front so BALANCED leaves an explicit trace that the
        # tier was not enabled (distinct from capability-unavailable on a pipe).
        if budget.max_tail_bytes <= 0:
            workspace.record_skip("zip_tail", TierSkipReason.NOT_ENABLED_BY_POLICY)
        elif DetectionCapability.TAIL not in workspace.capabilities():
            workspace.record_skip("zip_tail", TierSkipReason.CAPABILITY_UNAVAILABLE)

        # Magic signals split by where they live: "near" ones fit in the default window;
        # "far" ones (ISO's CD001 at 32 769) need an extended peek taken on demand.
        near = [e for e in magic_entries if e.offset + len(e.magic) <= DETECTION_LIMIT]
        far = [e for e in magic_entries if e.offset + len(e.magic) > DETECTION_LIMIT]
        near_span = max((e.offset + len(e.magic) for e in near), default=0)
        if near and budget.max_prefix_bytes < near_span:
            # A "near" magic past the budgeted prefix is unsearchable — record it rather
            # than silently incomplete-searching (DETECTION_LIMIT and max_prefix_bytes
            # are independent constants that happen both to be 4 096 today).
            workspace.record_skip("near_magic", TierSkipReason.BUDGET_EXHAUSTED)
        near_needed = min(
            budget.max_prefix_bytes,
            max(DETECTION_LIMIT, near_span),
        )
        data = workspace.peek_prefix(near_needed)
        peek_more = workspace.candidate_view(0)

        # Freeze the probe/far size-gate length from the near peek alone. A later SFX or
        # far growth that hits EOF would make ``remaining_known()`` report a length; that
        # is correct for capability accounting but must not flip content-probe framing
        # gates that deliberately treat a DETECTION_LIMIT-sized non-seekable peek as
        # unknown-length (A-34 stub residual).
        length = workspace.remaining_known()
        if length is None and len(data) < near_needed:
            length = len(data)

        # 1. Exact magic in the default window.
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
                workspace=workspace,
            )
            _warn_on_conflict(
                collector, name, ext_match, info.format, _ConflictEvidence.MAGIC
            )
            return _attach_receipt(info, workspace)

        # 2. Self-extracting archives.
        cue = executable_cue(data)
        if cue is not ExecutableCue.NONE:
            scan_limit = min(SFX_MAX, budget.max_scan_bytes)
            if scan_limit <= 0:
                workspace.record_skip("sfx_scan", TierSkipReason.BUDGET_EXHAUSTED)
            else:
                sfx_info = _scan_for_sfx_payload(
                    registry.sfx_magic_entries(),
                    peek_more,
                    workspace,
                    scan_limit=scan_limit,
                    validators=registry.sfx_hit_validators(),
                    # Shebang is text; ``WEAK`` alone is also unconfirmed MZ/ELF.
                    restrict_to_validated=data.startswith(b"#!"),
                )
                if sfx_info is not None:
                    _warn_on_conflict(
                        collector,
                        name,
                        ext_match,
                        sfx_info.format,
                        _ConflictEvidence.SFX_SCAN,
                    )
                    return _attach_receipt(sfx_info, workspace)

        # 3. Far magic (ISO's CD001 at offset 32 769). A signature that ends past
        # ``max_far_bytes`` cannot match in the clamped window, so it is dropped and the
        # tier is recorded as cut short, the same rule the near tier follows. A source
        # provably too short to hold the signature loses nothing to the clamp.
        reachable_far = [
            e for e in far if e.offset + len(e.magic) <= budget.max_far_bytes
        ]
        if budget.max_far_bytes > 0 and any(
            length is None or length >= e.offset + len(e.magic)
            for e in far
            if e not in reachable_far
        ):
            workspace.record_skip("far_magic", TierSkipReason.BUDGET_EXHAUSTED)
        if reachable_far:
            far_needed = max(e.offset + len(e.magic) for e in reachable_far)
            if length is not None and length < far_needed:
                pass  # size-gated: never pay the peek
            else:
                far_data = workspace.peek_prefix(far_needed)
                # Charge the bytes actually requested even when the window came up short.
                workspace.charge_far(len(far_data))
                far_fmt = _match_magic(far_data, reachable_far)
                if far_fmt is not None:
                    _warn_on_conflict(
                        collector, name, ext_match, far_fmt, _ConflictEvidence.MAGIC
                    )
                    return _attach_receipt(
                        FormatInfo(far_fmt, DetectionConfidence.CERTAIN, "magic"),
                        workspace,
                    )
        elif far and budget.max_far_bytes <= 0:
            workspace.record_skip("far_magic", TierSkipReason.NOT_ENABLED_BY_POLICY)

        # 4. Content probes.
        if cue is not ExecutableCue.STRONG:

            def read_at(offset: int, n: int) -> bytes | None:
                return workspace.read_at(offset, n)

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
                        workspace=workspace,
                    )
                    _warn_on_conflict(
                        collector,
                        name,
                        ext_match,
                        info.format,
                        _ConflictEvidence.CONTENT_PROBE,
                    )
                    return _attach_receipt(info, workspace)

        # 5. Extension-only guess.
        if ext_fmt is not None:
            return _attach_receipt(
                FormatInfo(ext_fmt, DetectionConfidence.GUESS, "extension"),
                workspace,
            )

        raise FormatDetectionError(
            "Could not detect archive format: no magic-byte match and no usable file extension.",
            archive_name=name,
        )
