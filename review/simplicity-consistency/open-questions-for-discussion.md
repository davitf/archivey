# Archivey — open questions from the simplicity & consistency review

**Written to be shared and read standalone.** No prior context needed; everything you
need to form an opinion is inline. Dated 2026-08-07, against `main` @ `2792f9c`.

**Revision 2** — this document went out for comment and came back with two independent
reviews. Eight of the ten items are now settled and are marked **RESOLVED** in place, with
the argument that decided them kept so the reasoning survives; one item (**O2b**) grew a
new sub-question that nobody addressed and that is the actual blocker (**O2c**). If you
read revision 1, the changed answers are O1's threshold shape and O8's "should an empty
TAR raise" — both flipped, both for reasons worth reading.

If you only want to weigh in on one thing, read **O2c** — the concurrent-members
lifetime problem. It is the only item where no reviewer has yet offered an answer.

### State at a glance

| | Item | Status |
|---|---|---|
| O1 | Rewind diagnostic predicate | **Resolved** — cost-based, absolute threshold, record/escalate split |
| O2a | Warn on out-of-order solid `open()` | **Resolved** — no |
| O2b | Hold the decompressor open across `open()` | Direction agreed; blocked on O2c |
| **O2c** | **Reuse under concurrent members** | **Open — the real question** |
| O3 | Where to express "I want to seek" | **Resolved** — per-archive, keep both names |
| O4 | Shape of the pipe-readability field | **Resolved** — `required_source: StreamCapability` |
| O5 | Argument the backend can't act on | **Resolved** — split by intent |
| O6 | Testing RAR without committed binaries | Open — diagnosis redirected, see below |
| O7 | Reject bidi-override filenames? | **Resolved** — reject overrides, warn on marks |
| O8 | `strict_archive_eof` / empty TAR | **Resolved** — don't raise; tighten the knob |

---

## 30-second background

**Archivey** is a pure-Python library that reads ZIP / TAR / RAR / 7z / ISO /
directories / single-file compressed streams (`.gz`, `.xz`, `.bz2`, `.zst`, …) behind one
uniform interface. Its two load-bearing claims are *safe by default* and *memory-safe
parsing of hostile input*; its founding use case is indexing and deduplicating decades of
messy backups, which is why "never decompress the same byte twice without saying so" and
"hashes without decompression where possible" are stated priorities.

It is heading for its first public release, **`0.2.0`**. Nothing is on real PyPI yet, so
**behaviour changes are still free** — "that would be breaking" is not an argument in any
question below. What *is* costly is anything that freezes into the public API at the tag.

A review walked every caller-visible operation across 24 formats by running code (not
reading it), and produced 20 findings. **Sixteen questions were asked and answered**, and
this document carried the remainder. After two rounds of outside comment, **eight of those
ten are now resolved too** — leaving O2c (new, and the only item with no answer proposed
by anyone) and O6 (open, with its diagnosis redirected). The resolved items are kept in
place with their reasoning, because several of them are the kind that get relitigated.

Two useful conventions from the project, referenced below:

- **Diagnostics** are structured, queryable advisory events (`reader.diagnostics`), each
  with a `DiagnosticCode`. A `DiagnosticPolicy` can escalate any of them to a raised
  error. They are the project's preferred alternative to log warnings, because
  *"a logging warning most applications never see is a surprise deferred, not avoided."*
- **`CostReceipt`** is an object every opened archive exposes describing static costs:
  is listing indexed or does it need a scan, is member access `DIRECT` or `SOLID`, is the
  source seekable.

---

## Framing: who actually uses this, and why it changes the answers

Several questions below turn on an unstated assumption about *who is on the other end*.
Worth making explicit, because "add a diagnostic" is a good answer for one kind of caller
and a non-answer for another. This is a sketch, not research — argue with it.

| Caller | What they do | Do they read `reader.diagnostics`? |
|---|---|---|
| **Batch indexer / dedupe** — the founding use case | Long-running, unattended, over heterogeneous and often damaged input. Opens thousands of archives, hashes members, does not want one bad file to stop the run. | **Yes, programmatically.** This is the caller who sets a `DiagnosticPolicy`, inspects reports per archive, and logs an audit trail. |
| **One-off script / notebook** | "Extract this thing." Runs once, a human is watching. | **No, never.** They see exceptions and printed output. |
| **Server / pipeline over untrusted uploads** | Cares about the safety guarantees and resource limits; wraps calls in try/except. | **Sometimes** — as an audit trail, usually after the fact. |
| **CLI users** | `archivey list \| test \| extract`. The wedge, and the maintainer's own tool. | **N/A** — never touch the API. |
| **Library integrators** (fsspec adapter, data tooling) | Map archivey onto another abstraction, so they hit *every* format through one code path. | **Rarely** — but they are the ones most hurt by per-format divergence, because their code cannot special-case. |

Three consequences that recur below:

1. **Diagnostics reach group 1, and essentially nobody else.** So a diagnostic is the
   right channel for something a *batch* caller would act on — integrity, damage, cost,
   an audit trail — and the wrong channel for something a *one-off* caller needs to
   notice. That second category needs an exception, a safe default, or a docstring.
   This is the core of **O1** and **O2a**.
2. **Groups 1 and 2 want opposite things from argument validation.** The batch caller
   passes one configuration across heterogeneous input and wants it to apply where it
   can ("here are the twenty passwords we know"). The one-off caller wants a typo to
   fail loudly. That tension is exactly **O5**, and it is why "split by intent" —
   assertions refuse, offered resources permit — was chosen over picking one globally.
   This framing did the deciding, which is the argument for keeping it written down.
