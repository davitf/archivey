#!/usr/bin/env bash
# Build RARLAB UnRAR (the freeware decompressor, not the trialware writer) from
# a pinned GitHub mirror of the published source, and install the `unrar` binary.
#
# Why this exists: Homebrew disabled the `rar` cask on 2026-09-01 because the
# official macOS binaries fail Gatekeeper notarization. Compiling from source
# produces a local binary CI will actually run. archivey's finder requires the
# RARLAB banner — `unar` / `unrar-free` / `7z` are not substitutes.
#
# Source: https://github.com/pmachapman/unrar (one-person mirror of rarlab
# UnRAR source). We fetch a pinned commit with git so the SHA is
# content-addressed; GitHub's generated archive tarballs are not used.
#
# Usage:
#   scripts/install-rarlab-unrar.sh --dest DIR [--cache-dir DIR]
#
# Writes DIR/unrar. No-ops if that path is already executable.
set -euo pipefail

# rarlab unrarsrc tarball name; the binary banner reads "UNRAR 7.23"
# (version.hpp RARVER_MAJOR 7 / RARVER_MINOR 23).
UNRAR_VERSION="7.2.7"
UNRAR_COMMIT="d8612461815f7feffcf7f1bf9c5942125b25c1b4"
UNRAR_REPO="https://github.com/pmachapman/unrar.git"

DEST=""
CACHE_DIR="${XDG_CACHE_HOME:-${HOME}/.cache}/archivey/unrarsrc"

usage() {
  cat <<'EOF'
Build RARLAB UnRAR from a pinned GitHub mirror and install the unrar binary.

Usage:
  scripts/install-rarlab-unrar.sh --dest DIR [--cache-dir DIR]

Writes DIR/unrar. No-ops if that path is already executable.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dest)
      DEST="${2:-}"
      shift 2
      ;;
    --cache-dir)
      CACHE_DIR="${2:-}"
      shift 2
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      echo "install-rarlab-unrar: unknown argument: $1 (try --help)" >&2
      exit 2
      ;;
  esac
done

if [ -z "$DEST" ]; then
  echo "install-rarlab-unrar: --dest DIR is required" >&2
  exit 2
fi

mkdir -p "$DEST"
if [ -x "${DEST}/unrar" ]; then
  echo "install-rarlab-unrar: already present at ${DEST}/unrar"
  exit 0
fi

need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "install-rarlab-unrar: need $1 on PATH" >&2
    exit 1
  fi
}
need git
need c++
need make

mkdir -p "$CACHE_DIR"
src="${CACHE_DIR}/unrar-${UNRAR_COMMIT}"
# Content-addressed fetch of the pin — not GitHub's generated archive tarball.
# init + fetch --depth 1 <sha> skips cloning the default branch. GitHub serves
# reachable SHAs of public repos this way.
if [ "$(git -C "$src" rev-parse HEAD 2>/dev/null || true)" != "$UNRAR_COMMIT" ]; then
  echo "install-rarlab-unrar: fetching ${UNRAR_REPO} @ ${UNRAR_COMMIT:0:12}"
  rm -rf "$src"
  mkdir -p "$src"
  git -C "$src" init --quiet
  git -C "$src" remote add origin "$UNRAR_REPO"
  GIT_TERMINAL_PROMPT=0 git -C "$src" fetch --depth 1 origin "$UNRAR_COMMIT"
  git -C "$src" -c advice.detachedHead=false checkout --detach FETCH_HEAD
fi
got="$(git -C "$src" rev-parse HEAD)"
if [ "$got" != "$UNRAR_COMMIT" ]; then
  echo "install-rarlab-unrar: expected commit ${UNRAR_COMMIT}, got ${got}" >&2
  exit 1
fi
if [ ! -f "${src}/makefile" ]; then
  echo "install-rarlab-unrar: expected makefile at ${src}/makefile" >&2
  exit 1
fi

jobs="$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)"
echo "install-rarlab-unrar: compiling UnRAR ${UNRAR_VERSION} (banner 7.23, ${UNRAR_COMMIT:0:12}) with ${jobs} jobs"
make -C "$src" -j"$jobs"

# macOS install(1) has no GNU -D; copy the binary ourselves.
cp "${src}/unrar" "${DEST}/unrar"
chmod +x "${DEST}/unrar"
echo "install-rarlab-unrar: installed ${DEST}/unrar"
