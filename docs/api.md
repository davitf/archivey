# API reference

Everything documented here is re-exported from the top-level `archivey` package and
listed in `archivey.__all__`, except three side modules at the end: the
[detection budget and receipt](#detection-cost) types in `archivey.detection_cost`, and
the [front-end helpers](#front-end-helpers) in `archivey.terminal` and `archivey.paths`.
Narrative guide:
[Home](index.md).
Authoritative contracts: `openspec/specs/`.

## Opening archives

::: archivey.open_archive
::: archivey.open_stream
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

::: archivey.ForwardArchiveReader
::: archivey.ArchiveReader
::: archivey.ArchiveStream
::: archivey.MemberSelector

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
::: archivey.NameRewrite
::: archivey.ExtractionPolicy
::: archivey.OverwritePolicy
::: archivey.OnError
::: archivey.AbortOn
::: archivey.MemberFilter
::: archivey.sanitize_names

## Configuration

::: archivey.ArchiveyConfig
::: archivey.DEFAULT_ARCHIVEY_CONFIG
::: archivey.ExtractionLimits
::: archivey.ListingLimits
::: archivey.DecoderLimits
::: archivey.SpoolLimits
::: archivey.AcceleratorMode
::: archivey.RarDecompressor
::: archivey.PasswordInput
::: archivey.PasswordRequest
::: archivey.PasswordProvider

## Access cost

::: archivey.CostReceipt
::: archivey.ListingCost
::: archivey.AccessCost
::: archivey.StreamCapability

## Errors

archivey's exceptions have two roots. `ArchiveyError` covers problems with the archive
or its environment. `ArchiveyUsageError` covers mistakes in the calling code and is
deliberately outside that tree, so `except ArchiveyError` does not hide them. The
entries below follow the class tree: each group starts with its base class, except the
group from `ResourceLimitError` to `DiagnosticRaisedError`. Those four are direct
subclasses of `ArchiveyError` and unrelated to each other.
[Errors and diagnostics](errors-and-diagnostics.md) explains which one to catch.

::: archivey.ArchiveyError

::: archivey.OpenError
::: archivey.FormatDetectionError
::: archivey.StreamNotSeekableError

::: archivey.ReadError
::: archivey.CorruptionError
::: archivey.TruncatedError
::: archivey.EncryptionError
::: archivey.LinkTargetNotFoundError
::: archivey.LinkTargetNotFoundReason

::: archivey.ExtractionError
::: archivey.FilterRejectionError
::: archivey.NameCollisionError
::: archivey.NameRewrittenError

::: archivey.ResourceLimitError
::: archivey.UnsupportedFeatureError
::: archivey.PackageNotInstalledError
::: archivey.DiagnosticRaisedError

::: archivey.ArchiveyUsageError

## Detection cost

These live in the `archivey.detection_cost` module and are not re-exported from
`archivey`. A budget bounds what [`detect_format`][archivey.detect_format] and
`open_archive` may read and decode before they answer; it is set on
`ArchiveyConfig(detection_budget=...)`, `BALANCED_BUDGET` by default. The receipt on
[`FormatInfo.cost_receipt`][archivey.FormatInfo] says what detection actually did. Both
are stable under the same rule as the rest of this page.

::: archivey.detection_cost.DetectionBudget
::: archivey.detection_cost.DetectionBudgetPreset
::: archivey.detection_cost.BALANCED_BUDGET
::: archivey.detection_cost.FAST_BUDGET
::: archivey.detection_cost.THOROUGH_BUDGET
::: archivey.detection_cost.DetectionCostReceipt
::: archivey.detection_cost.TierSkip
::: archivey.detection_cost.TierSkipReason

## Front-end helpers

These live in the `archivey.terminal` and `archivey.paths` modules and are not
re-exported from `archivey`. archivey's own command-line tool is built on them.

The `archivey.terminal` helpers are for showing archive-derived text, such as member
names, to a person without letting it control the terminal.

::: archivey.terminal.escape_control_chars
::: archivey.terminal.display_path
::: archivey.terminal.quoted

`archivey.paths` holds the naming rules extraction applies to destination paths, for a
front end that moves or renames what extraction wrote and wants the names a direct
extraction would have chosen.

::: archivey.paths.numbered_name
