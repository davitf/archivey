# Sources inventory — Topic 10 problem catalogue

Baseline: brief commissioned against `main` @ `d4668c3`. Inventory written before
mining so coverage is checkable. States: `unread` | `mined` | `no problem statement
(reason)`.

**Session progress:** sources inventory written. `dev-docs/history/` mined for
early-checkpoint sample (`checkpoint-sample.md`, 18 draft entries). **Paused for
maintainer neutrality steer** before investigations / threat-model / ADRs /
proposals. Next unread group after steer: §2 investigations + §3 threat-model.

---

## 1. `dev-docs/history/` (read first — natively neutral)

| Document | State |
|---|---|
| `ARCHITECTURE.md` | mined — trade-offs §2/§4/§5, seekability, solid, bombs, errors, deps |
| `ASYNC.md` | mined — sync C decoder constraint (§3) |
| `COMPARISON.md` | mined — solid streaming, seekable compressors, detection/SFX, member identity, packaging |
| `SPEC.md` | mined — late-bound fields, solid costs, detection, ZIP EOF, filters, bombs, per-format |
| `index.md` | no problem statement — router / triage list only |

Brief count: 5 files. Denominator matches.

---

## 2. `dev-docs/investigations/` (then — already problem-shaped)

| Document | State |
|---|---|
| `adr-0014-investigation.md` | unread |
| `parallel-reader.md` | unread |
| `ppmd-exit-after-green-exploration.md` | unread |
| `ppmd-native-investigation-brief.md` | unread |
| `ppmd-native-investigation-results.md` | unread |
| `pyppmd-upstream-report.md` | unread |
| `rapidgzip-upstream-report.md` | unread |
| `rar-corpus-sweep-diagnosis.md` | unread |

Brief count: 8. Denominator matches.

---

## 3. `dev-docs/threat-model.md` (9 `O` entries)

| Document / entry | State |
|---|---|
| `threat-model.md` (whole file) | unread |
| O1 Listing-time resource exhaustion | unread |
| O2 Case-insensitivity / Unicode-normalization collisions | unread |
| O3 Windows name mangling | unread |
| O4 NTFS alternate data streams | unread |
| O5 Fuzzing | unread |
| O6 Nested-archive amplification | unread |
| O7 Names as bytes vs target filesystem | unread |
| O8 7z wrong header-decryption password → empty archive | unread |
| O9 Attacker-controlled bytes to terminal | unread |

Brief count: 9 `O` entries. Denominator matches.

---

## 4. Standing registers

| Document | State |
|---|---|
| `dev-docs/known-issues.md` (6 sections / 709 lines) | unread |
| `dev-docs/library-analysis.md` (362 lines) | unread |
| `dev-docs/open-issues.md` (310 lines) | unread |
| `dev-docs/IDEAS.md` | unread |
| `dev-docs/discussions/2026-08-diagnostics/diagnostics-archive-vs-usage.md` | unread |
| `dev-docs/discussions/2026-08-diagnostics/reviewer-1-opinion.md` | unread |
| `dev-docs/discussions/2026-08-diagnostics/reviewer-2-opinion.md` | unread |
| `dev-docs/discussions/2026-08-diagnostics/reviewer-3-opinion.md` | unread |

---

## 5. ADRs — `dev-docs/decisions/` (17; `index.md` is router)

| Document | State |
|---|---|
| `index.md` | unread — router, not a source (per brief) |
| `0001-native-7z-not-py7zr.md` | unread |
| `0002-native-rar-metadata-unrar-data.md` | unread |
| `0003-member-streams-opt-in.md` | unread |
| `0004-streaming-bool-not-intent-enum.md` | unread |
| `0005-sync-only-v1.md` | unread |
| `0006-stdlib-zipfile.md` | unread |
| `0007-mutable-archive-member.md` | unread |
| `0008-single-accelerator-rapidgzip.md` | unread |
| `0009-zstd-stdlib-backports.md` | unread |
| `0010-no-silent-buffer-nonseekable.md` | unread |
| `0011-zero-dependency-core.md` | unread |
| `0012-usage-errors-outside-archiveyerror.md` | unread |
| `0013-cross-platform-name-safety-policies.md` | unread |
| `0014-integrity-verdicts-from-reads-not-close.md` | unread |
| `0015-zero-filled-files-are-valid-empty-tars.md` | unread |
| `0016-committed-rar-corpus-fixtures.md` | unread |
| `0017-bidi-override-rejection-is-policy-keyed.md` | unread |

