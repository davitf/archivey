"""Archivey — Python library for reading, streaming, and safely extracting archives.

Public surface layout (this package root only — not ``internal`` / ``cli``):

- :mod:`archivey.core` — ``open_archive`` / ``open_stream`` / ``extract`` / detection
- :mod:`archivey.reader` — ``ArchiveReader`` ABC
- :mod:`archivey.types` — formats, members, compression
- :mod:`archivey.config` — ``ArchiveyConfig``, limits, passwords, accelerators
- :mod:`archivey.cost` — listing/access cost receipt
- :mod:`archivey.diagnostics` — advisory codes, summaries, extraction reports
- :mod:`archivey.exceptions` — error hierarchy
- :mod:`archivey.measurement` — optional I/O counters

Names in ``__all__`` are the documented API. A few advanced types are also imported
here (so ``from archivey import …`` keeps working) but omitted from ``__all__`` so
they do not crowd the generated API reference — see the ``# noqa: F401`` imports.
"""

# Declared here so type checkers see a ``str``; the value comes from the module
# ``__getattr__`` at the end of this file, on first access.
__version__: str

from archivey.config import (
    DEFAULT_ARCHIVEY_CONFIG,
    RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE,  # noqa: F401 — advanced; not in __all__
    AcceleratorMode,
    ArchiveyConfig,
    DecoderLimits,
    ExtractionLimits,
    ListingLimits,
    PasswordInput,
    PasswordProvider,
    PasswordRequest,
)
from archivey.core import (
    DetectionConfidence,
    FormatAvailability,
    FormatInfo,
    FormatSupport,
    MissingComponent,
    detect_format,
    extract,
    format_availability,
    list_known_formats,
    list_supported_formats,
    open_archive,
    open_stream,
)
from archivey.cost import (
    AccessCost,
    CostReceipt,
    ListingCost,
    StreamCapability,
)
from archivey.diagnostics import (
    ARCHIVE_INTEGRITY_CODES,
    # Context payloads: importable for isinstance/match; omitted from __all__.
    ArchiveEofContext,  # noqa: F401
    Diagnostic,
    DiagnosticCode,
    DiagnosticContext,
    DiagnosticDisposition,
    DiagnosticPolicy,
    DiagnosticSeverity,
    DiagnosticSummary,
    DigestContext,  # noqa: F401
    EmptyArchiveContext,  # noqa: F401
    ExtractionReport,
    FormatConflictContext,  # noqa: F401
    MemberHeaderRecordContext,  # noqa: F401
    MemberListReport,
    MemberNameControlsContext,  # noqa: F401
    MemberTimestampContext,  # noqa: F401
    NameEncodingContext,  # noqa: F401
    NameNormalizationContext,  # noqa: F401
    OnDiagnostic,
    ScanRaceContext,  # noqa: F401
    SeekIndexContext,  # noqa: F401
    StreamRewindContext,  # noqa: F401
    SymlinkTargetContext,  # noqa: F401
    UnconfirmedFormatContext,  # noqa: F401
    UnusedArgumentContext,  # noqa: F401
)
from archivey.exceptions import (
    ArchiveyError,
    ArchiveyUsageError,
    ConcurrentAccessError,
    CorruptionError,
    DeceptiveNameError,
    DiagnosticRaisedError,
    EncryptionError,
    ExtractionError,
    FilterRejectionError,
    FormatDetectionError,
    LinkTargetNotFoundError,
    NameCollisionError,
    NameRewrittenError,
    OpenError,
    PackageNotInstalledError,
    PathTraversalError,
    ReadError,
    ResourceLimitError,
    SpecialFileError,
    StreamNotSeekableError,
    SymlinkEscapeError,
    TruncatedError,
    UnportableNameError,
    UnsupportedFeatureError,
    UnsupportedFormatError,
    UnsupportedOperationError,
    WriteError,  # noqa: F401 — write API not shipped yet; kept importable
)
from archivey.internal.extraction_types import (
    AbortOn,
    ExtractionPolicy,
    ExtractionProgress,
    ExtractionResult,
    ExtractionStatus,
    MemberFilter,
    OnError,
    OverwritePolicy,
)
from archivey.internal.streams.archive_stream import ArchiveStream
from archivey.measurement import IoStats, enable_measurement
from archivey.reader import ArchiveReader, MemberSelector
from archivey.types import (
    ArchiveFormat,
    ArchiveInfo,
    ArchiveInfoExtra,
    ArchiveMember,
    CompressionAlgorithm,
    CompressionMethod,
    ContainerFormat,
    CreateSystem,
    HashAlgorithm,
    MemberExtra,
    MemberStreams,
    MemberType,
    StreamFormat,
    crc32_digest,
)

