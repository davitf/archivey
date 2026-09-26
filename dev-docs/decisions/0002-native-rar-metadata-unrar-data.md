# 0002 — Native RAR metadata; system unrar for member data

- **Status:** accepted
- **Date:** 2026-07
- **Provenance:** `VISION.md`; `dev-docs/history/ARCHITECTURE.md` §5.7; OpenSpec `format-rar`

## Context

`rarfile` couples listing to its decompressor stack and does not match Archivey’s
streaming / cost model cleanly. RAR compression is proprietary; a full native
decompressor is out of scope.

## Decision

Parse RAR3/RAR5 **metadata natively** (list without `unrar`). Decompress member **data**
via the RARLAB `unrar` binary (process boundary). Decrypt encrypted headers natively via
`cryptography` (the `[recommended]` extra; recorded as `[crypto]` / `[rar]`, consolidated
before `0.2.0`). Keep `rarfile` as a test oracle only.

## Consequences

- Listing works without `unrar`; reading compressed members requires it on `PATH`.
- Solid `stream_members()` uses one `unrar p` pipe, not one process per member.
- Refuse silent fallbacks to `unrar-free` / `unar`.
- **Amended 2026-09-26:** `unar` is an **opt-in** second data program
  (`ArchiveyConfig.rar_decompressor="unar"`), requested by the maintainer after the
  2026-09-01 decompressor matrix left it open. It is still never a fallback: selecting it
  with `unar` missing raises `PackageNotInstalledError`, and the default stays `unrar`.
  The reads `unar` gets wrong are refused from the native listing before it runs
  (`internal/backends/rar_unar.py`); the process layer (`internal/external/`) is
  format-agnostic so `unar` can later serve other formats.
- The spec’s optional “extract-hack” (single-member temp RAR for tiny random opens) is
  **deferred** — allowed by `format-rar` but not implemented in the native reader change.
