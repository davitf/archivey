# Tasks — prune the public exception types

## 1. Library

- [x] 1.1 Remove the nine classes from `archivey.exceptions` and `archivey.__all__`;
      rewrite the docstrings of `OpenError`, `ReadError`, `FilterRejectionError`,
      `UnsupportedFeatureError`, `PackageNotInstalledError` and `ArchiveyUsageError`
- [x] 1.2 Move every raise site to its surviving type (filters, extraction, spool,
      reader state, base reader, diagnostics collector, registry, `unrar` argv, `open_stream`)

## 2. Tests and tools

- [x] 2.1 Update every test that names a removed type
- [x] 2.2 Update `scripts/exploration/capability_declaration_sweep.py`

## 3. Docs

- [x] 3.1 `docs/`: the errors table, the API page, and every page naming a removed type
- [x] 3.2 ADR 0012 amendment; dev-docs pages; `CHANGELOG.md`

## 4. Archive

- [x] 4.1 `openspec archive prune-exception-types --yes`
