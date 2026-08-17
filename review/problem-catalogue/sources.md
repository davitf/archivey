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

**Session 1 (2026-08-17).** Read in the brief's mandated order:

| Group | State |
|---|---|
| 1. `dev-docs/history/` (5 files) | **complete** |
| 2. `dev-docs/investigations/` (8) | **6 of 8** — three read in full; `adr-0014-investigation` and `ppmd-native-investigation-results` read selectively (the sections carrying problems not already in a register), with the method in their rows. Two PPMd files remain `unread — attributed`: headings scanned, every problem they state is carried by `known-issues.md`, but that is a summary's word rather than the primary document |
| 3. Standing registers (4) | **complete** |
| 4. Unsettled / parked (5) | **complete** |
| 5. `dev-docs/decisions/` (17 ADRs) | **complete** |
| 6. `review/archive/*/SUMMARY.md` (11) | **complete** |
| 7a. Archived proposal `## Why` blocks (72) | **complete** |
| 7b. `design.md` files (57) | **unread** — the only §Sources group not yet opened |
| 8. Topic 8 harvest | **outstanding** — see §8 |

**Catalogue at this point: 144 entries**, with 480 entry-source attributions across 100
documents (3.3 documents per problem).

**Next actions**, in order: the 57 `design.md` files (group 7b), then the two remaining PPMd
investigations (group 2), then the harvest when it exists.

**What the completed groups tell us about 7b.** The prediction recorded here before group 7a
was read — that a group summarised by a register yields **more sources per existing entry than
new entries** — held exactly: 72 `## Why` blocks produced 12 new entries and 41 new sources on
existing ones. Group 4 then produced 4 entries from 5 documents, all of them *parked* problems
that no register states as problems because the registers file them as ideas.

Group 7b should behave like 7a, more so: a `design.md` sits downstream of its own proposal's
`## Why`, so its forces are the ones already mined. Read it for **alternatives considered and
why they lost** — that is where a problem the `## Why` stated loosely gets measured, and it is
the one thing in the tree that records a force *against* a decision rather than for it.

---

## 1. `dev-docs/history/` — 5 files

Read **first**, per §Sources: the only source written before the current design existed,
so its problem statements are natively neutral.

| Document | State | Entries |
|---|---|---|
| `history/ARCHITECTURE.md` | mined | `FQ-01`, `FQ-03`, `FQ-04`, `FQ-05`, `FQ-06`, `FQ-14`, `FQ-15`, `FQ-16`, `FQ-17`, `FQ-18`, `FQ-19`, `FQ-26`, `FQ-28`, `UL-20`, `SEC-01`, `SEC-02`, `SEC-03`, `SEC-04`, `SEC-20`, `SEC-25`, `SEC-26`, `PLAT-01`, `PLAT-02`, `PLAT-07`, `PERF-01`, `PERF-02`, `PERF-07`, `PERF-12`, `API-03`, `API-04`, `API-06`, `API-07`, `API-08`, `API-10`, `API-11`, `API-28`, `PKG-01`, `PKG-04`, `PKG-05`, `PKG-09`, `CONC-02`, `CONC-04` |
| `history/ASYNC.md` | mined | `PERF-06`, `API-10` |
| `history/COMPARISON.md` | mined | `FQ-01`, `FQ-03`, `FQ-05`, `FQ-06`, `FQ-07`, `FQ-08`, `FQ-09`, `FQ-10`, `FQ-11`, `FQ-12`, `FQ-13`, `FQ-14`, `FQ-15`, `FQ-19`, `SEC-01`, `SEC-03`, `PLAT-03`, `PERF-01`, `PERF-02`, `API-02`, `API-03`, `API-04`, `API-05`, `API-08`, `PKG-06`, `PKG-07`, `CONC-02` |
| `history/SPEC.md` | mined | `FQ-01`, `FQ-02`, `FQ-03`, `FQ-04`, `FQ-05`, `FQ-06`, `FQ-07`, `FQ-08`, `FQ-09`, `FQ-10`, `FQ-11`, `FQ-13`, `FQ-14`, `FQ-15`, `FQ-16`, `FQ-17`, `FQ-18`, `FQ-19`, `FQ-20`, `FQ-21`, `FQ-26`, `FQ-27`, `FQ-28`, `FQ-30`, `SEC-01`, `SEC-02`, `SEC-03`, `SEC-04`, `SEC-25`, `SEC-26`, `PLAT-01`, `PLAT-02`, `PLAT-03`, `PERF-01`, `API-01`, `API-02`, `API-03`, `API-04`, `API-05`, `API-06`, `API-07`, `API-08`, `API-10`, `API-11`, `API-12`, `PKG-01` |
| `history/index.md` | no problem statement — triage router: a status table pointing at the four documents above, with a suggested-triage list. States no problem of its own |  |

