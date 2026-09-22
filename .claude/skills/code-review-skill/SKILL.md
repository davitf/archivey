---
name: code-review-skill
description: |
  Provides comprehensive code review guidance for Python, plus an archivey-specific
  addendum (VISION, CONTRIBUTING, review/ standards). Also reachable from Cursor via the
  project command in `.cursor/commands/code-review.md`.
  Covers architecture review, performance review, security audit, code quality anti-patterns,
  and common bugs.
  Use when: reviewing pull requests, conducting PR reviews, code review, reviewing code changes,
    establishing review standards, mentoring developers, architecture reviews, security audits,
    performance reviews, checking code quality, finding bugs, giving feedback on code,
    or when the user invokes /code-review-skill.
allowed-tools:
  - Read
  - Grep
  - Glob
  - Bash      # reproduce a finding or check a runtime claim — not to re-run the gates (§10)
  - WebFetch  # look up current docs and best practices
---

# Code Review Skill

> Python-only, English subset of
> [awesome-skills/code-review-skill](https://github.com/awesome-skills/code-review-skill)
> (MIT), trimmed for **archivey**. Repo-specific standards live in
> **[reference/archivey-review-addendum.md](reference/archivey-review-addendum.md)** —
> read it first, and keep its rules out of the upstream-derived guides so the delta stays
> visible.

**Invoke it by name: `/code-review-skill`.** A bare `/code-review` is a *builtin* skill in
both Claude Code and Cursor — different output shape, and it will edit code. Cursor's
[`.cursor/commands/code-review.md`](../../../.cursor/commands/code-review.md) routes
`/code-review` here; everywhere else ask for `/code-review-skill` explicitly.

**Repo default — report findings, edit nothing** unless the user asks. Output is markdown
prose in the addendum's **§0 three-block shape**: (1) maintainer briefing with an index of
the findings, (2) the findings themselves, one per inline thread, ranked by severity ×
confidence, (3) maintainer decisions. Blocks 1 and 3 are the review body; block 2 is the
threads. Not a host-specific findings tool. This holds however the skill is invoked.
Posting to a PR → **§10**, which also carries the header every comment opens with and the
rule against tables in anything posted. Reviewing an OpenSpec proposal instead of code →
**§9** (values-first), same three blocks.

**The review is the handoff.** Do not also produce a prompt or brief for whoever fixes the
PR — they run `address-review-findings` off the PR threads. §0 has the rule.

**Sweeping whole files rather than a diff?** Every file you finish reading gets a `SWEPT`
marker comment on #315, findings or no findings — addendum §10. A clean read that posts
nothing is indistinguishable from a file nobody opened, and twice it has been counted as one.

## Review process

Archivey PRs are **code first, then context** (addendum §8). That replaces the generic
"absorb the design narrative before the diff" order. Context is not optional — it is
pass 2.

### Logistics (≤1 min, before either pass)

This list lives here and nowhere else; addendum §8 points at it.

1. Scope: `git diff main...HEAD` (or the paths / PR named); size (>400 lines? ask to
   split). **Re-reviewing?** The scope is narrower, and §10 defines it
2. CI status if posted (`ruff`, pyrefly/ty, pytest) — glance only, **do not re-run**
   (addendum §10); enough to know whether failures are in-scope. Not posted → say so,
   don't infer
3. Linked artifact **names** only (issue #, `openspec/changes/<name>/`, `review/` finding
   ID) — what to open in pass 2, not the prose yet
4. Addendum **§8** for the two-pass process; §1–§5 as a mental checklist, not as loaded
   designs

### Pass 1 — code alone

Read the changed code and nearby context **cold**. Does the resulting tree make sense
self-contained, and are non-obvious choices explained *near the code*?

- **Logic** — edge cases, off-by-one, `None`, hostile or truncated input
- **Security** — path traversal, bombs, subprocess safety, secrets
- **Performance** — silent re-decompression, unbounded buffers, O(n²) member loops
- **Architecture** — fits the problem, consistent with existing backends, right module layer
- **Reuse** — look for an existing helper before accepting new code; check adjacent modules
- **Tests** — behavior coverage, red–green for fixes, edge/hostile cases
- **Maintainability** — clear names, one job per function, no magic numbers in parsers

Don't hand-review what tooling already owns: formatting, import order, lint violations,
typos.

### Pass 2 — context (required)

PR narrative, linked issue, OpenSpec change, contracts. Addendum §8 carries the order and
the documentation-debt rule; §1–§5 carry VISION ranking, contracts and the domain
checklist. Then write the §0 three-block report.

> Large diff? `git diff main...HEAD | python scripts/pr-analyzer.py` triages complexity
> before you read.

## Severity labels

🔴 `[blocking]` must fix · 🟡 `[important]` should fix, discuss if you disagree ·
🟢 `[nit]` small, but still fixed on this PR — *small*, not *optional*.
Non-blocking annotations: 💡 `[suggestion]` · 📚 `[learning]` · 🎉 `[praise]`.
Those three tiers are the standard across every guide here. Pair each
finding with a confidence tag — `CONFIRMED` / `PLAUSIBLE` / `DISPROVEN` (addendum §0).
Finding IDs carry your own initial (`K1…` from Claude Code, `C1…` from Cursor) so two
reviewers on one PR cannot collide (addendum §10).

**Verdict.** What each one commits to, when "only nits left" is not an approval, and the
round budget: **addendum §0 Verdicts**. A posted review cannot carry a GitHub `APPROVE`
event — expected, not a problem to report; the verdict lives in the text (§10).

## Guides — open as needed

| Doc | When |
|-----|------|
| **[Archivey addendum](reference/archivey-review-addendum.md)** | **Always.** VISION ranking, what to check, output shape, review order, posting rules |
| **[`CONTRIBUTING.md`](../../../CONTRIBUTING.md)** | **At the start of a review.** The coding and testing rules the addendum checks against — it cites them rather than repeating them |
| [Python](reference/python.md) | Mutable default args, exception handling, class attributes |
| [Architecture](reference/architecture-review-guide.md) | SOLID, coupling/cohesion, dependency direction |
| [Performance](reference/performance-review-guide.md) | Streaming, solid-archive costs, memory, complexity |
| [Security](reference/security-review-guide.md) | Path traversal, zip bombs, hostile parsers, subprocess |
| [Universal quality](reference/code-quality-universal.md) | Parameter sprawl, leaky abstractions, stringly-typed code, TOCTOU |
| [Common bugs](reference/common-bugs-checklist.md) | Python + archive-library bug patterns |
| [Error handling](reference/cross-cutting/error-handling-principles.md) | Fail fast, exception hierarchy/translation, logging |
| [Concurrency](reference/cross-cutting/async-concurrency-patterns.md) | Races, deadlocks; sync-first API |
| [Best practices](reference/code-review-best-practices.md) | Communication, reviewer mindset |
| [Reviewing proposals](reference/reviewing-proposals.md) | The thing under review is a proposal / delta spec / `design.md`, not code (§9) |
| [Deep reviews](reference/deep-reviews.md) | A commissioned `review/` brief, not an ordinary PR (§6) |
| [PR template](assets/pr-review-template.md) | The fill-in report shape (form only — the rules are in the addendum) |
