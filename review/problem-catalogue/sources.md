# `sources.md` — the inventory and coverage proof

Step 1 of [`brief.md`](brief.md) §Suggested process: **every** document named in
§Sources, listed with a state, written down *before* reading started. Coverage is
checkable only against a denominator recorded in advance.

Counts here match §Sources, which was measured against `main` @ `d4668c3`. They were
re-verified mechanically at the start of this pass (72 archived proposals carry a
`## Why`, 57 of those have a `design.md`, 17 ADRs, 11 archived review summaries, 8
investigations, 5 `history/` files) and not re-derived beyond that.

## States

| State | Meaning |
|---|---|
| `unread` | Not yet opened |
| `unread — attributed` | Not yet opened, but a document that **has** been read attributes one or more entries to it. The ids in the row are that attribution and are marked in the entry's own `Sources` line; reading the document confirms them and may add more. This is provenance chaining, not coverage — the row is still a gap |
| `mined` | Read; every problem statement in it is an entry in [`catalogue.md`](catalogue.md), by id |
| `no problem statement` | Read; states no problem, with the reason recorded |

A document may be `mined` and contribute **zero new** entries — that means every problem
it states was already in the catalogue from an earlier source. That is the dedupe working,
and the entry ids in its row are the proof. It is different from `no problem statement`,
which means the document does not state a problem at all.

## Where this pass stopped

<!-- KEEP THIS CURRENT AS YOU GO, NOT AT THE END. A fresh container has no memory of
     the session; this section plus the tables below are the entire handoff. -->

**Session 1 (2026-08-17) stopped after the neutral-source groups.** Read in the
brief's mandated order and complete:

| Group | State |
|---|---|
| 1. `dev-docs/history/` (5 files) | **complete** |
| 2. `dev-docs/investigations/` (8) | **3 of 8** — `parallel-reader`, `rapidgzip-upstream-report`, `rar-corpus-sweep-diagnosis`. The four PPMd files and `adr-0014-investigation` are unread; their conclusions reached the catalogue through `known-issues.md` and ADR 0014, so entries exist (`UL-08`–`UL-10`, `UL-18`, `UL-06`, `API-16`) but the primary documents have not been checked for anything those summaries dropped |
| 3. Standing registers (4) | **complete** |
| 4. Unsettled / parked (5) | **unread** |
| 5. `dev-docs/decisions/` (17 ADRs) | **complete** |
| 6. `review/archive/*/SUMMARY.md` (11) | **unread** |
| 7. Archived proposals (72 `## Why`, 57 `design.md`) | **unread** |
| 8. Topic 8 harvest | **outstanding** — see §8 |

**Catalogue at this point: 100 entries.** Next action is group 2's remaining five files,
then groups 4, 6 and 7 in that order (the brief's ordering puts the already-problem-shaped
sources before the solution-contaminated ones).

**What the read-so-far groups already tell us about the unread ones.** 39 of the 100 entries
cite `open-issues.md`, which is itself a triage over the reviews and the user-facing gotchas,
and 24 cite `threat-model.md`. Both aggregate material that originates in the review
summaries and the proposals. So groups 6 and 7 are expected to yield **more sources per
existing entry** than new entries — which is the dedupe working, and is why their rows carry
entry ids rather than being blank.

---

## 1. `dev-docs/history/` — 5 files

Read **first**, per §Sources: the only source written before the current design existed,
so its problem statements are natively neutral.

