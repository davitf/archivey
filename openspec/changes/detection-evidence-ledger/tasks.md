## 0. Order

- [ ] 0.0 **Implement this change fourth: after `detection-prefix-workspace`, before
      `detection-result-surface`.** It is the largest of the five and the only one that changes
      what existing callers observe, so everything cheap and self-contained should already have
      landed when it starts.

  **It is blocked by the workspace change**, which supplies `DetectionCapability`,
  `DetectionBudget` and the cost receipt the scheduler evaluates `affordable()` and
  `stop_now()` against. Attempting it first means building those twice.

  **Land it close behind `detection-format-gaps`, not long after.** That change widens the
  zlib probe gate from 4 accepted headers to 66 without its compensating rule; the stored-only
  regrade and the uniform `GUESS` for bounded probes are that rule, and they live here.

  **`prefixed-archive-detection` rebases onto this**, adding its three tiers as declarations on
  the scheduler rather than rewriting the tier code. Its revised delta drops the provisional
  note and the first-match-wins algorithm, both of which this change replaces.

  **Inherited from `detection-prefix-workspace` Decision 1B (spec honesty trim):** the budget
  fields `completion_window_bytes`, `max_index_bytes`, `max_probe_links`, and
  `collect_nonmaximal_candidates` are declared on `DetectionBudget` / presets but **not
  honoured** by any scheduled tier. Wire them here (tasks 5.6, 6.2, and the Brotli walk's
  link cap — today `CHAIN_MAX_LINKS` in `brotli_framing.py`). Do not re-claim them as live
  in `detection-cost` until this change archives.

## 1. Red tests first — the four measured defects

- [ ] 1.1 Failing test: each of the 15 registered magic entries, fed a source that is only
      that magic, reports `SIGNATURE_ONLY` → `PROBABLE`, not `CERTAIN`
- [ ] 1.2 Failing test: `zlib.compress(payload, 0)` (stored blocks only), too large to
      complete, is identified as `ZLIB` at `BOUNDED_PROBE` — not promoted by the decode; and
      the same stream small enough to complete reaches `COMPLETE`, its Adler-32 having verified
- [ ] 1.3 Failing test: the seven `/usr/share/perl` files whose first eight bytes are
      `"package "` are **not** claimed as Brotli — a 4096-byte prefix rejects them, a
      256-byte one does not
- [ ] 1.4 Failing test: a genuine Brotli stream under 64 KiB with no extension reports
      `CERTAIN` via completion, not `GUESS`
- [ ] 1.5 Failing test: a zero-filled file named `backup.gz` (and `.rar`, `.bz2`, `.xz`,
      `.zst`, `.br`, `.lzma`) fails with `format_unconfirmed=True`
- [ ] 1.6 Failing test: a genuine Brotli stream **above** the completion window named `x.br` reports `GUESS`, and a decode
      failure on it **is** stamped — the extension stops suppressing
- [ ] 1.7 Failing test: two candidates tied at the same class raise `AmbiguousFormatError`,
      caught by `except FormatDetectionError`
- [ ] 1.8 Failing test: a zero-byte source with no filename raises `FormatDetectionError`
      whose record names a capability shortfall, not an exhausted search

## 2. Evidence types and the ranking

- [ ] 2.1 `EvidenceClass`, `EvidenceKind`, `EvidenceAnchor`, `ValidationState`,
      `DetectionEvidence`, `FormatCandidate`
- [ ] 2.2 Total ordering over classes, with a test that it is total and that no code path
      sums evidence
- [ ] 2.3 `EvidenceAnchor.CANDIDATE` distinct from `ORIGIN`, so a prefixed archive's internal
      evidence stays anchored rather than degrading to floating
- [ ] 2.4 `estimated_random_bits` on the **record**, not the declaration — the same
      declaration yields different constraint on different sources

## 3. Declarations and the registry

- [ ] 3.1 `DetectionDeclaration` with `max_evidence`, `required_capabilities`,
      `estimated_cost`, `evaluate`
- [ ] 3.2 Evaluators return an iterable of candidates; absence is empty, never a sentinel
- [ ] 3.3 Convert each backend's `MAGIC` / `SFX_MAGIC` and each codec's probe to declarations
- [ ] 3.4 Split the content probes into a bounded-prefix declaration (`BOUNDED_PROBE`) and a
      whole-source completion declaration (`COMPLETE`), the second reusing the first's work
