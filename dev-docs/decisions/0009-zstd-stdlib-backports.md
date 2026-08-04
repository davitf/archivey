# 0009 — Zstd via stdlib / `backports.zstd`, not `zstandard`

- **Status:** accepted
- **Date:** 2026-07 (`zstd-stdlib-backend-migration`)
- **Provenance:** `dev-docs/library-analysis.md`; OpenSpec `packaging-and-extras`

## Context

`zstandard` (CFFI) silently short-read truncated frames in measured probes. CPython 3.14
added `compression.zstd`; `backports.zstd` mirrors that API on older Pythons.

## Decision

Use stdlib `compression.zstd` on 3.14+; `backports.zstd` on earlier versions, shipped in
`[recommended]` (recorded as a dedicated `[zstd]` extra, consolidated before `0.2.0`). Do
not pin `zstandard` or `pyzstd` in user-facing extras.

## Consequences

- Truncation raises instead of silent short reads.
- Seekable zstd (`indexed_zstd`) remains separate / backlog (accelerator coexistence
  concerns).
