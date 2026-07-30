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

## In flight

| Dir | Review | Status |
|-----|--------|--------|
| `docs/` | Documentation full review — audience separation + information architecture | Brief 2026-07-29; **phase 1 (audit) delivered** — `SUMMARY.md` + `inventory.md` + `QUESTIONS.md`. Awaiting phase-2 decisions (Q1 is blocking: it conflicts with `openspec/specs/documentation`). |

The round commissioned 2026-07-17 — the **non-security** pass toward the first public
`0.2.0` — closed on 2026-07-28 when `debt-ledger/` and `performance/` were archived.
Every finding there is fixed, accepted, or recorded as an explicit deferral.

`backlog.md` holds what is left: Topic 6 (decode-engine perf, + parked stream-layering
Q4), Topic 7 (outside-in adoption capstone — the **capstone**, meaningful only once
everything else is addressed), and the parked-from-archive table.

Note that an empty `review/` does **not** mean `0.2.0` is ready: the release bundle
(`PLAN.md` item 6 — packaging finalize, the free-threading support statement, the
migration guide) was never a review item and is still outstanding.

## Archive (complete & addressed)

| Dir | Review | Outcome |
|-----|--------|---------|
| `archive/2026-07-12-codebase-deep-review/` | First full-tree review (PR #73/#75) | Findings 1–10 fixed or moved to roadmap; several later closed by #104/#100/#109/#82. `deep-simplification` S1/S2/S3 **deferred** — see `backlog.md`. |
| `archive/2026-07-16-rar-reader/` | Native RAR hostile-input & correctness (PR #113) | F1–F6 fixed with tests. |
| `archive/2026-07-16-crypto/` | Native decryption/KDF/verification correctness (PR #115) | F1–F5 fixed in #127. |
| `archive/2026-07-16-stream-decoder/` | Post-#96 decoder layer, accelerators, vendored LZW (PR #122) | F1–F6 fixed in #128. |
| `archive/2026-07-17-cli/` | CLI design + implementation (PR #131 → #120) | F1–F12 + D1–D8 addressed in #120; R1–R4 and hoist H1–H3 / D4 logging follow-ups fixed before merge. |
| `archive/2026-07-19-stream-layering/` | Stream wrapper stack correctness + verify fusion | F1/F2/D1/D2 done (#137); Q4 parked → Topic 6. |
| `archive/2026-07-19-api-coherence/` | Public API & member-model coherence | Q1–Q7 decided + implemented (#153–#157); digest fill done (#160); Q5 → `IDEAS.md`; D1 → `cli-product/`. |
| `archive/2026-07-20-cli-product/` | The CLI as a **product** (UX / grammar / exit codes / output) | P1–P3/P5–P14/D1 done (#144 + #163/#165); **P4/`--json`** → `IDEAS.md` / DD7; **Q4/`--raw`** → DD8. |
| `archive/2026-07-28-debt-ledger/` | The pre-`0.2.0` **debt ledger** (backlog Topics 4+5) | Whole pay list paid: D1–D7, DD1/DD4, S2/S3, T1/T2/T3/T7, T4 half-test, Q1–Q5. Remaining items are explicit KEEPs. T7 audit: [`corpus-matrix.md`](archive/2026-07-28-debt-ledger/corpus-matrix.md). |
| `archive/2026-07-28-performance/` | The ≤1.3× stdlib perf budget — gate efficacy + traps | P1/P3/P4/P5/P6 fixed; P2/P7 partial with bands **accepted aspirational** (#191); Q1–Q6 all decided (Q4 → no verify-skip knob, 2026-07-28); P8/P9 + L4/L5 → tracked follow-ups. |

## Conventions every brief inherits

Briefs reference this section instead of repeating it.

- **Baseline first.** Capture a green baseline before hunting and record it (tests
  passed/skipped, coverage, `pyrefly`, `ty`, `ruff`). The `openspec` CLI is not
  preinstalled: `npm install -g @fission-ai/openspec` (see `CLAUDE.md`).
- **Three dependency configs.** Behaviour changes by both presence and version of
  optional libs. Exact commands in `CONTRIBUTING.md` → "Before pushing": `[all]`,
  `[all-lowest]` (`--resolution lowest-direct`), and zero-dep `[core-only]`. Say
  which config a finding reproduces in.
- **VISION is the tie-breaker.** Rank findings against the load-bearing claims:
  (1) one uniform interface + honest cost signals, (2) parse untrusted archives
  without native-code parser attack surface, (3) damaged input is a first-class
  citizen (recoverable members + an honest error), (4) the ≤1.3× stdlib perf
  budget. A finding that undercuts a marketing claim outranks a same-severity one
  that doesn't.
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
  (`CLAUDE.md`).

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
