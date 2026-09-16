# External ZIP fixtures

Archives produced by **other** tools, committed because we cannot generate them here.
Everything else in `tests/fixtures/` is built at test time; these two exist because the
producer is the point.

## `encoding_infozip_jules.zip`

Info-ZIP output whose member names are valid UTF-8 with general-purpose bit 11 *not* set —
the case `tests/test_zip.py::test_unflagged_utf8_name_is_sniffed` exists for. Neither
stdlib `zipfile` nor `7z` writes this combination.

## `aes_ae1_pyzipper036.zip`

A WinZip **AE-1** member (method 99, extra `0x9901` vendor version 1, AES-256, DEFLATE),
password `secret`, one entry `secret.txt`.

Written with `pyzipper` 0.3.6 on 2026-09-03. AE-1 keeps the plaintext CRC in the headers;
AE-2 zeroes it. No tool on the test image writes AE-1 — `7z -mem=AES256` emits AE-2 — so
without this fixture every AE-1 case in `tests/test_zip_aes.py` would be bytes we
assembled and then read back ourselves.

`pyzipper` is not a dependency, and this is not a hostage to its behaviour: it is a
snapshot of what versions 0.3.0 (2019-02) through 0.3.6 (2022-07) emitted for *every* AES
member regardless of size. 0.3.6 was the only release available until 0.4.0 switched the
default to AE-2 on 2026-05-14, and stdlib `zipfile` writes no encryption at all, so a
Python program emitting an AES ZIP in those four years was almost certainly emitting AE-1.
0.4.0 still emits AE-1 under its opt-in `conditionally_include_crc` for members of at
least 20 bytes.

See [`dev-docs/formats/zip.md`](../../../dev-docs/formats/zip.md) §3.
