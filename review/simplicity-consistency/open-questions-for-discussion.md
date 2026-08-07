# Archivey — open questions from the simplicity & consistency review

**Written to be shared and read standalone.** No prior context needed; everything you
need to form an opinion is inline. Dated 2026-08-07, against `main` @ `2792f9c`.

If you only want to weigh in on one thing: **O1** and **O2b** are the two with real
performance consequences, **O4** is the one with a deadline, and **O5** is the one where
picking a principle would settle three separate arguments at once.

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

A review just walked every caller-visible operation across 24 formats by running code
(not reading it), and produced 19 findings. **Sixteen questions were asked and answered.**
What follows is only what is still genuinely open — plus a few things that are decided but
where the *execution* has an unresolved sub-choice.

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
   assertions refuse, offered resources permit — looks better than picking one globally.
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

### What we actually need decided

1. **Threshold shape.** Absolute bytes (matching the existing gzip precedent), or
   relative — "you re-decoded more than the distance you jumped"? The relative form is
   more meaningful for a tripwire (it captures *disproportionate* work); the absolute
   form matches what's already in the codebase.
2. **Still "at most once per stream"?** That's the current specified behaviour and it
   keeps output bounded. But under a cost-based predicate, a caller doing many expensive
   seeks arguably wants *each* one to trip the guard — otherwise the tripwire fires once
   and then goes quiet while the job stays quadratic. Changing it risks flooding.
3. **Does `rapidgzip` expose its index spacing?** If not, that path keeps the current
   accelerator-presence rule and the specification has to admit the predicate isn't
   uniform across codecs.

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

**Recommendation on the table: decide "no", and write it down.** This case has a *better*
signal than the seek case in O1: `cost.access_cost == SOLID` is right there in the cost
receipt at open, before you do anything. If the rewind — which has no open-time signal at
all — doesn't warrant an ambient warning, this one certainly doesn't.

It needs an explicit answer rather than drift: it has now been rediscovered by two
separate reviews, and will be again until it is recorded.

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

**What is worth deciding:**

1. **Should the 7z reader hold its folder decoder open across `open()` calls**, so
   in-order random access costs one pass, as `.tar.gz` already does? The maintainer's
   own note is that this "opens a whole can of worms" — lifetime, when to discard,
   interaction with the single-live-stream rule and with concurrent members. All true.
   But the payoff is a 4.5× cost cliff on the founding use case (walk an archive, hash
   every member), and closing a cross-backend inconsistency rather than inventing
   something new.
2. **Now or later?** Nothing about it is a public-API change, so it is not tag-gated —
   which argues for later. Against: the current cost is the thing the documentation
   currently tells users to work around by using `stream_members()`, and a caller who
   reaches for `open()` because they want random access has no way to know 7z charges
   4.5× where TAR charges 1×.
3. **Either way, does this change the answer to O2a?** Arguably yes: if in-order random
   access stops being expensive on 7z, the remaining expensive pattern is genuinely
   out-of-order, and a warning for it becomes better-targeted — though the "the cost
   receipt already told you" argument still stands.

**Caveats on the numbers.** The 7z archive was written with `-mx1` by the system `7z`;
solid-block layout varies with settings, and a multi-folder archive would show a smaller
ratio. The `.tar.gz` one-pass baseline itself reads ~2.65× the compressed file, which is
unexplained and was not chased — the *relative* comparisons above are the reliable part.
Ratios are stated against each format's own one-pass cost for that reason. RAR was not
measured (see O6 — the test corpus cannot build RAR here).

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

### What we need

- Is the per-archive placement right, or should the capability move to (or also exist at)
  the member level, accepting the spec change and the index-lifetime work?
- If it stays per-archive, is there a name that reads correctly in both contexts, rather
  than picking one of the two current ones?
- If nothing changes, is the specification's current wording the reason, or just its
  effect? (It is the only thing making this "settled".)

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

Options:

- A single boolean (`streams_from_pipe: bool`)? Simple, but boolean fields age badly when
  a third source shape appears.
