# Tasks — truncation is a kind of corruption

## 1. Library

- [x] 1.1 Make `TruncatedError` a `CorruptionError` subclass; rewrite both docstrings
- [x] 1.2 Audit every `except CorruptionError` and `isinstance(..., CorruptionError)` site
      under `src/` for one that must not catch truncation (none found)
- [x] 1.3 Collapse every `(CorruptionError, TruncatedError)` pair in `src/` and `tests/` to
      `CorruptionError`; comment the two sites where `TruncatedError` must be tested first
      (`zip_reader`, `rar_reader`)

## 2. Tests

- [x] 2.1 Add `tests/corruption_util.py` (`raises_corruption_not_truncation`,
      `is_corruption_not_truncation`)
- [x] 2.2 Move the corruption-only `pytest.raises` and `isinstance` asserts onto it

## 3. Docs

- [x] 3.1 `docs/errors-and-diagnostics.md`: the errors table and the best-effort note
- [x] 3.2 ADR 0012 amendment
