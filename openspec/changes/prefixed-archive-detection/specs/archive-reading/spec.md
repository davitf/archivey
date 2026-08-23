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
class ArchiveyConfig:
    use_rapidgzip: AcceleratorMode = AcceleratorMode.AUTO
    use_indexed_bzip2: AcceleratorMode = AcceleratorMode.AUTO
    strict_archive_eof: bool = False
    exhaustive_prefix_scan: bool = False
    extraction_limits: ExtractionLimits = ExtractionLimits()
    listing_limits: ListingLimits = ListingLimits()
    diagnostic_policy: DiagnosticPolicy = DiagnosticPolicy()
    max_retained_diagnostic_references: int = 256
    on_diagnostic: Callable[[Diagnostic], None] | None = None
```

`max_retained_diagnostic_references` SHALL be non-negative. Policy/default/override
mappings and the dataclasses SHALL be defensively immutable. `config=None` →
immutable library default. No mutable global/context-local diagnostic policy or
callback.

A reader carries its open config, including `listing_limits` for its lifetime.
Later `extract_all(config=...)` MAY override policy/callback/strictness/
accelerators/`extraction_limits` for new work, but SHALL NOT change the
reader's effective `listing_limits` or
`max_retained_diagnostic_references` (see `diagnostics`). Per-call `limits`
still beat `config.extraction_limits`, then reader/library default. Other
per-call operational args stay outside `ArchiveyConfig`.

`strict_archive_eof=False` follows ordinary diagnostic policy for failed EOF check;
`True` forces `TruncatedError` after ordered diagnostic rules in `error-handling`.

`exhaustive_prefix_scan=False` leaves detection's prefix search bounded by `SFX_MAX` and
gated on a cue; `True` searches the whole source for the same validated signatures. It sits
here rather than on `open_archive` because `detect_format` accepts no per-call operational
keywords and both must be able to express it. Like `strict_archive_eof` it is a
**cost-bearing behaviour flag, not a tuning constant**: the cost is O(source size) rather
than O(constant), which is why it is opt-in and why it SHALL NOT be enabled implicitly. The
behaviour it selects is specified below and in `format-detection`.

`on_diagnostic` runs synchronously after count/retention/logging updates. Snapshot
reads from a callback are allowed. Starting another operation on the same
emitting reader/stream SHALL raise `UnsupportedOperationError`; other readers OK.
Callbacks hold no Archivey collector/reader/stream/backend/registry lock
(`diagnostics` / `reader-concurrency`).

#### Scenario: config matrix

| Case | Expected |
| --- | --- |
| `ArchiveyConfig()` | AUTO accelerators; EOF strictness false; **exhaustive prefix scan false**; documented extraction and listing defaults; COLLECT; budget 256; no callback |
| Reader budget 10, then `extract_all(config=…budget=1000)` | New policy/callback may apply; diagnostics still under budget 10 |
| `extract(..., extraction_limits=ExtractionLimits(max_ratio=100))` | 100:1 per-member ratio enforced (`safe-extraction`) |
| Reader opened with `listing_limits=ListingLimits(max_members=10)` | Listing caps stay at 10 for the reader lifetime even if later `extract_all(config=...)` omits listing_limits |
| `ArchiveyConfig(exhaustive_prefix_scan=True)` passed to `detect_format` or `open_archive` | Whole-source scan enabled for that call, with unchanged validation |

## ADDED Requirements

### Requirement: An exhaustive prefix scan is available and off by default

Detection's forward scan is bounded by `SFX_MAX` and gated on a prefix cue, because reading
that much from every source a caller opens is not free. A caller who knows better — someone
holding a firmware image, a disk image, or a file with an unrecognised wrapper — SHALL be
able to ask for an unbounded scan.

The opt-in SHALL be the `ArchiveyConfig` field `exhaustive_prefix_scan: bool = False`,
specified in `format-detection`, and SHALL NOT be a keyword argument on `open_archive`.
`detect_format` accepts no per-call operational keywords, so a flag placed on
`open_archive` alone could not be expressed on `detect_format`; a config field serves both
through the `config=` channel they already share. It joins `strict_archive_eof` as a
cost-bearing behaviour flag rather than a tuning constant.

Because it arrives through `config=`, it SHALL follow the existing rules for configuration
that a given call cannot act on: a source that is already a plain archive at offset 0 is
matched at tier 1 and the flag simply never applies, which is not an error and SHALL NOT be
reported as an unused argument.

The option SHALL default to off. When enabled it SHALL search the whole source for the same
validated container signatures the cued scan uses, and a hit SHALL be subject to the same
structural validation (`format-detection`) — the opt-in changes *how far* detection looks,
never *how much evidence* it demands. A hit found only this way SHALL report
`prefix_kind = UNKNOWN` and `detected_by = "exhaustive_scan"`.

Because the cost is unbounded in the size of the source, the system SHALL NOT enable this
implicitly — not as a retry after `FormatDetectionError`, and not because an extension
suggested a format that was not found.

#### Scenario: exhaustive scan matrix

| Case | Expected |
| --- | --- |
| Archive magic beyond `SFX_MAX`, option off | `FormatDetectionError`; the source is not read past the window |
| Same source, option on | Detected, `payload_offset` at the payload, `prefix_kind == UNKNOWN` |
| Option on, magic present but validation fails | No claim; the scan continues and then fails normally |
| Option on, plain archive at offset 0 | Found at tier 1 as usual; no scan performed |
| `FormatDetectionError` with the option off | SHALL NOT silently retry with it on |
