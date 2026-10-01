## 1. RAR dictionary cap

- [x] 1.1 Parse the declared dictionary size (RAR3 file flags, RAR5 compression info)
- [x] 1.2 Count it per program and check it before `unrar` or `unar` starts
- [x] 1.3 Tests in `tests/test_audit_rar_iso_dir.py` and `tests/test_rar_parser.py`;
      docs, handbook and threat model updated
- [x] 1.4 `openspec validate --strict rar-dictionary-memory-cap`