3. **Only groups 1 and 3 ever notice performance** — but they are the target audience,
   and they are the ones for whom a 4.5× cost cliff between two solid formats
   (**O2b**) or a silent quadratic seek (**O1**) actually matters.

A fourth, which cuts against several "just add data" answers: **groups 2, 4 and 5 are
probably the majority of users, and none of them will ever look at a diagnostic.** If a
behaviour matters to them, it has to be a default, an error, or a docstring — not a
queryable field.

---

## O1 — When should the library tell you a backward seek is going to be slow? *(biggest)*

### What's true today, measured

Some compressed formats can seek backwards cheaply (they carry an index); others must
re-decompress from the very beginning of the stream. The library emits a diagnostic,
`STREAM_REWIND_REDECOMPRESSES`, the first time a backward seek is going to be expensive.

**The predicate is the codec's identity, decided once when the file is opened.** xz, lzip
and unix-compress are treated as "has an index, never warn" — because those *formats* can
carry one.

But a format that *can* carry an index doesn't always *have* a useful one. Measured:

```
single-block .xz, 1 MB of incompressible data
  seek points in the stream : [(0, 0)]     ← one, just the origin
  rewind warning configured : None
  seek(end → offset 10)     : no diagnostic emitted
```

A single block is the **common** case, not a contrived one — Python's `lzma.compress()`
produces one, and so does the `xz` command-line tool without threading. The same shape
applies to lzip with one member, and `.Z` files with no reset codes.

So: that seek re-decompressed a megabyte from byte zero, and the library said nothing.

### Why it matters more than "a missing message"

The diagnostic's *real* job isn't informing. To reach it at all you must have opted into
seekable member streams, and that option is documented — so a passive advisory mostly
tells you something you already knew. Nobody polls a diagnostics list to discover their
seeks were slow; they discover it because it *was* slow.

Its real job is the **tripwire**: you can set a policy that turns this diagnostic into a
raised error, so a batch job aborts instead of silently going quadratic. For a library
whose founding use case is "index decades of backups," that's a genuinely useful guard.

**And today that guard is unreliable.** Arm it, and you're protected on `.lzma` and
un-accelerated `.bz2` — and silent on a single-block `.xz` that re-decodes the whole
stream on every backward seek. It fails exactly where you'd depend on it.

### The fix that seems obviously right

The library already computes the honest quantity. Each decompressor stream keeps a table
of seek points and can find the nearest one before any target. So the real cost of a
backward seek is one expression:

```
redecode_cost = target_offset − nearest_seek_point_before(target).offset
```

- No index at all → only the origin point exists → cost = the whole prefix. Loud. ✅
- **Single-block xz / one-member lzip** → *also* only the origin point → cost = whole
  prefix. **Loud, where today it is silent.** ✅
- Multi-block xz, multi-member lzip → cost = distance into the current block. Bounded,
  stays quiet. ✅
- Accelerated gzip (via `rapidgzip`) → index lives inside the accelerator, not in that
  table; stays a separate code path, as today.

One rule, no per-codec taxonomy. There's also precedent: the gzip path *already* uses a
size threshold rather than a codec rule, staying quiet below a floor because
"the rewind is cheap enough that warning is noise."

### RESOLVED — cost-based predicate, absolute threshold, record/escalate split

Both reviews and this review converged on the cost-based predicate above. The two
sub-choices resolved as follows.

**1. Threshold shape: absolute bytes, not relative.** Revision 1 leaned relative
("you re-decoded more than the distance you jumped"), on the grounds that it captures
*disproportionate* work. That is wrong on the case that matters most, and the
counterexample is decisive:

> A 1 GB single-block `.xz` stream. Seek from the end back to offset 900 MB. The nearest
> seek point is the origin, so the redecode cost is 900 MB — enormous. But the *jump
> distance* is only ~100 MB, so the relative ratio is ~0.11× — **well under any sane
> relative threshold, and the tripwire stays silent.** The relative form goes quietest
> exactly where the absolute cost is highest.

Relative measures inefficiency; the caller cares about *wall time*, which tracks bytes
re-decoded. It also matches the existing gzip precedent already in the codebase, so
there is one threshold vocabulary rather than two.

**2. "At most once per stream" — split recording from escalation.** This was the useful
new idea and it dissolves the flooding-versus-tripwire tension rather than trading it off:

- **Record** the diagnostic at most once per stream, as today. Bounded output, the
  audit trail stays readable, no behaviour change for the passive reader.
- **Evaluate the policy on every qualifying seek.** If a policy escalates the code to
  `RAISE`, the *second* expensive seek raises too — the guard does not disarm itself
  after firing once.

The two jobs of this diagnostic are different jobs and were only ever coupled by
implementation convenience. Deduplication is a presentation concern for the report;
escalation is a control-flow concern for the caller who explicitly asked to be stopped.

**3. `rapidgzip` index spacing — still empirical.** Nobody knows whether the accelerator
exposes its index granularity. This needs a measurement, not a decision: check the API,
and if the spacing isn't retrievable, that path keeps the current accelerator-presence
rule and the specification has to say the predicate is not uniform across codecs. Flagged
as work, not as an open question.

Any answer needs a spec change (the current text explicitly says xz/lzip/unix-compress
"SHALL NOT emit this event") plus the code change. Tests pinning today's blind spot are
already committed.

---

## O2 — Solid-archive re-decompression: the cost, and whether to warn

Two questions here. The first was the one originally asked; the second is bigger, was
raised in review of this document, and is **measured below** — the numbers move it from
"maybe someday" to a live cross-backend inconsistency.

### O2a — Should out-of-order `open()` on a solid archive warn?

