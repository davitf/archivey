# Brief — the problem catalogue (Topic 10)

Commissioned 2026-08-15 against `main` @ `d4668c3`. Not a review of the code: an
**extraction and normalization pass over what the project already knows**, producing one
entry per non-trivial problem archivey has had to solve.

Labels used here (`O-n` vs `On`, Topic numbers, phases) are defined in
[`../docs-content/brief.md` §Where the labels are defined](../docs-content/brief.md).

## The ask

> Write down every non-trivial problem this project has had to consider — format quirks,
> upstream library defects, security hazards, platform traps, usage patterns — stated so
> that **someone who has never seen archivey could design against them.**

Not decisions. A decision is an answer; this catalogue holds the questions the world
asked. Each entry *links* to the decision that resolved it (§Entry schema field 4), but
the entry itself must read as a constraint, not as a justification.

## Why now

1. **The material exists and is scattered.** Roughly 180 documents already contain a
   problem statement (§Sources). Nothing collects them, so the same problem is restated
   in a review finding, an ADR context section and a change proposal, in three
   vocabularies, and no one can count them.
2. **Topic 8 is about to read every subsystem, once.** Its capability workers harvest the
   residue that never reached a register as a byproduct
   ([`../docs-content/brief.md` §How to run this](../docs-content/brief.md)). That residue
   is not keyword-findable — a grep for `workaround|quirk` over `src/` finds four sites,
   while 136 comments name an upstream library — so it is reachable only by someone
   already reading the code. Free now, a full re-read later.
3. **Neutrality gets harder with time, not easier.** Every month the solution feels more
   like the problem. §The neutrality rule is the whole value of the artifact and it is
   cheapest to enforce while the sources still say what the world did, rather than what
   we built.
4. **There is already demand from the docs side.**
   [`../docs/independent/rationale-gaps.md`](../docs/independent/rationale-gaps.md)
   records **32** "why is it like this?" questions across 7 sections that the code does
   not answer. Those are catalogue entries with the answer missing.

## The two consumers, and the tension between them

| Consumer | Wants |
|---|---|
| **Documentation** (Topic 8, and every future page) | The user-visible symptom, in plain terms, with what the reader should do about it |
| **The fresh-design comparison** (§The experiment) | The forces alone — deduplicated, in no particular architecture's vocabulary, with our answer withheld |

They pull opposite ways on one axis: docs want our answer attached, the experiment
requires it detached. The schema resolves this by making the answer **a separate,
strippable field** rather than by writing two documents that would drift apart — which is
the failure the docs IA review spent a whole phase undoing.

## The neutrality rule

**A problem stated in terms of its solution is not a problem.** "How do we avoid copying
bytes twice in the decoder stack" presupposes a decoder stack. Hand that to a fresh
designer and you have asked a leading question; agreement then proves nothing — the same
weak-evidence trap that [`../docs/independent-brief.md`](../docs/independent-brief.md) and
Topic 9's brief both name for passes with shared priors.

**The test:** if the problem cannot be stated without naming an archivey type, module or
config field, it is a solution and needs rewriting. State what the *world* does:

| Instead of | Write |
|---|---|
| "`VerifyingStream` must fuse with the hasher to avoid a second pass" | "Verifying a member's declared checksum and delivering its bytes are the same read; doing them separately doubles I/O on large members" |
| "`_GzipTruncationCheckStream` backstops rapidgzip" | "A gzip decompressor optimized for random access can return a short read at EOF without signalling truncation; the caller cannot distinguish that from a legitimately short member" |
| "`MemberStreams.CONCURRENT` gates overlapping opens" | "Most archive formats are a single sequential byte source, so two readers of different members contend for one file position" |

The right-hand column is what a designer who has never seen this library needs. The
left-hand column is what we happen to have built.

## Sources (measured at `d4668c3`)

Denominators, so coverage is checkable rather than asserted.

