## MODIFIED Requirements

### Requirement: Explicit configuration object

The system SHALL define these complete frozen schemas:

```python
@dataclass(frozen=True)
class ExtractionLimits:
    max_extracted_bytes: int | None = 2 * 2**30
    max_ratio: float | None = 1000.0
    ratio_activation_threshold: int = 5 * 2**20
    max_entries: int | None = 1_048_576
    UNLIMITED: ClassVar["ExtractionLimits"]

@dataclass(frozen=True)
class ListingLimits:
    max_members: int | None = 1_048_576
    max_metadata_bytes: int | None = 64 * 2**20
    UNLIMITED: ClassVar["ListingLimits"]

@dataclass(frozen=True)
class DecoderLimits:
    max_decoder_memory: int | None = 2 * 2**30
    UNLIMITED: ClassVar["DecoderLimits"]

@dataclass(frozen=True)
class ArchiveyConfig:
    use_rapidgzip: AcceleratorMode = AcceleratorMode.AUTO
    use_indexed_bzip2: AcceleratorMode = AcceleratorMode.AUTO
    extraction_limits: ExtractionLimits = ExtractionLimits()
    listing_limits: ListingLimits = ListingLimits()
    decoder_limits: DecoderLimits = DecoderLimits()
    diagnostic_policy: DiagnosticPolicy = DiagnosticPolicy()
    max_retained_diagnostic_references: int = 256
    on_diagnostic: Callable[[Diagnostic], None] | None = None
```

`max_retained_diagnostic_references` SHALL be non-negative. Policy/default/override
mappings and the dataclasses SHALL be defensively immutable. `config=None` →
immutable library default. No mutable global/context-local diagnostic policy or
callback.

A reader carries its open config, all of it, for its lifetime. Reader methods
SHALL NOT take a `config=`: `extract_all(limits=...)` is the one per-call
override, and it replaces only the extraction limits for that call.
`decoder_limits` SHALL bound the working memory a codec allocates on the
strength of a number the archive declares, and SHALL be enforced before that
allocation is made. Per-call `limits`
still beat `config.extraction_limits`, then reader/library default. Other
per-call operational args stay outside `ArchiveyConfig`.

`on_diagnostic` runs synchronously after count/retention/logging updates. Snapshot
reads from a callback are allowed. Starting another operation on the same
emitting reader/stream SHALL raise `UnsupportedOperationError`; other readers OK.
Callbacks hold no Archivey collector/reader/stream/backend/registry lock
(`diagnostics` / `reader-concurrency`).

#### Scenario: config matrix

| Case | Expected |
| --- | --- |
| `ArchiveyConfig()` | AUTO accelerators; documented extraction and listing defaults; COLLECT; budget 256; no callback |
| `extract(..., extraction_limits=ExtractionLimits(max_ratio=100))` | 100:1 per-member ratio enforced (`safe-extraction`) |
| Reader opened with `listing_limits=ListingLimits(max_members=10)` | Listing caps stay at 10 for the reader lifetime; `extract_all()` has no `config=` to change them |

### Requirement: MemberListReport surfaces partial listings with terminal errors

The system SHALL expose an immutable listing report and a materializing accessor
that always returns both recovered members and any terminal archive-level error:

```python
@dataclass(frozen=True)
class MemberListReport:
    members: tuple[ArchiveMember, ...]
    error: ArchiveyError | None
    diagnostics: DiagnosticSummary

def members_report(self) -> MemberListReport: ...
```

`members_report()` SHALL recover every member the backend can list before a
terminal archive-level failure, put them in `members` (archive order), set
`error` to that failure or `None` when the listing is complete, and attach a
point-in-time `diagnostics` snapshot for the operation. It MUST NOT raise for
terminal archive-level listing errors covered by this requirement (those belong
on `error`). Open-time failures and `ResourceLimitError` from `ListingLimits`
SHALL still raise (limits are not the damage story).

`error is None` SHALL mean the listing is complete. Callers MUST treat a
non-`None` `error` as an incomplete listing even when `members` is non-empty.
The report SHALL iterate, index, and size as its `members` sequence (same
ergonomics as `ExtractionReport` vs its results).

Members in the report SHALL be identity-stamped for this reader (`member in
reader`) so `open(member)` works for recovered `FILE` members. An incomplete
report (`error` set) MUST NOT be treated as a successful complete materialization:
subsequent `members()` / `scan_members()` / `get(name)` MUST still raise the
terminal error rather than return a silent partial list.
`members_report_if_available()` SHALL return a `MemberListReport | None`: the stored
report when one exists without scanning — complete (`error is None`) **or**
incomplete (`error` set) from a prior pass — or the upfront index as a complete
report for backends that carry one; `None` only when nothing is materialized and a
scan would be required. Returning an incomplete report to a caller MUST NOT change
the complete-or-raise behaviour of `members()` / `scan_members()` / `get(name)`;
the report self-labels via `error` and those methods still raise.

On `streaming=True`, `members_report()` MAY start or finish the single forward
pass (like `scan_members`) and thereby consume it; it still returns a report
instead of raising on terminal archive-level listing errors.

#### Scenario: members_report / MemberListReport matrix

| Case | Expected |
| --- | --- |
| Clean archive | `error is None`; `members` is the full fully-resolved list |
| TAR rejected mid/final header after prefix (Option F) | `members` = recoverable prefix; `error` is `CorruptionError`; report stored incomplete |
| Absent/short TAR trailer with `ARCHIVE_EOF_MARKER_MISSING` set to `RAISE` | `DiagnosticRaisedError` raised: the caller's policy firing, not listing damage, so it is not carried on `error` |
| `members_report()` then `members()` on same RA reader after incomplete | `members()` raises the terminal error (not a partial list) |
| `open(report.members[i])` for a recovered FILE after incomplete | Succeeds by identity |
| `get(name)` after incomplete | Raises terminal error / does not pretend completeness |
| `members_report_if_available()` after incomplete pass already ran | Returns the incomplete report (prefix + `error`); count is a floor |
| `members_report_if_available()` with no materialization and no upfront index | `None` |
| `ListingLimits.max_members` exceeded during `members_report` | `ResourceLimitError` raised (not soft-returned on `error`) |
| Streaming `members_report` after recoverable prefix + terminal error | Report with prefix + error; pass consumed |