## 2. `dev-docs/investigations/` — 8 files

| Document | State | Entries |
|---|---|---|
| `investigations/adr-0014-investigation.md` | mined — §Seek and §`read_exact` read in full; the rest via headings, its conclusions already carried by ADR 0014 | `UL-06`, `API-16`, `API-17`, `API-18`, `API-19`, `API-26` |
| `investigations/parallel-reader.md` | mined | `FQ-06`, `UL-03`, `PERF-06`, `PERF-12`, `CONC-01`, `CONC-03`, `CONC-04`, `CONC-05`, `CONC-06` |
| `investigations/ppmd-exit-after-green-exploration.md` | unread — attributed; headings scanned, no problem absent from `known-issues.md` §exit-after-green found | `FQ-23`, `UL-08`, `UL-10` |
| `investigations/ppmd-native-investigation-brief.md` | unread — attributed; headings scanned, no problem absent from `known-issues.md` §PPMd found | `FQ-23`, `UL-08` |
| `investigations/ppmd-native-investigation-results.md` | mined — §B read in full; the rest via headings + `known-issues.md`, which carries its conclusions verbatim | `FQ-23`, `UL-08`, `UL-09`, `UL-10`, `UL-18` |
| `investigations/pyppmd-upstream-report.md` | mined | `UL-08` |
| `investigations/rapidgzip-upstream-report.md` | mined | `UL-05`, `UL-06`, `UL-15` |
| `investigations/rar-corpus-sweep-diagnosis.md` | mined | `FQ-03`, `FQ-18`, `SEC-15`, `PKG-05`, `PKG-09`, `CONC-02` |

## 3. Standing registers — 4 files

| Document | State | Entries |
|---|---|---|
| `dev-docs/threat-model.md` (9 `O` entries) | mined | `FQ-17`, `UL-02`, `UL-14`, `SEC-01`, `SEC-03`, `SEC-05`, `SEC-06`, `SEC-07`, `SEC-08`, `SEC-09`, `SEC-10`, `SEC-11`, `SEC-12`, `SEC-13`, `SEC-18`, `SEC-23`, `SEC-24`, `PLAT-05`, `PERF-06`, `API-09`, `API-29`, `PKG-04`, `PKG-08`, `PKG-11`, `CONC-01`, `CONC-03`, `CONC-04`, `CONC-05` |
| `dev-docs/known-issues.md` (6 sections / 709 lines) | mined | `FQ-02`, `FQ-20`, `FQ-23`, `UL-01`, `UL-02`, `UL-03`, `UL-04`, `UL-05`, `UL-06`, `UL-07`, `UL-08`, `UL-09`, `UL-10`, `UL-15`, `UL-18`, `PKG-03`, `PKG-08`, `CONC-05` |
| `dev-docs/library-analysis.md` (362 lines) | mined | `FQ-02`, `FQ-12`, `FQ-25`, `FQ-27`, `FQ-29`, `UL-04`, `UL-06`, `UL-07`, `UL-11`, `UL-12`, `UL-13`, `UL-19`, `PERF-02`, `PERF-03`, `PERF-04`, `PERF-05`, `PERF-11`, `PKG-02`, `PKG-03`, `PKG-06`, `PKG-07`, `PKG-10`, `PKG-11` |
| `dev-docs/open-issues.md` (310 lines) | mined | `FQ-02`, `FQ-04`, `FQ-06`, `FQ-07`, `FQ-08`, `FQ-16`, `FQ-17`, `FQ-21`, `FQ-22`, `FQ-24`, `UL-01`, `UL-02`, `UL-05`, `UL-06`, `UL-08`, `UL-16`, `UL-17`, `UL-18`, `SEC-11`, `SEC-13`, `SEC-14`, `SEC-15`, `SEC-16`, `SEC-17`, `SEC-18`, `PLAT-03`, `PLAT-04`, `PLAT-05`, `PERF-02`, `PERF-04`, `PERF-05`, `API-01`, `API-04`, `API-06`, `API-13`, `API-14`, `API-21`, `API-22`, `PKG-04`, `PKG-08`, `CONC-02`, `CONC-03` |

