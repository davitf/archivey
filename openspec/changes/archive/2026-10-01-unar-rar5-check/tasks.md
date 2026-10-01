## 1. Refuse a unar that drops compressed RAR5 members

- [x] 1.1 `find_unar` decodes an embedded RAR5 archive once per binary (`CliToolFinder`
      `verify`) and refuses a `unar` that does not write its member exactly with exit 0
- [x] 1.2 Tests in `tests/test_unar_probe.py`; docs, handbook and ADR 0002 updated
- [x] 1.3 `openspec validate --strict unar-rar5-check`
- [x] 1.4 `openspec archive unar-rar5-check --yes`
