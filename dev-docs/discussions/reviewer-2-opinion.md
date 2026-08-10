# Reviewer 2 opinion — diagnostics archive-vs-usage

Response to [`diagnostics-archive-vs-usage.md`](diagnostics-archive-vs-usage.md).
Based on the discussion doc only; the tree was not re-audited for this note, so
anything below marked *check this* is a claim I could not verify.

**Disclosure:** `reviewer-1-opinion.md` was visible to me before I wrote this
(it came back in a file listing, not by choice). I have not tried to agree or
disagree with it, but this is not a blind second opinion.

**Verdict:** the archive-vs-usage cut is descriptive, not normative — retire it.
Adopt **A** with sharper wording, and a narrower **E** than the doc proposes:
delete **one** code, not two. No `subject` axis, no new knob.

---

## 1. The cut is real as a description and useless as a rule

O-23 is not wrong about the taxonomy — eight codes really are shaped differently
from the other fourteen. It is wrong as an *admission rule*, because subject
matter was never what decided anything. The solid-out-of-order proposal was
refused because the cost receipt already said it. That is a redundancy test, and
it is the only test that has ever been applied.

So the fix is not to argue about where the archive/usage line falls. It is to
stop treating a category label as a decision procedure — and option **B** is the
same mistake a second time, at larger scale: it deletes eight codes as a group
when the eight do not behave alike under any test the project actually uses.

Retire the O-23 sentence explicitly rather than leaving it in
`review/docs/observations.md` for the next reviewer to cite as settled.

## 2. Two objections to the drafted ceiling

The Part 5A wording is close, but:

- *"could not have determined from the declared contract"* and *"SHALL NOT
  restate advice the API surface already carries"* are the same test stated
  twice, once vaguely and once well. Drop the first. Strictly, a caller could
  "determine" almost anything by re-implementing the check; what matters is
  whether the fact is **already retrievable from this call's own results**.
- *"and can act on"* does almost no filtering. Keep it, but do not expect it to
  reject anything.

More importantly the rule is missing a second limb, and the missing limb is what
makes the extraction question answerable:

> A `DiagnosticCode` SHALL exist only if the fact is **(a)** not otherwise
> retrievable from the declared result of the same call, **or (b)** something a
> caller would plausibly want to escalate to an exception via `RAISE`.
> A fact that is neither SHALL NOT have a code. Every fact SHALL have exactly
> one authoritative channel; a code that duplicates a return value is justified
> by (b) alone, and the return value remains authoritative for the outcome.

That last sentence is the answer to question 3, and the doc's options A–E do not
contain it. It reframes the extraction "double channel" as a division of labour
— `results` *reports*, diagnostics *escalate and count* — instead of a defect to
be resolved by deletion.

## 3. Applying it: one code goes

| Code | (a) not retrievable | (b) escalation-worthy | |
| --- | --- | --- | --- |
| `EXTRACTION_MEMBER_FAILED` | no — `results[].status` / `.error` | no — `OnError.STOP` already *is* "raise on first failure" | **delete** |
| `EXTRACTION_MEMBER_BLOCKED` | no | **yes** — abort-on-first-unsafe has no other mechanism | keep |
| `EXTRACTION_NAME_COLLISION` | yes under `REPLACE` (*check this*) | — | keep |
| `EXTRACTION_NAME_SANITIZED` | yes | — | keep |
| the other four | yes | — | keep |

`_FAILED` is the only one of the 22 that fails both limbs, and it fails (b)
because a *dedicated, named* knob for that exact want already ships. It should
go, and its one unique payload — `failure_group_id` for the hardlink fan-out —
should move onto `ExtractionResult`. That is a field addition to a result type,
not a taxonomy change, and it leaves `results` genuinely complete, which is what
makes it authoritative rather than merely primary.

That is a one-code diff. The doc's framing invites a much larger one.

## 4. Where I disagree with the doc's framing

**Consequence 1 (`RAISE` means two things) is overstated.** Per-code overrides
already exist; `COLLECT` is the default; `default=RAISE` is an explicit request
for maximal strictness. Being stopped by an unused `password=` is arguably what
that caller asked for. This is an ergonomics wart, not a design fault, and it is
too thin to justify a public field.

**Question 4 may not need answering.** `SUPERSEDED` already exists as an
`ExtractionResult` status. If a `REPLACE` collision does not emit a `SUPERSEDED`
result for the member that lost, that looks less like a gap in the diagnostics
taxonomy and more like a bug in extraction reporting. *Check this before
treating collision as homeless* — if it should emit one, the hardest case in
Part 4 dissolves, and `EXTRACTION_NAME_COLLISION` becomes a (b)-only keep.

## 5. Answers

1. **Right cut?** Neither. Not-otherwise-retrievable **or** escalation-worthy,
   plus one-authoritative-channel-per-fact. Archive-vs-usage stays as prose
   describing the taxonomy, never as a gate.
2. **`RAISE` axis?** No. If callers later want a coarse handle, ship a frozen
   module-level tuple of codes they can splat into `overrides` — same benefit,
   no field on a frozen public dataclass, and unlike an axis it does not
   silently absorb future codes into an existing caller's `RAISE` set. Revisit
   **C** post-1.0 with a real request; skip **D** entirely.
3. **Authoritative for extraction?** `report.results`, always, for outcomes.
   Diagnostics keep facts with no result-field home, and keep escalation.
   Write that down.
4. **Collision / sanitize home?** Keep as diagnostics — but check the
   `SUPERSEDED` question above first, because it may move collision.
5. **Seek tripwire?** Keep. Measured re-decode cost is not knowable in advance
   and `RAISE`-ing on it is the whole point: passes both limbs.
6. **Named abort-on-blocked knob?** No — this is where I would push back
   hardest. Adding one gives the same behaviour a *third* expression (the knob,
   the `RAISE` override, `results` inspection), which is the exact channel
   multiplication Part 3 complains about. `RAISE` composing into a useful
   behaviour is the system working, not an accident; the only real defect is
   that nobody wrote it down. Document the override as the supported mechanism,
   pin it with a contract test so it stops being emergent, and fix the `OnError`
   docstring at `extraction_types.py:83` that calls it unimplemented. If a named
   knob is ever added, implement it *as* that override.

---

## Before `0.2.0`

Tag-gated, so it has to land: delete `EXTRACTION_MEMBER_FAILED`, move
`failure_group_id` to `ExtractionResult`. That is the whole breaking change.

Not tag-gated but cheap, do it anyway: write the two-limb ceiling and the
one-authoritative-channel rule into `openspec/specs/diagnostics/spec.md`; retire
the O-23 sentence; document + test `RAISE`-on-blocked and fix the `OnError`
docstring; check the `SUPERSEDED`-on-`REPLACE` question.

Explicitly deferred: **B**, **C**, **D**. None of them are cheaper than the
problem they solve.
