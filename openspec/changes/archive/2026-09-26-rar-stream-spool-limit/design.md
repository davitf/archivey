# Design — bound the RAR stream-source copy

No deeper design beyond what `bounded-source-spooling/design.md` already records; this
change implements its Q1 (1 GiB default) and Q2 (a frozen `SpoolLimits` with an
`UNLIMITED` classvar) for the one spool archivey performs today. Three points are this
change's own.

## `None` means "no limit", not "never spool"

`bounded-source-spooling` sketched `SpoolLimits(max_bytes=None)` as *never spool*, with
`UNLIMITED` as a separate sentinel. That reverses the convention every other limit here
follows: on `ExtractionLimits`, `ListingLimits` and `DecoderLimits`, `None` disables the
guard and `UNLIMITED` is the instance with every field `None`. A caller who has learned
that would read `max_bytes=None` as the opposite of what it did. So `None` disables the
guard here too, and "never spool" is `max_bytes=0`: a limit of zero bytes, which that
change's own Q3 already describes as the same refusal.

## `ResourceLimitError`, not a new subclass yet

Q3 settled a dedicated `SpoolLimitExceededError` subclassing `ResourceLimitError`. This
change raises `ResourceLimitError` itself. Adding the subclass later is not a break —
`except ResourceLimitError` keeps catching it — while shipping it now adds a public name
for one call site. It stays in `bounded-source-spooling`, whose pipe spooling gives it a
second site.

## One budget per copy, checked twice

`archivey.internal.spool.SpoolBudget` holds the allowance. The reader calls
`check_total()` with the size it already knows — `SharedSource.size` for one stream, the
joined volume ranges for a set — before `mkstemp` / `mkdtemp`, so an oversized archive
leaves nothing behind. `copy()` then enforces the same allowance per chunk, reading at most
one byte past what is left, so a size that was unknown or wrong still cannot carry the file
past the limit. File volumes of a mixed set go through `copy()` too, rather than
`shutil.copy2`, so a file that grew after the set was joined is still counted.
