## 1. Takeover from a checkpoint

- [x] 1.1 `deflate_resume.py`: zlib resumes at a bit offset with a preset window
- [x] 1.2 The child stream keeps a checkpoint from its index and the output it sent
- [x] 1.3 `_StdlibOnAcceleratorError` takes over on a child crash, starting at the checkpoint
- [x] 1.4 A resumed decode that reaches a stream end starts over from the start
- [x] 1.5 Tests: `tests/test_deflate_resume.py`, `tests/test_rapidgzip_resume.py`
