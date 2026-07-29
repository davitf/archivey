# Page shape — acting on the independent pass's proportional disagreement

The brief reserves **merge / split / delete** and **dual-audience splits** to this
phase, because deferring them would misfile pages rather than merely leave them
unpolished. This file is that work. Writing the prose is Topic 8; deciding what
each page is *responsible for* is here.

Measured shares of the 1,482-line user guide at `ce674bf`:

| Page | Lines | Share | Independent pass proposed |
|---|---:|---:|---:|
| `usage.md` | 270 | **18.2%** | argues *against* a generic Usage page |
| `formats.md` | 180 | 12.1% | ~12% (agreement — weak signal, ignore) |
| `migrating.md` | 174 | 11.7% | not in its outline (no migration evidence base) |
| `gotchas.md` | 156 | 10.5% | not proposed as a page |
| `costs.md` | 154 | 10.4% | **~20%** (access / streaming / cost) |
| `support-matrix.md` | 140 | 9.4% | — |
| `acknowledgements.md` | 96 | 6.5% | — |
| `safe-extraction.md` | 93 | **6.3%** | **~25%** |
| `api.md` | 90 | 6.1% | separate, large |
| `philosophy.md` | 79 | 5.3% | — |
| `index.md` | 50 | 3.4% | ~5% |

The two disagreements are the payload; the `formats` match proves little (shared
model priors). Both disagreements point the same way: **the guide's two largest
pages are a manifesto-adjacent grab-bag (`usage.md`) and a migration guide, while
the page carrying VISION load-bearing claim #1 is the thinnest thing on the site.**

---

## 1. `safe-extraction.md` — 93 lines carrying the project's first claim

`VISION.md:26` states claim #1 as: *"Extraction cannot be zip-slipped,
symlink-escaped, or decompression-bombed unless the caller explicitly opts out.
Safety is a contract (specced, tested, threat-modeled), not a feature flag."*

`openspec/specs/safe-extraction/spec.md` is **809 lines** — the largest spec in the
tree. The user-facing page for it is 93 lines: a bullet list of enforced
protections, a policy table, a limits paragraph, and a two-line diagnostics note.
Its deepest sentence on trust boundaries is a link *out of the guide* into
`internal/threat-model.md` (line 28) — a page this review recommends unpublishing.

**The material to reach ~25% already exists. It is filed in four places:**

| Source | Lines | What it carries |
|---|---:|---|
| `docs/gotchas.md` §Extraction (103–126) | 24 | The 12-row "need to know" table: STRICT rewrites names, collisions on every OS, `OnError` semantics, hardlink+filter orphaning, staging leftovers, nested archives |
| `docs/gotchas.md` §Names, duplicates, hardlinks (91–102) | 12 | `get()` last-wins, `extract_all(members=["x"])` matching *every* `x`, positional hardlink resolution |
| `docs/internal/threat-model.md` §"What is already enforced" (26–58) | 33 | The three-layer symlink defence, extraction-root overwrite rejection, permission hygiene, atomic write semantics — maintainer-framed, but this is exactly the "what do you actually defend?" answer an evaluating user wants |
| `SECURITY.md` §"Hardening notes for callers" (68–89) | 22 | Accelerators are not the defended fuzz surface; `unrar` is in your trust boundary; extract to a scratch dir first |

Merged and de-duplicated that is ~250–300 lines. Against a post-split guide of
roughly 1,600 lines that is **~17%** — a near-tripling, short of 25%. Closing the
rest needs genuinely new prose (worked examples, a bounded-recursion recipe for
threat-model O6, what `TRUSTED` does *not* relax). **That writing is Topic 8.**

**Phase-1 decision:** `safe-extraction.md` becomes the guide's largest page and
owns the whole extraction-safety story. It absorbs the four sources above.
`threat-model.md` keeps the O1–O8 / C1–C4 gap register and stops being the only
place the enforced guarantees are written down.

---

## 2. `usage.md` — one page doing five jobs

270 lines, currently: install (13) · open and list (14) · damaged archives (20) ·
read a member (28) · one-shot extract (9) · detect (7) · streaming (11) · stored-hash
dedupe (31) · duplicate names (30) · passwords (9) · error handling (34) ·
**CLI (49)** · next steps (8).

The clearest evidence it is mis-shaped: **the CLI**. It has a 272-line spec
(`openspec/specs/cli/spec.md`), an entire archived product review
(`review/archive/2026-07-20-cli-product/`, 883 lines), and `VISION.md:123` calls it
"a wedge and a dev tool… the safer `unzip`/`tar` that demos the library in ten
seconds". It gets 49 lines at the bottom of a page called "Basic usage" and **no
nav entry of its own**. A reader looking for the command-line tool has no reason to
open "Basic usage".

