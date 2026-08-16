# Brief — documentation *content* (Topic 8)

Commissioned 2026-08-15 against `main` @ `d4668c3` (post-#239, with Topic 9 archived
and the diagnostics follow-ons `#233`–`#236` landed). The counterpart to the docs IA
review: that one decided **where each page lives**, this one decides **whether the page
is right**.

## Where the labels are defined

This brief inherits five separate numbering schemes from four programmes. None of them
is self-explanatory, and two of them collide, so look a label up rather than inferring
it.

| Label | Means | Defined in |
|---|---|---|
| **Topic 4–9** | Review *topics* — IDs, not an order. This review is Topic 8 | [`../backlog.md`](../backlog.md) — one `## Topic N` section each; Topic 8 at [`:169`](../backlog.md) |
| **phase 1–4** | The docs IA review's process: audit → decide → execute → guardrail. All four are done | [`../docs/brief.md` §Suggested process](../docs/brief.md) (`:233-257`) |
| **Phase 8, Phase 9** | *Capability implementation* phases — a different scheme entirely, about what the library builds and when, not about this review | [`openspec/project.md` §Implementation order](../../openspec/project.md) (`:83`) |
| **D1–D11** | The IA review's maintainer decisions (unpublish `docs/internal/`, the ADR summary page, the Gotchas rule, `AGENTS`/`CLAUDE`, `dev-docs/` …) | [`../docs/DECISIONS.md`](../docs/DECISIONS.md) — one `## DN` section each |
| **D-a–D-e** | Five later page-shape decisions taken *while* the splits were implemented (nav order, the `reading.md` split, the config screen, `extracting.md`'s name, the damage contract's home) | [`../docs/outline.md` §Decided](../docs/outline.md) (`:719-840`) |
| **O-1–O-26** | Content observations recorded by the IA audit and handed here — **hyphenated** | [`../docs/observations.md`](../docs/observations.md) |
| **O1–O9** | Threat-model residuals — the security gap register, **no hyphen**. O6 is nested-archive amplification, O9 is attacker-controlled bytes reaching the terminal | [`dev-docs/threat-model.md`](../../dev-docs/threat-model.md) (`:18-344`) |
| **W1–W9** | Topic 9's pay list, all landed in #232 | [`../archive/2026-08-15-simplicity-consistency/WORKPLAN.md`](../archive/2026-08-15-simplicity-consistency/WORKPLAN.md) |
| **finding N** | Numbered items in the two automated review rounds on #223, with their dispositions | [`../docs/outline.md` §Review disposition](../docs/outline.md) (`:669-718`, two rounds) |

> **The collision worth knowing:** `O-16` is a *documentation* observation and `O6` is a
> *threat-model* residual. The hyphen is the whole distinction, and this brief uses both.
> Likewise "phase 3" is the IA review's execute phase while "Phase 8" is a capability
> implementation phase — same word, unrelated schemes.

## The ask

Judge the published guide against the code and fix it. The question is:

> Does every claim on the 15 published pages still describe what the library does —
> and where the guide is silent, is that silence a decision or an omission?

Deliverable shape per [`review/README.md`](../README.md): `SUMMARY.md` (headline +
ranked findings with severity / where / status), theme files as useful, `QUESTIONS.md`
for maintainer decisions, and a "**what is actually fine**" section so verified pages
are not re-verified by the next pass. This brief carries a light recon (§Known seeds);
the seeds are a floor, not the job.

**This review writes, unlike Topic 9.** The simplicity pass was analysis-first because
library behaviour freezes at the tag. Prose does not: a docs edit is reversible, cheap,
and the specification for most of it already exists in
[`../docs/outline.md`](../docs/outline.md). So this review is expected to land page-sized
PRs, not only findings. The one hard exception is §Hard constraints' first rule — a
**library** defect found while writing is never fixed inside a docs PR.

**No budget, no target date** (maintainer, 2026-08-15: *"we'll do everything and take as
long as needed"*). All four passes run to completion; §Definition of done says what that
means concretely. The ranking below is a **sequence**, not a triage — it says what to do
first because each pass makes the next one cheaper, never what to drop.

## Why now

1. **The work stopped on purpose, and the reason has cleared.** Writing the guide kept
   turning up library defects, so the prose paused while they were paid: `#225` (four
   fixes found by the docs pass), `#232` (Topic 9's W1–W9, six OpenSpec changes +
   ADRs 0015–0017), and `#233`–`#236` (the diagnostics ceiling,
   `ExtractionReport.results` becoming authoritative, terminal escaping). All landed;
   Topic 9 archived 2026-08-15; nothing is in flight that moves the described surface
   again. The pages were written *before* most of that.
2. **That makes the accuracy pass a diff, not a re-read.** The highest-value pass has a
   bounded starting point — `fe6d4a7..d4668c3` — instead of "reread everything and
   hope". The pages were kept in sync opportunistically as those PRs landed (+226/−27
   lines across 9 files), but only on the surfaces each PR happened to touch.
