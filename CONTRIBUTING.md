# Contributing to Archivey (v2)

Thanks for working on Archivey! This file is the **coding and testing standards**;
the *design* lives elsewhere and is authoritative:

- `openspec/specs/<capability>/spec.md` — the authoritative capability specs.
- `docs/` — the published end-user guide, and nothing else (see "Where does a new doc
  go?" at the end of this file).
- `dev-docs/` — unpublished maintainer material: `decisions/` (the ADR log),
  threat model / codec analysis / known issues, `investigations/` (finished
  evidence), `history/` (superseded SPEC/ARCHITECTURE/COMPARISON/ASYNC prose,
  not normative).
- `VISION.md` (repo root), `dev-docs/PLAN.md`, `dev-docs/IDEAS.md` — vision, roadmap, backlog.
- `openspec/changes/<change>/` — in-flight proposals (propose changes here, don't
  edit shipped specs ad hoc). Default schema is `library` (compact library-style
  deltas); see `openspec/schemas/library/README.md` and `openspec/config.yaml`.
- `AGENTS.md` — orientation for AI agents working in this repo (`CLAUDE.md` points at it).

### Archiving an OpenSpec change

**Merging a change does not apply it.** A proposal's deltas reach the authoritative specs
only when someone runs `openspec archive <change> --yes` and commits the resulting
`openspec/specs/` updates.

**Archive in the PR that finishes the change.** Most changes here are proposed,
implemented, and finished in one PR, and that PR is where the archive belongs — the
deltas are what make `openspec/specs/` describe what actually ships. Treating the archive
as a follow-up produced both halves of the problem it was meant to avoid: a window on
`main` where the authority was wrong (three times running — #212, #213, #214) and a
steady stream of PRs whose entire content was `openspec archive` (#214, #215, #222, #227,
#238). CI enforces this on pull requests and on `main`
(`scripts/check_openspec_archived.py`).

### Writing a delta requirement body

**Do not write "this change" in a requirement body or its scenarios.** It is unambiguous
while you are writing it and meaningless the moment `openspec archive` folds that body
into `openspec/specs/`: the change has moved to `openspec/changes/archive/` and a reader
of the authoritative spec cannot tell which one you meant. Ten such phrases had
accumulated across five capabilities before anyone counted, and most were not worth
naming even then — they were pre-merge arguments ("this change is a strict increase in
what is stamped", "it SHALL ship anyway because…") whose other referents, the prior
behaviour and the pre-change tree, had gone too.

State the contract as it stands. The reasoning behind it goes in the change's
`design.md`, which `openspec/config.yaml` rules.specs already asks for. Naming the change
id is the last resort, for a cross-reference a reader of the archived spec would follow.
`scripts/check_openspec_self_reference.py` enforces this over `openspec/specs/` and over
in-flight deltas; the two changes that predate it are grandfathered in its `PENDING` set,
and it fails if an entry there goes stale, so the list can only shrink.

Practically, make the archive the change's **last task**, so checking the final box and
applying the deltas are the same act. Most of the archived corpus is already written this
way.

**When the change genuinely is not finished, leave the trailing task unchecked.** That is
the escape hatch, and it costs one character. Use it when the design is still moving under
review (archiving early is what turns a review round into a revert-and-rework), or when an
archive is deliberately batched with a sibling change. The gate reads finished-ness from
the checkboxes: an unchecked box is an honest "not done yet", while a checked one is a
claim that the change has landed in the specs. Do not check the last box to make a
progress report look tidy.

## Getting started

Python **3.11+**. Tooling runs through [`uv`](https://docs.astral.sh/uv/):

```bash
uv sync                         # create/refresh the dev environment
./scripts/install-git-hooks.sh  # required: auto ruff fix+format on commit
```

On macOS, Homebrew no longer ships RARLAB `unrar`; `scripts/setup-dev-env.sh`
compiles the same pin CI uses (`scripts/install-rarlab-unrar.sh`).

Then two scripts cover the gates, split by how long they take and how often you run them:

```bash
./scripts/check.sh --fix   # seconds — every fast gate; --fix applies ruff first
./scripts/test.sh          # minutes — the everyday [all] test leg
```

Before asking for review, also run `uv run python scripts/review_prep.py`. It is not a CI
gate; it lists docs still naming what your branch removed or moved, fails on lines left
far wider than their file, and its `red-on-base` subcommand checks a "fails on `main`"
claim (`.claude/skills/address-review-findings/SKILL.md` §5).

`check.sh` mirrors CI's `lint`, `docs` and `openspec` jobs — `ruff check`,
`ruff format --check`, **`pyrefly`**, **`ty`**, `check_openspec_archived.py`,
`check_openspec_self_reference.py`, `openspec validate --all`, `check_docs_nav.py`, and
the strict docs build. It runs every
gate even after one fails and lists what failed at the end, so a single run tells you
everything that is wrong. Without `--fix` it writes nothing and answers "will CI pass?".

`test.sh` passes extra arguments through to pytest (`./scripts/test.sh tests/test_zip.py
-k roundtrip`), and `--all-configs` runs the full before-pushing gate described below.

Run the underlying commands directly when you want one in isolation:

```bash
uv run pytest                      # test suite
uv run ruff check                  # lint
uv run ruff format                 # format (apply; CI uses `ruff format --check`)
uv run pyrefly check               # type-check (Pyrefly)
uv run ty check                    # type-check (ty)
uv run pre-commit run --all-files  # optional: framework hooks over the whole tree
```

Pyrefly and ty are scoped to `src/`, so every command above runs clean with no extra
path arguments. **Both type checkers must stay green** — `ruff` passing is not the gate,
and running it alone is the most common self-inflicted CI failure here.

**Format before you commit.** CI runs `ruff format --check` (and `ruff check`) over
`src/ tests/ scripts/ benchmarks/` and will fail on drift. Installing the git hook
(`./scripts/install-git-hooks.sh`) makes this automatic: staged `*.py` under those
paths are `ruff check --fix`'d and `ruff format`'d on commit. If you skip the hook,
run `uv run ruff format` yourself before committing — `ruff format --check` only
reports problems; it does not rewrite files.

(`uv run pre-commit install` remains an alternative if you prefer the
`pre-commit` framework's own installer, but on Cursor Cloud it can land in the
remapped `core.hooksPath`; `./scripts/install-git-hooks.sh` is the supported path.)

RAR *data* tests need the system `unrar` binary, and encrypted-ZIP fixtures need `7z`
(`p7zip-full`). Without them those tests skip cleanly — which is the problem: the suite
still reports green while running ~109 fewer tests. Run `scripts/setup-dev-env.sh` to
provision both (it is idempotent, and prints anything still missing at the end); agent
environments run it automatically at session start.

**Before pushing a change whose behaviour could depend on which optional libraries are
installed, run the suite in all three dependency configurations CI runs** — optional
libraries change behaviour by their presence *and* their version, so a change that passes
one way can break another (a codec that's absent, a floor-version library bug, an
accelerator that's only installed at current versions):

```bash
./scripts/check.sh && ./scripts/test.sh --all-configs
```

Judge it by what the change can reach, not by its size. A change to a comment, to prose,
or to a test that only reads files off disk cannot vary by dependency configuration, and
`./scripts/check.sh && ./scripts/test.sh` is enough for it; CI runs the other two legs
regardless. Anything touching a codec, a backend, an import, or a version check gets all
three before it is pushed. When you skip the legs, say so and why in the pull request,
so a reviewer is not left guessing whether the gate was run or forgotten.

The three legs, which `--all-configs` runs in order:

```bash
# 1. Current versions, all extras — the everyday leg.
uv sync --group dev --extra all && uv run --no-sync pytest

# 2. Minimum supported versions — every declared dependency pinned to its floor
#    (`pycdlib 1.16`, `zstandard 0.23`, …), so version-specific library bugs in the
#    supported range surface. --no-sync keeps the lowest resolution for the test run.
uv sync --group dev --extra all --resolution lowest-direct && uv run --no-sync pytest

# 3. Zero-dependency core — no extras, no dev group; proves tests needing an optional
#    library skip/xfail cleanly (see the `requires` helper in tests/conftest.py) and the
#    core imports nothing third-party.
uv sync --no-dev && uv run --no-sync python tests/check_zero_dep_core.py \
  && uv run --no-sync --with pytest --with pytest-timeout --with pytest-cov pytest tests/ -q
```

These mirror CI's `[all]`, `[all-lowest]`, and `[core-only]` legs; all three must stay
green.

> **`--resolution lowest-direct` rewrites `uv.lock`.** Leg 2 does not merely install
> different versions — it *persists* them, so every later `uv sync --frozen` /
> `uv run --no-sync` silently keeps the downgraded set until you re-lock, and `git status`
> shows a few hundred lines of lockfile churn that is easy to commit by accident.
> **`./scripts/test.sh --all-configs` handles this for you** — it restores `uv.lock` and
> the everyday environment on exit, including on failure or Ctrl-C. Running the legs by
> hand, restore with:
>
> ```bash
> git checkout -- uv.lock && uv sync --frozen --group dev --extra all
> ```
>
> The **lint and type checkers are pinned exactly** (`ruff`, `pyrefly`, `ty` — see the
> comment in `pyproject.toml`) precisely so this cannot change what the gates report:
> before that, leg 2 resolved `ruff>=0.11.0` to 0.11.0, which flagged 367 "errors" on an
> unchanged tree. Everything else keeps a floor, because exercising the *runtime*
> libraries across their supported range is what leg 2 is for.

CI also matrixes supported **Python versions** (3.11–3.14 on Linux; 3.11/3.14 on
macOS/Windows). Repo `.python-version` pins the default local env to 3.11, so the
workflow must pass `--python <matrix>` (and `UV_PYTHON`) on every `uv sync` /
`uv run` in the test job — otherwise newer-version legs silently re-test 3.11.

## Cutting a release

See [`dev-docs/release-checklist.md`](dev-docs/release-checklist.md)
(CHANGELOG triage, perf vs previous tag, docs, three-config tests, version bump,
tag, publish). One-time repo rename / PyPI setup:
[`dev-docs/release-repo-cutover.md`](dev-docs/release-repo-cutover.md).
User-facing history lives in [`CHANGELOG.md`](CHANGELOG.md).

## Tooling decisions

- **Type-checking is Pyrefly + ty** — the library is kept clean on **both**. We do
  **not** use mypy or pyright. What gives *users* correct checks and IDE autocompletion
  is the typed public API plus the `py.typed` marker (PEP 561), independent of which
  checker CI runs; keeping two modern checkers green guards us against either one's
  blind spots. That guarantee holds only while the source also *parses* under the
  checkers CI does not run — a comment whose first token is `type:` is a type comment
  to mypy, and prose there makes the file unparseable in the consumer's own run.
  `tests/test_no_stray_type_comments.py` is what keeps that true.
- **Coverage is reported, never gated.** `pytest-cov` produces a report you can eyeball;
  there is no `fail_under` threshold. Aim for meaningful coverage through the tests
  below, not a number.
- **Zero-dependency core.** The core (incl. native 7z read + RAR metadata) imports no
  third-party packages. Everything else is an optional extra (see
  `openspec/specs/packaging-and-extras/spec.md`). Don't add a runtime dependency to the
  core.

## Coding standards

- **Keep it simple and well typed.** Prefer straightforward code over cleverness; type
  everything that's part of, or feeds, the public API.
- **Don't accumulate debt — clean as you go.** When you touch something, leave it in the
  shape it *should* have, not a quick patch bolted onto the old shape. If a change calls
  for a rename, a moved file, an updated doc/spec, or a small refactor to keep the design
  coherent, do it now as part of the change rather than deferring it — a deferred cleanup
  is debt the next person (often the next phase) inherits. Code and docs/specs are kept in
  sync: renaming a type or changing a contract means updating the prose docs and the
  `openspec/specs/` that describe it in the same change. The one exception is the
  pause-and-ask rule below: when a cleanup would resolve a genuine design discrepancy,
  surface it instead of silently picking a direction.
- **A pre-existing bug in the mechanism this change is already editing is fixed here,
  when the fix is proportionate.** Finding one mid-change is not a reason to open a ticket
  and move on: it lands in this PR, where the reviewer can see it against the code it
  belongs to. Being in a file the change touches is not enough on its own — the test is
  the mechanism under change, not the file. What does *not* land here is a
  **sweep**: the same mistake across files this change does not touch, or a rename that
  ripples through specs and archived changes. That is a follow-up, recorded in
  `review/backlog.md` or `dev-docs/IDEAS.md` with a reason. The line is whether you are
  still in the code under review, not whether the bug is old.
- **Leave the code self-explanatory.** The *resulting* tree — names, structure, and
  nearby comments — must make sense to a future editor who never saw the PR. They will
  read the current code, not the diff or the OpenSpec change / `design.md` / PR body
  that motivated it. Those docs are for approach and contract; they are not a
  substitute for local clarity. Reviews in this repo cold-read the changed code before
  loading design narrative and treat non-obvious logic that only makes sense after
  external prose as important documentation debt (see `code-review-skill`'s `reference/code-pr.md`).
- **Comments explain *why*, not *what*.** Match the comment density and style of the
  surrounding code. Don't narrate what the code obviously does; do explain non-obvious
  decisions, format quirks, and edge cases (these archives are full of them). For a
  complex decision, a comment **may point** at a spec, `dev-docs/decisions/`, architecture
  note, exploration, or OpenSpec change — but **summarize the reason inline whenever
  possible** so the pointer is optional depth, not the only explanation.
- **Write the comment so it has one reading.** Sentence shape for comments, like all
  prose here, follows [`AGENTS.md`](AGENTS.md) §Writing English. It decides how you
  write a comment; the two rules above decide whether to write one and what it says,
  and they win where the two ever pull apart.
- **Comments describe the code as it is, not how it got there.** A comment in `src/` is
  read by someone who never saw the change that produced it, so it must not depend on
  that change being remembered. Three things this rules out:
  - **History.** "Previously", "the old implementation", "this change", "we used to", a
    PR number, an OpenSpec change name, the name of the work batch a change belonged to
    (`Parcel B`, `Wave 1` — see `dev-docs/open-work-inventory.md`), or a correction of an
    argument nobody else can see. If the
    superseded approach is worth recording, it goes in `dev-docs/decisions/` or the
    format handbook, not in a comment next to the code that replaced it.
  - **Claims stronger than the code.** A comment that states a bound, a ratio, or an
    invariant asserts something a reader will rely on. Say what is actually guaranteed,
    or measure it — "roughly 1:1 with the header" where the proven bound is 4× is a
    defect, not a rounding.
  - **References to what you just removed.** After an edit, re-read the comments in the
    files you touched: a comment naming a deleted call site, or explaining a case the
    change made unreachable, is now wrong. This is the single most common finding in
    this repo's reviews, and it is cheapest to catch before pushing.
- **Match the surrounding code.** Naming, structure, and idiom should read like the file
  you're editing.
- **Type-checker suppressions must be justified, and are a last resort.** A bare
  `# type: ignore` that hides a *fixable* error is not allowed — it lets a real bug
  through and silently rots. Before suppressing, fix the type model (e.g. declaring the
  named `ArchiveFormat` instances as `ClassVar`s removed ~20 `# type: ignore`s *and* the
  errors they were masking). When a suppression is genuinely unavoidable (a checker bug,
  or a third-party stub gap), it MUST:
  - be **specific to the checker that errors**, in that checker's native form:
    `# pyrefly: ignore[<code>]` or `# ty: ignore[<code>]`. Those bracketed codes
    are validated — a wrong or invented code restores the error. `# type: ignore[attr-defined]`
    is **not** specific here: Pyrefly does not check the bracketed code, so that form
    silences every future error on the line. Never a bare `# type: ignore`. Use only
    the directive of the checker that actually reports the error (they disagree).
  - carry an **inline reason** on the same line or just above, saying *why* it's needed
    and ideally linking the upstream issue.

  An unjustified or non-specific suppression should be treated as a review blocker. The
  library is kept clean on **both** Pyrefly and ty precisely so neither checker's blind
  spot can hide an error the other would catch — don't defeat that with a suppression.
- **Every resource bound is reachable from the public config.** Guards against hostile
  input belong in `ListingLimits` / `ExtractionLimits` on `ArchiveyConfig`, where a user
  who legitimately needs a bigger archive can raise them — including to `UNLIMITED`. Do
  **not** add a second ceiling as a module constant inside a parser or reader: it is
  invisible from the API, it cannot be lifted, and it turns a real archive into an error
  the caller has no way to accept. If a bound genuinely cannot be expressed in the config
  (it is structural, not a policy), say why in a comment at the constant and treat it as
  a contract change: it needs a spec row, not a quiet `_MAX_…`.
- **Never silently drop or clamp data.** Truncating an over-long read, clamping a seek
  past a boundary, or discarding a consumed count desynchronizes the stream for every
  later caller and turns a detectable error into wrong bytes. Raise, with a message that
  says which invariant was violated. Where `streamtools` cannot raise an `ArchiveyError`,
  `ValueError` is the right type (maintainer decision on #326).
- **Subprocess calls pass an argument list.** `unrar`, the fixture `7z`, and anything
  else spawned from this codebase take a list of arguments, never a single string with
  `shell=True`. Archive member names are attacker-controlled: a name is a value passed to
  the process, never text interpolated into a command line.
- **Secrets stay out of logs, `repr` and exception messages.** A password or key is
  handled, never displayed. An exception raised on a wrong password says that the password
  was wrong, not what was tried — the message ends up in a caller's log.
- **The public / `internal/` boundary is a contract, not a layout.** Code outside
  `internal/` is frozen surface; `__all__` is grown deliberately, one export at a time,
  with the reason in the PR. The CLI reaching into `internal/` is a signal that the public
  API has a gap — fix the gap rather than widening the reach.
- **Cost signals stay honest, and nothing silently re-decompresses.** `ListingCost` and
  `AccessCost` are promises a caller plans against, so a change that makes a path more
  expensive updates them. Reading two members out of one solid block must not decode the
  block twice without the cost signal saying so: a tiny fixture hides it, and the caller
  pays in production. Claims about speed cite bytes decompressed and seeks, or an existing
  `benchmarks/` run — not wall time on one machine.
- **Picking an exception type means checking what catches it.** In this codebase an
  exception type is control flow: `TruncatedError` from the wrong layer aborts
  password-candidate iteration, and a `CorruptionError` where a wrong-password error was
  expected ends the same iteration early. Before choosing or changing a type, grep for
  what catches it upstream and say in the PR what you found.
- **Exception translation is specific.** All errors caused by archive problems must
  surface as `ArchiveyError` subclasses, via each reader's per-library translator:
  - Map *known* third-party exceptions to the right `ArchiveyError`
    (`CorruptionError`, `TruncatedError`, `EncryptionError`, …).
  - **Never** add a catch-all that converts *any* `Exception` — that hides bugs. If an
    exception is unrecognized, let it propagate (return `None` from the translator) so we
    learn about it and can map it deliberately.
  - Genuine `OSError` / `KeyboardInterrupt` / `MemoryError` propagate unchanged, except
    where a spec says otherwise (e.g. safe-extraction catches a per-member filesystem
    `OSError` under `OnError.CONTINUE` — see `openspec/specs/safe-extraction/spec.md`).

## Testing standards

- **Test behaviour, not internal implementation.** Assert on what a public API returns
  and does, so refactors don't break tests gratuitously. *Narrow exception:* the
  low-level building blocks — stream primitives/helpers, format parsers, the codec
  layer — should also get focused **unit** tests of their internals, because they're
  shared foundations and their corner cases are exactly what break formats downstream.
- **Leaked OS resources fail the test.** `tests/leak_oracle.py` is an autouse oracle
  (disable with `ARCHIVEY_LEAK_ORACLE=0`) that fails a test which leaves a child
  process running or an owning stream unclosed (`owns_inner=True` / a
  `DelegatingStream` whose `_subclass_closes_inner` resolved True via the
  class flag or the kwarg). Extra pipe fds are annotated on those failures, not
  a standalone fail (`os.pipe()` helpers close by GC). It pins those objects so
  `IOBase.__del__` cannot reap them between the test return and teardown — that is
  the gap that let a missing `owns_inner=True` on a RAR glob-mask pipe ship with a
  green suite. An owning stream must be closed in the test that constructed
  it — a later test's close still fails the constructor. Module-scoped owning
  streams are invisible (pins reset at each test). `unrar`/`7z` leaks are
  invisible under `[core-only]` (those tests
  skip); the oracle still runs, and `tests/test_leak_oracle.py` spawns
  `sys.executable` so the gate is exercised in every config. Teardown reaps
  leaked children via `Popen.terminate` — `os.WNOHANG` does not exist on
  Windows, and using it hid the leak report behind an `AttributeError`.
  `@pytest.mark.allow_resource_leaks` skips the fail, not the reap.
- **Hit the corner cases.** Especially corrupt, truncated, and encrypted archives;
  wrong passwords; empty/zero-length members; unusual names and metadata; non-seekable
  sources. When porting or writing a reader, deliberately trigger each error path so the
  exception translator is exercised.
- **Use the declarative corpus.** Tests are driven by API-agnostic archive specs +
  expected data (generated on demand and cached); cross-validate against the `py7zr` /
  `rarfile` / frozen-DEV oracles where applicable (see
  `openspec/specs/testing-contract/spec.md`).
- **Fixing a bug? Red–green TDD.** First write a test that **reproduces** the bug and
  **fails**; then make it pass with the fix. The failing test is the proof the bug
  existed and that you fixed *that* bug.
- **A guard test must be shown to fail.** Red–green is not only for bug fixes. A new
  property test, inventory test, static guard, or assertion added to defend an invariant
  is done only once you have broken the thing it names and watched it fail. Write down in
  the PR which mutation you applied. A test that passes against the unfixed or
  un-guarded code is worse than no test, because it reports coverage that does not
  exist — this repo has shipped a property test that passed a `return block_start`
  mutant, an inventory test that passed vacuously, and a test whose fixture could not
  reach the path it named. For a test the PR says fails on `main`,
  `uv run python scripts/review_prep.py red-on-base <test ids>` runs it against the
  merge base's `src/` and prints a table for the PR body.

### Coverage-guided fuzz (Atheris)

Atheris lives in the PEP 735 `fuzz` dependency group (`atheris`) and runs via
`.github/workflows/atheris-fuzz.yml` — same shape as the benchmark wall split:

- **Every PR:** short partitioned budgets over all targets (blocks the PR; sharded
  across parallel jobs because each target pays a large Atheris cold-start).
- **Nightly schedule:** full partition, but only if default-branch HEAD moved in the
  last ~3 days (commit-recency guard; dormant stretches skip the expensive run).
- **`workflow_dispatch`:** force the full partition (optional `budget_scale`).

Mutation fuzz (`tests/test_mutation_fuzz.py`) and `ARCHIVEY_FUZZ=1` /
`tests/fuzz_sevenzip_parser.py` / `tests/fuzz_rar_parser.py` stay as they are.

Local smoke (Linux; needs corpus fixture builders). Prefer Python 3.12 for current
Atheris wheels; on 3.11 ``uv`` resolves ``atheris`` 3.0.x::

    uv sync --group fuzz --group dev --extra all
    uv run --no-sync python -m tests.atheris_fuzz --smoke

    # or explicitly:
    uv sync --python 3.12 --group fuzz --group dev --extra all
    uv run --python 3.12 --no-sync python -m tests.atheris_fuzz --smoke

Deepen one target (budget seconds via env, e.g. `ARCHIVEY_FUZZ_BUDGET_SEVENZIP_HEADER=60`,
`ARCHIVEY_FUZZ_BUDGET_ZIP=60`, or `ARCHIVEY_FUZZ_BUDGET_UNIX_COMPRESS=60`)::

    uv run --no-sync python -m tests.atheris_fuzz --target sevenzip_header
    uv run --no-sync python -m tests.atheris_fuzz --target zip
    uv run --no-sync python -m tests.atheris_fuzz --target unix_compress
On a crash the harness writes the input under `artifacts/atheris/` and prints a one-line
repro command.

## Working with the specs (please read)

When you hit a **discrepancy** — specs disagreeing with the prose docs, the specs
disagreeing with each other, or the design simply not covering your case — **pause and
ask the maintainer** rather than silently picking an interpretation. A conflict usually
means a decision hasn't been made yet, and guessing bakes the wrong one into the code.
Surface it (an issue, a PR comment, or an `openspec/changes/` proposal) and let it be
decided explicitly.

**Thin as you go.** Specs stay the authoritative *machine* contract for now (pair-workflow
DP1 = C), but we are migrating executable detail into **tests** and human truth into
**handbook** pages — see
[`dev-docs/discussions/2026-09-specs-to-handbook-and-tests.md`](dev-docs/discussions/2026-09-specs-to-handbook-and-tests.md).
On every PR that touches `openspec/specs/` or a change delta:

- Prefer a **pytest** (and a handbook / Verify link) over a new WHEN/THEN scenario.
- When you touch a requirement that is already covered by tests, **collapse or delete**
  redundant scenarios rather than adding siblings “for completeness”.
- Leave that capability **no denser than you found it** (preferably thinner).
- Keep in the spec only what tests cannot say well (signatures, non-goals, MUST refuse,
  packaging / threat posture).

## Where does a new doc go?

Five questions, in order. The first `yes` wins.

1. **Would someone who only *uses* the library need it?** → `docs/`, **and add it to
   `mkdocs.yml`'s nav in the same commit**. Curated "why we chose X" one-liners for
   curious users belong inline on the page that raises the question, not as a new
   page per decision. Use `/technical-writing` for structure and craft. The standing
   prose rules that apply to the result are [`AGENTS.md`](AGENTS.md) §Writing English.
2. **Is it current maintainer truth about a format or cross-cutting topic?** → a
   living handbook page `dev-docs/formats/<format>.md` or `dev-docs/topics/<topic>.md`
   (rewrite in place; light decision bullets, not a new ADR). **Create the file in the
   same PR that needs it** — do not add empty `formats/` / `topics/` trees. Format pages
   follow the eight-section shape in
   [`dev-docs/pair-workflow.md`](dev-docs/pair-workflow.md) §Format page structure, with
   [`dev-docs/formats/zip.md`](dev-docs/formats/zip.md) as the worked example. Everyday
   loop: same doc.
3. **Is it rare repo-wide policy that will not fit a handbook page?** → a new ADR in
   `dev-docs/decisions/`, ADR-shaped (Context / Decision / Consequences, tens of
   lines). If it needs an `## Open questions` section, it is not an ADR yet — grill
   first (`/grill-with-handbook`).
4. **Does a contributor need a live register or runbook *today*?** → `dev-docs/`
   (e.g. threat model, known issues, release checklist).
5. **Is it finished evidence — an investigation, a superseded design, a lab
   notebook?** → `dev-docs/investigations/`, or `dev-docs/history/` for prose that a
   newer document replaced.

If it is a *review*, it belongs to the `review/` lifecycle. If it is a *proposed
behaviour / contract change*, it belongs to `openspec/changes/` so the authoritative
main specs stay in sync (prefer `--schema minimalist` when proposal/design would only
be agent bus). Scenario-row widenings that do not change a requirement's SHALL
(extra producer names, the same error type) may edit `openspec/specs/` directly
when the maintainer says so — do not open a change folder just to add a matrix
row. Human conclusions still land on handbook pages — specs are the binding
contract, not the primary reading surface
([`dev-docs/pair-workflow.md`](dev-docs/pair-workflow.md)).

**Same PR as code:** when a change falsifies a handbook or published-doc claim, update
that page in the same PR.

**The invariant:** everything under `docs/` is published and is for users; nothing
else lives under `docs/`. That is why maintainer material sits in `dev-docs/` rather
than under `docs/` behind an exclusion list — an exclusion list needs a second list
to keep in sync with the first, and drift between the two is what left six pages
published, URL-reachable and absent from every menu.

`scripts/check_docs_nav.py` enforces it, along with the rule that a published page
must not link into unpublished material: prefer inlining the fact, and where the
depth is genuinely worth keeping, link the file on GitHub with an absolute
`https://github.com/davitf/archivey/blob/main/…` URL.
