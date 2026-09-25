# API reference

Everything documented here is re-exported from the top-level `archivey` package and
listed in `archivey.__all__`, except the [front-end helpers](#front-end-helpers) at the
end, which are imported from `archivey.terminal`. Narrative guide: [Home](index.md).
Authoritative contracts: `openspec/specs/`.

## Opening archives

::: archivey.open_archive
::: archivey.open_stream
::: archivey.extract
::: archivey.detect_format
::: archivey.FormatInfo
::: archivey.DetectionConfidence
::: archivey.format_availability
::: archivey.FormatAvailability
::: archivey.FormatSupport
::: archivey.MissingComponent
::: archivey.list_supported_formats
::: archivey.list_known_formats

## The reader interface

::: archivey.ArchiveReader
::: archivey.ArchiveStream
::: archivey.MemberSelector
::: archivey.MemberStreams

## Data model

::: archivey.ArchiveMember
::: archivey.ArchiveInfo

## Extra bags

::: archivey.MemberExtra
    options:
      members:
        - __getitem__

::: archivey.ArchiveInfoExtra
    options:
      members:
        - __getitem__

::: archivey.ArchiveFormat
::: archivey.ContainerFormat
::: archivey.StreamFormat
::: archivey.MemberType
::: archivey.HashAlgorithm
::: archivey.crc32_digest
::: archivey.CompressionAlgorithm
::: archivey.CompressionMethod
::: archivey.CreateSystem

## Diagnostics

Structured advisories (formerly log-only warnings). See the `diagnostics` capability
spec for lifecycle, retention, and policy.

::: archivey.Diagnostic
::: archivey.DiagnosticContext
::: archivey.DiagnosticCode
::: archivey.DiagnosticSeverity
::: archivey.DiagnosticDisposition
::: archivey.DiagnosticPolicy
::: archivey.ARCHIVE_INTEGRITY_CODES
::: archivey.DiagnosticSummary
::: archivey.OnDiagnostic
::: archivey.ExtractionReport
::: archivey.MemberListReport

## Extraction

::: archivey.ExtractionResult
::: archivey.ExtractionProgress
::: archivey.ExtractionStatus
::: archivey.ExtractionPolicy
::: archivey.OverwritePolicy
::: archivey.OnError
::: archivey.AbortOn
::: archivey.MemberFilter

## Configuration

::: archivey.ArchiveyConfig
::: archivey.DEFAULT_ARCHIVEY_CONFIG
::: archivey.ExtractionLimits
::: archivey.ListingLimits
::: archivey.DecoderLimits
::: archivey.AcceleratorMode
::: archivey.PasswordInput
::: archivey.PasswordRequest
::: archivey.PasswordProvider

## Access cost

::: archivey.CostReceipt
::: archivey.ListingCost
::: archivey.AccessCost
::: archivey.StreamCapability

## Measurement

::: archivey.IoStats
::: archivey.enable_measurement

## Errors

archivey's exceptions have two roots. `ArchiveyError` covers problems with the archive
or its environment. `ArchiveyUsageError` covers mistakes in the calling code and is
deliberately outside that tree, so `except ArchiveyError` does not hide them. The
entries below follow the class tree: each group starts with its base class, except the
group from `ResourceLimitError` to `DiagnosticRaisedError`. Those five are direct
subclasses of `ArchiveyError` and unrelated to each other.
[Errors and diagnostics](errors-and-diagnostics.md) explains which one to catch.

::: archivey.ArchiveyError

::: archivey.OpenError
::: archivey.FormatDetectionError
::: archivey.UnsupportedFormatError
::: archivey.StreamNotSeekableError

::: archivey.ReadError
::: archivey.CorruptionError
::: archivey.TruncatedError
::: archivey.EncryptionError
::: archivey.LinkTargetNotFoundError

::: archivey.ExtractionError
::: archivey.FilterRejectionError
::: archivey.PathTraversalError
::: archivey.SymlinkEscapeError
::: archivey.SpecialFileError
::: archivey.UnportableNameError
::: archivey.DeceptiveNameError
::: archivey.NameCollisionError
::: archivey.NameRewrittenError

::: archivey.ResourceLimitError
::: archivey.UnsupportedFeatureError
::: archivey.PackageNotInstalledError
::: archivey.UnsupportedOperationError
::: archivey.DiagnosticRaisedError

::: archivey.ArchiveyUsageError
::: archivey.ConcurrentAccessError

## Front-end helpers

These live in the `archivey.terminal` module and are not re-exported from `archivey`.
They are for showing archive-derived text, such as member names, to a person without
letting it control the terminal. archivey's own command-line tool is built on them.

::: archivey.terminal.escape_control_chars
::: archivey.terminal.display_path
::: archivey.terminal.quoted
