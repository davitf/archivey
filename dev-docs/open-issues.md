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

### P10. `format_availability()` fabricates a verdict for a wrong-typed argument

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
- **Open question:** raise `ArchiveyUsageError` for an argument that is not an
  `ArchiveFormat`; or accept `StreamFormat` and resolve it to its `RAW_STREAM` pair, on the
  symmetry argument that `open_stream` already takes both; or leave it to the type checkers
  and close this as won't-fix. The first is cheapest and matches ADR 0012. The second is a
  public-surface widening and should not be taken casually pre-`0.2.0`. The third is
  defensible precisely because the checkers do catch it — and is the option the original
  filing hid by overstating the problem.
- **Severity, honestly:** low as a defect, moderate as an *honesty* question — a public
  query that invents an answer is the shape `VISION.md` rules out, even when the caller was
  wrong to ask that way.
- **Provenance:** found 2026-08-17 reconciling the baselines of two Topic 8 step-2/3 passes
  (#246, #247). Recorded in
  [`review/docs-content/claims.md`](../review/docs-content/claims.md) Part 1. Not a docs
  defect: no published page passes a `StreamFormat` to this call.

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

### P10. `format_availability()` answers for a public type it does not know

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
