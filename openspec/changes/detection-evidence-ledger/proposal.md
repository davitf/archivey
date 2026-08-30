## Why

Detection answers with one format string and one of three confidence values, produced by
taking the first hit in registry order. Both halves are wrong in ways that are measurable
rather than theoretical, on `main` (`e54eff7`):

- **All near magic reports `CERTAIN`, including two bytes.** Feeding each of the 15
  registered magic entries a source consisting of *nothing but that magic* returns `CERTAIN`
  in all 15 cases. Two bytes of `1f 8b` cannot contain a gzip header, which is at minimum
  ten. Nothing is malformed — the validator simply never ran.
- **A decode that copied bytes counts as evidence.** `zlib.compress(payload, 0)` emits
  stored (`BTYPE=00`) deflate blocks; 200 000 bytes in, 200 026 out, detected as `ZLIB` /
  `PROBABLE` / `content_probe`. The decoder copied bytes. The only real evidence is a
  two-byte header admitting 66 of 65 536 pairs — about 2 to the minus 10.
- **First match in registry order is not a decision rule.** Where several detectors accept
  the same bytes — the three magic-less probes, or a genuine polyglot — the answer is
  whichever backend registered first. Not measurable as a failure rate, which is the
  problem: it is an undocumented intent policy.
- **`format_unconfirmed` blames the bytes for the filename's guess.** A 40 000-byte
  zero-filled file named `backup.gz`, `.bz2`, `.xz`, `.zst`, `.br` or `.lzma` is detected by
  extension, opens, and fails on read with `format_unconfirmed=False`. Only the filename
  ever claimed that format. Meanwhile a matching extension *suppresses* the flag on a probe
  result, which is the same filename being trusted in the other direction.

The independent design analysis
(`dev-docs/investigations/archive-format-detection-algorithm.md`) works all four through and
concludes that the fix is one mechanism, not four patches: detection should collect **typed
evidence** for candidates, compare **totally ranked evidence classes** with ordered
tie-breakers, and stop only when no unrun detector can change the winner.

## What Changes

- **Typed evidence, totally ranked.** A candidate accumulates `DetectionEvidence` records
  carrying a kind, a class, an anchor, an offset, bytes examined and a validation state.
  Classes, strongest first: `COMPLETE`, `SELF_VALIDATING`, `DISCRIMINATING_HEADER`,
  `SIGNATURE_ONLY`, `BOUNDED_PROBE`, `NAME`, `ASSERTED`. **Not an additive score** —
  evidence is correlated, and two weak correlated signals must never outrank one strong one.
- **Validators that realize the ranking.** gzip `FHCRC` when present, XZ stream-flags CRC,
  LZ4 header checksum, 7z `StartHeaderCRC` and bounds, RAR main-header CRC, the full TAR
  512-byte header checksum (both unsigned POSIX and historical signed sums) replacing bare
  `ustar`, the ISO descriptor tuple, bzip2's first block marker, `.Z` max-code width, lzip's
  coded dictionary size.
- **An `INCOMPLETE` validation caps a candidate at `SIGNATURE_ONLY`.** The signature matched
  and nothing corroborated it. Fixes the 15-of-15 result above. A truncated `.gz` still
  reports `GZ` — identification and completeness are different questions.
- **A decode counts as evidence only if it decoded something.** A *bounded* decode producing
  only stored or uncompressed output does not promote the candidate. Whole-source completion
  is exempt, because it verifies the format's own checksum.
- **The bounded probe is fed the prefix already peeked (4096 bytes), not a 256-byte sample.**
  Free — the bytes are held either way — and measured, it rejects 7 of 19 real fabrications
  that 256 bytes accepts, including seven Perl source files whose first eight bytes parse as
  a Brotli header declaring a 13 848-byte compressed meta-block that never materialises.
- **Whole-source completion runs at the default budget for sources within a 64 KiB window**,
  so a genuine small magic-less stream reports `CERTAIN` instead of `GUESS`. With both
  changes, all 4 genuine streams on the measured tree reach `COMPLETE` and **zero** of the 19
  fabrications rise above `GUESS`.
- **Confidence becomes a projection, not a second score.** `CERTAIN` = `COMPLETE` or
  `SELF_VALIDATING`; `PROBABLE` = `DISCRIMINATING_HEADER` or `SIGNATURE_ONLY`; `GUESS` =
  `BOUNDED_PROBE`, `NAME` or `ASSERTED`. **BREAKING (behavioural):** all three bounded
  probes — zlib, LZMA Alone, Brotli — become `GUESS`, including a `.br` file that a probe
  accepted. A `NAME` item never raises confidence.
- **`format_unconfirmed` keys on the winning content-evidence class.** Set when archivey
  chose the format and the strongest content evidence is at or below `BOUNDED_PROBE`.
  **BREAKING (behavioural):** filename-only failures start carrying it; a matching extension
  stops suppressing it. An explicit `format=` still never sets it — the caller took
  responsibility. Retires the interim `FormatInfo.corroborated` and
  `_brotli_probe_confidence`'s `.br`-to-`PROBABLE` rule, which are the same rule twice.
