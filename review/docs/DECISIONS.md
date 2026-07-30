# Phase-2 decisions

Answers from the maintainer. Recorded here so phase 3 has one place to read from;
the affected phase-1 artifacts have been updated in place to match.

Still open: **Q3, Q4, Q5, Q6, Q8, Q9** (`QUESTIONS.md`).

---

## D1 — Q1: unpublish `docs/internal/`. **Approved.**

> *"we can unpublish. let's leave the published docs user-facing only"*

The published site becomes the "User + current" quadrant. `docs/internal/` (12
files, 3,731 lines) and `docs/grab-bag/` (6 files, 3,068 lines) leave it. Option A
in Q1 — path move, not `exclude_docs` — so the invariant *everything under `docs/`
is published* holds with no exception list.

**Consequences to carry into phase 3:**

- An OpenSpec delta to `documentation` (the site-IA requirement at
  `spec.md:80-94`) and to both specs that name `docs/internal/library-analysis.md`
  verbatim (`documentation:65,77`, `packaging-and-extras:141`).
- Published lines drop from 9,316 to roughly 2,000–2,200.
- 13 nav entries deleted (`Internal:` ×7, `Grab-bag:` ×6).

---

## D2 — Q2: curated behind-the-scenes material may stay published. **Approved, with an addition.**

> *"optionally with some curated higher-level implementation details for curious
> users to know what's going on behind the scenes and the major decisions"*

Two things, and they are different:

**The major decisions** → `docs/decisions/` stays published, confirming the Q2
recommendation. Ten links from four user pages keep working.

**What's going on behind the scenes** → there is no page for this today. The
closest thing is `docs/grab-bag/ARCHITECTURE.md`, which is 1,017 lines, stale
(`observations.md` O-10), and about to be unpublished. A curious user currently has
the choice of a generated API reference or a valgrind transcript.

**Proposed: one new page, `docs/how-it-works.md`** (~120 lines), sitting late in the
nav, before the API reference. Curated overview, not a design document:

| Section | Sourced from |
|---|---|
| Native-first parsing — why 7z/RAR headers are parsed in pure Python, and what that buys | `VISION.md:29-34`, ADRs 0001/0002 |
| The uniform stream layer — one pull-based codec layer that format parsers compose | `library-analysis.md:14-19`, `compressed-streams` spec |
| Where the cost model comes from — why `CostReceipt` exists rather than silent heuristics | `access-mode-and-cost` spec, ADR 0003 |
| Backends and the registry — how format detection picks one, what an extra actually adds | `backend-registry` spec |
| What is *not* ours — stdlib `zipfile`/`tarfile`, `unrar`, `pycdlib`, and why | ADRs 0006/0002, `formats.md` |

Each section: a paragraph, then a link out for depth. Depth links follow D3.

This is a **new page**, so writing it is genuinely Topic 8 work, not a move. Phase 3
should create the file and the nav slot; phase 8 fills it. Marked optional — say the
word if you would rather not carry another page.

---

## D3 — Published pages must not link into unpublished docs. **New rule.**

> *"we shouldn't link to internal docs. if preserving a link is important to give
> additional context, we could change that link to point to the repository doc
> inside github"*

The rule, in order:

1. **Prefer no link.** If a published page needs a fact, the fact belongs on a
   published page. A link into maintainer material is usually a sign the fact is
   filed in the wrong place — which is the case for two of the nine (see below).
2. **If the context is genuinely worth preserving**, link the file on GitHub:
   `https://github.com/davitf/archivey/blob/main/dev-docs/<file>.md`. This is the
   pattern `README.md:20-22` already uses for `CONTRIBUTING` / `VISION` /
   `SECURITY`.
3. **Never** a bare repo path in prose as a substitute for a link — that was the
   phase-1 proposal and D3 supersedes it.

### The nine links, resolved

| Published page | Currently links | Under D3 |
|---|---|---|
| `safe-extraction.md:28` | `internal/threat-model.md` | **No link.** Q7 moves the enforced-guarantees prose onto this page; the residual gap register is not something the page needs to point at. |
| `gotchas.md:144` | `internal/known-issues.md` | **GitHub link.** "Don't close a source under a live accelerator stream" is stated on-page; the link is optional depth for someone who wants the upstream analysis. |
| `costs.md:143` | `internal/known-issues.md` | **GitHub link.** Same. |
| `gotchas.md:155` | `internal/open-issues.md` | **No link.** `open-issues.md` says "**Not user-facing**" in its own first line; pointing users at a maintainer triage list was always wrong. |
| `formats.md:23` | `internal/library-analysis.md` | **GitHub link.** "Which library backs each codec and why" is exactly the curious-user depth D2 describes. |
| `acknowledgements.md:11` | `internal/library-analysis.md` | **GitHub link.** Same. |
| `acknowledgements.md:44` | `internal/library-analysis.md` | **GitHub link.** Same. |
| `acknowledgements.md:46` | `internal/known-issues.md` | **GitHub link.** Same. |
| `index.md:47-48` | `internal/index.md`, `grab-bag/index.md` | **Rewrite the block.** The "For contributors" section becomes a short pointer to the repo (`CONTRIBUTING.md`, `openspec/specs/`, `dev-docs/`) rather than a nav-style list of site sections that no longer exist. |

Net: 4 links removed, 5 become absolute GitHub URLs, 1 block rewritten.

### The cost this adds, stated

Absolute `blob/main/` URLs rot silently when a file is renamed — the failure mode
this whole review exists to fix, reintroduced in miniature. Two mitigations, both
already planned:

- **Phase-4 guardrail #2** (link checker) must cover absolute `github.com/davitf/archivey`
  URLs in `docs/**`, not only the five in `README.md`. That was already its scope;
  D3 raises the count from 5 to ~10 and makes it load-bearing rather than
  precautionary.
- Keep them pointed at `main`, not a tag. A curious user following a
  behind-the-scenes link wants the current state of that document.

**Unresolved detail for phase 3:** GitHub renders `.md` at `blob/main/` with its own
navigation and no docs-site styling, so a reader crossing that boundary notices.
That is arguably correct — it signals "you are now reading maintainer material" —
but if you would rather it be seamless, the alternative is publishing a small
curated subset instead of linking out, which is what D2's `how-it-works.md` does for
the architecture story. The two mechanisms overlap; D2 handles the common case and
D3 handles the long tail.
