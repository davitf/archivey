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

- `VISION.md` — the product vision: positioning, priorities, perf budget, adoption
  strategy; the tie-breaker when trade-offs conflict. End-user distill:
  `docs/philosophy.md`.
- `dev-docs/PLAN.md` — phased implementation roadmap (resequenced 2026-07: native
  7z/RAR before CLI before writing). `dev-docs/IDEAS.md` — speculative backlog.
- `docs/` — the **published** end-user guide, and nothing else: `install`,
  `opening-and-listing`, `reading-members`, `gotchas`, `safe-extraction`,
  `access-and-cost`, `formats`, `errors-and-diagnostics`, `cli`, `migrating`,
  `support-matrix`, `philosophy`, `api`, `acknowledgements`. Every file under `docs/`
  has a nav entry in `mkdocs.yml` and `scripts/check_docs_nav.py` fails CI otherwise.
  Placement rule for a new doc: `CONTRIBUTING.md` §"Where does a new doc go?".
- `dev-docs/` — **unpublished** maintainer material: `decisions/` (the ADR log),
  threat model, codec analysis, known issues, `investigations/` (finished evidence),
  `history/` (superseded `SPEC` / `ARCHITECTURE` / `COMPARISON` / `ASYNC` prose, not
  normative).
- `dev-docs/threat-model.md` — trust boundaries + the open security/compat gap
  register (each open item becomes an OpenSpec change when tackled).
- `openspec/specs/<capability>/spec.md` — the authoritative capability specs
  (OpenSpec format: requirements + WHEN/THEN scenarios). The specs are authoritative,
  but when they disagree with the prose docs (or with each other), **pause and surface
  the discrepancy to the maintainer** rather than silently picking a winner — the
  conflict often signals a decision that hasn't been made yet.
- `openspec/project.md` — cross-cutting context: capability map, the phase →
  capability implementation-order table, and key strategy notes.
- `openspec/changes/<change>/` — in-flight change proposals (proposal/tasks).
- `CONTRIBUTING.md` — coding/testing standards (type-checking, exception translation,
  behaviour-focused tests, red-green TDD, the pause-and-ask-on-discrepancies rule).
- `dev-docs/IDEAS.md` — speculative future/backlog ideas (not committed, not in
  `dev-docs/PLAN.md`).

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
in place. Run tools with `--no-sync` to avoid a redundant re-resolve, e.g.:

- Tests: `uv run --no-sync pytest`
- Lint: `uv run --no-sync ruff check` and `uv run --no-sync ruff format --check`
- Type-check: `uv run --no-sync pyrefly check` and `uv run --no-sync ty check`
  (both must stay clean; mypy/pyright are intentionally not used)

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
- **`openspec` CLI** lives at `~/.local/bin` (on `PATH`). `CLAUDE.md`'s
  `npm install -g @fission-ai/openspec` fails with `EACCES` here because the global npm
  prefix is not user-writable — the update script instead installs it into a writable,
  already-on-`PATH` prefix: `npm install -g --prefix "$HOME/.local" @fission-ai/openspec`.
- The full push gate runs the suite in **three dependency configs** (`[all]`,
  `[all-lowest]`, `[core-only]`); the exact commands are in `CONTRIBUTING.md`. After a
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

**Archiving is a separate step from merging**, and forgetting it is the most common
failure mode here: a merged-but-unarchived change leaves the authoritative specs
describing something that no longer ships. Once a change's tasks are all done and its
PR has merged, archive it and commit the resulting `openspec/specs/` diff. CI enforces
this on `main` (`scripts/check_openspec_archived.py`).

Note `openspec validate --strict` does **not** check that a `MODIFIED` header names a
requirement that actually exists in the parent spec, so a mis-targeted delta can
validate green and silently do nothing on archive. For a non-trivial delta, verify with
a dry-run archive (apply on a scratch tree, diff `openspec/specs/`, then reset).

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

## Conventions

- Python 3.11+, zero-dependency core, sync-only API for v1.
- Tooling via `uv`: `uv sync`, `uv run pyrefly check`, `uv run ty check`,
  `uv run pytest`, `uv run ruff`. Type-checking is **Pyrefly + ty** (the library stays
  clean on both); mypy is not used. The package stays pip-installable (standard PEP 621
  metadata, `hatchling`).

  git hook with `./scripts/install-git-hooks.sh` (see `AGENTS.md` / `CONTRIBUTING.md`);
  otherwise run `uv run ruff format` yourself before committing.
- **Before pushing, run the test suite in all three dependency configurations** — current
  versions (`[all]`), minimum versions (`[all-lowest]`), and the zero-dep core
  (`[core-only]`) — since optional libraries change behaviour by both presence and version.
  The exact commands are in `CONTRIBUTING.md` ("Before pushing…").
- See `CONTRIBUTING.md` for coding/testing standards (incl. behaviour-focused tests,
  **leave the code self-explanatory** with inline *why*, and the rule to
  **pause and ask the maintainer on spec/design discrepancies** rather than silently
  resolving them).
