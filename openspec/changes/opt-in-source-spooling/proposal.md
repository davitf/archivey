# Opt-in source spooling, and a bound on the spool that already happens

## Why

Two different things get called "writing a temp file", and archivey currently gets one of
them wrong by doing it and the other wrong by refusing it.

**The tool-tax spool — happening today, unbounded, invisible.** `unrar` takes a filesystem
path, not a Python object (ADR 0002: native RAR metadata, external binary for data). So
`RarReader._ensure_archive_path()` writes the **whole archive** to
`tempfile.mkstemp(suffix=".rar")` the first time a member cannot be read directly, and
`_materialize_stream_volumes()` does the same for multi-volume stream sources. Measured on
a `rar -m5` archive read from a `BytesIO`:

```
member: big.txt  size: 300000        read from stream: 300000 bytes, ok
_ensure_archive_path calls: 1        -> /tmp/tmpqee8ey8z.rar   (whole archive)
reader.diagnostics: []               reader.cost: notes=()   (identical to a path source)
```

No size limit, no configuration, no entry in either honesty channel. The caller cannot
have known. This is `dev-docs/open-issues.md` **P11**, and it is the reason this change
exists rather than a nice-to-have alongside it.

The trigger is *per member*, which makes it worse: a stored member from a stream costs
nothing (`_can_direct_read`), and the next member in the same archive, compressed, costs a
full archive copy. Same call, same source, two very different costs, nothing distinguishing
them.

**The capability spool — refused today, and reasonably.** ZIP, 7z, RAR and ISO all declare
`required_source = SEEKABLE`, so a pipe is refused at open with `StreamNotSeekableError`
under both `streaming=False` and `streaming=True`. ADR 0010 decided that deliberately: *"A
convenience path that buffers a pipe into memory or a temp file to 'make ZIP work' hides
unbounded resource use and surprises callers who thought they were streaming."* That
reasoning is about **hidden** resource use, not about temp files, so an explicit, bounded
opt-in extends ADR 0010 rather than reversing it — and there is a real class of caller
(an archive arriving over HTTP, a socket, `stdin`) for whom "buffer it yourself first" is
the only answer archivey gives today.

## What Changes

**One policy object, two spool kinds, different defaults.** The kinds are different in
character and must not share a default:

| Kind | What it buys | Bounded by | Default |
|---|---|---|---|
| **Tool-tax** — a *seekable* source handed to a subprocess that only takes a path | nothing the caller did not already have; the source is fully addressable | archive size | **enabled**, because refusing it would break RAR-from-stream reads that work today |
| **Capability** — a *non-seekable* source made seekable | opens formats the source shape cannot otherwise serve | archive size | **disabled**, because enabling it silently is exactly ADR 0010's surprise |

The distinction is the load-bearing idea in this proposal. A tool-tax spool does not change
what the caller asked for — the bytes were already all addressable, and `unrar` merely
cannot accept a Python object. A capability spool overrides an explicit choice: a caller who
passed a pipe chose streaming, and spooling defeats that choice without saying so.

**Both become bounded and visible.** Whatever the kind, a spool that happens SHALL be
capped by an explicit byte limit and SHALL appear in `CostReceipt.notes`. That answers P11's
open question — the placement clause in `diagnostics` prefers a structured field where one
exists, and `notes` is that field; a diagnostic would be reporting the caller's own
configured choice back to them, which the admission clause rules out.

**Failure is at open where it can be, and typed where it cannot.** Exceeding the limit
raises `ResourceLimitError`, the same family as `ExtractionLimits` and `ListingLimits`.
Refusing a capability spool that was not enabled raises `StreamNotSeekableError`, as today,
with a message naming the option — turning the error into the documentation.

**Explicitly out of scope: caching decompressed member payloads.** Writing *decompressed*
data to disk to speed seeking within a member is a different feature — bounded by
uncompressed size rather than archive size, therefore in decompression-bomb territory, and
overlapping Topic 6 (decode-engine performance) and the parked `stream-layering` **Q4**
(`SlicingStream.readinto`). It needs measurement before design: specifying a cache without
knowing its hit rate is how a cache becomes permanent overhead. Recorded in
`dev-docs/IDEAS.md` rather than designed here.

## Specs

Deltas against three capabilities:

- **`access-mode-and-cost`** — ADDED: the spool policy, its two kinds, the byte limit, the
  `CostReceipt.notes` requirement, and the best-effort free-space pre-flight. MODIFIED: the
  non-seekable fail-fast requirement gains its "unless a capability spool is enabled" clause,
  so ADR 0010's rule stays stated rather than quietly outgrown.
- **`archive-reading`** — MODIFIED: `open_archive` accepts the policy through
  `ArchiveyConfig`, alongside `listing_limits`.
- **`format-rar`** — MODIFIED: the existing materialization is named as a tool-tax spool,
  subject to the limit and the cost note.

## Impact

- **Public surface:** one new config field and one new frozen dataclass on `ArchiveyConfig`,
  which already carries `extraction_limits` and `listing_limits`. Additive; the shape is
  worth settling pre-`0.2.0` even if the implementation lands after, because a config field
  is cheap to add later and expensive to reshape.
- **Behaviour change, pre-tag and deliberate:** a RAR-from-stream read that exceeds the
  default limit starts raising `ResourceLimitError` where it previously succeeded. That is
  the point — the unbounded case is the defect.
- **Not a breaking change for the common path:** path sources, seekable sources whose
  members read directly, and every format other than RAR are untouched.
- **Docs:** `review/docs-content/claims.md` **E-71** currently records that no page states
  the spill. When this lands, the page text changes from "RAR from a stream silently copies
  to disk" to a statement of the policy. Topic 8 should **not** wait for it — E-71's prose
  states today's behaviour minimally and is rewritten by this change.
- **Threat model:** untrusted bytes at a predictable path, spool-directory permissions, and
  cleanup after a hard kill need a pass. `docs/extracting.md` already documents
  `.archivey-tmp-*` leftovers, so there is a precedent to match rather than a new problem.
- **Not scheduled.** Specs-first, following `seekable-gzip-and-block-writing`. `tasks.md`
  describes the implementation for when it is accepted; nothing is implemented here.