**Background.** In a *solid* archive (7z, RAR, any compressed TAR), members share one
compression stream, so reaching member N may mean decompressing everything before it.

The library used to have a specification clause promising a warning here. **No code ever
implemented it**, and it was removed. During that removal the maintainer wrote down a new
rule to justify the removal and explicitly left one sub-question open: *should we emit a
plain Python `warnings.warn` instead?*

**Status:** still open. Verified: there is no `warnings.warn` anywhere in the library, and
the current specification says the opposite — "no diagnostic, no warning — discoverable
via `reader.cost.access_cost` and the `open()` docstring."

### RESOLVED — no ambient warning; record the decision

Unanimous across both reviews. This case has a *better* signal than the seek case in O1:
`cost.access_cost == SOLID` is right there in the cost receipt at open, before you do
anything. If the rewind — which has no open-time signal at all — doesn't warrant an
ambient warning, this one certainly doesn't. A `warnings.warn` would also be the library's
first, against a project rule that prefers structured diagnostics precisely because
"a logging warning most applications never see is a surprise deferred, not avoided."

**The deliverable is the written record, not the behaviour** — the behaviour is already
correct. This has now been rediscovered by two separate reviews and will be again until
the spec says "deliberately no warning here, and here is why."

### O2b — Should the reader hold the decompressor open across `open()` calls? *(new)*

**The framing "member 50 then member 10" is misleading**, and the measurements show why:
for 7z, **the order does not matter at all.**

Setup: 8 members × 200 KB of incompressible data, so compressed size ≈ uncompressed and
re-decode cost is directly visible. Counting bytes read from the compressed source.

**Solid 7z** (`-ms=on`, compressed 1,600,339 bytes):

| Access pattern | Compressed bytes read | vs. one pass |
|---|---:|---:|
| `stream_members()` — one forward pass | 1,608,739 | **1.0×** |
| `read()` of only the **last** member | 1,608,739 | **1.0×** |
| `read()` of all 8 **in archive order** | 7,236,643 | **4.5×** |
| `read()` of all 8 **in reverse order** | 7,236,643 | **4.5×** |

In-order and reverse-order are **identical**. The cost is not about ordering — every
random `open()` on a solid folder restarts the decode from the folder start and stops at
the target. For N members that is `1+2+…+N` over `N`, i.e. **(N+1)/2 × one pass**,
whatever order you use. Here (8+1)/2 = 4.5, exactly the measured ratio.

**Compressed TAR** (`.tar.gz`, same payload) behaves **differently**:

| Access pattern | vs. one pass |
|---|---:|
| `stream_members()` — one forward pass | 1.0× |
| `read()` of all 8 **in archive order** | **1.0×** |
| `read()` of all 8 **in reverse order** | 2.4× |

So on `.tar.gz` the reader *already* reuses decompression state for forward progress —
in-order random `open()` is free. On 7z it does not.

**That is the finding.** Two solid formats, one uniform interface, and the cost model for
the identical caller code differs by 4.5×. It is not a hypothetical optimization: one
backend already demonstrates the behaviour the other lacks.

**Direction agreed, three points settled, one blocker remaining:**

1. **Yes in principle** — the 7z reader should hold its folder decoder open across
   `open()` calls, so in-order random access costs one pass, as `.tar.gz` already does.
   Nobody defended the status quo. The payoff is a 4.5× cost cliff on the founding use
   case (walk an archive, hash every member), and it closes a cross-backend inconsistency
   rather than inventing something new.
2. **Scope it to forward reuse only.** Keep at most the *one* decoder positioned at or
   before the requested member, and reuse it only when the target is at or ahead of the
   current position; otherwise discard and restart. That captures the entire 4.5× → 1.0×
   win — which is a *forward*-progress win — without a cache, an eviction policy, or a
   memory budget. Backward access stays exactly as expensive as today, which is honest:
   it genuinely is expensive.
3. **Not tag-gated, so timing is free.** Nothing here is a public-API change, so this
   need not land before `0.2.0`. It does not foreclose anything either way.
4. **It does not change O2a.** Even with forward reuse, out-of-order access stays
   expensive, and the argument against warning — the cost receipt already told you at
   open — is unaffected.

**What blocks it is not the above.** It is the lifetime question under concurrent
members, which neither review addressed. That is O2c.

**Caveats on the numbers.** The 7z archive was written with `-mx1` by the system `7z`;
solid-block layout varies with settings, and a multi-folder archive would show a smaller
ratio. The `.tar.gz` one-pass baseline itself reads ~2.65× the compressed file, which is
unexplained and was not chased — the *relative* comparisons above are the reliable part.
Ratios are stated against each format's own one-pass cost for that reason. RAR was not
measured (see O6 — the test corpus cannot build RAR here).

### O2c — What does holding the decompressor open mean when members are concurrent? *(open — nobody has answered this)*

**This is the actual blocker on O2b, and it is why the optimization has been deliberately
deferred rather than just not-yet-done.** Both external reviews discussed O2b's payoff and
neither engaged with this; one explicitly deferred to the maintainer on it. It is stated
here as its own question so it can be brainstormed on its own terms.

**The easy case, for contrast.** With **one open member at a time**, "hold the decoder
open" is straightforward: there is exactly one underlying decompression stream, it has one
position, and the rule is the forward-reuse rule in O2b(2) — reuse if the target is ahead,
discard and restart otherwise. One object, one lifetime, one decision point.

**The hard case.** The library also supports **concurrent members** — several member
streams open and readable at once, on backends that declare the capability. Now the
question "which decoder do we keep, and for how long?" has no obvious answer:

- **When N member streams are open concurrently, are there N underlying decoders?** If
  each concurrent member needs its own decode position in a shared solid stream, holding
  them open means holding N decompressor states — each with its own window/dictionary
  memory. On a solid 7z with a large dictionary that is not a small number.
- **When those N streams close, which of the N do we keep?** All of them, so the next
  `open()` can pick the closest preceding one? Only one — and if so, which? The
  furthest-advanced? The most recently used? Each choice is a cache policy, with an
  eviction rule and a memory budget, which is exactly the complexity O2b(2) was scoped to
  avoid.
- **Does "pick the closest preceding decoder" beat "restart"?** It is the obviously
  appealing rule, but it needs the same `nearest-point-before` reasoning as O1, plus a
  tie-break, plus a rule for what to do when the closest one is *behind* but only barely.
- **What is the interaction with the single-live-stream rule?** Backends that do *not*
  declare concurrency enforce one live stream at a time. Does forward reuse mean the
  decoder outlives the member stream that created it — and if so, what owns it, and what
  does `close()` on the archive have to tear down?

### The cheap check, run — and it comes back "no"

The hope was that this might be a non-issue: **do the backends that declare
`concurrent_members` already materialize member data rather than keeping N live decoders?**
If so the two mechanisms never interact and O2b could be specified against the
single-live-stream path alone. **Measured, and they do not.**

On a 6-member single-folder solid 7z (200 KB per member, 1.2 MB payload,
`solid_block_count=1`), under `open_archive(concurrent_members=True)`:

| | |
|---|---|
| Open members 1 and 4 simultaneously, read both | **succeeds** |
| `IoStats.bytes_decompressed` for those two reads | **1 400 000** |
| Expected if each decodes independently from the folder start | 400 000 + 1 000 000 = **1 400 000** |
| Same second `open()` **without** `concurrent_members` | `ConcurrentAccessError` |

So two members of the *same* solid folder hold **two independent live decode pipelines**,
each decoding from the folder's start, at the same time. The CONCURRENT fan-out in this
library is over the **listing snapshot** ("first-touch materialization" in
`reader_state.py` is the member list, not member data) — member reads are not materialized.

The code says the same thing: `_open_member` calls `_open_folder_stream` →
`open_folder_pipeline` unconditionally on every call
(`src/archivey/internal/backends/sevenzip_reader.py:535`), and the comment at `:554`
already acknowledges it — *"each from-start folder decode counts."*

**So the bullets above are the agenda, not a formality.** N concurrent opens on one solid
folder means N live LZMA states, each carrying its own dictionary; "hold the decoder open"
has to answer what happens to all N when they close. RAR solid blocks are the same
question and are unmeasured (the corpus cannot build RAR here — see O6).

**Registered as backlog**, with this context, in `dev-docs/IDEAS.md` §Performance &
robustness, next to the existing "Parallel extraction / concurrent member streams" entry.
Promote by writing an `openspec` change.

---

## O3 — Where should "I want to seek inside members" be expressed at all?

This started as a naming question and is really a placement question. **Nothing here is
locked** — every argument name in the library is still changeable before the tag, so the
options are not limited to the two below, and a third name that reads well in both
contexts is fair game.

### The observation

Two entry points express the same idea with different spellings:

```python
open_archive(path, seekable_members=True)   # archives
open_stream(path, seekable=True)            # a single compressed stream
```

The specification currently **mandates** exactly this:

> `open_stream` SHALL keep its `seekable: bool` parameter, and both entry points SHALL use
> the same `seekable` vocabulary for the same concept; concurrency has no meaning for a
> single standalone stream, so `open_stream` MUST NOT gain a concurrency parameter.

So any change starts by changing that requirement. The defence for today's spelling:
`seekable_members` names *what it applies to*, `open_stream` has only one stream so
`seekable` is unambiguous, and both spell the capability `seekable`.

### The bigger question: is `open_archive` even the right place?

Today seekability is declared **per archive**. The alternative is per member:

```python
archive.open(member, seekable=True)
```

**A caller's actual need is usually per-member** — seek around inside one big member,
stream the rest — and today that forces the whole archive into the seekable mode.

**But the specification explicitly forecloses this**, in the same requirement:

> Capabilities are per-archive intent only — no `ArchiveyConfig` equivalent, **no
> per-`open()` flag**.

And there is a real technical reason, which a separate finding in this review made
concrete: **declaring seekability changes what the backend does at open time** — it drives
whether a seek index gets built and whether an accelerator is selected. A per-`open()`
flag would mean either building the index lazily on first seekable open (new state and
lifetime questions), or building it always (paying for callers who never seek).

That same finding — the flag also silently changing which *metadata* you get back — is
already decided and being fixed. Worth separating: **the bad thing about
`seekable_members` was never its name.**

### RESOLVED — keep per-archive, keep both names, and note that this decision has no deadline

Both reviews agreed on all three parts.

**1. Placement stays per-archive.** The index-lifetime argument above is decisive on its
own: declaring seekability drives open-time work, and a per-`open()` flag would mean
either building the index lazily on first seekable open — new state, new lifetime
questions, and the same problem O2c is stuck on — or building it always, taxing callers
who never seek.

**2. The decisive point, which removes the deadline entirely:** adding
`archive.open(member, seekable=True)` later is a **purely additive** API change. A new
keyword argument with a default that preserves today's behaviour breaks nothing. So
per-archive-now forecloses per-member-later; it is a decision that can be revisited with
real usage data instead of guessed at now. **This is also the answer to the separately
tracked question of whether the capability vocabulary must be finalized before `0.2.0`:
it must not.**

