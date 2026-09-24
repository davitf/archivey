# Tasks — gzip CRC at read

## 1. Code

- [x] 1.1 `SingleFileReader` stops scanning the whole compressed file at open to prove a gzip has one member; the listing shows no gzip CRC.
- [x] 1.2 `GzipDecoder` reports a clean end of input that held exactly one member and nothing after it; the reader then adds the trailer CRC-32 to `member.hashes`.

## 2. Proof and documents

- [x] 2.1 Tests in `tests/test_single_file.py`: no CRC before a read, CRC after a full read (path and `BytesIO`), none after a partial read, none with a second member or NUL padding, no second-member scan at open.
- [x] 2.2 `docs/formats.md` single-file section.
- [x] 2.3 `openspec validate --strict gzip-crc-at-read`, then archive this change.
