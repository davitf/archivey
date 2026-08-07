# QUESTIONS — maintainer decisions (merged)

> ## Decisions — recorded 2026-08-07
>
> All sixteen were ruled on by the maintainer. This table is the authority; each
> section below carries the same ruling inline with its evidence.
>
> | # | Finding | Decision |
> |---|---|---|
> | Q1 | F1 metadata gate | **A** — decouple; harvest regardless of the flag |
> | Q2 | F2 `encoding=` discard | **C** — emit a diagnostic, do not raise |
> | Q3 | F3 volume `ValueError` | **A** — `ArchiveyUsageError` + `StreamNotSeekableError` |
> | Q4 | F4 ZIP closed→corruption | **A** — carve out `"already closed"` only |
> | Q5 | F5 `compressed_size` Path gate | **A** — fill from `SEEK_END` |
> | Q6 | F6 directory report peek | **A** — fix the spec |
> | Q7 | F7 wrong explicit `format=` | **A** — diagnostic on empty listing |
> | Q8 | F8 pipe capability | **A** — capability axis on `FormatAvailability` |
> | Q9 | F9 laziness docs | **Add the caveat** |
> | Q10 | F10 bidi warning | **Add `MEMBER_NAME_BIDI_CONTROL`** (+ tighten the spec clause) |
> | Q11 | F16 RAR CI | **A** — make the sweep runnable on one leg |
> | Q12 | F11 `open_stream(dir)` | **Split the predicate** |
> | Q13 | F12 `STREAM_REWIND` | **Reopened → worked.** Resolution + drafts in [`q13-rewind-diagnostic.md`](q13-rewind-diagnostic.md); spawned **F19** |
> | Q14 | F13 + F14 docs/imports | **Do both** |
> | Q15 | F15 `unrar` `RuntimeError` | **Map it** |
> | Q16 | C1 `seekable` vocabulary | **Revisit before the tag** (overrides the merged verdict) |
>
> **Two rulings go against this review's recommendation, deliberately** — Q13 and Q16.
> Both are recorded as-decided; see those sections for what the decision now requires.
>
> **One thing stayed open**: the O-23 sub-question in Q13 (whether to `warnings.warn`
> on a solid out-of-order `open()`). Still formally undecided, but Q13's working now
> carries a recommendation — **decided-no**, because solid open has a *better* open-time
> data signal (`cost.access_cost == SOLID`) than the rewind does, so if the rewind does
> not warrant an ambient warning, solid open certainly does not.
>
> **Q13 vindicated its override.** Working it produced a resolution neither pass reached
> and a new `CONFIRMED` finding (**F19**: the rewind predicate is silent for a degenerate
> seek index, so the `RAISE` tripwire is unreliable where it would be depended on).
>
> The review itself remains **analysis-only** — nothing below is implemented in this
> PR. The pay list at the foot of this file is re-ranked against these decisions.


