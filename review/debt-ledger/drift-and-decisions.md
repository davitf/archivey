# Doc ↔ spec ↔ code drift + the deferred-decision register

Original refs: `main` @ `7bb862b`. **Status refresh 2026-07-25** against
`main` @ `033a883`, then D2/D7 pay.

Live OpenSpec: `seekable-gzip-and-block-writing` 0/24. Archived this window:
`2026-07-24-unify-pass-driver`, `2026-07-24-gzip-zlib-truncation-recovery`,
`2026-07-24-rapidgzip-truncation-investigation`,
`2026-07-25-gzip-truncation-backstop-any-seekable`.

## D1 — VISION ≤1.3× — **DONE (#191)**

Aspirational peer-ratio bands + measured nightly table in `docs/costs.md`.

## D2 — SECURITY.md — **DONE**

Root `SECURITY.md`: private report via GitHub Security Advisories (preferred),
supported-versions note, archive-relevant scope, accelerator-off guidance for
hard-latency untrusted input, pointers to safe-extraction + threat-model.
Peer pattern: rarfile / libarchive short policy (py7zr has no `SECURITY.md`;
archivey’s own bar still required it). OSS-Fuzz may trail.

## D3 — no CHANGELOG — **DONE (#193)**

`CHANGELOG.md` + `docs/internal/release-checklist.md` (Q5).

## D4 — `open-issues.md` stale (PAY)

P1 (Option F) still under "candidates to fix" with dead
`openspec/changes/decide-strict-archive-eof-default/` ref; suggested-first-cuts
still says "apply it". **PAY** 15-min sweep.

## D5 / D6 — **DONE (2026-07-20)**

## D7 — OpenSpec archive/sync hygiene — **DONE**

| Change | Status |
|--------|--------|
| `unify-pass-driver` | Archived 2026-07-24 |
| `gzip-zlib-truncation-recovery` | Archived 2026-07-24 |
| `rapidgzip-truncation-investigation` | Archived 2026-07-24 (DD4) |
| `gzip-truncation-backstop-any-seekable` | Synced into `seekable-decompressor-streams`; archived 2026-07-25 |

## What is *not* drifting (fine)

CLI / ExtractionStatus / `OnError.STOP` / threat-model spot checks; OpenSpec
sync for shipped changes. `docs/grab-bag/` historical — KEEP.

## Deferred-decision register

| ID | Verdict |
|---|---|
| **DD1** wall enforcement | **DONE** #171 |
| DD2 verify-skip knob | **KEEP** (leave-as-is) |
| **DD3** L5 vs aspirational bands | **DONE** #191 — Q2 (b) |
| **DD4** rapidgzip truncation | **DONE** #194 / #196 |
| DD5 seekable-gzip-and-block-writing | **KEEP** post-0.2.0 |
| DD6 Salvage | **KEEP** for 0.2.0 |
| DD7/DD8 CLI `--json` / `--raw` | **KEEP** |
| DD9–DD12 threat-model / C3 / Q5 / C4 | **KEEP** |
| **N1** pyppmd residual | **KEEP** — mitigated; soft-pass until hot-race clear |
