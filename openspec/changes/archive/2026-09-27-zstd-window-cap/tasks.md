## 1. zstd window cap

- [x] 1.1 Pass `window_log_max` from `max_decoder_memory` to the zstd decoder
- [x] 1.2 Map the window refusal to `ResourceLimitError`, or to `UnsupportedFeatureError`
      at libzstd's ceiling
- [x] 1.3 Tests in `tests/test_decoder_limits.py`; docs and handbook updated
