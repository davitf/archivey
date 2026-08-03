# Errors and diagnostics

What gets raised, what gets recorded, and how to tell them apart.

Every failure that comes from the archive or its environment derives from
[`ArchiveyError`][archivey.ArchiveyError], so one `except` covers them all:

```python
from archivey import open_archive, ArchiveyError

try:
    with open_archive("maybe.7z") as reader:
        reader.extract_all("out/")
except ArchiveyError as e:
    print("could not process archive:", e)
```

React to specific cases with the subtypes:

| Exception | Raised when |
| --- | --- |
| `OpenError` | the source can't be opened — `FormatDetectionError` (unknown format), `UnsupportedFormatError`, `StreamNotSeekableError` (random-access open on a pipe) |
| `EncryptionError` | a password is required, missing, or wrong |
| `CorruptionError` / `TruncatedError` | the archive is malformed or cut short |
| `PackageNotInstalledError` | an optional package or tool is absent (e.g. the `unrar` binary for RAR data) |
| `FilterRejectionError` | extraction blocked an unsafe member — `PathTraversalError`, `SymlinkEscapeError`, `SpecialFileError` |
| `ResourceLimitError` | a listing/extraction safety limit (member count, size) was exceeded |

Mistakes in **your** code are deliberately kept out of that hierarchy: opening a second
overlapping stream without `concurrent_members=True`, using a closed reader, and similar
misuse raise [`ArchiveyUsageError`][archivey.ArchiveyUsageError] (e.g.
`ConcurrentAccessError`), which is **not** an `ArchiveyError` — so a blanket
`except ArchiveyError` never silently swallows a bug. (When an *archive* genuinely can't
provide an operation — seeking a non-seekable member, a format that can't list — that is a
real `ArchiveyError`: `UnsupportedOperationError`.)

## Diagnostics

Structured advisories are queryable on the reader and on the extraction report — not
only in logs. Prefer `reader.diagnostics` and the returned `ExtractionReport` over
hoping something appeared in a log handler. See the `diagnostics` capability and the
[API reference](api.md).
