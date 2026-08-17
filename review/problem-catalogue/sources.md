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
| `mined` | Read; every problem statement in it is an entry in [`catalogue.md`](catalogue.md), by id |
| `no problem statement` | Read; states no problem, with the reason recorded |

A document may be `mined` and contribute **zero new** entries — that means every problem
it states was already in the catalogue from an earlier source. That is the dedupe working,
and the entry ids in its row are the proof. It is different from `no problem statement`,
which means the document does not state a problem at all.

## Where this pass stopped

<!-- KEEP THIS CURRENT AS YOU GO, NOT AT THE END. A fresh container has no memory of
     the session; this section plus the tables below are the entire handoff. -->

**Status:** see the progress line at the top of each table.

---

## 1. `dev-docs/history/` — 5 files

Read **first**, per §Sources: the only source written before the current design existed,
so its problem statements are natively neutral.

| Document | State | Entries |
|---|---|---|
| `history/ARCHITECTURE.md` | unread |  |
| `history/ASYNC.md` | unread |  |
| `history/COMPARISON.md` | unread |  |
| `history/SPEC.md` | unread |  |
| `history/index.md` | unread |  |

## 2. `dev-docs/investigations/` — 8 files

| Document | State | Entries |
|---|---|---|
| `investigations/adr-0014-investigation.md` | unread |  |
| `investigations/parallel-reader.md` | unread |  |
| `investigations/ppmd-exit-after-green-exploration.md` | unread |  |
| `investigations/ppmd-native-investigation-brief.md` | unread |  |
| `investigations/ppmd-native-investigation-results.md` | unread |  |
| `investigations/pyppmd-upstream-report.md` | unread |  |
| `investigations/rapidgzip-upstream-report.md` | unread |  |
| `investigations/rar-corpus-sweep-diagnosis.md` | unread |  |

## 3. Standing registers — 4 files

| Document | State | Entries |
|---|---|---|
| `dev-docs/threat-model.md` (9 `O` entries) | unread |  |
| `dev-docs/known-issues.md` (6 sections / 709 lines) | unread |  |
| `dev-docs/library-analysis.md` (362 lines) | unread |  |
| `dev-docs/open-issues.md` (310 lines) | unread |  |

## 4. Unsettled / parked — 5 files

| Document | State | Entries |
|---|---|---|
| `dev-docs/IDEAS.md` | unread |  |
| `discussions/2026-08-diagnostics/diagnostics-archive-vs-usage.md` | unread |  |
| `discussions/2026-08-diagnostics/reviewer-1-opinion.md` | unread |  |
| `discussions/2026-08-diagnostics/reviewer-2-opinion.md` | unread |  |
| `discussions/2026-08-diagnostics/reviewer-3-opinion.md` | unread |  |

## 5. `dev-docs/decisions/` — 17 ADRs

The **Context** section is the source; the Decision section is field 4 material.
`index.md` is a router, not a source (§Sources), and is listed for completeness only.

| Document | State | Entries |
|---|---|---|
| `0001-native-7z-not-py7zr.md` | unread |  |
| `0002-native-rar-metadata-unrar-data.md` | unread |  |
| `0003-member-streams-opt-in.md` | unread |  |
| `0004-streaming-bool-not-intent-enum.md` | unread |  |
| `0005-sync-only-v1.md` | unread |  |
| `0006-stdlib-zipfile.md` | unread |  |
| `0007-mutable-archive-member.md` | unread |  |
| `0008-single-accelerator-rapidgzip.md` | unread |  |
| `0009-zstd-stdlib-backports.md` | unread |  |
| `0010-no-silent-buffer-nonseekable.md` | unread |  |
| `0011-zero-dependency-core.md` | unread |  |
| `0012-usage-errors-outside-archiveyerror.md` | unread |  |
| `0013-cross-platform-name-safety-policies.md` | unread |  |
| `0014-integrity-verdicts-from-reads-not-close.md` | unread |  |
| `0015-zero-filled-files-are-valid-empty-tars.md` | unread |  |
| `0016-committed-rar-corpus-fixtures.md` | unread |  |
| `0017-bidi-override-rejection-is-policy-keyed.md` | unread |  |
| `index.md` | router, not a source | — |

