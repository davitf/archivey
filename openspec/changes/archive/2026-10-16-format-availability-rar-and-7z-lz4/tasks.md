## 1. Availability

- [x] 1.1 Add `Codec.LZ4` to the 7z optional codecs in `registry.py`; test the ZIP and 7z
      lists against their method tables
- [x] 1.2 `ReadBackend.missing_programs()`, RAR override via `rarlab_unrar_missing()`,
      called only from the public `format_availability()`
- [x] 1.3 Tests in `tests/test_registry.py`; `docs/install.md` updated
- [x] 1.4 `openspec validate --strict format-availability-rar-and-7z-lz4`, then archive
