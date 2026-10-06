## 1. Remove the top-level `extract()`

- [x] 1.1 Delete `extract` from `archivey.core`, the package root and `__all__`; fix docstrings that named it
- [x] 1.2 Move tests to `open_archive()` + `extract_all()`; drop tests of behaviour only `extract()` had
- [x] 1.3 Update `docs/`, `README.md`, `AGENTS.md` and `dev-docs/` call sites; drop it from the API reference
- [x] 1.4 Record the decision in ADR 0019
- [x] 1.5 Archive this change
