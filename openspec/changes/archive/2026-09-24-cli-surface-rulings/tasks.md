# Tasks — CLI surface rulings

## 1. Code

- [x] 1.1 `detect_format` returns `FormatInfo(DIRECTORY, CERTAIN, "directory")` for a directory path instead of raising `IsADirectoryError`, so `archivey info <dir>` works.
- [x] 1.2 `open_archive` records `PASSWORD_ARGUMENT_UNUSED` only for a concrete password (a value or a sequence), not for a provider callable.
- [x] 1.3 No code change for `./x`: the spec and `docs/cli.md` now say what the code does (a path-qualified token is a path).

## 2. Proof and documents

- [x] 2.1 Tests: `tests/test_cli.py` (`info <dir>`, no password warning on a default TAR run), `tests/test_diagnostics.py` (provider on GZ/TAR/directory, string still recorded), `tests/test_tar.py`, `tests/test_detection*.py`.
- [x] 2.2 `docs/cli.md`, `open_archive` docstring.
- [x] 2.3 `openspec validate --strict cli-surface-rulings`, then archive this change.
