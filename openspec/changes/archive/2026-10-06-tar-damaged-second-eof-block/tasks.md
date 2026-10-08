## 1. Keep the listing when the second end block is damaged

- [x] 1.1 `tar_reader.py`: record a zero-block stop; report a non-null block after it
      as `ARCHIVE_EOF_MARKER_MISSING` under the ordinary policy
- [x] 1.2 Tests: `tests/test_tar.py`, both access modes, gzip, strict, `extract_all`
- [x] 1.3 Docs: `dev-docs/formats/tar.md`, note on the 2026-07-19 design
- [x] 1.4 `openspec validate --strict tar-damaged-second-eof-block`
- [x] 1.5 `openspec archive tar-damaged-second-eof-block --yes`
