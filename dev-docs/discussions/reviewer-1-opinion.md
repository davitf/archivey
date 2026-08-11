# Reviewer 1 opinion — diagnostics archive-vs-usage

*Written by Grok 4.5 via Cursor. Had repository access, and used it: the
[tree pass](#after-reading-the-tree) below is this reviewer's own verification.*

Response to [`diagnostics-archive-vs-usage.md`](diagnostics-archive-vs-usage.md).

**Verdict:** reject the archive-vs-usage cut. Do **A** as the ceiling rule, plus
a narrow **E** on extraction — not **B**, and not **C/D** unless callers later
need axis-level policy.

Originally written from the discussion doc alone. A follow-up pass against the
tree is in [After reading the tree](#after-reading-the-tree); it **confirms** the
verdict and slightly tightens the extraction sequencing.

---

## Why

The eight “usage” codes are not one class. Collapsing them into “delete the
usage ones” repeats the mistake of O-23: wrapping a good local refusal in a
rule that never described the taxonomy.

The test that actually rejected solid-out-of-order-open — *already knowable
from the declared contract?* — fits all 22 codes and still fails the rejected
proposal. That is the real admission rule; write it down (**A**).

---

## Answers to the six questions

| # | Question | Take |
| --- | --- | --- |
| 1 | Right cut? | Knowability / actionability, not archive-vs-usage. |
| 2 | Should `RAISE` distinguish the two kinds? | No — one switch plus per-code overrides is enough. The “two wants” problem does not require a `subject` axis yet. |
| 3 | Authoritative extraction channel? | `report.results` for outcomes. Drop `EXTRACTION_MEMBER_BLOCKED` / `_FAILED` as diagnostics once that is not load-bearing. |
| 4 | Collision / sanitize record if not diagnostic? | Keep as diagnostics. Under `REPLACE` / a successful rewrite they are the audit trail; silence is worse than a mixed taxonomy. |
| 5 | Keep the seek tripwire? | Yes. The trigger is call-shaped; the fact (re-decode cost) is not knowable from the contract alone. |
| 6 | Named abort-on-blocked knob? | Yes. Today that behaviour is the strongest argument *against* deleting `_BLOCKED`, and it arrived as an accidental feature. |

---

## What to ship before `0.2.0`

1. Normative knowability ceiling (**A**).
2. A named “abort on first blocked member” knob.
3. Then delete only `_BLOCKED` / `_FAILED` (**E**); keep the other six.
   Move hardlink `failure_group_id` / `failure_group_size` onto
   `ExtractionResult` (or equivalent) before dropping `_FAILED` — those fields
   live only on the diagnostic today.

Skip **B** — too blunt; it reopens silent overwrite and loses the seek
tripwire. Skip **C/D** for now — they add public surface to encode a cut this
note discards. **A** alone is cheap and not tag-gated; the extraction cleanup
is what actually wants to land before the freeze.

---

## After reading the tree

Checked against `src/archivey/diagnostics.py`,
`src/archivey/internal/extraction.py`, `extraction_types.py`,
`openspec/specs/{diagnostics,safe-extraction}/spec.md`,
`docs/extracting.md`, `review/docs/observations.md` (O-23),
`review/simplicity-consistency/q13-rewind-diagnostic.md`, and the matching
tests. Counts: 22 codes in the enum; policy matches by code only.

### Confirmed

- **O-23 never described the library.** Its own prose says “every existing code
  fits” while enumerating only archive-shaped codes and waving
  `STREAM_REWIND_REDECOMPRESSES` through; the four extraction codes were already
  present and unnoticed. The discussion’s dating claim holds.
- **`RAISE` on `EXTRACTION_MEMBER_BLOCKED` already implements abort-on-blocked.**
  Spec, `OnError` docstring, and `docs/extracting.md` all still call that a
  “separate, future opt-in.” `test_raise_disposition_stops_despite_continue`
  locks the accidental feature in.
- **REPLACE collision is silent in `results`.** On a redirected REPLACE write,
  the coordinator sets `requested_path = result.path` on purpose
  (`extraction.py`), so both members look like ordinary `EXTRACTED`.
  `EXTRACTION_NAME_COLLISION` is the audit trail; the O2 REPLACE test asserts
  the diagnostic count, not a result-status change.
- **Sanitize has the same shape.** Successful portable rewrite → `EXTRACTED`;
  the presented→portable pair is only on the diagnostic.
- **Rewind tripwire is cost-based and escalate-every-time.** Not a codec-name
  nag; deleting it deletes the quadratic-seek guard.
- **Option A’s wording is already drafted** in the q13 rewind note. This opinion
  is converging on prior review work, not inventing a third cut.
- **`DiagnosticPolicy` has no subject axis** — overrides are per-code only —
  so C is additive API, not a missing field.

### Refinements (do not change the verdict)

- **The BLOCKED/FAILED dual channel is normative**, not drift:
  `safe-extraction` requires exactly one matching diagnostic per continued
  `BLOCKED`/`FAILED` result. **E is a deliberate spec change**, not a tidy-up.
- **`_FAILED` is only a parallel channel under `OnError.CONTINUE`.** Under
  `STOP`, failures raise and are not converted to advisories. That makes
  deletion cheaper for the common path — but hardlink grouping
  (`failure_group_id` / `failure_group_size`) exists only on
  `ExtractionOutcomeContext`, so it must move before the code can go.
- **`SUPERSEDED` is not a home for REPLACE collisions.** It means
  last-entry-wins non-current members, not O2 overwrite resolution. Do not
  overload it to avoid keeping the collision diagnostic.

### Unchanged conclusion

Still **A + named abort + narrow E**, still reject **B/C/D** for now. The tree
pass mainly raises the cost of step 3: promote abort-on-blocked and migrate
failure-group metadata *before* deleting the two outcome codes, and leave
collision / sanitize / rewind / unused-arg codes alone.
