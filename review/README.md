# `review/` — deep-review briefs & findings

External deep reviews of the codebase: each is commissioned with a **brief** (the
scoped prompt handed to a fresh model) and produces **findings** (SUMMARY + theme
files + QUESTIONS). This directory has an OpenSpec-style lifecycle:

- **Top level** — reviews **in flight**: one directory per review, containing at
  least `brief.md`. Findings land beside the brief as the review runs.
- **`archive/<YYYY-MM-DD>-<name>/`** — reviews that are **complete and fully
  addressed** (every actionable finding fixed or consciously deferred with a
  recorded decision). Date = completion. Nothing here is a live TODO.

When a review's findings are all resolved, move its directory into `archive/` with
a completion-date prefix. Leaving only in-flight work at the top level keeps "what
still needs attention" obvious at a glance — the same reason OpenSpec archives
completed changes out of `changes/`.

Which reviews are open is what sits at the top level (`ls review/`); each one's
`brief.md` says what it covers. What a finished review found and decided is in its
`archive/<date>-<name>/SUMMARY.md`. Read that before re-reviewing an archived area, so the
budget is not spent re-litigating settled ground. Work a review leaves over is tracked
internally, not in this directory. `sweep/` is not a review: it holds the conventions for
the whole-codebase reading pass ([`sweep/README.md`](sweep/README.md)).

## Conventions every brief inherits

Briefs reference this section instead of repeating it.

- **Baseline first.** Capture a green baseline before hunting and record it (tests
  passed/skipped, coverage, `pyrefly`, `ty`, `ruff`). Briefs are the exception to
  the review skill's no-re-run default (`SKILL.md` §6) — no CI run to inherit. The
  `openspec` CLI comes from `scripts/setup-dev-env.sh`; to install it by hand, see
  `CONTRIBUTING.md` §OpenSpec changes.
- **Three dependency configs.** Behaviour changes by both presence and version of
  optional libs. Exact commands in `CONTRIBUTING.md` → "Before pushing": `[all]`,
  `[all-lowest]` (`--resolution lowest-direct`), and zero-dep `[core-only]`. Say
  which config a finding reproduces in.
- **VISION is the tie-breaker.** Rank findings against its two load-bearing claims:
  (1) safe by default (extraction cannot be zip-slipped, symlink-escaped or
  decompression-bombed unless the caller opts out), (2) memory-safe parsing of hostile
  input (no native-code parser attack surface). A finding that undercuts one of them
  outranks a same-severity one that doesn't. VISION's other priorities (one uniform
  interface, honest cost signals, damaged input, the perf bands, which it calls
  aspirational) still count, below those two.
- **Error contract** (`CONTRIBUTING.md`): raw library/`OSError`s crossing the
  boundary are translated to the `ArchiveyError` tree; unrecognized exceptions
  propagate raw (no catch-all); `ArchiveyUsageError` sits deliberately outside the
  tree.
- **Deliverable shape** (mirror the archived reviews): a `SUMMARY.md` (headline +
  top-findings table with severity/where/status), theme files, a `QUESTIONS.md` for
  maintainer decisions, and a "**what is actually fine**" section. Findings traced
  from code (`file:line`), behaviour-focused (a fix-worthy finding names the
  concrete input/state that triggers it), with a runnable repro where practical.
  **Pause and ask** rather than silently resolving a spec/design discrepancy
  (`CONTRIBUTING.md` §Working with the specs); a design question no spec covers goes
  through `dev-docs/design-rules.md` first.

## Provenance notes

- The only artifacts from a completed review are what got committed here — there are
  no chat transcripts to consult. Cite the archived `brief.md` / findings and the
  OpenSpec `design.md` files under `openspec/changes/archive/`.
- The security round's briefs recorded which earlier findings were already **closed**
  (#104 dedupe digests, #100 benchmark gate, #109 name safety, #82/#83 listing
  limits) and two conclusions a later refactor **overturned** (the "don't touch
  `SegmentedDecompressorStream`" verdict, collapsed in #96; the "7z parser is clean"
  verdict, restructured in #93). Future briefs should keep doing this — a re-review
  that resurfaces settled ground wastes budget.
