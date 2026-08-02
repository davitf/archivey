# Phase-2 decisions

Answers from the maintainer. Recorded here so phase 3 has one place to read from;
the affected phase-1 artifacts have been updated in place to match.

Still open: **Q3, Q4, Q5, Q6, Q8, Q9** (`QUESTIONS.md`).
(Q7's dual-audience filing is carried by D1 + D3; the judgement call about publishing
the gap register is still open under Q7.)

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

## D2 — Q2: curated behind-the-scenes, not the raw ADR log. **Revised 2026-08-02.**

> *Earlier (2026-07-29): "optionally with some curated higher-level implementation
> details for curious users… and the major decisions" — recorded as keeping
> `docs/decisions/` published.*
>
> *Revised (2026-08-02): a summary of technical decisions is fine (with links to
> ADRs / OpenSpec changes); the whole raw ADR corpus is not. "1: A, single page;
> 2: A [move ADRs to `dev-docs/decisions/`]; 3: prefer dropping [user-page ADR
> links], unless the ADR has extra info relevant to the end user that can't be
> easily inlined."*

Two things, and they are different:

**What's going on behind the scenes + the major decisions** → one new published
page, `docs/how-it-works.md` (~120–180 lines), sitting late in the nav before the
API reference. Curated overview, not a design document. It absorbs both halves of
the earlier D2: architecture sketch *and* a short decisions summary (one paragraph
or bullet per load-bearing choice, with optional GitHub / OpenSpec links for depth).

| Section | Sourced from |
|---|---|
| Native-first parsing — why 7z/RAR headers are parsed in pure Python, and what that buys | `VISION.md:29-34`, ADRs 0001/0002 |
| The uniform stream layer — one pull-based codec layer that format parsers compose | `library-analysis.md:14-19`, `compressed-streams` spec |
| Where the cost model comes from — why `CostReceipt` exists rather than silent heuristics | `access-mode-and-cost` spec, ADR 0003 |
| Backends and the registry — how format detection picks one, what an extra actually adds | `backend-registry` spec |
| What is *not* ours — stdlib `zipfile`/`tarfile`, `unrar`, `pycdlib`, and why | ADRs 0006/0002, `formats.md` |
| Decisions summary — one short entry per load-bearing ADR outcome | `dev-docs/decisions/` (after the move) |

Each architecture section: a paragraph, then a link out for depth (GitHub per D3,
or an OpenSpec change). The decisions summary is not a second nav entry and not a
mirror of the ADR index.

**The raw ADR log** → unpublished. `git mv docs/decisions/ → dev-docs/decisions/`
so D1's invariant (*everything under `docs/` is published*) stays intact — no
`exclude_docs` exception list. New ADRs are written there; the filing rule in
[`target-tree.md`](target-tree.md) points at `dev-docs/decisions/`.

**User-page ADR links (ten today)** — prefer **drop**, after inlining any
end-user-relevant one-liner onto the calling page. Keep a link only when the ADR
still has user-relevant depth that cannot be inlined cheaply; that link is then an
absolute GitHub URL under D3, never a site-relative path. Phase 3 resolves each of
the ten under that rule (`acknowledgements` ×4, `migrating` ×3, `support-matrix`
×2, `usage` ×1, plus `index.md`'s "Decision log" nav pointer which becomes a
pointer into `how-it-works.md` or is removed).

This is a **new page**, so writing `how-it-works.md` is Topic 8 work, not a move.
Phase 3 creates the file and the nav slot; phase 8 fills it. The ADR path move is
phase-3 mechanical work alongside `docs/internal/` → `dev-docs/`.

**Spec delta note:** the D1 delta to `documentation` must also stop requiring the
decision log on the MkDocs site (`spec.md:84`, scenario at `:94` naming
`docs/decisions/`). Contributor lookup of "why not py7zr" lands in
`dev-docs/decisions/` (and/or the published summary).

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
| `formats.md:23` | `internal/library-analysis.md` | **GitHub link.** "Which library backs each codec and why" is exactly the curious-user depth D2 describes — or fold into `how-it-works.md` and drop. |
| `acknowledgements.md:11` | `internal/library-analysis.md` | **GitHub link.** Same. |
| `acknowledgements.md:44` | `internal/library-analysis.md` | **GitHub link.** Same. |
| `acknowledgements.md:46` | `internal/known-issues.md` | **GitHub link.** Same. |
| `index.md:47-48` | `internal/index.md`, `grab-bag/index.md` | **Rewrite the block.** The "For contributors" section becomes a short pointer to the repo (`CONTRIBUTING.md`, `openspec/specs/`, `dev-docs/`) rather than a nav-style list of site sections that no longer exist. |

Net: 4 links removed, 5 become absolute GitHub URLs, 1 block rewritten.

D2's ten ADR links are a separate list, resolved under D2's drop-unless-uninlinable
rule (not the nine above).

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
the architecture / decisions story. The two mechanisms overlap; D2 handles the
common case and D3 handles the long tail.
