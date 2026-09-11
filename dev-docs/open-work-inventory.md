# Open work inventory and sequencing

> **A dated snapshot, not a register.** Snapshot: **2026-09-11** against `main` @ `e50ffdd4`.
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
> closest, and it ranks *review topics* only — it was last revised 2026-08-15, before the
> five OpenSpec changes and the #315 review pass existed.

## The shape of it

Eleven registers hold open work. The count that matters is not the total — it is that
**four items block twelve others**, and roughly a fifth of what is written down is already
dead.

| Register | Open items | Health |
| --- | --- | --- |
| Open PRs (10) | 4 live, 5 dormant drafts, 1 hub | #319 and #324 merged since the first snapshot; drafts last touched 2026-08-23 by a bulk event, not by work |
| [#315](https://github.com/davitf/archivey/pull/315) review threads | 54 total, **5 resolved, 49 open** | Wave 1 closed four (#324); thread 16 self-resolved. Still the largest single block of actionable work |
| `openspec/changes/` (5 active) | 4 unimplemented, 1 half-done | `prefixed-archive-detection` is 31/68; the rest are 0/N |
| [`open-issues.md`](open-issues.md) | 8 product candidates, 1 deliberate docs gap | P15/P16 are specced; P2/P3/P4/P5 are unowned |
| [`formats/rar.md`](formats/rar.md) `§10` | 4 of 21 (#6 layer 2, #18, #19, #21) | Healthy — 17 closed with PR links |
| [`formats/rar.md`](formats/rar.md) `§7` | 5 open questions | Contains a **duplicated entry** (fixed in this pass) |
| [`IDEAS.md`](IDEAS.md) | 57 entries | A park, **not a queue** — see below |
| [`review/backlog.md`](../review/backlog.md) | 3 PR parks, 7 archived-review parks, Topics 6/7 | #320 F2 is the only one with a live question |
| [`review/STATUS.md`](../review/STATUS.md) | Topics 8 + 10 in flight, docs IA in flight, **+2 commissioned 2026-09-11** | Ranked list predates the current OpenSpec set |
| [`review/typing-escape-hatches/`](../review/typing-escape-hatches/brief.md) | ~81 typing hatches + 13 `assert isinstance` | **Two census rows already stale** — see below |
| [`review/exception-catchalls/`](../review/exception-catchalls/brief.md) | 30 marked blind `except` sites | A verification review; its own brief says a large "actually fine" section is the expected outcome |
| [`threat-model.md`](threat-model.md) | `O*` register | O12 is closed by `sevenzip-aes-tail-key-check`, registered in the merged #319 |
| [`known-issues.md`](known-issues.md) | Forensics, not a worklist | No action items of its own |

**[`IDEAS.md`](IDEAS.md) is not backlog.** 57 entries across six sections, and its job is to
stop the same speculative idea being re-derived. Nothing in it is late. Treat an `IDEAS.md`
entry as work only when something else pulls it in — which happens for exactly two entries
below. Do not read the 57 as a debt figure.

## Open PRs

| PR | What | Verdict |
| --- | --- | --- |
| [#315](https://github.com/davitf/archivey/pull/315) | `[COMMENT ONLY]` full-codebase review hub | **Not a PR to merge** — head *is* `main`. A container for 54 threads, 49 still open. Never close it while threads are open; drain them into fix PRs |
| [#297](https://github.com/davitf/archivey/pull/297) | Capability declaration vs corpus behaviour (387-line investigation) | **Merge.** Adds one `dev-docs/investigations/` page plus its index row. No dependencies |
| [#274](https://github.com/davitf/archivey/pull/274) | OpenSpec `archive-origin-reporting` proposal | **Merge as a proposal.** Specs-first, nothing implemented. Overlaps `detection-result-surface` — see the graph |
| [#251](https://github.com/davitf/archivey/pull/251) | OpenSpec `bounded-source-spooling` | **Blocked on four maintainer answers**, all in its `design.md` §Open questions. Q1 (the default limit) decides which working RAR-from-stream reads start failing. Merging the proposal does not need them; *scheduling* does |
| [#244](https://github.com/davitf/archivey/pull/244) | Topic 10 problem catalogue — 57 `design.md` files mined | **Pick one of #243/#244 and close the other.** They are competing attempts at the same deliverable |
| [#243](https://github.com/davitf/archivey/pull/243) | Topic 10 problem catalogue — early checkpoint sample | The earlier and thinner of the pair. `main` carries only `brief.md` + `harvest/`; neither `catalogue.md` nor `sources.md` exists, so nothing has been chosen yet |
| [#187](https://github.com/davitf/archivey/pull/187) | rapidgzip + inflate64 native stress harnesses | **Do not decide in isolation.** It is a coverage question wearing a keep-or-close mask — see [Native codec stress coverage](#native-codec-stress-coverage-its-own-evaluation) |
| [#185](https://github.com/davitf/archivey/pull/185) | OpenSpec `verification-integrity-mode` (STREAMING default, STRICT opt-in) | **Live but unranked.** July proposal, never reviewed. Decide whether it survives ADR 0014 before spending more on it |
| [#101](https://github.com/davitf/archivey/pull/101) | RAR `unrar` piping vs temp-file investigation | **Close.** Superseded — see below |

The five drafts (#243, #244, #187, #185, #101) all report `updated_at` within one minute of
each other on 2026-08-23. That is a bulk repository event, not activity: none has been worked
since creation.

## Already dead

Checked against `main`, not inferred from the documents that mention them.

- **#101 is superseded by its own successor page.** [`formats/rar.md`](formats/rar.md) §9
  says so outright: *"[PR #101], which was never merged; its conclusions are stated here and
  its measurements are what the first script re-runs, so the PR is provenance rather than a
  live reference."* The measurements live in `scripts/exploration/rar_unrar_input_matrix.py`.
  Nothing is lost by closing it.
- **#101 and #187 both write into `docs/internal/`, which no longer exists.** The docs IA
  migration (#221/#222) moved that tree to `dev-docs/`. Neither PR applies as written.
- **#187's shared-harness scaffolding is superseded in pattern.** `main` now carries
  `.github/workflows/ppmd-native-stress.yml` + `scripts/ppmd_native_stress.py`,
  `rapidgzip-truncation-sweep.yml` + `scripts/rapidgzip_truncation_sweep.py`, and
  `atheris-fuzz.yml`. The idea of a shared native-stress harness was adopted; this PR's
  version of it was not. **What the PR still proposes uniquely is a judgement, not a file** —
  see the section below.
- **#315 thread 50 is fixed.** `_open_folder_pipeline` is gone from `src/`; the only
  surviving mention is a comment in `tests/test_sevenzip_reader.py:754` recording that it
  *used to* exist.
- **#315 thread 52 is fixed by [#318](https://github.com/davitf/archivey/pull/318).**
  Password confirmation no longer materialises the folder; `sevenzip_reader.py:661` records
  the chunked replacement and its measured ~3× peak.
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

## #315 — the 54 threads

The largest single pool of actionable work, and the only one with verified correctness bugs
in it. Threads 1–15 are maintainer questions from 2026-09-07; 16–54 are an agent review pass
from 2026-09-08 concentrated on `streamtools/` and the two native backends.

| Group | Threads | Character |
| --- | --- | --- |
| `streamtools/` internals | 16–42 (27) | Layering, duplication, two-classes-in-one-name, and four real bugs |
| `backends/rar_reader.py` | 43–49 (7) | Hand-rolled `SlicingStream`, duplicated walks, a 115-line function |
| `backends/rar_parser.py` | 1–2, 4–9 (8) | Maintainer questions: explain, rename, or delete |
| `backends/sevenzip_reader.py` | 50–54 (5) | Two already fixed; one perf, two clarity |
| Module placement | 10, 14 | Should `rar_detect.py` / `zip_aes.py` move under `backends/` or a detection package? |
| Crypto consolidation | 3, 15 | A dead AES-CBC class, and `zip_aes.py` bypassing the crypto module |
| Explain-this | 11, 12, 26, 29, 35, 41, 44, 49, 51 | Docstring and comment work, no behaviour change |
| Performance | 13 | `volumes.py` bisects on every read; should cache on seek |

**The four verified bugs are fixed** — [#324](https://github.com/davitf/archivey/pull/324),
merged 2026-09-11, threads 17/21/38/39 resolved. Kept here because the PR's own review cycle
added findings F5–F13 on top, and because the shape of each is the precedent the two new
reviews in #325 were commissioned from.

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

Threads 1 and 2 are also potential bugs rather than questions — whether a RAR header-decrypt
offset accounts for `_buf` and for encrypted block boundaries — but answering them needs the
parser read that threads 5–9 also want, so they belong to one RAR-parser pass.

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

**Thread 42 still stands on that one item.** `nearest_resume_offset`
(`streamtools/base.py:149`, declined in `slice.py:190`) names `ArchiveStream._maybe_warn_rewind`
and a seek-point table — both archivey concepts — inside the package whose docstring says
nothing here knows about the rest of archivey. `DelegatingStream` forwards it by default, so
every wrapper inherits it. The proposed narrow fix is to move the forwarding onto the wrappers
that actually sit in a decompressed stream's chain. **This is the one concept leak the import
rule cannot see**, which is the thread's real point: the rule that gets enforced by tooling is
the one that was never violated.

**`src/` now has zero `# type: ignore`.** #324 deleted both dead ones and expressed the two
live suppressions as `# pyrefly: ignore[bad-override]` with inline reasons. That matters
because pyrefly does not validate the code inside `# type: ignore[...]` brackets — a bogus
code still silences the line, so that form is a blanket suppression in this repo while
`# pyrefly: ignore[<code>]` fails closed.

## The two reviews commissioned in #325

Both against `8e88e4f`, both `src/`-only, disjoint sources, designed to run in parallel.

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
| `bounded-password-confirmation` (#319) | 0/? | Merge the proposal, then implement. Closes most of **O12** |
| `sevenzip-aes-tail-key-check` (#319) | 0/59 | After the above. Split out deliberately: the only piece resting on an empirical premise about writer padding, so the easiest to revert alone |

**Three detection changes touch the same surface.** `prefixed-archive-detection` (in flight),
`detection-evidence-ledger`, `detection-result-surface`, plus `archive-origin-reporting` and
four [`IDEAS.md`](IDEAS.md) §API entries that the ledger explicitly absorbs
(`FormatInfo.corroborated`, extension-first ordering, "content decides, extension
corroborates", "presence and value are different questions"). This is the one place where
doing things in the wrong order costs real rework: **the ledger defines the vocabulary the
other three report in.**

## What blocks what

```
#319 (merge) ──> bounded-password-confirmation ──> sevenzip-aes-tail-key-check ──> O12 closed
                                                            │
                                                            └──> seekable AES-CBC stream (thread 3)

#315 threads 38, 39, 21, 17 ─────> (nothing; land now, one fix PR each)

#315 streamtools cleanup (16-42) ──> #315 rar_reader cleanup (43-49)
        │                                    ("this is SlicingStream rebuilt by hand"
        │                                      needs SlicingStream settled first)
        └──> thread 3 (dead AES-CBC class) ──> thread 15 (zip_aes via crypto module)

prefixed-archive-detection (31/68) ──> detection-evidence-ledger ──> detection-result-surface
                                                 │                          │
                                                 │                          └──> #274 archive-origin-reporting
                                                 └──> 4 IDEAS.md §API entries retire

#251 design Q1-Q4 (maintainer) ──> bounded-source-spooling ──> rar §10 #6 layer 2
                                                          └──> rar §10 #21
                                                          └──> open-issues P11 closed

Topic 8 (docs content) ∥ Topic 10 (catalogue) ──> Topic 6 (perf) ──> Topic 7 (capstone, last)
        │
        └── pick #243 or #244 first; Topic 10 cannot proceed with two candidate catalogues
```

Nothing else has a hard dependency. `single-file-open-time-validation` and
`seekable-gzip-and-block-writing` are both fully independent — they are the two changes to
hand someone who wants work that blocks on no decision.

## Native codec stress coverage (its own evaluation)

**#187 is not a keep-or-close call, and treating it as one is how it stalled for seven weeks.**
The PR adds native stress harnesses for rapidgzip and inflate64. Deciding it needs an answer
to a question nobody has asked yet: *which native codecs warrant which kind of hostile-input
coverage, and why?* That is a scoped piece of work with a written output, not a line item.

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
harness is built when an upstream defect is observed, not before"*, which would resolve #187
as **close, with the criterion recorded** rather than as a judgement about the code in it.

Durable home for the question: [`IDEAS.md`](IDEAS.md) §Testing.

## Plan of attack

Ordered by what unblocks the most, then by what is cheapest to verify.

**Wave 0 — clear the desk.** Doc-only, no decisions needed.
1. This page, plus the two register cleanups it names (rar.md §7 duplicate, IDEAS.md
   Windows-UnRAR entry). *Landed.*
2. Merge **#319** *(done, 2026-09-11)* and **#297** *(still open, docs-only and clean)*.
3. Close **#101** (superseded by rar.md §9).
4. One maintainer call: pick **#243 or #244**. (**#187** is deliberately not a Wave 0 call —
   it needs the native-stress evaluation above first.)

**Wave 1 — the four verified bugs. Done.**
[#324](https://github.com/davitf/archivey/pull/324), merged 2026-09-11: threads 17, 21, 38, 39
fixed red-green, plus F5–F13 from the PR's own review cycle. Thread 16 resolved with it. It
also surfaced the two reviews now commissioned in #325, and left `src/` with zero
`# type: ignore`.

**Wave 2 — drain #315 by file, not by theme.** 49 threads left. `streamtools/` first
(16–42, five now closed), because `rar_reader.py`'s findings (43–49) are stated as "this is
`SlicingStream` rebuilt by hand" and cannot be settled until `SlicingStream` is. Then
`rar_reader.py`, then the RAR-parser questions (1–2, 5–9) as one reading pass, then the
placement questions (10, 14) as one mechanical move. Resolve threads 50 and 52 with a pointer
to the commits that fixed them.

*Start with thread 42's surviving item* — moving `nearest_resume_offset` forwarding off
`DelegatingStream` — because it is the one finding in the group that changes a boundary rather
than tidying inside one, and every later `streamtools/` cleanup is easier once the base class
is honest.

**Wave 2b — the two #325 reviews, in parallel with Wave 2.** Disjoint sources, and both are
`src/`-only audits rather than changes, so they do not contend with the #315 drain for the same
files in the way two refactors would. **Refresh the typing brief's census first** (S2 and S3
are stale, see above) — a five-minute edit that stops the reviewer's first hour going into
rediscovery. `exception-catchalls` needs nothing before it starts.

**Wave 3 — the two unblocked OpenSpec changes.** `single-file-open-time-validation` (closes
P15 + P16) and `seekable-gzip-and-block-writing`. Neither waits on anyone.

**Wave 4 — decisions, then detection.** Answer #251's four questions. Finish or park
`prefixed-archive-detection`. Then `detection-evidence-ledger` → `detection-result-surface`
→ #274, in that order, retiring the four `IDEAS.md` §API entries as the ledger absorbs them.

**Wave 5 — the review topics.** Topic 8 ∥ Topic 10 → Topic 6 → Topic 7 last, per
[`review/STATUS.md`](../review/STATUS.md). Unchanged; this page does not re-rank them.

**Not scheduled, on purpose:** `verification-integrity-mode` (#185) needs a keep-or-kill
judgement against ADR 0014 before it earns a slot; rar `§10` **#18** is marked *very low
priority — remaining names are adversarial*; rar `§10` **#19** is a public knob and wants a
config decision; [`open-issues.md`](open-issues.md) **P2/P3/P4** belong to the native
streaming ZIP theme, which is an `IDEAS.md` entry rather than a scheduled change.