| Document | State | Entries |
|---|---|---|
| `history/ARCHITECTURE.md` | mined | `FQ-01`, `FQ-03`, `FQ-04`, `FQ-05`, `FQ-06`, `FQ-14`, `FQ-15`, `FQ-16`, `FQ-17`, `FQ-18`, `FQ-19`, `SEC-01`, `SEC-02`, `SEC-03`, `SEC-04`, `PLAT-01`, `PLAT-02`, `PERF-01`, `PERF-02`, `API-03`, `API-04`, `API-06`, `API-07`, `API-08`, `API-10`, `API-11`, `PKG-01`, `PKG-04`, `PKG-05`, `PKG-09`, `CONC-02`, `CONC-04` |
| `history/ASYNC.md` | mined | `PERF-06`, `API-10` |
| `history/COMPARISON.md` | mined | `FQ-01`, `FQ-03`, `FQ-05`, `FQ-06`, `FQ-07`, `FQ-08`, `FQ-09`, `FQ-10`, `FQ-11`, `FQ-12`, `FQ-13`, `FQ-14`, `FQ-15`, `FQ-19`, `SEC-01`, `SEC-03`, `PLAT-03`, `PERF-01`, `PERF-02`, `API-02`, `API-03`, `API-04`, `API-05`, `API-08`, `PKG-06`, `PKG-07`, `CONC-02` |
| `history/SPEC.md` | mined | `FQ-01`, `FQ-02`, `FQ-03`, `FQ-04`, `FQ-05`, `FQ-06`, `FQ-07`, `FQ-08`, `FQ-09`, `FQ-10`, `FQ-11`, `FQ-13`, `FQ-14`, `FQ-15`, `FQ-16`, `FQ-17`, `FQ-18`, `FQ-19`, `FQ-20`, `FQ-21`, `SEC-01`, `SEC-02`, `SEC-03`, `SEC-04`, `PLAT-01`, `PLAT-02`, `PLAT-03`, `PERF-01`, `API-01`, `API-02`, `API-03`, `API-04`, `API-05`, `API-06`, `API-07`, `API-08`, `API-10`, `API-11`, `API-12`, `PKG-01` |
| `history/index.md` | no problem statement — triage router: a status table pointing at the four documents above, with a suggested-triage list. States no problem of its own |  |

## 2. `dev-docs/investigations/` — 8 files

| Document | State | Entries |
|---|---|---|
| `investigations/adr-0014-investigation.md` | unread — attributed | `UL-06`, `API-16` |
| `investigations/parallel-reader.md` | mined | `FQ-06`, `UL-03`, `PERF-06`, `CONC-01`, `CONC-03`, `CONC-04`, `CONC-05` |
| `investigations/ppmd-exit-after-green-exploration.md` | unread — attributed | `UL-08`, `UL-10` |
| `investigations/ppmd-native-investigation-brief.md` | unread — attributed | `UL-08` |
| `investigations/ppmd-native-investigation-results.md` | unread — attributed | `UL-08`, `UL-09`, `UL-10`, `UL-18` |
| `investigations/pyppmd-upstream-report.md` | unread — attributed | `UL-08` |
| `investigations/rapidgzip-upstream-report.md` | mined | `UL-05`, `UL-06`, `UL-15` |
| `investigations/rar-corpus-sweep-diagnosis.md` | mined | `FQ-03`, `FQ-18`, `SEC-15`, `PKG-05`, `PKG-09`, `CONC-02` |

## 3. Standing registers — 4 files

| Document | State | Entries |
|---|---|---|
| `dev-docs/threat-model.md` (9 `O` entries) | mined | `FQ-17`, `UL-02`, `UL-14`, `SEC-01`, `SEC-03`, `SEC-05`, `SEC-06`, `SEC-07`, `SEC-08`, `SEC-09`, `SEC-10`, `SEC-11`, `SEC-12`, `SEC-13`, `SEC-18`, `PLAT-05`, `PERF-06`, `API-09`, `PKG-04`, `PKG-08`, `CONC-01`, `CONC-03`, `CONC-04`, `CONC-05` |
| `dev-docs/known-issues.md` (6 sections / 709 lines) | mined | `FQ-02`, `FQ-20`, `UL-01`, `UL-02`, `UL-03`, `UL-04`, `UL-05`, `UL-06`, `UL-07`, `UL-08`, `UL-09`, `UL-10`, `UL-15`, `UL-18`, `PKG-03`, `PKG-08`, `CONC-05` |
| `dev-docs/library-analysis.md` (362 lines) | mined | `FQ-02`, `FQ-12`, `UL-04`, `UL-06`, `UL-07`, `UL-11`, `UL-12`, `UL-13`, `PERF-02`, `PERF-03`, `PERF-04`, `PERF-05`, `PKG-02`, `PKG-03`, `PKG-06`, `PKG-07` |
| `dev-docs/open-issues.md` (310 lines) | mined | `FQ-02`, `FQ-04`, `FQ-06`, `FQ-07`, `FQ-08`, `FQ-16`, `FQ-17`, `FQ-21`, `FQ-22`, `UL-01`, `UL-02`, `UL-05`, `UL-06`, `UL-08`, `UL-16`, `UL-17`, `UL-18`, `SEC-11`, `SEC-13`, `SEC-14`, `SEC-15`, `SEC-16`, `SEC-17`, `SEC-18`, `PLAT-03`, `PLAT-04`, `PLAT-05`, `PERF-02`, `PERF-04`, `PERF-05`, `API-01`, `API-04`, `API-06`, `API-13`, `API-14`, `PKG-04`, `PKG-08`, `CONC-02`, `CONC-03` |

