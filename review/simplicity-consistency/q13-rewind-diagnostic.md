# Q13 — `STREAM_REWIND_REDECOMPRESSES`: resolution and drafts

Q13 was reopened against both review passes' recommendation. Working it produced a
different answer than either "recategorise it" or "leave it flagged", plus one new
`CONFIRMED` finding (**F19**) that neither pass caught.

This file carries the resolution, the three drafts it calls for, and F19.

---

## 1. What the diagnostic's job actually is

The maintainer's argument reopening this: *reaching this event already requires
`seekable_members=True`, we document that, so a diagnostic ends up invisible anyway.*

That is right, and the reason is structural rather than incidental. The event has two
possible jobs and only one of them survives:

| Job | Verdict |
|---|---|
| **Inform** the caller that a rewind was slow | **Fails.** Nobody polls `reader.diagnostics` to discover that their seeks were slow — they discover it because it *was* slow. `VISION.md`'s warnings-as-data argument is about **honesty** signals (was this name rewritten, is this digest unverifiable, is this archive truncated) — things that change whether you trust the bytes. A rewind does not change what you get, only how long it took, so the diagnostics channel is a poor fit for it. |
| **Arm a tripwire** — `DiagnosticPolicy` → `RAISE` → `DiagnosticRaisedError` from the seek | **Survives.** "Abort if this indexing run would go quadratic" is the founding use case's failure mode (`VISION.md`: index and dedupe decades of messy backups, where re-decompression made the job intractable). |

**So the emission site stays, and its stated job becomes the tripwire.** A tripwire is
*supposed* to fire on what the caller did, which is why the O-23 awkwardness mostly
dissolves rather than needing to be resolved. `from_offset` / `to_offset` stay for the
same reason: under this framing they are the useful payload (how far back you jumped ≈
how much gets re-decoded), where under the informational framing they were noise.

### An idea considered and rejected

Moving the informational half to `CostReceipt.notes` — which is declared at `cost.py:80`,
**never populated anywhere in `src/`**, and which `access-mode-and-cost` permits for
static capability caveats.

**Rejected**, on the maintainer's own argument. A cost note is exactly as unread as a
diagnostic; moving an unread signal to a different unread place fixes nothing, and it
would populate a dead public field that then freezes at `0.2.0`. It would also have
forced an archive-vs-member granularity decision (ZIP deflate members warn, stored
members do not) that only existed because of the idea itself.

**Where the informational job actually belongs:** the docstring, at the moment the
caller opts in. See draft A — and note that O-23's *own* remedy for usage advice was the
docstring, "which render[s] into `docs/api.md`".

---

## 2. F19 — the predicate is silent for a degenerate index *(new, `CONFIRMED`)*

Working Q13 surfaced a defect neither pass found, and it is the one that matters most
for the tripwire.

**The predicate today is codec identity, decided once at open.** `codecs.py`
`rewind_warning(config)` returns a `RewindWarning` or `None` per codec, and
`ArchiveStream._maybe_warn_rewind` fires on the first backward seek if it is non-`None`.
XZ, lzip and unix-compress return `None` unconditionally, because those formats *can*
carry a seek index — and `seekable-decompressor-streams` says so explicitly:

> XZ, lzip, and unix-compress indexed seeks SHALL NOT emit this event.

**But a degenerate index is indistinguishable from no index at runtime.** Measured:

```
single-block .xz, 1 MB incompressible payload, open_stream(seekable=True)
  inner stream        : DecompressorStream
  seek points         : [(0, 0)]          <- count = 1, just the origin placeholder
  _rewind_warning     : None
  seek(EOF -> 10)     : diagnostics {}    <- silent, and RAISE cannot fire either
```

A single block is the **common** case, not a contrived one: `lzma.compress()` produces
one, and so does `xz` without threading. The same shape applies to lzip with one member
and `.Z` with no CLEAR codes.

**Why this is the sharpest form of the Q13 concern:** arming `RAISE` today does *not*
protect a caller from the worst realistic case. They get protection on `.lzma` and
`.bz2`-without-accelerator, and silence on a single-block `.xz` that re-decodes the whole
stream on every backward seek. The tripwire — the one job the diagnostic has — is
unreliable exactly where it would be depended on.

### The predicate that would be honest, and it is already computable

`DecompressorStream` already keeps a uniform seek-point table (`_seek_points`, seeded
with `SeekPoint(0, 0)`) and already has `_seek_point_for(pos)`, a bisect for the nearest
preceding point. So the re-decode cost of any backward seek is one expression:

```
redecode_cost = target - nearest_seek_point_before(target).decompressed_offset
```

