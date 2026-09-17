# Open work inventory and sequencing

> **A dated snapshot, not a register.** Snapshot: **2026-09-17** against `main` @ `94468bd0`.
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

Eleven registers hold open work. The count that matters is not the total — it is that **two
maintainer answers gate everything still parked**, and that the pool which dominated the last
two snapshots has gone from 47 open threads to 10.

| Register | Open items | Health |
| --- | --- | --- |
| Open PRs | 4 live, 2 dormant drafts, 1 hub | #101, #187 and #243 **closed** 2026-09-11; 22 PRs merged since the first snapshot. #331 and #347 are new |
| [#315](https://github.com/davitf/archivey/pull/315) review threads | 56 total, **46 resolved, 10 open** | Parcels A–E closed 44 between 2026-09-11 and today. Everything left is parcel F plus one new thread |
| `openspec/changes/` (7 active) | 6 unimplemented, 1 half-done | `prefixed-archive-detection` is 31/68; the rest are 0/N. #347 proposes two more |
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

**[`IDEAS.md`](IDEAS.md) is not backlog.** 55 entries across six sections, and its job is to
stop the same speculative idea being re-derived. Nothing in it is late. Treat an `IDEAS.md`
entry as work only when something else pulls it in — which is what #347 does for two RAR
entries, and what the native-stress question below still needs. Do not read the 55 as a debt
figure.

## Open PRs

| PR | What | Verdict |
| --- | --- | --- |
| [#315](https://github.com/davitf/archivey/pull/315) | `[COMMENT ONLY]` full-codebase review hub | **Not a PR to merge.** Head *is* `main` (base is an orphan `empty-base`), so it re-renders against current `main` automatically — there is nothing to merge into it. 56 threads, 10 open |
| [#347](https://github.com/davitf/archivey/pull/347) | OpenSpec: `rar5-stored-encrypted-native-read` + `fold-rar-header-decrypt-stream` | **Merge as proposals.** Draft, docs-only, opened today out of #342's review. Promotes the two RAR items #342/#343/#344 left in `IDEAS.md`. Neither is scheduled by merging |
| [#331](https://github.com/davitf/archivey/pull/331) | Previous refresh of this page (parcels A–C) | **Superseded by this revision.** Same file, snapshot `c3259dff`; parcels D and E have landed since. Close it rather than merging both |
| [#297](https://github.com/davitf/archivey/pull/297) | Capability declaration vs corpus behaviour (387-line investigation) | **Merge.** Adds one `dev-docs/investigations/` page plus its index row. No dependencies |
| [#274](https://github.com/davitf/archivey/pull/274) | OpenSpec `archive-origin-reporting` proposal | **Merge as a proposal.** Specs-first, nothing implemented. Overlaps `detection-result-surface` — see the graph |
| [#251](https://github.com/davitf/archivey/pull/251) | OpenSpec `bounded-source-spooling` | **Blocked on four maintainer answers**, all in its `design.md` §Open questions. Q1 (the default limit) decides which working RAR-from-stream reads start failing. Merging the proposal does not need them; *scheduling* does |
| [#244](https://github.com/davitf/archivey/pull/244) | Topic 10 problem catalogue — 57 `design.md` files mined | **Now uncontested — merge or close it on its own merits.** #243 was closed 2026-09-11, so this is the only candidate catalogue. `main` still carries only `brief.md` + `harvest/` |
| [#185](https://github.com/davitf/archivey/pull/185) | OpenSpec `verification-integrity-mode` (STREAMING default, STRICT opt-in) | **Live but unranked.** July proposal, never reviewed. Decide whether it survives ADR 0014 before spending more on it |

**Three PRs were closed on 2026-09-11** — #101 (superseded by `formats/rar.md` §9), #243
(the thinner of the two catalogues) and #187 (native stress harnesses). That clears Wave 0
items 3 and 4. #187's closure does **not** answer the question underneath it; see
[Native codec stress coverage](#native-codec-stress-coverage-its-own-evaluation).

The two surviving dormant drafts (#244, #185) still report `updated_at` on 2026-08-23, one
minute apart. That is a bulk repository event, not activity: neither has been worked since
creation.

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
- **[`IDEAS.md`](IDEAS.md) §Testing "Decide what native-codec stress coverage is for" still
  reads as though #187 were open.** It says the criterion "would resolve PR #187 as
  close-with-criterion-recorded". #187 was closed on 2026-09-11 **without** the criterion
  being recorded, so the entry's framing is now backwards: the PR is gone and the question
  is not. Worth a one-line rewrite the next time that file is touched.
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

## #315 — the 56 threads

Once the largest pool of actionable work; now nearly drained. Threads 1–15 are maintainer
questions from 2026-09-07; 16–55 are an agent review pass from 2026-09-08 concentrated on
`streamtools/` and the two native backends; **thread 56 was added 2026-09-13** and is the
only one raised since.

**Forty-six of fifty-six are resolved.** Parcels A–E closed 44 of them between 2026-09-11 and
2026-09-17, on top of the eight closed earlier. Every `streamtools/` thread from the original
pass is done, and both RAR files are done.

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
**Neither has started.** Six days on, `brief.md` is still the only file in each directory,
while eleven PRs merged around them. They are not blocked by anything — they are simply not
being picked up, which makes them the clearest candidate for the next prompt after parcel F.

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
| `single-file-open-time-validation` | 0/25 | Self-contained. Closes [`open-issues.md`](open-issues.md) **P15** and **P16** |
| `seekable-gzip-and-block-writing` | 0/24 | Self-contained, no `.openspec.yaml` (predates the schema). BGZF + mgzip random access, zero new dependencies |
| `bounded-source-spooling` (#251) | 0/33 | Blocked on four `design.md` answers. Subsumes rar `§10` **#6 layer 2** and **#21** |
| `archive-origin-reporting` (#274) | 0/? | Proposal only. Overlaps `detection-result-surface` on `ArchiveInfo` |
| `bounded-password-confirmation` | 0/26 | In tree since #319 merged. Ready to implement; closes most of **O12** |
| `sevenzip-aes-tail-key-check` | 0/17 | After the above. Split out deliberately: the only piece resting on an empirical premise about writer padding, so the easiest to revert alone. #346 pinned what the task 1.1 guard must check |
| `rar5-stored-encrypted-native-read` (#347) | 0/? | **Proposed today.** Drop the `not info.is_encrypted` clause from `_can_direct_read` for RAR5 and decrypt the direct `SharedView` slice in process. Every piece is already parsed; #342 verified the result byte-for-byte against `unrar p`. RAR5-only, and its `design.md` says why. Amends ADR 0002 — the boundary is *decompression*, not *data* |
| `fold-rar-header-decrypt-stream` (#347) | 0/? | **Proposed today**, `skip_specs: true`. Answers #315 thread 3's merge half with an explicit bar: the fold lands only if the header caller's policy collapses to at most one new constructor argument, otherwise the outcome is to record the decision and close the change |

**`full-count-non-seekable-sources` was proposed, implemented and archived in three days** —
#330 → #333/#334/#335, archived `2026-09-12-full-count-non-seekable-sources`. It is the
fastest a change has gone from parcel finding to archived, and worth noting because it is the
shape that works: a narrow boundary found during a review parcel, specced immediately while
the context was live.

**Three detection changes touch the same surface.** `prefixed-archive-detection` (in flight),
`detection-evidence-ledger`, `detection-result-surface`, plus `archive-origin-reporting` and
four [`IDEAS.md`](IDEAS.md) §API entries that the ledger explicitly absorbs
(`FormatInfo.corroborated`, extension-first ordering, "content decides, extension
corroborates", "presence and value are different questions"). This is the one place where
doing things in the wrong order costs real rework: **the ledger defines the vocabulary the
other three report in.**

## What blocks what

```
bounded-password-confirmation ──> sevenzip-aes-tail-key-check ──> O12 closed

#315 Wave 1 + parcels A, B, C, D, E ──> DONE (46 of 56 threads resolved)
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
```

**Only two things in this graph are waiting on a person rather than on work:** the #251
`design.md` answers Q1–Q4, and the threads 10/14 module-placement call. Everything else is
either running, or ready for whoever picks it up next.

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

Durable home for the question: [`IDEAS.md`](IDEAS.md) §Testing, whose entry still describes
#187 as open and should be rewritten to state the criterion as a decision to make rather than
as a verdict on a PR that no longer exists.

## How much is actually left

Worth stating plainly, because two snapshots in a row opened with "this is the largest block
of actionable work" about a pool that is now nearly empty.

| | 2026-09-11 | 2026-09-17 |
| --- | --- | --- |
| #315 threads open | 47 | **10** |
| Open PRs (excluding the #315 hub) | 9 | **4** |
| Dormant drafts | 5 | **2** |
| OpenSpec changes in tree, unimplemented | 8 | 6 (+2 proposed in #347) |
| Reviews commissioned but not started | 2 | 2 |

**Six days moved 37 threads and closed five PRs.** Nothing in the remaining ten #315 threads
is a correctness bug: the last two candidates (threads 1 and 2) were measured against fixtures
in parcel D and neither was one. What is left across the whole page is renames, docstrings,
one placement decision, one layering fix (thread 15), one performance question
(thread 13's per-read bisect), and a stack of specced-but-unimplemented changes.

**That is the input to the release question.** The blocking work for a first publication is
not this page's tail — it is whichever of the unimplemented OpenSpec changes are judged to
change public API or behaviour after 0.2.0. `single-file-open-time-validation` closes two
confirmed bugs (P15, P16) and `bounded-password-confirmation` closes most of a threat-model
entry; those are the two with a claim on a release. The detection changes are the ones that
would be expensive to land *after* people depend on the current surface, because the ledger
defines vocabulary the other three report in.

## Plan of attack

Ordered by what unblocks the most, then by what is cheapest to verify.

**Wave 0 — clear the desk. Almost done.** Doc-only, no decisions needed.
1. This page, plus the two register cleanups it names (rar.md §7 duplicate, IDEAS.md
   Windows-UnRAR entry). *Landed.*
2. Merge **#319** *(done, 2026-09-11)*. **#297 is still open** — docs-only, clean, and the
   oldest thing on this list. It has been one merge away for twelve days.
3. Close **#101** *(done, 2026-09-11)*, **#243** *(done)*, **#187** *(done — but see the
   native-stress section: the criterion it needed was not recorded)*.
4. Close **#331**, this page's own previous revision, superseded by this one.

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

**Wave 5 — the review topics.** Topic 8 ∥ Topic 10 → Topic 6 → Topic 7 last, per
[`review/STATUS.md`](../review/STATUS.md). Unchanged; this page does not re-rank them. Note
that `review/STATUS.md` still carries a 2026-08-15 header and describes #187, #243 and the
two-catalogue standoff as live — it is the register most in need of its own refresh.

**Not scheduled, on purpose:** `verification-integrity-mode` (#185) needs a keep-or-kill
judgement against ADR 0014 before it earns a slot; rar `§10` **#18** is marked *very low
priority — remaining names are adversarial*; rar `§10` **#19** is a public knob and wants a
config decision; [`open-issues.md`](open-issues.md) **P2/P3/P4** belong to the native
streaming ZIP theme, which is an `IDEAS.md` entry rather than a scheduled change.