## 6. `review/archive/*/SUMMARY.md` — 11 completed reviews

Sources, not subjects: findings are mined, conclusions are not reopened (§Relationship to
the other topics).

| Document | State | Entries |
|---|---|---|
| `2026-07-12-codebase-deep-review/SUMMARY.md` | unread |  |
| `2026-07-16-crypto/SUMMARY.md` | unread |  |
| `2026-07-16-rar-reader/SUMMARY.md` | unread |  |
| `2026-07-16-stream-decoder/SUMMARY.md` | unread |  |
| `2026-07-17-cli/SUMMARY.md` | unread |  |
| `2026-07-19-api-coherence/SUMMARY.md` | unread |  |
| `2026-07-19-stream-layering/SUMMARY.md` | unread |  |
| `2026-07-20-cli-product/SUMMARY.md` | unread |  |
| `2026-07-28-debt-ledger/SUMMARY.md` | unread |  |
| `2026-07-28-performance/SUMMARY.md` | unread |  |
| `2026-08-15-simplicity-consistency/SUMMARY.md` | unread |  |

## 7. `openspec/changes/archive/*/` — 72 proposals with a `## Why`, 57 with a `design.md`

Two states per row: the proposal's `## Why` block, and the `design.md` if the change has
one. `n/a` in the design column means the change has no `design.md` — that is not a gap.

