# Open work inventory and sequencing

> **State now lives in Linear; this page keeps the reasoning.** As of 2026-09-17 the work
> below is tracked as issues on the `Archivey` Linear team — one issue per unit of work that
> can be handed out, each linking back here. **Linear answers "is this started, and did a PR
> close it?"; this page and the registers answer "why".** When the two disagree about state,
> Linear wins; when they disagree about reasoning, the registers win. GitHub issues are
> deliberately not used for internal tracking — they stay clear for external reports.
>
> **A dated snapshot, not a register.** Snapshot: **2026-09-19** against `main` @ `4ef5c98`.
> Every item below lives somewhere canonical — [`open-issues.md`](open-issues.md),
> [`threat-model.md`](threat-model.md), [`IDEAS.md`](IDEAS.md),
> [`review/backlog.md`](../review/backlog.md), [`review/STATUS.md`](../review/STATUS.md),
> the `§10` register on [`formats/rar.md`](formats/rar.md), or an `openspec/changes/`
> directory. This page adds the one thing none of them can carry: **what blocks what, across
> registers**, and which entries are already dead. When it disagrees with a register, the
> register wins and this page is stale.
>
> Written because the registers each answer "what is open in my area?" and none answers
> "what should happen next?". [`review/STATUS.md`](../review/STATUS.md) §What is next is the
> closest, and it ranks *review topics* only — it is still stamped 2026-08-15, before any of
> the OpenSpec changes now in tree and before the #315 review pass existed.

## The shape of it

Eleven registers hold open work, and **two bodies of work that no register covers** — the
codebase sweep that produced #315, which is 23% done, and the two documentation rewrites.
Those two are the largest open items on this page.

**The #315 count went up, not down, and that is the headline.** Two more sweep batches ran on
2026-09-17 and raised fifteen new threads, so the hub now holds 71 threads. Five of the
fifteen were already fixed on `main` and were resolved on 2026-09-19, leaving 20 open: ten
carried over from earlier passes and ten new. The pool is not draining, it is being refilled
faster than it drains, because each batch of the sweep finds more than the last batch's
findings cost to fix — and that is the sweep working as intended rather than a problem.

