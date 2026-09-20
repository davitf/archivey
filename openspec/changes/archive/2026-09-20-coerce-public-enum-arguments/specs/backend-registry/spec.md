# backend-registry — `format=` string spellings delta

## MODIFIED Requirements

### Requirement: A format argument outside its declared type is a usage error

Four public functions take a format argument. Each SHALL accept a value of the types
its own signature declares, **or that format spelled as a string**, and SHALL reject
anything else with `ArchiveyUsageError` — which sits outside `ArchiveyError`
(`error-handling`), so `except ArchiveyError` cannot swallow a caller bug — **before**
resolving, peeking or reading the source:

| Call | Accepts | Anything else |
| --- | --- | --- |
| `format_availability(format)` | `ArchiveFormat`, its spelling | `ArchiveyUsageError` |
| `open_archive(source, format=…)` | `ArchiveFormat`, its spelling, `None` (auto-detect) | `ArchiveyUsageError` |
| `extract(source, dest, format=…)` | `ArchiveFormat`, its spelling, `None` (auto-detect) | `ArchiveyUsageError` |
| `open_stream(source, format=…)` | `StreamFormat`, raw-stream `ArchiveFormat`, either spelling, `None` | `ArchiveyUsageError` |

`open_stream`'s wider argument is by design, not an inconsistency to remove: a raw
compressed stream has no container, so the codec alone identifies it. A container
`ArchiveFormat` there remains a usage error for the separate reason it already was.

An `ArchiveFormat` is a `(container, stream)` pair rather than an `Enum`, so it has no
`value` to spell. Its spellings SHALL be its **file extension** (`"zip"`, `"tar.gz"`)
and its **attribute name** (`"TAR_GZ"`), both derived from the existing tables so a
format added later is spellable without a second edit. `DIRECTORY` and `UNKNOWN` have
no extension, so the name alone spells those. Case is ignored and `-` and `_` are
interchangeable, matching the boundary rule in `error-handling`. No two formats may
share a spelling, and the test suite SHALL fail if a new format introduces a collision.

`open_stream` SHALL resolve a string against `ArchiveFormat` before `StreamFormat`. The
spellings the two types share name the same format at two levels, so the order picks a
representation rather than a meaning — and picks the one `open_archive` would.

A `StreamFormat` **object** passed where an `ArchiveFormat` is required SHALL still be
refused rather than widened: accepting `"gz"` as a spelling does not make the codec
half a valid pair.

The rejection SHALL be a refusal, never a substitute answer: the call MUST NOT fall
back to auto-detection, return a fabricated record, or let an internal `AttributeError`
reach the caller.

The message SHALL name **what was passed** and **what was expected**. For a
`StreamFormat` passed where an `ArchiveFormat` was required — the likeliest mistake,
since `open_stream` accepts one — it SHALL also name the predefined `ArchiveFormat`
pairs built on that codec, so one message ends the mistake:

```
format_availability() takes an ArchiveFormat, but got StreamFormat.ZSTD. A StreamFormat
is only the codec half of an ArchiveFormat's (container, stream) pair, so pass the pair
instead: ArchiveFormat.ZST (a raw .zst stream) or ArchiveFormat.TAR_ZST (a tar
compressed with it).
```

Those names SHALL be derived from the predefined `ArchiveFormat` instances rather than
a separate table, so a codec added later is named here without a second edit.

#### Scenario: wrong-typed format argument matrix

| Case | Expected |
| --- | --- |
| `format_availability(StreamFormat.ZSTD)` | `ArchiveyUsageError` naming `StreamFormat.ZSTD`, `ArchiveFormat.ZST` and `ArchiveFormat.TAR_ZST` |
| `format_availability(None)` | `ArchiveyUsageError` — the query has no auto-detect form |
| `open_archive(path, format=StreamFormat.ZSTD)` | `ArchiveyUsageError`, not `AttributeError: 'StreamFormat' object has no attribute 'container'` |
| `extract(path, dest, format=StreamFormat.ZSTD)` | `ArchiveyUsageError`; nothing written to `dest`, source never read |
| `open_stream(src, format=object())` | `ArchiveyUsageError`; the source is not read and detection does not run |
| `open_stream(src, format=StreamFormat.GZIP \| ArchiveFormat.GZ \| None)` | Opens as before |
| `open_archive(path, format=ArchiveFormat.ZIP \| None)` | Opens as before |
| `except ArchiveyError` around any of the refusals | Does not catch it |

#### Scenario: format spelled as a string

| Case | Expected |
| --- | --- |
| `open_archive(path, format="zip")` \| `extract(path, dest, format="zip")` | Opens as `ArchiveFormat.ZIP` |
| `format_availability("zip")` | Same record as `format_availability(ArchiveFormat.ZIP)` |
| Every format, by its name and by its file extension, in any case | Resolves to that format |
| `open_stream(src, format="gz")` | `ArchiveFormat.GZ` — what `open_archive` would resolve it to |
| `open_stream(src, format="gzip")` | `StreamFormat.GZIP` — no `ArchiveFormat` spells it |
| `open_archive(path, format="not-a-format")` | `ArchiveyUsageError` naming the accepted spellings |
| Two formats sharing a normalized spelling | Test suite fails |
