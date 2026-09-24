# Design

## Trailing-bytes scan: always on, bounded, and quiet on a tail that will not decode

The flag did two structurally different things. On a missing trailer it was a severity
switch, which a `RAISE` disposition replaces directly. On the trailing-bytes scan it was a
work switch: without it the scan never ran, so `ARCHIVE_TRAILING_DATA` was never emitted
and no disposition could reach it. The ruling makes the scan unconditional and bounds it
at a 1 MiB module constant.

Why 1 MiB and a constant, from the ruling thread (measured on `main` at `7604f28`): a
gzipped tar with a 1 MiB zero tail costs ~5 ms; the zero tail is the worst case because
the scan stops at the first non-zero byte. `tar` pads to 10 KiB records, so the bound is a
hundred times where a concatenated archive's header normally lands. A config field would
be hard to take back after 0.2.0, and on `ListingLimits` a `None` would have to mean "scan
to EOF", the opposite of what `None` means on every other field there.

`ARCHIVE_TRAILING_DATA` also loses its `CorruptionError` escalation. With the scan
unconditional, keeping it would make every tar with appended bytes (a concatenated pair,
a padded download) fail to list by default. The ruling's point was that the detection
becomes a diagnostic a caller can set to `RAISE`; the escalation is what `strict()` now
provides.

**A tail that does not decode ends the scan without an error.** Found while testing, not
covered by the ruling. On a `.tar.gz`, reading past the tar trailer now reaches the end of
the gzip stream, and the codec refuses junk after it and a missing footer. Before, the
default path never read there, so these archives listed cleanly. Raising would turn a
listing that succeeded into an error because of bytes outside every member. Reporting
`ARCHIVE_TRAILING_DATA` would misname it. So a `ReadError` from the tail read ends the scan
quietly. The members themselves are unaffected, since each was read whole.

**A forward-only source is read up to 1 MiB further than before.** On a pipe the scan
waits for those bytes or for EOF. A pipe that closes when its writer finishes is
unaffected; one held open after the tar ends (a socket kept alive by its sender) now
blocks at the end of the listing until more data or EOF arrives, where before it returned
once `tarfile` had its trailer. The ruling made the scan unconditional without singling
out non-seekable sources, so this change does not either; a caller in that position can
close the write side. Recorded so the choice is visible.

## Missing trailer under `RAISE` is not listing damage

`strict_archive_eof` raised `TruncatedError`, which `members_report()` carries on
`report.error` as terminal listing damage. A `RAISE` disposition raises
`DiagnosticRaisedError`, which is the caller's policy firing, and `members_report()`
propagates it like any other policy escalation during a listing. The `members_report`
matrix row that pinned the strict case now says so. The ruling named the exception-type
change; this is its consequence for the report model.

## Raw sector images: magic at offset 0, probe before the reader

The refusal needs the file to reach the ISO backend, so the sync pattern becomes a second
ISO magic. The probe lives in `iso_reader` but `open_archive` calls it before the
backend's availability check: otherwise a caller without `pycdlib` is told to install it
and only then learns the file cannot be read. It leaves a stream at the position it was
handed in at, and is skipped for a non-seekable source, which the seekability refusal
answers instead. The
sector layout facts come from the S22-K6 thread's prototype: byte 15 is the mode, submode
bit `0x20` splits Mode 2 Form 1 from Form 2, and the sector size is where the second sync
lands. The implementation notes for reading them later are in `dev-docs/IDEAS.md`.

## `__module__` pin: computed over `__all__`

A hand list of seventeen names would go stale the day an eighteenth arrives, so
`__init__` walks `__all__` and pins every class and function whose module starts with
`archivey.internal`. Instances are skipped: they report their class's module, and setting
the attribute on one would be wrong (and fail on a frozen dataclass). A test asserts no
public name reports an internal module.

The ruling asked whether the rendered API docs change. They do not: griffe reads source
statically. The internal path survives in the built site only in three hover titles (two
cross-reference tooltips and `ArchiveStream`'s base class). That is a separate fix in the
griffe extension if it is wanted, not a reason to skip the pin.
