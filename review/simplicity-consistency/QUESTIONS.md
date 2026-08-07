# QUESTIONS — maintainer decisions

Nine decisions. Each carries severity, the **fix vehicle** (which decides what is
realistically payable before the tag), and a recommendation. Ranked by
severity × confidence, then freeze-cost — where freeze-cost argues for fixing
something *before* the tag, never for accepting it now (`brief.md` §Hard constraints).

**Behaviour churn is free until `0.2.0`.** Nothing is on real PyPI. "This would be
breaking" is not an argument below.

Vehicles: **OpenSpec change** (cross-format contract move) · **bugfix PR** (red–green,
red half already committed) · **docs-only** · **decision only**.

---

## Q1 — `seekable_members` changes metadata. Decouple it? *(P1 · S1 · CONFIRMED)*

**The question.** Should `member.size` and `member.hashes` be independent of
`seekable_members` / `open_stream(seekable=)`?

**Today.** `.lz`: `size None → 44`, `hashes {} → {CRC32}`. `.xz`: `size None → 44`.
Identical for a `Path` and a seekable `BytesIO`, so this is a flag gate, not a
source-shape gate. gzip is already ungated and correct.

**Why it matters beyond tidiness.** `VISION.md` names dedupe as the founding use case
and "hashes without decompression where possible" as a priority. The plain
`open_archive(p)` — what a dedupe pass writes — gets no lzip CRC-32. And the XZ row of
`format-single-file-compressors` states the size rule with **no** seekability condition,
so the code diverges from a landed spec.

**Options.**

| | Option | Consequence |
|---|---|---|
| **A** *(recommended)* | Harvest cheap trailer/index metadata at open regardless of the flag — make xz and lzip look like gzip. | One bounded backward read on a source that is already seekable. Deletes the whole class; `docs/gotchas.md`'s `seekable_members` bullet becomes true *and* complete. |
| B | Keep the gate; fix the **XZ spec row** to state it, and rewrite the LZIP rows in caller terms rather than implementation terms ("through the seekable lzip backend"). | Cheapest. But it documents an accident permanently, which §Values explicitly ranks below deleting it, and leaves a metadata field whose presence depends on an unrelated capability. |
| C | Split the difference: harvest for `Path` sources only. | Re-introduces exactly the Path-gate that #225/O-25 removed. Not recommended. |

**Vehicle:** OpenSpec change (it moves a cross-format metadata contract) + a red–green
bugfix. Red half committed:
`test_member_size_does_not_depend_on_declared_seekability`,
`test_lzip_surfaces_crc32_without_declaring_seekable_members`.

**Recommendation: A, before the tag.** It is the only finding here that touches a
load-bearing VISION claim, and after the tag "size is None unless you pass the flag"
becomes something callers depend on.

---

## Q2 — directory backend vs the index-topology table: which side is wrong? *(P2 · S2 · CONFIRMED)*

**The question.** `access-mode-and-cost` lists "Leading (directory, ISO) | Both modes,
as complete report". The directory backend returns `None` from
`members_report_if_available()` in both modes. Spec or code?

**This is a pause-and-ask, not a reviewer call** (`CONTRIBUTING.md`) — the two fixes are
different products.

| | Option | Consequence |
|---|---|---|
| **A** *(recommended)* | **Fix the spec:** drop `directory` from the leading-index row. | Honest, cheap, and already consistent with the directory backend's `listing_cost=REQUIRES_SCANNING`. A live filesystem tree has no index at the front; grouping it with ISO was a categorization slip. |
| B | **Fix the code:** materialize the tree on open so the peek works. | Buys the peek at the cost of an eager `os.walk` on every open — and then `listing_cost` must change to `INDEXED` too, or the receipt starts lying. Worse for large trees, which is the case that most wants a peek. |

**Coupling to watch either way:** `listing_cost` and the topology table must end up
telling one story.

**Vehicle:** OpenSpec change (A) or bugfix + spec touch (B).
Red half committed: `test_directory_report_peek_matches_index_topology_spec`.

---

## Q3 — must an asserted-but-wrong `format=` fail loudly? *(P3 · S2 · CONFIRMED)*

**The question.** `open_archive(iso, format=ArchiveFormat.TAR)` opens, reports
`format=TAR`, and lists **zero members** with no error and no diagnostic —
`strict_archive_eof=True` does not catch it. Every other wrong-format pairing fails
loudly, and #225 made the directory case an `ArchiveyUsageError`.

