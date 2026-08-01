#!/usr/bin/env bash
# Claude Code SessionStart hook — provisions a web session's container.
#
# Runs the same bootstrap as Cursor Cloud (scripts/setup-dev-env.sh) so a session
# never starts with a partial toolchain. That matters more than it sounds: RAR data
# tests and the benchmark gate's rar_* cases *skip* when `unrar` is missing, so an
# unprovisioned container reports green while testing less than it claims.
#
# Local sessions are left alone — a developer's machine is their own.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

exec "${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}/scripts/setup-dev-env.sh"
