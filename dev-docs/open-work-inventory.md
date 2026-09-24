# Open work inventory and sequencing

> **State now lives in Linear; this page keeps the reasoning.** As of 2026-09-17 the work
> below is tracked as issues on the `Archivey` Linear team — one issue per unit of work that
> can be handed out, each linking back here. **Linear answers "is this started, and did a PR
> close it?"; this page and the registers answer "why".** When the two disagree about state,
> Linear wins; when they disagree about reasoning, the registers win. GitHub issues are
> deliberately not used for internal tracking — they stay clear for external reports.
>
> **A dated snapshot, not a register.** Snapshot: **2026-09-23** against `main` @ `1925039`.
> Every item below lives somewhere canonical — [`open-issues.md`](open-issues.md),
> [`threat-model.md`](threat-model.md), [`IDEAS.md`](IDEAS.md),
> [`review/backlog.md`](../review/backlog.md), [`review/STATUS.md`](../review/STATUS.md),
> the format handbook ([`formats/rar.md`](formats/rar.md) §7 for unfinished RAR work),
> or an `openspec/changes/`
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
codebase sweep that produced #315, whose reading half finished on 2026-09-20, and the two
documentation rewrites. Those two are the largest open items on this page.

**Every pull request is merged, and the #315 count is going down.** On 2026-09-23 no pull
request is open apart from the hub. The hub holds **209 threads, 98 resolved and 111 open**,
against 140 open at the previous revision: #393 cleared 26 in one PR, #394 closed S15-K4, and
this revision closed four more after checking each against `main` (the PPMd window, the tar
and ISO header allocations, and the `ConcatenatedFile` docstring). Nothing refills the pool
now — every file has been read once — except new code, which arrives unswept (see [the
coverage section](#the-first-pass-over-src-is-complete-and-countable)).

**Eight blocking findings remain**, down from eleven. #396 fixed the tar and ISO allocations
and #398 fixed the PPMd window, by adding the public `DecoderLimits` type that the LZMA
dictionary size and the KDF budget are now waiting to use.

**Thirteen S20–S25 rulings are still owed by davi.** Seventeen findings needed a ruling. Four
have one, and none of the four has landed: remove `extract_all(config=)` and
`strict_archive_eof`, detect raw `.bin` ISO images and refuse them, and pin `__module__` on
the public names before 0.2.0. A re-check on
2026-09-22 made two of the thirteen smaller: the `archivey ./x` question is a notation fix in one
spec row, and PR 382 already fixed half of the selector question. They are tracked
internally as one decision item.

| Register | Open items | Health |
| --- | --- | --- |
| Open PRs | **0 live**, 1 hub | Everything merged by 2026-09-23 14:17Z. Seventeen merged after the previous revision (#388, 2026-09-21 12:45Z): #386, #387, #389, #391, #392, #393, #394, #395, #396, #397, #398, #399, #400, #401, #402, #403, #404. See [Open PRs](#open-prs) |
| [#315](https://github.com/davitf/archivey/pull/315) review threads | 209 total, **98 resolved, 111 open** | #393 cleared 26, #394 one, and four were resolved on 2026-09-23 after a check against `main`. Eight blocking findings are open |
| `openspec/changes/` (15 active) | 14 unimplemented, 1 half-done | `prefixed-archive-detection` is 32/68; the rest are 0/N (bar one task on `archive-origin-reporting`). **435 tasks outstanding**, counted 2026-09-23. #402 and #404 added two proposals, `single-archive-source` (0/31) and `one-member-listing-per-reader` (0/38) |
| [`open-issues.md`](open-issues.md) | 13 product candidates, 1 deliberate docs gap | P15/P16 are specced; P2/P3/P4/P5 are unowned; **P18 is new** since the first snapshot |
| [`formats/rar.md`](formats/rar.md) `§10` | **gone** — the section is deleted | It said to delete it once empty, and it is: 19 of 21 shipped, #19 and #21 last. The two that had not shipped moved to homes that outlive it — the stream-copy bound to §7, the `unrar` mask port to [`IDEAS.md`](IDEAS.md) — and both are tracked internally |
| [`formats/rar.md`](formats/rar.md) `§7` | 5 open questions | Healthy; the duplicated entry was merged in #323 |
| [`IDEAS.md`](IDEAS.md) | 55 entries | A park, **not a queue** — see below. Two entries have stale framing; see [Already dead](#already-dead) |
| [`review/backlog.md`](../review/backlog.md) | 3 PR parks, 7 archived-review parks, Topics 6/7 | #320 F2 is the only one with a live question |
| [`review/STATUS.md`](../review/STATUS.md) | Topics 8 + 10 in flight, docs IA in flight, +2 commissioned 2026-09-11, +1 (`api-freeze`) 2026-09-21 | **Its own header still says 2026-08-15.** The ranked list predates every OpenSpec change now in tree |
| [`review/typing-escape-hatches/`](../review/typing-escape-hatches/brief.md) | 89 sites inventoried; the fixes are staged | **Started and mostly landed.** The inventory merged as #352; #376, #377, #378 and #384 landed four waves of it. The rest is held on file collisions only |
| [`review/exception-catchalls/`](../review/exception-catchalls/brief.md) | 30 marked blind `except` sites | **Not started.** `brief.md` is the only file. A verification review; its own brief says a large "actually fine" section is the expected outcome |
| [`threat-model.md`](threat-model.md) | `O*` register | O15 (tar extended header) and O16 (ISO directory record) were added and closed by #396. O12's memory half is mitigated; the rest closes with `sevenzip-aes-tail-key-check`. The PPMd window #398 capped has **no row yet** — #398 left it out because the file belonged to another open PR |
| [`known-issues.md`](known-issues.md) | Forensics, not a worklist | No action items of its own |
| **Linear** (`Archivey` team) | seeded 2026-09-17, added to continuously | **The state layer.** Labels: `sweep`, `decision`, `openspec`, `docs`, `review`, `pr-315`, `pr-open`. Not a replacement for any register below |
| **The #315 sweep** — *the `SWEPT` markers on #315* | First pass complete 2026-09-20; **94 of 97 files on 2026-09-23** | 40 178 of 40 662 lines, **98.8%**. Three files arrived after the pass: `internal/enum_args.py` (#380), `internal/arg_checks.py` (#382) and `internal/windows_reparse.py` (#386). Since then [#448](https://github.com/davitf/archivey/pull/448) added `terminal.py` (the former `escaping.py`, already swept, under a new name) and `detection.py` (moved classes). What is open now is draining the threads, not reading. Count it from the markers, not from this row |
| **`dev-docs/formats/`** — *no register* | 3 of ~7 handbook pages written | ZIP, RAR and 7z done. `rar.md` alone produced the 21-item `§10` register |
| **`docs/`** — *tracked in `review/docs-content/`* | ~174 lines of prose + `how-it-works.md` | Skeleton, scope and verified claim inventory all done; the writing is not |

**[`IDEAS.md`](IDEAS.md) is not backlog.** 55 entries across six sections, and its job is to
stop the same speculative idea being re-derived. Nothing in it is late. Treat an `IDEAS.md`
entry as work only when something else pulls it in — which is what #347 does for two RAR
entries, and what the native-stress question below still needs. Do not read the 55 as a debt
figure.

## Open PRs

**None is open apart from the hub.** Every pull request that was open at the previous
revision has merged, and so has everything opened since.

| PR | What | Where it sits |
| --- | --- | --- |
| [#315](https://github.com/davitf/archivey/pull/315) | `[COMMENT ONLY]` full-codebase review hub | **Not a PR to merge.** Head *is* `main` (base is an orphan `empty-base`), so it re-renders against current `main` automatically — there is nothing to merge into it. 209 threads, 111 open. Carries `no-review` so no review round can run on it. **It was closed by accident twice on 2026-09-21 and reopened both times** — see the note below |

**Seventeen merged between 2026-09-21 12:45Z and 2026-09-23 14:17Z.** What each one changed
for this page:

| PR | What it did | Effect here |
| --- | --- | --- |
| [#393](https://github.com/davitf/archivey/pull/393) | Cleared the straightforward sweep findings | 26 #315 threads and two bugs that were never filed |
| [#394](https://github.com/davitf/archivey/pull/394) | Refuse a volume sequence whose parts belong to different archives | Closed S15-K4 |
| [#396](https://github.com/davitf/archivey/pull/396) | Bound the tar extended-header and ISO directory allocations (`read_within_reach`) | Closed blocking S18-K1 and S22-K1; threat-model O15, O16 |
| [#398](https://github.com/davitf/archivey/pull/398) | Public `DecoderLimits`, default 2 GiB; cap the PPMd window | Closed blocking K1. The LZMA dictionary and the KDF budget are the next users of the same guard |
| [#401](https://github.com/davitf/archivey/pull/401) | A RAR member whose header walk stopped early is reported as encrypted | The blocking finding raised on #371 before it merged; not a #315 thread |
| [#400](https://github.com/davitf/archivey/pull/400) | Every stream source gets a `BorrowedStream`, so archivey never closes the caller's stream | A spec violation found outside the sweep. #402 builds on it |
| [#386](https://github.com/davitf/archivey/pull/386) | ZIP and 7z read Windows reparse points | Answers half of S20-K9. The thread stays open: 7-Zip stores no reparse buffer for a directory, so a junction still never comes back flagged, and `is_junction` still has no docstring saying so |
| [#402](https://github.com/davitf/archivey/pull/402) | **Proposal only**: one `ArchiveSource` class at the source boundary | OpenSpec `single-archive-source`, 0/31 |
| [#404](https://github.com/davitf/archivey/pull/404) | **Proposal only**: one member listing per reader | OpenSpec `one-member-listing-per-reader`, 0/38. Also fixes `member_id` being unset on streamed 7z and solid RAR members |
| [#397](https://github.com/davitf/archivey/pull/397) | The 7z handbook page | Third of ~7 |
| [#403](https://github.com/davitf/archivey/pull/403) | A `review` label runs one review round; the scheduled loop is gone | The `loop:*` labels, the `@claude review` trigger and the Linear hop are removed |
| [#392](https://github.com/davitf/archivey/pull/392), [#395](https://github.com/davitf/archivey/pull/395) | The hub watchdog; the round cap raised to five | Both are about the review loop that #403 then replaced |
| [#387](https://github.com/davitf/archivey/pull/387) | Drop the `unrar x` tempdir strategy from the RAR spec | Spec only |
| [#389](https://github.com/davitf/archivey/pull/389) | Split the 7z stream-cap test | Test only |
| [#391](https://github.com/davitf/archivey/pull/391) | Commission the public API review for the 0.2.0 freeze | `review/api-freeze/` is `brief.md` only — not started |
| [#399](https://github.com/davitf/archivey/pull/399) | The ASD-STE100 skill; scannable review comments | Tooling |

**A closing keyword closed the hub by accident — twice in one afternoon, the second time
from the text explaining the first.** Both were GitHub's ordinary documented form: the
keyword immediately before the hub's number.

PR 365's squash body ended with one, and merging it at `11:10:27Z` closed the hub two
seconds later: `closed_at` `2026-09-21T11:10:29Z`, `merged_at` null, the `closed` event
carrying `0454c54`, PR 365's own merge commit. 142 threads were open. Reopened at
`11:16:47Z`.

Then the revision of this page that recorded incident 1 did it again, and **the mechanism is
the quotation.** Explaining what PR 365 had written meant reproducing the phrase, and PR
388's *pull request body* reproduced it verbatim, twice. GitHub parses a pull request body on
merge, so the hub closed at `12:45:29Z`. The squash body is innocent here and its `closed`
event proves it: that event carries **no commit at all**, where incident 1's carries
`0454c54`. A closure driven by a commit message records its commit.

**So the rule is about reproduction, not about proximity: when you write down what a closing
phrase said, do not reproduce it.** Describe it — "PR 365's squash body ended with a closing
keyword and the hub's number" — or break the string. §10 of the review addendum
carried this exact shape for the old review loop's trigger phrase, which was a command rather
than a quotable string; the hub's number is the same hazard with a different parser. Ordinary
sentences that merely contain both a keyword and the number are fine: this page has eight of
them and neither closure came from that shape.

**Two surfaces, not one.** GitHub prefills the squash body from the pull request body, so
they normally match — but whoever merges can edit the squash body, and PR 388's was edited,
which is exactly why reading it alone gave the wrong answer here. Both are parsed. Check
both.

**And a closure fails silently**, which is why the rule has to be mechanical rather than
watchful. A closed hub still serves every thread over the `ccr/review_threads` route,
`scripts/sweep_coverage.py` keeps returning the same coverage, and the review workflow never
touches it anyway (`no-review`). Nothing in the repository degrades; the hub simply stops
being a pull request. `empty-base` was untouched both times, so reopening restored everything
and cost nothing.

**There is now a net.** [`.github/workflows/review-hub-watchdog.yml`](../.github/workflows/review-hub-watchdog.yml)
reopens the hub when it finds it closed and comments saying which surface did it, reading the
last `closed` event's `commit_id` exactly as the diagnosis above does. It runs on every push to
`main`, which is what a closing keyword needs to fire whether it arrives as a squash, a merge
commit or a commit pushed straight to the branch, with a six-hourly schedule behind it for a
close by hand or an event GitHub drops. It is recovery rather than prevention: a pull request
body could be checked before the merge, but a squash body could not — it is editable at merge
time and what GitHub parses is what was actually merged — so a pre-merge check would cover one
surface and miss the other. The escape hatch is the `hub:closed-on-purpose` label, which the
job honours and which was created on the repository when this landed.

The decision — open, merged, labelled, or reopen and which surface did it — lives in
[`scripts/review_hub_watchdog_gate.py`](../scripts/review_hub_watchdog_gate.py) and is unit
tested, the way `review_loop_gate.py` already splits the review loop. That split earns more
here than it does there: everything past "is the hub open" runs only during an incident, so a
break in it is invisible until the moment it is needed, which is exactly how the first version
shipped with the surface query broken by pagination.

**#382 and #380 were two halves of one sweep and they collided; #380 merged on 2026-09-21 and
resolved it.** Both refuse or coerce wrong-typed public arguments — #382 the object and numeric
ones, #380 the enums — and #382 landed first as `3875daa`, so #380 carried all three conflicts.
Two were unions (`ArchiveyConfig.__post_init__` and the `error-handling` spec) and one was not:
`internal/detection.py`'s `_resolve_budget` is #380's body outright, because #380 deliberately
accepts `budget="fast"` where #382 refuses it. Kept here because the union case is the trap and
it will recur: Python keeps only the last definition of a method in a class body, silently, so
picking one side of such a conflict deletes the other branch's validation with nothing failing
at import.

**Fourteen PRs merged on 2026-09-20 and 2026-09-21** — #365, #370, #371, #372, #373, #374,
#375, #376, #377, #378, #380, #382 and #383/#384 — and between them they account for
seventeen threads resolved on the hub, counted from the `ccr/review_threads` route on
2026-09-21 at 12:40Z. (Fifteen of those were the figure before #380 merged and closed S19-K6
and S19-K8; PR 388's own description still says fifteen.) #371, #373, #374 and #375 were the
first three decided S15/S16 findings plus the RAR mask fix, each turned around in about
seven minutes; #370 removed the `pybcj` dependency
outright; #382, #384 and #380 are the public-API argument, `extra`-typing and enum-coercion
changes; #372 deleted `formats/rar.md` §10; #376, #377 and #378 are three waves of the typing
escape-hatch inventory.

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

## #315 — the 209 threads

**Still the largest pool of actionable work.** 209 threads, **98 resolved and 111 open**,
counted from the `ccr/review_threads` route on 2026-09-23. The composition matters more than
the number, because the four cohorts behave differently:

| Cohort | Posted | Threads | Open | What they are |
| --- | --- | --- | --- | --- |
| Maintainer questions + the S0 agent pass | 2026-09-07 … 09-14 | 61 | 12 | Drained by parcels A–E, and by #365 for the orphan thread 56. Left: parcel F and the five `solid.py` naming questions from 09-14 |
| **S1 / S2** | 2026-09-17 | 15 | 7 | Five closed by #349/#350, three more since 2026-09-21. What is left is implementation with clear directions, not decisions |
| **S15 / S16 + R1–R3** | 2026-09-19 | 30 | 14 | The shared stream and codec path, the RAR parser and `unrar`. Ten closed by #370–#375, six more since 2026-09-21 (#393, #394, #398) |
| **S17–S25** (+ V-K1) | 2026-09-20 … 09-21 | 103 | 78 | The first pass finishing: the public API, the CLI, the remaining backends and stream formats. Seven of the eight open blocking findings are here |

**Four threads were resolved on 2026-09-23**, each checked against `main` @ `1925039`
rather than taken from a PR description:

| Thread | Fixed by | Evidence on `main` |
| --- | --- | --- |
| K1 — 7z PPMd window (blocking) | [#398](https://github.com/davitf/archivey/pull/398) | `check_decoder_memory()` (`streams/codecs.py:1775`) runs before `pyppmd` is built, on 7z (`:1854`) and ZIP method 98 (`:1841`); `tests/test_decoder_limits.py` passes |
| S18-K1 — tar extended header (blocking) | [#396](https://github.com/davitf/archivey/pull/396) | `tar_reader.py:220-231` reads through `read_within_reach`; O15 |
| S22-K1 — ISO directory record (blocking) | #396 | every source goes through `open_fp` over archivey's own capped handle (`iso_reader.py:273`, `:391`); O16 |
| `ConcatenatedFile` stream position (davi, 09-07) | [#388](https://github.com/davitf/archivey/pull/388) | the class docstring says the caller's position is ignored and to pass a sliced view (`volumes.py:333`) |

**Four more looked closeable and are not.** K6 (a 7z folder that overstates `unpack_size`
makes pyppmd raise `MemoryError`) shares K1's root but #398 did not touch `_decode`. V-K1
(`ConcatenatedFile`'s finalizer after a refused `__init__`) is unchanged: `_path_handles` is
still assigned after the sizing loop that can raise. S23-K6 (`FullCountStream` serving reads
after `close()`) is unchanged by #400, which rewrote the same boundary. S20-K9 is half answered
by #386; the documentation half is open, and the thread now carries a note saying so.

**Do not count these from memory.** The hub is past 100 top-level comments and 200 review
threads, so a single-page fetch drops both markers and threads *silently*. The
`ccr/review_threads` route on the PR returns every thread with its resolved flag in one page;
`issues/315/comments` still needs the paging loop in [How sweep coverage is counted](#how-sweep-coverage-is-counted).

**Seventeen threads were resolved on 2026-09-21.** Fourteen of them were verified against `main`
@ `b0fe664` rather than taken from a commit message or from this page, and the rule that
produced held again — see the three that looked closeable and were not, below. The fifteenth,
thread 56, closed with #365's merge; the last two are S19-K6 and S19-K8, both closed by #380
later the same day.

| Thread | Fixed by | Evidence on `main` |
| --- | --- | --- |
| volumes `read` bisect (davi, 09-07) | [#374](https://github.com/davitf/archivey/pull/374) | `read` advances a cursor; only `seek` bisects (`volumes.py:424-451`) |
| S15-K3 closed file serves data | #374 | `_checkClosed()` on `tell`/`seek`/`read` |
| S15-K5 eager opens, raw `EMFILE` | #374 | `os.stat` sizing, three-handle LRU, `_volume_open_error` → `OpenError` |
| S15-K6 silent short read | #374 | `TruncatedError("volume ended before its recorded size")` |
| R1-K1 mask matcher hang | [#371](https://github.com/davitf/archivey/pull/371) | `_unrar_mask_for` narrows `*` to `?`; `_unrar_component_match` is a fixed-length walk |
| R2-K7 malformed RAR5 extra | #371 | dropped, `MEMBER_HEADER_RECORD_SKIPPED`, still refused under a strict policy |
| S15-K9 unbounded hit stream | [#375](https://github.com/davitf/archivey/pull/375) | `MAX_VALIDATED_CANDIDATES = 256`, `ScanMiss.CAPPED` |
| S15-K11 the two scans disagree | #375 | `scan_for_magic(validator=…)`, both parsers plumbed |
| S15-K12 resume point | [#373](https://github.com/davitf/archivey/pull/373) | `composed = 0` at `q == 0`; the `min` kept, as ruled |
| slice forward the question | #373 | `SlicingStream.nearest_resume_offset` translates and clamps at 0 |
| K5 BCJ over 2 GiB | [#370](https://github.com/davitf/archivey/pull/370) | `pybcj` is not imported anywhere in `src/` |
| S19-K4 `budget=` unvalidated | [#382](https://github.com/davitf/archivey/pull/382) | `_resolve_budget` ends in `check_instance` |
| S20-K16 `members="a.txt"` | #382 | `selection.py:21` refuses a bare `str`/`bytes` |
| S18-K7 short read on a truncated member | — | **Withdrawn by its own author**: today's behaviour is ADR 0014 |
| 56 `DelegatingStream` flag style | [#365](https://github.com/davitf/archivey/pull/365) | `peel_for_source_size` and `readinto_passthrough` both class-flag-plus-constructor-override, like `_SUBCLASS_CLOSES_INNER` |
| S19-K8 `overwrite=`/`on_error=` strings | [#380](https://github.com/davitf/archivey/pull/380) | `core.py:727-732` coerces at the boundary; `internal/enum_args.py` names this failure in its docstring |
| S19-K6 `_optional()` re-walks `sys.path` | #380 | `@functools.cache` at `registry.py:132`, with the ~46 µs vs ~0.6 µs measurement in the docstring |

**Three threads that looked closeable were not, and the difference was one grep each.**
Thread 51's bug half was retracted but its rename (`_folder_unpack_size` shadowing the
parser's `folder_unpack_size` one underscore apart) had not happened. Thread 53's conclusion
about RAR held, but its own ask — collapsing the two `SlicingStream` constructor calls so the
branches differ in `start` alone — was untouched. Both were done later, in
[#440](https://github.com/davitf/archivey/pull/440). Thread 12's docstring question is answered by
this page's own PR, not by #374. **Resolving on the strength of a follow-up comment saying
"the conclusion held" would have closed all three wrongly.**

**Fifty-one of the first seventy-one were resolved before this pass.** Parcels A–E closed 44
of them between 2026-09-11 and 2026-09-17, on top of the eight closed earlier. Every
`streamtools/` thread from the original pass is done, and both RAR files are done.

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
| 56 | [#365](https://github.com/davitf/archivey/pull/365) — ARC-12; class flag + constructor override for `peel_for_source_size` and `readinto_passthrough` |

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

The 2 of this cohort that remain (the five `solid.py` rows, the three `sevenzip_reader.py` rows, the two placement threads and the `zip_aes.py` layering thread are done). `*` marks a thread whose follow-up narrowed it.

| File | Open | Threads | Character |
| --- | --- | --- | --- |
| ~~`streamtools/solid.py`~~ | 0 | five from 2026-09-14 | **Done** — [#439](https://github.com/davitf/archivey/pull/439): `_drain_chunks` folded into `skip_forward` (per-chunk `on_chunk` callback), `_skip_to` inlined, redundant check dropped, `_claim_offset` renamed `_check_can_open_at`, "Vend" reworded |
| ~~`backends/sevenzip_reader.py`~~ | 0 | 51\*, 53, 54 | **Done** — [#440](https://github.com/davitf/archivey/pull/440): `_folder_unpack_size` renamed `_folder_members_total_size`, the timestamp aliases dropped from the 7z and ZIP readers, one `SlicingStream` call in `_open_member`. 51's substream-count half was closed by #424 |
| ~~`backends/zip_aes.py`~~ | 0 | 15 | **Done** — [#445](https://github.com/davitf/archivey/pull/445): the AES-CTR keystream is a `CryptoBackend` stage with the counter convention as a parameter, written block-wise (Z-K1); `WinZipAesDecryptStream` stays as the ZIP AE boundary. 14 (placement) was closed by the move in [#443](https://github.com/davitf/archivey/pull/443) |
| `volumes.py` | 1 | 12 | The docstring half; 13 was closed by #374 |
| ~~`rar_detect.py`~~ | 0 | 10 | **Done** — [#443](https://github.com/davitf/archivey/pull/443) moved it under `backends/` |
| `reader_state.py` | 1 | 11 | "I can't even begin to review it." Explanation, not code |

**Threads 10 and 14 are done: the move landed in [#443](https://github.com/davitf/archivey/pull/443).** They asked whether
`rar_detect.py`, `zip_detect.py`, `sevenzip_detect.py`, `zip_aes.py` and `zipcrypto.py` should
move under `backends/` or into a new detection package. **Decided 2026-09-19: `backends/`**,
with the rationale written down for the first time — a format's detection code is similar to
and often shares logic with its parsing code, so keeping them together keeps them in sync. The
axis is per format, not per phase, which is also why the `internal/detection/` alternative was
rejected. `detection.py`, `detection_workspace.py` and `sfx.py` stay where they are, being
format-agnostic. The five modules now live in `internal/backends/`, every importer with them;
`internal/` holds 27 top-level modules instead of 32 (not counting `__init__.py`).

**Thread 15 is done ([#445](https://github.com/davitf/archivey/pull/445)).** `zip_aes.py` imported `cryptography` directly, under a
comment saying only the crypto wrapper may. Ruled 2026-09-19: move the primitive, keep the
framing. `CryptoBackend.aes_ctr_keystream_stage` takes the counter convention as a parameter
(WinZip AE counts little-endian from 1, where `modes.CTR` counts big-endian), and builds the
keystream block-wise — ~6 MiB/s became ~110 MiB/s on 4 MiB of random data in 64 KiB chunks (one
container, not a `benchmarks/` run; Z-K1). `WinZipAesDecryptStream` stays in
`zip_aes`, since its `read` also carries the HMAC.

**Thread 51's corrected scope is a rename.** The original finding — a 7z folder decoding to
more than its members account for, making a correct password read as wrong — was retracted by
its own author with a whole-suite instrumentation run showing zero divergence; the format
defines the last substream size as the folder remainder. What survives is
`_folder_unpack_size` (a sum over members) shadowing the parser's `folder_unpack_size` (from
the coder graph) one underscore apart, and a missing check that the declared substream count
matches the non-empty file count — which is why a malformed archive reports `EncryptionError`
where `CorruptionError` is meant. **Both halves are done:** #424 added the count check
(`sevenzip_parser._map_files_to_folders` raises `CorruptionError`), and
[#440](https://github.com/davitf/archivey/pull/440) made the rename.

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

**No verified bug survives in the fourteen of this cohort.** Threads 1 and 2 were the last
candidates — whether a RAR header-decrypt offset accounts for `_buf` and for encrypted block
boundaries — and parcel D measured both against the `encrypted_header__*.rar` fixtures and
found neither. That sentence used to describe the whole hub and no longer does: the later
cohorts carried eleven blocking findings between them, eight of them still open.

## The S15–S25 drain, which is now the bulk of the page

111 threads open, 92 of them from the batches S15 onwards. This is the
largest single block of work in the repository and it is not uniform. It splits three ways,
and the first two overlap — several of the blocking findings are also the ones waiting on a
ruling.

**Thirteen are waiting on a ruling.** Seventeen S20–S25 findings cannot be fixed without
deciding *what the behaviour should be*, not just how to implement it. Each is written up on
its thread with the options in plain terms, what each means for a user, and a recommendation;
they are tracked internally as one decision item and go to davi one at a time, because a list
of seventeen is not a thing anyone answers in one sitting and several of them interact. Four
are answered. The first two were **settled by removing surface rather than choosing between
behaviours** — `extract_all(config=)` loses the parameter, and `strict_archive_eof` is deleted,
which dissolves the contradiction it was in rather than resolving it. The other two: raw `.bin`
ISO images are detected and refused by name (reading them is after 0.2.0), and `__module__` is
pinned to `archivey` on the 17 public names that report an internal module. **None of the four
has landed yet.** Offer the "remove it" option explicitly in the remaining thirteen.

**Eight are blocking**, counted from the 🔴 markers on the open threads on 2026-09-23 rather
than remembered, and they are the ones with a claim on a release:

| Finding | File | What it is |
| --- | --- | --- |
| S15-K1 | `volumes.py` | A filename alone allocates gigabytes — `a.zip.9999999` is 19.7 s and a 139 MB error message |
| S18-K4 | `streams/xz.py` | Seeking in a multi-block XZ file returns silently wrong bytes |
| S20-K15 | `reader.py` | `extract_all(config=)` is a no-op against two spec rows. Ruled: drop the parameter |
| S21-K10 | `listing_limits.py` | `link_target` is weighed before it exists: 2.4 GiB inside `members()` from a 398 KiB ZIP |
| S23-K3 | `streams/lzip.py` | An unvalidated trailer `member_size` makes a crafted `.lz` return another member's bytes on seek |
| S24-K1 | `cli/extract_cmd.py` | The extraction summary interpolates a member name raw |
| S24-K7 | `cli/info_cmd.py` | The archive comment goes to stdout unescaped |
| S25-K5 | `internal/password.py` | The provider re-entry guard counts depth globally, so two threads make one of them fail |

Seven more were blocking and are fixed: R1-K1 (the `unrar` mask hang), S15-K9 (the unbounded
SFX hit stream), K5 (BCJ over 2 GiB), S19-K8 (`overwrite=`/`on_error=` strings, #380), and the
three allocations above — K1 (#398), S18-K1 and S22-K1 (#396). **Four of the eight are a
bounded-allocation or wrong-bytes bug reachable from hostile input** (S18-K4, S21-K10 and S23-K3
from a file, S15-K1 from a filename alone), which is the class this library exists to get right.

**The rest is ordinary implementation**, and a large fraction of it is nits, renames and
docstrings that state something the code stopped doing.

**`DecoderLimits` has landed; the two rulings that were waiting for it have not.**
[#398](https://github.com/davitf/archivey/pull/398) added it as a public type, default 2 GiB
(davi raised it from the 1 GiB he first gave), raising `ResourceLimitError`, carried to every
backend by `StreamConfig`. Its one guard, `check_decoder_memory()`, has two users (the PPMd
paths). Two follow-ups use it next:

- **The LZMA dictionary size** is the same shape as the PPMd window — an archive-declared
  number that sizes an allocation before any data is read. #398 made the helper shared for
  this reason. No ruling is needed; the default already exists.
- **The archive-declared KDF cost** gets a cache and a dedupe plus a total derivation budget
  on `DecoderLimits`. The budget needs a default number from davi. Budget the **summed declared
  rounds**, not derivations-at-max-cost, because RAR salts per member.

A second, stricter preset (`DecoderLimits.UNTRUSTED` is the suggested name, not decided) is
deferred to [`IDEAS.md`](IDEAS.md), by agreement on #398, until the type has more than one
field.

**Verify before closing, every time.** Of seventeen threads that read as fixed in this pass,
three were not, and each took one grep to tell apart. `git log -S` on a distinctive line
settles "did this ship?" better than any register — including this one.

## The first pass over `src/` is complete, and countable

**Every file `src/` held on 2026-09-20 has been read end to end at least once.** The 2026-09-08
review pass was never run over the whole codebase; fourteen batches have run since, and
coverage went 12% → 23% → 45% → **100%** over four days. Draining the threads a sweep produces
is not the same as having reviewed the library, and that draining is what is open now — the
reading is not.

**It slipped below 100% a day later and is still slipping, and that is the durable lesson.**
Counted on 2026-09-23 the figure is **98.8% — 94 of 97 files, 40 178 of 40 662 lines**. Three
files arrived after the pass finished: `internal/enum_args.py` (183 lines, #380),
`internal/arg_checks.py` (165, #382) and `internal/windows_reparse.py` (136, #386). The
previous revision's count was taken before `enum_args.py` merged. A sweep covers a tree at a moment; new code arrives unswept by default and nothing
flags it except running the count. The three are one small batch (484 lines), tracked
internally. [#448](https://github.com/davitf/archivey/pull/448) then renamed the swept
`escaping.py` to `terminal.py` and added `detection.py`, which holds classes moved from
swept files; a recount will list both as new. The move of five modules under `backends/` ([#443](https://github.com/davitf/archivey/pull/443)) orphans
their markers, which name the old paths: `internal/rar_detect.py`, `zip_detect.py`,
`sevenzip_detect.py`, `zip_aes.py` and `zipcrypto.py`. `sweep_coverage.py` reports a marker
whose path is gone and does not count it, so the figure drops by those five files until a sweep
batch re-anchors them at `internal/backends/`. They were read; they are not new code.
**Re-run the count before quoting it — a figure on this page is always a
snapshot, including this one.**

**Every figure here is countable from the `SWEPT` markers on #315** rather than maintained by
hand — one top-level comment per file a batch finishes, findings or not. Count it rather than
quoting it; this table is a snapshot taken on 2026-09-20.

| Batch | Date | Files | Lines | Findings |
| --- | --- | --- | --- | --- |
| S0 — the original agent pass | 2026-09-08 | 9 | 4 378 | 39 |
| S1 — ZIP backend | 2026-09-17 | 3 | 1 855 | 3 |
| S2 — 7z parser and pipeline | 2026-09-17 | 4 | 2 107 | 12 |
| S15 — shared stream and codec path | 2026-09-19 | 7 | 5 433 | 18 |
| S16 — RAR parser and `unrar` | 2026-09-19 | 3 | 3 040 | 9 |
| S17 — extraction and the reader base | 2026-09-20 | 4 | 4 460 | 12 |
| S18 — tar, xz, verify, archive stream | 2026-09-20 | 4 | 2 842 | 8 |
| S19 — the detection seam | 2026-09-20 | 4 | 1 845 | 7 |
| S20 — public API | 2026-09-20 | 6 | 2 294 | 19 |
| S21 — diagnostics and reader state | 2026-09-20 | 7 | 1 778 | 12 |
| S22 — ISO, single-file and directory backends | 2026-09-20 | 4 | 1 477 | 12 |
| S23 — remaining stream formats | 2026-09-20 | 8 | 1 581 | 8 |
| S24 — the CLI | 2026-09-20 | 15 | 1 982 | 10 |
| S25 — crypto, naming and the rest | 2026-09-20 | 16 | 1 525 | 14 |
| **Swept** | | **94** | **36 597 (100%)** | **183** |
| **Never swept** | | **0** | **0 (0%)** | |

**S20–S25 ran as six parallel batches in one afternoon** and produced 75 findings, seven of
them blocking: a listing-limit field weighed before the backends fill it (2.4 GB from a
398 KiB ZIP), an unvalidated ISO directory length (4 GiB from a 57 KB image), an lzip seek
that silently serves another member's bytes, `extract_all(config=)` being a no-op, two CLI
sites printing archive-controlled text unescaped, and a password-provider re-entry guard that
refuses a second thread. (Twelve blocking findings are open across *all* batches; the seven
here are this afternoon's share.) Two operational notes from running them in parallel are in the
sweep conventions: GitHub allows one pending review per account, so a batch retries rather
than clearing another batch's slot, and the MCP GitHub tools do **not** auto-append the
Claude Code footer on #315.

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

**The correction moved the single most important file, and S16 then read it.** The corrected
list put `backends/rar_parser.py` — the largest file in the repository at 2 358 lines, and its
most exposed hostile-input surface — in the unswept column, carrying only davi's eight
questions. S16 read it the same day and raised two findings; S15 took `streams/codecs.py` in
the same pass. The two largest files that remained after that — `base_reader.py` (2 175 lines)
and `extraction.py` (1 694) — went in S17 the next day. That is the sequence the markers are
for: the count named the riskiest gap each time, and each gap was closed within the day.

**Some of what is swept is swept against code that no longer exists**, and the list has grown
from seven files to fourteen in two days. Counted by `sweep_coverage.py` on 2026-09-21, these
have drifted more than 10% from the line count recorded in their own marker:

| File | Read at | Now | |
| --- | --- | --- | --- |
| `streamtools/binaryio.py` | 514 | 763 | +48% |
| `streamtools/base.py` | 165 | 251 | +52% |
| `streamtools/slice.py` | 316 | 444 | +41% |
| `internal/sfx.py` | 449 | 638 | +42% |
| `config.py` | 195 | 338 | +73% |
| `internal/selection.py` | 39 | 72 | +85% |
| `types.py` | 573 | 709 | +24% |
| `internal/volumes.py` | 525 | 690 | +31% |
| `streamtools/solid.py` | 172 | 225 | +31% |
| `streamtools/__init__.py` | 81 | 105 | +30% |
| `backends/rar_reader.py` | 1 420 | 1 564 | +10% |
| `streamtools/shared.py` | 155 | 135 | −13% |
| `streamtools/locked.py` | 108 | 96 | −11% |
| `streams/resume.py` | 30 | 23 | −23% |

**The drift used to be all of `streamtools/`; now it tracks wherever the fixes landed.** Six
of the new entries — `sfx.py`, `config.py`, `selection.py`, `types.py`, `volumes.py`,
`rar_reader.py` — grew *because* #371–#384 fixed findings on them. Draining a sweep's threads
rewrites the code the sweep read, which is the same effect the `streamtools/` parcels had a
week earlier. It is the cost of doing the follow-up, not evidence against it; the thing to
avoid is re-reading a file a landed change is about to rewrite again.

**Decided 2026-09-19 (davi): not now.** His ruling, on being shown the table above — the
changes on those files *were* the fixes from the sweep's own issues, and he would rather
finish reading the whole codebase and fix everything outstanding, then do another pass later.
So the drift is an argument for that eventual second pass over the whole tree, not a batch to
schedule ahead of the lines nobody had read once.

**The precondition is now met.** The first pass finished on 2026-09-20, so a second pass is no
longer ruled out by that decision — but his order was *finish reading, then fix everything
outstanding, then pass again*, and the middle step is under way: 111 threads are open on
#315, down from 140. **27 swept files have now drifted more than 10%** from the line count
their marker recorded (fourteen on 2026-09-21); `volumes.py` has nearly doubled. A re-sweep is the step after draining them, not instead of it.

**S1 and S2 were the evidence for what the rest was worth, and the rest paid out.** Those
3 972 lines produced fifteen findings, all `CONFIRMED`, including **two 🔴 blocking
hostile-input bugs in the 7z parser** — an unbounded allocation from a few header bytes, and a
66-byte archive that hangs `open_archive`. Both are now threat-model **O13** and **O14**. The
yield per line did not fall as the sweep proceeded: the last five batches read 8 859 lines and
raised 75 findings, seven of them blocking, at a higher rate than S1 and S2 managed.

**Run it in batches, not in one pass.** The single 4 327-line pass produced 39 threads and
six parcels of follow-up work, and it was the most expensive thing on this page per line
covered. The batches below were drawn on subsystem seams so each is one agent's reading pass
and one reviewable set of threads — keep that shape for the second pass.

**S1 and S2 confirmed the sizing.** Roughly 2 000 lines each produced 3 and 12 findings, all
confirmed, in a batch small enough to review in one sitting and fix in one PR each. Keep the
remaining batches at that size. Two things S2 proved worth carrying into every later prompt:
tell the agent to weight hostile and truncated input at every read — both blocking findings
came out of exactly that instruction — and tell it what is already decided or already tracked,
or it spends its budget re-raising known items.

The order was roughly by what a reader of the existing threads would most want checked next;
nothing in it was a hard dependency, which is why the passes that ran could re-cut it freely.

**This plan was never executed as written, and it is now spent.** The batches that actually
ran were cut on different seams and numbered S15–S25, and by 2026-09-20 they had covered every
file in every row below. The table is kept as the record of what was planned and which pass
actually took it; **the `SWEPT` markers on #315 remain the only authority on any given file.**
Do not hand out a row from this table — there is nothing left in it.

| Batch | Files | Lines |
| --- | --- | --- |
| ~~**S1 — ZIP backend**~~ — *done 2026-09-17, 3 findings* | 3 | 1 855 |
| ~~**S2 — 7z parser + pipeline**~~ — *done 2026-09-17, 12 findings, two blocking* | 4 | 2 117 |
| ~~**S3 — reader base**~~ — *`base_reader.py` by S17; `reader.py` by S20; `open_site.py` by S25* | 3 | 2 481 |
| ~~**S4 — extraction**~~ — *first three by S17; `escaping.py` by S25* | 4 | 2 406 |
| ~~**S5 — codec engine**~~ — *all three files taken by S15, 2026-09-19* | 3 | 2 703 |
| ~~**S6 — codec formats**~~ — *`xz.py` by S18; `unix_compress.py` and `lzip.py` by S23; `decompress.py` went with S15* | 3 | 1 647 |
| ~~**S7 — detection**~~ — *first three by S19; the last two by S25* | 5 | 1 719 |
| ~~**S8 — stream spine**~~ — *`archive_stream.py` and `verify.py` by S18; the other three by S23* | 5 | 1 587 |
| ~~**S9 — TAR + ISO**~~ — *`tar_reader.py` by S18; `iso_reader.py` by S22* | 2 | 1 438 |
| ~~**S10 — single-file, directory**~~ — *both by S22; `rar_unrar.py` went with S16* | 2 | — |
| ~~**S11 — public API surface**~~ — *by S20, except `detection_cost.py` (S19), `cost.py` (S21) and `internal/config.py` (S25)* | 8 | 2 521 |
| ~~**S12 — diagnostics and naming**~~ — *diagnostics pair and `listing_limits.py` by S21; `naming.py`, `selection.py` and `timestamps.py` by S25; `sfx.py` went with S15* | 6 | 1 453 |
| ~~**S13 — CLI**~~ — *all of `cli/` plus `__main__.py` by S24, 2026-09-20, 10 findings* | 14 | 1 975 |
| ~~**S14 — passwords, hashing, framing**~~ — *by S25, except the two framing modules and `measurement.py` ×2 (S23 and S21)* | 10 | 888 |

**Three of these overlapped work already specced**, and the passes that ran took them anyway:
detection went in S19 while the evidence ledger was still open, codecs in S15 and S23 ahead of
Topic 6's ranking, and extraction in S17 after `bounded-source-spooling`'s four design answers
merged on 2026-09-19 (#251). Only the last of those sequenced cleanly; the lesson for the
second pass is that a sweep reading code a change is about to rewrite costs a re-read, and
that is the one dependency worth honouring.

### How sweep coverage is counted

**From the `SWEPT` markers on #315, never from thread counts.** Every file a sweep finishes
reading gets one top-level comment on #315 whose first line is a machine-readable marker —
path, batch, date, line count, finding count. The shape is defined in
[`archivey-review-addendum.md`](../.claude/skills/code-review-skill/reference/archivey-review-addendum.md)
§10, and [`scripts/sweep_coverage.py`](../scripts/sweep_coverage.py) does the arithmetic:

```
for p in 1 2 3; do \
  curl -s "https://api.github.com/repos/davitf/archivey/issues/315/comments?per_page=100&page=$p" \
  | python3 -c 'import json,sys; [print(c["body"]) for c in json.load(sys.stdin)]'; \
done | python3 scripts/sweep_coverage.py --by-pass --unswept
```

That loop pages deliberately: the endpoint serves 100 comments at a time, #315 gains a marker per file swept, and a single-page fetch drops the rest **silently** — the files on the missing page come back as unswept. Raise the range until the last page prints nothing. With the `gh` CLI, `gh api --paginate repos/davitf/archivey/issues/315/comments --jq '.[].body'` does the same in one call.

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
hostile-input surface, in the swept column while it had never been read. It has been read
since — S16, on 2026-09-19 — which is how the miscount came to be caught rather than a reason
it stopped mattering.

**The sixteen pre-convention files were backfilled on 2026-09-19**, at the maintainer's
decision: the nine of the 2026-09-08 pass and the seven of S1 and S2. Each of those markers
carries `backfilled=2026-09-19` and says in its own text that nobody re-read the file — it
records the pass that did — and each was reconstructed from the paths the threads landed on,
the batch scope tables, and `main`'s tip on the pass date. The command above reproduces the
16 files and 8 299 lines this page derived by hand for S0, S1 and S2, which is the check that
the reconstruction is not a fresh guess. Because each marker records the line count as read, the
drift table above falls straight out of them rather than needing to be measured by hand
again.

**So every coverage number on this page is a snapshot and the command is the source.** The
batches running on 2026-09-19 post markers as they finish each file, which moves the figure
within a day; a row here is what was true when the row was written.

## The two docs rewrites

**Also not previously on this page**, because both predate it and neither lives in a register
it tracks.

### 1. The format handbook — `dev-docs/formats/`

Three of the intended set exist: [`rar.md`](formats/rar.md),
[`zip.md`](formats/zip.md) and [`7z.md`](formats/7z.md). `rar.md` is the longest by some
way; the other two are each a little over 40% of it. (Byte counts used to be written out
here and were wrong twice, because any edit to a page invalidates the number describing
it — `wc -c` the files if you need the exact figures.) All three follow the same
nine-section shape — At a glance, Shape, The pipeline here, In the wild, Threat surface,
Sharp edges, Decisions, Open questions, Verify, References — so the template is settled
and the remaining pages are writing, not design.

| Page | State |
| --- | --- |
| `rar.md` | **Written**; `§7` has 5 open questions. The temporary to-fix list is gone: #19 and #21 shipped, #6 layer 2 lives in §7, the `unrar` mask port in [`IDEAS.md`](IDEAS.md) |
| `zip.md` | **Written**; `§7` has 1 open question (whether PKWARE Strong Encryption deserves an explicit refusal rather than a misleading wrong-password error) |
| `7z.md` | **Written.** `§7` has 3 open questions. The file is `7z.md`, not the `sevenzip.md` this row used to name — the format is spelled `7z` everywhere else that faces a reader (`format-7z`, `docs/formats.md`, `review/backlog.md`). Thirteen open #315 findings still sit against the backend: six on `sevenzip_parser.py` (bind-pair arithmetic, pack-size overrun, substream mapping, the `kComment` external flag), three on the reader, two on the pipeline, one each on `sevenzip_methods.py` and `sevenzip_detect.py` |
| `tar.md` | **Missing.** Includes the stdlib-leniency question that `open-issues.md` **P3** is about |
| `iso.md` | **Missing.** Thin — one optional backend, `pycdlib` |
| `single-file.md` | **Missing.** gzip, bzip2, xz, lzip, zstd, lz4, brotli, `.Z`: the seek-point and truncation behaviour is spread across `codecs.py`, `xz.py`, `lzip.py` and `unix_compress.py` with no single page |
| `directory.md` | **Missing.** Thinnest of all; may not earn a page |

**The handbook is how a format's to-fix register gets created**, which is the argument for continuing it:
writing `rar.md` produced 21 tracked code changes, 19 of which have shipped, and `7z.md`
surfaced two of its own (a refusal that names the wrong coder, and the folder decode that
listing a solid archive with a symlink in it pays for). That is the
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
class. The seek-point table still lives outside `streamtools`: the implementations sit on the
streams that actually own one (`decompressor_stream.py:390`, `codecs.py:204`) or preserve
that offset space (`codecs.py:747`, `verify.py:600`, `counting.py:91`,
`archive_stream.py:423`). Two more translate the offset space rather than preserving it:
`crypto.py:293` (ciphertext to plaintext) and `slice.py:261` (contiguous window;
`SharedView` inherits it). The duck-typing helper `ask_resume_offset` is generic `getattr`
plumbing, so it lives in `streamtools/binaryio.py` and is re-exported from `streams/resume.py`.
`slice.py:261` is a named exception: it translates a contiguous window (`start + target` in,
clamp at 0 out). That is generic offset arithmetic, not the table. The exception is written
down in `streamtools/__init__.py` because the import linter cannot see a concept leak —
which is the thread's real point, and why the exception is named rather than left for the
next reader to discover. The helper and the translation are archivey-specific at the
method name; they may move out of `streamtools` later if the package is lifted.
[#343](https://github.com/davitf/archivey/pull/343) has since pinned the behaviour as a
Hypothesis property. The translation itself is [#373](https://github.com/davitf/archivey/pull/373).

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
| [`typing-escape-hatches/`](../review/typing-escape-hatches/brief.md) | 3 suppressions, 22 `cast()`, 48 `Any`, 3 `TypeGuard`, 12–13 `assert isinstance` | **Excavation.** Its precedent is #324's finding 3: a `TypeGuard` that lied, which the checkers then believed and propagated. The commissioning figures here ("26 casts / 37 `Any`") were a grep; [`inventory.md`](../review/typing-escape-hatches/inventory.md) has the counted ones |
| [`exception-catchalls/`](../review/exception-catchalls/brief.md) | 30 marked blind `except` sites, in five patterns | **Verification.** Its own brief says recon found no smoking gun and warns against manufacturing severity |

**The typing brief's census was stale in three rows, and the inventory in #352 supersedes
it.** Read [`SUMMARY.md`](../review/typing-escape-hatches/SUMMARY.md) and
[`inventory.md`](../review/typing-escape-hatches/inventory.md) rather than the brief's
commissioning table:

- **S1 — the `CONTRIBUTING.md` suppression rule** is **fixed in #352**. The rule used to
  offer `# type: ignore[attr-defined]` as an example of a *specific* suppression, which it
  is not here: pyrefly does not validate the bracketed code, so that form silences the whole
  line. It now names `# pyrefly: ignore[<code>]` / `# ty: ignore[<code>]`, the forms that
  fail closed.
- **S2 — "both existing `src/` suppressions are dead, DELETE them"** is **done**. #324's
  `chore(types)` commit removed both; `grep -c "type: ignore" src/` is now 0.
- **S3 — "two more exist only on #324's branch, sequence after it merges"** has happened.
  Both are on `main` now (`streamtools/base.py`, `streams/peekable.py`), already in the
  `# pyrefly: ignore[bad-override]` form with inline reasons, which is the outcome the brief
  wanted rather than work it still needs. A third (`full_count.py`) has since joined them,
  same disposition.
- **S6 — the twelve hidden pyrefly warnings** are answered: they appear under
  `--min-severity=warn`, and none is a hidden error.

What survives is the larger half, and it is now a worklist rather than a population: staged
fix PRs 1–5 and 7, concentrated in `binaryio.py`, `tar_reader`, `zip_reader`, `iso_reader`
and `decompress`. Q1 is decided (public `extra` values are `object`) and lands with the
inventory.

## OpenSpec changes

| Change | Tasks | State |
| --- | --- | --- |
| `prefixed-archive-detection` | **32/68** | The only one in flight. Finish or explicitly park it before opening another detection change |
| `single-archive-source` | 0/31 | Merged 2026-09-23 via #402, proposal only. One `ArchiveSource` replaces the stack of source wrappers (borrow, full-count, the ISO bound) and absorbs the detection replay buffer (davi, 2026-09-22). Builds on #400 |
| `one-member-listing-per-reader` | 0/38 | Merged 2026-09-23 via #404, proposal only. The base reader owns one member list, filled by one backend walk. Fixes the unset `member_id` on streamed 7z and solid RAR members, and adds `ArchiveyConfig.read_link_targets` (default `True`, davi 2026-09-23) |
| `detection-evidence-ledger` | 0/70 | The big one. Rebuilds detection on graded evidence |
| `detection-result-surface` | 0/44 | **Blocked by the ledger** — it exposes what the ledger produces. Its own proposal says so |
| `archive-origin-reporting` | 1/34 | Merged as a proposal 2026-09-19 via #274. Overlaps `detection-result-surface` on `ArchiveInfo` |
| `bounded-source-spooling` | 0/31 | Merged 2026-09-19 via #251. Its four design questions are answered; subsumes the RAR stream-copy bound (`rar.md` §7) and the lazy stream-volume copy (shipped) |
| `bounded-password-confirmation` | 0/26 | In tree since #319. Ready to implement; closes most of **O12** |
| `single-file-open-time-validation` | 0/25 | Self-contained. Closes [`open-issues.md`](open-issues.md) **P15** and **P16** |
| `seekable-gzip-and-block-writing` | 0/24 | Self-contained, no `.openspec.yaml` (predates the schema). BGZF + mgzip random access, zero new dependencies |
| `rar5-stored-encrypted-native-read` | 0/24 | Merged 2026-09-18 via #347. Drops the `not info.is_encrypted` clause from `_can_direct_read` for RAR5. Amends ADR 0002 — the boundary is *decompression*, not *data* |
| `sevenzip-aes-tail-key-check` | 0/17 | After `bounded-password-confirmation`. Split out deliberately: the only piece resting on an empirical premise about writer padding, so the easiest to revert alone |
| `verification-integrity-mode` | 0/16 | Merged 2026-09-19 via #185, after 60 days open. The STRICT opt-in that guarantees a verdict — ADR 0014 names it as the vehicle for its own unfinished half |
| `rar-archive-offset-and-aes-cursor` | 0/12 | Merged 2026-09-18 via #356, the other half of the split fold change |
| `fold-rar-header-decrypt-stream` | 0/8 | Merged 2026-09-18 via #347, `skip_specs: true`. Answers #315 thread 3's merge half with an explicit bar: the fold lands only if the header caller's policy collapses to at most one new constructor argument, otherwise the outcome is to record the decision and close the change |

**Fifteen changes, 435 unstarted tasks, one of them 32 tasks in** (counted 2026-09-23 from
the `- [ ]` lines in each `tasks.md`). That is the largest single number on this page, and none
of it is blocked on a decision any more. The two new proposals both touch `base_reader.py`
and the source boundary, so they should not be implemented at the same time as each other or as
a sweep fix in those files.

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

#315 Wave 1 + parcels A–E + the 09-20..23 fixes ──> DONE (98 of 209 threads resolved)
        │
        ├──> parcel F (placement + odds)   threads 10/14 DONE: the five modules moved
        │                                  under backends/ (#443)
        ├──> five solid.py naming questions from 09-14   no parcel, no owner
        └──> S15-S25 drain   92 threads
                ├──> 13 waiting on a ruling (one decision item, taken one at a time)
                ├──> 4 ruled, not landed (config=, strict_archive_eof, .bin refusal, __module__)
                ├──> DecoderLimits (DONE, #398) ──> LZMA dictionary cap
                │                                └──> KDF derivation budget (needs a default)
                └──> 8 blocking findings ──> the release question

single-archive-source (#402) ──┐  both rewrite base_reader.py and the source boundary:
one-member-listing (#404) ─────┘  one at a time, and not beside a sweep fix in those files

#342 seekable AES-CBC ──> #347 rar5-stored-encrypted-native-read  (proposal, unscheduled)
                     └──> #347 fold-rar-header-decrypt-stream     (proposal, unscheduled)

prefixed-archive-detection (32/68) ──> detection-evidence-ledger ──> detection-result-surface
                                                 │                          │
                                                 │                          └──> #274 archive-origin-reporting
                                                 └──> 4 IDEAS.md §API entries retire

#251 design Q1-Q4 (maintainer) ──> bounded-source-spooling ──> bound the rar stream copy
                                                          └──> open-issues P11 closed
                                          (the rar stream-volume copy is already lazy)

Topic 8 (docs content) ∥ Topic 10 (catalogue) ──> Topic 6 (perf) ──> Topic 7 (capstone, last)
        │
        └── #243 closed, so #244 is uncontested; Topic 10 is unblocked

typing-escape-hatches ──> #352 inventory ──> #376, #377, #378, #384 merged ──> the rest,
        │                                                                file collisions only
exception-catchalls ──> (nothing; unblocked, not started)

sweep S0..S25 ──> 209 #315 threads, 111 open ──> new parcels, new changes
        │              (first pass over src/ complete 2026-09-20; draining is what is left)
        └── a second pass waits on those threads being drained, not on a decision

formats/tar.md, iso.md, single-file.md ──> more §10-style registers
docs/ prose + how-it-works.md ──> (nothing; skeleton, scope and claims all done)
```

**One block in this graph is waiting on a person rather than on work, and it is now the
biggest one:** the thirteen undecided S20–S25 findings. The threads 10/14 module-placement call
was answered on 2026-09-19 and the #251 design answers landed the same day, so both of the
previous revision's blockers are gone — what replaced them is larger. The docs wait on
nobody; they wait on someone starting the next page. Everything else is either running, or
ready for whoever picks it up next.

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

Worth stating plainly, because three snapshots in a row opened with "this is the largest block
of actionable work" about pools of very different sizes, and the one that is largest keeps
changing.

| | 2026-09-11 | 2026-09-17 | 2026-09-19 | 2026-09-21 | 2026-09-23 |
| --- | --- | --- | --- | --- | --- |
| #315 threads open | 47 | 10 | 20 | 140 (68 of 208 resolved) | **111** (98 of 209 resolved) |
| — of those, waiting on a maintainer ruling | *not tracked* | 2 | 2 | 15 | **13** |
| — of those, blocking | *not tracked* | 0 | 2 | 11 | **8** |
| Open PRs (excluding the #315 hub) | 9 | 4 | 4 | 8 | **0** |
| Dormant drafts | 5 | 2 | 0 | 0 | **0** |
| OpenSpec changes in tree, unimplemented | 8 | 6 | 12 | 12 | **14** |
| Reviews commissioned but not started | 2 | 2 | 1 | 1 | **2** (`exception-catchalls`, `api-freeze`) |
| `src/` never swept | *not tracked* | 88% (stated as 76%) | 55% | 0.4% (one file) | **1.2%** (three files, all arrived after the pass) |
| Handbook pages unwritten | *not tracked* | ~5 of ~7 | ~5 of ~7 | ~5 of ~7 | **~4 of ~7** |

**2026-09-21 → 09-23: the first two days in which the backlog shrank without the sweep adding
to it.** 29 threads closed, three blocking findings fixed, every pull request merged. Two
things grew: the OpenSpec task count (+73, 69 of them the two proposals davi asked for) and the unswept
files (+2, both new code). Neither is a regression — but the second will keep growing with every
fix PR that adds a module, so the three-file batch should run before the count is quoted in
any release note.

**The two rows that matter moved in opposite directions, and that is the whole story of the
week.** `src/` never swept went to nearly zero, and #315 threads open went from 20 to 140.
Those are
the same event: the reading finished, and what it found is now the backlog. A snapshot that
read only the first row would say the work is done; a snapshot that read only the second would
say it got seven times worse. Neither is right. The library has now been examined closely once
end to end, and the findings that produced are the work.

**Open PRs doubled without a dormant draft appearing**, which is the other thing worth noting:
fourteen merged in two days and seven are live, every one of them touched within a day. The
review loop is what changed — a decided finding now becomes a green PR in minutes rather than
days — so the constraint moved off implementation and onto deciding, which is exactly what the
"waiting on a ruling" row says.

**Two earlier rows moved the wrong way and both were good news.** Open threads went from 10 to
25 on 2026-09-17 because the sweep ran and found fifteen more, then back to 20 as the
already-fixed ones were resolved. Unimplemented OpenSpec changes went from 6 to 12 because #347
and #356 promoted parked RAR items into proposals and #251, #274 and #185 merged three more.

The one row that read like a burndown moved less than it looked: `src/` never swept went 88%
to 55% over 2026-09-19, not 76% to 65%. Both earlier figures counted six files as swept that
never were; the real movement came from two batches running, not from the arithmetic.

**The correctness picture changed too.** The 2026-09-17 snapshot said nothing in the open
threads was a correctness bug. That was true of the ten threads then open and is not true
now: S2-F1 and S2-F2 were confirmed hostile-input bugs with working triggers, and both are
fixed and registered as **O13** and **O14**. It is now emphatically not true: twelve findings
were blocking on 2026-09-21 and **eight are still open**, four of them a bounded-allocation or
wrong-bytes bug reachable from hostile input, in `volumes.py`, `listing_limits.py`, XZ and lzip.
The tar, ISO and PPMd ones are fixed (#396, #398). The 2026-09-17 reading was accurate about the ten threads then open; it was a statement
about what had been *looked at*, not about the library.

**The registers are not where the remaining work is.** The two rows added to the table above
are each larger than everything else on this page put together, and until this revision
neither appeared anywhere that answers "what is open?". A snapshot that counted only the
registers would read as nearly finished; the honest reading, once the first pass closed, is
that the whole library has now been examined closely once and the findings that produced are
the work.

**That is the input to the release question, and the answer to it changed this week.** The
previous revision said "nothing measured so far blocks a publication", and hedged it against an
unswept 55% that was unmeasured rather than known-good. That hedge has been cashed: the
remaining 55% was read, and it produced ten more blocking findings on top of the two that were
known — twelve raised, eleven still open after #380 closed S19-K8. So the release question is no longer a forecast from a base rate — the sample is the
whole population.

**What that means concretely.** The eight blocking findings are the list, and none of them is
open-ended: seven are a bounded fix in one or two files with no decision left to take, and the
eighth (S21-K10) needs one default number from davi. `DecoderLimits`, which the PPMd finding
was waiting on, landed in #398. Against them, `P15`/`P16` are
specced in `single-file-open-time-validation` and `bounded-password-confirmation` closes most
of a threat-model entry — 51 tasks between the two, and both predate the sweep.

**An alpha is still defensible; "a clean state" now has a definition it did not have before.**
It is those eight, plus the thirteen undecided findings that could turn into more, plus —
by davi's ruling on 2026-09-21 — every non-blocking finding on the hub. That is a
countable amount of work rather than an unbounded one, which is the thing the sweep bought.
The detection changes remain separately expensive to land *after* people depend on the current
surface, because the ledger defines the vocabulary the other three report in.

## Plan of attack

**Next, as of 2026-09-23.** davi's order for 0.2.0 is every sweep finding, blocking ones first
(2026-09-21). With no PR open, this is the proposed sequence. The waves below it are the
longer record and are unchanged except where marked.

1. **The two silent-wrong-bytes seeks, S18-K4 (xz) and S23-K3 (lzip), in one PR.** No
   decision needed, and wrong bytes with no error is the worst class this library has.
   Ordinary `xz -T0` output reaches the first.
2. **The other blocking fixes that need no ruling**, one small PR each or batched by file:
   S24-K1 + S24-K7 (CLI escaping), S25-K5 (the password provider guard, per thread), S15-K1
   (describe the missing-volume gap instead of enumerating it).
3. **The LZMA dictionary cap on `DecoderLimits`.** The same shape as the PPMd window, and the
   guard #398 built is waiting for it. No ruling needed.
4. **The four ruled-but-not-landed changes**: drop `extract_all(config=)` (blocking S20-K15),
   remove `strict_archive_eof`, refuse raw `.bin` images by name, pin `__module__`. All four
   change public surface, which is why they belong before the 0.2.0 freeze.
5. **`single-archive-source`, then `one-member-listing-per-reader`.** Both rewrite
   `base_reader.py` and the source boundary, so they go one at a time. Some open sweep
   threads sit in code the first one changes or removes: S23-K6 is about `FullCountStream` and
   `PeekableStream`, which it deletes, and S19-K2 is about `detection_workspace.py`, which it
   rewrites. Fix those as part of it, not before.
6. **The remaining non-blocking threads**, batched by file as #393 did, skipping files a
   change in step 5 is about to rewrite. Sweep the three unswept files at the same time.

**Waiting on davi:** a default for the symlink-target length cap, which makes S21-K10 the one
blocking finding that cannot move without him (the same cap is probably the fix for the
uncapped link-target reads found while reviewing #404); a default for the KDF derivation
budget; and the thirteen S20–S25 rulings.

Ordered by what unblocks the most, then by what is cheapest to verify.

**Wave 0 — clear the desk. Done 2026-09-19.** Doc-only, no decisions needed.
1. This page, plus the two register cleanups it names (rar.md §7 duplicate, IDEAS.md
   Windows-UnRAR entry). *Landed.*
2. Merge **#319** *(done, 2026-09-11)* and **#297** *(done, 2026-09-19 — it merged once the
   prose conclusions became a runnable script, which was the objection against it)*.
3. Close **#101** *(done, 2026-09-11)*, **#243** *(done)*, **#187** *(done — but see the
   native-stress section: the criterion it needed was not recorded)*.
4. **Mark the five hub threads whose fixes merged in #349 and #350** — S1-F1, S1-F2,
   S1-F3, S2-F1, S2-F2. *Done 2026-09-19*, each verified against `main` first; see the #315
   section above. The ten remaining S1/S2 threads are tracked internally as one batch.

**Wave 1 — the four verified bugs. Done.**
[#324](https://github.com/davitf/archivey/pull/324), merged 2026-09-11: threads 17, 21, 38, 39
fixed red-green, plus F5–F13 from the PR's own review cycle. Thread 16 resolved with it. It
also surfaced the two reviews now commissioned in #325, and left `src/` with zero
`# type: ignore`.

**Wave 2 — drain #315 in six parcels. A through E are done.** One parcel remains.
Thread 56 (the post-drain orphan) closed with [#365](https://github.com/davitf/archivey/pull/365) / ARC-12.

| Parcel | Files | Threads | State |
| --- | --- | --- | --- |
| ~~A — stream bases~~ | `base.py`, `locked.py`, `__init__.py` | 18, 19, 20, 35, 36, 37, 42, 45 | **Done** — [#326](https://github.com/davitf/archivey/pull/326) |
| ~~B — slice + shared~~ | `slice.py`, `shared.py` | 27–34 | **Done** — [#328](https://github.com/davitf/archivey/pull/328) |
| ~~C — binaryio + solid~~ | `binaryio.py`, `solid.py` | 22, 23, 25, 26, 40, 41, 55 | **Done** — [#329](https://github.com/davitf/archivey/pull/329) |
| ~~D — RAR parser~~ | `backends/rar_parser.py` | 1, 2, 4, 5, 6, 7, 8, 9 | **Done** — [#332](https://github.com/davitf/archivey/pull/332). Threads 1 and 2 were the two possible header-decrypt bugs; both measured against fixtures and neither was one |
| ~~E — RAR reader~~ | `backends/rar_reader.py` | 43, 44, 46, 47, 48, 49 | **Done** — [#336](https://github.com/davitf/archivey/pull/336) |
| **F — placement + odds** | `backends/zip_aes.py`, `volumes.py`, `reader_state.py` | 11, 12 | **Placement done** (threads 10/14, ruled 2026-09-19; the five modules moved under `backends/` in [#443](https://github.com/davitf/archivey/pull/443)). Thread 15 was closed by [#445](https://github.com/davitf/archivey/pull/445). Two threads left. 51, 53 and 54 were closed by [#440](https://github.com/davitf/archivey/pull/440), 13 by #374, and thread 3 left when #342 answered it. |
| ~~(orphan)~~ | `streamtools/base.py` | 56 | **Done** — [#365](https://github.com/davitf/archivey/pull/365), merged 2026-09-21. Both flags now use the class-flag-plus-constructor-override pattern `_SUBCLASS_CLOSES_INNER` already had |
| ~~(new)~~ | five `solid.py` questions from 2026-09-14 | — | **Done** — [#439](https://github.com/davitf/archivey/pull/439), together with S18-K8 (two asserts in `ArchiveStream._collapse_nested`) |

**Parcel F's prompt should carry one correction** the follow-up comments make and the
opening comments do not: thread 3 is closed and out of scope. The other corrections recorded
here (threads 51, 53 and 54) no longer apply: those three were done in
[#440](https://github.com/davitf/archivey/pull/440).

**Wave 2c — the S15–S25 drain. The largest block on this page.** 92 threads on 2026-09-23,
eight of them blocking, thirteen rulings owed. It is not a parcel because it is not one shape: batch
it by file the way the merged PRs did, one or two files per PR, and take the rulings one at a
time rather than as a list. The pattern that worked on 2026-09-20 — decide, implement, review
in a separate session, merge — turned three decided findings into merged PRs in about seven
minutes each, so **deciding is the constraint, not implementing.**

**Wave 2b — the two #325 reviews. One has run and is half landed; one has not started.**
Disjoint sources, both `src/`-only audits rather than changes, so they never contended with
the #315 drain in the way two refactors would. `typing-escape-hatches` ran on 2026-09-17, its
inventory merged as #352, and the fixes have landed in four waves — #376, #377, #378 and #384
are all on `main`, and the rest is held on file collisions only (`tar_reader.py`,
`binaryio.py`, and `volumes.py` against #374). `exception-catchalls` needed nothing before it
started and still does not; it is the clearest thing left to hand out.

**Wave 3 — the unblocked OpenSpec changes.** Three, none waiting on anyone:
`single-file-open-time-validation` (closes P15 + P16, 25 tasks),
`bounded-password-confirmation` (26, closes most of threat-model **O12**), and
`seekable-gzip-and-block-writing` (24). `full-count-non-seekable-sources` left this list by
being implemented and archived on 2026-09-12. The two proposals merged on 2026-09-23,
`single-archive-source` (31) and `one-member-listing-per-reader` (38), are unblocked too, but
they are step 5 of the list at the top of this section rather than this wave, because they
rewrite the files the sweep fixes touch.

**Wave 4 — decisions, then detection.** Answer #251's four questions. Finish or park
`prefixed-archive-detection`. Then `detection-evidence-ledger` → `detection-result-surface`
→ #274, in that order, retiring the four `IDEAS.md` §API entries as the ledger absorbs them.

**Wave 6 — the docs, continuously and in parallel with everything above.** Not a wave in the
sense the others are: a long-running programme that should have one page in flight at a time
rather than a slot in the order. `7z.md` is written (see §1 above); `tar.md` is the next page
by value, because the stdlib-leniency question `open-issues.md` **P3** is about has no other
home. The user guide's remaining prose is the one item here with no agent-shaped unit of work
defined for it yet. **The sweep half of this wave is spent**: the reading is done, and the
second pass davi gated on it also waits on the drain, so there is no batch to schedule.

**Wave 5 — the review topics.** Topic 8 ∥ Topic 10 → Topic 6 → Topic 7 last, per
[`review/STATUS.md`](../review/STATUS.md). Unchanged; this page does not re-rank them. Note
that `review/STATUS.md` still carries a 2026-08-15 header and describes #187, #243 and the
two-catalogue standoff as live — it is the register most in need of its own refresh.

**Not scheduled, on purpose:** `verification-integrity-mode` (#185) needs a keep-or-kill
judgement against ADR 0014 before it earns a slot; the `unrar` mask port is marked *very
low priority — remaining names are adversarial* in [`IDEAS.md`](IDEAS.md); the
glob-concatenation knob shipped (refuse by default, config flag as the hatch);
[`open-issues.md`](open-issues.md) **P2/P3/P4** belong to the native
streaming ZIP theme, which is an `IDEAS.md` entry rather than a scheduled change.