| Register | Open items | Health |
| --- | --- | --- |
| Open PRs | 4 live, 1 hub | Fourteen merged on 2026-09-19, seven of them the decision backlog: #244, #251, #274, #297, #353, #362, #185. The dormant-draft category is now empty |
| [#315](https://github.com/davitf/archivey/pull/315) review threads | 71 total, **51 resolved, 20 open** | Parcels A–E closed 44 between 2026-09-11 and 2026-09-17; five S1/S2 threads resolved 2026-09-19. Left: parcel F, one orphan thread, and ten S1/S2 findings |
| `openspec/changes/` (13 active) | 12 unimplemented, 1 half-done | `prefixed-archive-detection` is 31/68; the rest are 0/N. 363 tasks outstanding. #347/#356 added three RAR changes; #251, #274 and #185 merged three more |
| [`open-issues.md`](open-issues.md) | 13 product candidates, 1 deliberate docs gap | P15/P16 are specced; P2/P3/P4/P5 are unowned; **P18 is new** since the first snapshot |
| [`formats/rar.md`](formats/rar.md) `§10` | 4 of 21 (#6 layer 2, #18, #19, #21) | Healthy — 17 closed with PR links |
| [`formats/rar.md`](formats/rar.md) `§7` | 5 open questions | Healthy; the duplicated entry was merged in #323 |
| [`IDEAS.md`](IDEAS.md) | 55 entries | A park, **not a queue** — see below. Two entries have stale framing; see [Already dead](#already-dead) |
| [`review/backlog.md`](../review/backlog.md) | 3 PR parks, 7 archived-review parks, Topics 6/7 | #320 F2 is the only one with a live question |
| [`review/STATUS.md`](../review/STATUS.md) | Topics 8 + 10 in flight, docs IA in flight, +2 commissioned 2026-09-11 | **Its own header still says 2026-08-15.** The ranked list predates every OpenSpec change now in tree |
| [`review/typing-escape-hatches/`](../review/typing-escape-hatches/brief.md) | ~81 typing hatches + 13 `assert isinstance` | **Not started.** `brief.md` is the only file, six days on. Two census rows were already stale when written |
| [`review/exception-catchalls/`](../review/exception-catchalls/brief.md) | 30 marked blind `except` sites | **Not started.** `brief.md` is the only file. A verification review; its own brief says a large "actually fine" section is the expected outcome |
| [`threat-model.md`](threat-model.md) | `O*` register | O12's memory half is mitigated; the rest closes with `sevenzip-aes-tail-key-check`, in tree since #319 |
| [`known-issues.md`](known-issues.md) | Forensics, not a worklist | No action items of its own |
| **Linear** (`Archivey` team) | 39 issues seeded 2026-09-17 | **The state layer.** Labels: `sweep`, `decision`, `openspec`, `docs`, `review`, `pr-315`, `pr-open`. Not a replacement for any register below |
| **The #315 sweep** — *the `SWEPT` markers on #315* | 78 of 94 `src/` files never reviewed | **The largest open item here.** 27 999 of 36 298 lines unswept at this snapshot; batched into S1–S14 below. Read the live figure with `scripts/sweep_coverage.py` rather than from this row |
| **`dev-docs/formats/`** — *no register* | 2 of ~7 handbook pages written | ZIP and RAR done. `rar.md` alone produced the 21-item `§10` register |
| **`docs/`** — *tracked in `review/docs-content/`* | ~174 lines of prose + `how-it-works.md` | Skeleton, scope and verified claim inventory all done; the writing is not |

**[`IDEAS.md`](IDEAS.md) is not backlog.** 55 entries across six sections, and its job is to
stop the same speculative idea being re-derived. Nothing in it is late. Treat an `IDEAS.md`
entry as work only when something else pulls it in — which is what #347 does for two RAR
entries, and what the native-stress question below still needs. Do not read the 55 as a debt
figure.

## Open PRs

| PR | What | Verdict |
| --- | --- | --- |
| [#315](https://github.com/davitf/archivey/pull/315) | `[COMMENT ONLY]` full-codebase review hub | **Not a PR to merge.** Head *is* `main` (base is an orphan `empty-base`), so it re-renders against current `main` automatically — there is nothing to merge into it. 71 threads, 20 open. Carries `loop:off` so it can never enrol in the review loop |
| [#365](https://github.com/davitf/archivey/pull/365) | `DelegatingStream` flags: class default plus constructor override | **Review.** The first PR produced by the Linear → Cursor → review-loop circuit, from ARC-12. Closes #315 thread 56. CI green; see the loop note below |
| [#364](https://github.com/davitf/archivey/pull/364) | RAR: per-member comments expand after the parse bound | **Review.** Draft, docs-only note of the amplification the `max_members` bound does not cover |
| [#357](https://github.com/davitf/archivey/pull/357) | Drop "this change" from spec prose, plus a guard | **Review and merge.** Docs plus one check script. Ten sites, nine of which are pre-merge arguments rather than cross-references |
| [#352](https://github.com/davitf/archivey/pull/352) | `typing-escape-hatches` review — 89 sites inventoried | **Review.** Draft. The first of the two #325 reviews to actually run. No new #324-class TypeGuard lie found |

**Fourteen PRs merged on 2026-09-19**, and seven of those were the decision backlog: #244,
#251, #274, #297, #353, #362 and #185, all in the same forty minutes. That clears every item
the previous revision listed as waiting on a maintainer answer, including both dormant drafts
— #185 had been open since 2026-07-21 and #244 since 08-17. **Wave 0 is done.**

**Three PRs were closed on 2026-09-11** — #101 (superseded by `formats/rar.md` §9), #243
(the thinner of the two catalogues) and #187 (native stress harnesses). That clears Wave 0
items 3 and 4. #187's closure does **not** answer the question underneath it; see
[Native codec stress coverage](#native-codec-stress-coverage-its-own-evaluation).

Both former dormant drafts (#244, #185) merged on 2026-09-19. For the record, their shared
`updated_at` of 2026-08-23 was a bulk repository event rather than activity — neither was
worked between creation and the day it merged.

## Already dead

Checked against `main`, not inferred from the documents that mention them. The first three
bullets were the argument for closing #101, #187 and #243; all three closed 2026-09-11, and
the reasoning is kept only so the closures are not re-litigated.

- **#101 was superseded by its own successor page.** [`formats/rar.md`](formats/rar.md) §9
  says so outright: *"[PR #101], which was never merged; its conclusions are stated here and
  its measurements are what the first script re-runs, so the PR is provenance rather than a
  live reference."* The measurements live in `scripts/exploration/rar_unrar_input_matrix.py`.
  Nothing was lost by closing it.
- **#101 and #187 both wrote into `docs/internal/`, which no longer exists.** The docs IA
  migration (#221/#222) moved that tree to `dev-docs/`. Neither PR applied as written.
- **#187's shared-harness scaffolding was superseded in pattern.** `main` carries
  `.github/workflows/ppmd-native-stress.yml` + `scripts/ppmd_native_stress.py`,
  `rapidgzip-truncation-sweep.yml` + `scripts/rapidgzip_truncation_sweep.py`, and
  `atheris-fuzz.yml`. The idea of a shared native-stress harness was adopted; this PR's
  version of it was not. **What the PR proposed uniquely was a judgement, not a file** — and
  closing it did not make that judgement. See the section below.
- **#315 thread 50 is fixed.** `_open_folder_pipeline` is gone from `src/`; the only
  surviving mention is a comment in `tests/test_sevenzip_reader.py:754` recording that it
  *used to* exist.
- **#315 thread 52 is fixed by [#318](https://github.com/davitf/archivey/pull/318).**
  Password confirmation no longer materialises the folder; `sevenzip_reader.py:661` records
  the chunked replacement and its measured ~3× peak.
- **[`IDEAS.md`](IDEAS.md) §Testing "Decide what native-codec stress coverage is for" read
  as though #187 were open.** It said the criterion "would resolve PR #187 as
  close-with-criterion-recorded". #187 was closed on 2026-09-11 **without** the criterion
  being recorded, so the framing was backwards: the PR was gone and the question was not.
  Rewritten in this pass to state the decision on its own, with the candidate criterion and
  what adopting or rejecting it costs.
- **[`IDEAS.md`](IDEAS.md) §Testing "Pin and checksum the Windows UnRAR download" is
  contradicted by a later decision.** #320 (2026-09-10) **rejected** a pinned SHA-256 — the
  URL is unversioned, so the digest goes stale on every upstream release — and handled
  staleness with a rotating cache window instead. The entry also recommends copying the macOS
  pinned-commit pattern, which the same decision rejected as making CI test a binary no
  Windows user runs. Rewritten in this pass to point at the live question
  ([`review/backlog.md`](../review/backlog.md) #320 F2: Authenticode, unverified because the
  agent proxy blocks rarlab.com).
- **[`formats/rar.md`](formats/rar.md) §7 asked the same question twice** — *"Can the
  stream-source copy be made small, rather than just moved?"* appeared as both a short and a
  long bullet. Merged in this pass.

## #315 — the 71 threads

Once the largest pool of actionable work; now nearly drained. Threads 1–15 are maintainer
questions from 2026-09-07; 16–55 are an agent review pass from 2026-09-08 concentrated on
`streamtools/` and the two native backends; **thread 56 was added 2026-09-13** and is the
only one raised since.

**Fifty-one of seventy-one are resolved.** Parcels A–E closed 44 of them between 2026-09-11
and 2026-09-17, on top of the eight closed earlier. Every `streamtools/` thread from the
original pass is done, and both RAR files are done.

**Five S1/S2 threads were fixed by #349 and #350 and resolved on 2026-09-19.** The two batches
raised fifteen threads on 2026-09-17; the two PRs merged the next day and closed five of them,
but the threads stayed open for a day. Each was verified against `main` @ `15263c9` rather
than taken from the commit messages:

| Thread | Fixed by | Evidence on `main` |
| --- | --- | --- |
| S1-F1 | [#350](https://github.com/davitf/archivey/pull/350) | `zip_aes.py` `close()` no longer drains — the comment now cites ADR 0014. Not the fix the finding proposed: rather than making the compressed path authenticate on close, close stopped being a verdict path at all, which removes the STORED/DEFLATE asymmetry the finding measured |
| S1-F2 | #350 | the absurd-length guard is deleted |
| S1-F3 | #350 | the dead `except` and its comment are deleted |
| S2-F1 | [#349](https://github.com/davitf/archivey/pull/349) | `sevenzip_parser.py:225` caps the count against `_MAX_NUM_STREAMS` |
| S2-F2 | #349 | `sevenzip_pipeline.py:540` rejects a second `EncodedHeader`, registered as threat-model **O14** |

Leaving them open would have cost twice: the hub reads as though nothing landed, and the
second sweep pass would re-raise them.

Read each remaining thread's **last** comment before its first. A large fraction of the
2026-09-08 pass was self-corrected on re-reading — claims refuted, bugs downgraded to nits,
a three-item leak list reduced to one — so the opening comment is often no longer the finding.

| Resolved | Closed by |
| --- | --- |
| 17, 21, 38, 39 | [#324](https://github.com/davitf/archivey/pull/324) — Wave 1 |
| 16, 24, 50, 52 | Retracted by their own authors, or overtaken by #318 |
| 18, 19, 20, 35, 36, 37, 42, **45** | Parcel A ([#326](https://github.com/davitf/archivey/pull/326)) |
| 27, 28, 29, 30, 31, 32, 33, 34 | Parcel B ([#328](https://github.com/davitf/archivey/pull/328)) |
| 22, 23, 25, 26, 40, 41, 55 | Parcel C ([#329](https://github.com/davitf/archivey/pull/329)) |
| 1, 2, 4, 5, 6, 7, 8, 9 | Parcel D ([#332](https://github.com/davitf/archivey/pull/332)) |
| 43, 44, 46, 47, 48, 49 | Parcel E ([#336](https://github.com/davitf/archivey/pull/336)) |
| 3 | [#342](https://github.com/davitf/archivey/pull/342) / [#344](https://github.com/davitf/archivey/pull/344), with the merge half promoted to a change in [#347](https://github.com/davitf/archivey/pull/347) |

**Thread 45 was parcel E's, and parcel A closed it.** It was the third site of
`DelegatingStream.close`'s workaround; A replaced all of them with `manual_inner_close=True`
(since renamed `owns_inner` by [#340](https://github.com/davitf/archivey/pull/340)) and found
an unlisted fourth in `codecs.py` while doing it.

**Parcels D and E mostly closed by explaining, not by changing.** D's eight threads produced
one rename (`_parse_rar_one` → `_parse_rar_volume`), one deleted argument (`rarbug`), one
deleted dead method (`_HeaderDecryptStream.seek`), and five docstrings. E's six produced one
real deletion — `_BoundedMemberPipe`, 48 lines, replaced by a `SlicingStream` factory — plus a
`_pipe_pos` clamp, a `shutil.copyfileobj`, and a 111-line function split in two. Two of E's
threads were retracted by their own author before an implementer saw them. That ratio is the
useful signal about what a review pass of this kind actually yields.

**Thread 3 closed because the class it asked about got used.** It asked why a non-seekable
AES-CBC stream existed that nothing imported. [#342](https://github.com/davitf/archivey/pull/342)
made `AesDecryptStream` the 7z member-data wrapper *and* seekable (CBC restarts at any block
from the preceding ciphertext block), which is what keeps `seekable_members=True` on an
encrypted 7z folder; [#344](https://github.com/davitf/archivey/pull/344) added `TruncatedError`
on a short final block. The "are the three decrypt streams mergeable?" half is answered in the
class docstring and promoted to `fold-rar-header-decrypt-stream` in #347.

The 10 that remain. `*` marks a thread whose follow-up narrowed it.

| File | Open | Threads | Character |
| --- | --- | --- | --- |
| `backends/sevenzip_reader.py` | 3 | 51\*, 53, 54 | Two renames and a nit; 51's bug half was retracted |
| `volumes.py` | 2 | 12, 13 | One docstring, one real performance question |
| `zip_aes.py` | 2 | 14, 15 | Placement, and the one live layering violation |
| `rar_detect.py` | 1 | 10 | Placement — same decision as 14 |
| `reader_state.py` | 1 | 11 | "I can't even begin to review it." Explanation, not code |
| `streamtools/base.py` | 1 | 56 | **New 2026-09-13**, and not part of any parcel |

**What is left is one parcel and one orphan.** Nine of the ten are parcel F. Thread 56 arrived
after `streamtools/` was declared drained and belongs with whoever next touches
`DelegatingStream`.

**Two of the ten are maintainer decisions, not implementation.** Threads 10 and 14 ask the
same question — whether `rar_detect.py`, `zip_detect.py`, `sevenzip_detect.py`, `zip_aes.py`
and `zipcrypto.py` should move under `backends/` or into a new detection package rather than
sitting at the top of `internal/`. Five modules move or do not move on one answer, and every
importer of them moves with it, so it is worth answering before the parcel goes out rather
than inside it.

**Thread 15 is the only live defect in the ten, and it is a layering one.** `zip_aes.py:99`
imports `cryptography` directly, under a comment that reads *"Local import: only the crypto
wrapper may import cryptography"* — the code states the rule it is breaking. The question the
thread asks is not "move it" but which of the two implementations should survive the merge.

**Thread 51's corrected scope is a rename.** The original finding — a 7z folder decoding to
more than its members account for, making a correct password read as wrong — was retracted by
its own author with a whole-suite instrumentation run showing zero divergence; the format
defines the last substream size as the folder remainder. What survives is
`_folder_unpack_size` (a sum over members) shadowing the parser's `folder_unpack_size` (from
the coder graph) one underscore apart, and a missing check that the declared substream count
matches the non-empty file count — which is why a malformed archive reports `EncryptionError`
where `CorruptionError` is meant.

**The four verified bugs are fixed** — [#324](https://github.com/davitf/archivey/pull/324),
merged 2026-09-11, threads 17/21/38/39 resolved. Kept here because the PR's own review cycle
added findings F5–F13 on top, and because the shape of each is the precedent the two reviews
commissioned in #325 were drawn from.

1. **Thread 38 — a stale member slice reads the next member's bytes** (`streamtools/solid.py:90`).
   `_MemberSlice.read` checks neither `self.closed` nor whether it is still the reader's
   active member, and calls `self._reader._consume(n)` regardless. Closing the slice does not
   stop it. Silent wrong data, which is the worst failure class this library has.
2. **Thread 39 — a failed skip desynchronises the reader's position** (`streamtools/solid.py:38`).
   `skip_forward` consumes bytes and raises `EOFError` without reporting how many; both
   callers update `_pos` on the line *after* the call, so a partial skip leaves `_pos` behind
   the block's real position.
3. **Thread 21 — a text-mode handle passes the `TypeGuard[BinaryIO]`** (`streamtools/binaryio.py:278`).
   Reproduced: `is_stream(open("README.md"))` returns `True` because `TextIOWrapper` is an
   `io.IOBase`, `ensure_binaryio` hands it back unwrapped, and `read(4)` returns `'# ar'` —
   a `str`, typed as `bytes`. The user finds out several layers down.
4. **Thread 17 — `name` is annotated `-> str` and always raises** (`streamtools/base.py:91`,
   and `DelegatingStream.name` at `:161`). The raise is deliberate (pycdlib duck-types on
   `hasattr`), so the **annotation** is the defect: a checker accepts `stream.name.upper()`
   and it crashes.

**No verified bug survives in the open ten.** Threads 1 and 2 were the last candidates —
whether a RAR header-decrypt offset accounts for `_buf` and for encrypted block boundaries —
and parcel D measured both against the `encrypted_header__*.rar` fixtures and found neither.

## The sweep that produced #315 is under a quarter done

**It changes what the #315 count means.** The 2026-09-08 review pass was never run over the
whole codebase. Two batches have run since — **S1 (ZIP backend)** and **S2 (7z parser and
pipeline)**, both on 2026-09-17 — taking coverage from 12% to 23%. Draining the threads a
sweep produces is not the same as having reviewed the library.

| | Files | Lines |
| --- | --- | --- |
| Swept by the 2026-09-08 agent pass | 9 | 4 327 (12%) |
| Swept by S1 and S2, 2026-09-17 | 7 | 3 972 (11%) |
| **Never swept** | **78** | **27 999 (77%)** |

**The 24% this page claimed on 2026-09-17, and the 35% it claimed earlier on 2026-09-19, were
both wrong by the same twelve points.** Both counted the fifteen threads dated 2026-09-07 as
sweep output. They are not: every one is a maintainer question davi wrote himself, and they
sit on six files — `rar_parser.py` (8), `volumes.py` (2), `zip_aes.py` (2), `streams/crypto.py`,
`rar_detect.py` and `reader_state.py` — that the agent pass never touched. The agent pass is
the 39 threads dated 2026-09-08, on nine files: `rar_reader.py`, `sevenzip_reader.py` and the
seven `streamtools/` files. That matches this page's own prose about what the pass covered,
which is how the arithmetic and the description came to disagree for two snapshots running.

The caveat that keeps this honest in the other direction: a clean reading produces no threads
either, so no findings on a file is not proof nobody read it. For these six the dates and the
description agree, which is why they are counted as unswept rather than unknown.

**The correction moves the single most important file.** `backends/rar_parser.py` is the
largest file in the repository at 2 358 lines, it is hostile-input surface, and it has never
been swept — it carries only davi's eight questions. So the four largest files in the
repository are all unswept: `rar_parser.py`, `base_reader.py`, `streams/codecs.py` and
`extraction.py`. `backends/zip_reader.py`, which S1 covered, is the fifth.

**Some of the swept 23% is swept against code that no longer exists.** Measured between
`7ed4879` (`main` on the pass date) and today, seven of the nine files the 2026-09-08 pass
read have drifted by more than 10%:

| File | Then | Now | |
| --- | --- | --- | --- |
| `streamtools/base.py` | 165 | 251 | +52% |
| `streamtools/binaryio.py` | 514 | 716 | +39% |
| `streamtools/slice.py` | 316 | 421 | +33% |
| `streamtools/solid.py` | 172 | 225 | +31% |
| `streamtools/__init__.py` | 81 | 93 | +15% |
| `streamtools/shared.py` | 155 | 135 | −13% |
| `streamtools/locked.py` | 108 | 96 | −11% |

**The drift is all of `streamtools/` and none of the two native backends**, which moved about
5% each — and the cause is this page's own history. Nine commits rewrote `streamtools/` since
the pass, and five of them (#324, #326, #328, #329, #340) are the parcels that fixed the
findings that pass produced. Draining a sweep's threads rewrites the code the sweep read, so
`streamtools/` is the subsystem where "swept" has decayed furthest, and it decayed *because*
the follow-up was done rather than in spite of it. Re-sweeping it is worth more than its
position in the batch order below suggests.

**S1 and S2 are the evidence for what the rest is worth.** 3 972 lines produced fifteen
findings, all `CONFIRMED`, including **two 🔴 blocking hostile-input bugs in the 7z parser** —
an unbounded allocation from a few header bytes, and a 66-byte archive that hangs
`open_archive`. Both are now threat-model **O13** and **O14**. The yield per line is not
falling as the sweep proceeds, and the 27 999 unswept lines are roughly seven times the batch
that just produced those two.

**Run it in batches, not in one pass.** The single 4 327-line pass produced 39 threads and
six parcels of follow-up work, and it is the most expensive thing on this page per line
covered. The batches below are drawn on subsystem seams so each is one agent's reading pass
and one reviewable set of threads.

**S1 and S2 confirmed the sizing.** Roughly 2 000 lines each produced 3 and 12 findings, all
confirmed, in a batch small enough to review in one sitting and fix in one PR each. Keep the
remaining batches at that size. Two things S2 proved worth carrying into every later prompt:
tell the agent to weight hostile and truncated input at every read — both blocking findings
came out of exactly that instruction — and tell it what is already decided or already tracked,
or it spends its budget re-raising known items.

The order is roughly by what a reader of the existing threads would most want checked next;
nothing in it is a hard dependency.

| Batch | Files | Lines |
| --- | --- | --- |
| ~~**S1 — ZIP backend**~~ — *done 2026-09-17, 3 findings* | 3 | 1 855 |
| ~~**S2 — 7z parser + pipeline**~~ — *done 2026-09-17, 12 findings, two blocking* | 4 | 2 117 |
| **S3 — reader base** — `base_reader.py`, `reader.py`, `open_site.py` | 3 | 2 481 |
| **S4 — extraction** — `extraction.py`, `extraction_types.py`, `internal/filters.py`, `escaping.py` | 4 | 2 406 |
| **S5 — codec engine** — `streams/codecs.py`, `decompressor_stream.py`, `resume.py` | 3 | 2 703 |
| **S6 — codec formats** — `decompress.py`, `xz.py`, `unix_compress.py`, `lzip.py` | 4 | 2 590 |
| **S7 — detection** — `detection.py`, `detection_workspace.py`, `registry.py`, `format_provenance.py`, `format_args.py` | 5 | 1 719 |
| **S8 — stream spine** — `archive_stream.py`, `verify.py`, `counting.py`, `peekable.py`, `streamtools/full_count.py` | 5 | 1 587 |
| **S9 — TAR + ISO** — `tar_reader.py`, `iso_reader.py` | 2 | 1 438 |
| **S10 — single-file, directory, `unrar`** — `single_file_reader.py`, `directory_reader.py`, `rar_unrar.py` | 3 | 1 401 |
| **S11 — public API surface** — `core.py`, `types.py`, `exceptions.py`, `detection_cost.py`, `cost.py`, `config.py` ×2, `__init__.py` | 8 | 2 521 |
| **S12 — diagnostics, naming, SFX** — `diagnostics.py`, `diagnostics_collector.py`, `naming.py`, `sfx.py`, `selection.py`, `listing_limits.py`, `timestamps.py` | 7 | 1 902 |
| **S13 — CLI** — all of `cli/` | 14 | 1 975 |
| **S14 — passwords, hashing, framing** — `password.py`, `password_confirm.py`, `hashing/`, `brotli_framing.py`, `zstd_framing.py`, `measurement.py` ×2, `logs.py` | 10 | 888 |

**Three of these overlap work already specced**, and are worth sequencing around rather than
running blind: S7 (detection) against the three detection changes, since the evidence ledger
rewrites much of what it would review; S5/S6 (codecs) against Topic 6, the decode-engine
performance review that is already ranked; and S4 (extraction) against `bounded-source-spooling`,
whose four design questions were answered and merged on 2026-09-19 (#251), so the shape it
will impose on extraction is now known rather than pending.

**S1 and S2 are the two to run first.** They are the direct counterparts of the work already
done — parcels D and E read the RAR pair, and threads 51/53/54 read part of `sevenzip_reader.py`
— so they are the places where the existing threads most obviously stop mid-subsystem. `zip_aes.py`
thread 15 also lands in S1's territory.

### How sweep coverage is counted

**From the `SWEPT` markers on #315, never from thread counts.** Every file a sweep finishes
reading gets one top-level comment on #315 whose first line is a machine-readable marker —
path, batch, date, line count, finding count. The shape is defined in
[`archivey-review-addendum.md`](../.claude/skills/code-review-skill/reference/archivey-review-addendum.md)
§10, and [`scripts/sweep_coverage.py`](../scripts/sweep_coverage.py) does the arithmetic:

```
curl -s 'https://api.github.com/repos/davitf/archivey/issues/315/comments?per_page=100' \
  | python3 -c 'import json,sys; [print(c["body"]) for c in json.load(sys.stdin)]' \
  | python3 scripts/sweep_coverage.py --by-pass --unswept
```

The denominator is the tree in the checkout, not a number remembered from a previous
snapshot. A file counts once however many times it has been swept. Lines come from the tree
rather than from the marker, so a file read six months and a rewrite ago is reported as
drifted rather than silently counted as current.

**The rule exists because counting threads produced the wrong answer twice.** The 2026-09-17
snapshot said 24% and a revision on 2026-09-19 said 35%; the true figures were 12% and 23%.
Both counted the fifteen threads dated 2026-09-07 — maintainer questions on six files no
agent had read — as sweep output, and the error survived a revision because nothing on #315
distinguished a file read and found sound from a file nobody opened. It put
`backends/rar_parser.py`, the largest file in the repository and its most exposed
hostile-input surface, in the swept column while it had never been read.

**The sixteen pre-convention files were backfilled on 2026-09-19**, at the maintainer's
decision: the nine of the 2026-09-08 pass and the seven of S1 and S2. Each of those markers
carries `backfilled=2026-09-19` and says in its own text that nobody re-read the file — it
records the pass that did — and each was reconstructed from the paths the threads landed on,
the batch scope tables, and `main`'s tip on the pass date. The command above reproduces this
page's hand-derived 16 files and 8 299 lines exactly, which is the check that the
reconstruction is not a fresh guess. The reconstruction also surfaced something the hand
count could not: seven of the nine files from 2026-09-08 have drifted more than 10% since
they were read, `binaryio.py` from 514 lines to 716, so part of that coverage stands against
a shape the code no longer has.

**So the number below is a snapshot and the command is the source.** Batches running on
2026-09-19 post markers as they finish files, which moves the figure within a day.

## The two docs rewrites

**Also not previously on this page**, because both predate it and neither lives in a register
it tracks.

### 1. The format handbook — `dev-docs/formats/`

Two of the intended set exist: [`rar.md`](formats/rar.md) (91 KB) and
[`zip.md`](formats/zip.md) (42 KB). Both follow the same nine-section shape — At a glance,
Shape, The pipeline here, In the wild, Threat surface, Sharp edges, Decisions, Open questions,
Verify, References — so the template is settled and the remaining pages are writing, not
design.

| Page | State |
| --- | --- |
| `rar.md` | **Written**, and carrying its own unfinished work: `§7` has 5 open questions, `§10` has 4 of 21 changes still open (#6 layer 2, #18, #19, #21) |
| `zip.md` | **Written**; `§7` has 1 open question (whether PKWARE Strong Encryption deserves an explicit refusal rather than a misleading wrong-password error) |
| `sevenzip.md` | **Missing.** The format with the most machinery behind it after RAR — folders, coder graphs, substreams, BCJ2 unsupported — and three #315 threads still open against its reader |
| `tar.md` | **Missing.** Includes the stdlib-leniency question that `open-issues.md` **P3** is about |
| `iso.md` | **Missing.** Thin — one optional backend, `pycdlib` |
| `single-file.md` | **Missing.** gzip, bzip2, xz, lzip, zstd, lz4, brotli, `.Z`: the seek-point and truncation behaviour is spread across `codecs.py`, `xz.py`, `lzip.py` and `unix_compress.py` with no single page |
| `directory.md` | **Missing.** Thinnest of all; may not earn a page |

**The handbook is how `§10` registers get created**, which is the argument for continuing it:
writing `rar.md` produced 21 tracked code changes, 17 of which have shipped. That is the
highest-yield documentation work in the repo, and it is also why each new page should be
expected to *add* open items rather than only close them.

### 2. The user guide — `docs/`

Further along than [`review/STATUS.md`](../review/STATUS.md) suggests. All 15 published pages
exist — 2 311 lines — and the sixteenth, `how-it-works.md`, still does not.

| Artefact | Where | State |
| --- | --- | --- |
| The skeleton | [`review/docs/outline.md`](../review/docs/outline.md) | **Done.** All 16 pages with purpose, reader question, sections in order, explicit non-coverage, and `file:lines` sources |
| Scope pass (pass 0) | [`review/docs-content/scope.md`](../review/docs-content/scope.md) | **Done**, merged in #242. Every page has a stated job and a non-coverage list |
| Claim inventory (steps 2–3) | [`review/docs-content/claims.md`](../review/docs-content/claims.md) | **Done** — 1 114 lines, merged from two independent passes (#246, #247), with pass-1 verification complete as of 2026-08-18. Every claim carries a `verified` / `wrong` / `unverifiable` / `out of scope` verdict |
| The prose | `docs/` | **The unfinished half.** ~174 lines outstanding by the scope pass's re-tally, plus `how-it-works.md` which is 100% new |

**`review/STATUS.md` says "Next: step 3, the claim inventory".** Step 3 is done and verified;
that line has been stale for a month. The actual next step is writing, and it is the one part
of this programme with no agent-shaped unit of work defined for it yet.

## What #324 settled, and what it left

Wave 1 landed as [#324](https://github.com/davitf/archivey/pull/324) and did more than fix
four bugs. Three things worth carrying forward:

**Two #315 threads closed themselves, and that is the useful outcome.** Thread 16 proposed
moving `mode` / `name` off `ReadOnlyIOStream` onto `_PyCdlibStream`; the maintainer pushed
back, the proposal was **retracted with evidence** (pycdlib receives the *source* stream, so
for a mid-positioned source that is a `SlicingStream` — the base class is the right home and
removing `mode` breaks the open with `TypeError`). What survived was smaller: the comments
framed a general requirement as a pycdlib workaround. #324's last commit rewrote both as
docstrings stating the requirement first. Thread 42's leak list shrank the same way, from
three items to one.

**Thread 42's one surviving item is now fixed, and it is worth recording how.**
`nearest_resume_offset` named `ArchiveStream._maybe_warn_rewind` and a seek-point table — both
archivey concepts — from inside the package whose docstring says nothing here knows about the
rest of archivey, and `DelegatingStream` forwarded it by default so every wrapper inherited it.
Parcel A ([#326](https://github.com/davitf/archivey/pull/326)) took the forwarding off the base
class. The concept now lives in `streams/resume.py`, which asks the inner engine by `getattr`,
and the implementations sit on the streams that actually own a seek-point table
(`decompressor_stream.py:390`, `codecs.py:204` and `:747`, `crypto.py:293`, `verify.py:599`,
`counting.py:91`, `archive_stream.py:423`). The only thing left under `streamtools/` is
`slice.py:260`, which declines it. **The thread's real point holds and is the reason to keep
this paragraph:** the rule that gets enforced by tooling is the one that was never violated,
and the import linter could not see this leak at any point.
[#343](https://github.com/davitf/archivey/pull/343) has since pinned the behaviour as a
Hypothesis property.

**`src/` still has zero `# type: ignore`**, six days and twenty-two merges later. #324 deleted
both dead ones and expressed the two live suppressions as `# pyrefly: ignore[bad-override]`
with inline reasons. That matters
because pyrefly does not validate the code inside `# type: ignore[...]` brackets — a bogus
code still silences the line, so that form is a blanket suppression in this repo while
`# pyrefly: ignore[<code>]` fails closed.

## The two reviews commissioned in #325

Both against `8e88e4f`, both `src/`-only, disjoint sources, designed to run in parallel.
**One has now started.** `typing-escape-hatches` ran on 2026-09-17 and its inventory is
[#352](https://github.com/davitf/archivey/pull/352) — 89 sites, no new #324-class TypeGuard
lie, five casts that delete with both checkers clean, and about half the `Any` sites
tightening to `object`. `exception-catchalls` is still `brief.md` only and is not blocked by
anything, which makes it the clearest candidate for the next hand-out.

| Review | Population | Character |
| --- | --- | --- |
| [`typing-escape-hatches/`](../review/typing-escape-hatches/brief.md) | 2 `type: ignore`, 26 `cast()`, 37 `Any`, 3 `TypeGuard`, 13 `assert isinstance` | **Excavation.** Its precedent is #324's finding 3: a `TypeGuard` that lied, which the checkers then believed and propagated |
| [`exception-catchalls/`](../review/exception-catchalls/brief.md) | 30 marked blind `except` sites, in five patterns | **Verification.** Its own brief says recon found no smoking gun and warns against manufacturing severity |

**The typing brief's census is already stale in two rows, because #324 merged after it was
written.** Worth fixing before anyone starts, so the first hour is not spent rediscovering it:

- **S2 — "both existing `src/` suppressions are dead, DELETE them"** is **done**. #324's
  `chore(types)` commit removed both; `grep -c "type: ignore" src/` is now 0.
- **S3 — "two more exist only on #324's branch, sequence after it merges"** has happened.
  Both are on `main` now (`streamtools/base.py:195`, `streams/peekable.py:86`), already in
  the `# pyrefly: ignore[bad-override]` form with inline reasons, which is the outcome the
  brief wanted rather than work it still needs.

What survives untouched is the larger half: **26 `cast()` and 37 `Any`**, concentrated in
`tar_reader` (6 casts), `zip_reader` (5), `streamtools/binaryio.py` (12 `Any`) and
`iso_reader.py` (8). Plus seed **S1**, which is a `CONTRIBUTING.md` fix rather than an audit
finding: the rule currently offers `# type: ignore[attr-defined]` as an example of a
*specific* suppression, and in this repo it is not one.

## OpenSpec changes

| Change | Tasks | State |
| --- | --- | --- |
| `prefixed-archive-detection` | **31/68** | The only one in flight. Finish or explicitly park it before opening another detection change |
| `detection-evidence-ledger` | 0/70 | The big one. Rebuilds detection on graded evidence |
| `detection-result-surface` | 0/44 | **Blocked by the ledger** — it exposes what the ledger produces. Its own proposal says so |
| `archive-origin-reporting` | 0/33 | Merged as a proposal 2026-09-19 via #274. Overlaps `detection-result-surface` on `ArchiveInfo` |
| `bounded-source-spooling` | 0/29 | Merged 2026-09-19 via #251. Its four design questions are answered; subsumes rar `§10` **#6 layer 2** and **#21** |
| `bounded-password-confirmation` | 0/26 | In tree since #319. Ready to implement; closes most of **O12** |
| `single-file-open-time-validation` | 0/25 | Self-contained. Closes [`open-issues.md`](open-issues.md) **P15** and **P16** |
| `seekable-gzip-and-block-writing` | 0/24 | Self-contained, no `.openspec.yaml` (predates the schema). BGZF + mgzip random access, zero new dependencies |
| `rar5-stored-encrypted-native-read` | 0/22 | Merged 2026-09-18 via #347. Drops the `not info.is_encrypted` clause from `_can_direct_read` for RAR5. Amends ADR 0002 — the boundary is *decompression*, not *data* |
| `sevenzip-aes-tail-key-check` | 0/17 | After `bounded-password-confirmation`. Split out deliberately: the only piece resting on an empirical premise about writer padding, so the easiest to revert alone |
| `verification-integrity-mode` | 0/16 | Merged 2026-09-19 via #185, after 60 days open. The STRICT opt-in that guarantees a verdict — ADR 0014 names it as the vehicle for its own unfinished half |
| `rar-archive-offset-and-aes-cursor` | 0/12 | Merged 2026-09-18 via #356, the other half of the split fold change |
| `fold-rar-header-decrypt-stream` | 0/8 | Merged 2026-09-18 via #347, `skip_specs: true`. Answers #315 thread 3's merge half with an explicit bar: the fold lands only if the header caller's policy collapses to at most one new constructor argument, otherwise the outcome is to record the decision and close the change |

**Thirteen changes, 363 unstarted tasks, one of them 31 tasks in.** That is the largest single
number on this page, and none of it is blocked on a decision any more.

**`full-count-non-seekable-sources` was proposed, implemented and archived in three days** —
#330 → #333/#334/#335, archived `2026-09-12-full-count-non-seekable-sources`. It is the
fastest a change has gone from parcel finding to archived, and worth noting because it is the
shape that works: a narrow boundary found during a review parcel, specced immediately while
the context was live.

**Four detection changes touch the same surface.** `prefixed-archive-detection` (in flight),
`detection-evidence-ledger`, `detection-result-surface` and `archive-origin-reporting`, plus
four [`IDEAS.md`](IDEAS.md) §API entries that the ledger explicitly absorbs
(`FormatInfo.corroborated`, extension-first ordering, "content decides, extension
corroborates", "presence and value are different questions"). This is the one place where
doing things in the wrong order costs real rework: **the ledger defines the vocabulary the
other three report in.**

## What blocks what

```
bounded-password-confirmation ──> sevenzip-aes-tail-key-check ──> O12 closed

#315 Wave 1 + parcels A–E + the S1/S2 fixes ──> DONE (51 of 71 threads resolved)
        │
        ├──> parcel F (placement + odds)   9 threads, ready
        │       └── gated on ONE maintainer answer: threads 10/14 module placement
        └──> thread 56 (DelegatingStream flag style)   orphan, not in any parcel

#342 seekable AES-CBC ──> #347 rar5-stored-encrypted-native-read  (proposal, unscheduled)
                     └──> #347 fold-rar-header-decrypt-stream     (proposal, unscheduled)

prefixed-archive-detection (31/68) ──> detection-evidence-ledger ──> detection-result-surface
                                                 │                          │
                                                 │                          └──> #274 archive-origin-reporting
                                                 └──> 4 IDEAS.md §API entries retire

#251 design Q1-Q4 (maintainer) ──> bounded-source-spooling ──> rar §10 #6 layer 2
                                                          └──> rar §10 #21
                                                          └──> open-issues P11 closed

Topic 8 (docs content) ∥ Topic 10 (catalogue) ──> Topic 6 (perf) ──> Topic 7 (capstone, last)
        │
        └── #243 closed, so #244 is uncontested; Topic 10 is unblocked

typing-escape-hatches ∥ exception-catchalls ──> (nothing; both unblocked, neither started)

sweep S1..S14 ──> new #315-shaped threads ──> new parcels, new changes
        │              (S1/S2 first; the 77% of src/ no review has read)
        ├── S4 wants #251's answers first    (bounded-source-spooling)
        ├── S5, S6 want Topic 6's ranking    (decode-engine performance)
        └── S7 wants the detection order     (ledger rewrites what it would review)

formats/sevenzip.md, tar.md, iso.md, single-file.md ──> more §10-style registers
docs/ prose + how-it-works.md ──> (nothing; skeleton, scope and claims all done)
```

**One thing in this graph is waiting on a person rather than on work:** the threads 10/14
module-placement call. The #251 design answers landed on 2026-09-19, which was the other one.
The sweep and the docs wait on nobody — they wait on someone starting the next batch.
Everything else is either running, or ready for whoever picks it up next.

`single-file-open-time-validation`, `seekable-gzip-and-block-writing` and
`bounded-password-confirmation` are fully independent — the three changes to hand someone who
wants work that blocks on no decision.

## Native codec stress coverage (its own evaluation)

**#187 was closed on 2026-09-11, and the question it was standing in for is still open.**
The PR added native stress harnesses for rapidgzip and inflate64. This page argued it was not
a keep-or-close call but a proxy for a question nobody had asked: *which native codecs warrant
which kind of hostile-input coverage, and why?* The PR is gone; the question was not answered
on the way out, and it is still a scoped piece of work with a written output.

**This is the one place where closing a PR lost something.** Everything else on the "already
dead" list was superseded by work that landed. Here the code was correctly declined and the
judgement behind declining it was never written down, so the next person to propose a stress
harness for `brotli` or `lz4` has nothing to be measured against.

**Two different things are both called "coverage" here**, and conflating them is what makes
the PR hard to judge:

| | Fuzzing (`tests/atheris_fuzz/`) | Native stress (`scripts/ppmd_native_stress.py`) |
| --- | --- | --- |
| Looks for | Crashes on *malformed* input | Aborts and races under *repeated valid* decode |
| Shape | Coverage-guided, seeded corpus | Scenario loops, subprocess isolation, soft-pass CI |
| Exists because | A general guarantee we want to hold | **One specific known upstream defect** |

**What `main` actually has today**, checked rather than assumed:

- **Atheris targets are broader than the registers suggest.** `tests/atheris_fuzz/targets.py`
  registers 7 required stream codecs (`unix_compress`, `xz`, `lzip`, `gzip`, `bzip2`,
  `lzma_alone`, `zlib`) and 4 optional ones — **including `deflate64`** — plus the RAR and 7z
  parser targets. So inflate64 is *not* uncovered, which an earlier draft of this page got
  wrong. It has fuzz coverage and no native-stress harness.
- **Native stress exists for exactly one library: `pyppmd`**, and
  [`known-issues.md`](known-issues.md) §"Intermittent `pyppmd` native aborts on valid PPMd
  streams" is why. The harness was built to chase a reproducible defect, not as a standard
  every native dependency is held to.
- **`rapidgzip` has a sweep, not a stress harness.** `rapidgzip-truncation-sweep.yml` targets
  one behaviour — truncation detection — from `rapidgzip-truncation-investigation`.

**The question to answer.** The native surface is `pyppmd`, `inflate64`, `rapidgzip` (and its
bundled `indexed_bzip2`), `brotli`, `lz4`, `cryptography`, `pycdlib`. One of eight has a stress
harness. Either that is correct — stress harnesses are a response to *evidence* of a defect,
and building them speculatively for the other seven buys little — or it is a gap, in which
case #187 is the start of closing it and the same treatment is owed to five more libraries.

**This page does not answer that**, deliberately: the answer changes what CI runs on every PR
and is a testing-strategy decision, adjacent to [`review/backlog.md`](../review/backlog.md)
Topic 4 (test-suite strategy, archived) rather than to any open change. What it needs is a
short evaluation with a stated criterion — the honest candidate being *"a native stress
harness is built when an upstream defect is observed, not before"*.

Durable home for the question: [`IDEAS.md`](IDEAS.md) §Testing, whose entry now states it as
a standing decision rather than as a verdict on a PR that no longer exists. It is the one
`IDEAS.md` entry that is a **decision owed**, not a speculative idea parked.

## How much is actually left

Worth stating plainly, because two snapshots in a row opened with "this is the largest block
of actionable work" about a pool that is now nearly empty.

| | 2026-09-11 | 2026-09-17 | 2026-09-19 |
| --- | --- | --- | --- |
| #315 threads open | 47 | 10 | **20** (5 fixed and resolved 09-19) |
| Open PRs (excluding the #315 hub) | 9 | 4 | **4** |
| Dormant drafts | 5 | 2 | **0** |
| OpenSpec changes in tree, unimplemented | 8 | 6 | **12** |
| Reviews commissioned but not started | 2 | 2 | **1** |
| `src/` never swept | *not tracked* | 88% (stated as 76%) | **77% — 78 files, 27 999 lines** |
| Handbook pages unwritten | *not tracked* | ~5 of ~7 | **~5 of ~7** |

**Two of these rows moved the wrong way earlier in the day, and both were good news.** Open
threads went from 10 to 25 because the sweep ran and found fifteen more, then back to 20 as the
already-fixed ones were resolved. Unimplemented OpenSpec changes went from 6 to 12 because #347
and #356 promoted parked RAR items into proposals and #251, #274 and #185 merged three more. Open PRs went from 4 to 9 and back to 4, because fourteen merged in a day.

The one row that reads like a burndown moved less than it looked: `src/` never swept went 88%
to 77%, not 76% to 65%. Both earlier figures counted six files as swept that never were.

**The correctness picture changed too.** The 2026-09-17 snapshot said nothing in the open
threads was a correctness bug. That was true of the ten threads then open and is not true
now: S2-F1 and S2-F2 were confirmed hostile-input bugs with working triggers, and both are
fixed and registered as **O13** and **O14**. Of the twenty open threads that remain after the
five already-fixed ones were resolved, six are 🟡 important and the rest are nits, renames and
one placement decision.

**The registers are not where the remaining work is.** The two rows added to the table above
are each larger than everything else on this page put together, and until this revision
neither appeared anywhere that answers "what is open?". A snapshot that counted only the
registers would read as nearly finished; the honest reading is that the parts of the library
that have been examined closely are in good shape, and three quarters of it has not been
examined closely.

**That is the input to the release question**, and two days of evidence sharpened it. Nothing
measured so far blocks a publication: the confirmed bugs are P15 and P16, both specced in
`single-file-open-time-validation`, and `bounded-password-confirmation` closes most of a
threat-model entry. Those two are the ones with a claim on a release, and they are 51 tasks
between them. Against that, the unswept 77% is unmeasured rather than known-good — and the
base rate is now measured rather than guessed. The most recent 3 972 lines swept produced two
blocking hostile-input bugs, in a parser that had looked fine for months. Scaling that
naively over 27 999 unswept lines is not a forecast, but it is the only number available, and
it does not point at zero. The correction above made this argument stronger rather than
weaker: there is a fifth more unread code than the last revision claimed, and the largest
unread file is the RAR parser. **An alpha is defensible now; "a clean state" is not a thing the
current evidence can certify.** The detection changes
are separately expensive to land *after* people depend on the current surface, because the
ledger defines the vocabulary the other three report in.

## Plan of attack

Ordered by what unblocks the most, then by what is cheapest to verify.

**Wave 0 — clear the desk. Done 2026-09-19.** Doc-only, no decisions needed.
1. This page, plus the two register cleanups it names (rar.md §7 duplicate, IDEAS.md
   Windows-UnRAR entry). *Landed.*
2. Merge **#319** *(done, 2026-09-11)* and **#297** *(done, 2026-09-19 — it merged once the
   prose conclusions became a runnable script, which was the objection against it)*.
3. Close **#101** *(done, 2026-09-11)*, **#243** *(done)*, **#187** *(done — but see the
   native-stress section: the criterion it needed was not recorded)*.
4. **Resolve the five #315 threads whose fixes merged in #349 and #350** — S1-F1, S1-F2,
   S1-F3, S2-F1, S2-F2. *Done 2026-09-19*, each verified against `main` first; see the #315
   section above. The ten remaining S1/S2 threads are tracked internally as one batch.

**Wave 1 — the four verified bugs. Done.**
[#324](https://github.com/davitf/archivey/pull/324), merged 2026-09-11: threads 17, 21, 38, 39
fixed red-green, plus F5–F13 from the PR's own review cycle. Thread 16 resolved with it. It
also surfaced the two reviews now commissioned in #325, and left `src/` with zero
`# type: ignore`.

**Wave 2 — drain #315 in six parcels. A through E are done.** One parcel remains, plus one
thread that arrived after the parcels were drawn.

| Parcel | Files | Threads | State |
| --- | --- | --- | --- |
| ~~A — stream bases~~ | `base.py`, `locked.py`, `__init__.py` | 18, 19, 20, 35, 36, 37, 42, 45 | **Done** — [#326](https://github.com/davitf/archivey/pull/326) |
| ~~B — slice + shared~~ | `slice.py`, `shared.py` | 27–34 | **Done** — [#328](https://github.com/davitf/archivey/pull/328) |
| ~~C — binaryio + solid~~ | `binaryio.py`, `solid.py` | 22, 23, 25, 26, 40, 41, 55 | **Done** — [#329](https://github.com/davitf/archivey/pull/329) |
| ~~D — RAR parser~~ | `backends/rar_parser.py` | 1, 2, 4, 5, 6, 7, 8, 9 | **Done** — [#332](https://github.com/davitf/archivey/pull/332). Threads 1 and 2 were the two possible header-decrypt bugs; both measured against fixtures and neither was one |
| ~~E — RAR reader~~ | `backends/rar_reader.py` | 43, 44, 46, 47, 48, 49 | **Done** — [#336](https://github.com/davitf/archivey/pull/336) |
| **F — placement + odds** | `rar_detect.py`, `zip_aes.py`, `volumes.py`, `reader_state.py`, `sevenzip_reader.py` | 10, 11, 12, 13, 14, 15, 51\*, 53, 54 | **Ready, and the last one.** Nine threads. Thread 3 left the parcel when #342 answered it. Threads 10 and 14 are a maintainer call, not a fix — answer that first, because the placement decision is what makes the rest mechanical |
| **(orphan)** | `streamtools/base.py` | 56 | Raised 2026-09-13, after `streamtools/` was drained. `peel_for_source_size` is a class field while `readinto_passthrough` is a constructor argument; #340's `owns_inner` and #341's class gating have since established the "class flag with a constructor override" pattern that answers it |

**Parcel F's prompt should carry three corrections** the follow-up comments make and the
opening comments do not: thread 3 is closed and out of scope; thread 51 is a rename plus a
missing substream-count check, not the password bug its first comment describes; and thread 54
is a ~10-site rename across `sevenzip_reader.py` **and** `zip_reader.py`, not the two-line
delete it was first written as.

**Wave 2b — the two #325 reviews. Commissioned, briefed, and not started.** Disjoint sources,
both `src/`-only audits rather than changes, so they never contended with the #315 drain in
the way two refactors would. The typing brief's census was refreshed 2026-09-11 (S2 and S3
closed); `exception-catchalls` needed nothing before it started and still does not. With
parcel F the only #315 work left, these are now the largest ready-to-hand-out block in the
repository.

**Wave 3 — the unblocked OpenSpec changes.** Three, none waiting on anyone:
`single-file-open-time-validation` (closes P15 + P16, 25 tasks),
`bounded-password-confirmation` (26, closes most of threat-model **O12**), and
`seekable-gzip-and-block-writing` (24). `full-count-non-seekable-sources` left this list by
being implemented and archived on 2026-09-12.

**Wave 4 — decisions, then detection.** Answer #251's four questions. Finish or park
`prefixed-archive-detection`. Then `detection-evidence-ledger` → `detection-result-surface`
→ #274, in that order, retiring the four `IDEAS.md` §API entries as the ledger absorbs them.

**Wave 6 — the sweep and the docs, continuously and in parallel with everything above.**
Neither is a wave in the sense the others are: they are long-running programmes that should
have one batch in flight at a time rather than a slot in the order. Run S1 and S2 first, one
batch at a time to keep the cost bounded, and sequence S4, S5, S6 and S7 around the changes
and topics they overlap. On the docs side, `sevenzip.md` is the next handbook page by value,
and the user guide's remaining prose is the one item here with no agent-shaped unit of work
defined for it yet.

**Wave 5 — the review topics.** Topic 8 ∥ Topic 10 → Topic 6 → Topic 7 last, per
[`review/STATUS.md`](../review/STATUS.md). Unchanged; this page does not re-rank them. Note
that `review/STATUS.md` still carries a 2026-08-15 header and describes #187, #243 and the
two-catalogue standoff as live — it is the register most in need of its own refresh.

**Not scheduled, on purpose:** `verification-integrity-mode` (#185) needs a keep-or-kill
judgement against ADR 0014 before it earns a slot; rar `§10` **#18** is marked *very low
priority — remaining names are adversarial*; rar `§10` **#19** is a public knob and wants a
config decision; [`open-issues.md`](open-issues.md) **P2/P3/P4** belong to the native
streaming ZIP theme, which is an `IDEAS.md` entry rather than a scheduled change.