| Case | Falls out as |
|---|---|
| Index-less codec (zstd, brotli, lz4, lzma-alone, bz2 without accelerator) | only the origin point exists → cost = `target` (full re-decode) |
| Single-block xz / one-member lzip / `.Z` with no CLEARs | **also** only the origin point → cost = `target`. Correctly loud, where today it is silent. |
| Multi-block xz, multi-member lzip, `.Z` with CLEARs | cost = offset into the containing block. Correctly bounded. |
| rapidgzip-accelerated gzip/deflate/zlib | index lives inside the accelerator, not in `_seek_points` — stays a separate arm, as today. |

**One definition covers all three shapes** the maintainer named — bounded, always-true,
and index-dependent — with no codec taxonomy.

**There is already precedent for a cost threshold rather than a codec rule:**
`_rapidgzip_rewind_warning` stays quiet below `RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE`
because "the rewind is cheap enough that warning … is noise." The codebase already
accepts that this is about cost; it just only applies it to the deflate family.

**Answer to "when would we add the diagnostic and possibly raise":** when
`redecode_cost` exceeds a threshold, evaluated **at seek time**. Not "which codec is
this". `RAISE` is a disposition on whatever is emitted, so the predicate *is* the answer
to both halves of the question.

### Scope

This is a behaviour change and it needs its own OpenSpec change on
`seekable-decompressor-streams` (the "XZ, lzip, and unix-compress … SHALL NOT emit" line
is what has to move). It is **independent** of drafts A–C below, which are docs/rule
work with no behaviour change — so it should not hold them up.

Open sub-questions for whoever takes F19:

1. **Threshold shape** — absolute bytes (mirroring `RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE`),
   or relative ("re-decoded more than the distance you jumped")? The relative form is
   more meaningful for a tripwire; the absolute form matches existing precedent.
2. **Still once per stream?** With a cost-based predicate, "once" may be the wrong cap —
   a caller doing many expensive seeks arguably wants each one to trip the guard. But
   changing it breaks the specced "at most once per stream" and risks flooding.
3. **Does `rapidgzip` expose its index spacing?** If not, that arm keeps the current
   accelerator-presence rule, and the spec has to say the predicate is not uniform.

**Guardrails committed:** `test_single_block_xz_rewind_is_silent` (pins the blind spot),
`test_full_rewind_emits_regardless_of_codec` (strict-xfail red half).

---

## 3. Draft A — `seekable_members` docstring

**Why.** `core.py:115` promises the two flags "each unlock**ing** one specific trap", and
then names the trap for `concurrent_members` (`ConcurrentAccessError`) but only the
*gate* for `seekable_members`. The rewind cost — the actual trap — is in
`docs/gotchas.md` and not in the docstring, even though O-23 established the docstring as
the channel for usage advice.

Current text:

> - `seekable_members=True` — `seek()` on a member stream works where the backend can
>   provide positioning. Without it, `seek()` raises `io.UnsupportedOperation`.

**Proposed replacement:**

> - `seekable_members=True` — `seek()` on a member stream works where the backend can
>   provide positioning. Without it, `seek()` raises `io.UnsupportedOperation`.
>   **The trap this unlocks is cost, not correctness:** on a codec with no usable
>   random-access index, a *backward* seek re-decompresses from the start of the stream,
>   so a scan that seeks backwards repeatedly is quadratic. Forward seeks are always
>   cheap. Install the `[seekable]` extra for indexed random access on gzip/bzip2, or
>   prefer one forward pass. The first slow rewind on a stream emits
>   `STREAM_REWIND_REDECOMPRESSES`, which `DiagnosticPolicy` can escalate to an error if
>   you want the cost to be fatal rather than silent.

Two notes for whoever applies it:

- **Land it with F1**, which rewrites this same bullet (the flag stops changing metadata).
- The last sentence should not be written until **F19** is decided — today it would be
  over-promising, since the escalation does not fire for a degenerate index. Either land
  A without that sentence now, or land A after F19.

---

## 4. Draft B — the reframed O-23 rule

**Why.** O-23's boundary is *"diagnostics are archive-related, not usage-related."* It
enumerates the codes it checked — normalized name, inferred encoding, format/extension
conflict, missing EOF marker, invalid timestamp, unverifiable digest, degraded seek index
— and concludes "every existing code fits it."

**That enumeration is a subset.** It omits the extraction codes, and those do not fit
either: `EXTRACTION_MEMBER_BLOCKED` fires when "a universal/**policy** check" blocks a
member, and `EXTRACTION_NAME_COLLISION` / `EXTRACTION_NAME_SANITIZED` depend on the
caller's `ExtractionPolicy` / `OverwritePolicy` / destination. Same archive, different
caller config, different diagnostics. So `STREAM_REWIND_REDECOMPRESSES` was never the
only usage-flavoured code — it is the one that got noticed.

