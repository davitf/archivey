# Spike findings — fileno path, source lifetime, and the Bug-3 trap

`rapidgzip==0.16.0`. Scripts: `scratchpad/probe_fileno.py`, `probe_pos.py`,
`probe_archivey_source.py`, `bug3_close.py`.

## 1. Does rapidgzip read a stream via `fileno`, and do our streams have one?

**rapidgzip uses the fd directly iff the source has a *valid* fileno; otherwise it drives the
Python `read`/`seek`/`tell` path.** Instrumented sources:

| Source | `_hasValidFileno` | What rapidgzip called during open+read |
| --- | --- | --- |
| `open(path,'rb')` file object | `True` | `fileno` only — **no** Python `read`/`seek`/`tell` |
| `io.BytesIO` | `False` | `fileno` (probe), then `tell` / `seekable` / `seek` / `read` |

So with a real fileno rapidgzip **bypasses the Python object entirely** and reads the fd.

**But archivey never gives it a valid fileno.** For a seekable *stream* source the single-file
reader already wraps it in `SharedSource` and hands the codec `self._shared.view(0)` — a
`SlicingStream` whose `.fileno()` **raises `UnsupportedOperation`**
(`_hasValidFileno == False`). Confirmed by spying on `_open_rapidgzip`: for both a real
`open()` file object *and* a `BytesIO`, rapidgzip receives a `SlicingStream`, not the raw source.

**Consequences for this change:**
- The "routing the accelerator through a `SharedSource` view forfeits the fileno fast path"
  worry is **moot** — stream sources already go through a fileno-less view today. Only **path**
  sources use rapidgzip's fd fast path (archivey passes the path string; rapidgzip opens its own
  fd). The design's Obstacle-1 open question ("does the accelerator have to read through a
  view?") is therefore answered *for stream sources*: it already does, at no new cost.
- The `SharedSource` needed for the multi-member scan-view **already exists** in the reader
  (`self._shared`); the accelerator is already reading a sibling `view(0)`. The scan just needs
  another `view(0)`. The remaining work is *layering* — the codec-level check stream must be able
  to reach a view factory (today it only gets the one view as `source`).

### 1a. The fd path is *not* position-independent

With a real fileno, after a full decode the caller file object's position had **moved** (5 → 0),
i.e. rapidgzip's fd handling shares the OS file *description* (a `dup`), not an independent
offset. So even a fileno source cannot be safely shared between the accelerator and a concurrent
scan on the same object — reinforcing why archivey wraps stream sources in locked `SharedSource`
views rather than handing rapidgzip the raw handle.

## 2. Bug 3 — trigger characterization and the trap

Each case run in an isolated subprocess (Bug 3 aborts the process); exit 134 = SIGABRT.

| Case | Result |
| --- | --- |
| Truncated **non-raising** bytes (`BytesIO`) through rapidgzip, read to EOF, closed | clean exit 0 — soft EOF, **no** abort |
| Source **closed underneath** a live accelerator, untrapped | **SIGABRT** — `std::invalid_argument: Cannot convert nullptr Python object …` (matches `known-issues.md` verbatim) |
| Same, wrapped in a trap that catches internally and serves benign EOF | **clean exit 0**; the `ValueError` is captured for re-raise on the archivey side |

**Findings:**
- The backstop's *actual target case* — a truncated single-member gzip of plain **non-raising**
  bytes — does **not** trip Bug 3. Truncated bytes just end; nothing raises into rapidgzip. So the
  backstop itself does not *require* the trap to be safe for its own target.
- The trap **does** contain the documented Bug-3 trigger (source raises/closed underneath a live
  accelerator): the abort becomes a normal Python exception. This validates the design's proposed
  mitigation and supports doing the trap now rather than deferring it.

**Caveats (do not overclaim):**
- The trap only helps where the fault surfaces **inside a wrapped Python call** (`readinto` /
  `seek` / `tell`). The close-underneath trigger does (`ValueError`); a synthetic reader that
  raised mid-decode did **not** reproduce the abort here because rapidgzip soft-EOF'd before
  re-entering the source — so not every path re-enters, and coverage should be proven with the
  existing wall-clock-timeout sweep, not assumed.
- `known-issues.md` also notes a *separate* finalizer-race trigger ("some path-source
  truncations/CRC mismatches can `std::terminate` during worker finalization after a Python
  exception"). The read/seek trap does not obviously cover that finalization path; keep it in
  scope for the sweep.

## Net effect on the change

1. Fileno objection to `SharedSource` views: **dismissed** for stream sources (already the path).
2. Multi-member scan: keep it, on an independent `SharedSource.view(0)` — it **cannot** be
   deferred to `gzip-multimember-detect-via-index`, which the index spike found infeasible.
3. Bug-3 trap: **feasible and effective** for the source-raises/closed trigger; recommend
   landing it with the backstop (maintainer's lean), with the sweep to bound the finalizer-race
   corner before calling it complete.