## 4. Unsettled / parked — 5 files

| Document | State | Entries |
|---|---|---|
| `dev-docs/IDEAS.md` | unread — attributed | `PLAT-05` |
| `discussions/2026-08-diagnostics/diagnostics-archive-vs-usage.md` | unread — attributed | `API-09` |
| `discussions/2026-08-diagnostics/reviewer-1-opinion.md` | unread — attributed |  |
| `discussions/2026-08-diagnostics/reviewer-2-opinion.md` | unread — attributed |  |
| `discussions/2026-08-diagnostics/reviewer-3-opinion.md` | unread — attributed |  |

## 5. `dev-docs/decisions/` — 17 ADRs

The **Context** section is the source; the Decision section is field 4 material.
`index.md` is a router, not a source (§Sources), and is listed for completeness only.

| Document | State | Entries |
|---|---|---|
| `0001-native-7z-not-py7zr.md` | mined — no new entry; its context is stated by earlier sources |  |
| `0002-native-rar-metadata-unrar-data.md` | mined | `FQ-17`, `PKG-04` |
| `0003-member-streams-opt-in.md` | mined | `API-15`, `CONC-01` |
| `0004-streaming-bool-not-intent-enum.md` | mined | `API-05`, `API-15` |
| `0005-sync-only-v1.md` | mined | `API-10` |
| `0006-stdlib-zipfile.md` | mined — no new entry; its context is stated by earlier sources |  |
| `0007-mutable-archive-member.md` | mined | `FQ-01`, `API-03` |
| `0008-single-accelerator-rapidgzip.md` | mined | `UL-03`, `UL-04`, `CONC-05` |
| `0009-zstd-stdlib-backports.md` | mined | `UL-11`, `PKG-02`, `PKG-07` |
| `0010-no-silent-buffer-nonseekable.md` | mined | `FQ-04`, `API-01` |
| `0011-zero-dependency-core.md` | mined | `PKG-01`, `PKG-06` |
| `0012-usage-errors-outside-archiveyerror.md` | mined | `API-07` |
| `0013-cross-platform-name-safety-policies.md` | mined | `SEC-06`, `SEC-07`, `SEC-08`, `SEC-19` |
| `0014-integrity-verdicts-from-reads-not-close.md` | mined | `UL-06`, `PERF-03`, `API-16` |
| `0015-zero-filled-files-are-valid-empty-tars.md` | mined | `FQ-20`, `UL-01` |
| `0016-committed-rar-corpus-fixtures.md` | mined | `PKG-05`, `PKG-09` |
| `0017-bidi-override-rejection-is-policy-keyed.md` | mined | `SEC-19` |
| `index.md` | router, not a source | — |

## 6. `review/archive/*/SUMMARY.md` — 11 completed reviews

Sources, not subjects: findings are mined, conclusions are not reopened (§Relationship to
the other topics).

| Document | State | Entries |
|---|---|---|
| `2026-07-12-codebase-deep-review/SUMMARY.md` | unread — attributed | `UL-01`, `SEC-05` |
| `2026-07-16-crypto/SUMMARY.md` | unread — attributed | `UL-14`, `SEC-13`, `SEC-14`, `SEC-15`, `SEC-16`, `SEC-17` |
| `2026-07-16-rar-reader/SUMMARY.md` | unread — attributed |  |
| `2026-07-16-stream-decoder/SUMMARY.md` | unread — attributed |  |
| `2026-07-17-cli/SUMMARY.md` | unread — attributed |  |
| `2026-07-19-api-coherence/SUMMARY.md` | unread — attributed | `API-07`, `API-12` |
| `2026-07-19-stream-layering/SUMMARY.md` | unread — attributed | `PERF-03`, `API-16` |
| `2026-07-20-cli-product/SUMMARY.md` | unread — attributed |  |
| `2026-07-28-debt-ledger/SUMMARY.md` | unread — attributed |  |
| `2026-07-28-performance/SUMMARY.md` | unread — attributed |  |
| `2026-08-15-simplicity-consistency/SUMMARY.md` | unread — attributed | `FQ-20`, `SEC-09`, `SEC-10`, `SEC-19`, `API-14`, `PKG-05`, `PKG-09` |

## 7. `openspec/changes/archive/*/` — 72 proposals with a `## Why`, 57 with a `design.md`

