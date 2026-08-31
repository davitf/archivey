# Detection Cost

## Purpose

Detection declares what it may spend (`DetectionBudget`) and reports what it spent
(`DetectionCostReceipt`), with capabilities derived from source and budget together.
A sibling of `access-mode-and-cost`'s archive-open `CostReceipt` — detection's I/O
happens before a reader exists.

**Stability note.** The types live in `archivey.detection_cost` and are accepted by
`detect_format(..., budget=)`, but they are **not** re-exported from `archivey.__all__`
yet. Public freeze of the root surface (top-level vs subpackage vs internal) is deferred
to `detection-result-surface` (Decision 3A on the prefix-workspace PR).

This spec describes **what ships today** after `detection-prefix-workspace`. Tiers and
knobs that the budget type *reserves* but that no code schedules yet are named explicitly
as reserved; they MUST NOT be read as current behaviour. The ZIP tail tier,
whole-source completion, probe-link capping, non-maximal candidate collection, and index
bounds land in `detection-evidence-ledger` / `prefixed-archive-detection` /
`detection-result-surface`.

## Related specs

| Spec | Relationship |
| --- | --- |
| `format-detection` | Tiers that spend against the budget |
| `access-mode-and-cost` | Shared vocabulary; detection receipt is not folded into `CostReceipt` |

## Requirements

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
    max_probe_links: int           # reserved → evidence-ledger (Brotli uses CHAIN_MAX_LINKS today)
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

`detect_format` SHALL accept a budget. The receipt SHALL be detection's own and SHALL NOT
be merged into the archive-open `CostReceipt`: detection's I/O happens before a reader
exists. `max_far_bytes` is separate from `max_prefix_bytes` because a far fixed-offset
signature needs a ~32 KiB window that a 4 096-byte near budget would otherwise forbid.

Live budget fields today: `max_prefix_bytes` (near peek clamp), `max_far_bytes`,
`max_scan_bytes` (SFX window), `max_decode_input` / `max_decode_output` (receipt bounds /
inner-TAR probe limit), `spool_non_seekable_up_to`, and the skip-recording of
`max_tail_bytes <= 0` as *not enabled by policy*. Content-probe `read_at` seeks on
cheap random-access sources (path, full spool, non-`ArchiveStream` seekable streams)
without growing the prefix through `[0, offset)`; non-seekable and expensive-seek
sources grow under a 1 MiB cap and record `BUDGET_EXHAUSTED` past it. Reserved fields
MAY appear on the type and in presets so follow-on changes can wire them without a
second public shape break; no tier SHALL claim to honour them until those changes land.

#### Scenario: receipt reflects the source kind

| Case | Expected |
| --- | --- |
| Path, near magic hit at offset 0 | `unique_bytes_read` is the single prefix read; `seeks` 0 |
| Growing 4 KiB → 32 KiB → 2 MiB | `unique_bytes_read` counts each byte once; `prefix_bytes` counts requests |
| A tier the preset does not enable (ZIP tail) | Recorded as *not enabled by policy* — a distinct reason, because it does not make the search incomplete |
| SFX scan miss under `FAST` | `scanned_bytes` and `unique_bytes_read` stay within `max_scan_bytes` |

### Requirement: Detection capabilities are derived from source and budget together

A detector SHALL declare the capabilities it needs, and the scheduler SHALL evaluate them
against `source.capabilities(budget)` — not against the source alone:

| capability | supplied when |
| --- | --- |
| `PREFIX` | always — a bounded head read through the prefix workspace |
| `SIZE_KNOWN` | a cheap total size is available (path, seekable stream, or fully-spooled source) |
| `REMAINING_KNOWN` | bytes from the caller's current position are provable, not estimated |
| `TAIL` | the source can be read near its end **and** `max_tail_bytes > 0` and `max_seeks > 0` — seekable, or spooled by explicit policy |
| `SEEK` | arbitrary range reads are allowed (`max_seeks > 0`) on a random-access source |
| `REREAD` | the source can be consumed and still presented to a backend afterwards |

An overestimated total size SHALL NOT be treated as proof that a later offset is reachable;
a size gate may skip a detector only when the remaining source is *provably* too short.
An abandoned spool (source exceeded `spool_non_seekable_up_to`) SHALL NOT report
`REMAINING_KNOWN` from the truncated buffer length.

