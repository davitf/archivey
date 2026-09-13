## MODIFIED Requirements

### Requirement: Declared capabilities compose with the two access modes

`streaming` SHALL remain the only access-mode choice. `seekable_members` /
`concurrent_members` SHALL declare stream capabilities **within** a mode (not a third
mode; no `ArchiveyConfig` equivalent). Ownership, leases, materialization, and
free-threaded rules for declared concurrency live in `reader-concurrency`; this
requirement only states how the capabilities compose with `streaming`.

| Mode | Capability composition |
| --- | --- |
| `streaming=False` | `concurrent_members` and/or `seekable_members` MAY be declared; concurrent-open semantics are `reader-concurrency`. Without `concurrent_members`, one live member stream (`archive-reading`). |
| `streaming=True` | Random `open`/`read` still unavailable. Single progressive pass is exclusive. **`concurrent_members=True` incompatible** → `ArchiveyUsageError` at open. `seekable_members=True` alone MAY be declared. |

Random-access `stream_members()` remains exclusive even when random `open()` is
otherwise available (simultaneous streams use materialize + random `open()` under
`concurrent_members=True` — see `reader-concurrency`). Detected pass/open/close overlap →
later op `ArchiveyUsageError`; active pass stays usable. Ops after `reader.close()` →
`ArchiveyUsageError` (idempotent `close`).

Defaults and behaviour are unchanged by the spelling: this requirement previously
described the same composition in terms of a `member_streams` flag enum.

#### Scenario: mode × capability matrix

| Case | Expected |
| --- | --- |
| `streaming=True` + `concurrent_members=True` | `ArchiveyUsageError` at open; no reader |
| RA + `concurrent_members=True` (or without) | Concurrent-open / single-live-stream rules per `reader-concurrency` / `archive-reading` |
| Active pass + conflicting pass/open/close | Later → `ArchiveyUsageError`; original pass usable |
| RA `stream_members` active + `open()` | `ArchiveyUsageError` |
| `extract_all` drives child `stream_members` | Permitted composition; unrelated public pass rejected |

### Requirement: Concurrent-stream cost is informational

`access_cost` / `solid_block_count` describe work (including under a declared
simultaneous schedule). They SHALL NOT permit or deny capabilities —
`concurrent_members` is the only gate (`reader-concurrency`). Solid open-*order* cost
is reported here and steered toward `stream_members()`, not gated.

#### Scenario: cost vs capability

| Case | Expected |
| --- | --- |
| `concurrent_members=True` on `DIRECT` and `SOLID` readers, multiple streams | Both supported and byte-correct; only reported/repeated work differs |
