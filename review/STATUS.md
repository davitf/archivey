# In-flight review status (2026-07-25)

Triage after rebasing ledger refresh onto `main` @ `3793646` (post-#193/#196/#194/#191).

## At a glance

| Review | Findings delivered? | Code/docs follow-ups | Ready to archive? |
|--------|---------------------|----------------------|-------------------|
| `debt-ledger/` | yes (2026-07-20; **refreshed 2026-07-25**) | **DONE:** D1 #191, D3 #193, DD4 #194/#196, S2/S3+T1 #184, Q1–Q5. **Open:** D2, T2/T3/T7, D4, D7 remainder (any-seekable 17/18), T4 half-test | no |
| `performance/` | yes (#134 + follow-ups) | residual **accepted aspirational** (#191); wall Q2 decided (#171); **Q4 open** | no |

Archived OpenSpec this window: `unify-pass-driver`, `gzip-zlib-truncation-recovery`,
`rapidgzip-truncation-investigation` (all 2026-07-24). Live: `gzip-truncation-backstop-any-seekable` 17/18.

---

## 1. Actionable right now

| ID | Action |
|----|--------|
| **D2** | Write `SECURITY.md` |
| **D7 remainder** | Sync + archive `gzip-truncation-backstop-any-seekable` (task 6.2) |
| **T2** | Seek-interleaving for lzip/`.Z` |
| **D4** | `open-issues.md` P1 sweep |
| **T3** | Bench-gate RAR / encrypted / accelerator data |
| **T7** | Corpus-matrix audit |
| **T4 half** | Multithread `members_report_if_available` test |

### From `performance/`

| ID | Action |
|----|--------|
| **P7 residual** | **Accepted** (#191) — nightly ratios in `docs/costs.md`; L5 → `IDEAS.md` |
| **P6 remainder** | = debt-ledger T3 |

---

## 2. Decisions

| Q | Status |
|---|--------|
| debt-ledger **Q1–Q5** | **decided + done** |
| performance **Q4** | **open** — lean leave-as-is |

---

## Already addressed (selected)

| Item | Where |
|------|-------|
| D1 VISION/costs peer bands (Q2 (b)) | #191 |
| D3 CHANGELOG + release checklist (Q5) | #193 |
| DD4 rapidgzip backstop + any-seekable / Bug-3 | #194 / #196 |
| Unify pass driver + solid-RAR mutation (S2/S3/T1) | #184 |
| Stdlib gzip truncation + ADR 0014 | #183 / #186 |
| Nightly wall-ratio drift (Q1) | #171 |
| `pyppmd` quiesce-on-close + valgrind UAF gate | #188/#189 |
| OpenSpec `stop-on-failure-not-policy` | #165 → archived |
| Listing L0–L3 + peers; perf P3–P5 | #143/#146/#148/#136/#139 |
| api-coherence / stream-layering / cli-product | #137/#154–#160/#163/#165 |
