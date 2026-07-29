## MODIFIED Requirements

### Requirement: Member stream capabilities are declared with booleans

The system SHALL let callers declare member-stream capabilities at `open_archive` with
two keyword-only booleans, `seekable_members` and `concurrent_members`, both defaulting
to `False`. The system SHALL NOT expose a flag-enum parameter for this purpose.

`open_stream` SHALL keep its `seekable: bool` parameter, and the two entry points SHALL
use the same `seekable` vocabulary for the same concept. Concurrency has no meaning for a
single standalone stream, so `open_stream` MUST NOT gain a concurrency parameter.

Defaults and behaviour are unchanged by the spelling: with neither declared, member
streams report `seekable() is False`, `seek()` raises `io.UnsupportedOperation`, and at
most one member stream may be live — a second overlapping `open()` raises
`ConcurrentAccessError`. Declaring `concurrent_members=True` together with
`streaming=True` MUST be rejected at open with `ArchiveyUsageError`.

The `MemberStreams` flag type SHALL remain publicly exported and SHALL remain the value
reported for declared capabilities on `CostReceipt` and in diagnostics. It is no longer
an input to `open_archive`.

Declaring `concurrent_members=True` MUST NOT change solid open-order cost; that remains
the caller's algorithm via `AccessCost` / `stream_members()`.

#### Scenario: declaring capabilities

| Case | Expected |
| --- | --- |
| `open_archive(p)` | Forward-only streams; one live at a time; `seek()` raises `io.UnsupportedOperation` |
| `open_archive(p, seekable_members=True)` | Member streams seekable; still one live at a time |
| `open_archive(p, concurrent_members=True)` | Overlapping opens allowed after materialization; streams not seekable |
| `open_archive(p, seekable_members=True, concurrent_members=True)` | Both capabilities |
| `open_archive(p, streaming=True, concurrent_members=True)` | `ArchiveyUsageError` at open |
| `open_archive(p, member_streams=...)` | `TypeError` — the parameter no longer exists |
| `reader.cost` after declaring | Declared capabilities reported as a `MemberStreams` value |
