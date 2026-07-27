#!/usr/bin/env bash
# Cursor Cloud update/install script (see .cursor/environment.json).
# Must stay idempotent — runs on every agent boot after the workspace is checked out.
set -euo pipefail

# Official uv installer lands here; keep it first for non-login shells.
export PATH="${HOME}/.local/bin:${PATH}"

# Cloud VMs sometimes boot with a skewed clock; apt then rejects Release files as
# "not valid yet". Prefer correcting the clock from an HTTP Date header; if that
# fails, still allow a generous future skew so package installs can proceed.
if date_hdr="$(
  curl -fsSI --max-time 10 https://archive.ubuntu.com/ubuntu/ \
    | awk -F': ' 'tolower($1) == "date" { print $2; exit }'
)"; then
  sudo date -s "${date_hdr}" >/dev/null 2>&1 || true
fi

# unrar: RAR member-data tests (multiverse). p7zip-full: encrypted ZIP fixture builds.
sudo apt-get \
  -o Acquire::Max-FutureTime=604800 \
  -o Acquire::Check-Valid-Until=false \
  update
sudo apt-get install -y unrar p7zip-full

npm install -g --prefix "${HOME}/.local" @fission-ai/openspec

# JIT / snapshot-less Cloud images may not ship uv; bootstrap if missing.
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi

uv sync --group dev --extra all

# Auto ruff fix+format on commit (Cursor remaps core.hooksPath; this install is aware).
./scripts/install-git-hooks.sh
