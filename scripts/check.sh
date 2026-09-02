#!/usr/bin/env bash
# Every fast gate CI runs, in one command.
#
# This is the "will CI's non-test jobs pass?" answer: it mirrors the `lint`, `docs` and
# `openspec` jobs in .github/workflows/ci.yml. It does not run the test suite — that is
# scripts/test.sh, which takes minutes rather than seconds, and the two get run at
# different cadences.
#
# It exists because the gate is seven commands and people were running one of them.
# Pushing after `ruff` alone, with `pyrefly` or `ty` red, is the most common
# self-inflicted CI failure in this repo.
#
#   ./scripts/check.sh          check only, writes nothing — what CI does
#   ./scripts/check.sh --fix    apply ruff format + autofix first, then check
#
# Runs every gate even after one fails, then lists what failed. A single run should tell
# you everything that is wrong, not just the first thing.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

FIX=0
for arg in "$@"; do
  case "$arg" in
    --fix) FIX=1 ;;
    -h|--help) sed -n '2,18p' "$0" | sed 's/^# \?//'; exit 0 ;;
    *) echo "unknown argument: $arg (try --help)" >&2; exit 2 ;;
  esac
done

PATHS=(src/ tests/ scripts/ benchmarks/)
FAILED=()

run() {
  local name="$1"; shift
  printf '\n\033[1m=== %s\033[0m\n' "$name"
  if "$@"; then
    return 0
  fi
  FAILED+=("$name")
  return 1
}

if [ "$FIX" -eq 1 ]; then
  printf '\n\033[1m=== applying ruff format + autofix\033[0m\n'
  uv run --no-sync ruff format "${PATHS[@]}"
  uv run --no-sync ruff check --fix "${PATHS[@]}"
fi

# --- CI job: lint ------------------------------------------------------------------
run "ruff check"          uv run --no-sync ruff check "${PATHS[@]}"
run "ruff format --check" uv run --no-sync ruff format --check "${PATHS[@]}"
run "pyrefly"             uv run --no-sync pyrefly check
run "ty"                  uv run --no-sync ty check

# --- CI job: openspec --------------------------------------------------------------
# Stdlib-only, same as CI. `openspec validate` needs the npm CLI, which the setup script
# installs; skip rather than fail if someone is running on a bare clone.
run "openspec archived" python3 scripts/check_openspec_archived.py
if command -v openspec >/dev/null 2>&1; then
  run "openspec validate" openspec validate --all
else
  printf '\n\033[1m=== openspec validate\033[0m\nSKIPPED — `openspec` CLI not on PATH (scripts/setup-dev-env.sh installs it)\n'
fi

# --- CI job: docs ------------------------------------------------------------------
# Needs the `docs` group (mkdocs, markdown) rather than the everyday env, so this leg
# syncs on demand instead of using --no-sync.
run "docs nav"   uv run --group docs python scripts/check_docs_nav.py
run "internal md links" uv run --no-sync python scripts/check_internal_md_links.py
run "docs build" uv run --group docs mkdocs build --strict --quiet

# --- verdict -----------------------------------------------------------------------
if [ ${#FAILED[@]} -eq 0 ]; then
  printf '\n\033[32mall checks passed\033[0m\n'
  exit 0
fi

printf '\n\033[31m%d check(s) failed:\033[0m\n' "${#FAILED[@]}"
for name in "${FAILED[@]}"; do
  printf '  - %s\n' "$name"
done
printf '\nRe-run one of them directly for the full output.\n'
exit 1
