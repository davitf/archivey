# Design — bound the RAR stream-source copy

No deeper design beyond what `bounded-source-spooling/design.md` already records; this
change implements its Q1 (1 GiB default), Q2 (a frozen `SpoolLimits` with an `UNLIMITED`
classvar) and Q3 (a dedicated `SpoolLimitExceededError` subclassing `ResourceLimitError`,
ruled by davitf on 2026-09-17) for the one spool archivey performs today. `except
ResourceLimitError` still catches the trip; a caller who wants to tell a refused copy from
an extraction or decoder limit catches the subclass. The pipe spooling left in
`bounded-source-spooling` raises the same type when it lands. Three points are this
change's own.

## `None` means "no limit", not "never spool"

`bounded-source-spooling` sketched `SpoolLimits(max_bytes=None)` as *never spool*, with
`UNLIMITED` as a separate sentinel. That reverses the convention every other limit here
follows: on `ExtractionLimits`, `ListingLimits` and `DecoderLimits`, `None` disables the
guard and `UNLIMITED` is the instance with every field `None`. A caller who has learned
that would read `max_bytes=None` as the opposite of what it did. So `None` disables the
guard here too, and "never spool" is `max_bytes=0`: a limit of zero bytes, which that
change's own Q3 already describes as the same refusal.

## One budget per reader, checked twice

`archivey.internal.spool.SpoolBudget` holds the allowance. The reader makes one on its
first copy and keeps it for its lifetime, so the limit weighs every byte the reader
writes: a copy that failed part-way still counts, and once the budget has refused, every
later call refuses without writing. A budget per attempt would let a source of unknown
size cost a full `max_bytes` write on each read that retried the copy, as every member of
`extract_all(on_error=OnError.CONTINUE)` does.

The reader calls `check_total()` with the size it already knows — `SharedSource.size` for
one stream, the joined volume ranges for a set — before `mkstemp` / `mkdtemp`, so an
oversized archive, or a retry after a refusal, leaves nothing behind. `copy()` then
enforces the same allowance per chunk, reading at most one byte past what is left, so a
size that was unknown or wrong still cannot carry the file past the limit. File volumes of
a mixed set go through `copy()` too, rather than `shutil.copy2`, so a file that grew after
the set was joined is still counted.

## The caveat says "refused" when the limit decides at open

The open-time caveat names the limit in force. When the limit already rules the copy out
— `max_bytes=0`, or a copy whose size is known at open and is over the limit — it says a
read that needs `unrar` will be refused, instead of promising a copy that cannot happen.
The size is a fact of the source at open, so this keeps the caveat a static snapshot.
