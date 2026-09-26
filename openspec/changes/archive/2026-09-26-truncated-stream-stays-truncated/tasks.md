# Tasks — a truncated decompressing stream stays truncated

## 1. Stream

- [x] 1.1 Record the raised deferred error; re-raise it from later reads and from
      `seek(0, SEEK_END)`; clear it when a seek restarts the decoder.
- [x] 1.2 Release the dropped prefix before raising; re-raise without the old traceback.

## 2. Tests and docs

- [x] 2.1 Red-green tests in `tests/test_codecs.py`.
- [x] 2.2 `CHANGELOG.md`.
- [x] 2.3 Archive this change.