__all__ = [
    "__version__",
    "open_archive",
    "open_stream",
    "extract",
    "ArchiveyConfig",
    "DEFAULT_ARCHIVEY_CONFIG",
    "DecoderLimits",
    "ExtractionLimits",
    "ListingLimits",
    "AcceleratorMode",
    "PasswordInput",
    "PasswordRequest",
    "PasswordProvider",
    "OnDiagnostic",
    "ExtractionPolicy",
    "OverwritePolicy",
    "OnError",
    "AbortOn",
    "ExtractionStatus",
    "ExtractionProgress",
    "ExtractionResult",
    "ExtractionReport",
    "MemberListReport",
    "MemberSelector",
    "MemberFilter",
    "detect_format",
    "FormatInfo",
    "DetectionConfidence",
    "format_availability",
    "list_supported_formats",
    "list_known_formats",
    "FormatSupport",
    "FormatAvailability",
    "MissingComponent",
    "ArchiveReader",
    "ArchiveStream",
    "ArchiveFormat",
    "ContainerFormat",
    "StreamFormat",
    "ArchiveMember",
    "ArchiveInfo",
    "ArchiveInfoExtra",
    "MemberExtra",
    "MemberType",
    "MemberStreams",
    "CompressionAlgorithm",
    "CompressionMethod",
    "CreateSystem",
    "HashAlgorithm",
    "crc32_digest",
    "CostReceipt",
    "ListingCost",
    "AccessCost",
    "StreamCapability",
    "IoStats",
    "enable_measurement",
    "Diagnostic",
    "DiagnosticCode",
    "DiagnosticContext",
    "DiagnosticSeverity",
    "DiagnosticDisposition",
    "DiagnosticPolicy",
    "DiagnosticSummary",
    "ARCHIVE_INTEGRITY_CODES",
    "DiagnosticRaisedError",
    "ArchiveyError",
    "ArchiveyUsageError",
    "ConcurrentAccessError",
    "OpenError",
    "FormatDetectionError",
    "UnsupportedFormatError",
    "StreamNotSeekableError",
    "ReadError",
    "CorruptionError",
    "TruncatedError",
    "EncryptionError",
    "LinkTargetNotFoundError",
    "ExtractionError",
    "FilterRejectionError",
    "PathTraversalError",
    "SymlinkEscapeError",
    "SpecialFileError",
    "UnportableNameError",
    "DeceptiveNameError",
    "NameCollisionError",
    "NameRewrittenError",
    "ResourceLimitError",
    "UnsupportedFeatureError",
    "PackageNotInstalledError",
    "UnsupportedOperationError",
]

# Eager backend registration so list_supported_formats / format_availability work
# immediately after `import archivey` (open_archive also imports as a safety net).
import archivey.internal.backends  # noqa: E402,F401


def _pin_public_module() -> None:
    """Report ``archivey`` as the module of every public name defined under ``internal``.

    Seventeen names in ``__all__`` (the extraction types, detection, the registry
    queries, ``ArchiveStream``, ``enable_measurement``) are defined under
    ``archivey.internal``. ``pickle`` records a class's ``__module__``, so an
    ``ExtractionResult`` or a policy enum persisted by a caller would otherwise name
    ``archivey.internal.extraction_types`` — a path that could then never move without
    breaking their data. Pinned here, it names ``archivey``, which is stable, and
    ``repr()``, ``help()`` and ``inspect.getmodule`` say the same. The internal layout
    stays free to change; ``internal`` imports still see the same objects.

    Computed over ``__all__`` rather than listed, so a name added later is covered too.
    Only classes and functions: an instance reports its class's module, and pinning it
    would set an attribute on the instance (or fail, on a frozen dataclass).

    Two lookups go through a class's ``__module__`` and would follow the pin to the
    wrong namespace:

    - **Annotations.** Under ``from __future__ import annotations`` a class's hints are
      strings, evaluated against ``sys.modules[cls.__module__]``, where ``Path`` or
      ``DetectionCostReceipt`` are not names. So each class's own hints are resolved
      *before* the pin, and ``typing.get_type_hints`` keeps working. A hint that cannot
      resolve here (a ``TYPE_CHECKING``-only name, or an attribute of such a module) is
      left as it was rather than failing the import; ``tests/test_public_api.py``
      catches it instead.
    - **Source.** ``inspect.getsource`` finds a class's file through its module, and
      there is no way to point it elsewhere. On a pinned class it raises ``OSError``;
      ``__firstlineno__`` is dropped so Python 3.13+ raises too, instead of returning
      lines of this file. Functions are unaffected: they carry their own code object.
    """
    import typing
    from types import FunctionType

    namespace = globals()
    for name in __all__:
        if name == "__version__":
            continue  # a str, and not bound until first access (``__getattr__`` below)
        obj = namespace[name]
        if not isinstance(obj, (type, FunctionType)):
            continue
        if not obj.__module__.startswith("archivey.internal"):
            continue
        if isinstance(obj, type):
            own = obj.__dict__.get("__annotations__")
            if own:
                try:
                    hints = typing.get_type_hints(obj, include_extras=True)
                except (NameError, AttributeError):
                    pass
                else:
                    obj.__annotations__ = {key: hints[key] for key in own}
            if "__firstlineno__" in obj.__dict__:
                delattr(obj, "__firstlineno__")
        obj.__module__ = __name__


_pin_public_module()
del _pin_public_module


def __getattr__(name: str) -> object:
    """Compute ``__version__`` on first access (PEP 562).

    ``importlib.metadata`` is a measurable part of ``import archivey`` on a core-only
    install, for a string most callers never read. (Some optional codec packages import
    it for their own version, so with those installed the saving is smaller.) The value
    is cached in the module globals, so this runs once; the import stays inside the
    function so nothing from it reaches the public namespace.
    """
    if name == "__version__":
        from importlib.metadata import PackageNotFoundError, version

        try:
            value = version("archivey")
        except PackageNotFoundError:
            value = "0.0.0+unknown"
        globals()["__version__"] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
