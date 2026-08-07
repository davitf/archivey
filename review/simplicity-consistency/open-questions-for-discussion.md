# Archivey — open questions from the simplicity & consistency review

**Written to be shared and read standalone.** No prior context needed; everything you
need to form an opinion is inline. Dated 2026-08-07, against `main` @ `2792f9c`.

If you only want to weigh in on one thing, **O1** is the one with real consequences and
**O4** is the one with a deadline.

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

## O2 — Should opening members out of order on a solid archive warn?

**Background.** In a *solid* archive (7z, RAR, any compressed TAR), members share one
compression stream. Reading member #50 then member #10 can mean decompressing from the
block start twice — an O(n²) trap if you loop that way.

The library used to have a specification clause promising a warning here. **No code ever
implemented it**, and it was removed. During that removal the maintainer wrote down a new
rule to justify the removal, and explicitly left one sub-question open: *should we emit a
plain Python `warnings.warn` instead?*

**Status:** still open. Verified: there is no `warnings.warn` anywhere in the library, and
the current specification says the opposite — "no diagnostic, no warning — discoverable
via `reader.cost.access_cost` and the `open()` docstring."

**Recommendation on the table: decide "no", and write it down.**

The argument, which is the same one that resolved O1's framing: this case has a *better*
signal than the rewind does. `cost.access_cost == SOLID` is right there in the cost
receipt, available at open, before you do anything wrong. Whereas nothing in the receipt
tells you a codec lacks a seek index. If the rewind — with no open-time signal — doesn't
warrant an ambient warning, this one certainly doesn't.

**Why it needs an explicit answer rather than drift:** it has now been rediscovered by
two separate reviews. Until it's recorded as decided, it will be rediscovered again.

---

## O3 — Two names for the same capability, and the deadline is the tag

Two entry points express the same idea with different spellings:

```python
open_archive(path, seekable_members=True)   # archives
open_stream(path, seekable=True)            # a single compressed stream
```

One review pass called this a live pre-freeze question — rename, alias both, or accept.
The other found that the specification **already mandates the current spelling**:

> `open_stream` SHALL keep its `seekable: bool` parameter, and both entry points SHALL use
> the same `seekable` vocabulary for the same concept; concurrency has no meaning for a
> single standalone stream, so `open_stream` MUST NOT gain a concurrency parameter.

The code matches that exactly. The maintainer has ruled to **revisit it before the tag**
anyway — which is legitimate, but means the specification has to change *first*, since it
currently forbids the alternatives.

**The case for leaving it:** the difference is deliberate. `seekable_members` names *what
it applies to* (the members), and `open_stream` has only one stream, so `seekable` is
unambiguous there. Both spell the capability `seekable`.

**The case for changing it:** a caller moving between the two entry points has to
remember two spellings for one concept, and renaming is free until the tag and expensive
after.

**Worth separating:** the genuinely bad thing about `seekable_members` was never its name
— it was that the flag also silently changed which *metadata* you got back. That's a
separate finding and has already been decided (it will be fixed).

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

## O5 — We declined a general rule. Is that right, or just deferred?

There's a recurring shape in the library: **a caller passes an explicit argument, and the
backend silently ignores it.**

Known instances:

| Argument | Honoured by | Silently ignored by |
|---|---|---|
| `encoding=` | ZIP, TAR | 7z, RAR, ISO, directory, single-file |
| `format=` (when wrong but plausible) | fails loudly on most formats | see below |

For contrast, a *third* argument is handled the opposite way: passing `password=` to a
format that has no encryption raises an error, centrally.

A previous fix established the principle for one case — passing `format=ZIP` on a
directory path now raises rather than silently reading the directory — with the reasoning
*"silently overruling it returns a reader over the directory tree to a caller who asserted
a different format."* That principle was never generalised.

**The review recommended generalising it**: an explicit argument naming something the
resolved backend cannot act on is refused at the entry point. One rule replacing three
special cases, and covering the next knob by construction.

**The maintainer chose the softer option instead**: emit a diagnostic when `encoding=` is
ignored, and leave the entry point permissive. Reasonable — it doesn't break a caller who
passes `encoding=` uniformly across a mixed batch of formats, and the discard becomes
queryable.

**The open question is whether that's the policy or just this instance.** As it stands,
the *next* argument someone adds gets decided from scratch again, and the three cases
above now have three different behaviours (raise / diagnostic / silent). Worth an explicit
"yes, permissive-plus-diagnostic is the house rule" — or not.

**One consequence that's easy to miss:** the softer choice does not cover the `format=`
case below (O8), which is being fixed separately.

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

## O8 — A knob that doesn't fire where you'd most want it

**Observed:** `open_archive(some_iso_file, format=TAR)` returns a working reader that
reports its format as TAR and lists **zero members**, with no error and no warning.

**Why it's not a TAR bug:** an ISO image starts with 32 KiB of zeros, and two zero blocks
are exactly a TAR end-of-archive marker. The TAR reader is correctly reading a valid,
empty TAR archive.

**Already decided:** emit a diagnostic when an explicitly-passed `format=` yields an empty
listing and format detection would have said something else. `format=` stays an override —
it exists because wrong file extensions are normal.

**The residue worth a look:** the library has a configuration knob, `strict_archive_eof`,
documented as what you set when you need a *provably complete* listing. It does not fire
here — the trailer is present and well-formed, so nothing is strictly wrong. Arguably
that's the single case where a caller would most want it to. Not a decision anyone has
made; just an oddity nobody has looked at.

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