**3. Keep both spellings.** `seekable_members=` on `open_archive` (it names what it
applies to, among several members) and `seekable=` on `open_stream` (one stream, no
ambiguity). Both spell the capability `seekable`, which was the actual consistency
requirement. No third name was proposed that read better in both contexts, and the
review's own conclusion stands: **the bad thing about `seekable_members` was never its
name** — it was the metadata divergence, which is separately decided and being fixed.

The specification's current wording therefore stays as-is, but for the reason above rather
than by default.

---

## O4 — What shape should the new "can this format read from a pipe?" field take? *(deadline)*

**Decided:** the library will expose, as data, whether a given format can be read from a
non-seekable source (a pipe or socket).

**Why:** today the behaviour is good — TAR and single-file codecs stream from a pipe;
ZIP, ISO, 7z and RAR refuse with one clear, consistent error explaining that their index
isn't at the front of the stream. But that split isn't *queryable*: a caller writing
"pipe it if you can, otherwise buffer to disk" has to try it and catch the exception. The
project's own rule is that behaviour differences between formats should be surfaced as
data, never discovered by trial.

**What's not decided is the shape**, and this is the item with a real deadline —
`FormatAvailability` is a public frozen dataclass whose field set freezes at `0.2.0`.
Adding a field later is technically additive, but the shape question gets much harder
once callers are pattern-matching the object.

Options considered:

- A single boolean (`streams_from_pipe: bool`)? Simple, but boolean fields age badly when
  a third source shape appears.
- A set/collection of supported source shapes? More future-proof, more to specify.
- Reuse the existing `StreamCapability` vocabulary (`SEEKABLE` / `FORWARD_ONLY`), which
  the cost receipt already uses for the source-shape axis.

### RESOLVED — `required_source: StreamCapability`, read as a minimum requirement

Both reviews landed on reusing `StreamCapability`, and one sharpened it into the framing
that makes it work:

```python
required_source: StreamCapability   # the *weakest* source shape this format can read from
```

`StreamCapability` is **ordered** — `FORWARD_ONLY` is weaker than `SEEKABLE` — so the
field is a minimum requirement, and the caller's test is a comparison, not a lookup table:

| Format | `required_source` | Reading |
|---|---|---|
| TAR, single-file codecs | `FORWARD_ONLY` | a pipe is enough |
| ZIP, ISO, 7z, RAR | `SEEKABLE` | needs to seek to a trailing index |

Why this beats the set-of-shapes option: a set can express nonsense (`{SEEKABLE}` but not
`FORWARD_ONLY` is fine; `{FORWARD_ONLY}` but not `SEEKABLE` is not a real format), whereas
an ordered minimum can only express the real thing. And it beats the boolean because when
a third source shape appears it is a new enum member, not a second boolean that has to be
kept consistent with the first.

It also avoids inventing a second vocabulary for a concept the cost receipt already names
on its source-shape axis — a caller can compare `format.required_source` against
`reader.cost.stream_capability` directly, because they are the same type.

**Deadline note:** this is the one item that genuinely must land before the `0.2.0` tag,
because `FormatAvailability` is a public frozen dataclass.

---

## O5 — Three arguments, three behaviours. Which one is the model?

There's a recurring shape: **a caller passes an explicit argument, and the backend can't
act on it.** The library currently does three different things.

| Argument | Behaviour when the backend can't use it |
|---|---|
| `encoding=` | **Silently ignored** by 7z, RAR, ISO, directory, single-file (honoured by ZIP and TAR) |
| `format=` when wrong but plausible | Usually raises — but see O8 for the case where it silently succeeds on wrong data |
| `password=` | **Raises** `UnsupportedOperationError` |

A previous fix established a principle for one case — `format=ZIP` on a directory path now
raises rather than silently reading the directory — reasoning that *"silently overruling it
returns a reader over the directory tree to a caller who asserted a different format."*
That principle was never generalised.

The review recommended generalising it (refuse anything the backend can't act on). **The
maintainer chose the softer option for `encoding=`**: emit a diagnostic, keep the entry
point permissive.

### The counter-proposal: maybe `password=` is the one that should change

`password=` is **already best-effort by design.** You can pass a whole list of candidates,
and the library tries them in order per encrypted unit, keeping the ones that work. That is
built for exactly the batch shape this library exists to serve: *"here are the twenty
passwords we know about — open whatever you can."*

Under that reading, raising because one archive in a batch happens to be a plain `.tar` is
the wrong behaviour. The caller isn't asserting "this archive is encrypted"; they're
supplying a keyring.

**And the library already half-agrees with that — measured:**

```
A plain .tar (no encryption at all):
  open_archive(tar, password="hunter2")            -> UnsupportedOperationError
  open_archive(tar, password=["a", "b"])           -> UnsupportedOperationError
  open_archive(tar, password=lambda req: "hunter2") -> opens fine
```

A *provider callable* is accepted and simply never consulted; a static list is refused.
The permissive behaviour already exists — it's just reachable only by wrapping your
password list in a lambda, which no one would guess.

So the asymmetry isn't only across arguments; it's **inside `password=` itself**.

### RESOLVED — split by intent, and `password=` becomes permissive in all its forms

Unanimous across both reviews and this one. The model is:

> **Refuse when the argument is an assertion about *this* archive. Permit — and record a
> diagnostic — when it is a resource offered for use if needed.**

Applied to all three:

| Argument | Intent | Behaviour |
|---|---|---|
| `format=` | **Assertion** — "I claim this is a ZIP" | **Refuse** when it can't hold. Already the established behaviour; the directory-path fix generalises rather than being a special case. |
| `password=` | **Resource** — a keyring | **Permit** in *every* form. On an archive with no encryption, a static string, a list, and a provider callable all behave the same: accepted, never consulted, diagnostic recorded. |
| `encoding=` | **Resource** — a hint for name decoding | **Permit**, diagnostic when the backend can't act on it. (Already the maintainer's decision; this now has a principle behind it rather than being a lone softening.) |

