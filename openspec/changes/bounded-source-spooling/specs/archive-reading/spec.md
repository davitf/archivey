# archive-reading — the spool limit on the config surface

## MODIFIED Requirements

### Requirement: Bounded implicit temporary storage

Reader ops SHALL NOT consume memory or temp storage proportional to member/archive
size as an implicit side effect of open/read/validate/password-confirm. Silently
spooling plaintext to a temp file is forbidden. A per-format strategy that
inherently needs proportional temp storage (e.g. `format-rar`'s documented copy
of a non-path archive source to disk, so `unrar` can seek it) is allowed only when
declared in that format's capability spec. Caller's own buffering of a returned stream is unrestricted.

**A spool of the archive source is not implicit** when it is bounded by the caller's
configured spool limit and recorded in `CostReceipt.notes` (`access-mode-and-cost`).
The word this requirement turns on is *silently*: what it forbids is temp storage the
caller could not have known about or bounded, and a configured limit removes both
halves. Spooling proportional to **archive** size under that limit is therefore
permitted; spooling **plaintext member data** proportional to member size remains
forbidden, and the spool limit does not license it.

#### Scenario: bounded storage matrix

| Case | Expected |
| --- | --- |
| Encrypted member, many candidates | Confirmation temp use bounded by a constant |
| Backend can only serve via materialization | Strategy declared in format spec, not adopted silently |
| Archive source spooled within the configured limit | Permitted; bounded by the limit and recorded in `CostReceipt.notes` |
| Archive source spooled with no limit or no record | Forbidden — that is the case this requirement exists for |
| Plaintext member data spooled proportional to member size | Forbidden; the spool limit does not license it |

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
class SpoolLimits:
    max_bytes: int | None = 2**30
    spool_dir: str | os.PathLike[str] | None = None
    UNLIMITED: ClassVar["SpoolLimits"]

@dataclass(frozen=True)
class ArchiveyConfig:
    use_rapidgzip: AcceleratorMode = AcceleratorMode.AUTO
    use_indexed_bzip2: AcceleratorMode = AcceleratorMode.AUTO
    strict_archive_eof: bool = False
    extraction_limits: ExtractionLimits = ExtractionLimits()
    listing_limits: ListingLimits = ListingLimits()
    spool_limits: SpoolLimits = SpoolLimits()
    diagnostic_policy: DiagnosticPolicy = DiagnosticPolicy()
    max_retained_diagnostic_references: int = 256
    on_diagnostic: Callable[[Diagnostic], None] | None = None
```

`max_retained_diagnostic_references` SHALL be non-negative. Policy/default/override
mappings and the dataclasses SHALL be defensively immutable. `config=None` →
immutable library default. No mutable global/context-local diagnostic policy or
callback.

`SpoolLimits.max_bytes` defaults to **1 GiB**. `None` means never spool;
`SpoolLimits.UNLIMITED` means never refuse on size. The limit governs source spooling
for every read the reader performs, which is why it lives on the config rather than as a
per-call argument beside `limits`.

A reader carries its open config, including `listing_limits` and `spool_limits` for its
lifetime.
Later `extract_all(config=...)` MAY override policy/callback/strictness/
accelerators/`extraction_limits` for new work, but SHALL NOT change the
reader's effective `listing_limits`, `spool_limits` or
`max_retained_diagnostic_references` (see `diagnostics`). Per-call `limits`
still beat `config.extraction_limits`, then reader/library default. Other
per-call operational args stay outside `ArchiveyConfig`.

`strict_archive_eof=False` follows ordinary diagnostic policy for failed EOF check;
`True` forces `TruncatedError` after ordered diagnostic rules in `error-handling`.

`on_diagnostic` runs synchronously after count/retention/logging updates. Snapshot
reads from a callback are allowed. Starting another operation on the same
emitting reader/stream SHALL raise `UnsupportedOperationError`; other readers OK.
Callbacks hold no Archivey collector/reader/stream/backend/registry lock
(`diagnostics` / `reader-concurrency`).

#### Scenario: config matrix

| Case | Expected |
| --- | --- |
| `ArchiveyConfig()` | AUTO accelerators; EOF strictness false; documented extraction, listing and spool defaults; COLLECT; budget 256; no callback |
| `ArchiveyConfig()` spool limit | 1 GiB, platform temporary directory |
| Reader opened with `spool_limits=SpoolLimits(max_bytes=None)` | No operation on that reader writes the source to temporary storage |
| Reader opened with a spool limit, then `extract_all(config=…)` omitting one | The reader's spool limit stands for its lifetime |
| Reader budget 10, then `extract_all(config=…budget=1000)` | New policy/callback may apply; diagnostics still under budget 10 |
| `extract(..., extraction_limits=ExtractionLimits(max_ratio=100))` | 100:1 per-member ratio enforced (`safe-extraction`) |
| Reader opened with `listing_limits=ListingLimits(max_members=10)` | Listing caps stay at 10 for the reader lifetime even if later `extract_all(config=...)` omits listing_limits |
