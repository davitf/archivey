## 1. Detection

- [x] 1.1 Refuse Info-ZIP final `.zip` parts whose EOCD disk fields are non-zero (`0xFFFF` = ZIP64 sentinel, ignored)
- [x] 1.2 Refuse 7-Zip `.zip.NNN` segment names with the same rejoin-first `UnsupportedFeatureError` as `.zNN`
- [x] 1.3 Keep plain, ZIP64, and prefixed single-volume ZIPs openable

## 2. Spec / handbook

- [x] 2.1 Collapse the `format-zip` multi-volume scenario matrix (keep MUST-refuse prose)
- [x] 2.2 Update `dev-docs/formats/zip.md` §5 (false rows) and §7 (new Verify links)

## 3. Verify

- [x] 3.1 Red–green tests in `tests/test_zip.py` (synthesised; no `zip` binary gate)
- [x] 3.2 `openspec validate --strict zip-split-volume-detection`
- [x] 3.3 `openspec archive zip-split-volume-detection --yes`