The measured asymmetry *inside* `password=` — static list refuses, provider callable
opens fine — was the strongest evidence for this line. The permissive behaviour already
exists in the library; it is just reachable only by wrapping your password list in a
lambda, which nobody would guess. Making all three forms permissive removes an
inconsistency rather than adding a permission.

**What is given up, stated plainly:** the caller who passes `password=` to an archive
that has no encryption no longer gets an immediate error. That is the batch caller's
whole point, and the diagnostic is there for anyone who wants to check. The typo case
this used to catch is narrow — a *wrong* password on an *encrypted* archive still fails
loudly, which is the case that actually matters.

**Not covered by this principle:** arguments that are neither assertions nor resources.
None were found in this review, but the rule should be written as a rule, so the next
argument's category is a question with an answer rather than a fresh debate.

---

## O6 — How do we test RAR, given we deliberately don't commit binaries?

**Situation.** The library has a cross-format conformance sweep: a declarative corpus of
archive shapes, built on demand in every format, asserting that every backend opens,
lists, reads and extracts them identically. It is described as the regression net that
catches "backend X broke shape Y" without a hand-written test per pair.

**The 41 RAR cases of that sweep run on no CI leg and in no developer environment.**
Building RAR test files needs the proprietary RARLAB `rar` writer. CI installs only
`unrar` (the reader) — and on macOS it installs the bundle then actively *deletes* the
writer, with the comment: *"the `rar` writer enables corpus RAR builds whose digest
expectations are Linux-fixture-oriented; keep writer off the PATH here."*

So this is a deliberate, documented trade-off, not an accident.

**What is still covered:** RAR *reading* is exercised by committed fixture files —
open, list, hashes, encrypted headers, RAR3 and RAR5, solid and non-solid. No
RAR-specific problem showed up on any of those.

**What isn't:** the RAR column of the declarative corpus.

**Decided:** close the hole — make the sweep runnable on at least one CI leg.
**Not decided: how**, and the two routes pull against different project values:

- Make the RAR fixtures' digest expectations platform-independent. Keeps the "no
  committed binaries" property the corpus was designed around. More work, and the
  platform-dependence needs diagnosing first.
- Commit a small pre-built RAR fixture set. Straightforward, but the corpus deliberately
  generates everything on demand precisely to avoid committed archives.

Extra context: `0.2.0` headlines a native RAR reader, which is what makes this worth
resolving now rather than after.

**Diagnosis update — one hypothesis is already ruled out.** A reviewer proposed that the
platform-dependence comes from the corpus asserting exact *payload digests*, which would
differ if a writer normalized content. It does not: the corpus asserts `act.size ==
len(exp.contents)` and checks digest **key presence**, not digest values. So the
platform-dependence is not in the payload.

That redirects the diagnosis to **metadata** the `rar` writer records differently per
platform — most likely candidates, in order: **mode bits** (umask, and the executable bit
on macOS vs Linux), **uid/gid**, and **mtime** granularity. Whoever picks this up should
start by diffing the recorded member metadata of the same corpus entry built on both
platforms, rather than re-examining content hashing.

---

## O7 — Should a filename with a right-to-left override be *rejected*, not just flagged?

**Background.** Unicode bidirectional control characters can make a filename *display*
differently from how it's stored — the classic trick turns `evil‮gnp.exe` into something
that looks like a `.png`. It's a real social-engineering vector for anything that shows
users a file listing.

**Today** the library logs a plain warning and presents the name. That warning is the
**only** advisory in the library with no structured diagnostic code — every other one
(name normalization, inferred encoding, unverifiable digest, degraded index, …) is
queryable and escalatable. Its immediate neighbour in the same code path, name
normalization, *does* have one.

**Already decided:** give it a diagnostic code like the others, so it's queryable and a
policy can escalate it.

**The question was:** the specification currently says the member is *"rejected **or**
exactly one warning is emitted"* — permitting either, which is why nobody noticed the code
only does one of them. Once we're editing that clause anyway, should the answer be "warn",
or should bidi controls in names be *rejected* by the safe-extraction path the way
path-traversal and null bytes already are?

### RESOLVED — reject the *overrides* during safe extraction; keep the *marks* warn-only

Both reviews converged on the distinction that makes this answerable, and it is the one
the original framing missed: **bidi controls are not one category.**

- **Overrides and isolates** — U+202A–202E (LRE/RLE/PDF/LRO/RLO) and U+2066–2069
  (LRI/RLI/FSI/PDI) — change the *rendered order of surrounding text*. They have no
  legitimate reason to appear in a filename; the `.gnp.exe` trick needs one of them.
  **Reject these** in the safe-extraction path, alongside path traversal and null bytes.
- **Directional marks** — U+061C (Arabic letter mark), U+200E (LRM), U+200F (RLM) — are
  invisible hints that do *not* reorder surrounding text, and **do occur in legitimate
  Arabic and Hebrew filenames**. **Keep these warn-only** with the new diagnostic code.

This dissolves the "rejecting breaks legitimate RTL filenames" objection, because
**RTL script is not a bidi control at all**: an Arabic or Hebrew filename contains Arabic
or Hebrew *letters*, whose direction comes from the characters' own properties. Nothing in
`فهرس.txt` is in either list above. Rejecting overrides costs legitimate RTL users
nothing.

