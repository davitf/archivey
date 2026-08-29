## 1. Red tests first — the four measured defects

- [ ] 1.1 Failing test: each of the 15 registered magic entries, fed a source that is only
      that magic, reports `SIGNATURE_ONLY` → `PROBABLE`, not `CERTAIN`
- [ ] 1.2 Failing test: `zlib.compress(payload, 0)` (stored blocks only) is identified as
      `ZLIB` but graded on its header, not on the decode
- [ ] 1.3 Failing test: a zero-filled file named `backup.gz` (and `.rar`, `.bz2`, `.xz`,
      `.zst`, `.br`, `.lzma`) fails with `format_unconfirmed=True`
- [ ] 1.4 Failing test: a genuine Brotli stream named `x.br` reports `GUESS`, and a decode
      failure on it **is** stamped — the extension stops suppressing
- [ ] 1.5 Failing test: two candidates tied at the same class raise `AmbiguousFormatError`,
      caught by `except FormatDetectionError`
- [ ] 1.6 Failing test: a zero-byte source with no filename raises `FormatDetectionError`
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
- [ ] 5.2 Stored-only decode regrade: output that is purely stored/uncompressed is graded on
      the header alone. Generalise Brotli's existing first-block gate to zlib's `BTYPE=00`
- [ ] 5.3 A candidate is never rejected for being shorter than the format's minimum header

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
- [ ] 8.4 **Monotonicity in budget**: a larger budget never yields a weaker class, and
      `THOROUGH` never returns a different winner than `BALANCED` — only more retained
      candidates
- [ ] 8.5 **`search_complete` does not lie**: when true, no declaration was skipped for budget
      or capability

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
