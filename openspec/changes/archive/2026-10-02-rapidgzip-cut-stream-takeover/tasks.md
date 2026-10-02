## 1. Takeover from a checkpoint

- [x] 1.1 `deflate_resume.py`: zlib resumes at a bit offset with a preset window
- [x] 1.2 The child stream keeps a checkpoint from its index and the output it sent
- [x] 1.3 `_StdlibOnAcceleratorError` takes over on a child crash, starting at the checkpoint
- [x] 1.4 A resumed decode that reaches a stream end starts over from the start
- [x] 1.5 Tests: `tests/test_deflate_resume.py`, `tests/test_rapidgzip_resume.py`
- [x] 1.6 Docs: `dev-docs/formats/gzip.md` §2.3, `dev-docs/known-issues.md` Bug 4,
      `dev-docs/investigations/rapidgzip-upstream-report.md` §2, and the published
      `docs/access-and-cost.md` and `docs/gotchas.md`
- [x] 1.7 `openspec validate --strict rapidgzip-cut-stream-takeover`
- [x] 1.8 `openspec archive rapidgzip-cut-stream-takeover --yes`
