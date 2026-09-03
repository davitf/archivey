## 1. Implement

- [x] 1.1 Consult `SUPPORTS_STREAMING_NON_SEEKABLE` before the mode in `open_archive`'s non-seekable branch, so a seek-only format gets the "needs a seekable source" message in both modes instead of a `streaming=True` retry it would refuse
- [x] 1.2 `tests/test_non_seekable_refusal.py`: both sides of the split (seek-only formats never name `streaming=True`; forward-only formats still do), plus the two modes agreeing

## 2. Verify

- [x] 2.1 `docs/access-and-cost.md` names all four seek-only formats and the fix that works
- [x] 2.2 `openspec validate --strict fix-non-seekable-refusal-advice`
- [x] 2.3 Archive this change in the finishing PR (`openspec archive fix-non-seekable-refusal-advice --yes`)
