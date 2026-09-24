## 1. Public classes in public modules

- [x] 1.1 Extraction types, `FormatSupport`, `FormatAvailability` into `archivey/types.py`; `internal/extraction_types.py` removed
- [x] 1.2 `FormatInfo`, `DetectionConfidence` into new `archivey/detection.py`
- [x] 1.3 Pin docstring and tests: only `ArchiveStream` among classes stays pinned; `getsource` works on the moved classes

## 2. `archivey.terminal`

- [x] 2.1 `archivey/escaping.py` becomes public `archivey/terminal.py`; importers updated; `internal/enum_args.py` stays internal
- [x] 2.2 The CLI stops using the enum-spelling helpers
- [x] 2.3 CONTRIBUTING rule; `tests/test_cli_uses_public_api.py` guard

## 3. Docs and archive

- [x] 3.1 Docs, AGENTS.md, code map and threat model name the new paths
- [x] 3.2 Archive the change
