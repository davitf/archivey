# Tasks — gzip reports no CRC

## 1. Code

- [x] 1.1 `SingleFileReader` stops scanning the whole compressed file at open to prove a gzip has one member; `GzipCodec.extract_metadata` no longer surfaces the trailer CRC.
- [x] 1.2 No read-time CRC either (maintainer decision on PR 441): after a full read the decoder has already checked every member's CRC, so a digest added then serves neither dedupe nor verification.

## 2. Proof and documents

- [x] 2.1 Tests in `tests/test_single_file.py`: no CRC listed or after a full read (one member, a second member, NUL padding; path and `BytesIO`); opening reads a bounded prefix. Guardrail in `tests/test_review_simplicity_consistency.py` with the accelerator OFF and ON.
- [x] 2.2 `docs/formats.md` single-file section.
- [x] 2.3 Validate, then archive this change.
