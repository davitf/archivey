## 1. Chain decode

- [x] 1.1 `walk_chain` reports where the walk stopped at a compressed block;
      `BrotliCodec.content_probe` decodes `[0, compressed_at + CHAIN_DECODE_MARGIN)` when
      the window decode did not reach it, within the 1 MiB reach
- [x] 1.2 `charge_decode` probe argument; detection backs it with `max_decode_input` and
      the read ceiling, and records `content_probe_decode`
- [x] 1.3 `read_at` reuses prefix bytes; probe samples are served 4 KiB per read
- [x] 1.4 Tests in `tests/test_brotli_chain_decode.py`; residual fixtures moved to a
      chain past the link cap; handbook pages updated
- [x] 1.5 `openspec validate --strict brotli-probe-decodes-to-compressed-block`, then
      archive
