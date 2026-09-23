## MODIFIED Requirements

### Requirement: Object-typed public arguments are refused at the boundary

Every public entry point SHALL raise `ArchiveyUsageError` — outside `ArchiveyError`,
per the misuse requirement above — when a non-enum argument is of a type it cannot
use, and SHALL do so at the call the caller wrote rather than wherever the value is
eventually read.

Enum-typed parameters are **out of scope of this requirement**, and this requirement
says nothing about how they behave. It covers the arguments listed below and no
others.

The refusal is what makes the boundary observable. Deferred to its point of use, a
wrong-typed argument surfaces as an `AttributeError`, a `TypeError`, or a `LookupError`
from library internals, naming a private attribute of ours (`'str' object has no
attribute 'max_extracted_bytes'`) and never naming the argument. Such a message both
leaks an internal field name and fails to say what the caller did wrong, which is the
no-internal-leakage rule.

The arguments covered:

| Entry point | Arguments |
| --- | --- |
| `open_archive()`, `open_stream()`, `extract()`, `detect_format()` | `config` |
| `detect_format()` | `budget` (a `DetectionBudget`; a `DetectionBudgetPreset` is an enum and out of scope) |
| `open_archive()`, `extract()` | `encoding`, `password` |
| `extract()`, `ArchiveReader.extract_all()` | `limits`, `on_progress` |
| `ArchiveReader.extract_all()`, `ArchiveReader.stream_members()` | `members` |
| `ArchiveReader.extract_all()` | `filter` |
| `ArchiveReader.open()` / `.read()` | `member` |
| `ArchiveyConfig(...)` | `extraction_limits`, `listing_limits`, `diagnostic_policy`, `on_diagnostic`, `zip_unflagged_fallback_encoding`, `max_retained_diagnostic_references` |
| `ExtractionLimits(...)`, `ListingLimits(...)` | every guard field |

`ArchiveyConfig` and the two `*Limits` types SHALL validate their own fields at
construction. Validating `config=` at an entry point does not reach them: the object
passed there is of the right type and the wrong one is a field in, and a limit is a
promise about an operation that has not begun, so construction is the last place a
message can still name what the caller wrote.

A value that silently disables a guard SHALL be refused on the same footing as a wrong
type: `None` on a field that is not optional, and a NaN or an infinity on a float
field, since nothing exceeds an infinity and every comparison against a NaN is false.
`None` remains the way to disable a guard deliberately, on the fields that allow it.

An `encoding` SHALL be refused unless it names a codec that can decode bytes to text,
so that a byte-to-byte transform (`"rot13"`, `"base64"`) is refused where it is written
rather than several frames into a member-name decode.

The raw exceptions the contract already permits SHALL continue to escape unchanged:
`KeyError` for an unknown member **name**, `TypeError` for `len()` / `in` and for a
wrong-typed `source` or `dest`, `io.UnsupportedOperation` for an unsupported `seek`,
`ValueError` for I/O on a closed stream, and `OSError`. Boolean flags read for their
truthiness are not covered, there being no wrong type to find.

#### Scenario: object argument refusal matrix

| Case | Expected |
| --- | --- |
| `open_archive(src, config="strict")` | `ArchiveyUsageError` naming `config`; never `AttributeError: 'str' object has no attribute 'diagnostic_policy'` |
| `ArchiveyConfig(extraction_limits="none")` then `extract(...)` | `ArchiveyUsageError` at construction, not `AttributeError` part-way through the extraction |
| `ArchiveyConfig(listing_limits="x")` then listing | `ArchiveyUsageError` at construction, not `AttributeError` mid-listing |
| `ArchiveyConfig(max_retained_diagnostic_references="x")` | `ArchiveyUsageError` at construction |
| `ArchiveyConfig(on_diagnostic=0)` | `ArchiveyUsageError` at construction, not when the first diagnostic fires |
| `ListingLimits(max_members="x")` | `ArchiveyUsageError` at construction, not `TypeError` mid-listing |
| `ExtractionLimits(max_ratio=float("nan"))` | `ArchiveyUsageError`; a NaN would leave the ratio guard switched off silently |
| `ExtractionLimits(ratio_activation_threshold=None)` | `ArchiveyUsageError`; the field is not optional and `None` disables nothing |
| `ExtractionLimits(max_extracted_bytes=True)` | `ArchiveyUsageError`; `bool` is an `int` subclass and would cap at one byte |
| `extract(src, dest, encoding="rot13")` | `ArchiveyUsageError` naming the argument; not a `LookupError` during a member-name decode |
| `extract(src, dest, encoding=0)` | `ArchiveyUsageError`; not silently ignored |
| `extract(src, dest, on_progress=0)` | `ArchiveyUsageError` before any output is written |
| `extract_all(dest, filter=0)` | `ArchiveyUsageError` before the first member is offered |
| `extract_all(dest, members="notes.txt")` | `ArchiveyUsageError` naming the list spelling; not a clean extraction of nothing |
| `extract_all(dest, members=0)` | `ArchiveyUsageError` at the call, before `dest` is created |
| `stream_members(members=0)` | `ArchiveyUsageError` at the call, not on first `next()` |
| `detect_format(src, budget=0)` | `ArchiveyUsageError` naming `budget`; never `AttributeError: 'int' object has no attribute 'max_tail_bytes'` |
| `reader.open(0)` | `ArchiveyUsageError`; never a message naming `_archive_id` |
| `reader.open("absent.txt")` | `KeyError` — unchanged, and specified by `archive-reading` |
| `open_archive(0)` | `TypeError: unsupported source type` — unchanged |

## REMOVED Requirements

### Requirement: Archive EOF strictness takes precedence

**Reason**: `strict_archive_eof` is removed (davitf, #315 S21-K8 thread, 2026-09-20), so
there is no terminal `TruncatedError` to take precedence. `ARCHIVE_EOF_MARKER_MISSING`
follows the ordinary per-code disposition, which also dissolves this requirement's
contradiction with the `IGNORE` row in `diagnostics`.
**Migration**: Set `ARCHIVE_EOF_MARKER_MISSING` to `RAISE` and catch
`DiagnosticRaisedError`.