## 4. Unsettled / parked — 5 files

| Document | State | Entries |
|---|---|---|
| `dev-docs/IDEAS.md` | mined | `FQ-30`, `PLAT-03`, `PLAT-05`, `PLAT-07`, `PERF-12` |
| `discussions/2026-08-diagnostics/diagnostics-archive-vs-usage.md` | mined | `API-29` |
| `discussions/2026-08-diagnostics/reviewer-1-opinion.md` | mined — outside comment on the document above; cited as its co-sources, states no further problem of its own | `API-29` |
| `discussions/2026-08-diagnostics/reviewer-2-opinion.md` | mined — outside comment on the document above; cited as its co-sources, states no further problem of its own | `API-29` |
| `discussions/2026-08-diagnostics/reviewer-3-opinion.md` | mined — outside comment on the document above; cited as its co-sources, states no further problem of its own | `API-29` |

## 5. `dev-docs/decisions/` — 17 ADRs

The **Context** section is the source; the Decision section is field 4 material.
`index.md` is a router, not a source (§Sources), and is listed for completeness only.

| Document | State | Entries |
|---|---|---|
| `0001-native-7z-not-py7zr.md` | mined — no new entry; its context is stated by earlier sources |  |
| `0002-native-rar-metadata-unrar-data.md` | mined | `FQ-17`, `FQ-26`, `SEC-20`, `PKG-04` |
| `0003-member-streams-opt-in.md` | mined | `API-15`, `CONC-01` |
| `0004-streaming-bool-not-intent-enum.md` | mined | `API-05`, `API-15` |
| `0005-sync-only-v1.md` | mined | `API-10` |
| `0006-stdlib-zipfile.md` | mined — no new entry; its context is stated by earlier sources |  |
| `0007-mutable-archive-member.md` | mined | `FQ-01`, `API-03` |
| `0008-single-accelerator-rapidgzip.md` | mined | `UL-03`, `UL-04`, `CONC-05` |
| `0009-zstd-stdlib-backports.md` | mined | `UL-11`, `PKG-02`, `PKG-07` |
| `0010-no-silent-buffer-nonseekable.md` | mined | `FQ-04`, `API-01`, `API-25` |
| `0011-zero-dependency-core.md` | mined | `PKG-01`, `PKG-06`, `PKG-10` |
| `0012-usage-errors-outside-archiveyerror.md` | mined | `API-07` |
| `0013-cross-platform-name-safety-policies.md` | mined | `SEC-06`, `SEC-07`, `SEC-08`, `SEC-19` |
| `0014-integrity-verdicts-from-reads-not-close.md` | mined | `UL-06`, `SEC-22`, `PERF-03`, `API-16`, `API-17`, `API-18`, `API-19`, `API-26` |
| `0015-zero-filled-files-are-valid-empty-tars.md` | mined | `FQ-20`, `UL-01`, `SEC-27` |
| `0016-committed-rar-corpus-fixtures.md` | mined | `PKG-05`, `PKG-09` |
| `0017-bidi-override-rejection-is-policy-keyed.md` | mined | `SEC-19` |
| `index.md` | router, not a source | — |

## 6. `review/archive/*/SUMMARY.md` — 11 completed reviews

Sources, not subjects: findings are mined, conclusions are not reopened (§Relationship to
the other topics).

