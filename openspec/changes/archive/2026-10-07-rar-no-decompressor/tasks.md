## 1. RarDecompressor.NONE

- [x] 1.1 Add `RarDecompressor.NONE`; the RAR reader skips the open-time probe, leaves
      compressed old-style comments `None`, reads stored plaintext members directly and
      refuses every other read with `UnsupportedFeatureError` before any process starts
- [x] 1.2 Cost note at open when a file member will be refused
- [x] 1.3 Tests in `tests/test_rar_no_decompressor.py`; docs updated
- [x] 1.4 `openspec validate --strict rar-no-decompressor` (one length warning, like its neighbours)
- [x] 1.5 `openspec archive rar-no-decompressor --yes`
