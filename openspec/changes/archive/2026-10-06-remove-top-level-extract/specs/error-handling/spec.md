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
| `open_archive()`, `open_stream()`, `detect_format()` | `config` |
| `detect_format()` | `budget` (a `DetectionBudget`; a `DetectionBudgetPreset` is an enum and out of scope) |
| `open_archive()` | `encoding`, `password` |
| `ArchiveReader.extract_all()` | `limits`, `on_progress` |
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
truthiness are not covered, there being no wrong type to find, except
`ArchiveyConfig`'s guard switches (`rar_allow_glob_member_concatenation`,
`read_link_targets`), which SHALL be a `bool`: a string such as `"false"` is truthy
and would silently switch the guard.

#### Scenario: object argument refusal matrix

| Case | Expected |
| --- | --- |
| `open_archive(src, config="strict")` | `ArchiveyUsageError` naming `config`; never `AttributeError: 'str' object has no attribute 'diagnostic_policy'` |
| `ArchiveyConfig(extraction_limits="none")` then `extract_all(...)` | `ArchiveyUsageError` at construction, not `AttributeError` part-way through the extraction |
| `ArchiveyConfig(listing_limits="x")` then listing | `ArchiveyUsageError` at construction, not `AttributeError` mid-listing |
| `ArchiveyConfig(max_retained_diagnostic_references="x")` | `ArchiveyUsageError` at construction |
| `ArchiveyConfig(on_diagnostic=0)` | `ArchiveyUsageError` at construction, not when the first diagnostic fires |
| `ArchiveyConfig(rar_allow_glob_member_concatenation="false")` | `ArchiveyUsageError` at construction; the refusal is never switched off by a truthy string |
| `ListingLimits(max_members="x")` | `ArchiveyUsageError` at construction, not `TypeError` mid-listing |
| `ExtractionLimits(max_ratio=float("nan"))` | `ArchiveyUsageError`; a NaN would leave the ratio guard switched off silently |
| `ExtractionLimits(ratio_activation_threshold=None)` | `ArchiveyUsageError`; the field is not optional and `None` disables nothing |
| `ExtractionLimits(max_extracted_bytes=True)` | `ArchiveyUsageError`; `bool` is an `int` subclass and would cap at one byte |
| `open_archive(src, encoding="rot13")` | `ArchiveyUsageError` naming the argument; not a `LookupError` during a member-name decode |
| `open_archive(src, encoding=0)` | `ArchiveyUsageError`; not silently ignored |
| `extract_all(dest, on_progress=0)` | `ArchiveyUsageError` before any output is written |
| `extract_all(dest, filter=0)` | `ArchiveyUsageError` before the first member is offered |
| `extract_all(dest, members="notes.txt")` | `ArchiveyUsageError` naming the list spelling; not a clean extraction of nothing |
| `extract_all(dest, members=0)` | `ArchiveyUsageError` at the call, before `dest` is created |
| `stream_members(members=0)` | `ArchiveyUsageError` at the call, not on first `next()` |
| `detect_format(src, budget=0)` | `ArchiveyUsageError` naming `budget`; never `AttributeError: 'int' object has no attribute 'max_prefix_bytes'` |
| `reader.open(0)` | `ArchiveyUsageError`; never a message naming `_archive_id` |
| `reader.open("absent.txt")` | `KeyError` — unchanged, and specified by `archive-reading` |
| `open_archive(0)` | `TypeError: unsupported source type` — unchanged |

### Requirement: Enum-typed public arguments are converted at the boundary

Every public entry point that declares an `Enum`-typed parameter SHALL accept the
member **spelled as a string** and convert it to the member before any branch on its
value, and SHALL raise `ArchiveyUsageError` — outside `ArchiveyError`, per the misuse
requirement above — on a value that is neither a member nor a recognised spelling.

This is a boundary rule, not a check at the point of use. The consuming code tests
these values with `is`, so an unconverted value is not refused where it is read: it
matches no member and takes whatever branch the chain falls through to. Two of those
fall-throughs are the unsafe option — `overwrite` falls through to REPLACE, `on_error`
to CONTINUE — so a value that survives the boundary destroys data or swallows an error
rather than producing an error of its own.

The parameters covered:

| Entry point | Parameters |
| --- | --- |
| `ArchiveReader.extract_all()` | `policy`, `overwrite`, `on_error`, `abort_on` |
| `ArchiveyConfig(...)` | `use_rapidgzip`, `use_indexed_bzip2` |
| `DiagnosticPolicy(...)` | `default`, and the keys and values of `overrides` |
| `detect_format()` | `budget` (a `DetectionBudget` passes through unconverted) |

A member SHALL be reachable by its `value`, by its member **name**, in any case, and
with `-` and `_` used interchangeably, so the dash spelling the CLI's `--help`
advertises is also valid in library code. Surrounding whitespace SHALL be ignored.
Accepting a string makes the enum **values** part of the public API: a value cannot
afterwards be renamed without breaking callers silently.

The conversion SHALL be unambiguous. No two members of a covered enum may share a
normalized spelling, and the test suite SHALL fail if a new member introduces such a
collision, rather than letting one member become unreachable by that spelling.

A member of a **different** enum SHALL be reported as a wrong type rather than as an
unrecognised spelling, including for the enums that mix in `str`.

A collection-valued parameter SHALL accept any iterable of members or spellings, and
SHALL refuse a bare string rather than iterating it into single characters —
`abort_on="blocked_member"` is a typo for `abort_on=["blocked_member"]`.

The message SHALL name the call, the parameter, the value received, and the spellings
that would have worked.

The CLI SHALL convert through the same helper, so the library and the CLI accept one
vocabulary rather than two that can drift.

#### Scenario: enum argument conversion matrix

| Case | Expected |
| --- | --- |
| `extract_all(dest, overwrite="skip")` | Behaves exactly as `OverwritePolicy.SKIP`; an existing local file is kept and reported `NOT_OVERWRITTEN` |
| `extract_all(dest, on_error="stop")` | Behaves exactly as `OnError.STOP`; a per-member failure raises |
| `extract_all(dest, policy="strict")` | Behaves as `ExtractionPolicy.STRICT`; never a bare `KeyError` |
| `extract_all(dest, abort_on=["blocked-member"])` | Accepted; the dash spelling resolves to `AbortOn.BLOCKED_MEMBER` |
| `extract_all(dest, abort_on="blocked_member")` | `ArchiveyUsageError` naming the list spelling |
| `extract_all(dest, overwrite="nonsense")` | `ArchiveyUsageError` naming `overwrite` and the valid spellings; nothing written to `dest` |
| `ArchiveyConfig(use_rapidgzip="on")` | Field holds `AcceleratorMode.ON`, not the string |
| `ArchiveyConfig(use_rapidgzip="sometimes")` | `ArchiveyUsageError` at construction, not at the later stream open |
| `DiagnosticPolicy(default="raise")` | Field holds `DiagnosticDisposition.RAISE`; a diagnostic the policy covers raises |
| `DiagnosticPolicy(overrides={"ARCHIVE_TRAILING_DATA": "raise"})` | The key is `DiagnosticCode.ARCHIVE_TRAILING_DATA`, so that code raises |
| `detect_format(src, budget="fast")` | Detects under the FAST preset |
| `detect_format(src, budget="turbo")` | `ArchiveyUsageError` naming the presets, not `AttributeError` on a budget field |
| `coerce to OverwritePolicy` given `AbortOn.BLOCKED_MEMBER` | `ArchiveyUsageError` reporting a wrong **type**, though `AbortOn` is a `str` subclass |
| `except ArchiveyError` around any of the refusals | Does not catch it |
