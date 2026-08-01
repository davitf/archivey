# Agent Guide

For repo orientation, specs, and workflow, see `CLAUDE.md` and `CONTRIBUTING.md`
(coding/testing standards, the "Before pushing…" three-config test rule). End-user
docs live under `docs/`; design “why” in `docs/decisions/`; historical prose in
`docs/grab-bag/`.

## Cursor Cloud specific instructions

This repo is a **pure Python library** (`archivey`) — there is no server, web UI, or
runnable CLI (the `archivey` command in `openspec/specs/cli/spec.md` is planned, not
implemented). "Running the application" means exercising the library API in Python:
`archivey.open_archive(path)` / `archivey.extract(path, dest)` plus the detection
helpers (`detect_format`, `list_supported_formats`). Implemented backends are ZIP, TAR,
ISO, directory, and single-file-compressed (gz/bz2/xz/lzip/zstd/lz4/.Z); **7z and RAR
readers are not implemented yet** despite their specs/extras existing.

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