The TAR reading is genuinely correct (an ISO's 32 KiB zero-filled system area *is* a
valid empty TAR), so this is not a TAR bug. The question is whether `open_archive`
should notice.

| | Option | Consequence |
|---|---|---|
| **A** *(recommended)* | Emit a **diagnostic** when an explicit `format=` yields an empty listing and magic detection would have said something else. | Keeps "the caller asserted it, we honour it" while making the wrong answer visible. Fits the "differences are data" rule. Costs one detection peek on a path callers rarely take. |
| B | Refuse: run detection even when `format=` is given, and raise on a confident disagreement. | Strongest, and closest to the #225 reasoning. But it makes `format=` stop being an override, which is what some callers use it *for* (wrong extensions are normal — `VISION.md`). |
| C | Accept and document as a Gotchas bullet. | Cheapest, and the one §Values ranks lowest: an accident documented is a permanent docs tax. |

**Vehicle:** OpenSpec change (it changes what `format=` means) + bugfix.
Pin committed: `test_wrong_explicit_format_on_iso_yields_an_empty_listing`;
red half: `test_wrong_explicit_format_does_not_silently_succeed`.

**Note.** If B is chosen, `strict_archive_eof` deserves a second look — it is documented
as the knob for "I need a provably complete listing" and does not fire here.

---

## Q4 — refuse arguments a backend cannot honour? *(P4 · S2 · CONFIRMED)*

**The question.** `encoding=` is honoured by ZIP and TAR, silently discarded by ISO, 7z,
directory and single-file. Should an argument the resolved backend cannot act on be
refused at the entry point?

**Context.** #225/P8 already decided this for directory `format=` — *"silently
overruling it returns a reader over the directory tree to a caller who asserted a
different format"* — and the rule was applied to that one argument only.

| | Option | Consequence |
|---|---|---|
| **A** *(recommended)* | Adopt the general rule: **an explicit argument naming something the resolved backend cannot act on is refused at the entry point** (`ArchiveyUsageError`). | One rule replaces three special cases. Covers `encoding=` now and any future knob by construction. Needs a per-backend "do you consume encoding?" declaration — small, and the registry already carries `SUPPORTS_PASSWORD` in exactly this shape. |
| B | Refuse for ISO only (the one backend with real encoding choices) and document the rest as inert. | Half a rule. Leaves the next argument to be decided again. |
| C | Accept and document. | See Q3/C. |

**Nuance worth deciding explicitly:** ISO is the case where the discard is most
surprising — it *has* encoding choices (Joliet UCS-2 vs Rock Ridge) and the detector
already produces an `encoding_hint` — so if only one backend changes, it should be ISO.

**Vehicle:** OpenSpec change (a cross-format entry-point rule) + bugfix.
Pins committed: `test_encoding_argument_is_applied`,
`test_encoding_argument_is_silently_discarded`; red half:
`test_unusable_encoding_argument_is_refused`.

---

## Q5 — make pipe capability queryable before the surface freezes? *(P5 · S2 · CONFIRMED)*

**The question.** Whether a format can be read from a non-seekable source is not
exposed. `FormatAvailability` carries `format` / `support` / `missing`; the fact lives on
`ReadBackend.SUPPORTS_STREAMING_NON_SEEKABLE`, which is `internal/`.

The *behaviour* is fine — the refusal is one typed error with one message shape, from
both `open_archive` and `extract()`. This is purely about VISION's "behaviour
differences between formats are **data**".

**Freeze-cost is the argument for doing it now:** `FormatAvailability` is public and its
field set freezes at `0.2.0`. Adding a field later is additive but the shape question
(one bool? a capability set?) gets harder once callers pattern-match the dataclass.

| | Option | Consequence |
|---|---|---|
| **A** *(recommended)* | Add a source-shape capability to `FormatAvailability`, reusing the existing `StreamCapability` vocabulary rather than inventing a second one. | Callers can write "pipe it if you can, else buffer" without try-and-catch. One field, decided once. |
| B | Expose it as a separate module-level helper (`can_stream_from_pipe(fmt)`). | Avoids touching the frozen dataclass; adds a second place to look. |
| C | Accept: try-and-catch is a fine idiom, and the error message already says what to do. | Defensible — the message is genuinely good. But it is the one place the review found where a format difference is an exception rather than data. |

**Vehicle:** OpenSpec change (public surface) + implementation.
Pins committed: `test_trailing_index_formats_refuse_a_pipe_loudly`,
`test_front_indexed_formats_accept_a_pipe`.

