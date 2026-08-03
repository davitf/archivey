# 0014 — Integrity verdicts surface from reads, never from `close()`

- **Status:** accepted
- **Date:** 2026-07 (review of `gzip-zlib-truncation-recovery`, PR #183)
- **Provenance:** OpenSpec `compressed-streams` (digest verification, read-vs-close
  fault split); `VISION.md` (no silent success; damaged input is first-class);
  `verification-integrity-mode` change (STRICT opt-in)

## Context

A member that carries a stored checksum or an authentication tag is verified as it
is read. The question this settles is **which call reports the verdict**. Reporting
from `close()` is a trap: `close()` runs in `__exit__` and in `finally` blocks, where
raising masks the original exception, and a caller who stops reading early has not
asked for a verdict at all.

## Decision

**Integrity verdicts surface from reads, never from `close()`.**

- `read(n)` is **full-count**: it returns exactly `n` bytes unless it hits a terminal
  boundary, so a short return is always terminal — never "healthy data, ask again".
- Reading a member to its end raises `CorruptionError` on proven-wrong bytes,
  **withholding** the reaching chunk; a truncation-shaped end delivers the
  best-effort prefix and raises `TruncatedError` on the read past it.
- Stopping early is not verification, and is quiet.
- `close()` never raises a content error (target contract; best-effort on a few
  backends today).

Callers who need verification regardless of access pattern use
`VerificationMode.STRICT` (`verification-integrity-mode`).

## Consequences

- Settles the contract `gzip-zlib-truncation-recovery` (#183) implements, revising
  its earlier "never withhold the last chunk" rule for size-declared corruption.
- The user-facing guarantee and the call × failure matrix are published on
  `docs/reading-members.md`.
- Rationale, rejected alternatives, the full-count trade-off analysis, and the
  implementation notes are in
  [`dev-docs/investigations/adr-0014-investigation.md`](../investigations/adr-0014-investigation.md).
