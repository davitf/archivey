## Context

Detection today has three separate ways of reading the source, none of which share state:

- `_peek_prefix(source, length)` — reopens a path, peeks a `PeekableStream`, or does
  `tell()` → read → `seek(start)` on a seekable stream, with no cache between calls;
- the `peek_more` closure handed to the inner-TAR probe and the SFX scan, which is
  `_peek_prefix` again;
- `_make_probe_read_at`, a separate absolute-offset reader built for the Brotli chain walk,
  which opens its *own* path handle.

All three read from the source origin. None can express "bytes at this candidate's origin".

This change is the plumbing layer for the detection redesign in
`dev-docs/investigations/archive-format-detection-algorithm.md` §2, §10 and §11. It ships no
new detection tier and changes no answer; it makes the tiers that follow affordable and
implementable.

## Goals / Non-Goals

**Goals:**

- One workspace, one pass, no rewinds, on every source kind.
- Candidate-relative views, which several already-specified features need and cannot get.
- A budget, a capability set and a receipt, so later work can say "I skipped that" and
  "that cost this".

**Non-Goals:**

- Any new detection tier. The ZIP tail tier is *specified* here only to the extent of
  saying which preset it is in; `prefixed-archive-detection` owns it.
- The selection rule, evidence classes, stopping. `detection-evidence-ledger`.
- Exposing the receipt publicly on `FormatInfo` or the reader — `detection-result-surface`.
- Resolving the seek-cost question. §13 asks how to price a source where seeking is cheap
  to *permit* and expensive to *do*; this change sidesteps it with a shape rule and records
  the residue.

## Investigations

**Measured access shape**, instrumenting a seekable stream through `detect_format` on
`main`:

| source | reads | forward seeks | backward seeks | note |
| --- | --- | --- | --- | --- |
| gzip | 5 | 0 | 5 | the same 30 bytes fetched five times |
| ISO | 2 | 0 | 2 | 4 096 bytes, rewind, then 32 774 re-read from zero |
| ZIP, TAR | 1 | 0 | 1 | |
| unrecognised, 40 KB | 2 | 0 | 2 | |

The non-seekable path has zero backward seeks by construction, because
`PeekableStream.peek` grows a forward buffer. So the discipline this change makes normative
already exists for pipes and is missing for seekable sources — the inverse of where it is
needed.

**Cost of the missing cache in the scan tier.** The cued scan searches 64 KiB, 256 KiB,
1 MiB and 2 MiB prefixes. Under `peek_more(first_n_bytes)` a miss requests
64 + 256 + 1024 + 2048 KiB = 3 473 408 bytes to cover a 2 097 152-byte window — 1.65625×
for paths and seekable streams, and repeated copying for `PeekableStream`. A monotone
workspace makes it 1×.

**Why candidate-relative views are a correctness matter, not an optimisation.** TAR's
`ustar` sits at offset 257 *within a TAR*, so a scan hit at absolute `H` denotes a candidate
beginning at `H - 257`. With only origin-relative reads there is no way to hand the TAR
validator its 512-byte header. The same applies to makeself, where the gzip needle's
candidate origin is the needle position itself, and to any 7z or RAR payload behind a stub.

**Where the shape rule does and does not reach.** Near magic, the 32 775-byte far window,
the cued 2 MiB scan and the magic-less prefix probes are all forward reads from the origin,
so they compose into one growing pass. The tail tier is one seek towards the end and a read
to end. Exactly one thing does not fit: resolving an exact `payload_offset` requires walking
the central directory, which the end-of-central-directory record points *backwards* to and
which points backwards again to the earliest local header.

## Decisions

### 1. One workspace, requests expressed as ranges

`_peek_prefix`, the `peek_more` closures and `_make_probe_read_at` collapse into one object
that owns the source handle and a growing buffer. Consumers ask for ranges; the workspace
decides whether that is a buffer slice, a delta read, or (once, for the tail) a seek.

This is what makes the three properties below the same property rather than three fixes:
bytes are read once, seeks do not recur, and a range at a candidate origin is a view rather
than a fetch.

**Rejected: caching inside `_peek_prefix`.** It would fix the byte count and not the shape —
`_make_probe_read_at` still opens its own handle and still seeks — and it leaves three
readers where the problem is that there are three.

### 2. A flat access-shape rule, not a source cost model

One forward pass, at most one seek towards the end, one read to end. Affordable on every
source kind, so no code has to ask how expensive a seek would be — which is fortunate,
because nothing can currently tell it. `StreamCapability` is `FORWARD_ONLY < SEEKABLE`, and
an HTTP range reader, a member stream from a solid 7z and a local file are indistinguishable
to every gate in the system.

Two residues stay open and are recorded rather than solved:

