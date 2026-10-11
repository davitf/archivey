## 1. Zero bytes between members or streams

- [x] 1.1 `FramedDecoder(padding_ends_data=True)` for bzip2 and `GzipDecoder` end the
      data at zero bytes that something follows
- [x] 1.2 The bzip2 accelerator's end scan and layout walk accept zeros only at the end
- [x] 1.3 Tests in `tests/test_stream_trailing_data.py`, `tests/test_codecs.py` and
      `tests/test_accelerator_corruption.py`; handbook pages and `design-rules.md` updated
- [x] 1.4 `openspec validate --strict nul-padding-ends-gzip-bzip2-data`, then archive