| Document | State | Entries |
|---|---|---|
| `2026-07-12-codebase-deep-review/SUMMARY.md` | mined | `UL-01`, `UL-19`, `SEC-05`, `SEC-21`, `SEC-23`, `PERF-09`, `PERF-10`, `CONC-06` |
| `2026-07-16-crypto/SUMMARY.md` | mined | `FQ-29`, `UL-14`, `SEC-13`, `SEC-14`, `SEC-15`, `SEC-16`, `SEC-17`, `SEC-24` |
| `2026-07-16-rar-reader/SUMMARY.md` | mined | `SEC-20`, `SEC-21`, `SEC-22`, `SEC-24` |
| `2026-07-16-stream-decoder/SUMMARY.md` | mined | `FQ-25`, `SEC-22`, `PERF-07`, `API-19` |
| `2026-07-17-cli/SUMMARY.md` | mined — findings table + headline read; the per-theme files are not §Sources rows | `UL-21`, `PLAT-06`, `API-23`, `API-24` |
| `2026-07-19-api-coherence/SUMMARY.md` | mined | `FQ-24`, `API-07`, `API-12`, `API-18`, `API-22`, `API-24` |
| `2026-07-19-stream-layering/SUMMARY.md` | mined | `UL-22`, `SEC-22`, `PERF-03`, `PERF-08`, `API-16`, `API-17`, `API-19` |
| `2026-07-20-cli-product/SUMMARY.md` | mined — findings table + headline read; the per-theme files are not §Sources rows | `PLAT-06`, `API-22`, `API-23`, `API-27`, `API-28` |
| `2026-07-28-debt-ledger/SUMMARY.md` | mined — findings table + headline read; the per-theme files are not §Sources rows | `PERF-09` |
| `2026-07-28-performance/SUMMARY.md` | mined | `PERF-07`, `PERF-08`, `PERF-09`, `PERF-10` |
| `2026-08-15-simplicity-consistency/SUMMARY.md` | mined | `FQ-20`, `FQ-26`, `UL-20`, `SEC-09`, `SEC-10`, `SEC-19`, `SEC-27`, `PERF-11`, `PERF-12`, `API-14`, `API-20`, `API-21`, `API-24`, `API-25`, `PKG-05`, `PKG-09` |

## 7. `openspec/changes/archive/*/` — 72 proposals with a `## Why`, 57 with a `design.md`

Two states per row: the proposal's `## Why` block, and the `design.md` if the change has
one. `n/a` in the design column means the change has no `design.md` — that is not a gap.

