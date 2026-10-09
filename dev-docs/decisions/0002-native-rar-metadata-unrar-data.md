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
- Refuse silent fallbacks to `unrar-free` / `unar`. (For `unar`, see the amendments
  below: the default `"auto"` uses `unar` when no RARLAB program is installed and the
  `unar` passes a RAR5 check.)
- **Amended 2026-09-26:** `unar` is an **opt-in** second data program
  (`ArchiveyConfig.rar_decompressor="unar"`), requested by the maintainer after the
  2026-09-01 decompressor matrix left it open. It is still never a fallback: selecting it
  with `unar` missing raises `PackageNotInstalledError`, and the default stays `unrar`.
  (The third amendment below makes `"auto"` the default.)
  The reads `unar` gets wrong are refused from the native listing before it runs
  (`internal/backends/rar_unar.py`); the process layer (`internal/external/`) is
  format-agnostic so `unar` can later serve other formats.
- **Amended again 2026-09-26:** at the maintainer's request, `"auto"` uses RARLAB
  `unrar` when it is installed and `unar` otherwise. The choice is made once at open,
  never per read; that part was an implementation choice, not the maintainer's
  (2026-09-28), and a per-member fallback is an open question in `formats/rar.md`. `unar` now reads encrypted RAR5 data, with the password on its command line
  (visible to local users; documented in `docs/formats.md`). Encrypted RAR 2.x-4.x data
  and non-ASCII passwords stay refused under `unar`, because `unar` 1.10 returns no data
  for them.
- **Amended a third time 2026-09-26:** `"auto"` is the default (maintainer: "auto is
  default"). A machine with `unar` and no RARLAB program now reads RAR member data with
  `unar` instead of raising; `"unrar"` keeps the RARLAB-only behaviour. This supersedes
  "the default stays `unrar`" above; `unrar-free` and `7z` are still never used.
- **Amended 2026-10-01:** at the maintainer's direction, only a `unar` that decodes
  correctly is used. Each `unar` decodes a small RAR5 archive once
  (`internal/external/unar.py`, `unar_rar5_probe_failure`) and is refused unless it
  writes the member exactly with exit 0. Debian and Ubuntu packages before
  1.10.8+ds1-10 (Ubuntu 22.04 to 26.04; Debian 13 expected) carry a patch that drops one
  compressed RAR5 member in about 25 and fail the check. So on those machines the third
  amendment no longer holds: with no RARLAB program, `"auto"` raises the `unrar`
  refusal as if `unar` were absent, and `"unar"` raises a refusal naming the patch.
  No setting accepts a refused `unar`. `dev-docs/known-issues.md` has the measurements.
- The spec’s optional “extract-hack” (single-member temp RAR for tiny random opens) is
  **deferred** — allowed by `format-rar` but not implemented in the native reader change.
