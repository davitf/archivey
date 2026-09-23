"""BaseArchiveReader ABC and ReadBackend/WriteBackend ABCs."""

from __future__ import annotations

import logging
import sys
import threading
import uuid
import weakref
from abc import ABC, abstractmethod
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    BinaryIO,
    Callable,
    ClassVar,
    Collection,
    ContextManager,
    Iterator,
    Literal,
    Mapping,
    NoReturn,
)

if TYPE_CHECKING:
    from archivey.internal.password import _PasswordCandidates
    from archivey.internal.registry import ContentProbe
    from archivey.measurement import IoStats

from archivey.config import DEFAULT_ARCHIVEY_CONFIG, ArchiveyConfig, ExtractionLimits
from archivey.cost import CostReceipt
from archivey.diagnostics import (
    Diagnostic,
    DiagnosticCode,
    DiagnosticContext,
    DiagnosticDisposition,
    DiagnosticSummary,
    EmptyArchiveContext,
    ExtractionReport,
    MemberListReport,
    SymlinkTargetContext,
    UnconfirmedFormatContext,
)
from archivey.escaping import escape_control_chars, quoted
from archivey.exceptions import (
    ArchiveyError,
    ArchiveyUsageError,
    CorruptionError,
    EncryptionError,
    LinkTargetNotFoundError,
    ReadError,
    TruncatedError,
    UnsupportedFeatureError,
    UnsupportedOperationError,
)
from archivey.internal.arg_checks import (
    check_callable,
    check_config,
    check_extraction_limits,
    describe_value,
)
from archivey.internal.diagnostics_collector import (
    DiagnosticCollector,
    collector_from_config,
)
from archivey.internal.enum_args import (
    coerce_enum,
    coerce_enum_collection,
)
from archivey.internal.extraction_types import (
    AbortOn,
    AbortOnStr,
    ExtractionPolicy,
    ExtractionPolicyStr,
    ExtractionProgress,
    MemberFilter,
    MemberSelectorArg,
    OnError,
    OnErrorStr,
    OverwritePolicy,
    OverwritePolicyStr,
)
from archivey.internal.format_provenance import FormatProvenance
from archivey.internal.listing_limits import ListingLimitTracker
from archivey.internal.logs import backends as logger
from archivey.internal.measurement import (
    ByteCounter,
    SeekCounter,
    measurement_enabled,
)
from archivey.internal.naming import (
    emit_member_name_bidi_control,
    resolve_link_target_name,
)
from archivey.internal.open_site import OpenSite
from archivey.internal.reader_state import LiveStreamReservation, ReaderState
from archivey.internal.selection import normalize_member_selector
from archivey.internal.sfx import HitValidator
from archivey.internal.streams.archive_stream import ArchiveStream, RewindWarning
from archivey.internal.streams.counting import (
    CountingReader,
    OutputCountingStream,
    SeekCountingStream,
)
from archivey.internal.streams.streamtools import (
    is_seekable,
    is_stream,
    source_byte_size,
)
from archivey.internal.windows_reparse import parse_reparse_data
from archivey.reader import ArchiveReader, MemberSelector
from archivey.types import (
    EXTRA_IS_JUNCTION,
    ArchiveFormat,
    ArchiveInfo,
    ArchiveMember,
    HashAlgorithm,
    MagicSignature,
    MemberStreams,
    MemberType,
)


def _apply_last_entry_wins_is_current(members: list[ArchiveMember]) -> None:
    """Stamp is_current for duplicate names (last same-name entry wins).

    Members whose ``name`` appears only once are left unchanged so format-specific
    non-current rows (RAR ``path;N`` file-version history) keep the flag the backend
    already set.
    """
    counts: dict[str, int] = {}
    for member in members:
        counts[member.name] = counts.get(member.name, 0) + 1

    seen: set[str] = set()
    for member in reversed(members):
        if counts[member.name] < 2:
            continue
        if member.name in seen:
            member.is_current = False
        else:
            member.is_current = True
            seen.add(member.name)


@dataclass(frozen=True)
class _Materialized:
    """Published member materialization plus its private lookup index."""

    report: MemberListReport
    by_name_lists: Mapping[str, list[ArchiveMember]]


def reject_start_offset(
    start_offset: int, fmt: ArchiveFormat, archive_name: str | None
) -> None:
    """Refuse a nonzero ``open_read(start_offset=…)`` for a format that cannot honour it.

    Only the self-extracting formats (RAR / 7z / ZIP) are ever handed a nonzero offset,
    because only they can carry an executable stub and only they are scanned for one by
    ``detect_format``. Reaching here with an offset means a caller invented one, and
    reading from byte 0 instead would answer with the wrong bytes rather than say so.
    """
    if start_offset:
        raise UnsupportedFeatureError(
            f"{fmt.display_name} cannot be opened at a nonzero start offset "
            f"({start_offset}): the format carries no self-extracting stub.",
            source_format=fmt,
            archive_name=archive_name,
        )


class ReadBackend(ABC):
    """Stateless factory for creating ArchiveReader instances.

    Each backend declares its magic and extensions **as data**, and every entry names
    the :class:`ArchiveFormat` it implies, so a *multi-format* backend (the single
    ``SingleFileBackend``, the TAR backend over ``TAR`` + its compressed combos) can map
    each signal to the right format. The detector aggregates these across all registered
    backends; backends carry no ``detect()`` method.
    """

    FORMATS: tuple[ArchiveFormat, ...]
    # ".gz" -> ArchiveFormat.GZ
    EXTENSIONS: Mapping[str, ArchiveFormat] = {}
    # Exact magic-byte signals as data (offset, bytes, format), accepted on the byte match.
    MAGIC: tuple[MagicSignature, ...] = ()
    # Magic to hunt for *behind an executable stub*, for the formats that ship as
    # self-extracting archives (RAR / 7z / ZIP). The offset is 0 because these are
    # magic at offset 0 of the *payload*, wherever in the file that starts; the
    # detector searches for them within the shared SFX_MAX window when the leading
    # bytes look executable-shaped, and reports the match position as
    # ``FormatInfo.payload_offset``. Deliberately a separate, narrower table than
    # ``MAGIC``: ZIP declares only its local-file header here, because scanning a stub
    # for the EOCD or spanned markers would claim any file containing those four bytes.
    SFX_MAGIC: tuple[MagicSignature, ...] = ()
    # Format-owned check the SFX scan calls on a candidate-relative view plus
    # known remaining length from that origin (``None`` if the source size is
    # unknown). ``None`` here means "needle match is enough". ZIP, 7z, and RAR
    # all supply one so a stub that merely contains their magic bytes is not
    # claimed: ZIP's local-header sanity check, 7z ``StartHeaderCRC``, RAR 5
    # main-header CRC32 / RAR 4 MAIN.
    # Returns :class:`~archivey.internal.sfx.HitOutcome`; the detector treats
    # anything other than ``VALID`` as "skip and continue".
    # Subclasses that supply a function must wrap it in ``staticmethod`` — a bare
    # function on the class body is a descriptor, and an instance lookup would bind
    # ``self`` as the first argument. The other detection tables (MAGIC, SFX_MAGIC)
    # are inert data and do not have this problem.
    SFX_HIT_VALIDATOR: ClassVar[HitValidator | None] = None
    # Formats this backend reads that have no exact magic and are recognized by a content
    # probe instead: (format, probe) pairs, where the probe inspects a peeked prefix and
    # returns True on a match (Brotli has no signature; zlib's 2-byte header is too weak).
    CONTENT_PROBES: tuple[tuple[ArchiveFormat, ContentProbe], ...] = ()
    # Whether open_archive(streaming=True) may open a NON-SEEKABLE source: true for
    # formats walkable front-to-back (TAR, the single-file codecs), false for formats
    # whose index/metadata is not at the front (ZIP's central directory, ISO's
    # descriptors). Random access (streaming=False) always requires a seekable source —
    # repeatable open()/read() cannot be honored over one forward pass, and the library
    # never implicitly buffers — so that side needs no per-backend flag.
    SUPPORTS_STREAMING_NON_SEEKABLE: bool = False
    # Whether this backend's format has encryption a password could unlock. Checked
    # centrally by open_archive(): a password passed for a format that cannot use one is
    # accepted and recorded as PASSWORD_ARGUMENT_UNUSED (a keyring offered, not an
    # assertion about this archive). ZIP sets this True; the native 7z/RAR readers too.
    SUPPORTS_PASSWORD: bool = False
    # Whether this backend applies a caller-supplied `encoding=` when decoding member
    # names. False for backends that decode names some other way — 7z stores UTF-16LE,
    # RAR decodes in its native parser, the directory and single-file names come from the
    # filesystem — so open_archive() can record ENCODING_ARGUMENT_UNUSED instead of the
    # backend silently `del encoding`-ing it, which is how five of them used to differ
    # from ZIP and TAR with no signal at all.
    USES_ENCODING: bool = False
    # Name of the optional dependency this backend needs (e.g. "pycdlib"); the registry
    # derives availability centrally from whether it imports. ``None`` for core backends.
    OPTIONAL_DEPENDENCY: str | None = None
    # Human-readable install hint surfaced when the dependency is absent
    # (e.g. "pip install archivey[recommended]").
    INSTALL_HINT: str | None = None

    @abstractmethod
    def open_read(
        self,
        source: Path | BinaryIO,
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
    ) -> "BaseArchiveReader":
        """Open ``source`` as ``format`` (the resolved format the registry selected this
        backend for — either detected by ``open_archive`` or supplied by the caller). A
        multi-format backend uses it to pick its concrete codec/variant rather than
        re-inspecting the source.

        ``collector`` is the prospective reader's diagnostic collector (created before
        detection). When omitted, the reader creates one from ``config``.

        ``start_offset`` is where the archive proper begins inside ``source`` — nonzero
        only for a self-extracting archive behind an executable stub, where it carries
        detection's ``FormatInfo.payload_offset``. A backend that accepts it MUST behave
        exactly as if it had been handed a view of ``source`` starting at that offset:
        nothing before it belongs to the archive. (The one visible difference is that a
        backend may still use the original path for an external tool that finds the
        payload itself — ``unrar`` on an SFX file.) Backends whose formats never carry a
        stub reject a nonzero value rather than silently reading from byte 0.
        """
        ...


class WriteBackend(ABC):
    """Stateless factory for creating ArchiveWriter instances."""

    FORMATS: tuple[ArchiveFormat, ...]
    OPTIONAL_DEPENDENCY: str | None = None

    @abstractmethod
    def open_write(
        self,
        dest: Path | BinaryIO,
        compression: object | None,
        password: bytes | None,
        encoding: str | None,
    ) -> "ArchiveWriter": ...


class ArchiveWriter(ABC):
    """Abstract base for archive writers. Defined here as a placeholder."""