The rule is therefore under-evidenced rather than violated, and the fix belongs on the
rule.

**Proposed replacement for the boundary sentence:**

> A diagnostic reports something the caller **could not have known from the declared
> contract**, and **can act on**. It may be a property of the archive, or of what
> happened when the caller's request met the archive — but never advice the API surface
> already gives.

**Audit against that wording** (all 14 codes):

| Code | Could not have known | Can act on | Fits |
|---|---|---|---|
| `MEMBER_NAME_NORMALIZED` | yes — depends on the stored name | yes — use `raw_name` | ✅ |
| `MEMBER_NAME_ENCODING_INFERRED` | yes | yes — pass `encoding=` | ✅ |
| `FORMAT_EXTENSION_CONFLICT` | yes | yes — pass `format=` | ✅ |
| `SCAN_DIRECTORY_VANISHED` / `SCAN_ENTRY_VANISHED` | yes — a race | yes — re-scan | ✅ |
| `ARCHIVE_EOF_MARKER_MISSING` | yes | yes — `strict_archive_eof` | ✅ |
| `MEMBER_TIMESTAMP_INVALID` | yes | yes — treat as unknown | ✅ |
| `SYMLINK_TARGET_UNAVAILABLE` | yes | yes | ✅ |
| `DIGEST_UNVERIFIABLE` | yes | yes — treat payload as unverified | ✅ |
| `SEEK_INDEX_DEGRADED` | yes | yes — expect slow seeks | ✅ |
| `STREAM_REWIND_REDECOMPRESSES` | yes — *which* stream lacks a usable index | yes — install `[seekable]`, or escalate | ✅ (was ❌ under the old wording) |
| `EXTRACTION_MEMBER_BLOCKED` / `_FAILED` | yes — archive × policy | yes — adjust policy | ✅ (was ❌, unnoticed) |
| `EXTRACTION_NAME_COLLISION` / `_SANITIZED` | yes — archive × destination | yes | ✅ (was ❌, unnoticed) |

**Every O-23 decision survives the rewording**, including the one it was made to settle:
"you opened members out of order" still fails the *first* clause, because
`cost.access_cost == SOLID` told the caller at open, before they did anything. It belongs
in the docstring, exactly where O-23 put it.

### The sub-question O-23 left open, closed by the same argument

O-23 explicitly left undecided whether to emit a plain `warnings.warn` on a solid
out-of-order `open()`. Verified: there is no `warnings.warn` anywhere in `src/archivey/`,
and `archive-reading:512` specifies the opposite.

**Recommend recording it as decided-no**, on the maintainer's own Q13 reasoning
*a fortiori*: solid open has a **better** open-time data signal than the rewind does —
`cost.access_cost == SOLID` is right there in the receipt, whereas nothing in
`CostReceipt` says "this codec has no seek index." If the rewind does not warrant an
ambient warning, solid open certainly does not.

---

## 5. Draft C — `diagnostics` spec note

To be added under the taxonomy in `openspec/specs/diagnostics/spec.md`, so the rule is
normative rather than only living in an observation:

> **Diagnostic admission rule.** A `DiagnosticCode` SHALL report something a caller could
> not have determined from the declared contract and can act on. A diagnostic MAY
> describe a property of the archive, or an outcome of the caller's request meeting the
> archive (extraction policy results; a seek whose cost the stream's index cannot
> bound). A diagnostic SHALL NOT restate advice the API surface already carries: costs
> that `CostReceipt` reports at open — notably solid out-of-order `open()`, which
> `access_cost` describes — belong in the method docstrings, not in the taxonomy.

---

## Summary of what Q13 produced

| Item | Kind | Status |
|---|---|---|
| Emission site stays; stated job is the `RAISE` tripwire | decision | resolved here |
| `CostReceipt.notes` idea | considered | **rejected** — as unread as diagnostics, and adds freezing surface |
| Draft A — docstring names the trap | docs-only | drafted; land with F1, sequence against F19 |
| Draft B — reframed O-23 rule + 14-code audit | observation edit | drafted |
| Draft C — normative admission rule | OpenSpec change on `diagnostics` | drafted |
| Solid-open `warnings.warn` | the sub-question O-23 left open | **recommend decided-no**; needs the maintainer's word |
| **F19** — predicate silent for a degenerate index | **new `CONFIRMED` finding** | behaviour change; own OpenSpec change; guardrails committed |

Net for the small half: one docstring paragraph, one rule rewrite, one spec note — no
behaviour change, no new public surface. F19 is separate and larger.
