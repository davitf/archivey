# archive-reading — reader re-entry error type delta

## MODIFIED Requirements

### Requirement: Reader-lifetime cumulative diagnostic snapshots

Every successfully created `ArchiveReader` SHALL expose:

```python
@property
def diagnostics(self) -> DiagnosticSummary: ...
```

Each access SHALL return a fresh immutable cumulative snapshot. Counts SHALL
include automatic-detection events that led to this reader (if any) plus every
subsequent open/list/read/stream/extract event it owns. Previously returned
snapshots SHALL not change. A stream returned by the reader SHALL expose an
operation-filtered `diagnostics` view of the same lifetime — not a separately
retained copy of the aggregate.

Value shape, retention budget, watermarks, and attachment rules: `diagnostics`.

#### Scenario: diagnostics matrix

| Case | Expected |
| --- | --- |
| Detection conflict + scan + rewind diagnostics | Later `reader.diagnostics` has exact cumulative counts in emission order; earlier snapshot unchanged |
| Two streams emit different diagnostics | Each stream sees only its op; reader sees both |
| Callback reads `diagnostics` then `reader.read(...)` | Snapshot OK (incl. current event); reentry → `ArchiveyUsageError` naming the callback |