class BaseArchiveReader(ArchiveReader):
    """Internal helper base for all format readers — the backend contract lives here.

    Implements the public :class:`ArchiveReader` surface (iteration, lookup, link
    following, lifecycle). Format backends extend **this**, not ``ArchiveReader``.

    Implementing a backend
    ----------------------
    **MUST implement** (abstract):

    - ``_iter_members()``       — yield every :class:`ArchiveMember` once, in archive
      order.
    - ``_open_member(member)``  — return a ``FILE`` member's data stream, wrapped via
      ``_wrap_member_stream`` (every member handle the library hands out is an
      ``ArchiveStream``: uniform error translation/stamping, the ``size``
      advertisement, and room to grow shared handle features).
    - ``_get_archive_info()``   — return the :class:`ArchiveInfo` (format, solidity,
      cost, …).
    - ``_close_archive()``      — release resources (called exactly once, via
      ``close()``).

    **MUST set** when they differ from the defaults (both default ``True``):

    - ``_MEMBER_LIST_UPFRONT``    — does the backend have a true upfront index (central
      directory, 7z header, filesystem listing) that yields the full member list
      *without scanning*? This is the predicate behind :meth:`members_report_if_available`
      (it returns a report when ``True``, else ``None``). It does **not** gate the
      access-mode-enforced methods — those key off the ``streaming`` flag alone.
    - ``_SUPPORTS_RANDOM_ACCESS`` — can an arbitrary member be opened out of order?
      When ``False``, ``open``/``read`` raise ``UnsupportedOperationError``; sequential
      access via ``stream_members`` still works. (The open-time fail-fast for a
      non-seekable source under ``streaming=False`` — which also consults this — lands
      with format detection in Phase 3.)

    Access-mode enforcement (independent of the flags above): a ``streaming=True`` reader
    is forward-only, so ``members``/``get``/``open``/``read`` all raise
    ``UnsupportedOperationError`` — uniformly, not per-backend. Only a single pass of
    ``__iter__``/``stream_members``/``extract_all`` is allowed; ``scan_members()`` may
    finish or return that pass. ``members_report_if_available()`` is a scan-free,
    index-only peek. ``member in reader`` is identity-based and scan-free, so it works in
    either mode; there is no ``__len__``/``__getitem__`` (name lookup is ``get()``).

    **MAY override**:

    - ``_iter_with_data()`` — see its own docstring. The default is correct for
      random-access / indexed backends only; **streaming / solid backends MUST override
      it** (correctness, not just efficiency). Streaming backends that override
      ``_iter_with_data()`` **MUST** route their forward metadata pass through the shared
      instance-held progressive pass (``_begin_forward_pass``) so
      ``scan_members()`` can finish an interrupted pass and the resolved cache is
      finalized on completion. Native 7z/RAR readers will need the same contract.

    Everything else here (``_get_members_registered``, ``_resolve_link``,
    ``_open_with_link_follow``, ``_stamp_error_context``) is internal plumbing and is not
    an extension point.
    """

    # Can an arbitrary member be opened out of order? When False, open()/read() raise
    # UnsupportedOperationError and callers must use stream_members() instead.
    _SUPPORTS_RANDOM_ACCESS: bool = True
    # Is the full member list available without reading member data (e.g. a central
    # directory)? Drives members_report_if_available(); does not gate the streaming methods.
    _MEMBER_LIST_UPFRONT: bool = True

    def __init__(
        self,
        format: ArchiveFormat,
        streaming: bool,
        archive_name: str | None,
        config: ArchiveyConfig | None = None,
        collector: DiagnosticCollector | None = None,
        member_streams: MemberStreams = MemberStreams(0),
        open_site: OpenSite | None = None,
    ) -> None:
        self._format = format
        self._streaming = streaming
        self._archive_name = archive_name
        self._config = config if config is not None else DEFAULT_ARCHIVEY_CONFIG
        self._diagnostics_collector = (
            collector if collector is not None else collector_from_config(self._config)
        )
        self._member_streams = member_streams
        self._open_site = open_site
        self._state = ReaderState(member_streams=member_streams, open_site=open_site)
        # A random, opaque identity token (not id(self), which the allocator can reuse
        # after a reader is garbage-collected — a member of a dead reader must never
        # pass another reader's identity check; and not a plain counter, whose small
        # sequential values invite confusion with member_id or hardcoding).
        self._archive_id = uuid.uuid4().hex
        # Public member streams still open, so close() can close them (stdlib parity:
        # ZipFile.close()/TarFile.close() invalidate member streams too). Weak, because
        # a strong registry here would be one more thing keeping a dropped stream alive.
        # Keyed by an open counter rather than being a set, because close order is part
        # of the contract and WeakSet iteration is unordered; dict iteration is
        # insertion-ordered, and dead entries drop out on their own.
        self._public_streams: weakref.WeakValueDictionary[int, ArchiveStream] = (
            weakref.WeakValueDictionary()
        )
        self._public_stream_seq = 0
        # The archive's source, recorded by backends that have one (path or stream);
        # backs the generalized compressed_source_size property below.
        self._source: Path | BinaryIO | None = None
        # A counter wrapping the raw compressed source, set by a backend that decompresses
        # a stream source; backs compressed_bytes_consumed (the live decompression-ratio
        # denominator for a source whose total size is not cheaply knowable).
        self._compressed_input_counter: CountingReader | None = None
        # Opt-in benchmark counters (see archivey.internal.measurement). When measurement
        # is off these stay None and wrap helpers are identity — zero hot-path overhead.
        self._measure = measurement_enabled()
        self._decompressed_counter: ByteCounter | None = (
            ByteCounter() if self._measure else None
        )
        self._seek_counter: SeekCounter | None = (
            SeekCounter() if self._measure else None
        )
        self._materialized: _Materialized | None = None
        # Member ids whose backend-independent presentation checks have already run, so a
        # second listing pass over fresh ArchiveMember objects for the same members does
        # not re-emit their diagnostics (see ``_register_member``).
        self._presentation_checked: set[int] = set()
        # Diagnostics already reported for a member while it was being typed, keyed by
        # the member's position in the listing and the code, so a backend that types the
        # same member again re-attaches the first report instead of emitting a second
        # (see ``_report_member_diagnostic``). The value is None for a report that never
        # attached, which still has to be remembered: under an IGNORE disposition, or
        # once the retention budget is spent, nothing attaches, and forgetting it there
        # would count the member twice in exactly the configuration where ``counts`` is
        # the only channel left.
        self._member_reports: dict[
            tuple[object, DiagnosticCode], Diagnostic | None
        ] = {}
        # Set by open_archive on the reader it is about to return; read only by the
        # empty-listing check in _publish_materialized. None for a reader built directly.
        self._format_provenance: FormatProvenance | None = None
        # PROBE_FORMAT_UNCONFIRMED is one provenance fact per reader, not per raised
        # exception — retries must not multiply the diagnostic (or burn retention slots).
        self._probe_unconfirmed_emitted: bool = False
        self._listing_tracker = ListingLimitTracker(self._config.listing_limits)
        self._forward_pass_started: bool = False
        # When true, progressive registration enforces ListingLimits (scan_members).
        # stream_members leaves this false so iteration stays the unguarded escape hatch.
        self._progressive_enforce_listing_limits: bool = False
        self._progressive_gen: Iterator[ArchiveMember] | None = None
        self._pass_scanned: list[ArchiveMember] = []
        self._pass_by_name_lists: dict[str, list[ArchiveMember]] = {}
        self._closed = False
        # A backend that shares one underlying handle across member streams (zipfile fp,
        # tarfile fileobj, pycdlib _cdfp) sets this to a lock under CONCURRENT (and TAR
        # also under streaming). Backends acquire it via ``_handle_guard()`` so the "lock
        # when present, no-op otherwise" branch lives in one place. ``None`` = no shared
        # handle to serialize (default readers, path-per-open backends).
        self._handle_lock: threading.Lock | None = None

    def _handle_guard(self) -> ContextManager[object]:
        """Hold the backend's shared-handle lock if one is set, else a no-op context.

        Collapses the repeated ``if self._handle_lock is not None: with lock: … else: …``
        branch that every shared-handle backend otherwise copy-pastes (and can forget to
        keep in sync on one arm).
        """
        return self._handle_lock if self._handle_lock is not None else nullcontext()

    def _raise_translated(
        self,
        exc: Exception,
        member_name: str | None = None,
        *,
        stamp_encryption: bool = True,
    ) -> NoReturn:
        """Translate ``exc`` via ``_translate_exception``, stamp context, and raise.

        The single backend-side error boundary (the out-of-stream counterpart of
        ``ArchiveStream._fail``): an already-typed ``ArchiveyError`` is stamped and
        re-raised as-is; a raw exception the translator recognizes is stamped and raised
        chained to the original; an unrecognized exception propagates unchanged (the
        catch-all-free rule in CONTRIBUTING). ``stamp_encryption=False`` skips member
        stamping for ``EncryptionError`` (ZIP's password errors carry their own message
        and must not be reattributed).
        """
        if isinstance(exc, ArchiveyError):
            self._stamp_error_context(exc, member_name)
            raise exc
        translated = self._translate_exception(exc)
        if translated is None:
            raise exc
        if stamp_encryption or not isinstance(translated, EncryptionError):
            self._stamp_error_context(translated, member_name)
        raise translated from exc

    def _translated_errors(
        self,
        member_name: str | None = None,
        *,
        stamp_encryption: bool = True,
    ) -> ContextManager[None]:
        """Context manager routing any exception from the body through ``_raise_translated``.

        Backends wrap every direct call into their underlying library with this instead
        of hand-rolling the translate/stamp/raise tail per site — the pattern that
        repeatedly drifted (one site catching a narrower tuple than its siblings).
        Take the boundary OUTSIDE ``_handle_guard()`` so translation and stamping never
        run while a shared-handle lock is held. ``BaseException`` (KeyboardInterrupt,
        GeneratorExit) passes through untouched.
        """
        return _TranslatedErrorBoundary(self, member_name, stamp_encryption)

    @property
    def member_streams(self) -> MemberStreams:
        """Declared member-stream capabilities for this reader."""
        return self._member_streams

    def _seek_declared(self) -> bool:
        return self._state.seekable

    def _register_public_stream(
        self,
        stream: ArchiveStream,
        token: LiveStreamReservation | None = None,
    ) -> ArchiveStream:
        """Admit ``stream`` under the live-stream gate and attach lease release on close.

        ``token`` is a reservation already taken (eager ``open()``); bind is
        infallible. Without ``token`` this acquires a new slot (lazy
        ``stream_members`` wrappers, which have not spawned yet).
        """
        if token is None:
            self._state.acquire_live_stream(stream)
        else:
            self._state.bind_live_stream(token, stream)
        self._public_stream_seq += 1
        self._public_streams[self._public_stream_seq] = stream

        # Capture the identity token, not the stream. ``weakref.finalize`` keeps its
        # callback alive until it fires, so a callback that strongly referenced the
        # stream would keep the stream alive too — and the finalizer could then never
        # fire. That is exactly what happened: an unclosed stream held its file
        # descriptor until process exit, immune to gc.collect().
        stream_id = id(stream)

        def _on_close() -> None:
            if self._state.release_live_stream_id(stream_id):
                self._maybe_teardown()

        stream._on_close = _on_close
        stream._attach_finalizer()
        return stream

    def _internal_member_opens(self) -> _InternalMemberOpens:
        """Context manager: library-internal opens are exempt from the live-stream gate."""
        return _InternalMemberOpens(self._state)

    def _maybe_teardown(self, pending: Exception | None = None) -> None:
        """Run archive teardown outside lifecycle state once the last lease drops.

        ``pending`` is a stream-close failure to combine with a teardown failure into an
        ``ExceptionGroup`` (D9). Teardown is never retried; the lifecycle is marked
        complete even when ``_close_archive`` fails.
        """
        if not self._state.claim_teardown():
            if pending is not None:
                raise pending
            return
        teardown_exc: Exception | None = None
        try:
            self._close_archive()
        except Exception as exc:  # noqa: BLE001 - combine with pending stream-close failure
            teardown_exc = exc
            self._state.complete_teardown()
        else:
            self._state.complete_teardown()
        if pending is not None and teardown_exc is not None:
            raise ExceptionGroup(
                "member-stream close and archive teardown both failed",
                [pending, teardown_exc],
            )
        if pending is not None:
            raise pending
        if teardown_exc is not None:
            raise teardown_exc

    @abstractmethod
    def _iter_members(self) -> Iterator[ArchiveMember]:
        """Yield every member once, in archive order.

        **Order stability is load-bearing:** repeated calls MUST yield the same members
        in the same order. ``member_id`` is a stamp of enumeration position, and
        selection/extraction match members from *separate* enumerations by
        ``(archive_id, member_id)`` (``members_report_if_available()`` re-enumerates for
        some backends while ``stream_members()`` serves the materialized cache) — an
        order that varies between calls would silently select the wrong members.
        """
        ...

    def _drive_pass_streams(
        self,
        members: Iterator[ArchiveMember],
        *,
        open_member: Callable[[ArchiveMember], ArchiveStream | None],
        close_previous: bool = True,
        cleanup: Callable[[], None] | None = None,
        after_members: Callable[[], None] | None = None,
    ) -> Iterator[tuple[ArchiveMember, ArchiveStream | None]]:
        """Shared close-previous / open / yield / finally driver for ``_iter_with_data``.

        Backends supply an ``open_member`` hook (return ``None`` for non-file members)
        and an optional ``cleanup`` for pass-scoped resources (solid block, ``unrar``
        pipe). ``close_previous=False`` is for TAR streaming, where tarfile invalidates
        the prior ``extractfile`` handle on advance.

        The driver **always** closes the last still-open stream in its ``finally``
        before running ``cleanup`` — never tear down a solid block / pipe under a live
        member wrapper. Double-close with ``stream_members``'s ``finally`` is fine:
        ``ArchiveStream.close`` is idempotent.
        """
        previous: ArchiveStream | None = None
        try:
            for member in members:
                if close_previous and previous is not None:
                    previous.close()
                    previous = None
                stream = open_member(member)
                if stream is not None:
                    previous = stream
                yield member, stream
            if after_members is not None:
                after_members()
        finally:
            if previous is not None:
                previous.close()
            if cleanup is not None:
                cleanup()

    def _iter_with_data(self) -> Iterator[tuple[ArchiveMember, ArchiveStream | None]]:
        """Yield (member, stream) pairs in archive order; backs ``stream_members``.

        This default is for **random-access / fully-indexed** backends only (ZIP,
        directory): it calls ``_get_members_registered()``, which eagerly drains
        ``_iter_members()`` and builds the name map *before* yielding anything.

        Streaming / forward-only / solid backends **must override** this — it is a
        correctness requirement, not just an optimization. A non-seekable TAR or a solid
        7z/RAR cannot enumerate every member before reading data, so the override must
        produce ``(member, stream)`` pairs progressively from a single forward pass and
        must **not** call ``_get_members_registered()``. The yielded stream is only valid
        until the iterator advances (see the ``stream_members`` contract in
        ``archive-reading``); for non-file members it is ``None``.

        When ``streaming=True`` and the backend does not override, this default pulls
        from the shared instance-held progressive pass and opens each file member on
        demand.

        The yielded streams are **lazy**: the member's data is opened on the first read,
        not at yield time. A consumer that skips a member (a ``stream_members`` selector,
        a filtered extraction) therefore pays nothing for it — no seek to its data, no
        decompressor setup — and an open-time error (e.g. a wrong password) surfaces only
        if the member is actually read, not merely iterated past.
        """
        report: MemberListReport | None = None
        members = self._begin_forward_pass() if self._streaming else None
        if members is None:
            report = self._materialize_members(enforce_listing_limits=False).report
            members = iter(report.members)

        def _open(member: ArchiveMember) -> ArchiveStream | None:
            if member.is_file:
                return self._lazy_member_stream(member)
            return None

        def _raise_report_error() -> None:
            if report is not None and report.error is not None:
                raise report.error

        yield from self._drive_pass_streams(
            members,
            open_member=_open,
            close_previous=True,
            after_members=_raise_report_error,
        )

    def _lazy_member_stream(self, member: ArchiveMember) -> ArchiveStream:
        """A stream over ``member``'s data that defers ``_open_member`` to the first read.

        Closing it before any read never opens the member at all. ``_open_member``
        already returns an ``ArchiveStream``; nesting is collapsed inside
        :meth:`ArchiveStream._ensure_open` so the public handle is a **single**
        wrapper. Deferral does not change what a failed open raises — only when.
        """
        return self._register_public_stream(
            self._wrap_member_stream(
                None,
                member.name,
                open_fn=lambda: self._open_member(member),
                size=member.size,
                # ``_open_member`` already applied output tracking inside its wrap.
                track_output=False,
                seekable=self._seek_declared(),
            )
        )

    @abstractmethod
    def _open_member(self, member: ArchiveMember) -> ArchiveStream:
        """Return a data stream for ``member`` (no link following).

        Backends wrap the raw handle via ``_wrap_member_stream`` so every member
        stream the library hands out is an ``ArchiveStream``.

        **Reentrancy invariant (random-access backends).** For a backend that advertises
        independent member open (``streaming=False``, byte-range / independent access —
        ZIP, single-file, and the future native 7z/RAR readers), this method MUST be a
        function of ``(member, shared source)`` only:

        - it MUST NOT keep unsynchronized per-open scratch on ``self`` that another
          concurrent open can overwrite;
        - synchronized shared bookkeeping (leases, password caches, handle locks) is
          permitted;
        - any archivey-owned byte-range access MUST go through a
          :class:`~archivey.internal.streams.streamtools.SharedSource` view (see
          *Multiple concurrently-open member streams* in ``archive-reading``).

        Immutable, already-materialized state (the member list / name index) MAY be read
        read-only. Backends whose member addressing is owned by an external library that
        already coordinates the shared handle (ISO via ``pycdlib``, ZIP via stdlib
        ``zipfile``) are not required to route through an archivey ``SharedSource`` view,
        but archivey-owned reader state still MUST NOT hold unsynchronized per-open scratch.

        Concurrent ``open`` is supported when the reader declared concurrency
        (``MemberStreams.CONCURRENT``, from ``open_archive(concurrent_members=True)``):
        first-touch materialization is coordinated
        (wait/share), and after the snapshot is published workers may fan out. Forward-only
        / streaming passes remain single-owner. See ``dev-docs/investigations/parallel-reader.md``.
        """
        ...

    def _translate_exception(self, exc: Exception) -> ArchiveyError | None:
        """Map a raw exception (from a codec/library while reading a member) to an
        ``ArchiveyError`` subclass, or return ``None`` to let it propagate unchanged.

        This is the backend's per-library translator hook (see ``error-handling`` and
        CONTRIBUTING). The default translates nothing; backends override it to map their
        library's known exceptions. It MUST NOT contain a catch-all that converts any
        ``Exception`` — an unrecognized error returns ``None`` so it surfaces and can be
        mapped deliberately.
        """
        return None

    def _track_decompressed(self, stream: BinaryIO) -> BinaryIO:
        """Wrap ``stream`` to count decoded/output bytes when measurement is on.

        Backends call this on the stream that *is* the decompressor (or stored-data)
        output: member streams for ZIP/TAR/single-file, folder/solid-block streams for
        solid 7z/RAR. Identity when measurement is off.
        """
        counter = self._decompressed_counter
        if counter is None:
            return stream
        return OutputCountingStream(stream, counter)

    def _seek_handle_wrapper(self) -> Callable[[BinaryIO], BinaryIO] | None:
        """Return a ``SharedSource(wrap_handle=…)`` callback when measurement is on."""
        counter = self._seek_counter
        if counter is None:
            return None
        return lambda handle: SeekCountingStream(handle, counter)

    def _track_source_seeks(self, source: Path | BinaryIO) -> Path | BinaryIO:
        """Wrap a seekable BinaryIO source to count seeks; leave paths unchanged.

        Path sources are instrumented via :meth:`_seek_handle_wrapper` on
        ``SharedSource``, or by opening the path and wrapping before a library that
        takes a file object (ZIP). Identity when measurement is off.
        """
        counter = self._seek_counter
        if counter is None or not is_stream(source):
            return source
        return SeekCountingStream(source, counter)

    @property
    def bytes_decompressed(self) -> int:
        """Total decoded/output bytes counted while measurement was enabled, else 0.

        Distinct from :attr:`compressed_bytes_consumed` (compressed *input* pressure for
        the live ratio guard). Internal / harness-facing — not on the public ABC.
        """
        c = self._decompressed_counter
        return c.total if c is not None else 0

    @property
    def source_seek_count(self) -> int:
        """Number of ``seek`` calls on the instrumented archive source, else 0."""
        c = self._seek_counter
        return c.count if c is not None else 0

    def _wrap_member_stream(
        self,
        inner: BinaryIO | None,
        member_name: str | None,
        *,
        open_fn: Callable[[], BinaryIO] | None = None,
        lazy: bool = False,
        size: int | None = None,
        track_output: bool = True,
        seekable: bool | None = None,
        expected_hashes: Mapping[HashAlgorithm, bytes] | None = None,
        expected_size: int | None = None,
        digest_transforms: Mapping[HashAlgorithm, Callable[[bytes], bytes]]
        | None = None,
        verify_member: ArchiveMember | None = None,
        rewind_warning: RewindWarning | None = None,
    ) -> ArchiveStream:
        """Wrap a raw member stream so read/seek errors route through the backend's
        translator and are stamped with format/archive/member context.

        Backends return ``_wrap_member_stream(raw, member.name, size=member.size)`` from
        ``_open_member`` so a decode error surfaces as a stamped ``ArchiveyError`` rather
        than a raw codec exception, and so the handle advertises its decompressed length
        (the fsspec-style ``size``) for cheap nested-archive source sizing.

        Pass ``open_fn`` (and ``inner=None``) for a lazy open — one ``ArchiveStream``,
        opened on first read. If ``open_fn`` returns an ``ArchiveStream`` (e.g. from
        ``_open_member``), :meth:`ArchiveStream._ensure_open` collapses the nesting
        automatically so callers never see a double wrapper.

        Seekability is gated by ``MemberStreams.SEEKABLE``: without it the wrapper reports
        non-seekable even when ``inner`` could seek. Pass ``seekable=`` to override the
        default (eager: declared ∧ ``is_seekable(inner)``; lazy: declared).

        When measurement is on and ``track_output`` is true (the default), decoded bytes
        delivered through this handle are added to :attr:`bytes_decompressed`. Solid
        backends that already count at the folder/block decode layer pass
        ``track_output=False`` to avoid double-counting. When ``open_fn`` may return an
        ``ArchiveStream``, pass ``track_output=False`` (counting belongs on that inner
        wrap); collapse happens in ``_ensure_open``.

        Pass ``expected_hashes`` / ``expected_size`` to fuse container digest and
        length verification into the returned ``ArchiveStream`` (no separate
        ``VerifyingStream`` layer). A never-opened lazy handle skips verify on
        close, so solid unread members stay cheap.
        """
        if (inner is None) == (open_fn is None):
            raise TypeError("exactly one of inner or open_fn is required")

        if open_fn is not None:
            if track_output:

                def tracked_open(
                    raw_open: Callable[[], BinaryIO] = open_fn,
                ) -> BinaryIO:
                    # open_fn should return a bytes stream when tracking here; nested
                    # ArchiveStream collapse is handled in ArchiveStream._ensure_open
                    # (use track_output=False when open_fn returns ArchiveStream).
                    return self._track_decompressed(raw_open())

                use_open: Callable[[], BinaryIO] = tracked_open
            else:
                use_open = open_fn

            return ArchiveStream(
                use_open,
                translate=self._translate_exception,
                stamp=lambda exc: self._stamp_error_context(exc, member_name),
                lazy=True,
                seekable=(self._seek_declared() if seekable is None else seekable),
                size=size,
                collector=self._diagnostics_collector,
                expected_hashes=expected_hashes,
                expected_size=expected_size,
                digest_transforms=digest_transforms,
                verify_member=verify_member,
                archive_name=self._archive_name,
                rewind_warning=rewind_warning,
            )

        assert inner is not None
        if track_output:
            inner = self._track_decompressed(inner)
        return ArchiveStream(
            lambda: inner,
            translate=self._translate_exception,
            stamp=lambda exc: self._stamp_error_context(exc, member_name),
            lazy=lazy,
            seekable=(
                self._seek_declared() and is_seekable(inner)
                if seekable is None
                else seekable
            ),
            size=size,
            collector=self._diagnostics_collector,
            expected_hashes=expected_hashes,
            expected_size=expected_size,
            digest_transforms=digest_transforms,
            verify_member=verify_member,
            archive_name=self._archive_name,
            rewind_warning=rewind_warning,
        )

    @abstractmethod
    def _get_archive_info(self) -> ArchiveInfo: ...

    @abstractmethod
    def _close_archive(self) -> None: ...

    def _emit_empty_listing_diagnostics(self) -> None:
        """Report a clean, zero-member listing, and an unconfirmed format behind it.

        Not an error: a legitimately empty tar is 10240 zero bytes, byte-identical to a
        zero-filled garbage file of the same length, so no predicate over the bytes can
        separate them and any "zero members is a problem" rule would reject a file
        ``tar(1)`` itself produces. Saying "this archive is empty" is the true thing;
        "this file is probably garbage" would be a guess.

        The format codes narrow that: they fire only when the format was never confirmed
        against the bytes. Both run only on an empty listing, so a normal archive pays
        nothing.
        """
        format_name = self._format.display_name
        self._diagnostics_collector.emit(
            code=DiagnosticCode.EMPTY_ARCHIVE,
            message=f"Archive listed no members ({format_name})",
            context=EmptyArchiveContext(
                archive_name=self._archive_name, format=format_name
            ),
        )

        provenance = self._format_provenance
        if provenance is None:
            return

        if provenance.chosen_by == "extension":
            # Detection fell through to the filename because magic, the content probes
            # and far magic all declined — the same answer detect_format gives when it
            # refuses the bytes. No rescan is needed to know the format is unconfirmed.
            self._emit_unconfirmed_format("extension", None)
            return

        if provenance.chosen_by != "argument" or provenance.source is None:
            # "content"/"directory": the bytes agreed. A stream argument records no
            # source, rather than seeking a live source back to its origin.
            return

        from archivey.exceptions import ArchiveyError as _ArchiveyError
        from archivey.internal.detection import detect_format

        try:
            detected = detect_format(provenance.source).format
        except (_ArchiveyError, OSError, ValueError):
            # Detection refuses these bytes outright — or cannot read them at all.
            #
            # OSError is not hypothetical: this is the only place that reopens the
            # archive **by name**, and the reader is otherwise immune to the path
            # changing under it because it holds an open handle. Without this arm a
            # deleted/unreadable path turned a *successful* empty listing into
            # FileNotFoundError, so a diagnostic decided whether listing worked. A
            # probe that exists to add an advisory must never do that.
            detected = None
        if detected is self._format:
            return
        self._emit_unconfirmed_format(
            "argument", detected.display_name if detected is not None else None
        )

    def _emit_unconfirmed_format(
        self,
        chosen_by: Literal["argument", "extension", "content_probe"],
        detected_format: str | None,
        *,
        escalate_as: type[BaseException] | None = None,
        escalate_message: str | None = None,
        escalate_kwargs: dict[str, object] | None = None,
    ) -> None:
        if chosen_by == "argument":
            code = DiagnosticCode.EXPLICIT_FORMAT_LISTED_EMPTY
        elif chosen_by == "extension":
            code = DiagnosticCode.EXTENSION_FORMAT_UNCONFIRMED
        else:
            code = DiagnosticCode.PROBE_FORMAT_UNCONFIRMED
        format_name = self._format.display_name
        if chosen_by == "content_probe":
            message = (
                f"Decode failed for {format_name}, which was identified only by a "
                f"content probe; the source may not be that format"
            )
        else:
            detected_text = detected_format or "nothing (detection refuses these bytes)"
            message = (
                f"Listed no members as {format_name}, which was chosen by "
                f"{chosen_by} and not confirmed by the archive's bytes; "
                f"content detection reports {detected_text}"
            )
        self._diagnostics_collector.emit(
            code=code,
            message=message,
            context=UnconfirmedFormatContext(
                archive_name=self._archive_name,
                format=format_name,
                chosen_by=chosen_by,
                detected_format=detected_format,
            ),
            escalate_as=escalate_as,
            escalate_message=escalate_message,
            escalate_kwargs=escalate_kwargs,
        )

    def _mark_format_unconfirmed(self, exc: ArchiveyError) -> None:
        """Stamp a probe-only decode failure and emit the matching diagnostic."""
        if not exc.format_unconfirmed:
            format_name = (exc.source_format or self._format).display_name
            detail = exc.raw_message
            new_msg = (
                f"Format identification was unconfirmed (content probe only); "
                f"the source may not be {format_name}. Decode failed: {detail}. "
                f"Partial output may already have been produced"
            )
            escaped = escape_control_chars(new_msg)
            exc.raw_message = new_msg
            exc.message = escaped
            exc.args = (escaped,)
            exc.format_unconfirmed = True

        if self._probe_unconfirmed_emitted:
            return

        # Under pedantic() (default=RAISE), a bare emit would raise DiagnosticRaisedError
        # mid-raise and destroy the typed TruncatedError/CorruptionError. escalate_as
        # keeps that type when RAISE fires; under COLLECT we leave escalate_as unset so
        # the already-stamped ``exc`` is re-raised by the caller.
        escalate_as: type[BaseException] | None = None
        escalate_kwargs: dict[str, object] | None = None
        escalate_message: str | None = None
        disposition = self._diagnostics_collector.policy.resolve(
            DiagnosticCode.PROBE_FORMAT_UNCONFIRMED
        )
        if disposition is DiagnosticDisposition.RAISE:
            escalate_as = type(exc)
            escalate_message = exc.raw_message
            escalate_kwargs = {
                "source_format": exc.source_format,
                "archive_name": exc.archive_name,
                "member_name": exc.member_name,
                "link_target": exc.link_target,
                "format_unconfirmed": True,
            }

        # Set before emitting: under RAISE the emit does not return, and a flag set
        # afterwards would never be reached — every retried read would record the
        # diagnostic again. `diagnostics` splits those concerns ("deduplication is a
        # presentation concern; escalation is not"), and normally a deduplicated code
        # still escalates on later occurrences via `escalate_only`. This code needs no
        # such re-escalation: the caller re-raises the stamped `exc` — same type, same
        # `format_unconfirmed=True` — on every occurrence, so a caller who asked to be
        # stopped is stopped whether or not the diagnostic fires a second time.
        self._probe_unconfirmed_emitted = True
        self._emit_unconfirmed_format(
            "content_probe",
            self._format.display_name,
            escalate_as=escalate_as,
            escalate_message=escalate_message,
            escalate_kwargs=escalate_kwargs,
        )

    def _publish_materialized(
        self,
        members: list[ArchiveMember],
        by_name_lists: dict[str, list[ArchiveMember]],
        *,
        error: ArchiveyError | None,
    ) -> _Materialized:
        if error is not None:
            self._stamp_error_context(error)
        elif not members:
            # Emitted before the snapshot below so it appears on the published report.
            self._emit_empty_listing_diagnostics()
        holder = _Materialized(
            report=MemberListReport(
                members=tuple(members),
                error=error,
                diagnostics=self._diagnostics_collector.snapshot(),
            ),
            by_name_lists=by_name_lists,
        )
        self._materialized = holder
        return holder

    def _finalize_links(
        self,
        members: list[ArchiveMember],
        by_name_lists: dict[str, list[ArchiveMember]],
        *,
        error: ArchiveyError | None = None,
        child_scope: bool = False,
        is_current_first: bool = False,
    ) -> None:
        """Resolve hardlink/symlink targets with one double-fault policy.

        When ``error is not None`` (incomplete listing / terminal pass damage), a
        secondary ``CorruptionError`` / ``TruncatedError`` during link-target reads is
        swallowed so the recovered prefix stays publishable. When ``error is None``
        (clean EOF / complete listing), secondary faults propagate.

        Eager materialization stamps ``is_current`` *before* link resolve and uses a
        child scope + internal-open exemption for link-data reads. Progressive finalize
        stamps ``is_current`` *after* link resolve and does not open a child scope —
        preserve those orderings unless a failing test forces convergence.
        """
        if is_current_first:
            _apply_last_entry_wins_is_current(members)
            if not any(member.is_link for member in members):
                return

        def _resolve() -> None:
            for member in members:
                if member.is_link:
                    self._resolve_link_target(member)
            for member in members:
                if member.is_link and member.link_target:
                    self._resolve_link(member, by_name_lists)

        try:
            if child_scope:
                # Also wraps ``_internal_member_opens()`` (live-stream gate exemption) —
                # the two always travel together for eager link-data reads.
                # Link-data reads are a private child scope under an active root when one
                # exists; otherwise they only need the live-stream gate exemption.
                root = self._state.current_root()
                child = (
                    self._state.enter_child(root, "link_reads")
                    if root is not None
                    else None
                )
                try:
                    with self._internal_member_opens():
                        _resolve()
                finally:
                    if child is not None:
                        self._state.release_child(child)
            else:
                _resolve()
        except (CorruptionError, TruncatedError):
            if error is None:
                raise

        if not is_current_first:
            _apply_last_entry_wins_is_current(members)

    def _materialize_members(
        self, *, enforce_listing_limits: bool = True
    ) -> _Materialized:
        """Materialize members into one report plus name index.

        ``CorruptionError`` / ``TruncatedError`` during listing publish an incomplete
        report. Resource limits, interrupts, and all other failures leave the reader
        unmaterialized and propagate unchanged.
        """
        if self._materialized is not None:
            if enforce_listing_limits:
                self._listing_tracker.assert_within_limits()
            return self._materialized

        if not self._state.begin_materialization():
            # Another thread published while we waited (or cache was already ready).
            assert self._materialized is not None
            if enforce_listing_limits:
                self._listing_tracker.assert_within_limits()
            return self._materialized

        members: list[ArchiveMember] = []
        by_name_lists: dict[str, list[ArchiveMember]] = {}
        try:
            self._listing_tracker.reset()
            self._account_archive_comment(enforce=enforce_listing_limits)
            try:
                for idx, member in enumerate(self._iter_members()):
                    self._register_member(
                        idx, member, enforce_listing_limits=enforce_listing_limits
                    )
                    self._index_member_name(by_name_lists, member)
                    members.append(member)
            except (CorruptionError, TruncatedError) as exc:
                # Prefer the original listing error on the report; leave unresolved
                # links as-is when a secondary link-target fault is swallowed.
                self._finalize_links(
                    members,
                    by_name_lists,
                    error=exc,
                    child_scope=True,
                    is_current_first=True,
                )
                holder = self._publish_materialized(
                    members,
                    by_name_lists,
                    error=exc,
                )
                self._state.complete_materialization()
                return holder

            self._finalize_links(
                members,
                by_name_lists,
                error=None,
                child_scope=True,
                is_current_first=True,
            )
            holder = self._publish_materialized(members, by_name_lists, error=None)
            self._state.complete_materialization()
            return holder
        except BaseException:
            # MUST be BaseException, not Exception: a KeyboardInterrupt/MemoryError/
            # SystemExit raised mid-scan (7z folder decode, TAR header walk, a bomb) would
            # otherwise leave cache_state stuck at MATERIALIZING forever — a non-concurrent
            # reader then raises a misleading "materialization already in progress", and a
            # CONCURRENT waiter blocks on the CV with no owner left to notify it. We reset
            # the election state and re-raise so the interrupt still propagates unchanged.
            # (mark_reader_closed's drain path handles BaseException the same way.)
            self._materialized = None
            self._listing_tracker.reset()
            self._state.fail_materialization()
            raise

    def _get_members_registered(
        self, *, enforce_listing_limits: bool = True
    ) -> list[ArchiveMember]:
        """Return the complete member list, raising on incomplete reports."""
        report = self._materialize_members(
            enforce_listing_limits=enforce_listing_limits
        ).report
        if report.error is not None:
            raise report.error
        return list(report.members)

    def _get_members_index_only(self) -> list[ArchiveMember]:
        """Index-only member list: stamp ids, no link resolution, no member-data reads."""
        self._listing_tracker.reset()
        self._account_archive_comment(enforce=True)
        members = list(self._iter_members())
        _apply_last_entry_wins_is_current(members)
        for idx, member in enumerate(members):
            self._register_member(idx, member, enforce_listing_limits=True)
        return members

    def _account_archive_comment(self, *, enforce: bool) -> None:
        comment = self._get_archive_info().comment
        self._listing_tracker.account_archive_comment(comment, enforce=enforce)

    def _register_member(
        self,
        idx: int,
        member: ArchiveMember,
        *,
        enforce_listing_limits: bool = False,
    ) -> None:
        """Assign identity and run backend-independent presentation checks once."""
        if member._member_id is not None:
            # Already stamped (e.g. by ``_get_members_index_only``). Still re-account
            # after a tracker ``reset()`` so totals match the member list — otherwise
            # extract-prep then full materialization on 7z/RAR leaves the tracker at 0
            # while the cache holds every member.
            self._listing_tracker.account_member(member, enforce=enforce_listing_limits)
            return
        member._member_id = idx
        member._archive_id = self._archive_id
        # Dedupe on the member *id*, not on this object. ``extract_all`` walks the list
        # twice — ``_get_members_index_only`` for the extraction prep, then
        # ``_materialize_members`` — and the two passes build *different* ArchiveMember
        # objects for the same member, so the ``_member_id is not None`` guard above
        # never sees the second one. The listing tracker wants that re-accounting (see
        # its comment above); a presentation diagnostic does not: one member with one
        # deceptive name is one finding, and counting it twice inflates
        # ``DiagnosticSummary.counts``, burns two retention slots and fires the caller's
        # callback twice.
        if idx not in self._presentation_checked:
            self._presentation_checked.add(idx)
            emit_member_name_bidi_control(
                self._diagnostics_collector,
                member=member,
                archive_name=self._archive_name,
            )
        self._listing_tracker.account_member(member, enforce=enforce_listing_limits)

    def _ensure_link_target(self, member: ArchiveMember) -> None:
        """Populate ``link_target`` from member data when needed. Base is a no-op."""
        return

    def _resolve_link_target(self, member: ArchiveMember) -> None:
        """Run the backend's link-target hook at most once per member.

        Nothing the hook can learn changes between two calls on the same member: the
        archive's bytes are fixed and a password is reader-level, never per-open. So a
        second call only re-opens and re-decompresses the member and emits the same
        diagnostic again — which would count one targetless link twice in
        ``DiagnosticSummary.counts``, and under a ``RAISE`` disposition would raise at
        whatever later access happened to touch the member rather than during listing.
        """
        if member.link_target is not None or member._link_target_resolved:
            return
        self._ensure_link_target(member)
        # After, not before: a hook that raised did not look and come back empty, it
        # never finished. `_finalize_links` swallows CorruptionError / TruncatedError on
        # an already-damaged listing, so marking it resolved on the way out would trade
        # the real fault for a generic "Link target is unknown" at the next access.
        # A backend that catches EncryptionError returns normally, so the repeated-read
        # case this memo exists for is still covered.
        member._link_target_resolved = True

    def _report_member_diagnostic(
        self,
        *,
        code: DiagnosticCode,
        message: str,
        context: DiagnosticContext,
        member: ArchiveMember,
        report_key: object | None,
        diagnostic_logger: logging.Logger | None = None,
    ) -> None:
        """Emit one diagnostic about one member, at most once however often it is typed.

        One member with one deceptive name is one finding, and a second report of it
        inflates ``DiagnosticSummary.counts``, burns a second retention slot and fires
        the caller's callback again. That is easy to get wrong here because a member can
        be typed more than once per archive: ``extract_all`` lists an indexed backend
        twice, once for the totals and the selector and once to drive the extraction,
        and a backend that builds its ``ArchiveMember`` objects from the header each
        time (ZIP, ISO) produces a different object for the same member on the second
        pass. One that caches them (7z, RAR) hands the same object back and reaches here
        with the same key, which is the same answer by a shorter route.

        ``report_key`` is what identifies the member across those passes — its position
        in the listing, and for a diagnostic a member can raise more than once (an
        invalid timestamp in two separate fields) the position paired with whatever
        tells the two apart. ``None`` disables the memo, for a caller with nothing
        stable to key on.

        ``diagnostic_logger`` keeps a code that belongs to another logging category on
        its own logger — ``archivey.normalization`` for a normalized name — since
        routing every diagnostic through this one method would otherwise re-label them
        all as ``archivey.backends``.

        A repeat sighting re-attaches the first report to the object this pass produced,
        because the caller holds that one and an empty ``member.diagnostics`` on it is no
        report at all. Where there is nothing to re-attach — an IGNORE disposition and a
        spent retention budget both emit without attaching — the repeat is still
        suppressed, which is the case ``counts`` depends on most: under IGNORE it is the
        only channel the caller has left.
        """
        memo = (report_key, code) if report_key is not None else None
        if memo is not None and memo in self._member_reports:
            reported = self._member_reports[memo]
            if reported is not None:
                self._diagnostics_collector.reattach_to_member(member, reported)
            return
        diagnostic = self._diagnostics_collector.emit(
            code=code,
            message=message,
            context=context,
            member=member,
            attach_to_member=True,
            logger=diagnostic_logger if diagnostic_logger is not None else logger,
        )
        if memo is not None:
            # Only a report that landed on the member can be handed to a later object:
            # re-attaching one that did not would give the second object a record the
            # first never got.
            self._member_reports[memo] = (
                diagnostic
                if any(attached is diagnostic for attached in member._diagnostics)
                else None
            )

    def _emit_link_target_unavailable(
        self,
        member: ArchiveMember,
        *,
        reason: str,
        message: str,
        target_in_archive: bool,
        report_key: int | None = None,
    ) -> None:
        """Report that a link's target could not be read, and why.

        Every path that leaves ``link_target`` unset on a member the archive calls a
        link goes through here. That is the whole guarantee `safe-extraction` and
        ``docs/extracting.md`` make about the ``LINK_TARGET_UNAVAILABLE`` outcome: it says
        only that extraction wrote nothing, so the *reason* has to reach the caller on
        the diagnostics channel, and ``SYMLINK_TARGET_UNAVAILABLE`` is in
        ``ARCHIVE_INTEGRITY_CODES`` so a strict policy refuses the archive outright.
        A backend that returns quietly instead makes that guarantee false.

        ``target_in_archive`` says whether the archive carries a target this reader
        could not reach — compressed, split across volumes, encrypted — as against
        recording none at all. Extraction turns the first into a per-member failure and
        only the second into ``LINK_TARGET_UNAVAILABLE``, because a member the archive
        describes in full must not go missing from the output under a status that reads
        as success. The caller decides it because the caller is the only place that
        knows; inferring it downstream from "the lookup finished" is what this
        parameter replaced.

        ``report_key`` is for a backend calling this while the member is still being
        *typed*, before :meth:`_register_member` has given it an id: it is the member's
        position in the archive's listing, which is what registration takes the id from.
        It is passed rather than waited for because typing time is the only moment every
        path shares. Link finalization does not run in a progressive pass a caller
        abandons early, and registration does not run at all in a 7z one, which streams
        straight off its cached member list — so anything held back for either loses the
        report on exactly the archives this outcome exists for. On that 7z path nothing
        ever stamps the id: the report names the position and the member's own public
        ``member_id`` stays unset, so a caller reading both sees an id on one and not on
        the other.

        One member reported once is what the caller's ``counts``, retention budget and
        callback all assume; :meth:`_report_member_diagnostic` is what holds that, here
        and for the other diagnostics a member can be typed into twice.
        """
        member._link_target_absent = not target_in_archive
        key = member._member_id if member._member_id is not None else report_key
        self._report_member_diagnostic(
            code=DiagnosticCode.SYMLINK_TARGET_UNAVAILABLE,
            message=message,
            context=SymlinkTargetContext(
                archive_name=self._archive_name,
                member_name=member.name,
                member_id=key,
                reason=reason,
            ),
            member=member,
            report_key=key,
        )

    def _apply_reparse_data(
        self,
        member: ArchiveMember,
        data: bytes,
        *,
        fallback_type: MemberType,
        report_key: int | None = None,
    ) -> None:
        """Set ``link_target`` (and ``is_junction``) from a Windows reparse buffer.

        This answers the target question outright — it either produces a target or
        records that the archive holds none — so it marks the lookup done. A backend
        that already has the buffer, or already knows there is none, can therefore call
        it while the member is being typed, and ``_resolve_link_target`` will not run
        the hook again later. That matters because the hook runs at EOF in a streaming
        pass, long after extraction has decided what to do with the member.

        The reparse tag that separates a junction from a symlink is the first field of
        that buffer, and the buffer is the member's *data*, so this is the same
        listing-from-data path a symlink target already takes — see
        :mod:`archivey.internal.windows_reparse`.

        The attribute bit that gets a member here says it *was* a reparse point on the
        source filesystem. It does not promise the archive carries the buffer, and it
        does not promise the tag named a link at all — Windows sets the same bit for
        deduplication stubs, cloud placeholders and WSL entries, whose data is ordinary
        content. So a member whose data is present but is not a link buffer goes back to
        ``fallback_type`` and keeps that content: the bytes are what we know, the link
        type is what we inferred, and discarding the former for the latter would cost
        the caller a readable member.

        That trade only pays when ``fallback_type`` is a FILE. Re-typing to DIRECTORY
        buys nothing and costs twice over: :meth:`open` refuses a directory, so the data
        this exists to preserve becomes unreachable anyway, and the entry has already
        lost the trailing slash that made it a directory in the first place (the backend
        suppresses that rename's diagnostic because a *link* stored with the directory
        convention is the format's own spelling). A directory-shaped entry therefore
        stays a targetless link, which is what the archive said it was.

        A member with no data at all has nothing to reinterpret and stays a link with no
        target. That is the case every Windows archiver actually produces for a junction:
        7-Zip writes no data for the directory reparse point that every junction is, so
        the target and the tag are both simply gone, and a member whose target we
        invented would be worse than one that says it has none.
        """
        member._link_target_resolved = True
        parsed = parse_reparse_data(data)
        if parsed is not None and parsed.is_junction:
            # The tag is the buffer's first field, so a junction is established as soon
            # as the buffer parses — independently of whether a target came out of it.
            member.extra[EXTRA_IS_JUNCTION] = True
        if parsed is not None and parsed.target:
            member.link_target = parsed.target
            return

        if parsed is None and data and fallback_type is not MemberType.DIRECTORY:
            member.type = fallback_type
            reason = "reparse_data_unrecognized"
            message = (
                f"{quoted(member.name)} is flagged as a Windows reparse point, but its "
                f"{len(data)} bytes of data are not a symlink or junction buffer; "
                f"presenting it as a {fallback_type.value} with that data as its content."
            )
        elif parsed is None and data:
            reason = "reparse_data_unrecognized"
            message = (
                f"{quoted(member.name)} is flagged as a Windows reparse point, but its "
                f"{len(data)} bytes of data are not a symlink or junction buffer; "
                f"it is stored as a directory, whose content is not readable either "
                f"way, so it stays a link with no target."
            )
        else:
            if not data:
                detail = "the writer stored no reparse data for it"
                reason = "reparse_data_absent"
            else:
                detail = "its reparse data names no target"
                reason = "reparse_data_nameless"
            message = (
                f"Cannot read the link target of {quoted(member.name)}: {detail}; "
                f"leaving link_target unset."
            )
        # Every branch above is the archive recording no target: no data, a buffer that
        # names nothing, or bytes that are not a link buffer at all. None of them is a
        # target this reader merely failed to reach.
        self._emit_link_target_unavailable(
            member,
            reason=reason,
            message=message,
            target_in_archive=False,
            report_key=report_key,
        )

    @staticmethod
    def _index_member_name(
        by_name_lists: dict[str, list[ArchiveMember]], member: ArchiveMember
    ) -> None:
        by_name_lists.setdefault(member.name, []).append(member)

    @staticmethod
    def _target_name_keys(target_name: str) -> tuple[str, ...]:
        if target_name.endswith("/"):
            return (target_name,)
        return (target_name, target_name + "/")

    @staticmethod
    def _latest_prior_named_member(
        target_name: str,
        before_id: int,
        by_name_lists: Mapping[str, list[ArchiveMember]],
    ) -> ArchiveMember | None:
        """Latest member matching ``target_name`` with ``member_id`` strictly before ``before_id``."""
        best: ArchiveMember | None = None
        best_id = -1
        for name in BaseArchiveReader._target_name_keys(target_name):
            for prior in reversed(by_name_lists.get(name, [])):
                prior_id = prior._member_id
                if prior_id is None:
                    continue
                if prior_id < before_id:
                    if prior_id > best_id:
                        best = prior
                        best_id = prior_id
                    break
        return best

    @staticmethod
    def _last_by_exact_name(
        name: str, by_name_lists: Mapping[str, list[ArchiveMember]]
    ) -> ArchiveMember | None:
        candidates = by_name_lists.get(name)
        if not candidates:
            return None
        return candidates[-1]

    @staticmethod
    def _last_named_member(
        target_name: str, by_name_lists: Mapping[str, list[ArchiveMember]]
    ) -> ArchiveMember | None:
        """Last-wins lookup for a link target (tries bare and ``/``-suffixed names)."""
        for name in BaseArchiveReader._target_name_keys(target_name):
            candidates = by_name_lists.get(name)
            if candidates:
                return candidates[-1]
        return None

    @staticmethod
    def _lookup_link_target(
        member: ArchiveMember,
        by_name_lists: Mapping[str, list[ArchiveMember]],
    ) -> ArchiveMember | None:
        """The member ``member``'s link target refers to, or ``None`` if not present.

        Resolves the stored target string to an archive-namespace name first (a symlink
        target is relative to the link's own directory — see
        :func:`resolve_link_target_name`), then looks it up in ``by_name_lists``
        (last-wins for symlinks); directory members carry a trailing ``/`` in their names,
        so both forms are tried.
        """
        if not member.link_target:
            return None
        target_name = resolve_link_target_name(
            member.name, member.link_target, member.type
        )
        if target_name is None:
            return None
        return BaseArchiveReader._last_named_member(target_name, by_name_lists)

    @staticmethod
    def _lookup_hardlink_target(
        member: ArchiveMember,
        by_name_lists: Mapping[str, list[ArchiveMember]],
        *,
        allow_forward_fallback: bool,
    ) -> ArchiveMember | None:
        """Positional hardlink resolution: latest same-named member strictly before ``member``."""
        if not member.link_target:
            return None
        target_name = resolve_link_target_name(
            member.name, member.link_target, member.type
        )
        if target_name is None:
            return None
        before_id = member._member_id
        if before_id is None:
            return None
        found = BaseArchiveReader._latest_prior_named_member(
            target_name, before_id, by_name_lists
        )
        if found is not None:
            return found
        if allow_forward_fallback:
            return BaseArchiveReader._last_named_member(target_name, by_name_lists)
        return None

    def _lookup_link_target_for_member(
        self,
        member: ArchiveMember,
        by_name_lists: Mapping[str, list[ArchiveMember]],
        *,
        allow_forward_fallback: bool = True,
    ) -> ArchiveMember | None:
        if member.type == MemberType.HARDLINK:
            return self._lookup_hardlink_target(
                member,
                by_name_lists,
                allow_forward_fallback=allow_forward_fallback,
            )
        return self._lookup_link_target(member, by_name_lists)

    def _resolve_link(
        self,
        member: ArchiveMember,
        by_name_lists: dict[str, list[ArchiveMember]],
    ) -> None:
        """Resolve link_target to the fully dereferenced link_target_member."""
        visited: set[int] = set()
        current = member

        while current.is_link and current.link_target:
            if current._member_id is None:
                return
            member_id = current._member_id
            if member_id in visited:
                # Cycle detected; leave link_target_member unset (None).
                return
            visited.add(member_id)
            target = self._lookup_link_target_for_member(current, by_name_lists)
            if target is None:
                return
            current = target

        if current is not member:
            member.link_target_member = current

    def _finalize_pass_links(self, *, error: ArchiveyError | None = None) -> None:
        """Resolve all links after a streaming forward pass reaches EOF or terminal damage."""
        if self._materialized is not None:
            return
        # scan_members drains with enforcement: refuse to publish an over-limit report.
        if self._progressive_enforce_listing_limits:
            self._listing_tracker.assert_within_limits()
        self._finalize_links(
            self._pass_scanned,
            self._pass_by_name_lists,
            error=error,
            child_scope=False,
            is_current_first=False,
        )
        self._publish_materialized(
            self._pass_scanned,
            self._pass_by_name_lists,
            error=error,
        )

    def _stamp_progressive_member(self, idx: int, member: ArchiveMember) -> None:
        # stream_members: account without enforcing (O(1) escape hatch).
        # scan_members: enforce per registration via ``_progressive_enforce_listing_limits``.
        self._register_member(
            idx,
            member,
            enforce_listing_limits=self._progressive_enforce_listing_limits,
        )
        if member.is_link and member.link_target_member is None:
            target = self._lookup_link_target_for_member(
                member,
                self._pass_by_name_lists,
                allow_forward_fallback=False,
            )
            if target is not None and target is not member:
                if target.is_link:
                    target = target.link_target_member
                if target is not None:
                    member.link_target_member = target
        self._index_member_name(self._pass_by_name_lists, member)
        self._pass_scanned.append(member)

    def _begin_forward_pass(self) -> Iterator[ArchiveMember]:
        """Return the shared instance-held progressive pass, creating it if needed."""
        if self._progressive_gen is None:
            self._pass_scanned = []
            self._pass_by_name_lists = {}
            self._listing_tracker.reset()
            self._account_archive_comment(
                enforce=self._progressive_enforce_listing_limits
            )
            self._progressive_gen = _ProgressivePassIterator(self)
        return self._progressive_gen

    def _guard_forward_pass_entry(self, op: str) -> None:
        if self._streaming and self._forward_pass_started:
            raise UnsupportedOperationError(
                f"{op} is not available after a streaming reader's forward pass has "
                f"started. Call scan_members() for the resolved member list, or "
                f"members_report_if_available() for an index-only peek.",
            )

    def _enter_forward_pass(self, op: str) -> None:
        self._guard_forward_pass_entry(op)
        self._forward_pass_started = True

    # --- Public API ---

    def _require_random_access(self, op: str) -> None:
        """Raise ``UnsupportedOperationError`` if ``op`` (a random-access or
        full-materialization operation) is not allowed on this reader.

        A ``streaming=True`` reader is forward-only: only a single pass of
        ``__iter__``/``stream_members`` (or one ``extract_all``) is allowed. This is
        uniform and format-independent — it does **not** depend on whether a backend
        happens to have an index loaded (use :meth:`scan_members` or
        :meth:`members_report_if_available` for member listing instead).
        """
        self._state.require_open(op)
        if self._streaming:
            raise UnsupportedOperationError(
                f"{op} is not available on a streaming (forward-only) reader. "
                f"Iterate with stream_members(), call scan_members() for the resolved "
                f"member list, or members_report_if_available() for an index-only peek.",
            )

    @property
    def format(self) -> ArchiveFormat:
        self._state.require_open("format")
        return self._format

    @property
    def info(self) -> ArchiveInfo:
        self._state.require_open("info")
        return self._get_archive_info()

    @property
    def cost(self) -> CostReceipt:
        self._state.require_open("cost")
        return self._get_archive_info().cost

    @property
    def diagnostics(self) -> DiagnosticSummary:
        """Fresh immutable cumulative snapshot of diagnostics for this reader."""
        self._state.require_open("diagnostics")
        return self._diagnostics_collector.snapshot()

    @property
    def compressed_source_size(self) -> int | None:
        """Byte size of the archive's source when cheaply knowable, else ``None``.

        The denominator for extraction's archive-wide decompression-ratio guard (see
        ``safe-extraction``): for zip/7z/rar/compressed-tar the source size *is* the
        compressed size, and for a plain tar or other uncompressed container the
        resulting ~1:1 ratio simply never trips the guard. Cheap only — see
        ``source_byte_size``: path ``stat``, a ``size`` attribute (fsspec convention,
        also on archivey's own member/codec streams, enabling nested archives), a
        ``try_get_size()`` index scan, or a ``SEEK_END`` probe restricted to provably
        O(1) types (never a decompressor). Backends record their source in
        ``self._source``; readers without one (directory) or with an unknowable
        source report ``None``.
        """
        self._state.require_open("compressed_source_size")
        return source_byte_size(self._source) if self._source is not None else None

    @property
    def compressed_bytes_consumed(self) -> int | None:
        """Running count of compressed bytes pulled from the archive's outer source so far,
        or ``None`` when nothing is being counted.

        The **live** denominator for extraction's archive-wide decompression-ratio guard
        (see ``safe-extraction``), used when ``compressed_source_size`` is ``None`` — a
        compressed archive whose source size is not cheaply knowable (a non-seekable pipe,
        or a seekable stream that is neither a whitelisted O(1)-seek type nor
        ``.size``-advertising). A backend that decompresses a *stream* source wraps it in a
        ``CountingReader`` and records it here; readers with a knowable source size (a path,
        a sizable stream) leave it ``None`` and rely on the cheaper static ratio instead.
        """
        self._state.require_open("compressed_bytes_consumed")
        c = self._compressed_input_counter
        return c.bytes_read if c is not None else None

    def _wrap_compressed_input(self, source: Path | BinaryIO) -> Path | BinaryIO:
        """Wrap a stream source **whose byte size is not cheaply knowable** in a
        ``CountingReader`` (recorded for the live decompression-ratio guard) and return the
        wrapper; return the source unchanged for a path or a sizable stream, whose static
        archive-wide ratio applies instead — exactly the complement of
        ``compressed_source_size``, so one of the two denominators is always available for
        a compressed source. A compressed backend that decompresses a stream source calls
        this on the raw source before handing it to the codec layer, so
        ``compressed_bytes_consumed`` tracks what the decompressor pulls.

        For a *seekable* unsizable source the codec layer may seek and re-read (an index
        scan, an accelerator); re-read bytes are counted again, which only ever inflates
        the denominator — the guard gets weaker, never a false positive.
        """
        if is_stream(source) and source_byte_size(source) is None:
            counter = CountingReader(source)
            self._compressed_input_counter = counter
            return counter
        return source

    def __iter__(self) -> Iterator[ArchiveMember]:
        self._state.require_open("__iter__")
        if self._streaming:
            token = self._state.acquire_pass("__iter__")
            try:
                self._enter_forward_pass("__iter__")
                yield from self._begin_forward_pass()
            finally:
                self._state.release_pass(token)
            return
        # Random access: iteration just walks the already-published immutable member
        # snapshot, so it must NOT hold a reader-wide pass across consumption — that would
        # reject the common `for m in reader: reader.open(m)` idiom as overlap. Acquire the
        # pass only around materialization (matching members()), then yield the captured
        # snapshot with no pass held so open()/get() inside the loop are admitted.
        token = self._state.acquire_pass("__iter__")
        try:
            report = self._materialize_members().report
        finally:
            self._state.release_pass(token)
        yield from report.members
        if report.error is not None:
            raise report.error

    def members(self) -> list[ArchiveMember]:
        self._require_random_access("members()")
        # Under CONCURRENT, first-touch materialization is coordinated via worker tokens
        # so overlapping members()/open()/get() share one build instead of rejecting.
        # Default readers keep an exclusive pass (single-owner materialization).
        if self._state.concurrent:
            token = self._state.acquire_worker("members")
            try:
                report = self._materialize_members().report
                if report.error is not None:
                    raise report.error
                return list(report.members)
            finally:
                self._state.release_worker(token)
        token = self._state.acquire_pass("members")
        try:
            # Return a shallow copy so callers cannot mutate the published cache container.
            report = self._materialize_members().report
            if report.error is not None:
                raise report.error
            return list(report.members)
        finally:
            self._state.release_pass(token)

    def members_report(self) -> MemberListReport:
        self._state.require_open("members_report()")
        if not self._streaming:
            if self._state.concurrent:
                token = self._state.acquire_worker("members_report")
                try:
                    return self._materialize_members().report
                finally:
                    self._state.release_worker(token)
            token = self._state.acquire_pass("members_report")
            try:
                return self._materialize_members().report
            finally:
                self._state.release_pass(token)

        token = self._state.acquire_pass("members_report")
        try:
            if self._materialized is not None:
                self._listing_tracker.assert_within_limits()
                return self._materialized.report
            # Enforce ListingLimits while draining; stream_members leaves this false.
            self._progressive_enforce_listing_limits = True
            try:
                if not self._forward_pass_started:
                    self._forward_pass_started = True
                gen = self._begin_forward_pass()
                while True:
                    try:
                        next(gen)
                    except StopIteration:
                        break
                    except (CorruptionError, TruncatedError):
                        assert self._materialized is not None
                        break
                assert self._materialized is not None
                self._listing_tracker.assert_within_limits()
                return self._materialized.report
            finally:
                self._progressive_enforce_listing_limits = False
        finally:
            self._state.release_pass(token)

    def scan_members(self) -> list[ArchiveMember]:
        self._state.require_open("scan_members()")
        token = self._state.acquire_pass("scan_members")
        try:
            if not self._streaming:
                report = self._materialize_members().report
                if report.error is not None:
                    raise report.error
                return list(report.members)
            if self._materialized is not None:
                self._listing_tracker.assert_within_limits()
                report = self._materialized.report
                if report.error is not None:
                    raise report.error
                return list(report.members)
            # Enforce ListingLimits while draining; stream_members leaves this false.
            self._progressive_enforce_listing_limits = True
            try:
                if not self._forward_pass_started:
                    self._forward_pass_started = True
                gen = self._begin_forward_pass()
                for _ in gen:
                    pass
                assert self._materialized is not None
                self._listing_tracker.assert_within_limits()
                report = self._materialized.report
                if report.error is not None:
                    raise report.error
                return list(report.members)
            finally:
                self._progressive_enforce_listing_limits = False
        finally:
            self._state.release_pass(token)

    def members_report_if_available(self) -> MemberListReport | None:
        """Return the member-list report if it is available **without scanning**, else
        ``None``. Safe to call on any reader (including a streaming one).

        Index-only: returns a materialized report after a completed forward pass, or the
        backend's upfront index when ``_MEMBER_LIST_UPFRONT`` is set. It never triggers
        a forward scan, never reads member data, and never consumes the forward pass.
        Link targets stored in member data (e.g. ZIP symlinks) may be unset; use
        :meth:`members` or :meth:`scan_members` for a fully-resolved list.
        """
        self._state.require_open("members_report_if_available()")
        if self._materialized is not None:
            self._listing_tracker.assert_within_limits()
            return self._materialized.report
        if self._MEMBER_LIST_UPFRONT:
            return MemberListReport(
                members=tuple(self._get_members_index_only()),
                error=None,
                diagnostics=self._diagnostics_collector.snapshot(),
            )
        return None

    def __contains__(self, member: object) -> bool:
        # Identity membership for ArchiveMembers: O(1), no scan, so it works in any
        # access mode. This method must exist even though it is a convenience — without
        # a __contains__, the `in` operator falls back to iterating __iter__, which
        # would silently consume a streaming reader's single forward pass (and compare
        # members by value). Strings are rejected: name lookup is get().
        if isinstance(member, ArchiveMember):
            return member._archive_id == self._archive_id
        raise TypeError(
            f"'in <ArchiveReader>' tests whether an ArchiveMember belongs to this "
            f"reader (by identity); to look up a member by name use reader.get(name). "
            f"Got {type(member).__name__}.",
        )

    def get(
        self, name: str, default: ArchiveMember | None = None
    ) -> ArchiveMember | None:
        self._require_random_access("get()")
        token = self._state.acquire_worker("get")
        try:
            materialized = self._materialize_members()
            found = self._last_by_exact_name(name, materialized.by_name_lists)
            if found is not None:
                return found
            if materialized.report.error is not None:
                raise materialized.report.error
            return default
        finally:
            self._state.release_worker(token)

    def open(self, member: str | ArchiveMember) -> ArchiveStream:
        """Open member for reading. Follows symlinks.

        Without ``open_archive(concurrent_members=True)``, at most one member stream may
        be live. With it, concurrent first-touch materialization is coordinated and
        concurrent ``open`` is supported. Positioning requires
        ``open_archive(seekable_members=True)``.
        """
        # Two independent gates: the access mode (streaming=True forbids random access)
        # and the backend capability (_SUPPORTS_RANDOM_ACCESS, used by the Phase-3
        # open-time fail-fast for non-seekable sources).
        self._require_random_access("open()/read()")
        if not self._SUPPORTS_RANDOM_ACCESS:
            raise UnsupportedOperationError(
                "This reader does not support random access (open()/read()); "
                "iterate with stream_members() instead.",
            )
        token = self._state.acquire_worker("open")
        try:
            materialized = self._materialize_members()
            if isinstance(member, str):
                found = self._last_by_exact_name(member, materialized.by_name_lists)
                if found is None:
                    if materialized.report.error is not None:
                        raise materialized.report.error
                    raise KeyError(f"Member {member!r} not found")
                member = found
            else:
                # Checked before the identity comparison below, which reads a private
                # attribute: without this, `open(0)` failed as
                # `AttributeError: 'int' object has no attribute '_archive_id'` —
                # a private field name crossing the public boundary in place of an
                # answer. `in` raises TypeError here (a spec'd escape for the operator
                # protocol); this is an ordinary argument, so it takes the usage error.
                if not isinstance(member, ArchiveMember):
                    raise ArchiveyUsageError(
                        f"reader.open() takes a member name (str) or an ArchiveMember "
                        f"yielded by this reader, but got {describe_value(member)}."
                    )
                # A member object must have been yielded by THIS reader (same identity rule
                # as `member in reader`). Without this check, a member from another archive
                # resolves against the wrong offsets/paths and can silently return the wrong
                # data (e.g. the directory backend would read whatever sits at the same
                # relative path under this reader's root).
                if member._archive_id != self._archive_id:
                    raise ArchiveyUsageError(
                        f"Member {quoted(member.name)} does not belong to this reader; open a "
                        f"member yielded by this reader, or look it up by name with "
                        f"reader.get(name)."
                    )
            # Gate before spawn: ``_lazy_member_stream`` already registers first.
            # Reserve under the lock so two threads cannot both pass, then open,
            # then bind. A refused second open must not build a decompressor /
            # unrar process that is never registered.
            reservation = self._state.reserve_live_stream()
            bound = False
            stream: ArchiveStream | None = None
            try:
                stream = self._open_with_link_follow(member, visited=set())
                registered = self._register_public_stream(stream, token=reservation)
                bound = True
                return registered
            finally:
                if not bound:
                    pending = sys.exception()
                    if stream is not None:
                        try:
                            stream.close()
                        except Exception as close_exc:  # noqa: BLE001 - attach, don't replace
                            # Don't replace the in-flight open/register error.
                            if pending is not None:
                                pending.add_note(
                                    "closing the unbound member stream also failed: "
                                    f"{close_exc}"
                                )
                            else:
                                raise
                    if self._state.release_reservation(reservation):
                        # True → last lease dropped. Today this is False: open()
                        # still holds a worker, so the reader lease remains. Honour
                        # the return so a future invariant break actually teardowns.
                        self._maybe_teardown()
        finally:
            self._state.release_worker(token)

    def _open_with_link_follow(
        self,
        member: ArchiveMember,
        visited: set[int],
    ) -> ArchiveStream:
        if member.type in (MemberType.SYMLINK, MemberType.HARDLINK):
            if member._member_id is None:
                raise LinkTargetNotFoundError(
                    "Link target is unknown",
                    member_name=member.name,
                )
            member_id = member._member_id
            if member_id in visited:
                raise ReadError(
                    f"Link cycle detected at '{member.name}'",
                    member_name=member.name,
                )
            visited.add(member_id)
            if member.link_target_member is not None:
                return self._open_with_link_follow(member.link_target_member, visited)
            if member.link_target is None:
                self._resolve_link_target(member)
            if member.link_target is None:
                raise LinkTargetNotFoundError(
                    "Link target is unknown",
                    member_name=member.name,
                )
            materialized = self._materialized
            by_name_lists = (
                materialized.by_name_lists if materialized is not None else None
            )
            target = (
                self._lookup_link_target_for_member(member, by_name_lists)
                if by_name_lists is not None
                else None
            )
            if target is None:
                raise LinkTargetNotFoundError(
                    "Link target not found in archive",
                    member_name=member.name,
                    link_target=member.link_target,
                )
            return self._open_with_link_follow(target, visited)
        if member.type in (MemberType.DIRECTORY, MemberType.ANTI, MemberType.OTHER):
            raise ArchiveyUsageError(
                f"Cannot open member {quoted(member.name)}: type is {member.type.value!r} "
                f"(not a file)"
            )
        return self._open_member(member)

    def read(self, member: str | ArchiveMember) -> bytes:
        """Read member data as bytes."""
        with self.open(member) as f:
            return f.read()

    def stream_members(
        self,
        members: MemberSelector = None,
    ) -> Iterator[tuple[ArchiveMember, ArchiveStream | None]]:
        """Yield (member, stream) pairs. members is a selector filter (no transform).

        The yielded stream is owned by the iterator: advancing closes/invalidates the
        previous stream before the next pair is produced.
        """
        self._state.require_open("stream_members()")
        # Validate here rather than inside the generator: a generator body does not
        # run until the first next(), so a check left there raised at a call site
        # that did not make the mistake.
        selector = normalize_member_selector(members)
        return self._iter_stream_members(selector)

    def _iter_stream_members(
        self,
        selector: Callable[[ArchiveMember], bool] | None,
    ) -> Iterator[tuple[ArchiveMember, ArchiveStream | None]]:
        token = self._state.acquire_pass("stream_members")
        current: ArchiveStream | None = None
        try:
            if self._streaming:
                self._enter_forward_pass("stream_members()")
            for m, stream in self._iter_with_data():
                if current is not None:
                    current.close()
                    current = None
                if selector is None or selector(m):
                    current = stream
                    yield m, stream
                elif stream is not None:
                    stream.close()
        finally:
            if current is not None:
                current.close()
            self._state.release_pass(token)

    def extract_all(
        self,
        dest: str | Path,
        *,
        members: MemberSelectorArg = None,
        filter: MemberFilter | None = None,
        policy: ExtractionPolicy | ExtractionPolicyStr = ExtractionPolicy.STRICT,
        overwrite: OverwritePolicy | OverwritePolicyStr = OverwritePolicy.ERROR,
        on_error: OnError | OnErrorStr = OnError.STOP,
        abort_on: Collection[AbortOn | AbortOnStr] = (),
        on_progress: Callable[[ExtractionProgress], None] | None = None,
        config: ArchiveyConfig | None = None,
        limits: ExtractionLimits | None = None,
    ) -> ExtractionReport:
        """Extract members to dest via the shared ``ExtractionCoordinator``."""
        # At the boundary, not on use: the coordinator tests these with ``is``, so an
        # unrecognised value is not refused there, it silently takes the other branch.
        policy = coerce_enum(
            policy, ExtractionPolicy, call="extract_all()", param="policy="
        )
        overwrite = coerce_enum(
            overwrite, OverwritePolicy, call="extract_all()", param="overwrite="
        )
        on_error = coerce_enum(
            on_error, OnError, call="extract_all()", param="on_error="
        )
        abort_on = coerce_enum_collection(
            abort_on, AbortOn, call="extract_all()", param="abort_on="
        )
        self._state.require_open("extract_all()")
        check_config(config, call="extract_all(config=…)")
        check_extraction_limits(limits, call="extract_all(limits=…)")
        check_callable(on_progress, call="extract_all(on_progress=…)")
        # ``filter`` is not consulted until the first member is offered, by which point
        # the extraction is under way; a non-callable there reads as
        # ``TypeError: 'int' object is not callable`` with nothing naming the argument.
        check_callable(filter, call="extract_all(filter=…)")
        # ``members=`` used to be checked inside the coordinator, after dest was
        # created. Same reason as filter: a refusal that has already touched the disk
        # is a side effect of a call the caller got wrong.
        normalize_member_selector(members)
        # Check (but do not enter) the single-pass guard here, so a second extract_all
        # on a streaming reader fails with this method's name; the coordinator drives
        # the pass through the public stream_members(), which enters it properly.
        if self._streaming:
            self._guard_forward_pass_entry("extract_all()")
        # Imported here (not at module top) to keep the import graph tidy: extraction.py
        # type-checks against BaseArchiveReader.
        from archivey.internal.extraction import ExtractionCoordinator

        # Listing limits stay on the open-time reader config for the reader lifetime;
        # a per-call config may override extraction_limits / policy / accelerators but
        # must not replace self._config.listing_limits (see archive-reading).
        effective_config = config if config is not None else self._config
        effective_limits = (
            limits if limits is not None else effective_config.extraction_limits
        )
        collector = self._diagnostics_collector
        # This call's report covers only its own extraction-phase events. The one-shot
        # extract() re-snapshots against its own pre-detection watermark to widen the
        # window, so extract_all() never needs to know about that outer scope.
        wm = collector.watermark()
        coordinator = ExtractionCoordinator(
            policy=policy,
            overwrite=overwrite,
            on_error=on_error,
            abort_on=abort_on,
            on_progress=on_progress,
            members=members,
            filter=filter,
            limits=effective_limits,
        )
        token = self._state.acquire_pass("extract_all")
        try:
            # Library-internal member opens (including hardlink recovery) are ungated.
            with self._internal_member_opens():
                results = coordinator.run(self, dest)
        finally:
            self._state.release_pass(token)
        return ExtractionReport(
            results=tuple(results),
            diagnostics=collector.snapshot(since=wm),
        )

    def close(self) -> None:
        """Close the reader.

        Idempotent. With declared concurrency, blocks until in-flight worker
        ``open()`` / ``read()`` / ``get()`` / ``members()`` calls return, then marks the
        reader closed. Still-open member streams are then closed, matching
        ``zipfile.ZipFile.close()`` / ``tarfile.TarFile.close()``: a member stream does
        not outlive the reader it came from.

        Without ``CONCURRENT``, ``close()`` still raises if a worker call or reader-wide
        pass is actively executing, and the reader stays open. Teardown runs at most
        once, after the last stream's lease drops.
        """
        if self._closed:
            return
        # Only mark closed after mark_reader_closed succeeds (or is a no-op because another
        # thread already closed). Raising on an active pass must leave the reader open --
        # so member streams are closed only once the transition has actually happened.
        run_teardown = self._state.mark_reader_closed()
        self._closed = True
        # Exactly one caller closes the streams. mark_reader_closed() returns False both
        # when this thread transitioned with leases outstanding and when a peer had
        # already closed, so it cannot tell the owner from a late caller -- and
        # ArchiveStream.close tests `self.closed` outside its lock, so two concurrent
        # close() calls could otherwise both reach inner.close() on the same stream.
        if self._state.claim_stream_shutdown():
            self._close_public_streams()
        if run_teardown:
            self._maybe_teardown()

    def _close_public_streams(self) -> None:
        """Close member streams that are still open, in the order they were opened.

        Each close releases that stream's lease, and the last one triggers teardown
        through the ordinary path -- so the source is always closed *after* the streams
        reading through it, never underneath one.
        """
        failures: list[BaseException] = []
        # Snapshot: closing mutates the registry through the lease callbacks.
        for stream in list(self._public_streams.values()):
            if stream.closed:
                continue
            try:
                stream.close()
            except BaseException as exc:  # noqa: BLE001 - report every failure, not the first
                failures.append(exc)
        self._public_streams.clear()
        if len(failures) == 1:
            raise failures[0]
        if failures:
            raise BaseExceptionGroup(
                "closing member streams during reader close failed", failures
            )

    def __enter__(self) -> "BaseArchiveReader":
        self._state.require_open("__enter__")
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _stamp_error_context(
        self, exc: ArchiveyError, member_name: str | None = None
    ) -> None:
        """Stamp format/archive/member context onto an ArchiveyError if not already set."""
        if exc.source_format is None:
            exc.source_format = self._format
        if exc.archive_name is None:
            exc.archive_name = self._archive_name
        if exc.member_name is None and member_name is not None:
            exc.member_name = member_name
        provenance = self._format_provenance
        if (
            provenance is not None
            and provenance.probe_only
            and isinstance(exc, (TruncatedError, CorruptionError))
        ):
            self._mark_format_unconfirmed(exc)

    def io_stats(self) -> "IoStats | None":
        """Return I/O counters if measurement is enabled, else ``None``.

        Enable measurement via :func:`archivey.measurement.enable_measurement` around
        the :func:`archivey.open_archive` call. Returns ``None`` when the reader was not
        opened inside an ``enable_measurement()`` context.
        """
        if not self._measure:
            return None
        from archivey.measurement import IoStats

        c_bytes = self._compressed_input_counter
        return IoStats(
            bytes_decompressed=self.bytes_decompressed,
            compressed_bytes_consumed=c_bytes.bytes_read
            if c_bytes is not None
            else None,
            source_seek_count=self.source_seek_count,
        )


