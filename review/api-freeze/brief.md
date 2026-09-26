# Brief — The public API at the `0.2.0` freeze

Read [`review/README.md`](../README.md) first: conventions, VISION tie-breakers, and the
deliverable shape. This brief inherits all of it and does not repeat it.

This is a **re-review**, not a first look. [`archive/2026-07-19-api-coherence/`](../archive/2026-07-19-api-coherence/)
judged the same surface two months ago and every one of its questions was decided and
implemented. Read that review before this one. Its `parity.md`, `surface.md` and
`QUESTIONS.md` are the baseline you are re-testing, not material to rediscover.

## Start condition

Runs against `main` at `c599fc5` (`#459`, bounded password confirmation) or later, and
**after** the three surface-changing PRs open when this brief was refreshed have merged
or been dropped:

- the rest of the detection series (`#468` and whatever follows it; `detect_format` lost
  its `budget=` argument in `#466`, and the budget is expected to reappear as an
  `ArchiveyConfig` field),
- `#470` (`created` never holds `st_ctime`; a new `ArchiveMember.ctime` field beside it,
  `rar.created_is_ctime` removed),
- `#465` (release docs: `how-it-works.md`, the full exception tree on `docs/api.md`, and a
  test that fails when a name in `__all__` has no entry there).

Confirm before starting: `ArchiveReader.format_info` exists (from `#468`),
`ArchiveMember.ctime` is in `src/archivey/types.py` (from `#470`), and
`tests/test_public_api.py::test_every_public_name_is_on_the_api_page` exists (from
`#465`). If any is missing, that PR has not landed; the review would be filing against a
surface that is about to move.

This brief was first written 2026-09-21 at `b0fe664` and refreshed 2026-09-25 at
`c599fc5`. Every figure below is from the refresh unless it says otherwise.

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

**The names turned over without the count moving.** `__all__` was 90 in July and is 90
now, which reads like stability and is not:

- **15 names left** — the 13 `Diagnostic*Context` classes,
  `RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE` and `WriteError`. That was the review's own
  recommendation (`surface.md` §Demote) and it was carried out.
- **15 names arrived**: `ARCHIVE_INTEGRITY_CODES`, `AbortOn`, `ArchiveInfoExtra`,
  `DeceptiveNameError`, `DecoderLimits`, `HashAlgorithm`, `IoStats`, `MemberExtra`,
  `MemberListReport`, `NameCollisionError`, `NameRewrittenError`, `OnDiagnostic`,
  `PasswordInput`, `crc32_digest`, `enable_measurement`. (`DecoderLimits` is the one
  added since the first draft of this brief, in `#398`.)

Some of those 15 are the July review's own follow-ups landing (`HashAlgorithm`,
`crc32_digest`, `PasswordInput`, `OnDiagnostic`). The rest are new, and **no review has
looked at the surface as a whole since they arrived.**

The bodies moved further than the names. Against the July baseline `7139c13`, the public
modules are **+3271 / −403 lines** at `c599fc5` (they were +1562 / −293 four days
earlier, when this brief was first drafted): `types.py` +793, `core.py` and `config.py`
each in the hundreds, `diagnostics.py`, `exceptions.py` and two whole new modules
(`detection.py`, `terminal.py`). A surface can keep its names and change what they mean.

Three things make this the last comfortable moment:

1. After `0.2.0` every one of those names carries a compatibility cost.
2. The diagnostics and cost surfaces grew most, and they are the parts a caller *branches
   on*. A wrong shape there is a wrong shape in user code. **The maintainer has said he
   does not fully understand how diagnostics work or how they should be used.** That is
   already a finding: if the author cannot explain it, users will not be able to either.
   See §0.
3. The surface changes that were in flight when this brief was first written have now
   landed or are about to (see §Start condition). Once they have, nothing else is
   scheduled to move a public name before the tag, so this review's verdicts can be
   implemented directly against the surface they describe.

## Scope

The public package root and the modules it re-exports from:

- `__init__.py` — `__all__` (90 names) plus the 19 `# noqa: F401` imports that are
  importable but undocumented (17 `Diagnostic*Context` payloads,
  `RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE`, `WriteError`). Both halves are the surface.
- `detection_cost.py` and `terminal.py` — two top-level modules, **not** under
  `internal/` and not re-exported from `archivey`. `terminal` is documented on
  `docs/api.md` with a stability promise (`#448`); `detection_cost` is named in the
  package docstring as "public, not re-exported" and documented nowhere else. See §A.
