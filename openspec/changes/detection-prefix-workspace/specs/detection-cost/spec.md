## ADDED Requirements

### Requirement: Detection declares what it may spend, and reports what it spent

The system SHALL expose a detection budget and a detection cost receipt in one shared
vocabulary — the budget is an upper bound, the receipt is measured work:

```python
@dataclass(frozen=True)
class DetectionBudget:
    max_prefix_bytes: int
    max_far_bytes: int
    max_tail_bytes: int
    max_seeks: int
    max_scan_bytes: int
    max_decode_input: int
    max_decode_output: int
    completion_window_bytes: int   # whole-source completion runs at or below this
    max_index_bytes: int
    max_probe_links: int
    spool_non_seekable_up_to: int
    collect_nonmaximal_candidates: bool

@dataclass(frozen=True)
class DetectionCostReceipt:
    prefix_bytes: int      # requested from the workspace
    unique_bytes_read: int # actually fetched from the source
    far_bytes: int
    tail_bytes: int
    scanned_bytes: int
    seeks: int
    decode_input: int
    decode_output: int
    index_bytes: int
    spooled_bytes: int
```

`detect_format` SHALL accept a budget. The receipt SHALL be detection's own and SHALL NOT
be merged into the archive-open `CostReceipt`: detection's I/O happens before a reader
exists. `max_far_bytes` is separate from `max_prefix_bytes` because a far fixed-offset
signature needs a 32 775-byte window that a 4 096-byte near budget would otherwise forbid.

#### Scenario: receipt reflects the source kind

| Case | Expected |
| --- | --- |
| Path, near magic hit at offset 0 | `unique_bytes_read` is the single prefix read; `seeks` 0 |
| Growing 4 KiB → 32 KiB → 2 MiB | `unique_bytes_read` counts each byte once; `prefix_bytes` counts requests |
| Tail tier runs | `tail_bytes` and one `seek` charged; `prefix_bytes` unchanged |
| A tier is skipped for budget | Nothing charged for it; recorded as *budget-exhausted* |
| A tier the preset does not enable | Recorded as *not enabled by policy* — a distinct reason, because it does not make the search incomplete |

### Requirement: Detection capabilities are derived from source and budget together

A detector SHALL declare the capabilities it needs, and the scheduler SHALL evaluate them
against `source.capabilities(budget)` — not against the source alone:

| capability | supplied when |
| --- | --- |
| `PREFIX` | always — a bounded head read through the prefix workspace |
| `SIZE_KNOWN` | a cheap total size is available |
| `REMAINING_KNOWN` | bytes from the caller's current position are provable, not estimated |
| `TAIL` | the source can be read near its end — seekable, or spooled by explicit policy |
| `SEEK` | arbitrary range reads |
| `REREAD` | the source can be consumed and still presented to a backend afterwards |

An overestimated total size SHALL NOT be treated as proof that a later offset is reachable;
a size gate may skip a detector only when the remaining source is *provably* too short.

#### Scenario: budget participates in the capability set

| Case | Expected |
| --- | --- |
| Ordinary file, `max_seeks = 0` | `SEEK` absent despite a seekable source |
| Pipe, no spool policy | `TAIL` absent |
| Pipe, explicit spool policy within budget | `TAIL` present |
| Caller-positioned seekable stream | `REMAINING_KNOWN` measured from the entry position, not from offset 0 |
| Nested member stream, `seekable_members=False` | `TAIL` and `SEEK` absent — no new gate needed beyond the capability check |

### Requirement: Detection budget presets

The system SHALL provide three presets, and `BALANCED` SHALL be the default:

| preset | behaviour |
| --- | --- |
| `BALANCED` | near prefix; far fixed-offset evidence; cued bounded scan; bounded probes over the full peeked prefix; whole-source completion for sources within the completion window (64 KiB); inner TAR; **no** ZIP tail tier; no exhaustive scan; no implicit spool |
| `FAST` | smaller tail/scan/decode budgets; the result reports an incomplete search; a weaker result SHALL NOT silently stand in for skipped stronger evidence |
| `THOROUGH` | `BALANCED` plus the ZIP tail tier, non-maximal candidate collection, whole-source completion with no size window, and the explicit embedded scan on a reopenable or seekable source |

The ZIP tail tier SHALL remain outside `BALANCED` until its aggregate cost is measured on
the founding backup workload, in seeks as well as bytes. Format-boundedness proves the
search is complete, not that it is affordable.

#### Scenario: preset boundaries

| Case | `BALANCED` | `THOROUGH` |
| --- | --- | --- |
| ZIP behind a non-cueing prefix (JPEG + appended ZIP) | Not found | Found via the tail tier |
| `zipapp` (`#!` prefix + ZIP) | Found via the cued scan | Found |
| Exhaustive whole-source scan | Never | Only on explicit opt-in, not by preset alone |
| Genuine 8 KB Brotli stream, no extension | Completes within the window → `CERTAIN` | `CERTAIN` |
| Genuine 800 KB Brotli stream, no extension | Above the window → `GUESS` | Completes → `CERTAIN` |

### Requirement: Non-seekable sources degrade explicitly, and spooling is opt-in

Detection SHALL NOT implicitly buffer a whole pipe. Near and far prefix evidence and cued
forward scans SHALL still work through replay buffering; tier that require `TAIL` or `SEEK`
SHALL be recorded as unavailable rather than attempted.

An explicit spool policy SHALL write at most `spool_non_seekable_up_to` bytes to a seekable
temporary file and share that object with the backend, spilling to disk in preference to
unbounded memory.

#### Scenario: pipe behaviour

| Case | Expected |
| --- | --- |
| Pipe, default budget | Prefix tiers run; tail tier recorded unavailable; no unbounded buffering |
| Pipe, spool policy, source ends within budget | Tail-capable; a prefixed ZIP becomes detectable |
| Pipe, spool policy, source exceeds the budget | Spool abandoned within the bound; tier recorded unavailable |
| Detection finds a format whose backend cannot consume the source | `open_archive` raises the capability error rather than opening |
