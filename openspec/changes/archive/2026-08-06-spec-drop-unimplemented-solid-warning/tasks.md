## 1. Align the spec with what ships

- [x] 1.1 Drop "and warn to prefer `stream_members()`" from the `stream_members`
      requirement; say the cost is silent and name the two discovery paths
- [x] 1.2 Rewrite the matrix row "Random `open()` into solid block" the same way
- [x] 1.3 Confirm no other spec asserts the warning
      (`grep -rn "warn" openspec/specs/`)

## 2. Put the cost where a caller will meet it

- [x] 2.1 `ArchiveReader.open()` docstring — the `SOLID` cost, that it is quadratic
      over a full pass, that nothing warns, and to prefer `stream_members()`
- [x] 2.2 `ArchiveReader.read()` docstring — same cost, by reference to `open()`
- [x] 2.3 Both render into `docs/api.md`, which documents the ABC in `reader.py`
      rather than the `base_reader.py` implementation

## 3. Verify

- [x] 3.1 `openspec validate --strict spec-drop-unimplemented-solid-warning`
- [x] 3.2 Dry-run archive on a scratch tree; confirm `~1` against the real
      requirement, then reset
- [x] 3.3 `mkdocs build --strict` green (the docstrings render)
- [x] 3.4 Close the docs/specs drift row in `dev-docs/open-issues.md`
