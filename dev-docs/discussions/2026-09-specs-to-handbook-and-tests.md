# Specs → handbook + tests (thinning the contract)

**Written to be acted on incrementally**, not as a big-bang rewrite. Dated 2026-09-02
(alongside PR #280 / pair-workflow adoption).

> **Status: open — direction accepted, migration incremental.**  
> **DP1 = C for now:** `openspec/specs/` remain the authoritative *machine* contract.  
> **Goal:** stop growing unread scenario farms; move executable detail into tests and
> human truth into handbook pages; eventually specs can shrink to thin cards or go away
> for areas that are fully covered.

Living reading surface: [`../pair-workflow.md`](../pair-workflow.md).  
Create-on-first-use handbook: `dev-docs/formats/<format>.md`, `dev-docs/topics/<topic>.md`.

---

## Problem

Dense OpenSpec scenarios are hard for humans to read and often **over-specify** details
that already belong in (or should belong in) pytest. Agents treat every SHALL as a
landmine and avoid useful refactors. We also do not want to keep *adding* scenario junk
while waiting for a future cleanup.

## Target dual contract

| Layer | Holds |
| --- | --- |
| Handbook (`formats/` / `topics/`) | Principles, consequences, one-line decisions, **links to tests** |
| Tests | Concrete behaviour / edge cases (today’s WHEN/THEN farms) |
| Specs (thin, for now still authoritative) | Public API shapes, intentional non-support, cross-cutting invariants, security posture — **not** every edge matrix |

## Thin as you go (do this on every spec-touching PR)

When you **add or edit** a capability under `openspec/specs/` or a change delta:

1. **Prefer a test over a new scenario.** If the claim is “given input X, behaviour Y”,
   pin it in pytest (corpus / focused test) and **link that test** from the handbook page
   or from a one-line Verify note in the thin brief. Do not grow a WHEN/THEN farm for it.
2. **If a scenario already exists and a test covers it**, prefer **deleting or collapsing
   the scenario** into a short matrix row or a “covered by `tests/…`” note when you touch
   that requirement — do not expand sibling scenarios “for completeness”.
3. **Handbook over ADR / design essay** for “why here”: light bullets on
   `formats/<fmt>.md` or `topics/<topic>.md` (create in the same PR if missing).
4. **Keep in the spec** only what tests cannot say well: non-goals, packaging policy,
   threat-model posture, “MUST refuse”, public signature tables.
5. **Default schema:** `--schema minimalist` when proposal/design would only be agent bus.
6. **Pause and ask** if deleting a scenario would drop the only statement of a product
   rule with no test yet — add the test first, then thin.

This is the anti-junk rule: **every PR that touches specs should leave that capability
no denser than it found it**, and preferably thinner where tests already exist.

## Suggested migration order (when doing a dedicated pass)

1. Pick one capability you are already changing (e.g. a format).  
2. Map its scenarios → existing tests; gap-fill missing tests.  
3. Create/update the handbook page with principles + Verify links.  
4. Shrink the spec to signatures / non-goals / MUST NOT + pointers.  
5. Repeat; do not schedule a “delete all specs” milestone until several capabilities have
   been thinned this way without agent regressions.

## Out of scope for day one

- Deleting `openspec/specs/` wholesale  
- Changing DP1 from C without a maintainer call  
- Inventing empty handbook trees ahead of a real page  

## Related

- Pair workflow: [`../pair-workflow.md`](../pair-workflow.md)  
- Adoption crib: [`2026-09-pair-workflow-adoption.md`](2026-09-pair-workflow-adoption.md)  
- Spec density (schema): [`../../openspec/schemas/library/README.md`](../../openspec/schemas/library/README.md)  
- CONTRIBUTING §Working with the specs
