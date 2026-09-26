# error-handling — a spool trip has its own type

## MODIFIED Requirements

### Requirement: Single rooted archive exception hierarchy

The system SHALL define every library-detected archive/environment failure under
this exact `ArchiveyError` hierarchy:

```text
ArchiveyError(Exception)
├── OpenError
│   ├── FormatDetectionError
│   ├── UnsupportedFormatError
│   └── StreamNotSeekableError
├── ReadError
│   ├── CorruptionError
│   ├── TruncatedError
│   ├── EncryptionError
│   └── LinkTargetNotFoundError
├── ExtractionError
│   ├── FilterRejectionError
│       ├── PathTraversalError
│       ├── SymlinkEscapeError
│       ├── SpecialFileError
│       ├── UnportableNameError
│       └── DeceptiveNameError
│   ├── NameCollisionError            raised only under abort_on=
│   └── NameRewrittenError            raised only under abort_on=
├── ResourceLimitError
│   └── SpoolLimitExceededError
├── UnsupportedFeatureError
├── PackageNotInstalledError
├── UnsupportedOperationError
└── DiagnosticRaisedError
```

Subclass boundaries SHALL keep their existing meanings:
`UnsupportedFeatureError` / `PackageNotInstalledError` may occur at open or read
time, `StreamNotSeekableError` is an `OpenError`, and
`UnsupportedOperationError` describes an archive/backend/access-mode operation
that cannot be provided, not a caller-code bug. `DiagnosticRaisedError` is direct
because advisory escalation can happen during detection, open, read, stream, or
extraction. `ResourceLimitError` is direct because configurable resource caps can
trip during listing materialization, extraction bomb guarding, opening a member's
codec, or copying a stream source to temporary storage; it is not an
`ExtractionError` subclass. `SpoolLimitExceededError` is the `SpoolLimits` trip, a
`ResourceLimitError` of its own type so a caller can tell a refused copy from the other
configured limits while `except ResourceLimitError` still catches it.

| Error split | Meaning |
| --- | --- |
| `UnsupportedOperationError` | Valid API call against a reader/backend/mode that cannot provide the requested operation: random access on `streaming=True`, write through read-only RAR. Post-close use is `ArchiveyUsageError` (below). |
| `UnsupportedFeatureError` | Valid archive uses a recognized feature Archivey does not implement: unsupported ZIP method, AES ZIP entry, 7z BCJ2, unknown coder. |
| `ResourceLimitError` | A configured resource limit was exceeded (`ListingLimits` materialization caps, `ExtractionLimits` bomb guards, a `DecoderLimits` cap on archive-declared decoder memory or key-derivation work, or `SpoolLimits`). |
| `SpoolLimitExceededError` | The `SpoolLimits.max_bytes` cap refused a copy of the archive source to temporary storage. |

The three name-related `FilterRejectionError` subclasses are kept apart because a caller
triaging a batch of rejections acts differently on each:

| Error split | Meaning |
| --- | --- |
| `PathTraversalError` | The name tries to reach **outside** the destination, or cannot name a path at all (`..`, absolute, NUL, unencodable). |
| `UnportableNameError` | The name cannot be written **as spelled** on this platform, and the policy declined to rewrite it. |
| `DeceptiveNameError` | The name is writable and stays inside the destination, but is built to **display as something other than what it is** — a bidi override or isolate. Nothing is wrong with the archive or the platform; the name is a lie. |

#### Scenario: archive exception matrix

| Case | Expected |
| --- | --- |
| Any open/read/extract/write failure detected by Archivey | Instance of `ArchiveyError`; `except ArchiveyError` catches it |
| Diagnostic policy escalates | `DiagnosticRaisedError` is caught by `except ArchiveyError` |
| Member name with a bidi override, extracted | `DeceptiveNameError`; caught by `except FilterRejectionError` and by `except ExtractionError` |
