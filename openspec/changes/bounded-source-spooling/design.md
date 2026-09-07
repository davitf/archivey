# Design — bounded source spooling

Specs-first, following `seekable-gzip-and-block-writing`. This file carries the reasoning
and the questions the proposal does not settle; the deltas carry only what is decided.

## Decision 1 — one limit, not two kinds

**Superseded design, recorded because the reasoning is the useful part.** The first draft
split spooling in two:

- a **tool-tax** spool — materializing an already-seekable source because `unrar` accepts
  only a filesystem path — permitted by default;
- a **capability** spool — making a non-seekable source seekable — refused by default,
  on the argument that passing a pipe *is* the statement "stream this", so spooling it
  overrides a choice the caller made.

**The distinction is real inside the library and invisible outside it.** Both write the
same bytes to the same directory at the same cost. Both are avoided by the same remedy:
pass a path. Both are made safe by the same bound. The asymmetric-preference test is what
kills it — "I want RAR-from-stream but never pipe buffering" is answered by the *limit*,
not by a second switch, and the reverse preference ("pipes yes, RAR temp files no") is
incoherent.

What the split was actually doing was avoiding a breaking change to RAR-from-stream reads,
dressed as a user-facing taxonomy. Topic 9's brief rules that out directly:

> **Behaviour churn is free until the `0.2.0` tag** — "this would be a breaking change" is
> not a reason to prefer documenting a quirk over deleting it.

So: one number. `spool_limit` decides *whether and how much*; nothing in the public surface
records *why* a spool was needed.

## Decision 2 — a limit, not a boolean

`allow_temp_files: bool` was the next obvious shape and is still worse than a number, for
two reasons:

1. **It forces the default question to be all-or-nothing.** A modest default limit lets
   ordinary archives keep working from streams *and* pipes while a 40 GB one fails loudly.
   A boolean has to choose between "everything works, unbounded" and "nothing works".
2. **`None` already means what a boolean's `False` would**, and `UNLIMITED` already means
   what `True` would, so the boolean adds a second way to say things the number says.

`ExtractionLimits.UNLIMITED` and `ListingLimits.UNLIMITED` establish the sentinel pattern;
this reads as the third member of an existing family rather than a new concept.

## Decision 3 — `CostReceipt.notes`, not a diagnostic

P11 left this open. The `diagnostics` capability settles it once the spool is *bounded*:

- The **admission** clause covers what a caller could not determine from the declared
  contract of the call. A spool inside a limit the caller set is declared.
- The **placement** clause prefers a structured field where one exists. `CostReceipt.notes`
  exists and is already the home for per-reader cost facts.

The reasoning inverts for *today's* unbounded, unreported behaviour, which no caller
declared — which is why P11 is a defect and not a preference.

## Decision 4 — config, not a per-call argument

`ArchiveyConfig` already carries `extraction_limits` and `listing_limits`; `limits=` is a
per-call argument on extraction only. The dividing line the repo already uses is scope: a
per-call argument governs one operation, config governs the reader. Source handling affects
every read the reader performs.

## Decision 5 — the limit is the guard; free space is a heuristic

A pre-flight space check is worth having and worth not trusting:

- `TMPDIR` may be a different device than the working directory, sized independently.
- Free space is a race — another process can consume it between check and write.
- Containers frequently report the host's figures rather than the container's quota.
- **A memory-backed temporary directory turns "spool to disk" into "spool to RAM"** — ADR
  0010's unbounded memory use, reached through the door marked disk. Several distributions
  mount `/tmp` as `tmpfs` by default. (Checked on the development container while writing
  this: `/tmp` there is `ext4`, so this is a known risk rather than an observed one, and it
  is a property of the deployment rather than of archivey.)

So the **byte limit is the contract** — checked before the write when the size is known and
during the write when it is not. The space check only fails earlier and more legibly than
`ENOSPC` would. The caller can name the spool directory precisely so someone who knows
their `/tmp` is `tmpfs` can point elsewhere.

## Decision 6 — when the spool happens is documented, not configurable

The maintainer's one surviving objection to collapsing the kinds was *timing*: a spool for
an external binary happens at the first read that needs it, while a spool to make a source
seekable happens at open. That difference is real and caller-visible — an archive can be
listed without paying for it in one case but not the other — so it is **specified and
documented**, not turned into a knob. `CostReceipt.notes` records the spool when it
happens, which makes the timing observable rather than something to reason about.

## Out of scope — caching decompressed payloads

Writing *decompressed* member data to disk to make seeking cheap is a **caller-side**
concern: the natural shape is a wrapper stream around a member, which archivey might ship
or recommend later, but which does not belong in the source layer.

| | Source spool (this change) | Payload cache (not this change) |
|---|---|---|
| Bounded by | archive size, often known at open | uncompressed size — decompression-bomb territory |
| Motivated by | an external binary's API, and pipe sources | seek performance |
| Evidence needed | none; the behaviour already exists unbounded | hit rate, and which paths are actually `readinto`-bound |
| Natural home | the reader's source handling | a wrapper stream the caller composes |
| Overlaps | — | Topic 6, and the parked `stream-layering` **Q4** |

## Open questions for the maintainer

1. **The default limit.** Every number is arbitrary and this one is load-bearing: it decides
   which of today's working RAR-from-stream reads start failing. Candidates: a fixed size
   (1 GiB keeps essentially everything working; 64 MiB makes the bound meaningful);
   `UNLIMITED` (nothing breaks, and the *reporting* becomes the whole fix — defensible,
   since unbounded-**and**-unreported is what makes P11 a defect); or `None` (safest, and
   removes a capability that works today).
2. **Packaging.** Two flat fields on `ArchiveyConfig` (`spool_limit`, `spool_dir`) or one
   frozen `SpoolLimits` object with `.UNLIMITED`, matching `ExtractionLimits` /
   `ListingLimits` exactly. The object matches the family; the flat fields are two lines
   shorter to use.
3. **Which error when spooling is refused** — `ArchiveyUsageError` (the caller configured
   the refusal, so it is their mistake) or `UnsupportedOperationError` (from the reader's
   side it is an operation the archive cannot provide under this configuration). Note the
   two sit on opposite sides of the `ArchiveyError` boundary (ADR 0012), so this is not
   cosmetic. The refusal for a **non-seekable source** stays `StreamNotSeekableError`
   either way, since that is the existing contract.
4. **Whether `streaming=True` plus a spooled source streams or seeks.** Spooling makes the
   source seekable, so the reader could switch to random access, or honour the stated intent
   and stream from the spooled file. The second is more predictable; the first is faster.
