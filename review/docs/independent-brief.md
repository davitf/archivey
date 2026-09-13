# Brief — independent code-derived documentation outline (bias control)

Commissioned 2026-07-29 against `main` @ `403e7ff`, as an **input to** the docs full
review ([`brief.md`](brief.md)) rather than a review of its own.

## Why this exists

`brief.md` hands its reviewer an inventory, a four-quadrant taxonomy, and a list of named
problems. That is efficient, and it is also **anchoring**: a reviewer given a structure
will mostly confirm it. The failure mode that survives is the one an incumbent reviewer
structurally cannot see — **what was never written down at all**.

So: derive, from the code alone, the documentation set this library *should* have. The
artifact we want is not your docs. It is the **diff** between what you propose and what
exists — and most of the value is in what you include that we lack.

## Deliberate exception to a repo convention

`review/README.md` says a re-review that resurfaces settled ground wastes budget, and
briefs normally front-load context to prevent that. **This brief deliberately does the
opposite**, and the waste is the point: re-derivation from scratch is the only way to get
an unanchored answer. Do not treat the convention as forgotten.

## Inputs

**Read these:**

- `src/` — the implementation and its docstrings.
- `tests/` — the richest input available. Tests encode intended behaviour, edge cases,
  and error contracts far better than prose does.
- `pyproject.toml` — the public surface, extras, entry points, supported Pythons.

**Do NOT read these** — reading them defeats the entire purpose:

- `docs/**` (including `internal/` and `grab-bag/`), `README.md`
- `VISION.md`, `PLAN.md`, `IDEAS.md`, `CHANGELOG.md`, `SECURITY.md`
- `review/**` — including `brief.md`, which sits next to this file
- `CONTRIBUTING.md`, `AGENTS.md`, `CLAUDE.md`

**`openspec/specs/`: excluded on this pass.** A real tension — the specs are normative and
would make you more accurate, but they are *derived from intent*, so reading them
re-anchors you on the same framing the existing docs came from. If the maintainer wants
a spec-informed second opinion, that is a separate pass with a separate brief.

If you find yourself needing a forbidden file to answer something, **that is a finding**:
record what you could not determine from code, and move on.

## Framing: argue, don't propose

Neutral framing produces agreement. Work adversarially:

- Assume the current documentation is **wrong until the code says otherwise**.
- For every page you propose, state what breaks for a user if it does not exist.
- Prefer the uncomfortable claim ("this API cannot be used safely without explaining X")
  over the safe one ("a usage guide would be helpful").

## Deliverables

Write into `review/docs/independent/`. **Do not modify anything under `docs/`.**

1. **`api-surface.md`** — the public surface as the code defines it: every exported
   symbol, what it does, its error contract, and its preconditions. Cite `file:line`.
   This doubles as a check on the real docs later.

2. **`must-explain.md` — the highest-value deliverable.** Every behaviour a user *will*
   hit that is **not inferable from the signature**: defaults that surprise, operations
   that are O(n²) if used naively, errors that mean something non-obvious, states that
   are illegal but type-check, resources that must be closed in an order. For each: the
   concrete failure a user experiences if nobody tells them.

3. **`rationale-gaps.md`** — behaviours where you can see **what** the code does but not
   **why**, and where a reasonable user would ask why. This is the inverse of the usual
   deliverable and it is deliberately valuable: it is precisely the list of things
   documentation *must* carry, because the code cannot. Do not guess the rationale —
   naming the gap is the whole output.

4. **`proposed-outline.md`** — the doc set you would write: page list, one line of purpose
   each, ordered by what a new user needs first. Include rough proportions (which pages
   are long, which are a screen). If a topic deserves a third of the guide, say so.

5. **Two sample pages, no more.** Pick the two you think matter most and write them
   properly. This is a probe for depth and voice, not a writing assignment.

**Do not write the full documentation set.** Generated prose has to be verified claim by
claim against the code, and that verification costs more than the writing saved.
Unverified generated docs are worse than missing ones: they are confidently wrong in ways
a reader cannot detect. The outline is the product.

## Rules of evidence

- Every behavioural claim cites `file:line` or a test. If you inferred it, say "inferred".
- Where the code is ambiguous, say so rather than picking the plausible reading — an
  ambiguity you flag is more useful than a guess you don't.
- Do not propose documenting something as a *defect* when it may be a deliberate
  trade-off; you cannot see intent from here. Phrase those as questions.

## Known limitation, recorded honestly

**Independence here is partial.** Isolating context removes *anchoring*; it does not
remove *model priors*. If this outline broadly matches the existing structure, that is
weak evidence the structure is right — it may only show that two runs of similar models
reach for similar shapes. Weight **disagreements** heavily and **agreements** lightly.

Two things sharpen it, in order of effect:

- run this on a **different model** from the one that wrote `brief.md`;
- the adversarial framing above, which is why it is a requirement and not a suggestion.

## How the output will be used

Not adopted, and not merged into `docs/`. The docs review will diff it against the real
structure and triage into three buckets:

| Bucket | Meaning |
|---|---|
| **We have it** | agreement — weak signal, note and move on |
| **We lack it** | the blind spot this exercise is for — triage seriously |
| **We deliberately don't** | the agent could not see intent; record *why* so the reason is written down |

That third bucket is a quiet win: every entry is a rationale that currently lives only in
the maintainer's head, and it feeds `rationale-gaps.md` straight into Topic 8.

## Timing

Run **before** the docs audit (`brief.md` phase 1) publishes findings, so nothing leaks
backwards. If that ordering slips, run it anyway — the input isolation matters more than
the sequence, since the agent never reads `review/` regardless.