Brief count: 17 ADRs (+ index). Denominator matches.

---

## 6. Archived review `SUMMARY.md` (11)

| Document | State |
|---|---|
| `review/archive/2026-07-12-codebase-deep-review/SUMMARY.md` | unread |
| `review/archive/2026-07-16-crypto/SUMMARY.md` | unread |
| `review/archive/2026-07-16-rar-reader/SUMMARY.md` | unread |
| `review/archive/2026-07-16-stream-decoder/SUMMARY.md` | unread |
| `review/archive/2026-07-17-cli/SUMMARY.md` | unread |
| `review/archive/2026-07-19-api-coherence/SUMMARY.md` | unread |
| `review/archive/2026-07-19-stream-layering/SUMMARY.md` | unread |
| `review/archive/2026-07-20-cli-product/SUMMARY.md` | unread |
| `review/archive/2026-07-28-debt-ledger/SUMMARY.md` | unread |
| `review/archive/2026-07-28-performance/SUMMARY.md` | unread |
| `review/archive/2026-08-15-simplicity-consistency/SUMMARY.md` | unread |

Theme files under each archive are consulted when a SUMMARY finding needs evidence
or a clearer problem statement; they are not separate denominator rows.

Brief count: 11. Denominator matches.

---

## 7. Archived OpenSpec proposals with `## Why` (72)

Each row is the proposal. `design.md` noted when present (brief: 57 of 72).

| Change | design.md | State |
|---|---|---|
| `2026-06-19-phase-1-scaffold-and-spine` | no | unread |
| `2026-06-21-phase-2-stream-layer` | no | unread |
| `2026-06-27-stream-wrapper-base` | no | unread |
| `2026-06-30-compression-library-evaluation` | no | unread |
| `2026-06-30-package-layout-restructure` | no | unread |
| `2026-06-30-phase-3-indexed-leaf-formats` | no | unread |
| `2026-07-01-codec-descriptor-refactor` | no | unread |
| `2026-07-01-zstd-stdlib-backend-migration` | no | unread |
| `2026-07-03-minimal-name-normalization` | yes | unread |
| `2026-07-03-phase-4-safe-extraction` | no | unread |
| `2026-07-04-inner-tar-probe-block-codecs` | no | unread |
| `2026-07-04-live-decompression-ratio-guard` | yes | unread |
| `2026-07-07-phase-5-public-api` | yes | unread |
| `2026-07-07-retire-dev-oracle` | yes | unread |
| `2026-07-07-scan-members` | yes | unread |
| `2026-07-10-parallel-reader-exploration` | yes | unread |
| `2026-07-11-concurrent-member-streams` | yes | unread |
| `2026-07-11-diagnostics-warnings-as-data` | yes | unread |
| `2026-07-11-tar-concurrent-open` | yes | unread |
| `2026-07-11-zip-multipassword-disambiguation` | yes | unread |
| `2026-07-12-anti-member-type-and-nonfile-open` | yes | unread |
| `2026-07-12-atheris-fuzz-harness` | yes | unread |
| `2026-07-12-hypothesis-property-tests` | yes | unread |
| `2026-07-12-listing-resource-limits` | yes | unread |
| `2026-07-12-native-7z-reader` | yes | unread |
| `2026-07-12-native-rar-reader` | yes | unread |
| `2026-07-12-phase-4-tar-streaming` | no | unread |
| `2026-07-12-promote-concurrent-member-streams` | yes | unread |
| `2026-07-12-shared-source-streams` | yes | unread |
| `2026-07-14-adversarial-string-corpus-contract` | yes | unread |
| `2026-07-14-decompressor-stream-composition` | yes | unread |
| `2026-07-14-rar-blake2sp-verification` | yes | unread |
| `2026-07-14-refactor-sevenzip-reader` | yes | unread |
| `2026-07-14-stored-digest-dedupe-parity` | yes | unread |
| `2026-07-14-vendor-unix-compress-lzw` | yes | unread |
| `2026-07-14-zip-name-encoding-sniffing` | yes | unread |
| `2026-07-15-atheris-harness-depth` | yes | unread |
| `2026-07-15-benchmark-gate` | yes | unread |
| `2026-07-15-extraction-progress-in-file` | yes | unread |
| `2026-07-15-rapidgzip-deflate-zlib-acceleration` | yes | unread |
| `2026-07-15-rar-file-version-members` | yes | unread |
| `2026-07-15-zip-aes-decryption` | yes | unread |
| `2026-07-15-zip-native-codec-streams` | yes | unread |
| `2026-07-16-cross-platform-name-safety` | yes | unread |
| `2026-07-17-cli-v1` | yes | unread |
| `2026-07-18-partial-members-and-errors` | yes | unread |
| `2026-07-18-sevenzip-header-cursor-parse` | yes | unread |
| `2026-07-19-clarify-extraction-status-names` | yes | unread |
| `2026-07-19-decide-strict-archive-eof-default` | yes | unread |
| `2026-07-19-surface-stored-stream-digests` | yes | unread |
| `2026-07-20-stop-on-failure-not-policy` | yes | unread |
| `2026-07-24-gzip-zlib-truncation-recovery` | yes | unread |
| `2026-07-24-rapidgzip-truncation-investigation` | yes | unread |
| `2026-07-24-unify-pass-driver` | yes | unread |
| `2026-07-25-gzip-truncation-backstop-any-seekable` | yes | unread |
| `2026-07-30-consolidate-optional-extras` | yes | unread |
| `2026-07-30-member-stream-capability-booleans` | yes | unread |
| `2026-07-31-rename-extras-in-remaining-specs` | yes | unread |
| `2026-08-01-short-read-source-contract` | yes | unread |
| `2026-08-03-docs-ia-unpublish-maintainer-tree` | yes | unread |
| `2026-08-04-docs-ia-split-user-guide` | yes | unread |
| `2026-08-06-close-member-streams-on-reader-close` | no | unread |
| `2026-08-06-reject-format-override-on-directory` | no | unread |
| `2026-08-06-spec-drop-unimplemented-solid-warning` | no | unread |
| `2026-08-09-decouple-member-metadata-from-declared-seekability` | yes | unread |
| `2026-08-09-format-availability-required-source` | yes | unread |
| `2026-08-09-reject-bidi-overrides-in-safe-extraction` | yes | unread |
| `2026-08-09-review-diagnostics-batch` | yes | unread |
| `2026-08-09-rewind-diagnostic-redecode-cost` | yes | unread |
| `2026-08-09-strict-archive-eof-trailing-bytes` | yes | unread |
| `2026-08-15-escape-cli-log-records` | no | unread |
| `2026-08-15-extraction-results-authoritative` | yes | unread |