---

## Q6 — `open_stream` on a directory *(P6 · S3 · CONFIRMED)*

`open_stream(directory)` raises `FileNotFoundError("Compressed stream not found: …")`
for a path that exists, while `open_archive(same_path)` opens it. One predicate
(`core.py:332` `if not path.is_file()`) collapses "absent" and "is a directory", and the
message asserts the false one.

**Recommendation:** split the predicate. Keep `FileNotFoundError` for genuinely missing
paths (it matches `error-handling`'s "filesystem `OSError` propagates unchanged"), and
raise `ArchiveyUsageError` naming `open_archive` for a directory — the caller is one
function call away from what they want.

**Vehicle:** bugfix PR, no spec change. Red half committed:
`test_open_stream_directory_error_names_the_real_problem`.

---

## Q7 — the O-23 `warnings.warn` decision, still open *(P8 · decision only)*

O-23 left explicitly undecided whether a plain `warnings.warn` should fire on a random
`open()` into a solid block. Verified: **there is no `warnings.warn` call anywhere in
`src/archivey/`**, and `archive-reading` currently specifies the opposite —
*"no diagnostic, no warning — discoverable via `reader.cost.access_cost` and the
`open()` docstring"*.

So the code and the spec already agree on "no warning". The only open item is whether
that is the *decision* or the *default*.

**Recommendation:** record it as decided-no. `VISION.md` is explicit that "a logging
warning most applications never see is a surprise deferred, not avoided", which argues
against adding one; and the cost receipt is the queryable channel the same paragraph
asks for. If it stays undecided it will be re-derived by the next review.

**Vehicle:** decision only (a line in `review/docs/observations.md` closing O-23).

---

## Q8 — `STREAM_REWIND_REDECOMPRESSES` on the usage side *(flagged, not churned)*

Per the brief: flagged only. The code still describes the caller's usage (they seeked
backwards) rather than a property of the archive, which is the O-23 rule. The review
found **no cleaner cut** and recommends no change.

One datum if it is ever revisited: it is the only diagnostic whose trigger a caller can
eliminate entirely by passing a flag, which is plausibly what makes it read as usage.

**Vehicle:** none proposed.

---

## Q9 — the RAR column of the conformance sweep runs nowhere *(P9 · S2 · deliberate)*

**Not a defect** — surfaced because a review that silently omits it would be the third
one to rediscover it.

`.github/workflows/ci.yml:187` installs `unrar` only, and on macOS actively deletes the
`rar` writer the cask ships ("keep writer off the PATH here"), because the RAR corpus
fixtures' digest expectations are Linux-fixture-oriented.
`scripts/setup-dev-env.sh:118` verifies `unrar` and `7z` and not `rar`. The consequence:
the **41 RAR cases of the cross-format conformance sweep run on no CI leg and in no
provisioned dev environment**, so the RAR column of the regression net
`VISION.md` §Quality scaffolding describes is unexercised.

**The question:** is that still the intended trade-off before a public `0.2.0` that ships
a native RAR reader as a headline feature?

| | Option | Consequence |
|---|---|---|
| **A** *(recommended)* | Commit a small set of pre-built RAR fixtures (or make the digest expectations platform-independent) so one CI leg runs the RAR sweep. | Closes the hole in the one net that is supposed to catch "backend X broke shape Y". Costs committed binaries, which the corpus deliberately avoids today. |
| B | Keep as-is and say so in `testing-contract`, so the next reader finds the decision instead of the symptom. | Free, honest, and does not close the hole. |

**Vehicle:** decision, then either a CI/testing change or a `testing-contract` spec note.
Documented by `test_rar_column_is_unmeasured_without_the_rar_writer`.

---

## Ranked pay list, if the answer is "pick three"

| Rank | Item | Why this order |
|---|---|---|
| 1 | **Q1** (P1 metadata gate) | Only finding touching a load-bearing VISION claim; a landed spec row is already wrong; freeze-cost is real |
| 2 | **Q4** (refuse unusable arguments) | One rule deletes a recurring failure mode and pre-answers the next instance — the §Values "deletes a failure class" bar |
| 3 | **Q2** (directory report peek) | A spec and the code disagree today; cheapest of the three if the answer is "fix the spec" |

Q5 is the best fourth if the surface freeze is close, since it is the only one whose
cost genuinely rises after the tag. Q3 is the most interesting product question but the
least urgent. Q6 and Q7 are minutes. Q8 is a no-op. Q9 is a process decision, not a
code one.
