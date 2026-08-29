# Open issues from gotchas triage

> **Not user-facing.** Holding area for items that *look* like user gotchas but are
> candidates to fix (product), sync (docs/specs), or deliberately leave irreducible.
> Companion to [threat-model.md](threat-model.md) (security/compat gap register) and
> `dev-docs/IDEAS.md` (speculative backlog). User-facing [Gotchas](../docs/gotchas.md) should
> keep the **irreducible** bucket (plus post-v1 “may improve later” notes) —
> everything else either ships as a fix or stays here until it does.
>
> Snapshot: 2026-07-18 against `main` @ `93dc28e`. Merged since the first triage:
> [#127](https://github.com/davitf/archivey/pull/127) (crypto F1–F5),
> [#128](https://github.com/davitf/archivey/pull/128) (stream-decoder F1–F6),
> [#124](https://github.com/davitf/archivey/pull/124) /
> [#130](https://github.com/davitf/archivey/pull/130) (PPMd bound decode),
> [#120](https://github.com/davitf/archivey/pull/120) (CLI). This triage PR is #129.
> **D4 refresh 2026-07-25:** P1 (TAR EOF Option F) moved to Closed; archive path fixed.

## How to use this list

| Bucket | Meaning | Goes to user Gotchas? |
| --- | --- | --- |
| **Product** | Behavior we can change | Only until fixed; then drop or turn into a “we used to…” note in Why |
| **Docs / specs** | Drift or missing user/spec prose for shipped behavior | No — fix the guide/spec |
| **Irreducible** | Format/stdlib/upstream constraint we can only document + warn | Yes |
| **Longer-term** | Real work, but belongs in `IDEAS.md` / OpenSpec changes | Maybe a one-liner pointer |
| **Closed** | Shipped; kept briefly for provenance | No |

When an item ships, move it to **Closed** (or delete) and update user docs in the
same change when relevant.

---

## Product — candidates to fix

### P2. Multi-volume / split ZIP (`.z01`…`.zip`)

- **Today:** Detected and rejected with `UnsupportedFeatureError` (“rejoin first”).
- **Why fixable:** 7z/RAR already join volumes; ZIP needs disk-aware central-directory
  addressing over an ordered concatenation — natural part of a **native streaming ZIP**
  reader (`IDEAS.md`), not a stdlib `zipfile` wrap.
- **Until then:** user Gotchas / `formats.md` (already noted).
- **Refs:** `IDEAS.md` native ZIP; `format-zip`; `zip_reader.py`.

### P3. Native TAR header walker (replace stdlib silent-EOF leniency)

- **Today:** Option F's EOF backstop raises `CorruptionError` on a rejected (non-null)
  header in random access (including final-block via `_EofProbeStream`) and on mid-archive
  rejected headers in streaming; streaming still cannot see a rejected *final* header
  (tarfile's `_Stream` hides it). The `absent`/`short` residual stays warn-by-default.
- **Why fixable:** Same native-first strategy as 7z/RAR — validate each header at its
  offset, close the streaming final-header gap, and improve salvage/precision on detectable
  cases. The `absent`/`short` residual remains intrinsically ambiguous even with a native
  walker (byte-identical trailer-less-complete vs. truncated-at-boundary).
- **Larger than closed P1** (Option F EOF honesty); this is the structural follow-on.
- **Refs:** `known-issues.md`; `IDEAS.md` (implied by native-first); W1 longer-term.

### P4. ZIP UTF-8 general-purpose bit 11 “lie”

- **Today:** Stdlib `zipfile` strictly UTF-8-decodes flagged names → one bad name can
  make the **whole archive** unlistable (`CorruptionError`).
- **Why fixable:** Native ZIP parser can fall back like unflagged names + diagnostic
  (same story as multi-volume / streaming ZIP).
- **Refs:** `IDEAS.md` native ZIP; adversarial string corpus.

### P5. Rapidgzip / accelerator process abort when caller closes the source

- **Today:** Upstream rapidgzip 0.16 can `terminate()` if the Python source raises
  under a live accelerator stream. Archivey avoids closing *its* SharedSource under
  the stream; **caller-owned** sources remain exposed.
- **Why partially fixable:** Keep mitigating in-tree; full fix is upstream. Product
  work: document loudly (Gotchas); optionally refuse accelerator on non-path /
  non-owned sources; hang sandbox for untrusted input (threat-model O5 follow-up).
- **Refs:** `known-issues.md` Bug 3; `access-and-cost.md`; Gotchas; threat-model accelerator hang.

### P7. An unclosed member stream is never reclaimed — **CLOSED**

**Resolved** in `close-member-streams-on-reader-close` (#225), and not by any of the
three options below. The maintainer chose **B** (close member streams on
`reader.close()`, stdlib parity) after pointing out that the principle A was protecting
says something narrower than the write-up assumed: "never silently close/invalidate a
held stream" sits inside the *concurrency gate* paragraph, where it is the rule that
makes a second overlapping `open()` raise instead of closing the first. It is about how
contention is resolved, not about lifetime.

Two corrections to the analysis below, both found while implementing:

1. **B does not exercise the Bug 3 accelerator trap.** Teardown was already deferred
   until the last lease dropped, so the source was never closed under a live stream —
   and B closes streams *before* teardown, which is the safe order either way.
2. **The fd leak was a plain bug, not a design consequence.** The safety-net finalizer
   could never fire: its callback strongly referenced the stream, and `weakref.finalize`
   keeps the callback alive until it fires, so the stream kept itself alive. Fixed by
   capturing `id(stream)` — which is all `ReaderState` ever used. It leaked under every
   option, including A.

Original write-up below.

### P7 (original). An unclosed member stream is never reclaimed — `reader.close()` does not help

**Raised 2026-08-04** by the maintainer, reading `docs/reading-members.md`: *"closing
streams on reader close makes sense and might be safer."*

**Current behaviour is specified and consistent**, so this is a design question, not a
bug. `archive-reading/spec.md:555-557` says a member stream opened before close **MAY**
remain usable until that stream is closed, with backend resources alive until the last
one closes; the scenario table at `:576-577` reads it as a guarantee. Measured on all
seven backends (zip, tar, tar.gz, bare gz, directory, 7z, RAR): reading after
`reader.close()` succeeds everywhere, and `stream.close()` afterwards is clean.

**What the measurement also found, and this is the actual hazard:** dropping the
stream reference *without* closing it leaks a file descriptor that GC never reclaims —
`+1 fd` on every backend tested, unchanged after three `gc.collect()` passes. So:

```python
with archivey.open_archive(p) as r:
    s = r.open("m.txt")      # forgot to close s
# reader closed, s dropped -> one fd held until process exit
```

The reader's own close is the natural place a caller expects that to be cleaned up,
and today it is not.

**The argument for the current design**, which is not obvious and should not be lost:

1. `archive-reading/spec.md:82-83` makes "never silently close/invalidate a held
   stream" a general principle — it is the same rule that makes a second overlapping
   `open()` raise instead of closing the first.
2. **The accelerator hazard runs the other way.** Closing a source underneath a live
   rapidgzip stream is precisely the operation that used to abort the process
   (`known-issues.md` Bug 3). Making `reader.close()` do that routinely would exercise
   the trapped path on every `with` block that holds a stream. Archivey contains it
   now — but it is contained, not free.

**The argument against**, i.e. for the maintainer's instinct:

1. **Stdlib does the opposite.** `zipfile.ZipFile.close()` and `tarfile.TarFile.close()`
   both invalidate member streams. Migrating users get a behaviour they did not ask
   for, and `migrating.md` does not mention it.
2. **Deterministic release.** Escaped streams are almost always a bug; failing loudly
   finds them, and the fd above stops leaking.
3. `stream_members()` already invalidates on advance, so "streams are ephemeral" is
   established where it matters most — the general `open()` case is the outlier.

**Options:**

| | Change | Cost |
|---|---|---|
| **A** | Keep as specified; add a **diagnostic on reader close when streams are still open**, and close-on-finalize so a dropped stream is reclaimed | No API break; fixes the leak; keeps the accelerator path cold |
| **B** | Close member streams on `reader.close()`, matching stdlib | Spec delta to `archive-reading`; breaks the escaped-stream contract; routinely exercises the Bug 3 trap |
| **C** | Status quo | The fd leak stays, and it is silent |

**Recommendation: A.** It gets the safety the instinct is reaching for — nothing leaks,
and you are told when you left a stream open — without turning `reader.close()` into
the operation that closes a source under a live accelerator. B is defensible on
stdlib-parity grounds and would need its own change; it is not a docs decision either
way.

**Docs status:** `docs/reading-members.md` and `support-matrix.md` describe the current
behaviour correctly. They do **not** mention the leak, because it is not user-facing
advice until this is decided.

### P8. A directory path silently ignores an explicit `format=` — **CLOSED**

**Fixed** in `reject-format-override-on-directory` (#225): `open_archive` now raises
`ArchiveyUsageError` when a directory path is given a `format=` that is neither `None`
nor `DIRECTORY`. Original write-up below.



- **Today:** `open_archive(path, format=ArchiveFormat.ZIP)` on a path that happens to
  be a directory opens it as a directory pseudo-archive. `core.py:201-203` sets
  `resolved_format = ArchiveFormat.DIRECTORY` before the caller's `format=` is ever
  consulted, so the argument is discarded without a diagnostic.
- **Why it looks wrong:** every other way of being explicit about the format is
  honoured or rejected loudly. A caller who passes `format=` is asserting something,
  and this is the one case where the assertion is silently overruled. The plausible
  path there is a variable holding what the caller believes is an archive path —
  exactly the case where a quiet reinterpretation is least welcome.
- **Proposal:** raise `ArchiveyUsageError` when `format=` is passed for a directory
  path and is not `DIRECTORY`. Cheap, and the check has one call site. Passing
  `format=DIRECTORY` explicitly stays valid.
- **Cost of not fixing:** the guide has to teach the exception, which is one more
  rule for a case nobody wants.
- **Refs:** raised by the maintainer reviewing `docs/opening-and-listing.md` (#224), fixed in #225;
  `outline.md` must-explain #25. `docs/opening-and-listing.md` states the current
  behaviour neutrally — "this holds even if you pass `format=`" — so the line becomes
  "raises" rather than needing a rewrite if this is fixed.

### P9. Should a solid out-of-order `open()` warn at all?

- **Today:** nothing warns. `reader.cost.access_cost == SOLID` is the queryable signal,
  and `ArchiveReader.open()` / `.read()` docstrings carry the cost (#225).
- **Decided already:** it is **not** a diagnostic. The reason is the taxonomy's
  **admission** clause (`openspec/specs/diagnostics`): a diagnostic must report
  something the caller could not have determined from the declared contract of the
  call, and out-of-order solid access is already carried by `reader.cost.access_cost`
  and the `open()` / `.read()` docstrings. (The older "diagnostics describe the archive,
  not the caller" phrasing was retired with O-23 — the outcome is unchanged, the test
  behind it is not.)
- **Open question:** whether a plain `warnings.warn` is worth it. Arguments for: a
  quadratic read is a real footgun and nothing surfaces it at runtime. Against: it fires
  on a legitimate access pattern the caller may have chosen knowingly, and per-call
  emission would log once per member in exactly the loop that needs the advice
  (once-per-reader would be the shape). Deliberately parked rather than left implicit.
- **Refs:** `spec-drop-unimplemented-solid-warning` (#225); O-23.

### P11. A RAR stream source silently costs a whole-archive disk copy, in no signal

- **Today:** `unrar` needs a filesystem path, so `RarReader._ensure_archive_path()`
  (`src/archivey/internal/backends/rar_reader.py:532-555`) writes the **entire archive**
  to `tempfile.mkstemp(suffix=".rar")` the first time a member cannot be read directly.
  Multi-volume stream sources go through `_materialize_stream_volumes()` (`:438-459`) into
  a temp dir. Both are removed on reader close. Measured on a `rar -m5` archive read from a
  `BytesIO`: the read succeeds, one temp `.rar` of full archive size appears, and

  ```
  reader.diagnostics      -> []
  reader.cost             -> CostReceipt(..., notes=())      # byte-identical to a path source
  ```

- **Why it matters.** `VISION.md`'s load-bearing claim is one uniform interface with
  **honest cost signals** — "behaviour differences are **data** (`None`, explicit fields,
  diagnostics, `CostReceipt`), never silent guesses". A whole-archive copy to disk is the
  largest hidden cost in the library, and it is in neither channel. A caller handing over a
  4 GiB `BytesIO` gets a 4 GiB temp file with no way to have known.
- **The trigger is per-member, which makes it worse.** A stored member from a stream costs
  nothing (`_can_direct_read`); the next member in the same archive, compressed, costs a
  full copy. Same call, same source, two very different costs, no signal distinguishing
  them. Any member of a solid archive triggers it via `_iter_with_data` (`:673-679`).
- **Not an ADR 0010 violation.** That decision forbids buffering a *non-seekable* source to
  fake seekability; this is a *seekable* stream copied to satisfy an external binary — a
  different trade-off that was never written down. Worth noting `docs/access-and-cost.md:142`
  ("Archivey will not silently buffer the whole archive into memory or a temp file") is
  scoped to the pipe case in context but reads as absolute.
- **Open question:** a `CostReceipt.notes` entry, a diagnostic, or both. A note is the better
  fit — the taxonomy's admission clause asks whether the caller could have determined it from
  the declared contract, and *nothing* declares it today, but the placement clause prefers a
  structured field where one exists and `notes` is exactly that field. A size threshold is a
  third option and probably wrong: the cost is unbounded either way and a caller choosing a
  stream source deserves to know before the first read, not after 1 MiB.
- **Provenance:** found 2026-08-17 answering a maintainer question during Topic 8's step-4
  checkpoint ("do we support extraction for rar files opened via a stream, since we rely on
  the external unrar binary and presumably can't pass a stream to it?"). Neither step-2/3
  pass had it: #246's E-43 captured temp materialization for *solid random opens* only, from
  `formats.md:123-124`, and no page states the general case.
- **The documentation half is separate** and belongs to Topic 8:
  [`review/docs-content/claims.md`](../review/docs-content/claims.md) **E-71**, a gap row —
  no page states the behaviour at all.

### P12. The Brotli content probe accepts arbitrary binary data — **narrowed**

- **Was:** Brotli has no magic, so `detect_format` identified it by decoding a 256-byte
  prefix. Measured **~8.2%** of random blobs and **~3.5%** of a real `/usr` tree
  (`/**\n` Doxygen openers dominated). `open_archive` listed one fabricated
  `.uncompressed` member; a full read raised `TruncatedError` naming a format the file
  never was — and a prefix of fabricated bytes (65 536 measured) may already have been
  produced.
- **Now (framing + completeness + chain walk):** when the source length is known, a first
  meta-block that declares more bytes than the source holds is rejected; a fully visible
  source that does not decode to completion is rejected; and a bounded self-describing
  block-chain walk rejects later overruns / trailing bytes. Re-measured after
  `probe-completeness-gate` with the 64 KiB completeness drain
  (`scripts/exploration/probe_residual_census.py`, 150 623 files under `/usr`):
  **29 fabricated claims (0.019%)**, down from 128 (0.193%) after the
  first-block gate alone. Residual families (OLE/CFB, COFF, lucky compressed-first fits
  above the prefix) remain — and end-to-end those structured residuals are usually claimed
  by the **LZMA Alone** probe at `PROBABLE` (not Brotli). **`probe-provenance-unconfirmed`**
  keys the unconfirmed channel on provenance rather than `GUESS` confidence, so those
  failures stamp too (re-measured: **0 of 29** fabrications carry no signal).
- **Confidence / errors:** probe-only Brotli is `PROBABLE` when the first meta-block is
  compressed (or `.br` corroborates), else `GUESS`. A decode failure on a **probe-only**
  result (any confidence; no matching extension, no inner-TAR upgrade) sets
  `format_unconfirmed=True` and emits `PROBE_FORMAT_UNCONFIRMED`.
- **Still three clauses, not one:** the listing can be wrong; a full read raises; **and**
  a prefix of fabricated bytes may already have been produced. Not a silent success.
- **Refs:** `openspec/changes/archive/2026-08-25-probe-completeness-gate/`; investigation
  [`investigations/brotli-content-probe-results.md`](investigations/brotli-content-probe-results.md);
  threat-model O10.

### P10. `format_availability()` fabricates a verdict for a wrong-typed argument — **CLOSED**

**Fixed** in `2026-08-17-reject-wrong-typed-format-arguments`: one validation helper
(`src/archivey/internal/format_args.py`) at the boundary, called from all four public
entry points that take a format. A value outside the types the signature declares now
raises `ArchiveyUsageError` (ADR 0012, so `except ArchiveyError` cannot swallow it),
naming what was passed, what was expected, and — for a `StreamFormat` — the
`ArchiveFormat` pairs built on that codec (`StreamFormat.ZSTD` → `ArchiveFormat.ZST` or
`ArchiveFormat.TAR_ZST`), read off the predefined names so a new codec needs no second
edit. The sweep table below is fully paid, plus one entry point the sweep did not name:

| Call | Was | Now |
|---|---|---|
| `format_availability(StreamFormat.ZSTD)` | fabricated `NONE / missing=() / SEEKABLE` | `ArchiveyUsageError` |
| `open_archive(path, format=StreamFormat.ZSTD)` | `AttributeError: … no attribute 'container'` | `ArchiveyUsageError` |
| `extract(path, dest, format=StreamFormat.ZSTD)` | same `AttributeError` | `ArchiveyUsageError` |
| `open_stream(src, format="zst")` | **argument silently discarded**, auto-detected instead | `ArchiveyUsageError` |

The fourth row is the one this entry did not have. `open_stream` was ruled correct by
design for accepting `StreamFormat | ArchiveFormat` — it still does, and that asymmetry
is untouched — but its fall-through ignored a value of *neither* type, which is the same
dishonesty as the fabricated record, wearing a different coat. It joined the same fix.

Two things deliberately did **not** change: the signatures (the decision was to restrict,
not to widen), and `ArchiveFormat.UNKNOWN`'s `NONE` with an empty `missing` — a hintless
NONE is a real answer, so the check keys on the argument's *type*, never on the shape of
the verdict. Guarded by `tests/test_format_arguments.py` (red-green per call site, and
re-verified failing against the unfixed code). Original write-up below.

> **Corrected 2026-08-17**, same day it was filed. The original entry was titled *"answers
> for a public type it does not know"* and implied the signature permits a `StreamFormat`.
> It does not, and both project type checkers reject it. What survives is narrower and
> still real: the failure mode on a wrong-typed call is a **fabricated record**, not an
> exception. The overstated half is struck rather than deleted, because a register that
> quietly rewrites itself teaches the next reader nothing.

- **The two types are not siblings.** `ArchiveFormat` is a `(container, stream)` **pair**
  (`src/archivey/types.py:76-88`) — `ArchiveFormat.ZST` *is*
  `ArchiveFormat(ContainerFormat.RAW_STREAM, StreamFormat.ZSTD)`. `StreamFormat` is the
  codec half of that pair. They live at different levels, so "both carry the value `'zst'`"
  describes a containment relation, not a duplicate.
- **The signature is correct and enforced.** `format_availability(fmt: ArchiveFormat)`
  (`src/archivey/internal/registry.py:314`). Passing the component is a type error and
  **both checkers catch it**:

  ```
  pyrefly check  -> 1 error   (argument type)
  ty check       -> 1 diagnostic ("Parameter declared here")
  ```

  So a caller under either checker — which is every caller in this repo — is protected.
- **What is still wrong.** Given the wrong type anyway, the call **fabricates a plausible
  record instead of raising**:

  ```
  format_availability(ArchiveFormat.ZST)  -> FULL, missing=(), FORWARD_ONLY
  format_availability(StreamFormat.ZSTD)  -> NONE, missing=(), SEEKABLE
  ```

  `support=NONE` with an empty `missing` is indistinguishable from a legitimate
  "unsupported, and here is nothing to install about it", and `required_source` contradicts
  the real record on the field `opening-and-listing.md:70-85` teaches callers to branch on.
  `ArchiveyUsageError` exists for exactly this class (ADR 0012: caller mistakes sit outside
  the `ArchiveyError` tree), and it is not raised.
- **How a caller reaches it.** Not through the documented recipe — `detect_format()` returns
  a `FormatInfo` whose `.format` is an `ArchiveFormat`. The realistic path is
  `open_stream(format=…)`, which publicly accepts `StreamFormat | ArchiveFormat`
  (`src/archivey/core.py:374`): a caller holding a `StreamFormat` for that call may pass the
  same value here, and an untyped project gets no warning.
- **DECIDED 2026-08-17 (maintainer): restrict to `ArchiveFormat` and raise a usage error
  on any other type.** Not a public-surface widening — `open_stream` keeps accepting
  `StreamFormat | ArchiveFormat` because a raw stream genuinely has no container, and that
  asymmetry is the point rather than an inconsistency. Rejected: accepting `StreamFormat`
  here and resolving it to the `RAW_STREAM` pair (widens the surface pre-`0.2.0` for a
  caller error), and closing won't-fix (the field-type violation is real even though the
  checkers catch the call).

- **The sweep widens the fix to three entry points, failing two different ways.** Four
  public functions take a format argument; `open_stream` is correct by design. The other
  three all mishandle a wrong-typed one:

  | Call | Today | Should be |
  |---|---|---|
  | `format_availability(StreamFormat.ZSTD)` | returns `NONE / missing=() / SEEKABLE` — a fabricated record whose `format` field violates its own declared type | `ArchiveyUsageError` |
  | `open_archive(path, format=StreamFormat.ZSTD)` | `AttributeError: 'StreamFormat' object has no attribute 'container'` | `ArchiveyUsageError` |
  | `extract(path, dest, format=StreamFormat.ZSTD)` | same `AttributeError` | `ArchiveyUsageError` |

  The second shape is arguably worse than the one this entry was filed for: a raw
  `AttributeError` naming a private attribute crosses the public boundary, which is the
  error contract's "no internal leakage" rule (`CONTRIBUTING.md`) as well as ADR 0012's.
  One validation helper at the boundary covers all three — this is a fix-the-cause case,
  not three fixes.
- **Severity, honestly:** low as a defect, moderate as an *honesty* question — a public
  query that invents an answer is the shape `VISION.md` rules out, even when the caller was
  wrong to ask that way.
- **Provenance:** found 2026-08-17 reconciling the baselines of two Topic 8 step-2/3 passes
  (#246, #247). Recorded in
  [`review/docs-content/claims.md`](../review/docs-content/claims.md) Part 1. Not a docs
  defect: no published page passes a `StreamFormat` to this call.

### P10 (original). `format_availability()` answers for a public type it does not know

- **Today:** `StreamFormat` and `ArchiveFormat` are both in `archivey.__all__`, and
  `StreamFormat.ZSTD` / `ArchiveFormat.ZST` are *distinct* members that both carry the
  value `'zst'`. `format_availability()` gives them different answers:

  ```
  format_availability(ArchiveFormat.ZST)  -> FULL, missing=(), FORWARD_ONLY
  format_availability(StreamFormat.ZSTD)  -> NONE, missing=(), SEEKABLE
  ```

  `StreamFormat.ZSTD` is not in `list_known_formats()`, so the second call **fabricates a
  verdict rather than raising**: `support=NONE` with nothing in `missing` to say what is
  absent, and a `required_source` that contradicts the real record.
- **Why it matters.** `NONE` with an empty `missing` is not a legible answer — a caller
  cannot act on it, and `install.md`'s inbound `format_availability()` section is being
  written around exactly this call. `required_source` is worse: `opening-and-listing.md:70-85`
  teaches callers to branch on it (`required_source <= StreamCapability.FORWARD_ONLY`),
  and the fabricated record answers `SEEKABLE` where the real one answers `FORWARD_ONLY`.
  A silent wrong negative on a public query is the "behaviour differences are **data**,
  never silent guesses" claim in `VISION.md` failing on its own terms.
- **Not currently reachable through the documented recipe.** `detect_format()` returns a
  `FormatInfo` whose `.format` is an `ArchiveFormat`, so the guide's own snippet is safe.
  The exposure is a caller who legitimately holds a `StreamFormat` — `open_stream(format=…)`
  publicly accepts `StreamFormat | ArchiveFormat` (`src/archivey/core.py:374`) — and then
  asks about its availability.
- **Open question:** raise `ArchiveyUsageError` for a format outside
  `list_known_formats()`, or make `StreamFormat` members resolve to their `ArchiveFormat`
  peer, or narrow the signature so a `StreamFormat` cannot be passed. The third is
  cheapest and the first is most honest; the second risks implying the two enums are
  interchangeable everywhere, which they are not.
- **Provenance:** found 2026-08-17 while reconciling the baselines of two independent
  Topic 8 step-2/3 passes (#246 read `ZST` as `FULL`, #247 swept
  `list_supported_formats()` only). Neither pass had it in this form; the disagreement
  between them was the signal. Recorded in
  [`review/docs-content/claims.md`](../review/docs-content/claims.md) Part 1, which is a
  docs artifact and deliberately does not fix it.
- **Not a docs defect.** No published page states anything false about this; the guide
  never passes a `StreamFormat` to `format_availability()`.

### P13. `.brotli` is not a registered extension, so genuine streams read as uncorroborated

- **What:** the extension map holds `.br` and `.tar.br` only. A genuine Brotli stream named
  `*.brotli` is identified by the content probe with nothing corroborating it, so a decode
  failure stamps `format_unconfirmed=True` on a file that really is Brotli.
- **Measured** (2026-08-26, `main` @ `a3dc408`, 63 343 files): the tree holds exactly four
  files with any magic-less-codec extension, and they split two/two —
  `underscore.min.js.br` / `.map.br` corroborate and stay silent, while
  `jquery.min.js.brotli` / `jquery.min.map.brotli` do not and would stamp. Both `.brotli`
  files are genuine, verified by full decode.
- **Why it is not just a missing table row:** it changes how much the *design* argument in
  PR #263 §6 costs. That section proposes the filename stop suppressing the stamp at all,
  and prices the cost at "genuine damaged files". Half that cost is already being paid here
  for an unrelated reason.
- **Open:** what the ecosystem actually emits. The reference CLI writes `.br`; `.brotli`
  clearly occurs in asset pipelines. Adding an extension is public detection data and has
  its own false-positive risk, so this wants a quick survey rather than a reflex fix.
- **Refs:** `src/archivey/internal/registry.py` `extension_map()`; census via
  `scripts/exploration/probe_residual_census.py`.

### P14. Several exported names are documented nowhere

- **What:** `docs/api.md` opens with "Everything documented here is re-exported from the
  top-level `archivey` package and listed in `archivey.__all__`" — true, but not the
  converse. 31 of the 87 names in `__all__` have no mkdocstrings page anywhere in `docs/`,
  including `FormatInfo`, `DetectionConfidence`, `FormatAvailability`, `FormatSupport`,
  `MissingComponent`, `ExtractionProgress`, `DiagnosticContext`,
  `ARCHIVE_INTEGRITY_CODES`, and every exception class.
- **Why it matters now:** `FormatInfo` is the return type of the public `detect_format`,
  and PR #263 proposes adding an evidence ledger to it. A type users are expected to read
  fields off, with no rendered reference, is where an "internal" field quietly becomes
  public — which is exactly what happened with `FormatInfo.corroborated` in #267 (held
  back with `compare=False, repr=False` in the follow-up).
- **Note:** the exceptions may be deliberate — they are described narratively in
  `docs/errors-and-diagnostics.md`. The data types are the gap.
- **Check:** compare `archivey.__all__` against `^::: archivey\.(\S+)` across `docs/*.md`.

### P15. `SingleFileReader`'s eager open-time validation is a no-op — **confirmed bug**

- **What it intends.** `src/archivey/internal/backends/single_file_reader.py:183-190`:

  ```python
  if self._seekable:
      # Eagerly open+close a codec stream so format/seekability errors surface at
      # archive-open time rather than on a later read. Not cached — every
      # _open_member builds a fresh codec stream.
      probe = self._open_codec_stream()
      probe.close()
  ```

- **What happens.** The probe opens and closes without ever reading, and **every stdlib
  codec validates its header on first read, not at construction**. Verified directly:
  `gzip.GzipFile`, `bz2.BZ2File` and `lzma.LZMAFile` over 40 000 zero bytes all construct
  and close without error, and raise only on the first read (`BadGzipFile`, `OSError`,
  `EOFError`). So the probe never triggers validation and the documented guarantee does
  not hold.

- **Observable effect** (measured on `main` @ `a3dc408`), a file of 40 000 zero bytes named
  for each codec, detected by extension at `GUESS`:

  | file | `open_archive` | listing | read |
  | --- | --- | --- | --- |
  | `backup.gz` / `.bz2` / `.xz` / `.zst` / `.br` | succeeds | 1 fabricated member | `CorruptionError` |
  | `backup.lzma` | succeeds | 1 fabricated member | `TruncatedError` |

  Contrast the container formats, which behave as intended: ZIP, RAR, 7z, ISO and TAR+GZ
  all raise `CorruptionError` at open for the same payloads. Only the single-file path
  defers.

- **Why it matters beyond the immediate surprise.** This is the mechanism behind the
  extension-fallback honesty gap discussed in PR #263: because listing *succeeds*, the
  `EXTENSION_FORMAT_UNCONFIRMED` diagnostic (which keys on an **empty listing**) cannot
  fire, and the failure lands on the read path where the flag is currently probe-only. So
  a `.gz` that was never gzip reports `CorruptionError` with `format_unconfirmed=False` —
  archivey blaming the bytes for a format only the filename ever claimed.

- **Fix.** Read one byte in the probe before closing it. Verified: `read(1)` over wrong
  bytes raises a properly *translated* `ArchiveyError` for all seven codecs tested (gz,
  bz2, xz, zst, br, lzma, lz4) — `CorruptionError` for six, `TruncatedError` for LZMA
  Alone. A valid empty stream returns `b""` rather than raising, so the check is safe.

- **The zero-byte case is worse, and `read(1)` does not fully close it.** A file of zero
  bytes named for each of the ten single-file codecs opens cleanly on `main` — gz, bz2, xz,
  zst, lz4, zlib, brotli, lzma-alone, lzip and `.Z`, ten for ten — with a listing showing
  one fabricated member, and fails only on read. With `read(1)` in the probe, **nine** raise
  a translated `ArchiveyError` at open time; **`.Z` still opens**, because its decoder reads
  an empty input as an empty stream rather than a truncated one. So the fix needs a
  per-codec answer for `.Z` (a minimum-header check, most likely) rather than resting on the
  one read. Worth noting the same patch makes the open-time error carry `member='<name>'`,
  which reads oddly for a failure that happens before any member was requested.

- **Cost, measured** (~1.8 MB payload, 20 iterations): gzip `0.43 ms → 0.49 ms`
  (negligible); **bzip2 `3.47 ms → 6.67 ms`** — nearly double, because bzip2 must decode a
  full block (up to 900 KB) to yield one byte. That is a real trade against the honest-cost
  contract and should be a deliberate decision, not a silent regression.

- **Non-seekable sources need separate handling.** The `else` branch keeps the opened
  stream as `self._pending_stream` and hands it to the first `_open_member`, so a probe
  read there would consume a byte the caller expects. It needs pushback, or the check has
  to stay seekable-only (in which case say so in the comment).

- **No test covers the guarantee.** Nothing in `tests/test_single_file.py` asserts that a
  malformed single-file stream raises at `open_archive` time — which is why an eager check
  that never checks anything went unnoticed. A red-green test belongs with the fix.

### P16. A corrupt bzip2 member reads as empty under the accelerator — **confirmed bug**

- **What happens.** With `[seekable]` installed and `seekable_members=True`, a bzip2
  single-file member opens through **rapidgzip's bundled bzip2 decoder**
  (`codecs.py: BZip2Codec.open` → `_rapidgzip_bzip2`, gated by
  `use_indexed_bzip2.enabled_for(seekable=...)`, default `AUTO`). That decoder returns
  **zero bytes with no error** for input the stdlib decoder rejects. The capability flag,
  not the data, decides whether a corrupt archive raises.

- **Measured** on `main` @ `e54eff7`, `rapidgzip` installed, `indexed_bzip2` **not**
  installed — so this is rapidgzip's bundled decoder specifically, not the separate package:

  | `backup.bz2` contents | `seekable_members=False` | `seekable_members=True` |
  | --- | --- | --- |
  | valid bzip2 | 11 bytes | 11 bytes |
  | 40 000 zero bytes | `CorruptionError` | **0 bytes, no error** |
  | zero-byte file | `TruncatedError` | **0 bytes, no error** |

  End-to-end: `open_archive(bad, seekable_members=True).read(member)` returns `b""`.
  Holding everything else fixed and flipping only `use_indexed_bzip2` reproduces it
  (`AUTO` silent, `OFF` raises).

- **gzip is unaffected**, which is what makes this a bzip2 decoder defect rather than a
  general accelerator-wrapper problem: a corrupt `.gz` raises `CorruptionError` and an
  empty one `TruncatedError` under both accelerator modes.

- **Why it matters.** This is silent data loss on the founding workload. Someone verifying
  or indexing a backup corpus gets "this archive is fine and contains nothing" for a file
  that is corrupt — the worst available answer, and worse than P15, which at least raises
  eventually. Note also that no spec currently forbids it: `compressed-streams`'s *Content
  faults raise from read, never from close* explicitly scopes rapidgzip **out**.

- **Interaction with P15.** It defeats P15's fix. The proposed `read(1)` probe gets `b""`
  back on this path and concludes the stream is a valid empty one, so the two cannot be
  fixed independently.

- **Fix.** Stated as a contract rather than a patch: an accelerator SHALL raise the same
  class of translated error, on the same inputs, as the path it replaces. Concretely, a
  decoder ending a stream with no output, no input consumed and no valid end-of-stream
  marker raises. If rapidgzip cannot report enough to distinguish that from a genuine empty
  stream, decline acceleration below the codec's minimum framing size.

- **Refs:** found while measuring P15's fix; both are addressed together by
  `openspec/changes/single-file-open-time-validation/` (spec delta:
  `compressed-streams` → *An accelerator preserves the error contract of the path it
  replaces*). Adjacent but distinct from P5, which is an accelerator **abort**, not a
  silent success.

### P6. RAR solid demux ↔ `unrar` emission-policy coupling

- **Today:** Solid ALL-pipe demux must match what `unrar` actually emits (RAR5
  symlink targets in header → 0 stdout bytes; RAR3 symlink targets in LZ data →
  also 0 after decode). Easy to desync on new member kinds.
- **Why fixable:** Spec’d hardening / shared emission table; called out in the
  unrar-piping investigation as a future change (same class as mixed-password
  ALL-pipe forbid).
- **Refs:** PR #101 (still open) / `dev-docs/investigations/rar-unrar-piping-investigation.md`
  (when merged); `format-rar`.

---

## Docs / specs — drift and missing prose

Code is done unless noted. These should not appear in Gotchas as “broken.”

| Item | Code | Doc / spec action |
| --- | --- | --- |
| Gzip multi-member: omit trailer CRC from `member.hashes` | Done | **Closed** — `formats.md` + Gotchas accurate |
| 7z CRC-less encrypted store → diagnostic | #127 | **Closed** — Gotchas + `formats.md` + `format-7z` (P7) |
| RAR5 HASHMAC / tweaked digests | #127 | **Closed** — noted in `formats.md` RAR section |
| 7z `NumCyclesPower` ≤24 / `0x3F` | #127 | **Closed** — `formats.md` + `format-7z` |
| RAR password via stdin (`-p` + stdin) | #127 | **Closed** — `formats.md` |
| Cross-platform name safety (O2/O3/O4/O7 + RENAME) | #109 / #123 | **Closed** — Gotchas + threat-model marked implemented |
| RAR5 `-ver` history rows in `members()` | Specced + implemented | **Closed** — Gotchas + `formats.md` |
| Duplicate names / `get` last-wins / str vs `ArchiveMember` selectors | Specced | Gotchas done; optional `opening-and-listing.md` pointer remains nice-to-have |
| Hardlink target = earlier same name by `member_id` | Specced | Gotchas done; optional `opening-and-listing.md` pointer remains nice-to-have |
| Nested-archive stance + bounded recursion recipe | Behavior OK | Gotchas one-liner done; fuller recipe still nice for usage/O6 |
| Symlink-unsupported FS ≠ `tarfile` copy-through | Specced | Gotchas done; optional line in `extracting.md` |
| Accelerator opt-out for untrusted + latency budget | Mitigations in tree | Gotchas + costs cover it; P5 residual remains |
| Truncated gzip: stdlib engine recovers prefix on large `read(n)` (`gzip-zlib-truncation-recovery`) | Done | **Composed** with rapidgzip empty→stdlib: fallback fully switches `_inner` to the same gzip-window `DecompressorStream` (#183 / ADR 0014); ISIZE remains for non-empty soft EOF. |
| `stream_members` laziness not honoured by the solid backends | Behaviour was wrong | **Closed** — the spec already required it ("unselected/unread members are not opened/decompressed **and do not request passwords**"); 7z opened each folder at yield time and solid RAR spawned `unrar` at pass start, so iterating an encrypted archive without reading raised `EncryptionError` and made a wrong password look right. Both now defer to the first read. Fixed in #225; found by review on #224. |
| `error-handling`'s `ArchiveyUsageError` catalog does not list the argument-validation rules | Done (P10) | **Open, deliberately.** The wrong-typed `format=` refusal is specced once, in `backend-registry` §"A format argument outside its declared type is a usage error"; `error-handling`'s list is prefixed "SHALL also cover" and is already partial in the same way — it names `open_archive(streaming=True, concurrent_members=True)` but not the directory-`format=` refusal, which lives in `archive-reading`. Raised as a nit on #250 and declined there rather than half-done: centralising is a spec-organisation call that should move **both** argument rules at once, or neither |
| Solid out-of-order `open()` re-decode: spec said it warns, nothing does | Behaviour correct | **Decided** — the spec was wrong, not the code. Maintainer's rule: **diagnostics describe the archive, not the caller's usage pattern**, so "you opened members out of order" is not a diagnostic. `spec-drop-unimplemented-solid-warning` removes the clause; `ArchiveReader.open()` / `.read()` docstrings carry the cost instead, and `reader.cost.access_cost` remains the queryable signal. A plain `warnings.warn` is on the radar as **P9** rather than silently undecided. Fixed in #225; found writing `reading-members.md` (#224). |

---

## Irreducible — document forever (user Gotchas)

These are constraints of formats, stdlib, or upstream. Hardenings and diagnostics
help; they do not disappear. Covered in [Gotchas](../docs/gotchas.md).

- **Solid archives:** out-of-order `open()` can re-decode; prefer `stream_members()`.
- **Seek without index:** backward seek may re-decompress (`STREAM_REWIND_REDECOMPRESSES`).
- **Streaming mode is one pass** (including after early `break`); `scan_members()` to drain.
- **ZIP / ISO need seek** — no pure-pipe path even with `streaming=True`; no silent buffer.
- **ZipCrypto multi-password + STORED** confirmation cost (~1/256 false open → CRC scan).
- **7z AES has no password check value** — without CRC/folder digest, wrong password can
  yield garbage (we warn; 7-Zip does the same).
- **RARLAB `unrar` only** for member data; listing works without it.
- **BCJ2 unsupported** — rejected, not garbage.
- **Native optional wheels / accelerators** may crash or hang on hostile input; we
  mitigate, cannot promise 100%. Includes residual `pyppmd` native-abort risk despite
  bounds + capped extra-NUL flush (see `known-issues.md`); the former
  **exit-after-green** abort of `tests/test_ppmd_raw_streams.py` is **partially**
  mitigated there (overshoot fixed; `Ppmd7T_Free` residual + CI soft-pass remain).
- **ISO import patches pycdlib’s collections** (cycle guard) — visible if the process
  also uses pycdlib directly.
- **`.Z` truncation:** only nonzero leftover bits are loud.
- **Bare `.gz` / `open_stream` + rapidgzip:** truncation detection is best-effort (empty→stdlib
  + single-member ISIZE on path sources); use `use_rapidgzip=OFF` when you need certainty.
  ZIP/7z/… **members** are a different story (container CRC/`VerifyingStream`) — see Gotchas.
- **Metadata fidelity** (xattrs/ACLs/forks) not claimed on extract.
- **Concurrent hostile modification** of the destination during extract — out of scope.

---

## Longer-term (point at `IDEAS.md` / OpenSpec; don’t park design here)

| Theme | Notes |
| --- | --- |
| Native streaming ZIP | Pipes, truncated/no-EOCD, multi-volume (P2), UTF-8 flag lie (P4) |
| Salvage / best-effort read mode | Founding use case; all-or-error today |
| `pyppmd` exit-after-green abort | Partially mitigated (NUL cap + pack_size gate; Free-race residual / `--allow-exit-after-green`); see `known-issues.md` + exploration doc |
| Accelerator hang sandbox | Threat-model O5; fuzz with accelerators off until then |
| OSS-Fuzz onboarding | Before public “safe” marketing (`SECURITY.md` landed) |
| Nested-archive helper / bounded recursion | O6 recipe → maybe a small helper later |
| Free-threading support matrix | Document core vs ISO vs accelerators |
| Public backend API / plugins | Home for exotic formats without libarchive-in-core |
| CLI UX polish | CLI shipped (#120); remaining design Qs under `review/archive/2026-07-17-cli/` |
| Container CRC vs rapidgzip soft-EOF | Separate check: under `use_rapidgzip=ON`, confirm **corrupted** (and truncated) **ZIP/7z** DEFLATE *member payloads* still fail via `VerifyingStream`/CRC. Whole-archive truncation is less the worry for ZIP (missing central directory → open fails); **in-member corruption** is the sneaky case where rapidgzip soft-EOF could otherwise look like a short clean decode. Codec backstop is bare-stream only. From `rapidgzip-truncation-investigation`. |

---

## Closed (recent)

| Item | Closed by |
| --- | --- |
| **P10** A wrong-typed `format=` argument is refused, not answered (all four public entry points) | archived `openspec/changes/archive/2026-08-17-reject-wrong-typed-format-arguments/` |
| **P1** TAR EOF Option F (`observed_kind` split; `strict_archive_eof` default stays `False`) | #149 / #162 — archived `openspec/changes/archive/2026-07-19-decide-strict-archive-eof-default/` |
| Crypto F1–F5 (HASHMAC, 7z no-anchor diagnostic, NumCycles clamp, unrar stdin password, `compare_digest`) | #127 |
| Stream-decoder F1–F6 (seek-point collision, rapidgzip size/verify, feed budgets, `readall` pending_error, …) | #128 |
| PPMd `max_length` / after-eof / version pin product work | #124 / #130 (residual abort → Irreducible) |
| Gzip multi-member CRC omission from `hashes` | Earlier + tests; docs accurate |
| Cross-platform name safety implementation + threat-model prose sync | #109 / #123 + docs sweep |
| `format-7z` “never silent bytes” vs F2 diagnostic (P7) | docs sweep |
| `formats.md` RAR `-ver` / crypto notes | docs sweep |

---

## Suggested first cuts

1. **Why Archivey page** (next narrative doc): hardenings / why not wrap / why “large.”
2. Optional polish: `opening-and-listing.md` duplicate-name / hardlink pointers; fuller nested-archive
   recipe; one line in `extracting.md` on symlink-hostile FS.