Two states per row: the proposal's `## Why` block, and the `design.md` if the change has
one. `n/a` in the design column means the change has no `design.md` — that is not a gap.

| Change | `## Why` | `design.md` | Entries |
|---|---|---|---|
| `2026-06-19-phase-1-scaffold-and-spine` | unread | n/a |  |
| `2026-06-21-phase-2-stream-layer` | unread | n/a |  |
| `2026-06-27-stream-wrapper-base` | unread | n/a |  |
| `2026-06-30-compression-library-evaluation` | unread — attributed | n/a | `UL-11`, `PKG-02` |
| `2026-06-30-package-layout-restructure` | unread | n/a |  |
| `2026-06-30-phase-3-indexed-leaf-formats` | unread — attributed | n/a | `UL-13` |
| `2026-07-01-codec-descriptor-refactor` | unread | n/a |  |
| `2026-07-01-zstd-stdlib-backend-migration` | unread — attributed | n/a | `UL-11` |
| `2026-07-03-minimal-name-normalization` | unread | unread |  |
| `2026-07-03-phase-4-safe-extraction` | unread | n/a |  |
| `2026-07-04-inner-tar-probe-block-codecs` | unread — attributed | n/a | `FQ-13` |
| `2026-07-04-live-decompression-ratio-guard` | unread | unread |  |
| `2026-07-07-phase-5-public-api` | unread | unread |  |
| `2026-07-07-retire-dev-oracle` | unread | unread |  |
| `2026-07-07-scan-members` | unread — attributed | unread | `API-06` |
| `2026-07-10-parallel-reader-exploration` | unread — attributed | unread | `PERF-06` |
| `2026-07-11-concurrent-member-streams` | unread — attributed | unread | `API-15`, `CONC-01` |
| `2026-07-11-diagnostics-warnings-as-data` | unread — attributed | unread | `API-09` |
| `2026-07-11-tar-concurrent-open` | unread — attributed | unread | `CONC-01` |
| `2026-07-11-zip-multipassword-disambiguation` | unread — attributed | unread | `SEC-14` |
| `2026-07-12-anti-member-type-and-nonfile-open` | unread | unread |  |
| `2026-07-12-atheris-fuzz-harness` | unread — attributed | unread | `UL-02`, `SEC-12`, `SEC-18` |
| `2026-07-12-hypothesis-property-tests` | unread | unread |  |
| `2026-07-12-listing-resource-limits` | unread — attributed | unread | `SEC-05` |
| `2026-07-12-native-7z-reader` | unread | unread |  |
| `2026-07-12-native-rar-reader` | unread | unread |  |
| `2026-07-12-phase-4-tar-streaming` | unread | n/a |  |
| `2026-07-12-promote-concurrent-member-streams` | unread — attributed | unread | `API-15` |
| `2026-07-12-shared-source-streams` | unread — attributed | unread | `CONC-01` |
| `2026-07-14-adversarial-string-corpus-contract` | unread — attributed | unread | `SEC-19` |
| `2026-07-14-decompressor-stream-composition` | unread | unread |  |
| `2026-07-14-rar-blake2sp-verification` | unread — attributed | unread | `SEC-15` |
| `2026-07-14-refactor-sevenzip-reader` | unread | unread |  |
| `2026-07-14-stored-digest-dedupe-parity` | unread | unread |  |
| `2026-07-14-vendor-unix-compress-lzw` | unread — attributed | unread | `PERF-05` |
| `2026-07-14-zip-name-encoding-sniffing` | unread — attributed | unread | `FQ-07` |
| `2026-07-15-atheris-harness-depth` | unread — attributed | unread | `SEC-18` |
| `2026-07-15-benchmark-gate` | unread | unread |  |
| `2026-07-15-extraction-progress-in-file` | unread | unread |  |
| `2026-07-15-rapidgzip-deflate-zlib-acceleration` | unread | unread |  |
| `2026-07-15-rar-file-version-members` | unread — attributed | unread | `FQ-22` |
| `2026-07-15-zip-aes-decryption` | unread | unread |  |
| `2026-07-15-zip-native-codec-streams` | unread | unread |  |
| `2026-07-16-cross-platform-name-safety` | unread — attributed | unread | `SEC-06`, `SEC-07` |
| `2026-07-17-cli-v1` | unread | unread |  |
| `2026-07-18-partial-members-and-errors` | unread — attributed | unread | `PERF-04`, `API-16` |
| `2026-07-18-sevenzip-header-cursor-parse` | unread | unread |  |
| `2026-07-19-clarify-extraction-status-names` | unread | unread |  |
| `2026-07-19-decide-strict-archive-eof-default` | unread — attributed | unread | `UL-01` |
| `2026-07-19-surface-stored-stream-digests` | unread — attributed | unread | `PERF-03` |
| `2026-07-20-stop-on-failure-not-policy` | unread | unread |  |
| `2026-07-24-gzip-zlib-truncation-recovery` | unread — attributed | unread | `PERF-04`, `API-16` |
| `2026-07-24-rapidgzip-truncation-investigation` | unread | unread |  |
| `2026-07-24-unify-pass-driver` | unread | unread |  |
| `2026-07-25-gzip-truncation-backstop-any-seekable` | unread | unread |  |
| `2026-07-30-consolidate-optional-extras` | unread — attributed | unread | `UL-09`, `PKG-03`, `PKG-06` |
| `2026-07-30-member-stream-capability-booleans` | unread — attributed | unread | `API-15`, `CONC-03` |
| `2026-07-31-rename-extras-in-remaining-specs` | unread | unread |  |
| `2026-08-01-short-read-source-contract` | unread | unread |  |
| `2026-08-03-docs-ia-unpublish-maintainer-tree` | unread | unread |  |
| `2026-08-04-docs-ia-split-user-guide` | unread | unread |  |
| `2026-08-06-close-member-streams-on-reader-close` | unread — attributed | n/a | `UL-05`, `UL-16`, `UL-17`, `API-14`, `CONC-03` |
| `2026-08-06-reject-format-override-on-directory` | unread — attributed | n/a | `API-13` |
| `2026-08-06-spec-drop-unimplemented-solid-warning` | unread | n/a |  |
| `2026-08-09-decouple-member-metadata-from-declared-seekability` | unread — attributed | unread | `PLAT-01` |
| `2026-08-09-format-availability-required-source` | unread — attributed | unread | `PKG-01` |
| `2026-08-09-reject-bidi-overrides-in-safe-extraction` | unread — attributed | unread | `SEC-19` |
| `2026-08-09-review-diagnostics-batch` | unread — attributed | unread | `API-09` |
| `2026-08-09-rewind-diagnostic-redecode-cost` | unread | unread |  |
| `2026-08-09-strict-archive-eof-trailing-bytes` | unread — attributed | unread | `FQ-20` |
| `2026-08-15-escape-cli-log-records` | unread — attributed | n/a | `SEC-09` |
| `2026-08-15-extraction-results-authoritative` | unread — attributed | unread | `API-09` |

