# Tasks — single-file codecs: read past appended bytes and report them

- [x] 1.1 `trailing_bytes` on the decoder protocol; `DecompressorStream` ends the stream
      there and reports once when `StreamConfig.report_trailing_data` is set.
- [x] 1.2 One-stream library decoders (bzip2, LZMA Alone, zstd, LZ4) framed by stream
      magic; gzip and zlib end at the member; Brotli replays to tell trailing bytes
      from damage.
- [x] 1.3 rapidgzip and the bzip2 accelerator: switch to the standard library on a
      data error or ISIZE mismatch; the bzip2 accelerator reports from its compressed
      position.
- [x] 1.4 xz and lzip indexes found within 1 MiB of trailing bytes.
- [x] 1.5 Tests, docs, handbook.
- [x] 1.6 Archive this change.
