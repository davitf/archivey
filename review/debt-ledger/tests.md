# Test-strategy holes

Original refs: `main` @ `7bb862b`. **Status refresh 2026-07-25** @ `3793646`.

## Paid since the original ledger

- Randomized seek (XZ), both-idioms truncation (`.Z`), Atheris gate, etc.
- **T1 solid-RAR mutation — DONE (#184).**

## T1 — **DONE (#184)**

Static solid RAR4/RAR5 under mutation. Encrypted-header / multi-volume still
outside mutation → **T7**.

## T2 — seek-interleaving stops at XZ (PAY)

Still only `test_xz_seek_interleaving_matches_plaintext`. Parametrize over
lzip / `.Z`. **Open.**

## T3 — benchmark-gate RAR / encrypted / accelerator data (PAY)

Still no hits in `test_benchmark_gate.py`. Perf P6 remainder. **Open.**

## T4 — free-threaded core-only; `*_if_available` untested under threads

KEEP scope / **PAY** one multithread barrier test — still missing.

## T5 / T6 — KEEP (opportunistic / past 0.2.0)

## T7 — corpus matrix thin spots (PAY)

ISO only in `basic`; enc-header 7z / multi-volume mainly outside sweep+mutation.
**PAY** audit + cheap extensions; record deliberate exclusions.