Brief count: 72 with `## Why`; 57 with `design.md`. Denominators match
(72 Why rows; 57 design.md files under archive).

### Archived changes without `## Why` (not in brief denominator)

Mined only if a problem statement appears elsewhere in the change; otherwise marked
explicitly so they are not silent gaps.

| Change | State |
|---|---|
| `2026-07-12-reader-concurrency-coordination` | unread — no `## Why` |
| `2026-07-12-support-lzma1-bcj` | unread — no `## Why` |
| `2026-07-13-nameless-7z-member-names` | unread — no `## Why` |
| `2026-07-13-sevenzip-lz4-codec` | unread — no `## Why` |
| `2026-07-13-support-lzma-alone` | unread — no `## Why` |

---

## 8. Topic 8 harvest (`harvest/`)

| Document | State |
|---|---|
| `harvest/README.md` | read — explains drops; not a problem source |
| Per-capability drops | **outstanding** — Topic 8 capability workers have not run; directory empty aside from README. Recorded per Definition of done #5. Capabilities TBD by Topic 8 pass 0. |

---

## Coverage checklist (brief §Sources)

| Source | Brief count | Inventory rows | Match |
|---|---:|---:|---|
| proposals with `## Why` | 72 | 72 | yes |
| …of those with `design.md` | 57 | 57 | yes |
| ADRs (excl. index) | 17 | 17 | yes |
| review `SUMMARY.md` | 11 | 11 | yes |
| investigations | 8 | 8 | yes |
| threat-model `O` entries | 9 | 9 | yes |
| known-issues | 1 file / 6 sections | listed | yes |
| library-analysis | 1 file | listed | yes |
| open-issues | 1 file | listed | yes |
| history/ | 5 files | 5 | yes |
| discussions + IDEAS | — | listed | yes |
| Topic 8 harvest | ~136 comment sites | outstanding | noted |

---

## Extraction notes (for resume)

- Read order (load-bearing): history → investigations + threat-model → ADRs +
  proposal Why blocks → registers + review SUMMARYs → merge/dedupe → neutrality
  pass → emit `catalogue-neutral.md` + `experiment.md`.
- Early checkpoint: after first ~15–20 entries, pause for maintainer steer
  (`checkpoint-sample.md`).
- No code/spec changes. Unevidenced items → separate unverified list.
