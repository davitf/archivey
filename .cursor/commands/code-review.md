# Code review

Review the relevant code with a code review mindset.

## Priorities (Cursor `/code-review` defaults)

Keep these as the primary lens:

1. **Bugs** and correctness errors
2. **Behavioral regressions**
3. **Security / safety** issues
4. **Missing tests** (especially red–green for bug fixes)

**Findings must be the primary focus**, ordered by severity. Do **not** make code
changes unless the user explicitly asks for them.

## Archivey process (best of both)

This repo vendors a fuller review skill under `.claude/skills/code-review-skill/`.
Combine the priorities above with that skill’s process:

1. **Read process rules:** `.claude/skills/code-review-skill/reference/archivey-review-addendum.md`
   — especially **§0 (output shape + finding discipline)** and **§8 (code first, then
   context)**, plus VISION ranking, contracts, and the domain checklist. Do **not**
   absorb OpenSpec / design / long PR rationale before the cold code pass.
2. **Pass 1 — code alone:** changed code (+ nearby context) for self-explanatory
   sense in the resulting tree, local docs for non-obvious choices, bugs/safety/tests.
   Use `.claude/skills/code-review-skill/SKILL.md` techniques and severity labels;
   open deeper guides under `reference/` only as needed.
3. **Pass 2 — context (required):** PR narrative, OpenSpec change, VISION / threat
   model / addendum rows that apply — check contract fit; pause-and-ask on
   discrepancies. Findings that only dissolve after external prose are usually
   documentation debt in the code (addendum §8).

## Scope

- Default: current branch vs `main` (`git diff main...HEAD` and/or `@Branch`), plus any
  paths or PR the user named.
- If the user is asking about uncommitted work, include the working-tree diff.
- Prefer concrete `file:line` evidence and triggering inputs/states.
- **Reviewing an OpenSpec proposal / `design.md` instead of code?** Skip the code-first
  order and use the addendum's **§9 (values-first)** — check the design against
  VISION/CONTRIBUTING values and contracts, then proposal shape, then hunt **decision
  gaps & unknown unknowns** (what an implementor must decide first; what the proposal
  isn't thinking about) and raise them as maintainer questions.

## Output format

Emit **exactly three sections**, in order — addendum **§0 (Output shape)**. The
maintainer often has not read the diff; do not dump a long findings list first.

**Brevity fence:** short form applies only to blocks 1 and 3 *presentation*. Do not
shrink review depth (full §8/§9 passes), finding discipline, block 2 specificity, or
real pause-and-ask items. A thin block 2 that matches a short briefing is a failed
review, not compliance.

### 1. Maintainer briefing (read this first)

Short and scannable (about half a screen unless the change is huge):

- **What this change is** — 2–4 plain-language sentences (intent, areas touched,
  behaviour delta); no assumed PR/OpenSpec familiarity
- **Snapshot** — size, gates if known, **Verdict** (✅ Approve / 💬 Comment /
  🔄 Request Changes)
- **Main points** — ranked one-liners for 🔴/🟡 only (severity + gist; no essays)
- **What’s fine** (optional, 1–3 bullets)

### 2. Implementor handoff (copy-paste ready)

Paste-safe block for the implementor. Self-contained (no “see above”). Prefer
thoroughness here over keeping the whole reply short:

- One-line context header, then **full findings** ranked by severity × confidence
  (`CONFIRMED` / `PLAUSIBLE` / `DISPROVEN→reclassified`)
- Each finding: severity, confidence, `file:line`, what’s wrong, why it matters, fix
  direction, trigger/repro when possible (`CONFIRMED` + trigger = red–green candidate)
- Include 🟢/💡 here; end with the Verdict line again

Follow **addendum §0 (finding discipline)**: over-report on existence, label honestly;
verification *reclassifies* (a disproven bug often becomes clarity/doc-debt) — it never
silently culls. Rank archivey blockers using the addendum (VISION claims, exception
contract, path/bomb safety, silent solid re-decode, unjustified debt, etc.).

### 3. Maintainer decisions (your attention)

Numbered list of items that need a **human call** only. Each item must be decidable
without reading blocks 1–2 or the diff: the decision, why it needs you, options +
consequences when useful, optional recommendation. Routine fixes stay in block 2. If
nothing needs a decision: `None.` Do not invent filler questions; when unsure whether
something needs a human call, include it.

Skip formatting/lint nits that `ruff` / type-checkers already own.
