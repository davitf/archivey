# Brief — The public API at the `0.2.0` freeze

Read [`review/README.md`](../README.md) first: conventions, VISION tie-breakers, and the
deliverable shape. This brief inherits all of it and does not repeat it.

This is a **re-review**, not a first look. [`archive/2026-07-19-api-coherence/`](../archive/2026-07-19-api-coherence/)
judged the same surface two months ago and every one of its questions was decided and
implemented. Read that review before this one. Its `parity.md`, `surface.md` and
`QUESTIONS.md` are the baseline you are re-testing, not material to rediscover.

## Start condition

Runs against `main` at `b0fe664` (`#384`, the `extra` overloaded mapping) or later.
Confirm `MemberExtra` and `ArchiveInfoExtra` are in `src/archivey/types.py` before
starting — they are the newest thing on the surface and the freeze covers them.

## Constraint: this review is non-security, and must stay that way

The maintainer is spending Fable credit on this pass, and hostile-input work trips the
model's safeguards and downgrades it. **Do not frame findings as attacks, hostile
archives, malicious input, or exploitation.** The subject is design: does the surface
mean one thing, is it the right size to commit to, does a caller get the same answer
everywhere.

Resource limits, damage handling and detection confidence are all in scope **as API
shape** — whether `ListingLimits` and `ExtractionLimits` are coherent with each other,
whether `DetectionConfidence` is legible to a caller. They are out of scope as threat
analysis. If a finding can only be stated as "an attacker could…", it belongs in
`dev-docs/threat-model.md`, not here: note it in one line under a "handed off" heading
and move on.

## Why now

`0.2.0` is the first public release and it freezes every name and shape in
`src/archivey/__init__.py` for real users. The July review made that same argument. What
makes it worth making twice is what the surface did in between.

**The names turned over without the count moving.** `__all__` was 90 then and is 89 now,
which reads like stability and is not:

- **15 names left** — the 13 `Diagnostic*Context` classes,
  `RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE` and `WriteError`. That was the review's own
  recommendation (`surface.md` §Demote) and it was carried out.
- **14 names arrived**: `ARCHIVE_INTEGRITY_CODES`, `AbortOn`, `ArchiveInfoExtra`,
  `DeceptiveNameError`, `HashAlgorithm`, `IoStats`, `MemberExtra`, `MemberListReport`,
  `NameCollisionError`, `NameRewrittenError`, `OnDiagnostic`, `PasswordInput`,
  `crc32_digest`, `enable_measurement`.

Some of those 14 are the July review's own follow-ups landing (`HashAlgorithm`,
`crc32_digest`, `PasswordInput`, `OnDiagnostic`). The rest are new, and **no review has
looked at the surface as a whole since they arrived.**

The bodies moved further than the names. Against the July baseline `7139c13`, the nine
public modules are **+1562 / −293 lines**: `core.py` +513, `diagnostics.py` +388,
`types.py` +346, `config.py` +181, `exceptions.py` +147. A surface can keep its names and
change what they mean.

Three things make this the last comfortable moment:

1. After `0.2.0` every one of those names carries a compatibility cost.
2. The diagnostics and cost surfaces grew most, and they are the parts a caller *branches
   on*. A wrong shape there is a wrong shape in user code.
3. Surface changes are still in flight (`#386` adds an `extra` key for Windows reparse
   points). The review should say what the surface should be, and the in-flight work can
   land against that.

## Scope

The public package root and the modules it re-exports from:

- `__init__.py` — `__all__` (89 names) plus the 17 `# noqa: F401` imports that are
  importable but undocumented. Both halves are the surface.
- `detection_cost.py` and `terminal.py` — two top-level modules, **not** under
  `internal/` and not re-exported from `archivey`. See §A. (`terminal.py` replaced
  `escaping.py` in #448, which also added `detection.py`, the new home of `FormatInfo` and
  `DetectionConfidence`.)
- `core.py` — `open_archive` / `open_stream` / `extract`, detection entry points.
- `reader.py` — the `ArchiveReader` ABC, `MemberSelector` / `MemberFilter`.
- `types.py` — `ArchiveMember`, `ArchiveInfo`, the format enums, `MemberType`,
  `MemberStreams`, `MemberExtra` / `ArchiveInfoExtra`, `HashAlgorithm`.
- `config.py` — `ArchiveyConfig`, `ExtractionLimits`, `ListingLimits`, `AcceleratorMode`,
  the password types.
