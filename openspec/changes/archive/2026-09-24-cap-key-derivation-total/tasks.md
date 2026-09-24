## 1. Implementation

- [x] 1.1 Add `DecoderLimits.max_key_derivation_rounds` (default `2**27`, `None` in `UNLIMITED`), validated like the other limit fields
- [x] 1.2 Add a per-reader `KeyDerivationBudget` and charge it on each miss in `RarKdfCache` (RAR5, RAR3) and `SevenZipKeyCache`
- [x] 1.3 Let `ResourceLimitError` through the RAR header walks' `EncryptionError` re-wrap
- [x] 1.4 Tests: exact boundary on a header-encrypted volume set, wrong candidates charged, member reads charged once, RAR3, 7z
- [x] 1.5 Docs: `DecoderLimits` docstring, `extracting.md` limits, `ResourceLimitError`, changelog, threat-model O18, 7z handbook open question, IDEAS preset entry
