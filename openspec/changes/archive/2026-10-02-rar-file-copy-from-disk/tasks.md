## 1. File copies from disk, and no copy streams on request

- [x] 1.1 `FileCopySources` asks whether to keep a source at its first decoded byte;
      extraction declines the sources it writes from the pass
- [x] 1.2 Extraction copies a file copy from its source's written file, checked on the
      opened file, and falls back to the copy's stream otherwise
- [x] 1.3 `stream_members(file_copy_streams=False)`: `None` for copies, nothing kept
- [x] 1.4 Tests in `tests/test_rar_file_copy_solid_pass.py`; docs and handbook updated
- [x] 1.5 `openspec validate --strict rar-file-copy-from-disk`
