# 0012 — Usage errors are outside `ArchiveyError`

- **Status:** accepted
- **Date:** 2026-07-11 (`concurrent-member-streams`)
- **Provenance:** that change’s design D4; OpenSpec `error-handling` /
  `archive-reading`

## Context

`except ArchiveyError` is the natural “archive or environment failed” handler.
Caller bugs (undeclared concurrent open, operating on a closed reader) must not be
swallowed by that blanket.

## Decision

Introduce `ArchiveyUsageError` (and `ConcurrentAccessError`) **not** subclassing
`ArchiveyError`. Archive/mode/feature limitations stay `ArchiveyError`
(`UnsupportedOperationError`, etc.; see the amendment below, which moved mode misuse to
`ArchiveyUsageError` and removed both named subclasses). Stream protocol stays stdlib-shaped
(`ValueError` / `io.UnsupportedOperation`).

## Consequences

- Misuse fails loudly in development.
- Applications can catch archive failures without masking programmer errors.

## Amendment (2026-09-27, before 0.2.0)

The line between the two roots moved, and two types went away:

- **Access-mode misuse is a usage error.** `members()`, `get()`, `open()` or `read()` on a
  `streaming=True` reader, and driving a reader from inside a diagnostic callback, now
  raise `ArchiveyUsageError`. The caller chose the mode, so the call is a bug in the
  calling code, which is the rule the maintainer gave when keeping `TypeError` and
  `ValueError` for wrong argument types: usage errors are for what the types alone cannot
  rule out, "e.g. calling a method at an invalid mode".
- **`UnsupportedOperationError` is removed.** What it still covered once the mode cases
  left was an archive or backend that cannot serve a request (a RAR password with a line
  break for `unrar`), which is `UnsupportedFeatureError`.
- **`ConcurrentAccessError` is removed.** A usage error reports a bug to fix, not a case
  to catch, so a subtype of one had no reader; a second overlapping `open()` raises
  `ArchiveyUsageError` with the same message.

The same pass folded the five `FilterRejectionError` subclasses into their parent,
`SpoolLimitExceededError` into `ResourceLimitError`, and `UnsupportedFormatError` into
`PackageNotInstalledError` (missing package), `UnsupportedFeatureError` (no backend) and
`ArchiveyUsageError` / `FormatDetectionError` (`open_stream()` on something that is not
a compressed stream). The rule behind all of it: a type is public only when a caller
would act on it differently from its parent. Adding a subclass back later breaks no one;
removing one after a release does.
