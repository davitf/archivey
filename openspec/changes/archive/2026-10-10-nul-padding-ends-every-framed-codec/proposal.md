# Zero bytes between zstd, LZ4 and LZMA Alone streams end the data

## Why

gzip and bzip2 now stop at zero bytes between streams (change
`nul-padding-ends-gzip-bzip2-data`). zstd, LZ4 and LZMA Alone still read a stream after
zero bytes. Their official tools do not: on a stream, 4 zero bytes, a stream, `zstd -dc`
1.5.5 writes the first payload and fails with "unsupported format", `lz4 -dc` 1.9.4
writes it and exits 1, and `xz --format=lzma -dc` 5.4.5 writes it and fails with
"Compressed data is corrupt". 7-Zip stops on the `.lzma`. The maintainer ruled that the
same rule applies to these codecs (2026-10-10).

## What changes

For every codec but xz, zero bytes are padding only where they run to the end of the
source. After them, the first non-zero byte is `ARCHIVE_TRAILING_DATA`, also when it
starts another stream; strict raises. lzip already behaved so. xz keeps reading past its
Stream Padding, which its format defines. Skippable zstd frames right after a frame are
still part of the data.

Zero bytes at the end of the file stay silent, although `zstd`, `lz4` and
`xz --format=lzma` refuse them: tape, `dd` and `tar` pad files to a block size, and one
rule holds for every codec.

`FramedDecoder` loses its `padding_ends_data` option: the rule is now the only one for
every codec it decodes, so the option has nothing left to choose.

## Impact

- `format-single-file-compressors`: the trailing-data requirement names xz as the one
  exception, with scenario rows.
- Code: `framed_decoder.py`, `bzip2_codec.py`.
