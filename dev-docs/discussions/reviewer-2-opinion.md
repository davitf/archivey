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

---

## After reading the tree

Added after reviewer 3's note landed, to settle the one claim this opinion
flagged as unverified. Checked against `src/archivey/internal/extraction.py`,
`extraction_types.py`, and `openspec/specs/safe-extraction/spec.md`; the
`REPLACE` collision case was run, not just read.

### Refuted — my own suggestion was wrong

**`SUPERSEDED` is not a home for a `REPLACE` collision.** Part 4 above
suggested it might be, marked *check this*. It is not, and reviewer 1's tree
pass was right to say so. `SUPERSEDED` means a *non-current duplicate* —
several archive entries under one name, only the last live — decided from
`is_current` at listing time before any write is attempted
(`extraction.py:365-370`), yielding `path=None, error=None`
(`safe-extraction/spec.md:597-599`). A destination collision is a different
event at a different stage: two *current* members whose resolved paths land on
one key during the write walk. Same English word, unrelated conditions.

Reviewer 3's parallel suggestion — that a portability rewrite show up as
`requested_path != path`, "exactly as `RENAME` already is" — hits the same
problem. `requested` is computed from the already-sanitized name
(`extraction.py:565`, fed by `_transform` at 543-546), so the two are equal
for a sanitized member today; and that signal is already spoken for, since
`requested_path != path and status == EXTRACTED` is the defined marker for
`OverwritePolicy.RENAME` (`safe-extraction/spec.md:603-606`). Reusing it would
make one signal mean two things.

So both of reviewer 3's relocations need a genuinely *new* signal plus a spec
amendment. The structural argument survives; the "it already has a home"
costing does not.

### Confirmed

**The `REPLACE` overwrite is silent in `results`, by design.** A ZIP holding
`A.txt` and `a.txt` under `OverwritePolicy.REPLACE`:

```
member='A.txt'  status=extracted  path='A.txt'  requested='A.txt'
member='a.txt'  status=extracted  path='A.txt'  requested='A.txt'
on disk → 'A.txt': 'second member content'
```

The first member's content is gone and its result still reads plainly
`EXTRACTED`. `extraction.py:591-597` sets the merged member's `requested_path`
to the merged path deliberately, so a replace-merge does not masquerade as a
rename.

**Reviewer 1 is also right that the dual channel is normative**, not drift:
`safe-extraction/spec.md:608-609` requires exactly one matching advisory per
continued `BLOCKED`/`FAILED` result, and `:602` states the non-failure statuses
emit none.

### Corrections to all three notes, including this one

**The collision is not *entirely* invisible in `results`.** Two `EXTRACTED`
entries sharing one `path` is a detectable signature if the caller joins by
path. What is unavailable is the *labelled* fact — that this was a replace,
and which member lost. "The diagnostic is the entire audit trail" overstates
it slightly; "the diagnostic is the only labelled record" is exact.

**A caller `filter` rename produces three names, not two.** Archive name,
filter output, portable rewrite. The advisory records the middle-to-final
pair; the result carries only the archive name and the final path. For
filtered members the pair is not reconstructible from the result at all, which
raises the cost of relocating this one.

### Unflagged by any of the three notes

**Relocating a fact out of diagnostics removes its escalation.** Dispositions
apply to diagnostics only, so moving the collision and sanitize records into
`ExtractionResult` silently deletes the ability to say "raise if a member is
silently overwritten." That is the same trade accepted for
`EXTRACTION_MEMBER_BLOCKED` — but there a named knob replaces it, and for
collision nobody has proposed a replacement. Whatever is decided, this should
be decided rather than discovered.

### Mechanics, for costing

Retroactively marking the clobbered member is cheap: `collision_map` stores
only a path (`extraction.py:337, 715`), so it would also need the prior
writer's result index, and the coordinator already revises `results` by index
(`_resolve_orphan`). A few lines. The expensive part is the new public signal
and the spec amendment, not the plumbing.

### Effect on this opinion

The verdict above is unchanged on admission, but the placement argument moves.
Reviewer 3's job-vs-stream clause is a better formulation than the
one-authoritative-channel wording in Part 2, because it separates *what
qualifies* from *where it lives* — and this note let escalation-worthiness
override placement, which is what kept `EXTRACTION_MEMBER_BLOCKED` alive here.
With a named abort knob in place, that objection falls, and this note concedes
the blocked-member deletion. Reviewer 3's blanket-`RAISE` argument also
defeats the "consequence 1 is overstated" claim in Part 4: per-code overrides
are the rare path, so the coarse switch is where the conflation actually
bites.
