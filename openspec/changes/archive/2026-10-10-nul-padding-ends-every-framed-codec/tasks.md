## 1. Zero bytes between zstd, LZ4 and LZMA Alone streams

- [x] 1.1 `FramedDecoder` ends the data at zero bytes that something follows, for every
      codec it decodes
- [x] 1.2 Tests in `tests/test_stream_trailing_data.py`; handbook pages and
      `design-rules.md` updated
- [x] 1.3 `openspec validate --strict nul-padding-ends-every-framed-codec`, then archive
