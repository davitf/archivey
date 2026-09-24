## Why

Four sweep findings had a maintainer ruling before 0.2.0 and none had landed. Each changes
public surface, so each has to land before the release freezes it. All four rulings are
recorded on their threads in the #315 review hub.

## What Changes

- **BREAKING** `ArchiveReader.extract_all()` loses `config=` (S20-K15, ruled 2026-09-20).
  It honoured only `extraction_limits` and silently dropped the rest, a per-call
  diagnostic policy and callback included, against two spec rows that said otherwise.
  `limits=` stays. Those two rows are deleted, not implemented.
- **BREAKING** `ArchiveyConfig.strict_archive_eof` is removed (S21-K8, ruled 2026-09-20).
  `ARCHIVE_EOF_MARKER_MISSING` becomes an ordinary diagnostic with no `TruncatedError`
  escalation. The trailing-bytes scan the flag also gated always runs, bounded at 1 MiB
  past the trailer, and `ARCHIVE_TRAILING_DATA` also becomes ordinary, so
  `DiagnosticPolicy.strict()` raises on both — which it promised and could not deliver.
- Raw CD sector images (`.bin`) are recognised and refused by name (S22-K6, ruled
  2026-09-21, option c). The spec, `formats.md` and a verified claims row all promised
  sector stripping that never existed. Reading raw images is deferred past 0.2.0.
- Every class and function in `__all__` defined under `archivey.internal` reports
  `archivey` as its `__module__` (S20-K13, ruled 2026-09-21), so pickled data does not
  freeze the internal layout.

## Capabilities

### New Capabilities

### Modified Capabilities

- `archive-reading`: config lifetime, `MemberListReport` matrix row
- `safe-extraction`: `extract_all` signature and diagnostics
- `format-tar`: missing-trailer disposition; trailing-bytes check replaces the strict one
- `diagnostics`: `ARCHIVE_TRAILING_DATA` no longer strict-only
- `error-handling`: EOF strictness precedence removed; boundary matrix row
- `documentation`: TAR EOF documentation without the flag
- `format-iso`: raw images refused by name instead of stripped
- `format-detection`: raw sector sync in the magic table
- `packaging-and-extras`: `__module__` pin on public names

## Impact

`reader.py`, `internal/base_reader.py`, `config.py`, `internal/backends/tar_reader.py`,
`internal/backends/iso_reader.py`, `internal/diagnostics_collector.py` (docstring),
`__init__.py`. User docs `formats.md`, `gotchas.md`, `CHANGELOG.md`. Four unarchived
changes restate `archive-reading` or `diagnostics` requirements touched here and are
patched to match, so archiving them later does not bring the removed text back.
