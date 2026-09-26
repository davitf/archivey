## 1. Align the specs

- [x] 1.1 `backend-registry`: RAR support does not count the `unrar` binary; ZIP is FULL
      with every optional member codec installed
- [x] 1.2 `safe-extraction`: `STANDARD` strips setuid, setgid and sticky
- [x] 1.3 `testing-contract`: `py7zr` is the only 7z oracle; the `7z` CLI builds fixtures

## 2. Verify

- [x] 2.1 Check each statement against the code (`format_availability`, `_HIGH_BITS`,
      `tests/test_sevenzip_oracle.py`)
- [x] 2.2 `openspec validate --strict align-specs-with-shipped-behaviour`
- [x] 2.3 `openspec archive align-specs-with-shipped-behaviour --yes`