Sixteen decisions, merged from the two independent passes (PR #230 and PR #231). Each
carries severity, the **fix vehicle** (which decides what is realistically payable before
the tag), and a recommendation. Ranked by severity × confidence, then freeze-cost —
where freeze-cost argues for fixing something *before* the tag, never for accepting it
now (`brief.md` §Hard constraints).

**Behaviour churn is free until `0.2.0`.** Nothing is on real PyPI. "This would be
breaking" is not an argument below.

Vehicles: **OpenSpec change** (cross-format contract move) · **bugfix PR** (red–green,
red half already committed) · **docs-only** · **decision only**.

Q1–Q10 are the ones that change code or contracts. Q11–Q16 are cheap or informational.

---

## Q1 — `seekable_members` changes metadata. Decouple it? *(F1 · S1 · CONFIRMED)*

> **DECIDED:** **A — decouple.** Harvest cheap trailer/index metadata at open regardless of `seekable_members` / `open_stream(seekable=)`; make xz and lzip behave like gzip. Fixes the unconditioned XZ spec row as a side effect.
>
> *Vehicle:* OpenSpec change + bugfix.

**The question.** Should `member.size` and `member.hashes` be independent of
`seekable_members` / `open_stream(seekable=)`?

**Today.** `.lz`: `size None → 44`, `hashes {} → {CRC32}`. `.xz`: `size None → 44`.
Identical for a `Path` and a seekable `BytesIO`, so this is a flag gate, not a
source-shape gate. gzip is already ungated and correct.

**Why it matters beyond tidiness.** `VISION.md` names dedupe as the founding use case
and "hashes without decompression where possible" as a priority. The plain
`open_archive(p)` — what a dedupe pass writes — gets no lzip CRC-32. And the XZ row of
`format-single-file-compressors` states its size rule with **no** seekability condition,
so the code diverges from a landed spec.

**The two passes disagreed here** (SUMMARY §D1). PR #231 filed the lzip half under
"what is actually fine" on the grounds that the gate is seekability, not `Path`. The
counter-argument is that a *caller capability flag* is a worse gate than a source shape,
not a better one — and #231 did not have the xz row or the unconditioned XZ spec text.

**Options.**

| | Option | Consequence |
|---|---|---|
| **A** *(recommended)* | Harvest cheap trailer/index metadata at open regardless of the flag — make xz and lzip look like gzip. | One bounded backward read on a source that is already seekable. Deletes the class; `docs/gotchas.md`'s `seekable_members` bullet becomes true *and* complete. |
| B | Keep the gate; fix the **XZ spec row** to state it, and rewrite the LZIP rows in caller terms rather than implementation terms ("through the seekable lzip backend"). | Cheapest. Documents an accident permanently, which §Values ranks below deleting it, and leaves a metadata field whose presence depends on an unrelated capability. |
| C | Split: harvest for `Path` sources only. | Re-introduces the Path gate #225/O-25 removed — and F5 shows one is still there. Not recommended. |

**Vehicle:** OpenSpec change + red–green bugfix. Red halves committed:
`test_member_size_does_not_depend_on_declared_seekability`,
`test_lzip_surfaces_crc32_without_declaring_seekable_members`.

**Recommendation: A, before the tag.** The only finding touching a load-bearing VISION
claim, and after the tag "size is `None` unless you pass the flag" becomes something
callers depend on.

---

## Q2 — refuse arguments a backend cannot honour? *(F2 · S2 · CONFIRMED · both passes)*

> **DECIDED:** **C — one diagnostic, do not raise.** Emit a diagnostic when `encoding=` is passed to a backend that ignores it (7z, RAR, ISO, directory, single-file). Keeps the uniform-passer working, makes the discard queryable per VISION. *Not* the general refuse-at-entry rule — the entry point stays permissive.
>
> *Vehicle:* OpenSpec change (diagnostics + a `format-*` note) + bugfix.

**The question.** `encoding=` is honoured by ZIP and TAR and silently discarded by 7z
(`sevenzip_reader.py:190` `del encoding`), RAR (`rar_reader.py:362` `del encoding`),
directory, ISO and single-file. Should an argument the resolved backend cannot act on be
refused at the entry point?

**Context.** `password=` on a non-encrypting format **is** refused, centrally, via
`ReadBackend.SUPPORTS_PASSWORD`. `encoding=` has no analogous gate. And #225/P8 already
decided the principle for directory `format=` — *"silently overruling it returns a reader
over the directory tree to a caller who asserted a different format"* — then applied it
to that one argument only.

| | Option | Consequence |
|---|---|---|
| **A** *(recommended)* | Adopt the general rule: **an explicit argument naming something the resolved backend cannot act on is refused at the entry point** (`ArchiveyUsageError`). | One rule replaces three special cases, and covers the next knob by construction. Needs a per-backend `SUPPORTS_ENCODING` declaration — small, and `SUPPORTS_PASSWORD` is exactly that shape already. Risk: surprises a caller who passes `encoding=` uniformly across mixed formats. |
| B | Document as ZIP/TAR-only and accept the silence. | Docs-only. Leaves the next argument to be decided again. |
| C | Emit one diagnostic instead of raising. | Soft middle; queryable per VISION; no breakage for the uniform-passer. |

**Nuance worth deciding explicitly:** for 7z (UTF-16 names) and single-file (name from
the filesystem) the *behaviour* is right — there is nothing to decode. **ISO is the
awkward one**: it has real encoding choices (Joliet UCS-2 vs Rock Ridge), the detector
already produces an `encoding_hint`, and the caller's explicit override is dropped
anyway. If only one backend changes, it should be ISO.

**Vehicle:** OpenSpec change + bugfix. Pins and a red half committed.

---

## Q3 — type the volume-sequence refusals *(F3 · S2 · CONFIRMED · PR #231)*

> **DECIDED:** **A — type both.** `ArchiveyUsageError` for an empty sequence; `StreamNotSeekableError` for non-seekable volumes, matching the single-source spelling exactly.
>
> *Vehicle:* bugfix PR (red halves committed).

`open_archive([])` and `open_archive([pipe, pipe])` both raise a bare `ValueError`.
`resolve_source` runs at `core.py:194`, before format resolution and before any backend
translator exists, so nothing on that path can type them.

The inconsistency is the point: the **single**-source spelling of the second refusal is
already a typed `StreamNotSeekableError` with a remediation sentence. Same caller
mistake, two different error contracts depending on how it was written.

- **Recommend:** map at `resolve_source` — `ArchiveyUsageError` for an empty sequence
  (caller misuse), `StreamNotSeekableError` for non-seekable volumes (capability
  refusal, matching the single-source path).
- **Vehicle:** bugfix PR. Red halves committed.
- **Pay before the tag?** Yes — cheap, and it is error-contract honesty, which
  `CONTRIBUTING.md` treats as a standing rule rather than a nice-to-have.

---

## Q4 — ZIP reports an already-closed handle as corruption *(F4 · S2 · CONFIRMED · PR #231)*

> **DECIDED:** **A — carve out `"already closed"` only.** Map it to `ArchiveyUsageError` ahead of the blanket arm. The rest of ZIP's `ValueError` → `CorruptionError` mapping stays as-is.
>
> *Vehicle:* bugfix PR (pin + red half committed).

Closing the underlying `ZipFile` while the reader is live yields
`CorruptionError: Corrupt ZIP member offset/structure: ValueError('Attempt to use ZIP
archive that was already closed')`. ZIP's translator maps **every** `ValueError` to
corruption; `ArchiveStream._fail` only special-cases the substring `"closed file"`, which
`"already closed"` does not match.

Not the settled case: a normal `reader.close()` then `open()` already raises
`ArchiveyUsageError` (`#225`). Only the underlying-handle path is wrong.

**Why it matters beyond taxonomy:** `CorruptionError` tells the caller their *archive* is
damaged. It is not — the handle is. That sends someone hunting a bad file.

| | Option | Consequence |
|---|---|---|
| **A** *(recommended)* | Carve `"already closed"` out ahead of the blanket arm → `ArchiveyUsageError`. | Narrow, obviously safe. |
| B | A, **plus** narrow ZIP's `ValueError` arm to known-corruption substrings. | ZIP's translator is by far the widest in the codebase (TAR catches 2 exception types; 7z/RAR catch 1). The blanket arm will mislabel the next lifecycle bug too. Slightly more risk of letting a real corruption `ValueError` through untranslated. |

- **Vehicle:** bugfix PR. Pin + red half committed. **Pay before the tag?** Yes.

---

## Q5 — single-file `compressed_size` Path gate *(F5 · S2 · CONFIRMED · PR #231)*

> **DECIDED:** **A — fill from `SEEK_END`** on any seekable source, mirroring the trailer/CRC probes on the same class.
>
> *Vehicle:* bugfix PR (pin + red half committed).

`member.compressed_size` is filled from a `Path` and `None` from a seekable `BytesIO`,
for **every** single-file codec. `single_file_reader.py:173` uses `os.path.getsize`
behind an `isinstance(..., Path)` check with no seekable fallback — while
`_with_seekable_source`, which already serves both shapes, sits a few lines below and
feeds the trailer/CRC probes.

This is the residual the `#225` Path/seekable sweep did not reach. It is also the reason
PR #230's "seed A2 is clean" verdict was wrong (SUMMARY §Corrections).

- **Recommend:** fill from `SEEK_END` on a seekable source, exactly as the CRC probe does.
- **Vehicle:** bugfix PR — the narrowest change in the review. Red half committed.
- **Pay before the tag?** Yes; it finishes a sweep that is already 90% done.

---

## Q6 — directory backend vs the index-topology table *(F6 · S2 · CONFIRMED · PR #230)*

> **DECIDED:** **A — fix the spec.** Drop `directory` from the leading-index row of the topology table; the code and `listing_cost=REQUIRES_SCANNING` are already self-consistent.
>
> *Vehicle:* OpenSpec change on `access-mode-and-cost`.

`access-mode-and-cost:96` lists **"Leading (directory, ISO) | Both modes, as complete
report"**. The directory backend returns `None` from `members_report_if_available()` in
both modes. Spec or code?

**This is a pause-and-ask, not a reviewer call** (`CONTRIBUTING.md`) — the two fixes are
different products.

| | Option | Consequence |
|---|---|---|
| **A** *(recommended)* | **Fix the spec:** drop `directory` from the leading-index row. | Honest, cheap, already consistent with the directory backend's `listing_cost=REQUIRES_SCANNING`. A live filesystem tree has no index at the front; grouping it with ISO was a categorization slip. |
| B | **Fix the code:** materialize the tree on open so the peek works. | Buys the peek at the cost of an eager `os.walk` on every open — and then `listing_cost` must become `INDEXED` too, or the receipt starts lying. Worse for large trees, which is the case that most wants a peek. |

**Coupling to watch either way:** `listing_cost` and the topology table must end up
telling one story. **Vehicle:** OpenSpec change (A) or bugfix + spec touch (B).

---

## Q7 — must an asserted-but-wrong `format=` fail loudly? *(F7 · S2 · CONFIRMED · PR #230)*

> **DECIDED:** **A — diagnostic, not refusal.** When an explicit `format=` yields an empty listing and magic detection would have said something else, emit a diagnostic. `format=` stays an override.
>
> *Vehicle:* OpenSpec change + bugfix.

`open_archive(iso, format=ArchiveFormat.TAR)` opens, reports `format=TAR`, and lists
**zero members** with no error and no diagnostic. `strict_archive_eof=True` does not
catch it. Every other wrong-format pairing fails loudly, and #225 made the directory case
an `ArchiveyUsageError`.

The TAR reading is genuinely correct — an ISO's 32 KiB zero-filled system area *is* a
valid empty TAR — so this is not a TAR bug. The question is whether `open_archive` should
notice.

| | Option | Consequence |
|---|---|---|
| **A** *(recommended)* | Emit a **diagnostic** when an explicit `format=` yields an empty listing and magic detection would have said something else. | Keeps "the caller asserted it, we honour it" while making the wrong answer visible. Fits "differences are data". Costs one detection peek on a path callers rarely take. |
| B | Refuse: run detection even when `format=` is given; raise on a confident disagreement. | Strongest, closest to the #225 reasoning. But it makes `format=` stop being an override, which is what some callers use it *for* — wrong extensions are normal (`VISION.md`). |
| C | Accept and document as a Gotchas bullet. | Cheapest, and the option §Values ranks lowest: an accident documented is a permanent docs tax. |

**Vehicle:** OpenSpec change + bugfix. Pin + red half committed.

**If B is chosen,** `strict_archive_eof` deserves a second look — it is documented as the
knob for "I need a provably complete listing" and does not fire here.

---

## Q8 — make pipe capability queryable before the surface freezes? *(F8 · S2 · CONFIRMED · PR #230)*

> **DECIDED:** **A — add a capability axis to `FormatAvailability`**, reusing the existing `StreamCapability` vocabulary rather than inventing a second one. Decided before the dataclass freezes.
>
> *Vehicle:* OpenSpec change + implementation.

Whether a format can be read from a non-seekable source is not exposed.
`FormatAvailability` carries `format` / `support` / `missing`; the fact lives on
`ReadBackend.SUPPORTS_STREAMING_NON_SEEKABLE`, which is `internal/`.

The *behaviour* is fine — one typed error with one message shape, from both
`open_archive` and `extract()`. This is purely about VISION's "behaviour differences
between formats are **data**".

**Freeze-cost is the argument for now:** `FormatAvailability` is public and its field set
freezes at `0.2.0`. Adding a field later is additive, but the shape question (one bool? a
capability set?) gets harder once callers pattern-match the dataclass.

| | Option | Consequence |
|---|---|---|
| **A** *(recommended)* | Add a source-shape capability to `FormatAvailability`, reusing the existing `StreamCapability` vocabulary rather than inventing a second one. | Callers can write "pipe it if you can, else buffer" without try-and-catch. Decided once. |
| B | A separate module-level helper (`can_stream_from_pipe(fmt)`). | Avoids touching the frozen dataclass; adds a second place to look. |
| C | Accept: try-and-catch is a fine idiom and the message already says what to do. | Defensible — the message is genuinely good. But it is the one place a format difference is an exception rather than data. |

**Vehicle:** OpenSpec change + implementation. Pins committed.

---

## Q9 — the guide says passwords are lazy; header encryption is not *(F9 · S2 · CONFIRMED · PR #231)*

> **DECIDED:** **Add the caveat.** One sentence on `docs/reading-members.md`'s laziness bullet bounding it to *data* encryption, pointing at `formats.md` for the header cases.
>
> *Vehicle:* docs-only (coordinate with Topic 8).

`docs/reading-members.md:74–77`:

> **Nothing is decompressed until you read.** A member you skip is never opened, and no
> password is requested for it.

True for data encryption. **False** for a header-encrypted archive: 7z `-mhe` and RAR
with encrypted headers both raise `EncryptionError` at `open_archive()`, because the
listing itself is ciphertext. The loop that bullet describes never gets to exist.
`docs/formats.md` already documents the header cases; the two pages disagree.

- **Recommend:** one caveat sentence on the laziness bullet, pointing at `formats.md`.
  Format law, so nothing to fix in code.
- **Vehicle:** docs-only. Coordinate with Topic 8. Guardrails committed both ways.
- **Pay before the tag?** Yes — it is one sentence and the claim is currently wrong.

---

## Q10 — the bidi/RTL warning is the only advisory that is not data *(F10 · S2 · CONFIRMED)*

> **DECIDED:** **Add the diagnostic.** Promote the bidi/RTL warning to a `MEMBER_NAME_BIDI_CONTROL` `DiagnosticCode` with a context dataclass, so it is queryable and escalatable like every other advisory. The `testing-contract` clause is tightened to what ships as part of the same change.
>
> *Vehicle:* diagnostics change + OpenSpec change on `testing-contract`.

Two things, and the second is the rankable one.

**(a) The spec clause is vague.** `testing-contract:55` says "RTL warns **or** rejects",
and the scenario says "rejected **or** exactly one warning". The code
(`naming.py:38–53`) only warns. PR #231 filed this as spec fiction; strictly it is not —
the clause is a disjunction and the code implements one branch. But a conformance spec
that does not say which outcome ships is not doing its job. **Tighten it** to "warns
exactly once via `logger`; null bytes reject as traversal."

**(b) The warning has no `DiagnosticCode`** — the only advisory in the library without
one. `VISION.md`: *"a logging warning most applications never see is a surprise deferred,
not avoided."* Its own neighbour in the same helper (name normalization) has
`MEMBER_NAME_NORMALIZED`. So this is an omission, not a policy — and it is the security-
flavoured advisory (RTL-override filename disguise), i.e. the one a caller would most
want to query and escalate via `DiagnosticPolicy`.

| | Option | Consequence |
|---|---|---|
| **A** *(recommended)* | Do (a) now — spec tightening, ~free. Add a `MEMBER_NAME_BIDI_CONTROL` diagnostic code as a small follow-up. | Makes the advisory surface uniform for the first time. |
| B | (a) only. | Honest spec, gap stays. |
| C | Neither. | Not recommended — the clause actively misinforms. |

**Vehicle:** OpenSpec change on `testing-contract` (landed capability), then optionally a
diagnostics change. Guardrail committed.

---

## Q11 — the RAR corpus sweep runs nowhere *(F16 · S2 · deliberate · PR #230)*

> **DECIDED:** **A — close the hole.** Make the RAR conformance sweep runnable on at least one CI leg (platform-independent digest expectations, or a committed fixture set).
>
> *Vehicle:* CI/testing change.

**Not a defect** — surfaced because a review that silently omits it would be the third to
rediscover it.

`.github/workflows/ci.yml:187` installs `unrar` only and on macOS deletes the `rar`
writer the cask ships ("keep writer off the PATH here"), because the RAR corpus fixtures'
digest expectations are Linux-fixture-oriented. `scripts/setup-dev-env.sh:118` verifies
`unrar` and `7z`, not `rar`. Consequence: the **41 RAR cases of the cross-format
conformance sweep run on no CI leg and in no provisioned dev environment**.

RAR *reading* is still exercised — committed fixtures under `tests/fixtures/rar/` cover
open/list/hashes/encrypted-headers, and that is how the second pass filled its RAR
matrix column. What is unexercised is the RAR column of the **declarative corpus** net
that `VISION.md` §Quality scaffolding describes.

**The question:** is that still the intended trade-off before a `0.2.0` that headlines a
native RAR reader?

| | Option | Consequence |
|---|---|---|
| **A** *(recommended)* | Make the RAR fixtures' digest expectations platform-independent (or commit a small pre-built set) so one CI leg runs the RAR sweep. | Closes the hole in the net meant to catch "backend X broke shape Y". Costs either committed binaries or a fixture rework. |
| B | Keep as-is and record the decision in `testing-contract`, so the next reader finds the decision instead of the symptom. | Free, honest, does not close the hole. |

**Vehicle:** decision, then a CI/testing change or a `testing-contract` note.

---

## Q12 — `open_stream` on a directory *(F11 · S3 · CONFIRMED · PR #230)*

> **DECIDED:** **Split the predicate.** `FileNotFoundError` for genuinely missing paths; `ArchiveyUsageError` naming `open_archive` for a directory.
>
> *Vehicle:* bugfix PR (red half committed).

`open_stream(directory)` raises `FileNotFoundError("Compressed stream not found: …")` for
a path that exists, while `open_archive(same_path)` opens it. `core.py:332`
(`if not path.is_file()`) collapses "absent" and "is a directory" and asserts the false
one.

- **Recommend:** split the predicate. Keep `FileNotFoundError` for genuinely missing
  paths (matches `error-handling`'s "filesystem `OSError` propagates unchanged"); raise
  `ArchiveyUsageError` naming `open_archive` for a directory — the caller is one function
  call away from what they want.
- **Vehicle:** bugfix PR, no spec change. Red half committed.

---

## Q13 — `STREAM_REWIND_REDECOMPRESSES`, and the O-23 warning decision *(F12)*

> **DECIDED:** **Reopen placement** — and it was then worked through. Full resolution,
> evidence and drafts: **[`q13-rewind-diagnostic.md`](q13-rewind-diagnostic.md)**.
>
> *Outcome:* the emission site **stays**; its stated job becomes the `DiagnosticPolicy`
> `RAISE` tripwire, not informing. The defect is in the **O-23 rule**, which is
> under-evidenced — the *extraction* codes do not fit its wording either, so
> `STREAM_REWIND` was never the sole exception. Three drafts are ready: the
> `seekable_members` docstring (which names the gate but not the trap), the reframed rule
> plus a 14-code audit, and a normative admission rule for `diagnostics`.
>
> *Rejected along the way:* moving the informational half to `CostReceipt.notes` — a cost
> note is exactly as unread as a diagnostic, and would populate a dead public field that
> then freezes.
>
> *Spawned:* **F19**, a new `CONFIRMED` finding — the predicate is codec identity, so a
> single-block `.xz` re-decodes its whole stream on a backward seek and emits nothing,
> `RAISE` included. Behaviour change; separate OpenSpec change; does **not** block the
> drafts.
>
> *Still open:* the solid-open `warnings.warn` sub-question, with a **decided-no**
> recommendation attached.
>
> *Vehicle:* docs-only + observation edit + OpenSpec change on `diagnostics` (drafts
> ready) · F19 separately on `seekable-decompressor-streams` + bugfix.

**Both passes agreed** the code should be flagged and not churned, precisely because
neither found a cleaner cut. Reopening was the right call anyway: the cleaner cut was not
in the code.

**The reopening argument** (maintainer): reaching this event already requires
`seekable_members=True`, which is documented, so a diagnostic ends up invisible. Correct —
and structurally so. `VISION.md`'s warnings-as-data argument is about *honesty* signals,
things that change whether you trust the bytes. A rewind changes only how long it took,
so nobody polls `reader.diagnostics` for it. What survives is the tripwire.

**What that leaves.** A tripwire is supposed to fire on what the caller did, so the O-23
awkwardness largely dissolves; `from_offset`/`to_offset` become the useful payload rather
than noise. And the rule itself needs fixing regardless, because its own enumeration
omitted the extraction codes.

**The wrinkle that produced F19** (maintainer): whether a backward seek requires
re-decompression is not binary — bounded for accelerators, always true for some codecs,
index-dependent for others. Measured answer: the predicate is codec identity decided at
open, `DecompressorStream` already computes the honest quantity
(`target - nearest_seek_point_before(target)`), and there is already precedent for a cost
threshold in `_rapidgzip_rewind_warning`. Full working in the linked file.

---

## Q14 — two docs/spelling cleanups *(F13 + F14 · S3)*

> **DECIDED:** **Do both.** Fix `must-explain` #25 to match `#225` (wording: "rejected **for a directory path**", not "always rejected" — F7 is the case where a wrong `format=` still wins), and switch the two CLI imports to the public path.
>
> *Vehicle:* docs-only + 2-line import change.

Bundled because they are minutes and neither has a design question.

1. **F13** — `must-explain.md:331–335` still says a directory path forces `DIRECTORY`
   "even if `format=` says otherwise". #225 made that an `ArchiveyUsageError`. Note when
   rewriting: the replacement should say "rejected **for a directory path**", not
   "always rejected" — F7 is the case where a wrong `format=` still wins silently.
2. **F14** — `cli/progress.py:10` and `cli/test_cmd.py:22` import `ExtractionProgress`
   from `archivey.internal.extraction_types`; `cli/extract_cmd.py` uses the public path.
   The type is in `archivey.__all__`. Pattern is **isolated** — both passes confirmed the
   CLI reaches into `internal/` for nothing else.

- **Vehicle:** docs-only + a 2-line import change. **Pay before the tag?** Yes, trivially.

---

## Q15 — RAR stdout-pipe `RuntimeError` *(F15 · S3 · PLAUSIBLE)*

> **DECIDED:** **Map it.** Translate the `unrar` stdout-pipe `RuntimeError` in the RAR translator or at the spawn site.
>
> *Vehicle:* bugfix PR.

`rar_unrar.py:157` raises a raw `RuntimeError("unrar produced no stdout pipe")` from call
sites outside `_translated_errors`; the RAR translator returns `None` for anything but
`EOFError`, so it would escape untyped.

The passes disagreed mildly: unreachable in practice with
`subprocess.Popen(..., stdout=PIPE)` (so "note it") versus one line to close (so "map
it"). No repro exists — the condition cannot be provoked.

- **Recommend:** map it in the RAR translator or at the spawn site. It is defensive, it
  is one line, and "unreachable" arguments have a poor track record.
- **Vehicle:** bugfix PR. **Pay before the tag?** Nice-to-have.

---

## Q16 — confirm the `seekable_members` / `seekable` split is still wanted *(D2 · informational)*

> **DECIDED:** **Revisit the naming before the tag** — *reversing* this review's merged verdict. PR #231's reading wins: the split is treated as a live pre-freeze question, not as settled by spec. Because `archive-reading` §"Declared member-stream capabilities" currently **mandates** the present spelling, any rename or alias requires changing that requirement first.
>
> *Vehicle:* OpenSpec change on `archive-reading`, then implementation.

The two passes disagreed (SUMMARY §D2): one filed it as a live pre-freeze vocabulary
question, the other found that `archive-reading` §"Declared member-stream capabilities"
already decides it —

> `open_stream` SHALL keep its `seekable: bool` parameter, and both entry points SHALL
> use the same `seekable` vocabulary for the same concept; concurrency has no meaning for
> a single standalone stream, so `open_stream` MUST NOT gain a concurrency parameter.

The code matches the spec exactly. So this is not an open design question — but since
renaming is free until the tag and the spec is the only thing making it settled, it is
worth one confirmation that the spec still says what you want.

- **Recommend:** confirm and move on. *(The real problem with the flag is F1, not its
  name.)*
- **Vehicle:** decision only.

---

## Also decided, no action

**F18 — CLI defaults vs library defaults.** Overwrite `rename` vs `ERROR`;
`OnError.CONTINUE` vs `STOP`; smart anti-tarbomb dest. Authority:
`review/archive/2026-07-20-cli-product/QUESTIONS.md` Q1 — a deliberate product split for
the safer-`unzip` demo, recorded as must-explain #23. Both passes confirmed the CLI
constructs explicit arguments rather than carrying a shadow `ArchiveyConfig`. **Accept**;
just keep `docs/cli.md` loud about it.

**F17 — concept count.** A signal, not a defect (see `vocabulary.md`). Recorded as the
review's only before/after metric.

---

## Pay list, re-ranked against the decisions

Every question was answered, so this is no longer a "pick a few" list — it is the work
the decisions imply, ordered by what unblocks what. **None of it is implemented in this
PR**; the review stays analysis-only until each item gets its own change.

### Tier 1 — bugfix PRs, red halves already committed

| # | Work | Notes |
|---|---|---|
| 1 | **Q5 / F5** — fill `compressed_size` from `SEEK_END` on any seekable source | Smallest diff in the review; finishes the `#225` Path/seekable sweep |
| 2 | **Q3 / F3** — type the volume-sequence refusals | Two mappings in `resolve_source` |
| 3 | **Q4 / F4** — carve `"already closed"` out of ZIP's `ValueError` arm | One condition, ahead of the blanket arm |
| 4 | **Q12 / F11** — split `open_stream`'s `is_file()` predicate | `core.py:332` |
| 5 | **Q15 / F15** — map the `unrar` stdout-pipe `RuntimeError` | One line, defensive |

Each flips its `xfail(strict=True)` red half to XPASS, which fails the suite until the
marker is removed — that is the signal the fix landed.

### Tier 2 — needs an OpenSpec change first

| # | Work | Spec touched |
|---|---|---|
| 6 | **Q1 / F1** — decouple metadata harvest from `seekable_members` | `format-single-file-compressors` (the XZ row is already wrong) |
| 7 | **Q8 / F8** — capability axis on `FormatAvailability`, reusing `StreamCapability` | `backend-registry` / `packaging-and-extras` — do before the surface freezes |
| 8 | **Q2 / F2** — diagnostic when `encoding=` is passed to a backend that ignores it | `diagnostics` + the affected `format-*` specs |
| 9 | **Q7 / F7** — diagnostic when an explicit `format=` yields an empty listing | `archive-reading` / `format-detection` |
| 10 | **Q10 / F10** — `MEMBER_NAME_BIDI_CONTROL` diagnostic, and tighten the RTL clause | `diagnostics` + `testing-contract` |

Q2, Q7 and Q10 all add diagnostic codes; **worth landing as one `diagnostics` change**
rather than three, since they share the taxonomy and the policy plumbing.

### Tier 3 — spec-only, docs-only, and process

| # | Work | Vehicle |
|---|---|---|
| 11 | **Q6 / F6** — drop `directory` from the leading-index row | OpenSpec change on `access-mode-and-cost`. Flips its red half; if that XPASSes *before* the change lands, the code moved instead — check which. |
| 12 | **Q9 / F9** — caveat the laziness bullet in `reading-members.md` | docs-only; the published claim is currently false |
| 13 | **Q14 / F13 + F14** — `must-explain` #25, and the two CLI imports | docs-only + 2-line import change |
| 14 | **Q11 / F16** — make the RAR conformance sweep runnable on one CI leg | CI/testing change; touches `ci.yml`, possibly the RAR fixtures |

### Tier 4 — design work the decisions opened

Two rulings went against the review's recommendation and therefore create work rather
than closing it:

| # | Work | Why it is design, not a fix |
|---|---|---|
| 15 | ~~**Q13 / F12**~~ — **done**: worked through in [`q13-rewind-diagnostic.md`](q13-rewind-diagnostic.md). Three drafts ready (docstring / O-23 reframe + audit / `diagnostics` admission rule), no behaviour change. Moved to Tier 3 in practice. | — |
| 15b | **F19** — replace the rewind predicate with the seek's re-decode distance | Genuine design work, and the one item here that is a *behaviour* change. Open sub-questions in the linked file: threshold shape (absolute vs relative), whether "once per stream" still holds under a cost-based predicate, and whether `rapidgzip` exposes index spacing. OpenSpec change on `seekable-decompressor-streams` + bugfix; guardrails already committed. |
| 16 | **Q16 / C1** — revisit `seekable_members` vs `open_stream(seekable=)` | `archive-reading` §"Declared member-stream capabilities" currently **mandates** the present spelling, so the spec has to change *before* any rename or alias. Free until the tag; not free after. |

**Still undecided, and it will resurface unless written down:** the O-23 sub-question
inside Q13 — whether a solid out-of-order `open()` should emit a plain `warnings.warn`.
Code and spec agree on "no warning" today and `VISION.md` argues against adding one, but
that has never been recorded as a decision.