**A correction to the reviewers' proposal, from reading the code.** One review proposed
rejecting "the bidi control set" and listed exactly U+202A–202E and U+2066–2069. The
library's actual `_BIDI_CONTROLS` (`src/archivey/internal/naming.py:32`) is **broader** —
it also contains U+061C, U+200E and U+200F. So "reject everything currently warned about"
would sweep in the three marks and *would* break legitimate RTL filenames. The reject set
must be defined explicitly as the two override/isolate ranges, not as "the existing set."

**Scope, to be explicit:** rejection belongs to the *safe-extraction* path, which is where
a name becomes a filesystem path. Listing and reading still present the name as stored,
with the diagnostic — the library does not get to decide that a name is unreadable.

---

## O8 — What does `strict_archive_eof` actually assert, and should an empty TAR raise?

Originally filed as a small oddity. Two questions raised in review turned it into
something with a wider blast radius, and both are now measured.

### What `strict_archive_eof` actually checks — precisely

A TAR archive ends with two 512-byte all-zero blocks (writers often pad further, to
10 KiB). The library's check, in full:

1. `tarfile` reads until it hits a null block and stops there, having consumed the
   **first** trailer block.
2. Archivey then reads the **next 512 bytes** and requires them to be 512 nulls.
   - 512 nulls → **accept, and stop looking.**
   - a full non-null block → `CorruptionError`, regardless of `strict_archive_eof`.
   - short or empty read → advisory diagnostic; **`strict_archive_eof=True` turns this,
     and only this, into `TruncatedError`.**

So `strict_archive_eof` asserts exactly one thing: **"the two-block null trailer is
present and complete."** It answers the specific question *"was this file truncated at a
member boundary?"* — that shape is byte-identical to a legitimately-ended archive, which
is why the knob exists.

**It never looks past block 2.** Measured, and this is the part worth knowing:

