# QUESTIONS — maintainer decisions (merged)

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

## Q13 — `STREAM_REWIND_REDECOMPRESSES`, and the O-23 warning decision *(F12 · S3)*

**Both passes agree: flag, do not churn.** The code still describes the caller's seek
rather than a property of the archive, which is the O-23 rule; neither pass found a
cleaner cut. One datum if it is ever revisited: it is the only diagnostic whose trigger a
caller can eliminate entirely by passing a flag, which is plausibly what makes it read as
usage.

**The separate half O-23 left open** — whether to emit a plain `warnings.warn` on a solid
random `open()` — is verifiably still open: there is no `warnings.warn` anywhere in
`src/`, and `archive-reading:512` specifies the opposite ("no diagnostic, no warning").

- **Recommend:** record it as **decided-no**, and keep `STREAM_REWIND` for `0.2.0` with a
  taxonomy note. `VISION.md` argues against adding an ambient warning, and the cost
  receipt is the queryable channel the same paragraph asks for. Leaving it "undecided"
  guarantees the next review re-derives it.
- **Vehicle:** decision only — a line in `review/docs/observations.md` closing O-23.

---

## Q14 — two docs/spelling cleanups *(F13 + F14 · S3)*

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

## Ranked pay list, if the answer is "pick a few"

| Rank | Item | Why this order |
|---|---|---|
| 1 | **Q1** (F1 metadata gate) | Only finding touching a load-bearing VISION claim; a landed spec row is already wrong; freeze-cost is real |
| 2 | **Q5** (F5 `compressed_size`) | Smallest diff in the review, finishes an already-90%-done #225 sweep |
| 3 | **Q3** (F3 volume `ValueError`) | Cheap error-contract honesty; the inconsistent-spelling argument is unambiguous |
| 4 | **Q4** (F4 ZIP closed→corruption) | Actively misleads a caller about their data |
| 5 | **Q2** (F2 encoding policy) | One rule deletes a recurring failure mode — but needs the product call first |
| 6 | **Q6** (F6 directory report peek) | Spec and code disagree today; cheapest of the three if the answer is "fix the spec" |
| 7 | **Q9 + Q14** (docs) | Minutes each, and Q9's claim is currently false |

Q8 is the best next if the surface freeze is close — the only one whose cost genuinely
rises after the tag. Q7 is the most interesting product question and the least urgent.
Q10 splits into a free half and a follow-up. Q11 is a process decision. Q13 and Q16 are
confirmations. Q15 is one defensive line.
