# In-flight review status (2026-07-25)

Triage after D2 (`SECURITY.md`) + D7 (any-seekable OpenSpec archive) on top of
`main` @ `033a883`, then T2 #199 + D4.

## At a glance

| Review | Findings delivered? | Code/docs follow-ups | Ready to archive? |
|--------|---------------------|----------------------|-------------------|
| `debt-ledger/` | yes (2026-07-20; **refreshed 2026-07-25**) | **DONE:** D1 #191, D2 #198, D3 #193, D4, D7 #198, T2 #199, DD4 #194/#196, S2/S3+T1 #184, Q1–Q5. **Open:** T3/T7, T4 half-test | no |
| `performance/` | yes (#134 + follow-ups) | residual **accepted aspirational** (#191); wall Q2 decided (#171); **Q4 open** | no |

Archived OpenSpec this window: `unify-pass-driver`, `gzip-zlib-truncation-recovery`,
`rapidgzip-truncation-investigation` (2026-07-24),
`gzip-truncation-backstop-any-seekable` (2026-07-25).

---

## 1. Actionable right now

| ID | Action |
|----|--------|
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
| D2 `SECURITY.md` + D7 any-seekable OpenSpec archive | #198 |
| T2 seek-interleaving XZ / lzip / `.Z` | #199 |
| D4 `open-issues.md` P1 sweep | this change |
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

---

## Notes

- Private vulnerability reporting is **enabled** on `davitf/archivey`; see root
  `SECURITY.md`.
