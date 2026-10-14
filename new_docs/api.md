# API reference

This page lists every public name in archivey, grouped by what you use it for. You can import
each one from `archivey`, except the names in the last two groups, which live in their own
modules. The other pages explain when to use them, starting with [Opening an
archive](opening.md).

## Opening

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

## Readers and streams

`open_archive` returns an `ArchiveReader` unless you pass `streaming=True`. Then its type is
`ForwardArchiveReader`, which reads the archive in one pass with `stream_members()`, and calling
`members`, `get`, `read` or `open` raises `ArchiveyUsageError` ([Reading
once](reading.md#reading-once)).

::: archivey.ForwardArchiveReader
::: archivey.ArchiveReader
::: archivey.ArchiveStream
::: archivey.MemberSelector
::: archivey.MemberListReport

## Members and archive details

::: archivey.ArchiveMember
::: archivey.ArchiveInfo

::: archivey.MemberExtra
    options:
      members:
        - __getitem__

::: archivey.ArchiveInfoExtra
    options:
      members:
        - __getitem__

## Formats and member types

::: archivey.ArchiveFormat
::: archivey.ContainerFormat
::: archivey.StreamFormat
::: archivey.MemberType
::: archivey.CompressionMethod
::: archivey.CompressionAlgorithm
::: archivey.HashAlgorithm
::: archivey.crc32_digest
::: archivey.CreateSystem

## Extraction

::: archivey.ExtractionReport
::: archivey.ExtractionResult
::: archivey.ExtractionStatus
::: archivey.NameRewrite
::: archivey.ExtractionProgress
::: archivey.ExtractionPolicy
::: archivey.OverwritePolicy
::: archivey.OnError
::: archivey.AbortOn
::: archivey.MemberFilter
::: archivey.sanitize_names

## Configuration and passwords

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

## Diagnostics

::: archivey.Diagnostic
::: archivey.DiagnosticContext
::: archivey.DiagnosticCode
::: archivey.DiagnosticPolicy
::: archivey.ARCHIVE_INTEGRITY_CODES
::: archivey.DiagnosticSummary
::: archivey.OnDiagnostic

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

## Access cost

::: archivey.CostReceipt
::: archivey.ListingCost
::: archivey.AccessCost
::: archivey.StreamCapability

## Detection budgets

These live in the `archivey.detection_cost` module. A budget bounds what
[`detect_format`][archivey.detect_format] and `open_archive` may read and decode before they
answer. You set it with `ArchiveyConfig(detection_budget=...)`, and `BALANCED_BUDGET` is the
default. The receipt on [`FormatInfo.cost_receipt`][archivey.FormatInfo] says what detection
actually did.

::: archivey.detection_cost.DetectionBudget
::: archivey.detection_cost.DetectionBudgetPreset
::: archivey.detection_cost.BALANCED_BUDGET
::: archivey.detection_cost.FAST_BUDGET
::: archivey.detection_cost.THOROUGH_BUDGET
::: archivey.detection_cost.DetectionCostReceipt
::: archivey.detection_cost.TierSkip
::: archivey.detection_cost.TierSkipReason

## Display and naming helpers

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

It also holds two functions that turn patterns into a `members=` argument, for
selecting by pattern where `members=` itself matches names exactly. They read the same
pattern differently: `match_glob` keeps `*` inside one folder, as archivey's CLI does,
and `match_tar_glob` follows `tar`, where `*` also matches `/`.

::: archivey.paths.match_glob
::: archivey.paths.match_tar_glob
