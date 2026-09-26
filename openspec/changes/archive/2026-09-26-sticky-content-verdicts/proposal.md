# A content verdict sticks to its stream

## Why

The S28 re-sweep found that a member stream delivered a content verdict once (S28-K1):
after a CRC or HMAC mismatch raised, `seek(0)` and a second `read()` returned the whole
damaged member with no error. The verifier checks a member only on the read that reaches
its end, and a seek back forfeits that. davi ruled (2026-09-26): keep raising.

## What Changes

- `ArchiveStream` keeps the first content verdict (`CorruptionError` / `TruncatedError`,
  or an error raised from one) and raises it again on every later read and seek, with
  its first traceback so a retry loop does not grow it.
- `tell()`, `seekable()` and `close()` are not gated.

## Impact

- `compressed-streams` spec: the content-faults requirement and its matrix.
- ADR 0014 records the reasoning; `docs/errors-and-diagnostics.md` the user-facing rule.
