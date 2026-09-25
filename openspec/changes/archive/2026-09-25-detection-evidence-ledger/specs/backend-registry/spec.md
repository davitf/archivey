## MODIFIED Requirements

### Requirement: Detection owns matching and registry selects by format

The system SHALL keep format detection and backend selection separate. `detect_format()` is
the authority for source format; the registry SHALL map the resolved `ArchiveFormat` to a
registered available backend. If a detected format has no available backend, lookup SHALL
raise `UnsupportedFormatError` with the install hint.

```python
class BackendRegistry:
    def register_reader(self, backend_cls: type[ReadBackend]) -> None: ...
    def reader_for_format(self, format: ArchiveFormat) -> type[ReadBackend]: ...
    def register_writer(self, backend_cls: type[WriteBackend]) -> None: ...
    def writer_for_format(self, format: ArchiveFormat) -> type[WriteBackend]: ...
    def list_formats(self) -> list[ArchiveFormat]: ...
    def list_writable_formats(self) -> list[ArchiveFormat]: ...
```

Backends SHALL declare detection as **declarations** rather than as bare
`(offset, magic, format)` tuples, so the scheduler can price and order them without running
them:

```python
@dataclass(frozen=True)
class DetectionDeclaration:
    name: str
    max_evidence: EvidenceClass              # ceiling, not prediction
    required_capabilities: frozenset[DetectionCapability]
    estimated_cost: DetectionCostEstimate
    evaluate: DetectionEvaluator             # yields zero or more candidates
```

`max_evidence` is the strongest result the detector can possibly produce; the achieved class
may be lower. A detector SHALL be split into two declarations only when reaching the higher
class costs **materially more**, so the expensive half can be priced and excluded separately.

An evaluator SHALL return an **iterable** of candidates rather than one optional record: a
single scan pass can yield several hits at different offsets, each a separate
`(format, payload_offset)` candidate. Absence is an empty iterable, never a sentinel. The
evaluator charges the detection cost receipt for the bytes it requests, so affordability is
checked once per declaration rather than once per read.

A declaration that cannot estimate a cost field SHALL state an upper bound and never zero,
because the scheduler reads the estimate as a promise.

#### Scenario: detection/selection matrix

| Case | Expected |
| --- | --- |
| Detection reports `ArchiveFormat.SEVEN_Z` | `reader_for_format()` returns native `SevenZReadBackend` |
| Detected backend's optional dependency is missing | `UnsupportedFormatError` names missing package and install hint |
| No magic/probe/extension matches | `FormatDetectionError`; no backend lookup |

#### Scenario: declaration ceilings

| Case | Expected |
| --- | --- |
| Content probe | Two declarations — a bounded prefix decode at `BOUNDED_PROBE`, and whole-source completion at `COMPLETE` with its own cost and capability requirement |
| gzip | **One** declaration ceilinged `SELF_VALIDATING`, achieving `SIGNATURE_ONLY` when `FHCRC` is absent — verifying it is free once the header is read |
| A single probe declaration ceilinged `COMPLETE` | Rejected: no detector could ever stop while probes were unrun, so every archive would pay for three probe decodes |
| One scan pass finding three `ustar` hits | Three candidates, not one |
| A declaration whose required capabilities are unmet | Never runs; recorded as unavailable, distinctly from a budget skip |