### Archived changes with no `proposal.md` — outside the denominator

Five archived changes carry only `specs/` + `tasks.md`. They are not part of §Sources'
count of 72 and state no problem of their own; the spec deltas they carry are behaviour,
not problem statements.

| Change | State |
|---|---|
| `2026-07-12-reader-concurrency-coordination` | no problem statement (no `proposal.md`; specs + tasks only). Attributed `CONC-01` from `investigations/parallel-reader.md` §2 |
| `2026-07-12-support-lzma1-bcj` | no problem statement (no `proposal.md`; specs + tasks only). Attributed `UL-12` from `library-analysis.md` §LZMA1/LZMA2 |
| `2026-07-13-nameless-7z-member-names` | no problem statement (no `proposal.md`; specs + tasks only) |
| `2026-07-13-sevenzip-lz4-codec` | no problem statement (no `proposal.md`; specs + tasks only) |
| `2026-07-13-support-lzma-alone` | no problem statement (no `proposal.md`; specs + tasks only) |

## 8. Code residue — Topic 8's harvest

| Source | State |
|---|---|
| [`harvest/`](harvest/) | **outstanding** — see [`SUMMARY.md`](SUMMARY.md) §Outstanding. Filled by Topic 8's capability workers, which are downstream of that topic's pass 0 and have not run. `harvest/` holds only its `README.md` at the time of this pass. |

## Not in §Sources, consulted as demand signals

Not mined for entries; they say what the catalogue is *for*.

| Document | Use |
|---|---|
| `review/docs/independent/rationale-gaps.md` | 32 "why is it like this?" questions — catalogue entries with the answer missing (§Why now #4) |
| `VISION.md` | The four load-bearing claims; input to the experiment, not a problem source |
| `review/README.md` | Conventions every brief inherits |
