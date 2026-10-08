## 1. Stream layout and end handover

- [x] 1.1 `_Bzip2Layout` checks the index before a read returns; the takeover starts at the first gap
- [x] 1.2 The end scan hands a following stream header to the standard library
- [x] 1.3 Tests compare the accelerator with the standard library on each case
- [x] 1.4 `openspec validate --strict bzip2-accelerator-stream-gaps`
- [x] 1.5 `openspec archive bzip2-accelerator-stream-gaps --yes`
