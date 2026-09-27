# error-handling — fewer exception types

## MODIFIED Requirements

### Requirement: Single rooted archive exception hierarchy

The system SHALL define every library-detected archive/environment failure under
this exact `ArchiveyError` hierarchy:

```text
ArchiveyError(Exception)
├── OpenError
│   ├── FormatDetectionError
│   └── StreamNotSeekableError
├── ReadError
│   ├── CorruptionError
│   ├── TruncatedError
│   ├── EncryptionError
│   └── LinkTargetNotFoundError
├── ExtractionError
│   ├── FilterRejectionError
│   ├── NameCollisionError            raised only under abort_on=
│   └── NameRewrittenError            raised only under abort_on=
├── ResourceLimitError
├── UnsupportedFeatureError
├── PackageNotInstalledError
└── DiagnosticRaisedError
```

Subclass boundaries SHALL keep their existing meanings:
`UnsupportedFeatureError` / `PackageNotInstalledError` may occur at open or read
time, and `StreamNotSeekableError` is an `OpenError`. `OpenError` means reading could
not start (no recognized format, a non-seekable source the format needs to seek, a
volume file that cannot be opened); a recognized archive whose header is damaged, cut
short or encrypted raises a `ReadError` subclass, from `open_archive()` as from any
later call. `DiagnosticRaisedError` is direct
because advisory escalation can happen during detection, open, read, stream, or
extraction. `ResourceLimitError` is direct because configurable resource caps can
trip during listing materialization, extraction bomb guarding, opening a member's
codec, or copying a stream source to temporary storage; it is not an
`ExtractionError` subclass.

A type SHALL be public only when a caller would act on it differently from its parent;
anything finer goes in the message. So the checks behind `FilterRejectionError` (path
traversal, symlink escape, special file, unportable name, deceptive name) share that one
type, and a `SpoolLimits` trip is a plain `ResourceLimitError`.

| Error split | Meaning |
| --- | --- |
| `UnsupportedFeatureError` | Valid archive uses a recognized feature Archivey does not implement (unsupported ZIP method, unknown 7z coder, a 7z coder graph that is not a tree of chains, a raw CD sector image), or the archive or backend cannot serve a valid request (writing any format, a RAR password with a line break for `unrar`, `format=ArchiveFormat.UNKNOWN`). |
| `PackageNotInstalledError` | A package or external tool the format or member needs is absent: at open for a format whose backend or single codec is missing (ISO without `pycdlib`), at read for one member's codec. |
| `ResourceLimitError` | A configured resource limit was exceeded (`ListingLimits` materialization caps, `ExtractionLimits` bomb guards, a `DecoderLimits` cap on archive-declared decoder memory or key-derivation work, or `SpoolLimits`). |

#### Scenario: archive exception matrix

| Case | Expected |
| --- | --- |
| Any open/read/extract/write failure detected by Archivey | Instance of `ArchiveyError`; `except ArchiveyError` catches it |
| Diagnostic policy escalates | `DiagnosticRaisedError` is caught by `except ArchiveyError` |
| Member name with a bidi override, extracted | `FilterRejectionError` whose message names the override; caught by `except ExtractionError` |
| Recognized archive with a damaged header, opened | `CorruptionError` or `TruncatedError` from `open_archive()`; not an `OpenError` |

### Requirement: Caller misuse remains outside ArchiveyError

The system SHALL define `ArchiveyUsageError(Exception)` outside `ArchiveyError`
for detected caller-code bugs. `except ArchiveyError` MUST NOT swallow misuse.

`ArchiveyUsageError` SHALL be raised when a second member stream opens while another is
live on a reader opened without `concurrent_members=True`. Its message SHALL include the recorded `open_archive()` call
site (`file:line`) and SHALL name `concurrent_members=True` as the parameter that would
have allowed the operation.

`ArchiveyUsageError` SHALL also cover:

- reader-wide exclusive pass overlap (`__iter__`, `stream_members`,
  `extract_all`, materialization, active worker calls);
- close overlapping an active worker call without declared concurrency;
- any reader operation/property after `close()` except repeated `close()` /
  `__exit__`;
- same-reader password-provider reentry into password-requiring work;
- using an `ArchiveMember` from another reader;
- member I/O after a caller closes its supplied source early;
- `open_archive(streaming=True, concurrent_members=True)`;
- a random-access or second-pass call on a `streaming=True` reader (`members()`,
  `get()`, `open()`, `read()`, a second `__iter__` / `stream_members()` /
  `extract_all()`), since the caller chose the mode;
- driving a reader or stream from inside a diagnostic callback it is emitting;
- `open_stream()` given a `format=` that is not a compressed stream;
- `open()` / `read()` of a resolved non-payload member (`DIRECTORY`, `ANTI`,
  `OTHER`). A symlink/hardlink that fails to resolve remains
  `LinkTargetNotFoundError` (`ArchiveyError`) — that is an archive property,
  not caller misuse. A link that resolves to a non-`FILE` then hits the
  non-payload rule above.

The later operation SHALL fail before changing state and MUST leave the earlier
operation/stream usable. Internal owner-child operations are exempt only through
explicit internal tokens; public reentry does not inherit them. Closed stream I/O
continues to raise `ValueError`, and unsupported stream positioning continues to
raise `io.UnsupportedOperation`.

#### Scenario: usage error matrix

| Case | Expected |
| --- | --- |
| `except ArchiveyError` wraps code that raises `ArchiveyUsageError` | Usage error propagates past the handler |
| Second overlapping member stream without `concurrent_members=True` | `ArchiveyUsageError` with open-site `file:line` naming `concurrent_members=True`; first stream still readable |
| Exclusive pass/materialization is active and conflicting public op begins | Later op raises `ArchiveyUsageError`; active op remains valid |
| Operation/property after `reader.close()` | `ArchiveyUsageError`; already-open member stream follows lifecycle lease |
| Repeated `reader.close()` | No error; no repeated backend teardown |
| Unsupported `seek()` on a stream | `io.UnsupportedOperation`, not archivey-typed |
