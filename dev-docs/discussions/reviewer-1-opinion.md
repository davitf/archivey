# Reviewer 1 opinion — diagnostics archive-vs-usage

Response to [`diagnostics-archive-vs-usage.md`](diagnostics-archive-vs-usage.md).
Read-only take on the discussion doc alone; the rest of the repo was not
re-audited for this note.

**Verdict:** reject the archive-vs-usage cut. Do **A** as the ceiling rule, plus
a narrow **E** on extraction — not **B**, and not **C/D** unless callers later
need axis-level policy.

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

Skip **B** — too blunt; it reopens silent overwrite and loses the seek
tripwire. Skip **C/D** for now — they add public surface to encode a cut this
note discards. **A** alone is cheap and not tag-gated; the extraction cleanup
is what actually wants to land before the freeze.