| Input | `strict=False` | `strict=True` |
|---|---|---|
| Legitimately empty tar (`tarfile`-written) | 0 members, no diagnostic | **same** |
| 1 KiB of zeros | 0 members, no diagnostic | **same** |
| 32 KiB of zeros (an ISO's system area) | 0 members, no diagnostic | **same** |
| Valid tar, 1 member | 1 member | 1 member |
| Valid tar **+ 4 KiB of trailing junk** | 1 member, no diagnostic | **1 member, no diagnostic** |
| Valid tar + 4 KiB of trailing zeros | 1 member, no diagnostic | **same** |

**So: yes, it accepts any archive whose two trailer blocks are present and ignores
everything after them — including 4 KiB of arbitrary junk, under `strict`.** It does not
check that the file ends there.

**Should it already be firing on the ISO case? Under its current definition, no** — the
trailer is genuinely present. Under a reading of its *documented promise* ("set this when
you need a provably complete listing"), arguably yes: the file continues for another
31 KiB and the reader silently ignored all of it. That gap between the definition and the
promise is the finding.

### The three-layer version of the wrong-format problem

Reviewing this surfaced that the original `format=TAR`-on-an-ISO case was the *least*
realistic of three layers. Measured, on a file of 32 KiB of zeros:

| How the format is chosen | Result |
|---|---|
| **Content detection** (no extension) | `FormatDetectionError` — refuses. ✅ |
| **Extension fallback** (file named `z.tar`) | **Opens as TAR. 0 members. No error, no diagnostic.** ⚠️ |
| **Explicit `format=TAR`** | Opens as TAR. 0 members. No error, no diagnostic. ⚠️ |

The middle row is the realistic one, and it was not previously identified. A zero-filled
or zero-truncated file with a `.tar` extension is exactly the shape the project's founding
use case is full of — "old downloads with wrong extensions, truncated files, archives
produced by buggy tools." Content detection correctly refuses it; the extension path
doesn't.

**Already decided** for the explicit-`format=` layer: emit a diagnostic when an explicit
`format=` yields an empty listing and detection would have said otherwise. That decision
does **not** cover the extension-fallback layer, because there is no explicit `format=`
to compare against.

### RESOLVED (a) — a zero-member TAR must **not** raise

Revision 1 left this genuinely open and floated a middle option. **Both are dead, killed
by one measurement:**

> A legitimately empty tar, as written by `tarfile` or by
> `tar cf empty.tar --files-from /dev/null`, is **10240 bytes, every one of them zero.**
> It is **byte-identical** to a 10 KiB zero-filled garbage file. There is no predicate
> over the bytes that separates them, because there is no difference between them.

So "raise on zero members" would reject a file the stdlib accepts and that `tar(1)`
itself produces. And the proposed middle option — "raise when zero members **and** the
file continues past the trailer" — fails for the same reason: the trailer is at 1024
bytes and the legitimate empty tar continues to 10240, so the condition is true for the
*valid* file. The middle option would reject every empty tar in existence.

**Decided instead — two mechanisms, neither of which raises:**

1. **An `EMPTY_ARCHIVE` diagnostic**, format-independent, emitted whenever a listing
   completes with zero members. Cheap, honest, and it says the true thing ("this archive
   is empty") rather than a guess ("this file is probably garbage"). It reaches the batch
   caller, who is the one running over messy input at scale.
2. **After-the-fact detection for the extension-fallback layer**, which is the realistic
   one and which the already-decided explicit-`format=` diagnostic does not cover. When
   the format was chosen by *extension* and the result is an empty listing, run content
   detection on the bytes; if detection would have refused the file (as measured, it does
   refuse 32 KiB of zeros), emit a diagnostic saying so. This costs nothing in the common
   case because it only runs on an empty result, and it converts "silently opens as an
   empty TAR" into something queryable without making any valid file fail.

**Acknowledged gap, deliberately accepted:** neither mechanism reaches the one-off caller
(framing group 2), who does not read diagnostics. Per the framing section that would
require an exception or a default, and the measurement above says no correct exception
exists. The honest position is that a zero-filled `z.tar` opens as an empty archive, that
this is *correct* behaviour, and that a caller who needs more must either use content
detection (which refuses) or check `len(reader)`.

### RESOLVED (b) — `strict_archive_eof` should assert "nothing but zeros from the trailer to EOF"

Both reviews agreed, and it makes the knob mean what its documentation already promises
("a provably complete listing"). Precisely:

> With `strict_archive_eof=True`, after the two-block null trailer, every remaining byte
> to EOF MUST be zero. Any non-zero byte raises. With `strict_archive_eof=False`,
> behaviour is unchanged.

- **Zero padding still passes** — writers pad routinely to 10 KiB and beyond, and that is
  the overwhelmingly common case. This is why the rule is "nothing but zeros" rather than
  "EOF immediately."
- **The ISO case still passes**, and that is now a deliberate answer rather than an
  oversight: an ISO's 32 KiB system area is zeros, so under this rule it is a valid empty
  TAR with padding. The `EMPTY_ARCHIVE` diagnostic from (a) is what covers it.
- **The trailing-junk case now fails**, which is the whole point — 4 KiB of arbitrary
  bytes after the trailer means the file is not what the listing claims.
- **Concatenated archives now fail under `strict`.** Accepted: they *are* multiple
  archives, the reader only listed the first one, and a caller who asked for a provably
  complete listing should be told. The knob is opt-in and off by default, which is the
  argument for letting it mean the strong thing.

**Cost to note for implementation:** the check must read to EOF, so `strict_archive_eof`
becomes O(file size) on the tail rather than O(1). For a non-seekable source that is a
real scan. Worth a sentence in the docstring.

---

## Execution notes — decided, but these bite if missed

Not questions, but sequencing constraints that a reader of the decision list wouldn't
otherwise see:

1. **Six separate decisions now add diagnostic codes** — the ignored-`encoding=` one, the
   ignored-`password=` one (new, from O5), the wrong-`format=` one, the bidi one (O7),
   `EMPTY_ARCHIVE` (O8a), and the extension-fallback-versus-detection one (O8a). They
   share the taxonomy and the policy plumbing — **land them as one change, not six.**
   Revision 1 said three; O5 and O8 added the rest, which strengthens the point.
2. **Only one item is tag-gated:** O4's `required_source` field, because
   `FormatAvailability` is a public frozen dataclass. Everything else here — including
   O3, per the additivity argument — can land after `0.2.0` without cost. Sequence
   accordingly rather than treating the whole list as pre-release work.
3. **A drafted docstring paragraph must not ship before O1 is fixed.** It advertises that
   you can escalate the rewind diagnostic to an error — which is exactly the promise O1
   shows is currently unreliable. Ship the paragraph without that sentence, or ship it
   after.
4. **O1's record/escalate split touches shared machinery, not just this code.** "Record
   once, evaluate the policy every time" is a change to how a deduplicated diagnostic
   relates to its policy. Decide whether that is the rule for *all* once-per-stream
   diagnostics or a local exception, and write it down either way — otherwise the next
   deduplicated code inherits the question.
5. **O8b makes `strict_archive_eof` cost O(tail length)**, because it must read to EOF to
   prove there is nothing but zeros. Today it reads 512 bytes. On a non-seekable source
   that is a real scan of the remainder — document it on the flag.
6. **One committed test will flip when either side of a spec-vs-code disagreement is
   fixed.** A specification table claims the directory backend offers a cheap "peek" at
   its member list; the code returns nothing, consistent with its own cost receipt. The
   decision was to fix the *specification*. If that test starts passing before anyone
   edits the spec, it means someone changed the *code* instead — check which.
7. **A measurement in the review is a hand count, not a metric.** "Format-conditionals per
   documentation page" is useful as a direction (are we adding or removing caveats?), not
   as a threshold. Don't let it become a target.

---

## Explicitly settled — please don't reopen these

Recorded so discussion doesn't circle back. Each was checked against running code across
24 formats:

- **The uniform interface holds.** `len()`, membership tests, lookup of a missing name,
  opening a directory member, overlapping opens, seeking without the capability, close
  lifetime, and the whole streaming-mode enforcement block behave *identically on every
  measured backend*. Two independent review passes reached this separately.
- **Pipe refusals** are loud, typed and consistent (the only gap is queryability — O4).
- **Password laziness**: data encryption stays lazy; *header*-encrypted 7z/RAR necessarily
  need the password at open, because the listing itself is ciphertext. Format law. A
  documentation sentence that overstated this is being fixed.
- **Digest availability per format** matches each format's specification, including
  WinZip AES zeroing the CRC field.
- **Cost receipts** reproduce every specified example exactly.
- **The error-translation boundary** is clean inside itself; the three holes found were
  all *outside* it, and all are being fixed.
- **Exception hierarchy roots**, duplicate-name handling, extras naming, and the
  CLI-versus-library default split are all settled by prior decisions and were re-verified.

## Not examined at all

Stated so nobody assumes coverage: multi-volume archive joins beyond the entry-point
argument checks, free-threaded/concurrent execution paths, and salvage-mode reads of
damaged archives (a known, deliberate gap on the roadmap).

**One of those gaps is now load-bearing.** O2c turns on how concurrent members hold
decompression state, and the concurrency paths were never measured by this review.
Nothing in O2c is backed by a probe — unlike everything else in this document. The
"is it actually a non-issue?" check named there is the way to close that gap cheaply,
and it should happen before the design discussion, not during it.