- `cost.py` — `CostReceipt`, `ListingCost`, `AccessCost`, `StreamCapability`.
- `diagnostics.py` — the diagnostic value types, `ARCHIVE_INTEGRITY_CODES`,
  `MemberListReport`, the extraction report types.
- `exceptions.py` — the `ArchiveyError` tree, and `ArchiveyUsageError` outside it.
- `measurement.py` — `enable_measurement`, `IoStats`.

Cross-reference the specs: `archive-reading`, `archive-data-model`,
`access-mode-and-cost`, `error-handling`, `diagnostics`, `format-detection`.

**Read for context, do not file against:** `src/archivey/cli/` and `docs/`. The CLI is
evidence about the library (see C below); a CLI defect is a separate report. A docs error
belongs to `review/docs-content/`, not here — unless the docs are right and the code is
wrong, which is a finding about the code.

## Do not re-raise

Each of these is decided and landed. Verified on `main` at `b0fe664` while writing this
brief; re-check rather than assume, but do not reopen the decision.

| Settled | Where it landed |
|---|---|
| Duplicate-name `is_current` — three formats, three behaviours (`parity.md` P1) | Fixed centrally: `_apply_last_entry_wins_is_current`, `base_reader.py:111`. Not per-backend any more. |
| The 13 `Diagnostic*Context` classes and `RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE` at top level (`surface.md` §Demote) | Demoted — importable, out of `__all__`. |
| `PasswordInput` and `OnDiagnostic` missing from exports | Both exported. |
| `member.hashes` typing | `Mapping[HashAlgorithm, bytes]`, `types.py:567`. |
| `ArchiveFormat` has no display name | `display_name` property, `types.py:146`. |
| `open_stream` missing from `docs/api.md` | Present. |
| A `verify` primitive (Q5) | Parked in `dev-docs/IDEAS.md`. Still parked. |
| Wrong-typed public arguments raising raw exceptions | Swept end to end in `#380` and `#382`; guarded by `tests/test_argument_boundary.py`, which reads the surface back through `inspect.signature` and fails for any public argument with no probe row. Argument *validation* is done. Argument *naming and shape* is not, and is yours. |
| `members=` callable-vs-collection precedence | Decided by the maintainer on `#376`: collection wins. Pinned by a test. |
| `PackageNotFoundError` / `version` leaking from the package root | They do not — `__init__.py:242` deletes them. |
| `__all__` re-exporting the `extra` bag as a plain `dict` | Superseded by `#384`'s overloaded mapping. |

Also read `dev-docs/open-issues.md` and `dev-docs/code-map.md` §"Where the answers live"
before deriving anything that feels like it should already be settled.

## What to evaluate, ranked by cost of getting it wrong at a freeze

### A. Do the 14 new names belong on the surface?

This is the headline question and the reason the review exists. For each, three things:

- **Is it API or implementation that leaked?** `IoStats` and `enable_measurement` came
  from the CLI's `--track-io` need — is the counter surface something a library user
  should depend on, or a debugging affordance that should live behind a narrower door?
  `MemberListReport` and `ARCHIVE_INTEGRITY_CODES`: does a caller construct these, branch
  on them, or only receive them?
- **Does it fit the vocabulary already there?** `AbortOn` next to `OnError` and
  `OnDiagnostic`; `DeceptiveNameError` / `NameCollisionError` / `NameRewrittenError` next
  to the rest of the tree. Three name-related exception types is either a precise
  taxonomy or a split that will read as arbitrary to someone who did not watch it happen.
  Say which.
- **Is it documented?** A name in `__all__` that `docs/api.md` never mentions is either an
  export gap or evidence the name is not really public.

Then the same question for the other half. **The surface is bigger than 89, and nothing
states its real size.** Counted on `main` at `b0fe664`:

| Where | Names | Documented? |
|---|---|---|
| `__all__` | 89 | Yes |
| Importable from `archivey`, not in `__all__` (the `# noqa: F401` block) | 17 | No |
| `archivey.detection_cost` — `DetectionBudget`, `DetectionCostReceipt`, `DetectionCapability`, `TierSkip`, `TierSkipReason`, `DetectionBudgetPreset`, `MutableDetectionCostReceipt`, the three budget presets, `default_detection_budget` | 11 | Not in the layout list |
| `archivey.escaping` — `display_path`, `escape_control_chars`, `quoted` | 3 | Not in the layout list |

