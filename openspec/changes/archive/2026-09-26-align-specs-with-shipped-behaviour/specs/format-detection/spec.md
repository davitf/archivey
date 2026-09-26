## MODIFIED Requirements

### Requirement: detect_format() returns a FormatInfo

The system SHALL expose:

```python
archivey.detect_format(
    source: str | Path | BinaryIO,
    *,
    config: ArchiveyConfig | None = None,
) -> FormatInfo
```

```python
class DetectionConfidence(Enum):
    CERTAIN = "certain"
    PROBABLE = "probable"
    GUESS = "guess"

@dataclass(frozen=True)
class FormatInfo:
    format: ArchiveFormat
    confidence: DetectionConfidence
    detected_by: Literal["magic", "extension", "content_probe", "sfx_scan", "directory"]
    payload_offset: int = 0
    diagnostics: DiagnosticSummary = DiagnosticSummary.empty()
    cost_receipt: DetectionCostReceipt | None = None   # compare=False; see detection-cost
    unavailable_tiers: tuple[TierSkip, ...] = ()       # compare=False; see detection-cost
```

`config=None` → library default. `confidence` = magic / structural probe /
extension-guess. `payload_offset > 0` marks an SFX payload start. `cost_receipt` and
`unavailable_tiers` are part of this contract; `detection-cost` defines what they hold.
`FormatInfo` also carries a provisional `corroborated: bool` for `open_archive`'s own use,
which SHALL NOT be part of this contract: its `False` cannot tell an uncorroborated probe
from a result that was not a probe.

A **directory path** SHALL return `FormatInfo(format=DIRECTORY,
confidence=CERTAIN, detected_by="directory")` without reading anything, the same
format `open_archive` reads it as. Its `cost_receipt` SHALL be the zero receipt (one pass, no
bytes read). It SHALL NOT raise `IsADirectoryError` or any
other `OSError`.

**Collectors:**

| Path | Behavior |
| --- | --- |
| Standalone `detect_format` | One finite collector; policy/callback/logging/budget; final summary on `FormatInfo.diagnostics` |
| Inside `open_archive` | Open creates prospective-reader collector + detection watermark, passes that collector into detection. On success the reader owns it — no seed/merge/replay/copy; each retained occurrence charged once. Internal detection-range `FormatInfo.diagnostics` is not retained after handoff; same events remain on the reader's cumulative summary |

#### Scenario: detect / handoff matrix

| Case | Expected |
| --- | --- |
| Standalone detect with magic/extension conflict | `FormatInfo.diagnostics` has exact conflict count + retained detail under default budget |
| Auto-detect inside `open_archive` retains conflict, open succeeds | Reader continues same collector/order/budget; no copied aggregate |
| Magic match | `confidence=CERTAIN`, `detected_by="magic"` |
| Extension-only guess | `confidence=GUESS`, `detected_by="extension"` |
| Directory path | `format=DIRECTORY`, `confidence=CERTAIN`, `detected_by="directory"`; zero `cost_receipt`; no `OSError` |
| Explicit `diagnostic_policy` on detect | IGNORE/COLLECT/RAISE applies to that finite detection |
