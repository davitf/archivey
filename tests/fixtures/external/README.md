# External fixtures

Archives and streams produced by **other** tools, committed because we cannot generate
them here. Everything else in `tests/fixtures/` is built at test time; these exist
because the producer is the point.

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

## `lzma_bpo21872/`

Three real LZMA Alone (`.lzma`) streams from the attachments on CPython's BPO-21872,
"LZMA library sometimes fails to decompress a file" — files that stdlib `lzma` used to
decompress *short* while the `xz` utility read them whole. Committed 2026-09-19.

They are a shape nothing on the test image writes: **the declared uncompressed size is
known and there is no end-of-payload marker**, so a decoder has to stop at the size rather
than at a marker. stdlib `FORMAT_ALONE` always writes the unknown-size marker instead, and
`tests/test_single_file.py::test_lzma_alone_size_from_header_when_known` cannot fill the
gap: it patches the size field into stdlib output, and that contradictory header (size
*and* end marker) is rejected as corrupt by some liblzma builds, so it must not be read
back.

| File | Source attachment | Notes |
| --- | --- | --- |
| `22h_ticks_bad.bi5` | `Archive.zip` (2014-06-25) | The original report's failing file. Dukascopy tick data. Also shipped as `14_22h_ticks.bi5` in `more_bad_lzma_files.zip` — byte-identical. |
| `23h_ticks_good.bi5` | `Archive.zip` (2014-06-25) | The control uploaded beside it: same producer, never failed. |
| `failed_file_01.lzma` | `failed_files_more.zip` (2017-12-24) | A later report, different submitter and three years apart. |

CPython fixed this in 3.7 (PR 14048, the `needs_input` handling in the decompress reader),
so all three decode correctly today — `tests/test_single_file.py::test_bpo21872_lzma_alone_samples_decode_whole`
is a regression pin on our own stream layer, which chunks reads differently from
`lzma.open`, not a live bug hunt. The bug was sensitive to where reads landed relative to
internal buffer boundaries, so the test reads each file at several chunk sizes, 8192
among them.

Only three of the nineteen attached files are committed; the rest are the same shape from
the same two sources.