| Change | `## Why` | `design.md` | Entries |
|---|---|---|---|
| `2026-06-19-phase-1-scaffold-and-spine` | unread | n/a |  |
| `2026-06-21-phase-2-stream-layer` | unread | n/a |  |
| `2026-06-27-stream-wrapper-base` | unread | n/a |  |
| `2026-06-30-compression-library-evaluation` | unread | n/a |  |
| `2026-06-30-package-layout-restructure` | unread | n/a |  |
| `2026-06-30-phase-3-indexed-leaf-formats` | unread | n/a |  |
| `2026-07-01-codec-descriptor-refactor` | unread | n/a |  |
| `2026-07-01-zstd-stdlib-backend-migration` | unread | n/a |  |
| `2026-07-03-minimal-name-normalization` | unread | unread |  |
| `2026-07-03-phase-4-safe-extraction` | unread | n/a |  |
| `2026-07-04-inner-tar-probe-block-codecs` | unread | n/a |  |
| `2026-07-04-live-decompression-ratio-guard` | unread | unread |  |
| `2026-07-07-phase-5-public-api` | unread | unread |  |
| `2026-07-07-retire-dev-oracle` | unread | unread |  |
| `2026-07-07-scan-members` | unread | unread |  |
| `2026-07-10-parallel-reader-exploration` | unread | unread |  |
| `2026-07-11-concurrent-member-streams` | unread | unread |  |
| `2026-07-11-diagnostics-warnings-as-data` | unread | unread |  |
| `2026-07-11-tar-concurrent-open` | unread | unread |  |
| `2026-07-11-zip-multipassword-disambiguation` | unread | unread |  |
| `2026-07-12-anti-member-type-and-nonfile-open` | unread | unread |  |
| `2026-07-12-atheris-fuzz-harness` | unread | unread |  |
| `2026-07-12-hypothesis-property-tests` | unread | unread |  |
| `2026-07-12-listing-resource-limits` | unread | unread |  |
| `2026-07-12-native-7z-reader` | unread | unread |  |
| `2026-07-12-native-rar-reader` | unread | unread |  |
| `2026-07-12-phase-4-tar-streaming` | unread | n/a |  |
| `2026-07-12-promote-concurrent-member-streams` | unread | unread |  |
| `2026-07-12-shared-source-streams` | unread | unread |  |
| `2026-07-14-adversarial-string-corpus-contract` | unread | unread |  |
| `2026-07-14-decompressor-stream-composition` | unread | unread |  |
| `2026-07-14-rar-blake2sp-verification` | unread | unread |  |
| `2026-07-14-refactor-sevenzip-reader` | unread | unread |  |
| `2026-07-14-stored-digest-dedupe-parity` | unread | unread |  |
| `2026-07-14-vendor-unix-compress-lzw` | unread | unread |  |
| `2026-07-14-zip-name-encoding-sniffing` | unread | unread |  |
| `2026-07-15-atheris-harness-depth` | unread | unread |  |
| `2026-07-15-benchmark-gate` | unread | unread |  |
| `2026-07-15-extraction-progress-in-file` | unread | unread |  |
| `2026-07-15-rapidgzip-deflate-zlib-acceleration` | unread | unread |  |
| `2026-07-15-rar-file-version-members` | unread | unread |  |
| `2026-07-15-zip-aes-decryption` | unread | unread |  |
| `2026-07-15-zip-native-codec-streams` | unread | unread |  |
| `2026-07-16-cross-platform-name-safety` | unread | unread |  |
| `2026-07-17-cli-v1` | unread | unread |  |
| `2026-07-18-partial-members-and-errors` | unread | unread |  |
| `2026-07-18-sevenzip-header-cursor-parse` | unread | unread |  |
| `2026-07-19-clarify-extraction-status-names` | unread | unread |  |
| `2026-07-19-decide-strict-archive-eof-default` | unread | unread |  |
| `2026-07-19-surface-stored-stream-digests` | unread | unread |  |
| `2026-07-20-stop-on-failure-not-policy` | unread | unread |  |
| `2026-07-24-gzip-zlib-truncation-recovery` | unread | unread |  |
| `2026-07-24-rapidgzip-truncation-investigation` | unread | unread |  |
| `2026-07-24-unify-pass-driver` | unread | unread |  |
| `2026-07-25-gzip-truncation-backstop-any-seekable` | unread | unread |  |
| `2026-07-30-consolidate-optional-extras` | unread | unread |  |
| `2026-07-30-member-stream-capability-booleans` | unread | unread |  |
| `2026-07-31-rename-extras-in-remaining-specs` | unread | unread |  |
| `2026-08-01-short-read-source-contract` | unread | unread |  |
| `2026-08-03-docs-ia-unpublish-maintainer-tree` | unread | unread |  |
| `2026-08-04-docs-ia-split-user-guide` | unread | unread |  |
| `2026-08-06-close-member-streams-on-reader-close` | unread | n/a |  |
| `2026-08-06-reject-format-override-on-directory` | unread | n/a |  |
| `2026-08-06-spec-drop-unimplemented-solid-warning` | unread | n/a |  |
| `2026-08-09-decouple-member-metadata-from-declared-seekability` | unread | unread |  |
| `2026-08-09-format-availability-required-source` | unread | unread |  |
| `2026-08-09-reject-bidi-overrides-in-safe-extraction` | unread | unread |  |
| `2026-08-09-review-diagnostics-batch` | unread | unread |  |
| `2026-08-09-rewind-diagnostic-redecode-cost` | unread | unread |  |
| `2026-08-09-strict-archive-eof-trailing-bytes` | unread | unread |  |
| `2026-08-15-escape-cli-log-records` | unread | n/a |  |
| `2026-08-15-extraction-results-authoritative` | unread | unread |  |

### Archived changes with no `proposal.md` — outside the denominator

Five archived changes carry only `specs/` + `tasks.md`. They are not part of §Sources'
count of 72 and state no problem of their own; the spec deltas they carry are behaviour,
not problem statements.

| Change | State |
|---|---|
| `2026-07-12-reader-concurrency-coordination` | no problem statement (no `proposal.md`; specs + tasks only) |
| `2026-07-12-support-lzma1-bcj` | no problem statement (no `proposal.md`; specs + tasks only) |
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
