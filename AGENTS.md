# Archivey v2 — Agent Guide

**This file is canonical for agents.** `CLAUDE.md` is a pointer to it plus a few
Claude Code–specific environment notes.

This repo (`archivey`) is the clean-slate **v2** of the Archivey archive
library: read, stream, and safely extract ZIP / TAR / RAR / 7z / ISO / directory
/ single-file-compressed archives behind one uniform interface. The previous
v1 tree is [`davitf/archivey-old`](https://github.com/davitf/archivey-old).

It is a **pure Python library** — no server and no web UI. It does ship a CLI
(`archivey list|test|extract|info`, `openspec/specs/cli/spec.md`), but "running the
application" normally means exercising the library API:
`archivey.open_archive(path)` / `archivey.extract(path, dest)` plus the detection
helpers (`detect_format`, `format_availability`, `list_supported_formats`).
All backends ship: ZIP, TAR, **7z**, **RAR**, ISO, directory, and
single-file-compressed (gz/bz2/xz/lzip/zstd/lz4/.Z).

## Where things live

**Everyday maintainer/agent loop:** [`dev-docs/pair-workflow.md`](dev-docs/pair-workflow.md)
(investigate → grill into handbook → thin brief → implement → other-agent review →
decision packets). **Start with [`dev-docs/code-map.md`](dev-docs/code-map.md)** when the
question is *where in the source do I make this change*. Format/topic handbook pages are
created **with the first real change** that needs them (see pair-workflow); until then
use code-map, threat model, ADRs, and investigations. The list below is the document map;
that one is the code map.

- `VISION.md` — the product vision: positioning, priorities, perf budget, adoption
  strategy; the tie-breaker when trade-offs conflict. End-user distill:
  `docs/philosophy.md`.
- `dev-docs/PLAN.md` — phased implementation roadmap (resequenced 2026-07: native
  7z/RAR before CLI before writing). `dev-docs/IDEAS.md` — speculative future/backlog
  ideas (not committed, not in `PLAN.md`).
- `docs/` — the **published** end-user guide, and nothing else: `index`, `install`,
  `opening-and-listing`, `reading-members`, `extracting`, `gotchas`, `access-and-cost`,
  `formats`, `errors-and-diagnostics`, `cli`, `migrating`, `support-matrix`, `philosophy`,
  `api`, `acknowledgements`. Every file under `docs/` has a nav entry in `mkdocs.yml` and
  `scripts/check_docs_nav.py` fails CI otherwise. Placement rule for a new doc:
  `CONTRIBUTING.md` §"Where does a new doc go?".
- `dev-docs/` — **unpublished** maintainer material: pair workflow, format/topic handbook,
  `code-map.md`, `decisions/` (rare repo-wide ADRs; prefer handbook notes for new
  decisions), threat model, codec analysis, known issues, `investigations/` (finished
  evidence), `discussions/` (design questions written for circulation; each gets a
  RESOLVED header once settled), `history/` (superseded `SPEC` / `ARCHITECTURE` /
  `COMPARISON` / `ASYNC` prose, not normative). Index: `dev-docs/index.md`.
- `dev-docs/threat-model.md` — trust boundaries + the open security/compat gap
  register (each open item becomes an OpenSpec change when tackled).
- `review/` — the **deep-review program**: `README.md` (conventions, ranking, deliverable
  shape), `STATUS.md` (live triage of in-flight rounds — read this before starting a
  review), `backlog.md` (deferred topics with reasons), and `archive/<date>-<topic>/` for
  finished rounds. Findings in an archived area are **re-reviews**: check the archive
  tables before spending budget re-litigating settled ground.
- `openspec/specs/<capability>/spec.md` — the **authoritative machine-checkable
  contract** (OpenSpec requirements + scenarios) for agents and CI. **Not** the primary
  human reading surface — that is the pair workflow + handbook
  ([`dev-docs/pair-workflow.md`](dev-docs/pair-workflow.md)). When specs disagree with
  the handbook, prose docs, or each other, **pause and surface the discrepancy to the
  maintainer** rather than silently picking a winner — the conflict often signals a
  decision that hasn't been made yet.
- `openspec/project.md` — cross-cutting context: capability map, the phase →
  capability implementation-order table, and key strategy notes.
- `openspec/changes/<change>/` — in-flight change proposals (proposal/tasks).
- `CONTRIBUTING.md` — coding/testing standards (type-checking, exception translation,
  behaviour-focused tests, red-green TDD, the pause-and-ask-on-discrepancies rule).

## Writing English (unslop + ASD-STE100)

**Standing rule for every agent session.** Two skills shape prose here. They cut
different things, and where both apply, both run:

- [`unslop`](.claude/skills/unslop/SKILL.md) cuts **AI tells** — puffery,
  throat-clearing, decorative ornaments, vague claims. It applies to maintainer-facing
  prose: chat replies, decision packets, PR comments, thin briefs.
- [`asd-ste100`](.claude/skills/asd-ste100/SKILL.md) cuts **ambiguity** — sentences a
  reader can parse two ways. It applies to every piece of English an agent writes here:
  chat with the maintainer, user-visible text (exception messages, CLI output, `docs/`),
  pull request titles and descriptions, review and inline comments, commit messages, and
  code comments in `src/` and `tests/`.

`technical-writing` is separate and is still opened only for Diátaxis structure and
sentence craft on a published docs or handbook page.

### Advice, not a gate

**Maintainer decision (davitf, 2026-09-22): the scope is broad and the rules are advisory
only, and the tree gets rewritten as it is touched rather than in a sweep** — *"broad, and
advisory only. we'll rewrite as we go, let's see if it improves readability."* The point
is to find out whether this makes prose here easier to read, and a bulk rewrite would not
answer that.

What that means in practice:

- A semicolon or a long sentence is **not a defect**. A review that reports one as a
  finding is wrong, and so would a gate that failed on one be.
- Nothing in the tree is in violation, because there is nothing to violate. Improve the
  prose in a file you are already editing and leave the rest alone.
- `ste-lint.py` is a tool to point at your own draft. It is not a bar to clear, and
  `scripts/check.sh` does not call it.

### Which mode

The skill has two modes and tells you to pick one. Both are advice under the ruling above.
Pick by the text, and lean hardest where a second reading costs the most:

- **Strict** — short text someone meets once and out of context, where a second reading
  is expensive or impossible: exception and diagnostic messages, CLI `--help` text, code
  comments, and a short instruction written for another agent. A code comment belongs
  here for that reason and not because a machine parses it — the developer reading it has
  the code in front of them and not the change that produced it, which is the same
  argument `CONTRIBUTING.md` makes for writing comments at all.
- **STE-flavored** — everything else, which is most of it: chat, pull request
  descriptions and comments, commit messages, `docs/`, `dev-docs/`, `CONTRIBUTING.md`,
  and the pages under `.claude/skills/`. Structural rules as written. The lexical rules
  are a direction of travel everywhere, for the reason the skill gives: without ASD's
  dictionary they are a preference for plain words rather than a checkable standard.

### What it does not override

- **`CONTRIBUTING.md` wins on code comments.** Comments explain *why*, not *what*, and
  they match the density and style of the file around them. STE decides the shape of the
  sentence you write. It does not ask for more comments, shorter comments, or a comment
  where the rule says none.
- **Never trade a hedge for a shorter sentence.** "May have failed" is not "failed", and
  `PLAUSIBLE` is not `CONFIRMED`. The whole review vocabulary here is calibrated
  confidence, so a rewrite that firms one up has changed the finding.
- **It is style, not substance.** A rewrite that supplies a cause, a frequency or a
  mechanism the source did not state is no longer a rewrite.
- **Quoted text stays as it was quoted** — an error string under test, a commit message
  being cited, a maintainer's own words.

`ste-lint.py` is stdlib-only and takes a file or stdin. `--baseline N` tolerates what is
already there, and `--disable` silences named rules.

**Read a clean run for what it is.** The linter splits on newlines, not on sentences, so
a sentence spanning a hard-wrapped line is measured as two short ones and the
sentence-length rule does not fire on it: the same 28-word sentence scores one hard
violation on a single line and zero when wrapped. Most prose here is hard-wrapped near 90
columns, so on those files that rule is close to unreachable and you judge sentence
length yourself. It does work, and is worth reading, wherever a line carries a whole
sentence — an exception message, CLI help text, an unwrapped chat draft, or a file like
the vendored `asd-ste100/SKILL.md`, whose paragraphs are one line each. What a clean run
always tells you is that the checks reading a whole line came back empty: semicolons,
phrasal verbs, nominalization, marketing adjectives. The script is vendored verbatim so
an upstream fix can be re-copied, which is why this caveat lives here and not in it.

## Session setup (`unrar`, `7z`, `openspec`, deps)

`scripts/setup-dev-env.sh` provisions everything: the `unrar` and `7z` system
binaries, the `openspec` CLI, `uv sync --group dev --extra all`, and the
format-on-commit git hook. It runs automatically — Claude Code web sessions via
the `SessionStart` hook (`.claude/hooks/session-start.sh`), Cursor Cloud via
`.cursor/install.sh`. Both call the same script so they cannot drift. Run it by
hand after a manual clone; it is idempotent.

**Do not skip this.** RAR data tests and the benchmark gate's `rar_*` cases *skip*
when `unrar` is absent, and encrypted-ZIP fixtures skip without `7z` — quietly. A
container missing them runs ~109 fewer tests while still reporting all-green, and
`--update-baselines` there would rewrite `structural.json` without those cases. The
script ends by printing what is missing; read that line.

## Environment and tooling

Environment is managed by `uv` (Python 3.11, pinned in `.python-version`). The startup
update script runs `uv sync --group dev --extra all`, so the everyday dev env is already
in place.

**Two scripts run the gates.** Use these rather than assembling the commands yourself —
that is how legs get skipped:

```bash
./scripts/check.sh --fix          # seconds — every fast gate CI runs
./scripts/test.sh                 # minutes — the everyday [all] test leg
./scripts/test.sh --all-configs   # all three configs, when the change can reach an extra
uv run python scripts/review_prep.py   # seconds — before each `review` label
```

`review_prep.py` is not a CI gate: it catches what reviews keep flagging (docs still
naming what the branch moved, paragraphs edited and not rewrapped, "fails on `main`"
claims) before a review round is spent on them. `address-review-findings` §5 says how to
read its output.

`check.sh` mirrors CI's `lint`, `docs` and `openspec` jobs: `ruff check`,
`ruff format --check`, `pyrefly`, `ty`, `check_openspec_archived.py`,
`openspec validate --all`, `check_docs_nav.py`, strict docs build. It runs every gate even
after one fails and names what failed, so one run gives you the whole picture; without
`--fix` it writes nothing. `test.sh --all-configs` restores `uv.lock` and the everyday
environment on exit, so the `[all-lowest]` leg cannot leave a downgraded resolution behind.

Run a tool directly when you want one in isolation (`--no-sync` avoids a redundant
re-resolve):

- Tests: `uv run --no-sync pytest`
- Lint: `uv run --no-sync ruff check` and `uv run --no-sync ruff format --check`
- Type-check: `uv run --no-sync pyrefly check` and `uv run --no-sync ty check`
  (both must stay clean; mypy/pyright are intentionally not used)

### Gates from a `git worktree`

For implementers and `review/` briefs — ordinary PR review doesn't run the gates
(`code-review-skill` addendum §10). Working from a `git worktree` is fine, and **both scripts work there unchanged** —
`./scripts/check.sh` and `./scripts/test.sh`, exactly as in the primary checkout. A
worktree starts without a `.venv`, so they create one for it (`check.sh` announces
`=== no .venv in this tree — creating one`). That takes about a second and ~11 MiB: uv
hardlinks from its global cache, so the second environment on a machine is nearly free.

The tree gets its **own** environment rather than sharing another checkout's, which is
what makes `test.sh --all-configs` and any dependency change on the branch meaningful
there.

One trap, if you ever bypass the scripts: a bare `uv run --no-sync` in a worktree creates
an *empty* `.venv`, fails to spawn the tool, and leaves it behind — after which `pyrefly`
and `ty` resolve that empty environment and report `missing-import` for every optional
extra (`pycdlib`, `brotli`, `pyppmd`, `inflate64`, `bcj`, `tqdm`). That output reads like
a wall of real findings. If you see it, the environment is missing, not the code — and
running `./scripts/check.sh` is the fix.

If a gate cannot be run for environment reasons, **say it was not run** rather than
reporting its output. A phantom failure passed off as a result costs the next reader more
than a skipped gate honestly labelled.

### Formatting before commit (required)

CI fails on unformatted Python (`ruff format --check` over `src/ tests/ scripts/
benchmarks/`). **Do not commit without formatting.**

1. **Cursor Cloud** installs the git hook via `.cursor/install.sh` on every boot.
   On a fresh local clone (or if the hook is missing), run:

   ```bash
   ./scripts/install-git-hooks.sh
   ```

   (Cursor remaps `core.hooksPath`; this script installs into the chained original
   hooks dir so it still runs. Prefer it over bare `pre-commit install`.)

2. **Before every commit**, if the hook is not installed (or you used
   `--no-verify`), run formatting yourself:

   ```bash
   uv run --no-sync ruff format src/ tests/ scripts/ benchmarks/
   uv run --no-sync ruff check --fix src/ tests/ scripts/ benchmarks/
   ```

   `ruff format --check` only *detects* drift; it does not rewrite files. Always
   run `ruff format` (no `--check`) to apply.

Non-obvious gotchas:

- The startup script is committed at `scripts/setup-dev-env.sh` and is shared by every
  environment that provisions a workspace, so they cannot drift on what is installed:
  Cursor Cloud calls it from `.cursor/install.sh` (wired via `.cursor/environment.json`),
  Claude Code web from the `SessionStart` hook `.claude/hooks/session-start.sh`
  (registered in `.claude/settings.json`; it no-ops unless `CLAUDE_CODE_REMOTE=true`,
  leaving a developer's own machine alone). Run it directly after a manual clone.
  It bootstraps `uv` if missing (JIT Cloud images may
  not ship it), installs `unrar` + `p7zip-full`, the `openspec` CLI, runs
  `uv sync --group dev --extra all`, and `./scripts/install-git-hooks.sh`, so the
  format-on-commit hook is present without a manual step. It also best-effort
  corrects a skewed VM clock (and relaxes apt Release-date checks) so
  `apt-get update` does not fail with "Release file … is not valid yet".
- **`unrar`** (system binary, from the `multiverse` apt component) backs RAR *data*
  tests; without it they skip cleanly rather than fail.
- **`7z`** (system binary, from `p7zip-full`) is required by tests that build encrypted
  ZIP fixtures by shelling out to it (`tests/test_password.py`, the encrypted corpus
  entries in `tests/test_corpus_sweep.py`); they skip cleanly when it is absent.
  The setup script installs `p7zip-full` automatically.
- Both of the above skip **quietly**, which is the trap: a container without them ran
  1900 passed / 167 skipped where a provisioned one runs 2009 / 58 — ~109 tests gone
  with the suite still green. `--update-baselines` in that state would also rewrite
  `structural.json` without the `rar_*` cases (it now refuses instead). If you are
  unsure whether the environment is complete, run `scripts/setup-dev-env.sh`; its
  closing verification block names anything missing.
- **`openspec` CLI** lives at `~/.local/bin` (on `PATH`). The plain
  `npm install -g @fission-ai/openspec` fails with `EACCES` here because the global npm
  prefix is not user-writable — the update script instead installs it into a writable,
  already-on-`PATH` prefix: `npm install -g --prefix "$HOME/.local" @fission-ai/openspec`.
- The full push gate runs the suite in **three dependency configs** (`[all]`,
  `[all-lowest]`, `[core-only]`), for a change that can reach an optional library; the
  exact commands and when it applies are in `CONTRIBUTING.md`. After a
  `--no-dev` / lowest-resolution leg, restore the everyday env with
  `uv sync --group dev --extra all`.
- Docs (optional): `uv run --group docs mkdocs build --strict`.
- **Atheris fuzz** is a separate main-push / `workflow_dispatch` job (not the PR matrix).
  Install with `uv sync --group fuzz --group dev --extra all`, then
  `uv run --no-sync python -m tests.atheris_fuzz --smoke`. Mutation /
  `ARCHIVEY_FUZZ` harnesses are unchanged. See `CONTRIBUTING.md` ("Coverage-guided fuzz").
- **CI matrix Python versions**: repo `.python-version` pins local/default envs to 3.11.
  The test matrix in `.github/workflows/ci.yml` must pass `--python <matrix>` (and set
  `UV_PYTHON`) on every `uv sync` / `uv run`, or "py3.12/3.13/3.14" legs silently re-test
  3.11. The free-threaded and atheris jobs already did this; the main `test` job must too.


## Cross-platform traps (you develop on Linux; CI runs Windows and macOS)

The test matrix covers Linux, macOS and Windows, but your container is Linux — so this
class of failure lands *after* you push, and it has repeatedly cost a review round. Check
new tests and new message-formatting code for all four:

- **`read_text()` / `open()` without `encoding="utf-8"`.** Python's default encoding on
  Windows is the ANSI code page (cp1252), not UTF-8. Any test that reads a repo source
  file — static/AST guards especially — will raise `UnicodeDecodeError` there the moment a
  source file contains a curly quote or an accented character. Always pass
  `encoding="utf-8"` explicitly.
- **Control characters and `:*?"<>|` in on-disk filenames.** Windows rejects them with
  `WinError 123` at *creation* time, so a test that writes a hostile member name to disk
  fails before it reaches the behaviour it meant to assert. Use a Windows-legal spoof
  (U+2028 and friends) for the portable case, and mark the ANSI variant with the repo's
  existing `_ANSI_ONLY` marker.
- **Path separators in compared strings.** A native `Path` interpolated into a message
  renders `C:\Users\…` on Windows, and backslashes double once the text is escaped for
  terminal display. Render paths through `escaping.display_path()` before they enter a
  message, and compare against `as_posix()` rather than `str(path)`.
- **Filesystem case-insensitivity.** macOS and Windows collapse `A.txt` / `a.txt`, which
  changes name-collision behaviour. If a test depends on two members differing only by
  case, it is testing something different on each platform.

## OpenSpec CLI

Installed by the setup script above. If you need it manually, it ships as the npm
package `@fission-ai/openspec` (Node is available):

```bash
npm install -g @fission-ai/openspec
```

The bare `openspec` package on npm is an unrelated empty stub — install the
`@fission-ai/...` scoped package, not that one. On images where the global npm
prefix is not user-writable this fails with `EACCES`; use
`npm install -g --prefix "$HOME/.local" @fission-ai/openspec` instead (what the
setup script does). Verify with `openspec --version` (known-good: 1.4.1). Common
commands, run from the repo root:

```bash
openspec list                 # in-flight changes + task progress
openspec validate --all       # validate all specs and changes
openspec validate --strict <item-name>
openspec archive <change> --yes   # apply the deltas to openspec/specs/
```

**Archive in the PR that finishes the change.** A merged-but-unarchived change leaves the
authoritative specs describing something that no longer ships, and deferring the archive to
a follow-up PR is what caused that three times running. Most changes here are proposed,
implemented, and finished in a single PR — so run `openspec archive <change> --yes` in that
same PR and commit the resulting `openspec/specs/` diff. Make it the change's **last task**,
so checking the final box and applying the deltas are one act.

CI enforces this **on pull requests and on `main`** (`scripts/check_openspec_archived.py`).
If a change really is not finished — the design is still moving under review, or the archive
is deliberately batched with a sibling change — **leave its trailing task unchecked**. That
is the escape hatch, and it is honest: a checked last box is a claim that the change has
landed in the specs. Details: `CONTRIBUTING.md` §"Archiving an OpenSpec change".

Note `openspec validate --strict` does **not** check that a `MODIFIED` header names a
requirement that actually exists in the parent spec, so a mis-targeted delta can
validate green and silently do nothing on archive. For a non-trivial delta, verify with
a dry-run archive (apply on a scratch tree, diff `openspec/specs/`, then reset).

**Retiring a whole capability has no delta form.** Removing every requirement leaves a
spec with none, which `openspec archive` refuses to write; deleting the directory first
makes archive read the same delta as a request to *create* the capability. Delete
`openspec/specs/<capability>/` by hand, keep only the deltas that modify surviving specs,
and record the retired requirements and their reasons in the change's `README.md` so the
archived change still explains itself. Worked example:
`openspec/changes/archive/2026-09-02-retire-archive-writing-specs/`.

Default change schema is **`library`** (proposal → compact specs + design →
tasks). Specs stay dense (signatures/matrices); `design.md` holds investigations
and decisions (stub OK for trivial deltas). Use `--schema minimalist` for tiny
changes. See `openspec/schemas/library/README.md` and `openspec/config.yaml`.

## Reference repository: `archivey-dev`

`archivey-dev` is the **v1 / DEV** codebase that v2 selectively ports from and
whose `openspec/changes/` contain the native-reader explorations. It is a separate
repo and is NOT in this session's GitHub-tool scope.

**How to access it:** a plain HTTPS `git clone` works from this environment:

```bash
git clone https://github.com/davitf/archivey-dev.git /tmp/archivey-dev
```

Notes:
- The GitHub **API** (and WebFetch against `api.github.com`) is rate-limited for
  unauthenticated calls and returns `403` — do not conclude the repo is private;
  use `git clone` instead.
- Pin to a specific commit for reproducible ports. Known-good revision used while
  authoring these specs: `730275b7a755f8b5b8d08d3d4d9b267b5bdadb0d` (default
  branch HEAD; the clone carries no release tags).
- High-value paths inside it:
  - `openspec/changes/sevenzip-native-reader/` and
    `openspec/changes/rar-native-metadata-reader/` (+ `docs/*-native-reader-design.md`)
    — the native-parser designs this repo's `format-7z` / `format-rar` specs follow.
  - `src/archivey/` — the source to port (Phase 1).
  - `tests/` — the declarative test harness and fixtures.

## 7z / RAR reading strategy (native-first)

7z and RAR are read with **native** parsers, not `py7zr` / `rarfile`:
- 7z: native header parse + stdlib `lzma`/`bz2`/`zlib` for the common codecs
  (core, zero-dep). PPMd/Deflate64 and AES decryption via the `[recommended]` extra;
  BCJ2 is detected and rejected. `py7zr` is a **dev oracle** only

- RAR: native RAR3/RAR5 metadata parser (drops `rarfile`); the external `unrar`
  binary remains the decompressor for member data. Encrypted headers are decrypted
  natively via `cryptography` (`[recommended]`). `rarfile` is a test oracle only.

See `openspec/specs/format-7z/spec.md`, `format-rar/spec.md`,
`packaging-and-extras/spec.md`, and `testing-contract/spec.md`.

## Review workflow (two agents, two skills)

PR review here is a **handoff between two agents**, and each half has a skill.

**Each rule has one home, and the other places link to it.** Restating a rule in five
files is what produced #351's duplicate findings and the #354/#355 pair, where the same
defect was fixed twice. The split is by *reader*:

| Reader | Reads | Holds |
|--------|-------|-------|
| Implementer | `CONTRIBUTING.md` | Every coding and testing rule — typing, exceptions, comments, config bounds, red–green, the three-config gate |
| Implementer | `address-review-findings/SKILL.md` | How a finding gets dispositioned |
| Reviewer | `CONTRIBUTING.md` + the review addendum | The addendum is review-only: finding discipline and output shape (§0), what to check (§3–§5, citing CONTRIBUTING rather than repeating it), review order (§8), posting (§10) |
| Reviewer, sometimes | `code-review-skill/reference/reviewing-proposals.md`, `…/deep-reviews.md` | Opened only for what they name — a proposal or delta spec, a commissioned `review/` brief. A contract-moving code PR opens the first for its values check alone. Addendum §6 and §9 are stubs pointing here |
| Autopilot | `steward/SKILL.md` | Only where this repo differs from a generic watcher |

`SKILL.md`, `.cursor/commands/*.md` and this section are **entrypoints**. An entrypoint
routes: it names a concern and points at the file the rule lives in, and it may bind
host-specific facts — which finding-ID prefix *this* host uses, which command name lands
where. It does **not** restate the rule, because the restatement is the copy that drifts.
`SKILL.md` owns one thing outright and says so: the ≤1-minute logistics list, which
addendum §8 points at.

Adding a rule means editing one file — if you find yourself editing a second, the rule is
in the wrong place.


1. **A separate agent reviews** the PR with **`/code-review-skill`** — not a bare
   `/code-review`, which is a *builtin* skill in both Claude Code and Cursor and is not
   this one; the Cursor project command `.cursor/commands/code-review.md` is what makes
   `/code-review` land correctly there. It posts the **full** findings to the PR (blocks
   1–2 for the implementor; block 3 packets for the maintainer). When also chatting with
   the maintainer, send **decision packets only** unless they ask for the full handoff
   ([`dev-docs/pair-workflow.md`](dev-docs/pair-workflow.md) §Decision packet). The review
   rules live in `.claude/skills/code-review-skill/reference/archivey-review-addendum.md`
   and only there: **§0** is the output shape and the verdicts, **§3–§5** are what to check
   (against `CONTRIBUTING.md`, which holds the rules themselves), and **§10** is posting —
   stable finding IDs carrying the reviewer's own initial, a header on every comment
   carrying the round and the verdict, the findings themselves in inline threads with the
   body carrying only an index, no tables in anything posted, and one status bullet per
   previous ID when re-reviewing.
2. **The implementing agent works through them** with `address-review-findings`
   (Cursor: `/address-review`). Every finding gets an explicit disposition — fixed,
   disproven, escalated, or deferred-with-a-written-home. Nothing is dropped silently, and
   nothing is "fixed" without being reproduced first. Escalations to the maintainer use
   the **decision packet** shape only (same section of `pair-workflow.md`).

3. **An agent may pick the review up without being asked.** A session subscribed to PR
   activity reacts to a review comment or a CI failure as an *event*, from its own generic
   posture — it never invokes a skill nobody named, so the two-skill handoff above does not
   reach it on its own. Two things close that: `code-review-skill` names the responder skill
   in the review body (addendum §10), and `.claude/skills/steward/SKILL.md`, which a Claude
   Code session subscribed to PR activity is instructed to read *before* it acts on a CI or
   review event. That instruction is a harness convention rather than a documented
   extension point — ADR 0018 §"Where the filename comes from" records what it rests on,
   and why the directory must not be renamed. `steward` is a router, not a third process:
   it hands off to `address-review-findings` and records only the deltas from a watcher's
   defaults, including where it may push autonomously (a failure the PR itself caused,
   reproduced, no contract or docs move, gate clean) and where it must stop and ask. Why
   the two skills stay separate rather than merging into one review-and-fix mode: ADR
   [0018](dev-docs/decisions/0018-review-and-address-stay-separate-skills.md).

4. **Linear issues** use `.claude/skills/address-linear-issue/SKILL.md` (Cursor:
   `/address-linear-issue`). Same two skills, sequenced: the implementing agent reads
   the ticket, fixes it, opens the PR and hands it to the
   [review loop](dev-docs/review-loop.md), which runs `code-review-skill` in a separate
   Claude session and posts to the PR; whoever holds the branch then runs
   `address-review-findings`. **Add the `review` label when you have finished** — after
   implementing, and again after addressing a round whose closing comment asked to see
   the fixes — as the last action, after the final push; adding the label is what starts
   each round. Do not review your own
   diff, and do not spawn a reviewer of your own — the loop is the second opinion. Maintainer decision (davitf,
   2026-09-19): Claude reviews PRs started from a Linear issue, replacing the fresh
   Cursor Grok subagent this step used to spawn.

**Nothing from the internal tracker goes into PR text.** This repository is public; the
tracker is not. Three rules, and they are about the *internal tracker* only:

- **Never** an internal-tracker URL, anywhere — PR title or body, review or inline
  comment, commit message, or a file in the repo.
- **Not** an internal-tracker key (`ARC-123`) in a PR title or body, a review or inline
  comment, or a commit message. Write "tracked internally" instead: the reader cannot open
  the ticket, so the key is dead weight to them and it publishes the internal layout.
- **GitHub `#nnn` is the public record and is always fine** — in PR text, commits,
  comments and repo files alike. This rule does not touch it.

**Inside `dev-docs/` and `openspec/changes/`, a bare key may mark tracked work — never
cite one as a source.** Maintainer decision (davitf, 2026-09-19): the data lives in the
repo and the PRs, so a key says *where this work is tracked*, not *where the reason is
written*. "The dedup is ARC-54, not this change's job" is fine. "See ARC-54 for the
measurement" is not — put the measurement here. Test it by deleting the key: if the
sentence still says everything a reader needs, it was a tracking tag; if the sentence
now has a hole, the content is in the wrong place. The existing citations in
`threat-model.md`, `investigations/adr-0014-investigation.md` and two
`openspec/changes/` files all pass that test, so this is a rule for new writing, not a
sweep.

Where a finding deserves a durable in-repo home, that is `review/backlog.md` under
"Parked from PR reviews", with the reasoning in the format handbook
(`dev-docs/formats/<fmt>.md` §6). The cross-reference runs one way only: put the PR URL on
the tracker item, never the reverse.

Two things about this repo make the handoff sharper than it looks:

- **Agents post through the maintainer's GitHub account**, unless the host has its own bot
  identity (`cursor[bot]`, `qodo-code-review[bot]`). So a comment from the `davitf` login
  carrying an agent attribution footer — `_Generated by [Claude Code](https://claude.ai/code)_`
  in Claude Code sessions, the equivalent elsewhere — is an agent; the same login *without*
  one is the human. Make your own PR comments identifiable the same way, and read inline
  threads carefully: the maintainer's own questions arrive that way and carry more weight
  than an automated finding.
- **Never reproduce a closing phrase next to the review hub's number.** The hub
  ([#315](https://github.com/davitf/archivey/pull/315)) is the one pull request here that
  must never close, and a commit message or a pull request body carrying `Closes` (or
  `Fixes`, or `Resolves`) immediately before its number closes it. Both surfaces are
  parsed: GitHub prefills the squash body from the pull request body, but the squash body
  can be edited at merge time, so check each. The trap is *quoting* — writing down what
  such a phrase said reproduces it, which closed the hub a second time on 2026-09-21 from
  the text explaining the first. Describe the phrase instead, or break the string. The
  failure is silent: threads still serve, `sweep_coverage.py` is unaffected, nothing goes
  red. [`dev-docs/open-work-inventory.md`](dev-docs/open-work-inventory.md) has both
  incidents. There is a net under this —
  [`review-hub-watchdog.yml`](.github/workflows/review-hub-watchdog.yml) reopens the hub
  and says which surface closed it — but it is recovery, not prevention. A pull request
  body could be checked before the merge; a squash body could not, because whoever merges
  can edit it at merge time and GitHub parses what was actually merged. So a pre-merge
  check would cover one surface and miss the other, and the watchdog covers both after.
  Its decision is in
  [`scripts/review_hub_watchdog_gate.py`](scripts/review_hub_watchdog_gate.py), unit tested,
  because the branch that names the surface only ever runs during an incident.
- **Escalate one decision packet at a time.** Shape and fields:
  [`dev-docs/pair-workflow.md`](dev-docs/pair-workflow.md) §Decision packet (canonical).
  Do not dump the full finding list into chat — that stays on the PR. A batched list of
  five numbered decisions pushes the work back onto the person you are asking.

## Conventions

- Python 3.11+, zero-dependency core, sync-only API for v1.
- Tooling via `uv`: `uv sync`, `uv run pyrefly check`, `uv run ty check`,
  `uv run pytest`, `uv run ruff`. Type-checking is **Pyrefly + ty** (the library stays
  clean on both); mypy is not used. The package stays pip-installable (standard PEP 621
  metadata, `hatchling`).
- Install the format-on-commit git hook with `./scripts/install-git-hooks.sh` (the setup
  script does this for you); otherwise run `uv run ruff format` yourself before committing.
- **Both type checkers, every time.** `ruff` passing is not the gate. Running `ruff` alone
  and pushing is the most common self-inflicted CI failure here — `pyrefly` and `ty` are
  separate checks and either one can be red on a tree ruff calls clean. `./scripts/check.sh`
  runs all of them so there is no list to get half-right.
- **Before pushing a change that could behave differently depending on which optional
  libraries are installed, run all three dependency configurations** — current versions
  (`[all]`), minimum versions (`[all-lowest]`), and the zero-dep core (`[core-only]`) —
  since optional libraries change behaviour by both presence and version:
  `./scripts/check.sh && ./scripts/test.sh --all-configs`. Judge it by what the change can
  reach: a comment, a piece of prose, or a test that only reads files off disk cannot vary
  by configuration, and `./scripts/check.sh && ./scripts/test.sh` is enough; a codec, a
  backend, an import or a version check gets all three. Say in the pull request when you
  skipped the legs and why. Details and the underlying commands are in `CONTRIBUTING.md`
  ("Before pushing…").
- See `CONTRIBUTING.md` for coding/testing standards (incl. behaviour-focused tests,
  **leave the code self-explanatory** with inline *why*, and the rule to
  **pause and ask the maintainer on spec/design discrepancies** rather than silently
  resolving them).
