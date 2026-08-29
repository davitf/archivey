## Context

`_detect_format_body` is a straight line of five steps, each returning the first thing it
finds. `FormatInfo` carries the result as `(format, confidence, detected_by)` plus an
interim internal `corroborated: bool` added by `probe-provenance-unconfirmed` (#267) to key
`format_unconfirmed`.

`dev-docs/investigations/archive-format-detection-algorithm.md` is the redesign input,
accepted as such by `prefixed-archive-detection`'s own spec delta, which flags two of its
own rules as provisional and known-wrong pending this work. `dev-docs/IDEAS.md` records
`FormatInfo.corroborated` as interim and points at the same document for its replacement.

This change is the decision layer. `detection-prefix-workspace` supplies capabilities, the
budget and the receipt it schedules against; `detection-result-surface` exposes what it
produces.

## Goals / Non-Goals

**Goals:**

- One ranking, one selection rule, one predicate for `format_unconfirmed`.
- Validators sufficient to occupy the ranking — a lattice with one populated level is not a
  ranking.
- Make the four measured defects impossible rather than individually patched.

**Non-Goals:**

- Public exposure of the ledger, the widened `detected_by` values, the `sfx_scan` rename,
  the `detection=` handoff. All `detection-result-surface`.
- The ZIP tail tier, the widened prefix cue, the exhaustive scan.
  `prefixed-archive-detection`, revised against this.
- An all-candidates inspection API. Its name and shape stay open; only the **winner's**
  evidence is required, because error provenance depends on it.
- Settling what `detect_format()` reports when an exact `payload_offset` exceeds the index
  budget. Only the conservative floor is settled: never turn unknown into zero.
- gzip `XFL` / `OS` as identity gates.

## Investigations

All reproduced on `main` at `e54eff7` in this container.

**Every magic entry reports `CERTAIN` on a source that is only its magic.** Feeding each of
the 15 registered entries a source consisting of nothing but that signature:

| source | bytes | result |
| --- | --- | --- |
| `1f 8b` | 2 | `GZ` / `CERTAIN` / `magic` |
| `BZh` | 3 | `BZ2` / `CERTAIN` / `magic` |
| `PK\x03\x04` | 4 | `ZIP` / `CERTAIN` / `magic` |
| the other 12 | 4–32 774 | `CERTAIN`, without exception |

A gzip header is at minimum 10 bytes and a bzip2 stream header 4 plus a 6-byte block marker.
Nothing is malformed; the validator never ran.

**A decode that copied bytes counts as evidence.** `zlib.compress(payload, 0)` emits stored
deflate blocks — 200 000 in, 200 026 out — and detects as `ZLIB` / `PROBABLE` /
`content_probe`. The only real evidence is a two-byte header admitting 66 of 65 536 pairs,
about 2 to the minus 10.

**`format_unconfirmed` is backwards in both directions.** A 40 000-byte zero-filled file
named for each single-file codec is detected by extension at `GUESS`, opens, lists one
fabricated member, and fails on read with `format_unconfirmed=False`:

| named | read | flag today |
| --- | --- | --- |
| `.gz` `.bz2` `.xz` `.zst` `.br` `.Z` `.lz4` `.lz` `.zz` | `CorruptionError` | `False` |
| `.lzma` | `TruncatedError` | `False` |

Meanwhile a matching extension *suppresses* the flag on a genuine probe result. So the
filename is trusted to excuse a failure it caused, and to excuse one it did not.

**Extension corroboration buys nothing, measured on two trees.** #267's post-completeness
census: 29 fabrications, none with a matching extension. Re-run on a different tree of
63 343 files: 23 content-probe claims, 19 fabrications (0.030%), all 19 stamping, again zero
corroborated. Its cost is four genuine streams on that tree, of which two (`.brotli`, not a
registered extension) already carry the flag today — so removing the rule newly affects two
files, on a tree where two of the same kind already pay it silently.

**TAR at `SELF_VALIDATING` survives its obvious objection.** A 512-byte sum carries far fewer
bits than the CRC32s beside it, yet: **0 hits in 2 000 000 random 512-byte blocks**, and
across **80 378 real files** the gate accepted 175 blocks — 167 genuine tars and 8
deliberately-malformed tar fixtures, i.e. zero genuine false positives. The constraint is not
the numeric match but that eight bytes at offset 148 must parse as octal ASCII *and* equal
the sum, worth roughly 2 to the minus 30 on random data. Scan mode behaves the same because
candidates are generated needle-first: 2 MiB windows across those files produced **884
`ustar` candidates in the entire corpus**, about 0.011 per file.

**Same-class probe ties are reachable but not natural.** Running all three magic-less probes
independently over 33 947 real files gave 25 single-probe hits and **0** multi-probe hits. So
priority key 4 is not a hot path — but bytes can satisfy zlib's header and Brotli's framing
at once, and polyglots are in scope, so the rule still has to be right.

**The false-positive tail-probe warning, from a directory every developer has.** Of 3 320
ELF/PE files under `/usr/bin`, `/usr/lib`, `/usr/local` and `/opt`, **zero** carry a real
appended ZIP, and all **six** `PK\x05\x06` matches are false positives — `zip`, `zipnote`,
`zipsplit`, `zipcloak`, `libzip.so`, `librevenge-stream.so` — every one parsing to nonsense
(entry counts 19 280–55 381, directory offsets past end of file). A concrete instance of the
requirement that a tier **validate** rather than locate.

## Decisions

### 1. Totally ranked classes, never an additive score

Evidence is correlated: a `.br` suffix and a Brotli probe are not two independent
observations, and the base rate differs radically between `/usr`, a browser cache and a
backup corpus. Adding points would let a filename plus a weak decode outweigh a
checksum-validated header, which is the defect, not the fix.

**Rejected: anchoring as the top-level ordering rule.** "All anchored evidence first, cost
within that class, stop on a hit" recreates the original defect in a new form — a weak near
anchor stops before stronger far evidence. Anchoring is useful metadata and may refine within
a class; it is not the ranking. zlib has mandatory header grammar but no fixed byte string,
and v7 TAR has a checksum-valid header with no `ustar` anchor, so the two notions do not even
coincide.

**Rejected: a counted bit-set.** `dev-docs/IDEAS.md` reaches the same conclusion from the
other direction while recording why `corroborated: bool` cannot become public.

### 2. `max_evidence` is a ceiling, and the split rule is stated once

Split a detector into two declarations only when reaching the higher class costs materially
more. Both cases occur and getting it wrong breaks branch-and-bound outright:

- **Content probes split.** A single declaration ceilinged `COMPLETE` would make the stopping
  predicate permanently false — no detector could ever stop while probes were unrun, so every
  gzip, ZIP and 7z would pay for three probe decodes. Ceilinged `BOUNDED_PROBE`, the
  completeness rule could produce evidence *above* the declared ceiling, breaking the
  invariant. So: a cheap prefix declaration at `BOUNDED_PROBE`, and a separate whole-source
  completion at `COMPLETE` with its own cost and a capability requirement that the remaining
  size is known.
- **gzip does not split.** Verifying `FHCRC` is free once the header has been read.

The consequence is explainable: a small genuine `.br` is `GUESS` under `BALANCED` and
`CERTAIN` under `THOROUGH`, because the completion declaration is excluded by budget — the
stopping rule's own third arm, not an exception.

### 3. Confidence is a projection, with no third arm

Three rows, the seven classes, no overlap. An earlier revision of the analysis described
`PROBABLE` as covering a "well-calibrated" bounded probe while the surrounding text said
`BOUNDED_PROBE` projects to `GUESS` unconditionally — the two rows then both matched every
bounded probe, on exactly the case at issue, with "well-calibrated" undefined. It also gave
`CERTAIN` a third arm, "a header whose declared false-match risk is accepted as decisive",
which smuggles per-format judgement into a mechanical projection: accepted by whom, recorded
where? Both are gone. If a bounded probe should count as more than `GUESS`, that is a change
to its **class**, with measurement, not a second opinion at projection time.

### 4. One predicate for `format_unconfirmed`, keyed on the class

"Archivey chose the format, and the strongest content evidence is at or below
`BOUNDED_PROBE`." The `ASSERTED` exclusion falls out without a special case: we trust what
the caller says, not what the file says.

Filename-only lands on the yes side for a reason stronger than its rank — the extension
fallback is reached only because every content signal declined, which is the explicit absence
of evidence after trying.

**Rejected: reverting #267.** It never suppressed a stamp the previous confidence-keyed rule
produced — "old stamps, new does not" reduces to `GUESS` **and** corroborated, which is
unreachable. Reverting would restore the larger blind spot (Alone and zlib never stamping at
all) while leaving the filename rule in place via confidence.

**Rejected: a contradiction check under `format=`.** Tempting, and the common mistake is real
(`format=ZIP` on a `.tar.gz` raises `CorruptionError`, blaming the bytes for the caller's
claim). It fails on cost archivey cannot see: the check needs source-head bytes, which on a
caller-supplied stream means reading forward and seeking back. `StreamCapability` cannot
distinguish an HTTP range reader or a solid member stream from a local file, so the rewind is
an extra round trip or a re-decoded solid block — and would trip archivey's own
`STREAM_REWIND_REDECOMPRESSES` guard, on the one call where the caller asked for no
detection. Measuring it on a path (8.8 µs against 273 µs, 3.2%) is what hid this. So
`format=` performs **no detection I/O of any kind**, which is what makes it usable as an
escape hatch. What survives moves to the `detection=` handoff in
`detection-result-surface`, where the caller opts into paying.

### 5. Refinement runs before selection, so key 4 is the filename

An earlier revision offered an inner-TAR checksum as the tie-break example, which cannot
happen: by the time keys are consulted, refinement has already replaced the candidate. Key 4
is for evidence that distinguishes equals *without* being strong enough to promote either —
exactly the filename, and nothing else.

### 6. `THOROUGH` retains losing candidates; unbounded discovery is a different axis

Under `THOROUGH` every bounded declaration runs to completion even when its ceiling cannot
beat the current winner, so the ledger records everything the source could be said to be.
`BALANCED` runs only what can change the answer. The exhaustive scan is **not** on this axis:
its cost is not bounded by any format, so it stays its own opt-in.

This corrects a claim worth naming because it was wrong by the ranking's own table: an earlier
revision required far declarations to run even against a `SELF_VALIDATING` near winner, so a
polyglot ISO would be revealed. The ISO descriptor check is `DISCRIMINATING_HEADER`, which
cannot tie `SELF_VALIDATING`, so running it would change no outcome — it would only add a
losing candidate. That is what `THOROUGH` is for.

### 7. Validation failure grades; it does not erase

A 7z signature identifies a damaged 7z even when its `StartHeaderCRC` fails. The disposition
is format-declared: a failed mandatory check on a two-byte signature normally rejects; a
failed checksum after a six- to eight-byte identifier means "identified but damaged"; absence
of bytes is `INCOMPLETE` and never a proved mismatch.

**Rejected: rejecting a candidate whose source is shorter than the format's minimum header.**
Cleaner in the abstract and it discards the more useful answer — a truncated `.gz` off a
damaged backup should report "GZ, truncated", not "unknown format".

### 8. Ambiguity raises, and the error subclasses `FormatDetectionError`

Preserves existing broad handling while making a wrong-format choice loud. Deliberately
narrower than an all-candidates API, whose name and shape stay open.

## Risks / Trade-offs

- [Two deliberate user-visible regressions] → Reported confidence drops for genuine `.br`,
  `.zz` and `.lzma` files, and filename-only failures start carrying `format_unconfirmed`.
  Both are consequences of rules argued above, but a release note that does not say so will
  read as a bug. They need prose, not a changelog line.
- [The stopping rule is the part most likely to break under later edits] → Every declaration
  added to the registry changes it, and none of its invariants are local to the code being
  edited. Hence property tests over generated declaration sets rather than more fixtures:
  soundness (the winner stopped on equals the winner an exhaustive run would select), order
  independence, budget monotonicity, and that `search_complete` does not lie. Stub sources
  and scripted evaluators make them cheap enough to run per commit.
- [A test asserting `format` alone passes through every regression this change prevents] →
  Golden fixtures pin the **ledger**: format, class, `payload_offset`, `search_complete` and
  the projections. Two results with the same format can differ in what justifies them.
- [Validators parse attacker-controlled length and count fields] → Structure-aware fuzzing of
  the validators themselves; one that raises an unexpected exception type converts an
  identification into a crash.
- [The zlib gate widened in `detection-format-gaps` without its compensating rule] → The
  stored-only regrade here is that rule. Sequencing the two close together is the mitigation.

## Open Questions

- **What `detect_format()` reports when an exact `payload_offset` exceeds the index budget.**
  Three options: pay for the central-directory walk, raise a budget error, or separate
  identification from offset resolution and let the search-completeness record say
  "identified; exact offset not computed". Only the floor is settled — never turn unknown
  into zero. `detection-prefix-workspace`'s access-shape rule argues independently for the
  third, because the directory walk is the one thing that cannot be monotone.
- **Whether budget limits are per-detection aggregates or per-candidate**, carried from
  `detection-prefix-workspace`. Scan tiers are the only ones that multiply candidates, so it
  is a scan-tier question. The measurement is taken: 209 715 valid gzip headers in a 2 MiB
  decoy-packed window; decoding each to a 64 KiB cap costs 1.26 s and 1 365 MiB — 683-fold.
  Memory is not the problem, time is, and only an aggregate bounds it. A per-format
  scan-candidate cap is the alternative and costs decoy resistance. What to measure first:
  realistic scan-candidate counts on the founding backup corpus — if real prefixed archives
  yield 5 or fewer, a cap in the tens closes it at no cost.
- **Numeric thresholds behind the class boundaries**, and the probe-overlap matrix on real
  positives and negatives, so release notes can describe the behaviour cliff rather than
  discovering it from user reports.

## Sequencing

Depends on `detection-prefix-workspace` (capabilities, budget, receipt) and lands after
`detection-format-gaps` (whose three fixes are inside the validator table this change
generalises).

Blocks `detection-result-surface` and the revised `prefixed-archive-detection`. Per the
investigation, #257's revised delta drops its provisional note and its first-match algorithm,
and adds its three tiers as declarations onto this scheduler rather than rewriting the tier
code.
