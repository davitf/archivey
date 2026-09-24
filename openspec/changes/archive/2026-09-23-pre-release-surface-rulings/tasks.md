## 1. `extract_all(config=)`

- [x] 1.1 Drop the parameter from `ArchiveReader.extract_all` and `BaseArchiveReader.extract_all`; `limits=` stays
- [x] 1.2 Tests: a `config=` is a `TypeError`; the open-time listing caps still govern the call

## 2. `strict_archive_eof`

- [x] 2.1 Remove the field; `ARCHIVE_EOF_MARKER_MISSING` emits with no escalation
- [x] 2.2 Trailing-bytes scan always runs, bounded at `_MAX_TRAILING_SCAN` (1 MiB); `ARCHIVE_TRAILING_DATA` emits with no escalation
- [x] 2.3 A tail that fails to decode ends the scan without an error
- [x] 2.4 `DiagnosticCollector.emit` docstring: `escalate_as` raises whatever the disposition
- [x] 2.5 Tests moved from the flag to `RAISE` / `DiagnosticPolicy.strict()`; the bound tested on both sides

## 3. Raw `.bin` images

- [x] 3.1 Sync-pattern magic; probe and refusal in `IsoReadBackend.open_read`, before `pycdlib` is needed
- [x] 3.2 Tests for every layout, detection, explicit `format=`, stream position
- [x] 3.3 `formats.md` and claims E-47 describe the refusal; the reading notes move to `IDEAS.md`

## 4. `__module__` pin

- [x] 4.1 `__init__` pins every class and function in `__all__` defined under `internal`
- [x] 4.2 Tests: no public name reports `internal`; pickles name `archivey`
- [x] 4.3 Checked the built API docs before and after: unchanged (griffe is static)

## 5. Docs and sibling changes

- [x] 5.1 `formats.md`, `gotchas.md`, `CHANGELOG.md`, `known-issues.md`, ADR 0015 amendment
- [x] 5.2 Patch the unarchived changes that restate the touched `archive-reading` and `diagnostics` requirements, so archiving them later does not restore the removed text

## 6. Verify

- [x] 6.1 `./scripts/check.sh`
- [x] 6.2 `./scripts/test.sh --all-configs`
- [x] 6.3 Dry-run the archive and read every removed line in the `openspec/specs/` diff
- [x] 6.4 `openspec archive pre-release-surface-rulings --yes`