- `detection.py` — `FormatInfo` and `DetectionConfidence` (moved here in `#448`;
  `FormatInfo.encoding_hint` was removed in `#467`).
- `core.py` — `open_archive` / `open_stream` / `extract`, detection entry points.
- `reader.py` — the `ArchiveReader` ABC, `MemberSelector` / `MemberFilter`, and (after
  `#468`) `format_info`.
- `types.py` — `ArchiveMember`, `ArchiveInfo`, the format enums, `MemberType`,
  `MemberStreams`, `MemberExtra` / `ArchiveInfoExtra` and every `EXTRA_*` key constant,
  `HashAlgorithm`, `FormatAvailability` / `FormatSupport` / `MissingComponent`.
- `config.py` — `ArchiveyConfig`, `ExtractionLimits`, `ListingLimits`, `DecoderLimits`,
  `AcceleratorMode`, the password types, and the detection budget once it lands there.
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

Each of these is decided and landed. Verified on `main` at `c599fc5` while refreshing
this brief; re-check rather than assume, but do not reopen the decision.

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
| Whether `extra` should be a `TypedDict` (PEP 728) | Decided: an overloaded `dict[str, object]` subclass whose known keys carry types (`#384`, `#421`). Not reopened. |
| The three `escaping` helpers: public or internal? | Public as `archivey.terminal` (`#448`, hub thread S25-K8). The enum-spelling helpers stay internal. |
| `FormatInfo.encoding_hint` | Removed (`#467`). |
| `extract_all(config=)`, `strict_archive_eof` | Dropped and removed respectively (`#415`). |
| A detection evidence ledger on `FormatInfo` | Decided against 2026-09-25 after a design review; small fixes in `#466`/`#468` instead. |
| Decoder memory caps as a public type | `DecoderLimits` on `ArchiveyConfig`, `ResourceLimitError` on breach (`#398`, `#413`, `#434`). The *shape* is yours to judge next to `ListingLimits` / `ExtractionLimits`; the existence is settled. |
| `created` holding `st_ctime` | Never, in any format; the time goes to `ArchiveMember.ctime`, one cross-format field rather than per-format `extra` keys (`#470`, the maintainer's ruling). A member has at most one of the two. |
| 29 names in `__all__` with no entry on `docs/api.md` (21 of them exceptions) | Fixed in `#465`, which also adds a test that keeps it fixed. Do not file the gap; do file any name whose new entry reads as though it should not be public. |

Also read `dev-docs/open-issues.md` and `dev-docs/code-map.md` §"Where the answers live"
before deriving anything that feels like it should already be settled.

## What to evaluate, ranked by cost of getting it wrong at a freeze

### 0. Diagnostics: can a new user tell when to look, and what to do?

This comes first because the maintainer asked for it first. He wrote, on 2026-09-25:
"I'm particularly worried about the diagnostics part, I still don't fully understand how
it works and how it should be used." Treat that as the review's opening finding and
answer one question before anything else: **reading only `docs/` (the guide pages and
`api.md`), can a new user tell (a) when something will be a diagnostic rather than an
exception, (b) where to look for it, and (c) what to do about it?**

Do it the way a new user would. Start from `docs/index.md`, follow the links, and write
down each point where you had to open `src/` or an OpenSpec spec to answer one of the
three questions. Each such point is a finding. Then check the mechanism against what the
docs say. The parts to hold up next to each other:

- **The channels.** `reader.diagnostics` (a `DiagnosticSummary`),
  `ExtractionReport.diagnostics` (extraction-only, or the whole open when it came from
  `archivey.extract()`; `core.py` around line 857 explains the asymmetry),
  `MemberListReport`, `ExtractionReport.results`, `ArchiveyConfig.on_diagnostic`, and
  the logger. `docs/errors-and-diagnostics.md` says a fact "has exactly one authoritative
  channel". Is that true, and can a reader tell which channel a given fact is on without
  reading the code?
- **The policy.** `DiagnosticPolicy` with `strict()`, `pedantic()`, `overrides=`,
  `default=`, `ARCHIVE_INTEGRITY_CODES`, and `DiagnosticRaisedError`. Is the disposition
  vocabulary (`IGNORE` / `COLLECT` / `RAISE`) and the severity vocabulary two axes a user
  needs, or one axis spelled twice?
- **The retention knob.** `max_retained_diagnostic_references` on `ArchiveyConfig`. Is
  it something a user should have to know about, and does the docstring say what happens
  past the cap?
- **The codes.** `DiagnosticCode` has grown to about thirty values. The guide documents
  nine in a table and names the rest in passing. Is the table the right nine, and is
  there a place a user can see the whole list with one line each?
- **The 17 context payloads.** Importable, out of `__all__`, and reachable only through
  `Diagnostic.context`. Say whether a user matching on `context` needs them in `__all__`,
  or whether `to_dict()` is the intended path and the classes can stay demoted.
- **The CLI as the second consumer.** `src/archivey/cli/` touches diagnostics in only a
  handful of places. What does it do with them, and is that what the docs would lead a
  library user to do?

The deliverable for this section is a one-page "how diagnostics work and how to use
them" explanation, written for the maintainer, plus the list of places the docs fall
short of it. If the explanation cannot be written in a page, that is the finding.

### A. Do the 15 new names belong on the surface?

This is the headline question and the reason the review exists. For each, three things:

- **Is it API or implementation that leaked?** `IoStats` and `enable_measurement` came
  from the CLI's `--track-io` need (`#465` gives them a guide section, so read that too) — is the counter surface something a library user
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

Then the same question for the other half. **The surface is bigger than 90, and nothing
states its real size.** Counted on `main` at `c599fc5`:

| Where | Names | Documented? |
|---|---|---|
| `__all__` | 90 | Yes, once `#465` lands (29 had no `docs/api.md` entry before it) |
| Importable from `archivey`, not in `__all__` (the `# noqa: F401` block) | 19 | No |
| `archivey.detection_cost` — `DetectionBudget`, `DetectionCostReceipt`, `DetectionCapability`, `TierSkip`, `TierSkipReason`, `DetectionBudgetPreset`, `DetectionBudgetPresetStr`, `MutableDetectionCostReceipt`, the three budget presets, `default_detection_budget` | 12 | Named in the package docstring only; nothing on `docs/api.md` |
| `archivey.terminal` — `display_path`, `escape_control_chars`, `quoted` | 3 | Yes (`#448`) |

That is **124 importable names**, against an `__all__` of 90. `detection_cost` is the one
to settle first: a caller reaching for `FAST_BUDGET` or reading a `DetectionCostReceipt`
is using API, and at `0.2.0` that becomes a promise whether or not anyone decided to
make it. `detect_format` no longer takes a budget at all (`#466`), and the budget is
expected to move onto `ArchiveyConfig`; once it does, ask whether a caller who never
imports `detection_cost` can still do everything the docs describe. If yes, the module
can go under `internal/`. If no, it needs a `docs/api.md` section like `terminal` has.
Either way, not after the tag.

`terminal` carried the smaller version of the same question and is settled (see §Do not
re-raise). Also in `terminal`: `os` and `PurePath` are importable as
`archivey.terminal.os`. Trivial, and the kind of thing worth one line in "what is
actually fine" if you judge it harmless.

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

90 documented names plus 19 undocumented, plus two side modules, is a lot to freeze. The
July review asked "is that the right size?" and shed 15; 15 came back. Ask it again, with an answer this time:
**which names does the "open, list, hash, extract" contract actually need?**

The CLI is still the best evidence available — it is the only real second consumer. Trace
every place `src/archivey/cli/` reaches past the public surface, imports from `internal/`,
or hand-rolls a helper the library should have offered. The July review found three such
gaps; two were fixed (`display_name`, the measurement counters), one was parked (the
`verify` primitive). `#448` then made "the CLI uses only public API" a rule, so the
expected answer is none; a new gap is a finding about the rule as much as the code.

### D. The error tree at the boundary

`exceptions.py` is about +200 lines against July. The contract (`CONTRIBUTING.md`, ADR 0012) is that raw
library and `OSError`s are translated into the `ArchiveyError` tree, unrecognized
exceptions propagate raw, and `ArchiveyUsageError` sits deliberately outside the tree.
Argument validation is already swept and tested. What is not swept: whether the tree's
*shape* is right to freeze — three name-related error types, the granularity a caller
would `except` on, and whether every public entry point documents which branch it raises. `#465` adds a
"what is translated, and what passes through" section to the guide and puts the whole
tree on `docs/api.md`; read the tree as it appears there, since that is how a user will
first meet it.

## Deliverable

The archived reviews' shape, per `review/README.md`:

- `SUMMARY.md` — headline plus a top-findings table (severity / where / status).
- Theme files matching 0–D above. `0-diagnostics.md` carries the one-page explanation.
- `QUESTIONS.md` — maintainer decisions, each stated as options with a recommendation.
  The maintainer decides; the brief does not.
- Every finding stated in plain language with a recommendation, since the maintainer
  reads the report directly and decides from it. Options first, recommendation marked.
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
