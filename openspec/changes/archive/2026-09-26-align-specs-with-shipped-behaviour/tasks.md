## 1. Align the specs

- [x] 1.1 `backend-registry`: RAR support does not count the `unrar` binary; ZIP is FULL
      with every optional member codec installed
- [x] 1.2 `safe-extraction`: `STANDARD` strips setuid, setgid and sticky
- [x] 1.3 `testing-contract`: `py7zr` is the only 7z oracle; the `7z` CLI builds fixtures
- [x] 1.4 `safe-extraction`: `STANDARD` keeps uid/gid on the transformed member; only
      `TRUSTED` as root applies ownership
- [x] 1.5 `format-detection` / `detection-cost`: `cost_receipt` and `unavailable_tiers` are
      public `FormatInfo` fields; `corroborated` is not part of the contract

## 2. Verify

- [x] 2.1 Check each statement against the code (`format_availability`, `_HIGH_BITS`,
      `tests/test_sevenzip_oracle.py`, `transform_standard`, `detect_format`)
- [x] 2.2 `openspec validate --strict align-specs-with-shipped-behaviour`
- [x] 2.3 `openspec archive align-specs-with-shipped-behaviour --yes`
