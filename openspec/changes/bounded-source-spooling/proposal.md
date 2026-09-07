# One spool limit, and a bound on the spool that already happens

## Why

`unrar` takes a filesystem path, not a Python object (ADR 0002: native RAR metadata,
external binary for data). So `RarReader._ensure_archive_path()` writes the **whole
archive** to `tempfile.mkstemp(suffix=".rar")` the first time a member cannot be read
directly, and `_materialize_stream_volumes()` does the same for multi-volume stream
sources. Measured on a `rar -m5` archive read from a `BytesIO`:

```
member: big.txt  size: 300000        read from stream: 300000 bytes, ok
_ensure_archive_path calls: 1        -> /tmp/tmpqee8ey8z.rar   (whole archive)
reader.diagnostics: []               reader.cost: notes=()   (identical to a path source)
```

No size limit, no configuration, no entry in either honesty channel. The caller cannot
have known. This is `dev-docs/open-issues.md` **P11**, and it is why this change exists
rather than being a nice-to-have alongside it.

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
spool_limit = <bytes>              # spool when needed, up to this much
spool_limit = SpoolLimit.UNLIMITED # never refuse on size
spool_limit = None                 # never spool
```

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

**Every spool appears in `CostReceipt.notes`.** That answers P11's open question: the
`diagnostics` admission clause covers what the caller could not determine from the declared
contract of the call, and a spool the caller bounded is declared; the placement clause
prefers a structured field where one exists, and `notes` is that field.

**Explicitly out of scope: caching decompressed member payloads.** Writing *decompressed*
data to disk to speed seeking is a caller-side concern best served by a wrapper stream
around a member — something archivey might ship or recommend later, but not this change and
not this layer. It is bounded by uncompressed size rather than archive size, therefore in
decompression-bomb territory, and it overlaps Topic 6 and the parked `stream-layering`
**Q4**. Recorded in `dev-docs/IDEAS.md` with that reasoning.

## Specs

- **`access-mode-and-cost`** — ADDED: the spool limit, its three settings, the
  `ResourceLimitError` on exceeding it, the `CostReceipt.notes` requirement, the
  best-effort pre-flight, and the rule that a spool happens at the first operation needing
  it rather than at open. MODIFIED: the non-seekable fail-fast requirement gains its
  "unless spooling is permitted" clause, so ADR 0010's rule stays stated rather than
  quietly outgrown.
- **`archive-reading`** — MODIFIED: the limit reaches the reader through `ArchiveyConfig`,
  beside `listing_limits`.
- **`format-rar`** — MODIFIED: the existing materialization becomes subject to the limit
  and to the cost note.

## Impact

- **Public surface:** one new field on `ArchiveyConfig` (plus a spool directory), which
  already carries `extraction_limits` and `listing_limits`. Additive; worth settling
  pre-`0.2.0` because a config field is cheap to add later and expensive to reshape.
- **Behaviour change, pre-tag and deliberate:** a RAR-from-stream read of an archive larger
  than the default limit starts raising `ResourceLimitError` where it previously succeeded.
  The unbounded case is the defect.
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
