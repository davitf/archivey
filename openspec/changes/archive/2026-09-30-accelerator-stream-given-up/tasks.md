## 1. Accelerated streams

- [x] 1.1 `_AcceleratorStream` moves the decoder back after a read that raised
- [x] 1.2 Give the stream up, with a per-cause `ReadError`, when it cannot, or when the
      caller's source faulted during a read or seek
- [x] 1.3 Tests in `tests/test_accelerator_corruption.py`
