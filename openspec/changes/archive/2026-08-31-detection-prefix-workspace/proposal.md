## Why

Detection's byte budget is bounded; its **access shape** is not, and on the sources where
seeking is expensive the shape is what costs. Measured on `main`, instrumenting a seekable
stream through `detect_format`:

| source | reads | forward seeks | backward seeks |
| --- | --- | --- | --- |
| gzip | 5 | 0 | **5** — the same 30 bytes fetched five times |
| ISO | 2 | 0 | **2** — 4 096 bytes, rewind, then 32 774 re-read *from zero* |
| ZIP, TAR | 1 | 0 | 1 |

`_peek_prefix` on a seekable stream does `tell()` → read → `seek(start)`, with no cache
between peeks, so every peek rewinds to the origin. The non-seekable path already behaves
correctly — `PeekableStream.peek` grows a forward buffer and never seeks — so the
discipline exists for the source type that *cannot* seek and is missing for the one where
seeking is most expensive. An HTTP range reader and a member stream from a solid 7z both
report `SEEKABLE`, and a rewind on the second re-decodes a block archivey elsewhere treats
as serious enough to warn about (`STREAM_REWIND_REDECOMPRESSES`).

The same primitive blocks correctness work, not only cost. `peek_more(length)` always
starts at the source origin, so a validator or probe cannot be handed a view relative to a
*candidate*. TAR's `ustar` sits at candidate offset 257, so a scan hit at absolute `H`
means a candidate origin of `H - 257` — makeself and TAR self-extracting support are not
implementable correctly until a candidate-relative range read exists. The investigation
lists this as an underspecified part of `prefixed-archive-detection`.

And detection has no cost vocabulary at all. It cannot say what it read, cannot be told
what it may spend, and cannot record that it skipped a tier. Every later piece of the
detection redesign — branch-and-bound stopping, "the search was incomplete", the ZIP tail
tier's default gate — needs a budget object and a receipt that do not exist.

## What Changes

- **One monotonically growing prefix workspace, owned by detection**, shared by every tier
  that reads from the front. Extending from 4 KiB to 32 KiB reads only the delta. A path
  keeps one handle; a seekable caller stream records its entry position, reads forward
  once, and restores once in an exception-safe exit; a non-seekable source uses the same
  replay buffer the backend will consume.
- **A stated I/O shape, normative rather than incidental.** Detection SHALL perform at most
  one forward-only pass from the detection origin, then at most one seek towards the end,
  then one read to end. No backward seek, and no re-reading of bytes already retrieved.
  This is affordable on every source kind, which is why it can be a flat rule instead of a
  cost model.
- **A candidate-relative range view** (`peek_range(candidate_origin, length)` or
  equivalent), so a validator or probe receives bytes positioned at the candidate rather
  than at the source origin.
- **`DetectionCapability`** — what a detector needs *from the source*: `PREFIX`,
  `SIZE_KNOWN`, `REMAINING_KNOWN`, `TAIL`, `SEEK`, `REREAD`. Derived from source **and**
  budget together, so an explicit spool policy can make a pipe `TAIL`-capable and a
  zero-seek budget can withdraw `SEEK` from an ordinary file.
- **`DetectionBudget` and a detection cost receipt**, in one shared vocabulary: prefix
  bytes, tail bytes, seeks, scanned bytes, decode input, decode output, index bytes,
  spooled bytes. The budget is the promise; the receipt is what happened. Detection's own
  receipt, not the archive-open `CostReceipt` — the I/O happens before a reader exists.
- **Presets**: `BALANCED` (default), `FAST`, `THOROUGH`. The ZIP tail tier stays **out of
  `BALANCED`** until its aggregate cost is measured on the founding backup workload; it is
  specified and available under `THOROUGH`.
- **An explicit spool policy for non-seekable sources**, bounded and spilling to a seekable
  temporary file rather than to unbounded RAM, shared with the backend. Off by default.

No **BREAKING** change: no public API is removed and no currently-detected input changes
its answer. `detect_format` gains a way to be told a budget.

## Capabilities

### New Capabilities

- `detection-cost` — the budget, the capability set, the receipt, the presets, and the
  spool policy. A new capability rather than more surface on `format-detection`, because
  `access-mode-and-cost` already owns the cost vocabulary this borrows and detection's
  cost model is consulted by tiers that live in several specs.

### Modified Capabilities

- `format-detection` — the non-consumption requirement gains the access-shape rule and the
  workspace; peeks become candidate-relative rather than origin-only.
- `access-mode-and-cost` — detection's receipt is named as a sibling of `CostReceipt`,
  sharing its vocabulary without being overloaded onto it.

## Decisions

- **A flat shape rule instead of a source cost model.** `StreamCapability` is a two-value
  ordering (`FORWARD_ONLY < SEEKABLE`), so nothing distinguishes an HTTP range reader from
  a local file, and the investigation records that as an open question. One forward pass
  plus at most one tail seek is affordable on *every* source, so the rule sidesteps the
  question rather than waiting on it. What remains unanswered is **sizing** — a
  range-request source counts round trips, not bytes — and that is recorded, not solved.
- **Capabilities are computed from source and budget together.** Written as
  `source.capabilities(budget)` rather than a field on the source, because the budget is
  half the answer. This is the mechanism the later stopping rule uses to record a tier as
  "unavailable" rather than "declined".
- **The ZIP tail tier's default stays gated on measurement.** Format-boundedness proves
  completeness, not affordability: 65 557 bytes is a hard ceiling, and one extra seek per
  file across a backup sweep is still a real cost that seek latency, not byte count, will
  dominate.
- **The budget's scope — aggregate per detection, or per candidate — is deliberately left
  open**, with a test that pins the invariant either way. See Impact.

## Impact

- Modules: `src/archivey/internal/detection.py` (`_peek_prefix`, `_make_probe_read_at`,
  `peek_more` closures — all three become views over one workspace),
  `src/archivey/internal/streams/peekable.py`, `src/archivey/internal/sfx.py`
  (`find_magic_in_prefix` gains candidate-relative hits), a new module for the budget and
  receipt.
- Public API: `DetectionBudget` and the preset enum become public; `detect_format` accepts
  a budget. `FormatInfo` is unchanged here — exposing the receipt is
  `detection-result-surface`.
- Tests: an instrumented source asserting the access shape (zero backward seeks, at most
  one tail seek) for every source kind and every tier; a delta-read assertion that growing
  4 KiB → 32 KiB → 2 MiB reads each byte once; candidate-relative views returning the same
  bytes a source-origin read would at the same absolute offset; capability derivation under
  a zero-seek budget and under a spool policy.
- **Open, and carried into `detection-evidence-ledger`**: whether budget limits are
  per-detection aggregates or per-candidate. The measured amplification that decides it —
  a 2 MiB window packed with back-to-back decoys yields 209 715 valid gzip headers and
  1.26 s / 1 365 MiB of successful decoding, 683-fold — is a scan-tier property, and the
  scan tiers are that change's. This change ships the receipt and a fuzz assertion that
  detection's aggregate cost stays inside the declared budget, which pins the invariant
  whichever way the scope resolves.
- Threat model: detection-time decode work is currently unbounded and is covered by no
  register entry — O1 scopes to *listing*-time metadata bombs and `ExtractionLimits` scopes
  to `extract`. Register it as an open gap here, resolved by whichever change lands the
  bound.
- Unblocks: `prefixed-archive-detection`'s makeself and TAR self-extracting needles, which
  need the candidate-relative view; `detection-evidence-ledger`'s scheduler, which needs
  capabilities, the budget and the receipt.