| Change | `## Why` | `design.md` | Entries |
|---|---|---|---|
| `2026-06-19-phase-1-scaffold-and-spine` | mined | n/a |  |
| `2026-06-21-phase-2-stream-layer` | mined | n/a |  |
| `2026-06-27-stream-wrapper-base` | mined | n/a | `UL-22` |
| `2026-06-30-compression-library-evaluation` | mined | n/a | `UL-11`, `PKG-02` |
| `2026-06-30-package-layout-restructure` | mined | n/a |  |
| `2026-06-30-phase-3-indexed-leaf-formats` | mined | n/a | `FQ-25`, `UL-13` |
| `2026-07-01-codec-descriptor-refactor` | mined | n/a |  |
| `2026-07-01-zstd-stdlib-backend-migration` | mined | n/a | `UL-11` |
| `2026-07-03-minimal-name-normalization` | mined | unread | `SEC-25` |
| `2026-07-03-phase-4-safe-extraction` | mined | n/a | `SEC-25` |
| `2026-07-04-inner-tar-probe-block-codecs` | mined | n/a | `FQ-13`, `FQ-27` |
| `2026-07-04-live-decompression-ratio-guard` | mined | unread | `SEC-26` |
| `2026-07-07-phase-5-public-api` | mined | unread |  |
| `2026-07-07-retire-dev-oracle` | mined | unread |  |
| `2026-07-07-scan-members` | mined | unread | `FQ-28`, `API-06` |
| `2026-07-10-parallel-reader-exploration` | mined | unread | `PERF-06` |
| `2026-07-11-concurrent-member-streams` | mined | unread | `API-15`, `CONC-01` |
| `2026-07-11-diagnostics-warnings-as-data` | mined | unread | `API-09` |
| `2026-07-11-tar-concurrent-open` | mined | unread | `CONC-01` |
| `2026-07-11-zip-multipassword-disambiguation` | mined | unread | `SEC-14`, `SEC-24` |
| `2026-07-12-anti-member-type-and-nonfile-open` | mined | unread | `FQ-24` |
| `2026-07-12-atheris-fuzz-harness` | mined | unread | `UL-02`, `SEC-12`, `SEC-18`, `SEC-23` |
| `2026-07-12-hypothesis-property-tests` | mined | unread |  |
| `2026-07-12-listing-resource-limits` | mined | unread | `SEC-05` |
| `2026-07-12-native-7z-reader` | mined | unread |  |
| `2026-07-12-native-rar-reader` | mined | unread |  |
| `2026-07-12-phase-4-tar-streaming` | mined | n/a |  |
| `2026-07-12-promote-concurrent-member-streams` | mined | unread | `API-15` |
| `2026-07-12-shared-source-streams` | mined | unread | `CONC-01` |
| `2026-07-14-adversarial-string-corpus-contract` | mined | unread | `FQ-30`, `SEC-19` |
| `2026-07-14-decompressor-stream-composition` | mined | unread | `UL-22` |
| `2026-07-14-rar-blake2sp-verification` | mined | unread | `FQ-29`, `SEC-15` |
| `2026-07-14-refactor-sevenzip-reader` | mined | unread |  |
| `2026-07-14-stored-digest-dedupe-parity` | mined | unread |  |
| `2026-07-14-vendor-unix-compress-lzw` | mined | unread | `PERF-05` |
| `2026-07-14-zip-name-encoding-sniffing` | mined | unread | `FQ-07`, `FQ-30` |
| `2026-07-15-atheris-harness-depth` | mined | unread | `SEC-18`, `SEC-23` |
| `2026-07-15-benchmark-gate` | mined | unread | `PERF-09` |
| `2026-07-15-extraction-progress-in-file` | mined | unread | `API-28` |
| `2026-07-15-rapidgzip-deflate-zlib-acceleration` | mined | unread |  |
| `2026-07-15-rar-file-version-members` | mined | unread | `FQ-22` |
| `2026-07-15-zip-aes-decryption` | mined | unread |  |
| `2026-07-15-zip-native-codec-streams` | mined | unread |  |
| `2026-07-16-cross-platform-name-safety` | mined | unread | `SEC-06`, `SEC-07` |
| `2026-07-17-cli-v1` | mined | unread | `UL-21`, `API-23` |
| `2026-07-18-partial-members-and-errors` | mined | unread | `FQ-28`, `PLAT-07`, `PERF-04`, `API-16`, `API-22` |
| `2026-07-18-sevenzip-header-cursor-parse` | mined | unread |  |
| `2026-07-19-clarify-extraction-status-names` | mined | unread | `FQ-24`, `API-27` |
| `2026-07-19-decide-strict-archive-eof-default` | mined | unread | `UL-01`, `SEC-27` |
| `2026-07-19-surface-stored-stream-digests` | mined | unread | `PERF-03` |
| `2026-07-20-stop-on-failure-not-policy` | mined | unread | `API-22`, `API-27` |
| `2026-07-24-gzip-zlib-truncation-recovery` | mined | unread | `PERF-04`, `API-16` |
| `2026-07-24-rapidgzip-truncation-investigation` | mined | unread |  |
| `2026-07-24-unify-pass-driver` | mined | unread |  |
| `2026-07-25-gzip-truncation-backstop-any-seekable` | mined | unread |  |
| `2026-07-30-consolidate-optional-extras` | mined | unread | `UL-09`, `PKG-03`, `PKG-06`, `PKG-10`, `PKG-11` |
| `2026-07-30-member-stream-capability-booleans` | mined | unread | `API-15`, `API-20`, `CONC-03` |
| `2026-07-31-rename-extras-in-remaining-specs` | mined | unread | `PKG-10` |
| `2026-08-01-short-read-source-contract` | mined | unread | `API-26` |
| `2026-08-03-docs-ia-unpublish-maintainer-tree` | mined | unread |  |
| `2026-08-04-docs-ia-split-user-guide` | mined | unread |  |
| `2026-08-06-close-member-streams-on-reader-close` | mined | n/a | `UL-05`, `UL-16`, `UL-17`, `API-14`, `CONC-03` |
| `2026-08-06-reject-format-override-on-directory` | mined | n/a | `API-13`, `API-21` |
| `2026-08-06-spec-drop-unimplemented-solid-warning` | mined | n/a |  |
| `2026-08-09-decouple-member-metadata-from-declared-seekability` | mined | unread | `PLAT-01`, `API-20` |
| `2026-08-09-format-availability-required-source` | mined | unread | `API-25`, `PKG-01` |
| `2026-08-09-reject-bidi-overrides-in-safe-extraction` | mined | unread | `SEC-19` |
| `2026-08-09-review-diagnostics-batch` | mined | unread | `API-09`, `API-29` |
| `2026-08-09-rewind-diagnostic-redecode-cost` | mined | unread | `PERF-11`, `API-17` |
| `2026-08-09-strict-archive-eof-trailing-bytes` | mined | unread | `FQ-20`, `SEC-27` |
| `2026-08-15-escape-cli-log-records` | mined | n/a | `SEC-09` |
| `2026-08-15-extraction-results-authoritative` | mined | unread | `API-09`, `API-22`, `API-27`, `API-29` |

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