class _ProgressivePassIterator(Iterator[ArchiveMember]):
    """Instance-held streaming pass.

    A generator would be closed (and its post-loop tail skipped) when a consumer
    breaks out of ``for member in reader``; this iterator survives early exit so
    :meth:`BaseArchiveReader.scan_members` can drain the remainder.
    """

    def __init__(self, reader: BaseArchiveReader) -> None:
        self._reader = reader
        self._members_source = reader._iter_members()
        self._next_id = 0
        self._exhausted = False
        self._error: BaseException | None = None

    def __iter__(self) -> _ProgressivePassIterator:
        return self

    def __next__(self) -> ArchiveMember:
        if self._error is not None:
            # The pass previously failed. Its generator is closed, so a plain retry
            # would see StopIteration and finalize the PARTIAL scan as the complete,
            # resolved member cache — scan_members() would then silently return a
            # truncated listing after the caller caught the original error. Fail loud
            # and keep the cache unpublished instead.
            err = ReadError(
                "The archive scan previously failed "
                f"({type(self._error).__name__}); the member list is incomplete and "
                "cannot be resumed. Reopen the archive to retry."
            )
            raise err from self._error
        if self._exhausted:
            raise StopIteration
        try:
            member = next(self._members_source)
        except StopIteration:
            self._exhausted = True
            try:
                self._reader._finalize_pass_links()
            except BaseException as exc:
                # Listing-limit refusal (or link finalize failure) must not leave a
                # half-published cache retryable as success via a second StopIteration.
                self._error = exc
                raise
            raise
        except (CorruptionError, TruncatedError) as exc:
            self._exhausted = True
            try:
                self._reader._finalize_pass_links(error=exc)
            except BaseException as finalize_exc:
                # Non-archive-damage finalize failures (e.g. listing-limit refusal) must
                # not leave a half-published cache. Secondary Corruption/Truncated during
                # link finalization after terminal listing damage is swallowed inside
                # _finalize_links so the recovered prefix stays published.
                self._error = finalize_exc
                raise
            raise
        except BaseException as exc:
            self._error = exc
            raise
        idx = self._next_id
        self._next_id += 1
        try:
            self._reader._stamp_progressive_member(idx, member)
        except BaseException as exc:
            # Registration/link bookkeeping failed mid-pass (e.g. a RAISE-disposition
            # diagnostic or ListingLimits): the pass state is inconsistent, so poison
            # it the same way.
            self._error = exc
            raise
        return member