- **Sizing.** Limits are counted in bytes and seeks; on a range-request source the
  meaningful unit is round trips. `BALANCED`'s 32 775 far bytes and the tail tier's 65 557
  are priced in the wrong currency even when the shape is right. This matters most for the
  tail tier's default gate, which is stated as an aggregate byte and seek threshold.
- **Declaration.** Nothing lets a source say which kind it is. `seekable_members=False`
  already keeps nested member streams forward-only by default, so the worst case is
  caller-declared; a seekable network source is not.

**Rejected: a third `StreamCapability` value now.** It is a public API change made to
answer a question the shape rule already removes from the hot path, and the investigation
lists it as one of three options with no measurement behind any of them.

### 3. Capabilities are a function of source **and** budget

`source.capabilities(budget)`, not a field. A zero-seek budget withdraws `SEEK` from an
ordinary file; an explicit spool policy grants `TAIL` to a pipe. Written this way because
the later stopping rule needs to distinguish "this tier could not run" from "this tier
declined", and both arms are half source and half budget.

Nested member streams need no new gate: `open_archive`'s `seekable_members` already defaults
to `False`, so `TAIL` is absent exactly where a rewind would re-decode a solid block —
provided each tier requires the capability rather than testing `seekable()` directly. That
proviso is the load-bearing part.

### 4. `max_far_bytes` is its own budget field

`BALANCED` described as "4 096 near; ISO far" is self-contradictory as a configuration: the
far descriptor needs a 32 775-byte prefix that a 4 096-byte prefix budget forbids. A
separate field is the smallest honest fix.

### 5. The ZIP tail tier stays out of `BALANCED`

The only §13 question this change settles, per the maintainer's decision. Format-boundedness
proves the search is *complete*, not *affordable*: measured over 71 983 files under `/usr`,
only 4.4% are large enough to pay the locator in full and the real aggregate is 0.61 GiB
against a 4.39 GiB worst case — but that is one additional seek per file across a whole
sweep, and seek latency rather than byte count will dominate on a network or spinning-disk
source. The accepted consequence is that a JPEG with an appended ZIP is a detection error at
the default budget.

### 6. Spooling spills to disk, and never happens implicitly

Buffering a whole pipe to make ZIP detection possible would change a streaming open into a
whole-input operation, and a socket may have no imminent end. The ZIP backend needs random
access anyway, so detection alone would not even solve the open. An explicit, bounded,
spill-to-file policy is the honest version of the capability, and its absence is recorded in
the result rather than silently degrading.

## Risks / Trade-offs

- [One workspace holding one open handle per detection changes lifetime management] → The
  seekable-stream path must restore the entry position in an exception-safe exit path; the
  existing non-consumption tests cover the observable half, and a new test asserts the
  handle is closed on the error path too.
- [The workspace retains more bytes than the old peek did] → It retains what the largest
  request needed, which is what the old code fetched repeatedly. The retained-byte ceiling
  is a budget field, so it is bounded rather than incidental.
- [A shape rule flatly forbidding backward seeks could block a future tier that needs one] →
  Then that tier states its own capability requirement and is excluded from the presets that
  cannot afford it, which is the mechanism decision 3 exists for. The central-directory walk
  is already the known instance and is scoped out.
- [Budget scope is unresolved and the scan tiers are where it bites] → The fuzz assertion
  below pins the invariant — detection's aggregate cost stays inside the declared budget —
  rather than the mechanism, so it holds whichever way the scope resolves and fails loudly
  if neither does.

## Open Questions

- **Aggregate or per-candidate budget limits.** Carried into `detection-evidence-ledger`,
  which owns the scan tiers where candidates multiply. The deciding measurement is already
  taken: a 2 MiB window packed with back-to-back decoys yields 209 715 valid gzip headers,
  and decoding each to a 64 KiB per-candidate cap costs 1.26 s and 1 365 MiB — 683-fold
  amplification that a per-candidate cap cannot bound. Memory is not the problem (each
  candidate's output is discarded); time is.
- **Pricing a source in round trips rather than bytes.** Decision 2's first residue. Needs a
  measurement on a real range-request source, which this container cannot supply.
- **Whether the detection receipt should be public at all**, and under what name. Deferred
  to `detection-result-surface`; nothing here depends on the answer.

## Sequencing

Depends on nothing. Lands after `detection-format-gaps` only to avoid two changes editing
`_detect_format_body`'s step order in parallel; the two are otherwise independent.

Blocks `detection-evidence-ledger` (which needs capabilities, budget and receipt) and
unblocks `prefixed-archive-detection`'s makeself and TAR self-extracting needles, which
cannot be implemented correctly without the candidate-relative view.

Registers a threat-model gap: detection-time decode work is unbounded today, and no register
entry covers it — O1 scopes to listing-time metadata bombs, `ExtractionLimits` to `extract`.
The entry is opened here and closed by whichever change lands the bound.
