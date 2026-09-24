# Tasks — public surface hub fixes

## 1. Code

- [x] 1.1 `open_archive` records the resolved source's path, not the caller's argument, as the re-detection source for an asserted `format=`.
- [x] 1.2 `ArchiveFormat.__post_init__` converts string fields to enum members.
- [x] 1.3 `archivey.__version__` is computed on first access.
- [x] 1.4 `DiagnosticPolicy.__hash__`, so a frozen `ArchiveyConfig` hashes.
- [x] 1.5 Config and limit fields carry attribute docstrings, so the API reference renders them.

## 2. Proof and documents

- [x] 2.1 Red-green tests in `tests/test_probe_provenance_unconfirmed.py`, `tests/test_public_types.py`, `tests/test_public_api.py`, `tests/test_archivey_config.py`.
- [x] 2.2 `openspec validate --strict public-surface-hub-fixes`, then archive this change.
