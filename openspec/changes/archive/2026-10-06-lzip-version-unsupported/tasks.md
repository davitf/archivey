## 1. Refuse other lzip versions

- [x] 1.1 `lzip.py`: the forward decoder and the backward walk refuse a version other
      than 1 with `UnsupportedFeatureError`; a full `LZIP` magic starts a member
- [x] 1.2 Tests: `tests/test_seekable_streams.py`, `tests/test_single_file.py`
- [x] 1.3 Docs: `docs/formats.md`, `dev-docs/formats/xz.md`
- [x] 1.4 `openspec validate --strict lzip-version-unsupported`
- [x] 1.5 `openspec archive lzip-version-unsupported --yes`