class _TranslatedErrorBoundary:
    """``with``-boundary that applies ``_raise_translated`` to an escaping exception.

    A plain ``__exit__`` class (not a ``@contextmanager`` generator) so the exception
    handling is explicit: ``BaseException`` that is not an ``Exception`` always passes
    through untouched; an exception ``_raise_translated`` re-raises *unchanged* (already
    typed, or unrecognized by the translator) propagates as the original — with context
    stamped where applicable — and only a genuinely translated exception replaces it,
    chained via ``from``.
    """

    __slots__ = ("_reader", "_member_name", "_stamp_encryption")

    def __init__(
        self,
        reader: BaseArchiveReader,
        member_name: str | None,
        stamp_encryption: bool,
    ) -> None:
        self._reader = reader
        self._member_name = member_name
        self._stamp_encryption = stamp_encryption

    def __enter__(self) -> None:
        return None

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> bool:
        if exc is None or not isinstance(exc, Exception):
            return False
        try:
            self._reader._raise_translated(
                exc,
                self._member_name,
                stamp_encryption=self._stamp_encryption,
            )
        except BaseException as raised:
            if raised is exc:
                # Already typed (stamped in place) or unrecognized: let the ORIGINAL
                # propagate from the with-body rather than re-raising from here, which
                # would tack an extra frame onto its traceback.
                return False
            raise


class _InternalMemberOpens:
    """``with``-boundary marking library-internal opens, exempt from the live-stream gate.

    A plain ``__enter__``/``__exit__`` class, matching ``_TranslatedErrorBoundary``
    above: this runs on every eager link-data read and every ``extract_all``, so it
    allocates one small object rather than a generator and a context manager per call.
    """

    __slots__ = ("_state",)

    def __init__(self, state: ReaderState) -> None:
        self._state = state

    def __enter__(self) -> None:
        self._state.begin_internal_opens()

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self._state.end_internal_opens()
