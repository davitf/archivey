## MODIFIED Requirements

### Requirement: Exact bounded diagnostic summaries

`DiagnosticSummary` snapshots SHALL expose `total_count`, exact per-code `counts`,
retained occurrences in emission order, and `dropped_count`. Counts include every
emitted event regardless of disposition/retention. `dropped_count` = aggregate
occurrences not retained.

Each standalone detection, reader lifetime, top-level extraction, or standalone
stream SHALL enforce one `ArchiveyConfig.max_retained_diagnostic_references`
budget (default 256) across every library-retained reference for that collector.
Aggregate retention = one slot; one eligible object attachment = another.
Order: aggregate first, then most-specific attachment. No attachment without a
retained aggregate. Exact counters do not consume slots.

Snapshots are freshly created, bounded, never mutated. Caller-retained snapshots
and caller-created member copies are outside the library budget, and so is the record
a random-access member walk keeps until it ends so that a walk started over can
replay its diagnostics (`archive-reading` §Each member is listed once per reader).

**Watermark (implementer):** an internal operation watermark consumes no slot and
copies no occurrence; a ranged summary computes counter deltas and selects from
already-retained aggregate entries.

#### Scenario: retention matrix

| Case | Expected |
| --- | --- |
| Member diagnostic, ≥2 slots left | Aggregate + member attachment; 1 slot → aggregate only |
| Budget exhausted, more events | No further detail/attachment; counts stay exact |
| `before = reader.diagnostics`, more events, `after = …` | `before` unchanged; `after` includes later counts in order |
