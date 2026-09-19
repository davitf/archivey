# One spool limit, and a bound on the spool that already happens

## Why

`unrar` takes a filesystem path, not a Python object (ADR 0002: native RAR metadata,
external binary for data). So `RarReader._ensure_archive_path()` writes the **whole
archive** to `tempfile.mkstemp(suffix=".rar")` the first time a member cannot be read
directly, and `_materialize_stream_volumes()` does the same for multi-volume stream
sources.

This was `dev-docs/open-issues.md` **P11**, filed when the copy was both unbounded and
unreported. **The reporting half has since shipped.** `format-rar` now requires a
disk-copy caveat in `ar.cost.notes` at open for non-path stream sources, and
`rar_reader.py:119` emits it. Re-measured on a `rar -m5` archive read from a `BytesIO`,
on `main` at `74a8f92`:

```
archive size: 65 946                read from stream: 1 048 576 bytes, ok
cost.notes: ('Reading a compressed member will copy the whole archive to disk
              so RARLAB unrar or rar can read it.',)
diagnostics: 0                      path source cost.notes: ()
new temp .rar: tmpwcvadlai.rar      65 946 bytes  (the whole archive)
```

**The caller is now told, and still cannot say no.** There is no size limit and no
configuration: `spool_limit` does not exist, so a caller handing over a 4 GiB `BytesIO`
reads an accurate warning and then gets a 4 GiB temp file anyway. A warning without a
bound is half an answer — it converts a hidden cost into a declared one, which is
progress, but it leaves nothing for the caller who wants the cost refused rather than
announced. **That remaining half is what this change is now for**, and it is the only
half: the note's wording, its placement in `notes` rather than `diagnostics`, and its
at-open timing are settled and shipped, and this change does not revisit them.

Separately, ZIP, 7z, RAR and ISO all declare `required_source = SEEKABLE`, so a pipe is
refused at open with `StreamNotSeekableError` under both `streaming=False` and
`streaming=True`. ADR 0010 decided that deliberately: *"A convenience path that buffers a
pipe into memory or a temp file to 'make ZIP work' hides unbounded resource use and
surprises callers who thought they were streaming."* That reasoning is about **hidden**
resource use, not about temporary storage, so a bounded and reported spool extends ADR 0010
rather than reversing it — and there is a real class of caller (an archive arriving over
HTTP, a socket, `stdin`) for whom "buffer it yourself first" is the only answer archivey
gives today.

## What Changes

**One limit, not a taxonomy.** Archivey spools an archive source to temporary storage
whenever it needs to, bounded by a single configured byte limit, and reports every spool it
performs. It does not distinguish *why* the spool was needed:

```
SpoolLimits(max_bytes=<bytes>)     # spool when needed, up to this much
SpoolLimits.UNLIMITED              # never refuse on size
SpoolLimits(max_bytes=None)        # never spool
```

**The default is 1 GiB**, settled with the other three open questions on 2026-09-17. A byte
count rather than unlimited (which leaves the behaviour that makes this a defect in place)
or none (which removes a capability that works today).

An earlier draft of this proposal split the limit in two — one switch for materializing a
seekable source for an external binary, another for making a non-seekable source seekable —
on the argument that the second overrides a choice the caller made and the first does not.
**That distinction does not reach the caller.** Both write the same bytes to the same place
at the same cost, both are avoided by the same remedy (pass a path), and both are made safe
by the same bound. The asymmetric default it implied existed to avoid breaking
RAR-from-stream reads, and *"this would be a breaking change"* is explicitly not a reason
to shape the API this way before the `0.2.0` tag. One number covers it.

**`None` is the no-temp-files posture, in one field.** A caller who wants archivey never to
touch temporary storage sets one value and gets a typed refusal everywhere it would have.

**The limit is the guard; free space is a heuristic.** Where a pre-flight space check is
possible it fails earlier and more legibly than `ENOSPC` would, but it is never presented
as a guarantee — `TMPDIR` may be a different device, free space is a race, containers report
host figures, and a memory-backed temporary directory makes a byte limit a *memory* limit.
The caller can name the spool directory for exactly that reason.

**The open-time caveat gains its bound.** P11's note-versus-diagnostic question is already
answered and shipped; what the caveat cannot say today is how large the copy may get, because
nothing bounds it. With a limit configured it can, so the existing note names it. Nothing is
appended when a spool actually happens: `CostReceipt` is an immutable open-time description,
and `format-rar` already requires the caveat to be a static statement rather than an
occurrence log.

**Explicitly out of scope: caching decompressed member payloads.** Writing *decompressed*
data to disk to speed seeking is a caller-side concern best served by a wrapper stream
around a member — something archivey might ship or recommend later, but not this change and
not this layer. It is bounded by uncompressed size rather than archive size, therefore in
decompression-bomb territory, and it overlaps Topic 6 and the parked `stream-layering`
**Q4**. Recorded in `dev-docs/IDEAS.md` with that reasoning.

## Specs

- **`access-mode-and-cost`** — ADDED: the spool limit, its three settings, the
  1 GiB default, the `SpoolLimitExceededError` on exceeding it, the open-time caveat naming
  its bound, the best-effort pre-flight, the rule that a spool happens at the first
  operation needing it rather than at open, and the rule that a spooled source does not turn
  a `streaming=True` read into a random-access one. MODIFIED: the non-seekable fail-fast
  requirement gains its "unless spooling is permitted" clause, so ADR 0010's rule stays
  stated rather than quietly outgrown.
- **`archive-reading`** — MODIFIED: the limit reaches the reader through `ArchiveyConfig`,
  beside `listing_limits`, as a frozen `SpoolLimits` with an `UNLIMITED` classvar.
- **`format-rar`** — MODIFIED: the existing materialization becomes subject to the limit
  and to the cost note.

## Impact

- **Public surface:** one new `ArchiveyConfig` field carrying a frozen `SpoolLimits`
  (limit plus spool directory), beside `extraction_limits` and `listing_limits`; and one new
  exception, `SpoolLimitExceededError`, subclassing `ResourceLimitError`. Additive; worth
  settling pre-`0.2.0` because a config field is cheap to add later and expensive to
  reshape.
- **Behaviour change, pre-tag and deliberate:** a RAR-from-stream read of an archive larger
  than 1 GiB starts raising `SpoolLimitExceededError` where it previously succeeded. The
  unbounded case is the defect. **No corpus archive reaches that boundary** — the largest is
  188 KiB — so CI will not catch a badly chosen default, and the implementation owns a test
  that drives the boundary directly.
- **Capability gain:** seek-requiring formats become openable from a pipe when the limit
  allows it.
- **Not a breaking change for the common path:** path sources, and stream sources whose
  members all read directly, are untouched.
- **Docs:** `review/docs-content/claims.md` **E-71** records that no page states the spill.
  Topic 8 should **not** wait — E-71's prose states today's behaviour minimally and is
  rewritten by this change.
- **Threat model:** untrusted bytes at a predictable path, spool-directory permissions, and
  cleanup after a hard kill need a pass. `docs/extracting.md` already documents
  `.archivey-tmp-*` leftovers, so there is a precedent to match.
- **Not scheduled.** Specs-first, following `seekable-gzip-and-block-writing`. `tasks.md`
  describes the implementation for when it is accepted; nothing is implemented here.