*Updated for #448:* the `escaping` row is now `archivey.terminal`, the same three names,
documented in `docs/api.md` and in the layout list, with a stability promise and a
`packaging-and-extras` requirement. Recount
before relying on the total below, which was taken at `b0fe664`.

That is **120 importable names**, against a package docstring that describes eight modules
and an `__all__` of 89. `detection_cost` is the one to settle first: a caller reaching for
`FAST_BUDGET` or reading a `DetectionCostReceipt` is using API, and at `0.2.0` that
becomes a promise whether or not anyone decided to make it. Either fold it into the
documented surface or move it under `internal/` — but not after the tag.

`escaping` carried the smaller version of the same question, with history: the July
review's O7 residual parked "a public un-escape helper" as addable later. #448 settled it
(ruling on hub thread S25-K8 and on #448): the three helpers are the public
`archivey.terminal`, and the enum-spelling helpers stay internal.

Also in `terminal`: `os` is importable as `archivey.terminal.os`. Trivial, and the kind of
thing worth one line in "what is actually fine" if you judge it harmless.

### B. Do `diagnostics` and `cost` mean one thing?

These two grew most (+388, +45) and are what a caller branches on. The July review checked
that `CostReceipt` reported honest values per backend. Re-test the **semantics**, not the
honesty:

- Does `AccessCost` / `ListingCost` mean the same thing across ZIP, TAR, 7z, RAR, ISO,
  single-file and directory — same axes, same thresholds, comparable between formats?
- Is the diagnostic surface one mechanism or two? There are advisory codes, an integrity
  code set, per-member reports and an extraction report. A caller who wants "tell me
  what went wrong" should have one obvious way in.
- `StreamCapability` and `MemberStreams`: does a caller branching on the declared
  capability get the same answer shape from every backend?

`tests/test_corpus_sweep.py` asserts what the declarative corpus *declares* per (entry ×
format). It does not assert that these types mean the same thing across backends. Where
you find divergence, say whether a conformance assertion could pin it — that is the
durable fix, and its absence is why the July parity verdict could erode quietly.

### C. Surface size and the second-consumer evidence

89 documented names plus 18 undocumented is a lot to freeze. The July review asked "is
that the right size?" and shed 15; 14 came back. Ask it again, with an answer this time:
**which names does the "open, list, hash, extract" contract actually need?**

The CLI is still the best evidence available — it is the only real second consumer. Trace
every place `src/archivey/cli/` reaches past the public surface, imports from `internal/`,
or hand-rolls a helper the library should have offered. The July review found three such
gaps; two were fixed (`display_name`, the measurement counters), one was parked (the
`verify` primitive). Are there new ones?

### D. The error tree at the boundary

`exceptions.py` is +147 lines. The contract (`CONTRIBUTING.md`, ADR 0012) is that raw
library and `OSError`s are translated into the `ArchiveyError` tree, unrecognized
exceptions propagate raw, and `ArchiveyUsageError` sits deliberately outside the tree.
Argument validation is already swept and tested. What is not swept: whether the tree's
*shape* is right to freeze — three name-related error types, the granularity a caller
would `except` on, and whether every public entry point documents which branch it raises.

## Deliverable

The archived reviews' shape, per `review/README.md`:

- `SUMMARY.md` — headline plus a top-findings table (severity / where / status).
- Theme files matching A–D above.
- `QUESTIONS.md` — maintainer decisions, each stated as options with a recommendation.
  The maintainer decides; the brief does not.
- A "**what is actually fine**" section. Expect it to be large: the July review found the
  surface in better shape than the name count suggested, and everything it asked for was
  implemented. A re-review that reports the same health is a useful result, not a failed
  one.

Findings traced to `file:line`, behaviour-focused, with a runnable repro where one is
practical. Rank against the VISION claims, with claim 1 — one uniform interface — the
tie-breaker here. **Pause and ask** rather than silently resolving a spec/design
discrepancy.

Report findings; edit nothing. A fix goes through
`.claude/skills/address-review-findings/`.

## Definition of done

The review is complete when every name in `__all__` and every `# noqa: F401` import has a
verdict — keep, demote, rename, or remove — and every question a verdict depends on is in
`QUESTIONS.md` with a recommendation. It is *addressed*, and moves to `archive/`, when
those verdicts are implemented or consciously deferred with a recorded decision.
