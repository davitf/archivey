# Code Review Quick Checklist

Quick reference for reviewing changes in this Python archive library.

Archivey order: **code first, then context** (addendum §8). Do not absorb OpenSpec /
design / long PR rationale before the cold code pass.

## Logistics (≤1 min) — before either pass

- [ ] The four-item list in `SKILL.md` → Logistics (scope, CI status, artifact names,
  §8). Not restated here.

## Pass 1 — code alone

Read the changed code (+ nearby context) cold. Self-explanatory resulting tree;
local *why* for non-obvious choices; bugs / safety / tests.

`SKILL.md` → Pass 1 has the per-area list (logic, security, performance, architecture,
reuse, tests, maintainability) and addendum §5 the archivey-specific rows. Tick these,
which are the ones reviews here actually miss:

- [ ] Edge cases: truncated / hostile input, `None` metadata, off-by-one on length fields
- [ ] Error handling uses the library exception contract (addendum §3)
- [ ] Format parity preserved, or the difference is explicit data
- [ ] Extract-path safety and bomb/resource limits considered (addendum §5)
- [ ] No silent re-decompression; cost signals still honest (addendum §5)
- [ ] Changes land in the right module layer; public API impact intentional
- [ ] An existing helper wasn't reinvented — checked adjacent modules
- [ ] Red–green test for a bugfix; a stated mutation for any new guard / property test
- [ ] Complex parser logic explained *near the code*, not only in PR prose
- [ ] Comments still match the code: none naming a deleted call site or a case the change
  made unreachable, none carrying history, none claiming more than the code guarantees
  (addendum §3 Comments — the largest finding category here)
- [ ] New resource bounds are reachable from `ArchiveyConfig`, not a parser `_MAX_…`
  constant (addendum §5)
- [ ] Nothing drops or clamps data silently; a changed exception type was checked against
  what catches it upstream (`CONTRIBUTING.md`)

## Pass 2 — context (required)

- [ ] PR description + linked issue / full OpenSpec change / `review/` finding
- [ ] Contract fit: OpenSpec / VISION / threat model / addendum (§1, §3, §5)
- [ ] Spec ↔ code ↔ docs: match, intentional revision, or pause-and-ask
- [ ] Concerns that only dissolve after external prose → usually 🟡 doc debt in the code
- [ ] Write the **§0 three-block report**: briefing → implementor handoff → decisions
- [ ] Posted it, and wrote **no** separate implementor prompt — the PR is the handoff
- [ ] Closed with a **Measured this round** list, so the next round inherits it (§10)

## Re-reviews (round 2 and later)

- [ ] Scope is `<last-reviewed-sha>..HEAD`, not `main...HEAD` (addendum §10)
- [ ] Status table over the previous IDs before any new finding, **in place of** "what
  this change is" — that is first-round only (§0)
- [ ] Nothing re-measured that a previous round already recorded (§10)
- [ ] Round 3+ with only 🟢 nits open → conditional approval, not another round (§0)

---

## Output shape (addendum §0 — rules and brevity fence live there)

1. **Maintainer briefing** — what the change is, snapshot + verdict, main 🔴/🟡 points
2. **Implementor handoff** — full findings for the PR (severity × confidence, `file:line`)
3. **Maintainer decisions** — only human calls; each decidable without the diff; or `None.`

---

## Red Flags

- Empty `except:` / swallowed errors
- `shell=True` with path interpolation
- Ad-hoc path joins on extract destinations
- TODO left in production paths without issue link
- Commented-out code
- Magic numbers in parsers
- Copy-pasted codec/backend blocks that should share helpers
- Hardcoded credentials
