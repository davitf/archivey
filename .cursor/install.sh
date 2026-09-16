#!/usr/bin/env bash
# Cursor Cloud update/install script (see .cursor/environment.json).
# Must stay idempotent — runs on every agent boot after the workspace is checked out.
#
# The work itself lives in scripts/setup-dev-env.sh, shared with the Claude Code
# SessionStart hook (.claude/hooks/session-start.sh) so the two provisioning paths
# cannot drift on what gets installed. Environment-specific steps, if any are ever
# needed, belong here; anything a plain checkout needs belongs in the shared script.
set -euo pipefail

exec "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/scripts/setup-dev-env.sh"
