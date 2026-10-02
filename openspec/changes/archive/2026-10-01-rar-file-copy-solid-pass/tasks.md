## 1. Kept file-copy sources

- [x] 1.1 Keep the sources in the solid pass (`unrar` and `unar`), in memory and in a
      temporary file charged to the spool budget from the first written byte
- [x] 1.2 Give the charge back when the pass ends
- [x] 1.3 Tests in `tests/test_rar_file_copy_solid_pass.py`, `tests/test_rar_reader.py`
      and `tests/test_rar_spool_limit.py`; docs and handbook updated
- [x] 1.4 `openspec validate --strict rar-file-copy-solid-pass`
