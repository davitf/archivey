# Structural debt — S1–S4 revisited, module seams, markers

Original refs: `main` @ `7bb862b`. **Status refresh 2026-07-25** against
`main` @ `3793646`.

## S3 — one pass-stream driver — **DONE (#184)**

`BaseArchiveReader._drive_pass_streams` shared by base / TAR
(`close_previous=False`) / 7z / RAR. OpenSpec archived
`2026-07-24-unify-pass-driver/`.

## S2 — one finalize path — **DONE (#184)** with S3

One `_finalize_links` / `_finalize_pass_links` double-fault policy. Drive-loop
*shapes* remain two by design.

## S1 — one error boundary: **paid** (fine; TAR open-error residue KEEP)

## S4 — ReaderState: reworked, not accreted (fine)

## Module-split coherence (fine) — KEEP all

## Markers / leftovers

- O7 rename follow-up — registered threat-model residual.
- **`VerifyingStream`:** still used for container length bounds / unit tests;
  rapidgzip path now has its own truncation backstop (#194/#196). Topic 6
  adjacency unchanged — **KEEP** until nothing needs the standalone wrapper.
- Public two-tier `__init__.py` exports deliberate.
