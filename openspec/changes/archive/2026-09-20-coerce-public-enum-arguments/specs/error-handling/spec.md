# error-handling — enum argument coercion delta

## ADDED Requirements

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
| `extract()`, `ArchiveReader.extract_all()` | `policy`, `overwrite`, `on_error`, `abort_on` |
| `ArchiveyConfig(...)` | `use_rapidgzip`, `use_indexed_bzip2` |
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
| `extract(src, dest, overwrite="skip")` | Behaves exactly as `OverwritePolicy.SKIP`; an existing local file is kept and reported `NOT_OVERWRITTEN` |
| `extract(src, dest, on_error="stop")` | Behaves exactly as `OnError.STOP`; a per-member failure raises |
| `extract(src, dest, policy="strict")` | Behaves as `ExtractionPolicy.STRICT`; never a bare `KeyError` |
| `extract(src, dest, abort_on=["blocked-member"])` | Accepted; the dash spelling resolves to `AbortOn.BLOCKED_MEMBER` |
| `extract(src, dest, abort_on="blocked_member")` | `ArchiveyUsageError` naming the list spelling |
| `extract(src, dest, overwrite="nonsense")` | `ArchiveyUsageError` naming `overwrite` and the valid spellings; nothing written to `dest` |
| `ArchiveyConfig(use_rapidgzip="on")` | Field holds `AcceleratorMode.ON`, not the string |
| `ArchiveyConfig(use_rapidgzip="sometimes")` | `ArchiveyUsageError` at construction, not at the later stream open |
| `detect_format(src, budget="fast")` | Detects under the FAST preset |
| `detect_format(src, budget="turbo")` | `ArchiveyUsageError` naming the presets, not `AttributeError` on a budget field |
| `coerce to OverwritePolicy` given `AbortOn.BLOCKED_MEMBER` | `ArchiveyUsageError` reporting a wrong **type**, though `AbortOn` is a `str` subclass |
| `except ArchiveyError` around any of the refusals | Does not catch it |