- **Branch-and-bound stopping.** Stop when the winner is unique and every unrun declaration
  is incapable of dominating it, unavailable by capability, or excluded by a budget that the
  result records. Far fixed-offset evidence beating content probes falls out of the ranking
  rather than needing a rule.
- **Ordered priority keys for ties**, consulted one at a time and never summed: evidence
  class; semantic position; end anchoring; matching filename; then ambiguous. Refinement —
  corroboration that changes the class or the format, such as an inner TAR turning `GZ` into
  `TAR_GZ` — happens before the keys and never reaches them.
- **`AmbiguousFormatError`**, a `FormatDetectionError` subclass carrying the tied maximal
  candidates. `open_archive` and `open_stream` propagate it rather than picking by registry
  order.
- **`search_complete` reports policy-completion, not tier-completion.** A detector the
  selected policy never enables is recorded as such and does not make the search incomplete;
  only a run that exhausted its own budget does. Conflating them would mark 1 840 of 2 029
  real archives (90.7%) incomplete, including every gzip and ZIP. The flag never blocks an
  open.
- **An empty or sub-minimum source says so.** Today it raises "no magic-byte match and no
  usable file extension", which is misleading when there were no bytes to match. Same
  exception type; the incomplete-search record distinguishes a capability shortfall from an
  exhausted search.

## Capabilities

### New Capabilities

### Modified Capabilities

- `format-detection` — the selection rule, the evidence classes and their validators, the
  confidence projection, the stopping rule and the ambiguity outcome. This is the change's
  centre of gravity.
- `error-handling` — `format_unconfirmed`'s predicate moves from probe-only provenance to
  the winning content-evidence class; `AmbiguousFormatError` joins the hierarchy.
- `diagnostics` — `PROBE_FORMAT_UNCONFIRMED` names one of three provenances for an event
  that is "a decode failed on a format archivey guessed"; it becomes provenance-neutral with
  the provenance in its context.
- `backend-registry` — a magic entry becomes a detection **declaration** carrying an
  evidence ceiling, required capabilities, a cost estimate and an evaluator.

## Decisions

- **`max_evidence` is a ceiling, not a prediction.** Split a detector into two declarations
  only when reaching the higher class costs materially more. Content probes split — a
  bounded prefix decode at `BOUNDED_PROBE` and a separate whole-source completion at
  `COMPLETE` — because one declaration ceilinged at `COMPLETE` would mean no detector could
  ever stop while probes were unrun, and every gzip would pay for three probe decodes. gzip
  does **not** split: verifying `FHCRC` is free once the header is read.
- **`GUESS` means "the bytes did not confirm this identity"**, not "this is probably wrong".
  A genuine `asset.js.br` a probe accepted is `GUESS`: the answer is very likely right and
  the bytes did not establish it. Two of the behavioural changes are deliberate,
  user-visible regressions and need release-note prose, not a changelog line — reported
  confidence drops for genuine `.br`, `.zz` and `.lzma` files.
- **A matching extension buys nothing on the false-positive side, measured twice.** #267's
  census: 29 fabrications, none with a matching extension. Re-run on a different tree of
  63 343 files: 19 fabrications, again zero corroborated. Its cost is two genuine files on
  that tree — and two others already pay it because `.brotli` is not a registered extension.
- **No error path branches on `confidence`.** Error provenance asks whether the winner was
  probe-only, name-only, structurally validated, or explicit.
- **Validation failure lowers a class; it does not erase an identity.** A 7z signature
  identifies a damaged 7z even when its `StartHeaderCRC` fails. Turning a useful
  `CorruptionError` into "unknown format" is the worse answer.

## Impact

- Modules: `src/archivey/internal/detection.py` (the scheduler and selection replace
  `_detect_format_body`'s straight-line steps), `src/archivey/internal/registry.py`
  (declarations), each backend's `MAGIC` declaration, the codec descriptors' probes,
  `src/archivey/exceptions.py`, `src/archivey/diagnostics.py`.
- Public API: `AmbiguousFormatError` is new. `DetectionConfidence` keeps its three values
  with new meanings. `FormatInfo.corroborated` (internal, `compare=False`) is removed. The
  evidence records themselves stay internal here — exposing them is
  `detection-result-surface`.
- Tests: a golden fixture per required case with the **whole ledger** pinned, not just the
  format — two results with the same format can differ in what justifies them, and a test
  asserting format alone passes through every regression this change prevents. Property
  tests over generated declaration sets for soundness, order independence, budget
  monotonicity and `search_complete` honesty; these need no real archives and run per commit.
  Structure-aware fuzzing of the validators, each of which parses attacker-controlled length
  and count fields.
- Docs: `docs/formats.md` and `docs/gotchas.md` describe the current confidence and
  `format_unconfirmed` rules and both change; `docs/errors-and-diagnostics.md` gains
  `AmbiguousFormatError`.
- Depends on `detection-prefix-workspace` for capabilities, budget and receipt. Blocks
  `detection-result-surface` and the revised `prefixed-archive-detection`.
