#!/usr/bin/env bash
# Build RARLAB UnRAR (the freeware decompressor, not the trialware writer) from
# a pinned GitHub mirror of the published source, and install the `unrar` binary.
#
# Why this exists: Homebrew disabled the `rar` cask on 2026-09-01 because the
# official macOS binaries fail Gatekeeper notarization. Compiling from source
# produces a local binary CI will actually run. The tarball is fetched from
# GitHub (not rarlab) and cached so later jobs do not re-download. archivey's
# finder requires the RARLAB banner — `unar` / `unrar-free` / `7z` are not
# substitutes.
#
# Source: https://github.com/pmachapman/unrar (mirror of rarlab UnRAR source).
# Pin both the commit and the archive sha256; bump both together.
#
# Usage:
#   scripts/install-rarlab-unrar.sh --dest DIR [--cache-dir DIR]
#
# Writes DIR/unrar. No-ops if that path is already executable.
set -euo pipefail

# Mirror commit "Updated to 7.2.7" — same tree as rarlab unrarsrc-7.2.7.
UNRAR_VERSION="7.2.7"
UNRAR_COMMIT="d8612461815f7feffcf7f1bf9c5942125b25c1b4"
UNRAR_SHA256="3d385eb009bbd657981ef317fc6625be9b627e699095019349ffec48da7fcf04"
UNRAR_URL="https://github.com/pmachapman/unrar/archive/${UNRAR_COMMIT}.tar.gz"

DEST=""
CACHE_DIR="${XDG_CACHE_HOME:-${HOME}/.cache}/archivey/unrarsrc"

usage() {
  sed -n '2,19p' "$0" | sed 's/^# \?//'
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

if ! command -v c++ >/dev/null 2>&1; then
  echo "install-rarlab-unrar: need a C++ compiler (macOS: Xcode CLI tools)" >&2
  exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "install-rarlab-unrar: curl is required to fetch ${UNRAR_URL}" >&2
  exit 1
fi

mkdir -p "$CACHE_DIR"
tarball="${CACHE_DIR}/unrar-${UNRAR_COMMIT}.tar.gz"

verify_sha256() {
  local file="$1"
  local got
  if command -v sha256sum >/dev/null 2>&1; then
    got="$(sha256sum "$file" | awk '{ print $1 }')"
  elif command -v shasum >/dev/null 2>&1; then
    got="$(shasum -a 256 "$file" | awk '{ print $1 }')"
  else
    echo "install-rarlab-unrar: need sha256sum or shasum" >&2
    return 1
  fi
  if [ "$got" != "$UNRAR_SHA256" ]; then
    echo "install-rarlab-unrar: sha256 mismatch for $file" >&2
    echo "  got  $got" >&2
    echo "  want $UNRAR_SHA256" >&2
    return 1
  fi
}

if [ -f "$tarball" ] && verify_sha256 "$tarball"; then
  echo "install-rarlab-unrar: using cached tarball $tarball"
else
  echo "install-rarlab-unrar: fetching $UNRAR_URL"
  curl -fsSL "$UNRAR_URL" -o "$tarball"
  if ! verify_sha256 "$tarball"; then
    rm -f "$tarball"
    exit 1
  fi
fi

workdir="$(mktemp -d "${TMPDIR:-/tmp}/unrarsrc.XXXXXX")"
cleanup() { rm -rf "$workdir"; }
trap cleanup EXIT

tar -xf "$tarball" -C "$workdir"
# GitHub archive/<sha>.tar.gz extracts to unrar-<sha>/ (files at repo root).
src="${workdir}/unrar-${UNRAR_COMMIT}"
if [ ! -f "${src}/makefile" ]; then
  echo "install-rarlab-unrar: expected makefile at ${src}/makefile" >&2
  exit 1
fi

jobs="$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)"
echo "install-rarlab-unrar: compiling UnRAR ${UNRAR_VERSION} (${UNRAR_COMMIT:0:12}) with ${jobs} jobs"
make -C "$src" -j"$jobs"

# macOS install(1) has no GNU -D; copy the binary ourselves.
cp "${src}/unrar" "${DEST}/unrar"
chmod +x "${DEST}/unrar"
echo "install-rarlab-unrar: installed ${DEST}/unrar"
