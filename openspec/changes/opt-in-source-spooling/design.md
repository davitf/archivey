# Design — opt-in source spooling

Specs-first, following `seekable-gzip-and-block-writing`. This file carries the reasoning
and the questions the proposal does not settle; the deltas carry only what is decided.

## Decision 1 — two kinds, not one knob

The temptation is a single `allow_temp_files: bool`. It is wrong, because the two spools
differ in what they take from the caller:

- A **tool-tax** spool takes nothing. The caller handed over a seekable source; every byte
  was already addressable; `unrar` merely cannot accept a Python object. Refusing it would
  break RAR-from-stream reads that work today, for a rule that does not describe the case.
- A **capability** spool overrides an explicit choice. Passing a pipe *is* the statement
  "stream this"; spooling it silently is the surprise ADR 0010 was written about.

One flag would force the same default onto both, and either default is wrong for one of
them. Hence two fields and asymmetric defaults.

**The honest cost of the asymmetry:** "temp files are off by default" becomes false as a
one-liner, and a reader has to learn the distinction. That is a documentation cost paid
once, against a behaviour cost paid on every RAR-from-stream read. The alternative — one
flag defaulting to permissive — reinstates ADR 0010's problem; one flag defaulting to
restrictive silently breaks working code.

## Decision 2 — `CostReceipt.notes`, not a diagnostic

P11 left this open. The `diagnostics` capability settles it once the spool is *configured*:

- The **admission** clause covers what a caller could not determine from the declared
  contract of the call. Once the policy is explicit and bounded, a spool is declared — the
  caller enabled it and set its ceiling. Emitting a diagnostic would report a choice back
  to the person who made it.
- The **placement** clause prefers a structured field where one exists. `CostReceipt.notes`
  exists, is already the home for per-reader cost facts, and is queryable.

The reasoning inverts for *today's* unbounded behaviour, which no caller declared — which is
exactly why P11 is a defect and not a preference.

## Decision 3 — config, not a per-call argument

`ArchiveyConfig` already carries `extraction_limits` and `listing_limits`; `limits=` is a
per-call argument on extraction only. The dividing line the repo already uses is scope: a
per-call argument governs one operation, config governs the reader. Source handling affects
every read, so it is config. This also keeps `open_archive`'s signature from growing a
parameter that would have to be threaded through `open_stream` and `extract` identically.

## Decision 4 — the limit is the guard; free space is a heuristic

A free-space pre-flight is worth having and worth not trusting:

- `TMPDIR` may be a different device than the working directory, sized independently.
- Free space is a race — another process can consume it between check and write.
- Containers frequently report the host's figures rather than the container's quota.
- **A memory-backed temporary directory turns "spool to disk" into "spool to RAM"**, which
  is ADR 0010's unbounded memory use reached through the door marked disk. This is not
  hypothetical: several distributions mount `/tmp` as `tmpfs` by default. (Checked on the
  development container while writing this: `/tmp` there is `ext4`, so this is a known risk
  rather than an observed one, and it is a property of the deployment, not of archivey.)

So: the **byte limit** is the contract, checked before the write when the size is known and
during the write when it is not. The space check only fails earlier and more legibly than
`ENOSPC` would. The policy lets the caller name the directory precisely so a caller who
knows their `/tmp` is `tmpfs` can point elsewhere.

## Out of scope, and why — caching decompressed payloads

Writing *decompressed* member data to disk to make seeking within a member cheap is a
different feature and does not belong here:

| | Source spool (this change) | Payload cache (not this change) |
|---|---|---|
| Bounded by | archive size, known at open | uncompressed size — decompression-bomb territory |
| Motivated by | an external binary's API, and pipe sources | seek performance |
| Evidence needed | none; the behaviour already exists unbounded | hit rate, and which access patterns are actually `readinto`-bound |
| Overlaps | — | Topic 6, and the parked `stream-layering` **Q4** (`SlicingStream.readinto`) |

Specifying a cache before measuring its hit rate is how a cache becomes permanent overhead.
It also interacts with `STREAM_REWIND_REDECOMPRESSES` and the `[seekable]` accelerator
story, both of which are Topic 6's ground. Recorded in `dev-docs/IDEAS.md` §Performance.

## Open questions for the maintainer

1. **The default byte limit.** A number has to be picked and every number is arbitrary.
   Candidates: a fixed size (say 1 GiB); a fraction of reported free space (interacts badly
   with Decision 4's caveats); or `None` meaning unbounded-but-reported, which keeps today's
   RAR behaviour working unchanged and makes the limit purely opt-in. The third is the
   smallest behaviour change and the weakest guard — and P11 is a defect precisely because
   unbounded-and-unreported is the status quo, so "reported" may be the half that matters.
2. **Naming.** `SpoolPolicy` / `TempStoragePolicy` / `SourceSpooling`, and whether the two
   fields read as `allow_tool_spool` / `allow_capability_spool` or as one enum with three
   values (`NEVER` / `TOOL_ONLY` / `ALWAYS`). The enum is tidier and forecloses a future
   third kind less gracefully.
3. **Does a refused tool-tax spool raise `ArchiveyUsageError` or `UnsupportedOperationError`?**
   The delta says usage error, on the grounds that the caller configured the refusal. The
   counter-argument is that from the reader's side it is an operation the archive cannot
   provide under the current configuration, which is what `UnsupportedOperationError` names.
4. **Whether `streaming=True` plus a capability spool is coherent.** Spooling makes the
   source seekable, so the reader could ignore `streaming=True` and use random access — or
   honour the caller's stated intent and stream from the spooled file. The second is more
   predictable; the first is faster. Not obvious, and it is a caller-visible difference.