- A set/collection of supported source shapes? More future-proof, more to specify.
- Reuse the existing `StreamCapability` vocabulary (`SEEKABLE` / `FORWARD_ONLY`), which
  the cost receipt already uses for the source-shape axis? Avoids inventing a second
  vocabulary for the same concept — probably the cheap right answer, but worth a second
  opinion before it sets.

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

### What we need decided

Pick the model, then apply it to all three:

- **Permissive + diagnostic** (what `encoding=` is getting, and what a password *provider*
  already does): the entry point accepts, the backend ignores, the discard is queryable.
  Best for batch/keyring callers. Weakest for the caller who typo'd an argument.
- **Strict refusal** (what static `password=` does today, and what the directory `format=`
  fix established): loud, catches mistakes immediately. Worst for mixed-format batches.
- **Split by intent**: refuse when the argument is an *assertion about this archive*
  (`format=` — "I claim this is a ZIP"), permit when it is a *resource offered for use if
  needed* (`password=`, arguably `encoding=`). This is the most defensible line and the
  one that explains why the existing static/provider split feels wrong — a list of
  candidates is a keyring, not an assertion.

Whatever the answer, the static-vs-provider inconsistency inside `password=` should
probably stop existing either way.

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

**Still open, and worth a security-minded opinion:** the specification currently says the
member is *"rejected **or** exactly one warning is emitted"* — permitting either, which
is why nobody noticed the code only does one of them. Once we're editing that clause
anyway, should the answer be "warn" (current behaviour, plus the new diagnostic), or
should bidi controls in names be *rejected* by the safe-extraction path the way
path-traversal and null bytes already are?

Arguments for warn-only: the danger is in *display*, not in the write — the library
extracts to the literal name, and a caller who never shows the name to a human is
unaffected. Rejecting breaks legitimate right-to-left filenames (Arabic, Hebrew).

Arguments for reject-under-strict-policy: the library's headline claim is safe-by-default,
and the extraction policy already rejects other display-and-path tricks.

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

### The questions

1. **Should a TAR that yields zero members raise?** It would close all three layers at
   once, and "any zero-filled file is a valid tar" is a genuinely bad property. Against:
   an empty tar is legal — `tar cf empty.tar --files-from /dev/null` is a real thing that
   `tarfile` accepts — so this makes archivey stricter than the stdlib on valid input, and
   the project's stated position is that damaged input should yield what is recoverable
   plus an honest error, not a refusal. A middle option: raise only when the archive has
   zero members **and** the file continues past the trailer.
2. **Should `strict_archive_eof` also assert that nothing follows the trailer?** That
   would make it match its documented promise, and would fire on both the ISO case and the
   trailing-junk case. Costs: `tar` writers pad with zeros routinely (that is fine — they
   are zeros), but concatenated archives and some tools append real data after a trailer,
   so this could reject files that other tools read happily. It is opt-in, which is the
   argument for making it mean the stronger thing.
3. **Does the extension-fallback layer need its own answer?** Per the framing section,
   the caller here is likely group 2 (one-off, human watching) or group 1 (batch over
   messy input) — and a diagnostic reaches only the second. If the answer to (1) is "no",
   this layer stays silent for the caller least equipped to notice.

---

## Execution notes — decided, but these bite if missed

Not questions, but sequencing constraints that a reader of the decision list wouldn't
otherwise see:

1. **Three separate decisions all add diagnostic codes** (the ignored-`encoding=` one, the
   wrong-`format=` one, and the bidi one). They share the taxonomy and the policy
   plumbing — **land them as one change, not three.**
2. **A drafted docstring paragraph must not ship before O1 is fixed.** It advertises that
   you can escalate the rewind diagnostic to an error — which is exactly the promise O1
   shows is currently unreliable. Ship the paragraph without that sentence, or ship it
   after.
3. **One committed test will flip when either side of a spec-vs-code disagreement is
   fixed.** A specification table claims the directory backend offers a cheap "peek" at
   its member list; the code returns nothing, consistent with its own cost receipt. The
   decision was to fix the *specification*. If that test starts passing before anyone
   edits the spec, it means someone changed the *code* instead — check which.
4. **A measurement in the review is a hand count, not a metric.** "Format-conditionals per
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
