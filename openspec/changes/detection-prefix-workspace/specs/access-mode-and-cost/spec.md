## ADDED Requirements

### Requirement: Detection cost is a sibling of CostReceipt, not part of it

`CostReceipt` describes an **opened archive**. Detection's I/O happens before a reader
exists, so its measured work SHALL be reported through the separate `DetectionCostReceipt`
(`detection-cost`) and SHALL NOT be folded into `CostReceipt` or `ArchiveInfo.cost`.

The two SHALL share vocabulary where they describe the same thing — capability names, the
kinds of work counted — so a caller reading both sees one cost model rather than two.

#### Scenario: the two receipts stay separate

| Case | Expected |
| --- | --- |
| `open_archive` on a path, detection read 32 774 bytes | `ar.cost` unchanged by those bytes; the detection receipt carries them |
| Standalone `detect_format` | A detection receipt exists; no `CostReceipt` does, because no archive was opened |
| A caller comparing the two | Capability and work-kind names match; the totals are of different things and are not summed |
