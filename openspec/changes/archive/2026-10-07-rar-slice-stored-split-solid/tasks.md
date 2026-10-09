## 1. Slice stored RAR members natively

- [x] 1.1 The parser keeps each split member's parts as `(data_offset, size)` in the
      concatenated volume space; the reader joins them for a stored member
- [x] 1.2 The direct-read rule ignores a stored member's own solid flag
- [x] 1.3 Tests compare the bytes with `unrar`, and run under `rar_decompressor="none"`
- [x] 1.4 `openspec validate --strict rar-slice-stored-split-solid`
- [x] 1.5 `openspec archive rar-slice-stored-split-solid --yes`