| Source | Count | Carries |
|---|---:|---|
| `openspec/changes/archive/*/proposal.md` with a `## Why` | 72 | The problem that motivated each landed change |
| …of those, with a `design.md` | 57 | Forces, alternatives considered, and why they lost |
| `dev-docs/decisions/` (ADRs) | 18 | Context sections — the problem half of each decision |
| `review/archive/*/SUMMARY.md` | 11 | Every finding from 11 completed reviews, already severity-ranked |
| `dev-docs/investigations/` | 8 | The expensive ones: rapidgzip truncation, pyppmd native crashes, RAR corpus sweep, parallel reader |
| `dev-docs/threat-model.md` | 9 `O` entries | Security hazards, mitigated and open |
| `dev-docs/known-issues.md` | 6 sections / 709 lines | Upstream defects archivey works around |
| `dev-docs/library-analysis.md` | 362 lines | Per-codec library choice and what forced it |
| `dev-docs/open-issues.md` | 310 lines | Open problems, as of a stale snapshot (O-9) |
| `dev-docs/history/` | 5 files | `ARCHITECTURE`, `ASYNC`, `COMPARISON`, `SPEC` — pre-implementation framing, the closest thing to a problem list written *before* the solution existed |
| `dev-docs/discussions/`, `dev-docs/IDEAS.md` | — | Unsettled questions and parked work |
| **Code residue** (via Topic 8's harvest) | ~136 comment sites name an upstream library | Problems that never reached any register |

**`dev-docs/history/` deserves a first read.** It is the only source written before the
current design existed, so its problem statements are natively neutral — everything else
has to be translated.

## Entry schema

Four fields. Field 4 is separable; fields 1–3 must stand alone without it.

| # | Field | Rule |
|---|---|---|
| 1 | **Problem** | Solution-neutral, per §The neutrality rule. What the format, the library, the platform, the attacker or the user does |
| 2 | **Symptom** | What someone actually observes — a wrong listing, a hang, a silent truncation, a confusing exception. This is the field documentation consumes |
| 3 | **Evidence** | Format spec section, upstream issue URL, a failing or pinning test, a `file:line`. An entry without evidence is a belief, not a problem |
| 4 | **How archivey answers it today** | The mechanism plus the ADR / change / review finding that decided it. **Strippable** — the experiment gets fields 1–3 only |

Plus metadata: a stable id, a category, and **every source that states it** — one entry,
N sources. The catalogue's value is that the four restatements of one problem collapse
into one row; that dedupe is the same argument as Topic 8's claim inventory, and it fails
the same way if fanned out.

**Categories** (start here, extend with evidence): format quirk · upstream library defect
· security / hostile input · platform & filesystem · performance & memory · API and usage
pattern · packaging & dependency · concurrency & lifetime.

## The experiment (design it now — it constrains the schema)

The catalogue's second consumer is a fresh-design comparison. Writing the protocol down
now is what keeps the schema honest; running it is a later, separate exercise.

1. Hand a frontier model **fields 1–3 only**, plus `VISION.md`'s goals, and ask for a
   library architecture.
2. Compare its design against archivey's.
3. Grade **with field 4 in hand**, sorting every divergence into two buckets:
   - **Already considered** → cite the ADR or change. The value here is a re-test: does
     that reasoning still hold under the model's framing?
   - **Not considered** → the real output of the experiment, and a finding for whichever
     review owns that area.
4. **Convergence is weak evidence; divergence is the signal.** A model trained on
   archive-handling libraries shares our priors, so agreeing with us proves little. This
   is the same caveat #208 and Topic 9's two-pass round already recorded — the reason both
   were run isolated rather than in conversation.

Anything the experiment cannot ask because the catalogue phrased a problem in our terms
is a defect in the catalogue, not in the experiment.

## What this is / is not

| This topic | Not this topic |
|---|---|
| Collecting and normalizing problems already recorded | Finding new problems — that is what reviews do |
| Making them legible to someone without our context | Re-litigating how any of them was solved |
| One entry per problem, N sources | A history of the project (`dev-docs/history/` is that) |
| Linking each problem to its decision | Restating the decisions (they are field 4, and strippable) |
| Designing the comparison protocol | Running it |

## Suggested process

1. **Inventory the sources before reading them.** List every document in §Sources as a row
   with a state: *unread* / *mined* / *no problem statement (reason)*. Coverage is
   checkable only if the denominator is written down first — the same discipline as Topic
   9's matrix and Topic 8's claim table.
2. **Start with `dev-docs/history/`**, for the neutral phrasing, then the investigations
   and threat model (already problem-shaped), then ADR context sections and the 72
   proposal `## Why` blocks (most solution-contaminated, so translate hardest).
3. **Extract, then dedupe centrally.** Extraction can fan out by source group; the merge
   cannot — its whole job is recognizing that four documents describe one problem.
4. **Neutrality pass, ideally by a second reader** who has not been in the sources. Take
   each field 1 and ask: could this be understood by someone who has never seen archivey?
   Rewrite until yes. Expect this to be the slowest step and the one that matters.
5. **Fold in Topic 8's harvest** from `harvest/` as it arrives; it is the only source for
   problems that never reached a register.
6. **Emit the redacted view** — fields 1–3 — as a committed artifact, so the experiment
   cannot accidentally be run against the annotated version.

## Hard constraints

- **`review/README.md` conventions apply.**
- **No fixes, no refactors, no spec changes.** If the catalogue makes a design look wrong,
  that is a finding for the review that owns the area — file it, do not act on it.
- **Every entry needs evidence.** No problem is admitted because it seems likely or
  because someone remembers it. Unevidenced recollections go in a separate "unverified"
  list, never in the catalogue.
- **Never state a problem in archivey's vocabulary** (§The neutrality rule). This is the
  one constraint whose violation silently destroys the artifact's second use.
- **Do not editorialize about severity.** "This problem is why X is hard" is field 4 talk.
  The catalogue records that the problem exists and what it does.
- **One entry, N sources.** A duplicate is a merge, not a second row.

## Deliverables

| File | Contents |
|---|---|
| `SUMMARY.md` | Headline, entry count by category, coverage against §Sources, and what the sources turned out **not** to contain |
| `catalogue.md` | The full annotated catalogue — all four fields |
| `catalogue-neutral.md` | Fields 1–3 only. The artifact the experiment is run against, committed separately so redaction is not a manual step at experiment time |
| `sources.md` | The step-1 inventory with every document's state; the coverage proof |
| `harvest/` | Topic 8's per-capability drops, raw, before merging |
| `experiment.md` | The §The experiment protocol as a runnable procedure, with the grading rubric |

## Definition of done

1. **Every document in §Sources is marked** *mined* or *no problem statement, with
   reason*. No silent gaps.
2. **Every entry has all of fields 1–3**, and field 4 or an explicit "unresolved".
3. **Every entry passes the neutrality test**, checked by someone other than its author
   where possible.
4. **`catalogue-neutral.md` is derivable and committed**, and reading it alone conveys the
   problem space.
5. **Topic 8's harvest is merged**, or recorded as still outstanding with the capabilities
   named.
6. **`experiment.md` is written**, whether or not the experiment has been run.

## Relationship to the other topics

- **Topic 8 (`../docs-content/`)** — runs in parallel; disjoint sources (that one reads
  `docs/` and `src/`, this one reads `dev-docs/` and the archives). It supplies the code
  residue and consumes the catalogue for `how-it-works.md` and the rationale gaps.
- **Topic 7** (adoption capstone) — a consumer. "What problems does this library solve
  that a naive one does not?" is its question, and this is the evidence.
- **Topic 6** (decode performance) — a consumer for the performance category.
- **Archived reviews** — sources, not subjects. Their findings are mined; their
  conclusions are not reopened.
