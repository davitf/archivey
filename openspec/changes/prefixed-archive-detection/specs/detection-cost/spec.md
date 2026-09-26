## MODIFIED Requirements

### Requirement: Detection declares what it may spend, and reports what it spent

The system SHALL expose a detection budget and a detection cost receipt in one shared
vocabulary — the budget is an upper bound, the receipt is measured work:

```python
@dataclass(frozen=True)
class DetectionBudget:
    max_prefix_bytes: int
    max_far_bytes: int
    max_tail_bytes: int            # reserved: ZIP tail not scheduled yet (all presets 0)
    max_seeks: int                 # reserved for the same reason (all presets 0)
    max_scan_bytes: int
    max_decode_input: int
    max_decode_output: int
    completion_window_bytes: int   # reserved: whole-source completion → evidence-ledger
    max_index_bytes: int           # reserved → evidence-ledger
    max_probe_links: int           # live for within_budget probe allowance; walk still uses CHAIN_MAX_LINKS
    spool_non_seekable_up_to: int
    collect_nonmaximal_candidates: bool  # reserved → evidence-ledger

@dataclass(frozen=True)
class DetectionCostReceipt:
    prefix_bytes: int      # sum of range lengths requested from the workspace
    unique_bytes_read: int # actually fetched from the source (each byte once)
    far_bytes: int
    tail_bytes: int        # always 0 until a tail tier exists
    scanned_bytes: int
    seeks: int             # ZIP-tail seeks only; probe read_at restores and exit restore are not charged
    decode_input: int
    decode_output: int
    index_bytes: int
    spooled_bytes: int
```

Callers SHALL pass the budget as `ArchiveyConfig.detection_budget` (`None` selects
`BALANCED_BUDGET`). `detect_format` and `open_archive` SHALL NOT take a `budget=`
keyword — `#273`'s `detect_format(..., budget=)` is removed when that field lands.
The types live in `archivey.detection_cost` and are **not** re-exported from
`archivey.__all__` yet; public freeze of the root surface is deferred to
`detection-result-surface`. The receipt SHALL be detection's own and SHALL NOT
be merged into the archive-open `CostReceipt`: detection's I/O happens before a reader
exists. `max_far_bytes` is separate from `max_prefix_bytes` because a far fixed-offset
signature needs a ~32 KiB window that a 4 096-byte near budget would otherwise forbid.

Live budget fields today: `max_prefix_bytes` (near peek clamp), `max_far_bytes`,
`max_scan_bytes` (SFX window), `max_decode_input` / `max_decode_output` (receipt bounds /
inner-TAR probe limit), `spool_non_seekable_up_to`, `max_probe_links` (via
`within_budget`'s probe-seek allowance of `max_probe_links × 24` bytes, aligned with
the Brotli chain header read), and the skip-recording of `max_tail_bytes <= 0` as
*not enabled by policy*. Content-probe `read_at` seeks on cheap random-access sources
(path, full spool, non-`ArchiveStream` seekable streams) without growing the prefix
through `[0, offset)`; non-seekable and expensive-seek sources grow under a 1 MiB cap
and record `BUDGET_EXHAUSTED` past it. Remaining reserved fields
(`completion_window_bytes`, `max_index_bytes`, `collect_nonmaximal_candidates`, and the
ZIP-tail pair) MAY appear on the type and in presets so follow-on changes can wire them
without a second public shape break; no tier SHALL claim to honour them until those
changes land.

#### Scenario: receipt reflects the source kind

| Case | Expected |
| --- | --- |
| Path, near magic hit at offset 0 | `unique_bytes_read` is the single prefix read; `seeks` 0 |
| Growing 4 KiB → 32 KiB → 2 MiB | `unique_bytes_read` counts each byte once; `prefix_bytes` counts requests |
| A tier the preset does not enable (ZIP tail) | Recorded as *not enabled by policy* — a distinct reason, because it does not make the search incomplete |
| SFX scan miss under `FAST` | `scanned_bytes` ≤ `max_scan_bytes`; `unique_bytes_read` ≤ scan ceiling + probe allowance |
| SFX miss then extension guess (`.zip`) | `within_budget` is True under `BALANCED` and `FAST` |
| `detect_format(path)` / `open_archive(path)` with no config | BALANCED budget; caller never names a spend cap |
| `detect_format(..., budget=…)` | No such keyword; TypeError / the argument is gone |