#### Scenario: budget participates in the capability set

| Case | Expected |
| --- | --- |
| Ordinary file, `max_seeks = 0` | `SEEK` and `TAIL` absent despite a seekable source |
| Pipe, no spool policy | `TAIL` absent |
| Pipe, explicit spool policy within budget and `max_seeks > 0` | `TAIL` present |
| Pipe, spool abandoned past the bound | Prefix buffer retained; `REMAINING_KNOWN` absent |
| Caller-positioned seekable stream | `REMAINING_KNOWN` measured from the entry position, not from offset 0 |

### Requirement: Detection budget presets

The system SHALL provide three presets, and `BALANCED` SHALL be the default. Preset
behaviour below is what detection **runs today**; reserved knobs may differ in numeric
value across presets so follow-on changes inherit a ready table.

| preset | behaviour today |
| --- | --- |
| `BALANCED` | near prefix; far fixed-offset evidence; cued bounded SFX scan (`max_scan_bytes` = 2 MiB); bounded content probes; inner TAR; **no** ZIP tail; no whole-source completion; no exhaustive scan; no implicit spool |
| `FAST` | same tiers as `BALANCED` with a smaller SFX scan (`max_scan_bytes` = 256 KiB) and smaller decode ceilings |
| `THOROUGH` | same scheduled tiers as `BALANCED` today; reserved fields (`completion_window_bytes`, `max_probe_links`, `collect_nonmaximal_candidates`, `max_index_bytes`) carry the intended future values but are **not honoured** until `detection-evidence-ledger` wires them. ZIP tail stays off (`max_tail_bytes = 0`, `max_seeks = 0`) until `prefixed-archive-detection` schedules it |

The ZIP tail tier SHALL remain outside every preset until its aggregate cost is measured on
the founding backup workload, in seeks as well as bytes, **and** a caller exists. Format
boundedness proves the search is complete for the tiers a policy enables, not that every
reserved knob is live.

#### Scenario: preset boundaries (shipping)

| Case | `BALANCED` | `FAST` | `THOROUGH` (today) |
| --- | --- | --- | --- |
| Ordinary ZIP / gzip / ISO at a known offset | Found | Found | Found (same tiers) |
| MZ stub + junk, no archive magic, `.zip` name | Extension `GUESS`; scan charged ≤ 2 MiB | Extension `GUESS`; scan charged ≤ 256 KiB | Same as `BALANCED` |
| ZIP behind a non-cueing prefix (JPEG + appended ZIP) | Not found | Not found | Not found — tail tier not scheduled yet |
| `zipapp` (`#!` prefix + ZIP) | Not found — shebang is not an executable cue today | Not found | Not found |
| Exhaustive whole-source scan | Never | Never | Never (opt-in later, not by preset alone) |

### Requirement: Non-seekable sources degrade explicitly, and spooling is opt-in

Detection SHALL NOT implicitly buffer a whole pipe. Near and far prefix evidence and cued
forward scans SHALL still work through replay buffering; tiers that would require `TAIL`
or `SEEK` SHALL be recorded as unavailable or not-enabled-by-policy rather than attempted.

An explicit spool policy SHALL write at most `spool_non_seekable_up_to` bytes to a seekable
temporary file. When the source ends within the bound, the spool is the detection (and
future backend-shared) object. When the source exceeds the bound, detection SHALL keep the
already-spooled prefix plus the one-byte look-ahead that proved overflow, abandon further
spooling, record the tier as budget-exhausted, and SHALL NOT treat the truncated length as
a proven remaining size.

#### Scenario: pipe behaviour

| Case | Expected |
| --- | --- |
| Pipe, default budget | Prefix tiers run; ZIP tail recorded *not enabled by policy*; no unbounded buffering |
| Pipe, spool policy, source ends within budget | Spooled; `SIZE_KNOWN`; `TAIL` only when `max_seeks > 0` and `max_tail_bytes > 0` |
| Pipe, spool policy, source exceeds the budget | Spool abandoned within the bound; lookahead byte retained; `REMAINING_KNOWN` absent |
| Detection finds a format whose backend cannot consume the source | `open_archive` raises the capability error rather than opening |
