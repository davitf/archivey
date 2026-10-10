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
- **A verdict sticks to its stream.** Once a read has raised a content verdict
  (`CorruptionError`, `TruncatedError`, or an error raised from one), every later read
  raises it again until the caller seeks. A seek restarts the decode, so the prefix
  reads again, and the read that reaches the end raises the verdict again, even after a
  seek that forfeited the digest check. (Since 2026-10-01 a seek to 0 re-arms the
  digest instead of forfeiting it; the same error object is still the one raised.) Reaching the end is a position, not a short return:
  a full `read(member.size)` reaches it too. Without this, a caller who caught the
  verdict and seeked back re-read the damaged member with no error, because a verifier
  checks a member once. The maintainer chose to keep raising on 2026-09-26 (sweep
  finding S28-K1); letting the seek through matches the rewind rule for truncated
  streams (#491). It lives in `ArchiveStream`, so it holds for every format.
- **A seek loses the digest only by skipping bytes.** (Since 2026-10-10; before, any
  seek but one to 0 forfeited it.) The verifier hashes from 0 to its furthest read, so
  a seek back keeps the digest, and so does a forward seek whose inner decodes the
  skipped bytes anyway: the verifier asks the inner where it would resume
  (`ask_seek_resume_offset`) and, when that is at or before the frontier, reads the
  gap through its hashers itself at the same cost. A seek to or past the declared size
  defers the gap to the concluding read, which hashes it. Only a read that starts past
  the frontier, after a jump by a seek index, an accelerator or random access, forfeits
  the digest. The maintainer asked for this on 2026-10-10.
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
