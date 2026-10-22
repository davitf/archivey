## 1. Implement

- [x] 1.1 Retry a refused overlap once after `gc.collect()` on every interpreter
  (`reader_state._retry_after_collecting`); tests in `tests/test_reader_state.py`

## 2. Verify

- [x] 2.1 `openspec validate --strict dropped-pass-is-not-an-overlap`
- [x] 2.2 Archive the change
