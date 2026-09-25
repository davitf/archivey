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
::: archivey.DiagnosticCode
::: archivey.DiagnosticSeverity
::: archivey.DiagnosticDisposition
::: archivey.DiagnosticPolicy
::: archivey.DiagnosticSummary
::: archivey.OnDiagnostic
::: archivey.ExtractionReport
::: archivey.MemberListReport

## Extraction

::: archivey.ExtractionResult
::: archivey.ExtractionStatus
::: archivey.ExtractionPolicy
::: archivey.OverwritePolicy
::: archivey.OnError
::: archivey.AbortOn
::: archivey.MemberFilter

## Configuration

::: archivey.ArchiveyConfig
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

::: archivey.ArchiveyError
::: archivey.ResourceLimitError
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
