# 0006 — Stdlib `zipfile` for the ZIP core

- **Status:** accepted
- **Date:** early architecture
- **Provenance:** `dev-docs/history/ARCHITECTURE.md` §5.1; OpenSpec `format-zip`

## Context

Alternatives (`python-libarchive-c`, etc.) offer speed or broader edge-case coverage at
the cost of native dependencies and packaging pain.

## Decision

Use stdlib `zipfile` for core ZIP read/write. Document gaps (multi-disk sets rejected;
some methods unsupported at read). Optional native/streaming ZIP reader remains backlog
(`IDEAS.md`).

## Consequences

- Zero-dep ZIP path; seekable sources only for this backend.
- Multi-disk / spanned ZIP (Info-ZIP `.zNN` + `.zip`, or EOCD / ZIP64 disk fields
  declaring more than one disk) → clear `UnsupportedFeatureError`.

*Amended 2026-09-26:* a 7-Zip `-v` split set (`.zip.001`…) is no longer rejected.
Its parts are byte slices of one ordinary ZIP, so archivey joins them and `zipfile`
reads the result; a missing part raises `TruncatedError`. Only multi-disk sets, whose
entries are addressed by disk number, stay refused.