- [ ] 3.5 Keep gzip as **one** declaration ceilinged `SELF_VALIDATING` — assert this, since
      splitting it is the tempting wrong move
- [ ] 3.6 A cost estimate that cannot be computed states an upper bound, never zero

## 4. Structural validators

- [ ] 4.1 gzip: `CM`, reserved `FLG` bits, optional-field bounds, `FHCRC` verified when set
      and fully available (`INCOMPLETE` when not)
- [ ] 4.2 XZ stream-flags CRC32; LZ4 header xxHash; 7z `StartHeaderCRC` and next-header
      bounds; RAR main-header CRC
- [ ] 4.3 TAR: full 512-byte header parse, checksum accepted against **both** the unsigned
      POSIX and the historical signed sum, replacing bare `ustar`
- [ ] 4.4 TAR v7 without `ustar` as an anchored-only candidate requiring plausible numeric,
      type and name fields — and explicitly **not** a scan needle
- [ ] 4.5 bzip2 first block marker; `.Z` max-code width and reserved bits; lzip coded
      dictionary size; ISO descriptor tuple with type 255 rejected at sector 16
- [ ] 4.6 Each validator declares its failure disposition (reject / identified-but-damaged /
      `INCOMPLETE`) rather than the caller assuming one
- [ ] 4.7 Verify: a 7z with a failed `StartHeaderCRC` is still `SEVEN_Z`, and the read raises
      `CorruptionError` — identity is graded, not erased

## 5. Grading rules

- [ ] 5.1 `INCOMPLETE` validation caps the candidate at `SIGNATURE_ONLY`, whatever the
      declaration's ceiling says
- [ ] 5.2 Stored-only decode rule: a **bounded** decode producing only stored/uncompressed
      output does not promote the candidate above `BOUNDED_PROBE`. Whole-source completion is
      exempt — it verifies the format's own checksum (zlib's Adler-32 fails on one corrupted
      payload byte), so a completed stored stream legitimately reaches `COMPLETE`
- [ ] 5.3 A candidate is never rejected for being shorter than the format's minimum header
- [ ] 5.4 Raise `_PROBE_PREFIX` from 256 to `DETECTION_LIMIT` (4096) — the bytes are already
      peeked, so this is free; measured, it rejects 7 of 19 real fabrications that 256 bytes
      accepts, and runs marginally faster because rejections happen earlier
- [ ] 5.5 Assert the direction: a longer prefix can only reject more, never admit a candidate
      a shorter prefix rejected
- [ ] 5.6 Run whole-source completion under `BALANCED` when the remaining size is known and
      within the completion window (64 KiB), so a genuine small magic-less stream reaches
      `COMPLETE` → `CERTAIN` rather than `GUESS`

## 6. Scheduler and selection

- [ ] 6.1 Acquisition loop over declarations in tier order, filtered by
      `source.capabilities(budget)` and `affordable()`
- [ ] 6.2 Branch-and-bound skip on `can_dominate(d.max_evidence, winner)`, suppressed by
      `collect_nonmaximal_candidates` under `THOROUGH`
- [ ] 6.3 `merge_or_refine` before `select`, so class- or format-changing corroboration
      replaces a candidate instead of competing with it
- [ ] 6.4 Ordered priority keys 1–5, consulted one at a time, never summed
- [ ] 6.5 `stop_now`: unique winner **and** every unrun declaration incapable, unavailable, or
      budget-excluded — with the exclusion recorded
- [ ] 6.6 Scan resume is one byte past a rejected candidate's **start**, not past its claimed
      extent — otherwise a decoy can hide a real archive inside its declared range
- [ ] 6.7 Far evidence preceding probes must fall out of the ranking; assert there is **no**
      special-case arm for it
- [ ] 6.8 Record a skipped declaration with its **reason**: *not enabled by policy*,
      *budget-exhausted*, or *unavailable by capability*. Only the last two set
      `search_complete=False`
- [ ] 6.9 Regression pin for the reason this exists: under `BALANCED`, an ordinary gzip and an
      ordinary ZIP both report `search_complete=True` even though the ZIP tail tier is off.
      Conflating the reasons marks 90.7% of real archives incomplete
- [ ] 6.10 Assert `search_complete` gates nothing: `open_archive` opens regardless, and no
      code path branches on it

## 7. Confidence, provenance, ambiguity

- [ ] 7.1 `DetectionConfidence` becomes a projection over the winning class; three rows, no
      third arm on `CERTAIN`, no "well-calibrated" clause