Second: **install**. The independent pass's #1 gap is that users `pip install
archivey`, try RAR or ISO, and conclude the library is broken. Today install is 8
lines at the top of `usage.md` and the format × extra × tool information is spread
across `formats.md`'s quick matrix, `acknowledgements.md`'s extras table, and
`support-matrix.md`. `format_availability()` exists in the API to answer exactly
this, and no page is built around it.

**Phase-1 decision — split into four:**

| New page | From `usage.md` | Also gains |
|---|---|---|
| `install.md` | Install (lines 3–12) | The format × extra × external-tool table, driven by `format_availability` |
| `reading.md` | Open/list, damaged archives, read a member, one-shot extract, detect, streaming, dedupe, duplicate names, passwords | The 56-line `## Guarantee (for users)` currently buried in ADR 0014 (line 320) |
| `errors-and-diagnostics.md` | Error handling (180–213) | Diagnostics — today two lines in `safe-extraction.md` (90–93) plus a bare mkdocstrings list in `api.md` |
| `cli.md` | CLI (215–262) | Its own nav entry |

This is a **move**, not a rewrite: each block lands whole. The one addition inside
phase 1's scope is the ADR-0014 user guarantee, which is a dual-audience split
(Q4), not new prose.

---

## 3. `gotchas.md` — a digest that became a third copy

156 lines. Its own framing (line 5) is *"If you read only one page after Basic
usage, make it this one"* — a curated index, and a good idea.

But it currently **restates** rather than links. Four of its seven sections have a
same-titled section in `costs.md`:

| Section | `gotchas.md` | `costs.md` |
|---|---|---|
| Seeking and redecompression | 13–25 | 81–100 |
| Solid archives and open order | 27–37 | 57–79 |
| Streaming mode is one pass | 49–58 | 125–129 |
| Passwords | 60–70 | 131–136 |
| Accelerators / native libraries | 128–144 | 138–143 |

Plus §Format limitations (71–89) restating `formats.md`, and §Extraction (103–126)
restating `safe-extraction.md`.

**This is not hypothetical drift — it has already happened.** The rapidgzip gzip
truncation caveat exists in four places, and two of them are stale against the
authoritative spec (`observations.md` O-2).

**Phase-1 decision:** `gotchas.md` keeps its slot (the `documentation` spec
requires it there) and keeps being the "read this next" page, but each bullet
becomes **one line plus a link to the owning page** — target ~80 lines. It stops
being a place a fact can be written down for the third time.

> **Constraint check:** `openspec/specs/documentation/spec.md:175` requires the
> Gotchas page to cover multi-volume ZIP, the ZIP/ISO seek requirement, the UTF-8
> bit-11 case, and TAR silent-shorten "as today's behavior". A one-line-plus-link
> entry for each still satisfies that — the requirement is about *coverage and
> framing*, not length. Confirm in Q3.

---

## 4. Splits summary

| Page | Operation | Destination | Question |
|---|---|---|---|
| `docs/usage.md` (270) | split 4 ways | `install` / `reading` / `errors-and-diagnostics` / `cli` | — |
| `docs/gotchas.md` (156) | shrink to index (~80) | content already lives in `costs`/`formats`/`safe-extraction` | Q3 |
| `docs/safe-extraction.md` (93) | grow ~3× by absorption | from `gotchas`, `threat-model`, `SECURITY.md` | Q7 |
| `docs/internal/threat-model.md` (320) | split | enforced-guarantees → `safe-extraction.md`; O/C register → `dev-docs/` | Q7 |
| `docs/decisions/0014-*.md` (615) | split 3 ways | ADR (~30) stays; investigation → `dev-docs/investigations/`; user guarantee → `reading.md` | Q4 |
| `docs/internal/known-issues.md` (709) | **no split** | user-relevant 5% is already summarised in `gotchas`/`costs` | Q7 |

`known-issues.md` is the one dual-audience case that resolves to *no split*. Read
end to end it is valgrind output, CI workflow bandages, version-matrix soak tables,
bisect recipes, and red-CI run IDs. The four things a user needs from it — don't
close a source under a live accelerator stream, turn accelerators off for untrusted
input under a latency budget, `import archivey` patches pycdlib process-globally,
bare-`.gz` truncation detection is best-effort — are **already** summarised in
`gotchas.md` and `costs.md` with a link back. Unpublishing the register and turning
those links into repo-path mentions loses a user nothing.

## Handed to Topic 8, not done here

Recorded in [`observations.md`](observations.md) and deliberately not acted on:
accuracy against the code, the four-way rapidgzip drift, `AGENTS.md`'s two false
statements, worked examples, tone, and the ~8% of "safe extraction should be 25%"
that merging cannot supply.

## Handed to Topic 7

- Whether `install.md` + `format_availability` actually stops the "I installed it
  and RAR didn't work" abandonment. That is a persuasion question.
- Whether the ADR log reads as trust-building or as noise to an external evaluator
  (this review recommends keeping it published on the assumption that it builds
  trust — Q2 asks the maintainer to confirm).