3. **Ordering against the release — not a deadline.** `STATUS.md` ranks this review
   before more releases ship more permanent URLs: renaming or merging a page is free
   today and a redirect forever after. That is an argument about *sequence*, and it is
   not a reason to hurry or to cut a pass short.
4. **Topic 7 is the capstone and should not re-find this.** The adoption review judges
   whether the docs persuade. Every inaccuracy it would otherwise rediscover is work
   done twice.

## What this is / is not

| This review | Not this review |
|---|---|
| Accuracy of published prose vs shipped code and specs | Where pages live / nav shape (settled: [`../docs/DECISIONS.md`](../docs/DECISIONS.md) D1–D11, `outline.md` D-a–D-e) |
| Writing the prose `outline.md` specifies and no merge could supply | Library behaviour changes ([Topic 9](../archive/2026-08-15-simplicity-consistency/brief.md) closed that class; new defects → separate PR) |
| Register and concision on migrated maintainer prose ([O-16 / O-17](../docs/observations.md)) | Decode-engine performance ([Topic 6](../backlog.md)) |
| Reference completeness — what a user cannot look up | Whether the docs *persuade* ([Topic 7](../backlog.md), the capstone) |
| Triaging the maintainer registers the IA move left stale ([O-9, O-15](../docs/observations.md)) | Re-litigating settled ADRs or archived review KEEPs |
| A guardrail that makes accuracy a CI failure rather than a review | Feature work (salvage mode, `archive-writing`, [Phase 8](../../openspec/project.md) blocked gzip) |

## Values (tie-breakers)

`VISION.md` is the product authority. Rank findings against it; where prose and values
conflict, prefer the reading below.

### From VISION (load-bearing)

1. **Safe by default** and **memory-safe parsing of hostile input** — a page that
   overstates a safety guarantee is the worst defect this review can find, and O-16 is
   the proof it has already happened once.
2. **One uniform interface with honest cost signals** — where a page needs a
   format-conditional to stay true, the conditional is the honesty; hiding it is not
   simplification.
3. **Damaged input is a first-class citizen** — the damage contract is
   `errors-and-diagnostics.md`'s (D-e), and the flow pages keep the one-line promise
   plus a link.
4. **Content-first** — reading and listing before extraction.

### Documentation addenda (not in VISION, but decide most calls here)

- **An inaccurate doc is a bug; an unpolished one is not.** That is what orders the four
  passes; it is not permission to stop after the first. `backlog.md`'s "if time forces a
  choice, do (1) alone" was written when this topic might have been squeezed against the
  release — it is not, so the fallback does not apply.
- **Three outcomes, only one is a docs fix** ([O-26](../docs/observations.md)). When prose and behaviour disagree,
  the code may be wrong, the spec may be wrong, or the prose may be wrong. Check the
  spec before rewriting the sentence. Softening the guide to match buggy code is the
  wrong half of the time — `#225` exists because four of these went the other way.
- **The reader is a working developer, not an archive-format specialist.** Prose
  promoted from a threat model, an ADR or a capability spec is accurate and carries the
  wrong register. Plainer is not vaguer: "we can't tell which bytes are good" is both
  plainer and more precise than "the prefix is best-effort salvageable"
  ([O-17](../docs/observations.md)).
- **Silence is a claim too.** A behaviour a competent user will hit and the signature
  does not reveal is a documentation defect whether or not any sentence is wrong —
  that is what [`../docs/independent/must-explain.md`](../docs/independent/must-explain.md)
  measures.