- [ ] 7.2 A `NAME` record never raises confidence
- [ ] 7.3 `format_unconfirmed` keyed on the winning content-evidence class; remove
      `FormatInfo.corroborated`, `_extension_corroborates` and
      `_brotli_probe_confidence`'s `.br`-to-`PROBABLE` rule **together**
- [ ] 7.4 Rename `PROBE_FORMAT_UNCONFIRMED` to `FORMAT_UNCONFIRMED_ON_DECODE`, carrying the
      provenance in its typed context; keep the once-per-reader and `escalate_as` contracts
- [ ] 7.5 `AmbiguousFormatError(FormatDetectionError)` carrying tied candidates; propagate
      through `open_archive` / `open_stream`
- [ ] 7.6 Assert no error path branches on a `DetectionConfidence` value
- [ ] 7.7 Empty / sub-minimum sources: same exception type, record naming a capability
      shortfall

## 8. Property tests for the stopping rule

- [ ] 8.1 Over randomly generated declaration sets (ceilings, capabilities, costs) and stub
      sources with scripted evaluators — no real archives, so these run per commit
- [ ] 8.2 **Soundness**: the winner the scheduler stops on equals the winner an exhaustive run
      would select
- [ ] 8.3 **Order independence**: permuting declarations within a tier changes the receipt but
      not the winner, or raises `AmbiguousFormatError`
- [ ] 8.4 **Monotonicity in budget**, stated as two separate properties because conflating
      them contradicts the preset contract:
      (a) *for a winner both budgets find*, a larger budget never yields a weaker class and
      never yields a different winner — only more retained candidates;
      (b) enabling additional tiers may turn "no candidate" into a candidate, or a winner
      into one that **dominates** it, but never into an unrelated weaker one.
      A JPEG with an appended ZIP is the case that forces the split: not found under
      `BALANCED`, found via the tail tier under `THOROUGH`
- [ ] 8.5 **`search_complete` does not lie**: when true, no declaration was skipped because
      this run's budget or capabilities ran out. A declaration the policy never enables does
      **not** falsify it — see 6.8

## 9. Golden fixtures and fuzzing

- [ ] 9.1 One committed fixture per required case, with the **whole ledger** pinned — format,
      class, `payload_offset`, `search_complete`, and the `confidence` / `detected_by`
      projections — under the policy that case names
- [ ] 9.2 Required **non**-detections: the six `PK\x05\x06` false positives under `/usr/bin`
      (`zip`, `zipnote`, `zipsplit`, `zipcloak`, `libzip.so`, `librevenge-stream.so`) as
      committed byte fixtures, not as a live scan of the machine
- [ ] 9.3 Corrupted and truncated fixtures asserting a validator grades rather than erases
- [ ] 9.4 Structure-aware fuzzing of each validator: they all parse attacker-controlled length
      and count fields, and an unexpected exception type turns identification into a crash

## 10. Docs and register

- [ ] 10.1 `docs/formats.md` §Detection and `docs/gotchas.md`: the confidence meanings and the
      `format_unconfirmed` rule both change
- [ ] 10.2 `docs/errors-and-diagnostics.md`: `AmbiguousFormatError`, and the renamed diagnostic
- [ ] 10.3 Release-note prose for the two deliberate user-visible regressions — lower reported
      confidence on genuine `.br`/`.zz`/`.lzma`, and filename-only failures now stamping
- [ ] 10.4 Update `dev-docs/threat-model.md` O10: the residual clauses change once probe
      results are uniformly `GUESS` and uniformly stamped
- [ ] 10.5 Close the `FormatInfo.corroborated` entry in `dev-docs/IDEAS.md`, which names this
      change as its replacement
- [ ] 10.6 Revise `prefixed-archive-detection`: drop its provisional note and its
      first-match-wins algorithm, and restate its three tiers as declarations on this scheduler

## 11. Verify

- [ ] 11.1 `uv run --no-sync pytest tests/test_detection.py tests/test_corpus_sweep.py`
- [ ] 11.2 Property tests (group 8) and golden-ledger fixtures (group 9) pass
- [ ] 11.3 `uv sync --group fuzz --group dev --extra all && uv run --no-sync python -m tests.atheris_fuzz --smoke`
- [ ] 11.4 `./scripts/check.sh --fix`
- [ ] 11.5 `./scripts/test.sh --all-configs`
- [ ] 11.6 `openspec validate --strict detection-evidence-ledger`
