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
    passes: int            # detection passes summed (2 after following a stub), each under the full budget
```

`detect_format` SHALL accept a budget. The receipt SHALL be detection's own and SHALL NOT
be merged into the archive-open `CostReceipt`: detection's I/O happens before a reader
exists. The receipt SHALL cover the work of the whole `detect_format` call: when a
stub-only executable is followed to its sibling split volume, the receipt and the skips
SHALL be those of both passes together, with each repeated skip kept once. Each pass
runs under the full budget, so the receipt SHALL say how many passes it sums (`passes`,
2 here) and `within_budget` SHALL judge it against that many budgets. `max_far_bytes` is separate from `max_prefix_bytes` because a far fixed-offset
signature needs a ~32 KiB window that a 4 096-byte near budget would otherwise forbid.

Live budget fields today: `max_prefix_bytes` (near peek clamp), `max_far_bytes`,
`max_scan_bytes` (SFX window), `max_decode_input` / `max_decode_output` (receipt bounds /
inner-TAR probe limit: compressed input is capped at the smaller of `max_decode_input` and
1 MiB, a decode that fails is charged like one that succeeds, and a probe cut short by the
cap records `inner_tar` as *budget exhausted*), `spool_non_seekable_up_to`, `max_probe_links` (via
`within_budget`'s probe-seek allowance of `max_probe_links × 24` bytes, aligned with
the Brotli chain header read), and the skip-recording of `max_tail_bytes <= 0` as
*not enabled by policy*. Content-probe `read_at` seeks on cheap random-access sources
(path, full spool, non-`ArchiveStream` seekable streams) without growing the prefix
through `[0, offset)`; non-seekable and expensive-seek sources grow under the smaller of
1 MiB and the budget's prefix/far/scan ceiling, and record `BUDGET_EXHAUSTED` past it. A
far signature that ends past a positive `max_far_bytes` SHALL be recorded as `far_magic`
*budget exhausted* rather than searched in a window too short to hold it, unless the
source is provably too short to hold it anyway.

`within_budget` SHALL compare every bounded counter with its limit, `far_bytes` against
`max_far_bytes` included; `prefix_bytes` is the one exception, because it bills
overlapping requests in full and `unique_bytes_read` stands in for it. A receipt that fails `within_budget` SHALL carry a *budget exhausted* or
*capability unavailable* skip naming the tier that was cut short, except where a budget
field is not yet honoured by the tier spending against it: the Brotli walk follows
`CHAIN_MAX_LINKS`, not `max_probe_links`, until `detection-evidence-ledger` wires it. Remaining reserved fields
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
| ISO under a `max_far_bytes` smaller than the `CD001` span | Extension `GUESS`; `far_magic` recorded *budget exhausted*; `far_bytes` 0 |
| `.tar.bz2` whose first block exceeds `FAST`'s decode input | Bare `BZ2`; `decode_input` ≤ 64 KiB; `inner_tar` recorded *budget exhausted*; `within_budget(FAST)` True |
| Expensive-seek source, content probe asks past the budget ceiling | `read_at` returns `None`; `content_probe_read_at` recorded *budget exhausted*; `within_budget` True |
| Stub-only `vol.exe` beside `vol.7z.001` | `SEVEN_Z`; the receipt includes the stub pass's SFX scan; `passes` 2; `within_budget` True; `zip_tail` recorded once |
| `max_decode_output` below one 512-byte TAR header | Inner-TAR probe not run; `inner_tar` recorded *budget exhausted*; nothing charged |