- **Write the record after the work, never before.** Three of the four findings in
  [#223's round-2 re-review](../docs/outline.md) were the same mistake: a decision recorded in the present
  tense ahead of the prose it depended on. A worklist and a record are different
  documents.
- **Do not document an accident you could delete.** [Topic 9](../archive/2026-08-15-simplicity-consistency/brief.md)'s rule still applies; the
  vehicle is now a separate fix PR rather than this review.

## The surface being reviewed (measured at `d4668c3`)

Denominators, not targets.

| Axis | Count | Where |
|---|---:|---|
| Published pages | 15 | `docs/*.md`, 2 108 lines; `mkdocs.yml` nav has all 15 |
| Pages `outline.md` specifies | 16 | the missing one is `how-it-works.md` (D2) |
| Code blocks in the guide | 39 | 35 of them `python` — the doctest guardrail's domain |
| Public names that freeze at the tag | 87 | `archivey.__all__` |
| Names with an API-reference entry | 56 | `::: archivey.…` in `docs/api.md` — **31 have none** (§D) |
| Behaviours not inferable from signatures | 29 | [`../docs/independent/must-explain.md`](../docs/independent/must-explain.md) |
| "Why" questions the code does not answer | 32 | [`../docs/independent/rationale-gaps.md`](../docs/independent/rationale-gaps.md), 7 sections |
| Content observations recorded, not acted on | 26 | [`../docs/observations.md`](../docs/observations.md) O-1…O-26 (many now closed — §Provenance) |
| Maintainer registers left stale by the IA move | 2 | `dev-docs/known-issues.md` (709 lines, untriaged), `dev-docs/open-issues.md` (310) |

Baseline is green at commission: `uv run --group docs python scripts/check_docs_nav.py`
reports *15 pages, all in nav; repo, site and anchor links all resolve*.

## The four passes, ranked

`backlog.md:168` ranks these and the ranking is the plan. All four run (§Definition of
done). Do not bundle them: each one makes the next cheaper, and running them together is
how a rewrite ends up arguing about a paragraph's structure before knowing whether the
paragraph is true.

1. **Accuracy vs the code.** Highest value; it is the pass this review exists for.
2. **Gaps.** What does an adopting engineer look for and not find? Coordinate with
   Topic 7 (it asks whether the docs persuade; this asks whether they answer).
3. **Register and concision.** Promoted, not polish — the material most needing it is
   the material carrying the safety claims (O-16 / O-17).
4. **Remaining quality: examples, within-page structure.** Genuinely bikeshed-prone, and
   in scope for **every** page. It goes last because on a page whose claims are still
   unverified a structure argument is unfalsifiable — you are rearranging something you
   do not yet know to be true. Start with the load-bearing pages (`index`, `extracting`,
   `formats`, `opening-and-listing`, `reading-members`), where a wrong call costs most,
   then finish the rest.

## Known seeds (light recon — deepen, do not re-derive from zero)

> **The seeds are a floor.** They say where someone already suspected an answer.
> §Suggested process step 2 walks every page regardless, including pages no seed points
> at. A seed that turns out to be a non-issue is a finding — record it under "what is
> actually fine" so the next pass does not re-derive it.

### A. Accuracy — the `fe6d4a7..d4668c3` diff (highest value)

| Seed | Signal | Confidence |
|---|---|---|
| **Extraction results became authoritative** (`#235`) | Per-member outcomes left the diagnostics channel entirely: 22 codes → 18, four removed, `ExtractionStatus.OVERWRITTEN` / `presented_name` / `collided_with` / `failure_group_*` added, `abort_on` introduced. `extracting.md` and `errors-and-diagnostics.md` were updated in that PR — verify the *rest* of the guide (Gotchas digest, `index.md` recipes, `cli.md`, `migrating.md`) does not still describe the removed codes. | Partially synced; sweep the remainder |
| **Diagnostics gained a written ceiling** (`#234`/`#235`, O-23 retired) | Admission ("only what the caller could not determine and can act on") and placement ("a structured per-item report is the sole carrier of per-item outcomes"). Any guide sentence implying diagnostics report caller usage is now contradicted by spec. | Rule is settled; prose unchecked |
| **Topic 9's W1–W9** (`#232`, six changes + ADRs 0015–0017) | Six behavioural contracts changed at once — `format_availability().required_source`, metadata decoupled from declared seekability, bidi override rejection, strict archive EOF + trailing bytes, rewind redecode diagnostic. Each has a page that should mention it. | Method proven; unchecked page by page |
| **`#225`'s four fixes** | Solid-archive password laziness, `format=` rejected on a directory, seekable (non-`Path`) size/CRC probes, close-on-reader-close. `formats.md` / `access-and-cost.md` describe all four areas. | Re-verify |
| **Terminal escaping and [threat-model O9](../../dev-docs/threat-model.md)** (`#236`) | CLI print sites escape archive-derived text; library log records still do not, registered as O9. `cli.md` (48 lines) is the thinnest page against the largest recent change to CLI output. | Unchecked |
| **Every `python` block should run** | 35 blocks, none executed by CI. This is mechanically checkable and doubles as the pass-1 method: a block that no longer imports is an accuracy finding with a zero-judgement repro. | Ripe; see §Deliverables guardrail |

**Apply the [O-21](../docs/observations.md) method to each claim, not each page:** find the line that implements the
behavioural claim, then check the branch where it does not hold. That method produced
`#225`; reading pages for plausibility does not.

### B. Missing prose — `outline.md`'s worklist, re-tallied first

`outline.md` §"What merging cannot supply" is the specification. **Its estimate of
~455 outstanding lines is stale** — +226/−27 lines of guide prose landed in `#225`/
`#232`/`#235` after it was written, some of it against these rows. Re-tally before planning
against it; that stale-worklist failure is finding 5 of #223's own round-2 review.

Verified still open at `d4668c3`:

| Row | State |
|---|---|
| `how-it-works.md` (~150, all six D2 sections) | **Does not exist.** The only page in the outline with no file, and the reason nav is 15 where the outline says 16 |
| `install.md` (~45) | 34 lines, two sections; no `format_availability()` section, matrix not re-cut by extra |
| `access-and-cost.md` (~55) | Has the AUTO threshold; **no measurement section** (`enable_measurement` / `IoStats` are public and unexplained), and §Checklist is a situation→API table, not the config-at-a-glance screen finding 4 asked for |
| `extracting.md` (~90) | "What `TRUSTED` does not relax" **is** covered; the bounded-recursion **worked recipe** ([threat-model O6](../../dev-docs/threat-model.md), not observation O-6) is still a one-paragraph pointer under Limits |
| `errors-and-diagnostics.md` (~55) | Grew +61 in `#235`; **no error-translation narrative** — `CONTRIBUTING.md`'s boundary contract (raw library/`OSError` translated, unrecognized propagate raw, `ArchiveyUsageError` deliberately outside the tree) is a user-facing promise the guide never states |
| `opening-and-listing.md` / `reading-members.md` (~25 / ~35) | Largely written in `#224`; confirm the remainders (sources, `stream_members` lifetime, the `extract()` pipe note) rather than assuming |

**Fix vehicle for `how-it-works.md`:** it needs a `documentation` spec delta, not just a
file. `openspec/specs/documentation/spec.md:78-93` enumerates the narrative pages and
requires every file under `docs/` to carry a nav entry; adding a sixteenth page changes
that requirement.

### C. Register, and the two registers left stale

| Seed | Signal | Confidence |
|---|---|---|
| **O-17 rules + O-16 accuracy half** | The worked example and the rules are already written in `observations.md`. Expect most rewritten sections to lose 20–30% without losing substance. O-16 is the case where the *integrity guarantee* was overstated — that one is a safety claim, not a style note | Rules written; application unstarted |
| **O-15 — `known-issues.md` triage (D9)** | 709 lines, no triage into resolved / mitigated / upstream / fixable / evidence. Recorded as a **required follow-up, not optional** | `CONFIRMED` open |
| **O-9 — `open-issues.md` is a dated snapshot** | 310 lines that have aged; several entries were closed by `#232`–`#236` | `CONFIRMED` open |
| **O-12 — runtime error messages embed documentation paths** | Two messages cite maintainer documents. Whether an error message should cite a doc at all is this review's call | Open question |

### D. Reference completeness — a measured gap needing a decision

`docs/api.md` enumerates 56 of the 87 names in `archivey.__all__`. **31 have no entry**,
and 21 of those are the exception tree — `CorruptionError`, `TruncatedError`,
`OpenError`, `PathTraversalError`, `SymlinkEscapeError` and the rest. They are described
narratively in `errors-and-diagnostics.md`'s table, and the page's opening sentence
("Everything documented here is re-exported from the top-level `archivey` package and
listed in `archivey.__all__`") is true but reads as a completeness claim it does not make.

Also absent: `ARCHIVE_INTEGRITY_CODES`, `DEFAULT_ARCHIVEY_CONFIG`, `DetectionConfidence`,
`DiagnosticContext`, `ExtractionProgress`, `FormatAvailability`, `FormatInfo`,
`FormatSupport`, `MissingComponent`, `__version__`.

**This is a question, not a defect** — 87 mkdocstrings entries may be the wrong page.
Decide it deliberately (enumerate all, enumerate a documented subset with the sentence
corrected, or generate the list), then make it a guardrail either way. Nothing is broken
today — `check_docs_nav.py` passes, and all four `[X][archivey.X]` cross-references in
the guide point at names api.md carries; the undocumented types are spelled as plain code
spans instead. That is also why the gap stays invisible until someone counts.

### E. Explicitly out of scope (do not expand into)

- Nav order, page boundaries, filenames — settled in D1–D11 and `outline.md` D-a–D-e.
  Reopening one needs new evidence, not a fresh opinion.
- Library behaviour changes. Topic 9 is archived; a defect found here is a finding plus a
  separate fix PR (the `#225` pattern), never an edit inside a docs PR.
- Specs for unlanded phases — `archive-writing` (Phase 9) and the Phase 8
  `seekable-gzip-and-block-writing` change (live, 0/24, specs-only by design). They are
  deliberately ahead of the code and are not documentation gaps.
- Persuasion, positioning, competitive framing — Topic 7 owns those, and the docs reviews
  deliberately hand adoption findings to it.
- `dev-docs/` prose quality beyond the O-15 / O-9 triage. The site is the deliverable.

## Suggested process

1. **Baseline.** `scripts/check.sh` plus `uv run --group docs python scripts/check_docs_nav.py`,
   recorded. **Check the environment before trusting green:** missing `unrar` / `7z`
   makes ~109 tests skip quietly (`CLAUDE.md`); run `scripts/setup-dev-env.sh` and read
   its closing verification block. Record `format_availability()` output — a page claiming
   a format works is unverifiable if that format is unavailable in the session.
2. **Claim inventory, then the diff.** Walk all 15 pages and extract every *checkable
   claim* (a behavioural assertion, a code block, a table row, a default value) into one
   table with `page:line` and the `src/` or `openspec/specs/` line that would settle it.
   Then work §A's diff window first — those are the claims most likely already wrong.
   Every claim ends as **verified** / **wrong** / **unverifiable**; a bare gap is an
   unfinished row, and "unverifiable" is a legitimate outcome that names why.
3. **Checkpoint — hand over the claim table before writing anything.** The pass is wide
   enough that a mid-point re-aim is cheap and a wrong aim discovered at delivery is not.
   Report which pages the errors cluster on. Continue on the maintainer's steer, or after
   a short wait if none comes.
4. **Classify each disagreement before fixing it** (O-26): code wrong → finding + separate
   fix PR; spec wrong → OpenSpec change; prose wrong → fix here. Never pick a winner
   silently on a spec/design discrepancy — pause and ask (`CONTRIBUTING.md`).
5. **Then write the missing prose**, against the re-tallied §B worklist, `how-it-works.md`
   last of the large rows — it is the page whose absence keeps the docs review open.
6. **Then the register pass** (O-17 rules) across every page carrying promoted maintainer
   prose, then pass 4 across every page, load-bearing ones first.
7. **Close the registers** — the O-15 `known-issues.md` triage and the O-9
   `open-issues.md` refresh. They are the review's last unwritten deliverable and the
   easiest to forget, because neither is a published page.
8. **Ship page-sized PRs throughout.** One page, or one closely-coupled pair, per PR. A
   move-plus-rewrite diff is unreviewable — that argument is why this topic exists at
   all, and it applies just as much to a rewrite-plus-rewrite diff.

## Hard constraints

- **`review/README.md` conventions and the VISION tie-breakers apply.**
- **No library changes in a docs PR.** However small, however obviously right. It becomes
  a finding with a runnable repro and its own red–green PR; `#225` is the shape.
- **Do not weaken a safety or honesty claim to make a page read better.** If a guarantee
  is narrower than the prose says, narrow the prose *and* say what the reader should do
  instead — O-16's fix, not a deletion.
- **Verify against code or spec, never against another page.** Four copies of the
  rapidgzip caveat drifted into two wrong ones precisely because each was checked against
  its neighbour (O-2, now fixed).
- **Name the dependency config** for any claim whose truth depends on optional libraries —
  `[all]`, `[all-lowest]`, `[core-only]` (`CONTRIBUTING.md` → "Before pushing").
- **Record after the work, not before.** No present-tense entry in `SUMMARY.md`,
  `outline.md` or a PR body for prose that is not written yet. This trap has caught the
  docs work three times.
- **Every claim removed from a page must land somewhere or be recorded as dropped.** The
  IA move lost facts twice (the ZIP UTF-8 bit-11 row, the `dev-docs/internal/` references)
  because "it is covered elsewhere" was asserted rather than checked.

## Definition of done

There is no budget and no date, so "we stopped here" is not a completion state — it is a
pause, and the record has to say which. The review is done when **all** of these hold:

| # | Done means |
|---|---|
| 1 | **Every checkable claim on every page is marked** verified / wrong / unverifiable in `claims.md`. No bare gaps. *Unverifiable* names its reason — format unavailable in the session, behaviour genuinely undecided, a spec question raised in `QUESTIONS.md` — and never means "not checked" |
| 2 | **Every §B worklist row is written**, or dropped with a recorded reason that *supersedes* it — a decision, a duplicate, a page that turned out not to need it. "Ran out of time" is not one of those reasons |
| 3 | **The guide is complete against `outline.md`**: 16 pages, `how-it-works.md` among them with its `documentation` spec delta, and the nav matching |
| 4 | **Every page carrying promoted maintainer prose has had the register pass**, with the O-16 safety-claim class fixed first |
| 5 | **Pass 4 is recorded per page** as done or deliberately skipped, with the reason. Silence about a page is not an outcome |
| 6 | **The registers are triaged** — O-15 (`known-issues.md`) and O-9 (`open-issues.md`), both required follow-ups rather than optional ones |
| 7 | **Every open observation** in `../docs/observations.md` is closed, transferred with a pointer, or parked with a recorded justification |
| 8 | **The guardrail is in CI** — the 35 `python` blocks execute, and the §D API-reference decision has become a test rather than a preference |
| 9 | **`SUMMARY.md` lists every page verified clean** under "what is actually fine", so the next review skips them instead of re-deriving them |

Then `docs-content/` and `../docs/` archive together (§Provenance sequencing note).

## Deliverables

| File | Contents |
|---|---|
| `SUMMARY.md` | Headline, ranked findings (severity / page / status / disposition), and "what is actually fine" — including seeds that were non-issues and pages verified clean, so the next pass skips them |
| `claims.md` | The step-2 claim table: every checkable claim, its `page:line`, the `src/`–`spec` line that settles it, and verified / wrong / unverifiable. This is the review's real artifact — the prose fixes are downstream of it |
| `QUESTIONS.md` | Maintainer decisions with fix vehicle: the §D API-reference shape, O-12 (should an error message cite a document), anything where the code/spec/prose winner is not obvious |
| Page PRs | One page or coupled pair each, against the re-tallied `outline.md` worklist |
| Fix findings | For each library defect: `file:line`, the input that triggers it, the config it reproduces in, and a proposed vehicle. Not fixed here |
| **Guardrail** | Executable examples — doctest or a tested snippets file over the 35 `python` blocks, plus whatever the §D decision implies (e.g. an assertion that every `__all__` name has a reference entry). This is the difference between doing this pass once and doing it every release, and the docs review's own conclusion was that the guardrail phase decides whether a review has to be run twice |

## Provenance / do not resurface

Settled or already fixed — cite and move on:

- **O-2 is fixed.** `formats.md:148` now reads "seekable `.gz`", matching
  `seekable-decompressor-streams/spec.md`. The four-copy drift is closed.
- **O-23 is retired** (`#235`) — the diagnostics admission and placement clauses replaced
  it; the outcome it decided is unchanged.
- **O-19** — anchor checking now ships in `check_docs_nav.py`; broken anchors fail CI.
- **O-1** — the `AGENTS`/`CLAUDE` merge landed (D6), and `#239` corrected the residue.
- **D1–D11** (`DECISIONS.md`), **D-a–D-e** (`outline.md`), and #223's two review rounds.
  The nav order, the `reading.md` split, `extracting.md`'s name, and the damage
  contract's home are all decided *with recorded reasoning*.
- **ADRs 0003 / 0010 / 0012 / 0013 / 0014 / 0015–0017**, and every archived review's
  KEEP list.

If this review rediscovers one of these, the finding is "regression since settle" or
"the prose never caught up" — not a new design question.

**Sequencing note for the maintainer.** [`../docs/`](../docs/) stays in flight until this
topic lands: its remaining deliverable is `how-it-works.md`, which D2 assigns to Topic 8,
and its `outline.md` / `observations.md` are this review's inputs. Archiving both together
avoids a link churn that would rewrite every citation in this brief.
