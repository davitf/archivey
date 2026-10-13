## 1. Policy as two code sets

- [x] 1.1 `DiagnosticPolicy(ignore=, raise_on=)` with `STRICT` / `PEDANTIC` instances in
      `src/archivey/diagnostics.py`; `overrides=`, `default=`, `strict()`, `pedantic()`
      removed
- [x] 1.2 Tests converted; new tests for the both-sets refusal, the bare-string refusal,
      string spellings, keyword-only fields and preset equality
- [x] 1.3 Docs, handbook pages and spec references updated
- [x] 1.4 `openspec validate --strict diagnostic-policy-code-sets`, then archive
