## MODIFIED Requirements

### Requirement: Bytes after a compressed stream are read past and reported

For gzip, zlib, bzip2, xz, lzip, LZMA Alone, zstd, LZ4 and Brotli, a
member read SHALL deliver the whole payload of every stream before the end, and SHALL
then stop reading the source. Bytes after the end SHALL be classified this way:

- the start of another stream of the same codec is more data and is read: its magic,
  or for LZMA Alone, which has none, a properties byte liblzma decodes (`lc + lp` at
  most 4) and a zero first byte of range-coder data, as `lzma.LZMAFile` reads a second
  stream. Bytes that pass this check but are not a valid stream fail the read with
  `CorruptionError`;
- zero bytes are padding and SHALL NOT be reported where they run to the end of the
  source. For every codec but xz, whose format defines Stream Padding between streams,
  they are padding only there: after zero bytes, the first non-zero byte is trailing
  data as the next bullet says, also when it starts another stream, in every
  accelerator mode. GNU `gzip`, `bzip2`, `zstd`, `lz4`, `xz --format=lzma` and 7-Zip
  stop there too;
- anything else is trailing data: the system SHALL emit one `ARCHIVE_TRAILING_DATA`
  per opened member stream, with `expected_marker="end_of_stream"`, the codec name as
  `format`, and the offset of the first non-zero byte after the end as
  `observed_bytes`. A re-read after a seek SHALL NOT report again.

Under the default policy the diagnostic is a warning and the read succeeds. Under
`DiagnosticPolicy.strict()` it raises `DiagnosticRaisedError` from the read that
reaches it.

The report SHALL come only from a bare single-file read, a compressed TAR's codec,
and `open_stream`. A codec opened inside a ZIP or 7z member, or for detection or a
metadata probe, SHALL stop at the end without reporting.

xz and lzip SHALL find their index when non-zero trailing bytes follow it, by
searching back from the end of the source at most `TRAILING_DATA_SEARCH` (1 MiB) and
checking at most `TRAILING_DATA_CANDIDATES` (4096) candidate ends there. A footer
further out, or behind more candidates, is not found: the index is reported unreadable
(`SEEK_INDEX_DEGRADED` on a seek) and the size is unknown, while a forward read still
delivers the payload and reports the trailing data. A footer (xz) or trailer (lzip)
whose following bytes start another stream, as the first bullet above classifies
them, is not the last one, so the search SHALL NOT stop there: when the last stream's
own footer or trailer is damaged, the index is reported unreadable, the size and CRC
are unknown, and the read or seek that reaches the damage raises `CorruptionError`.

Brotli's decoder fails on input past the end the same way it fails on damage. The
system SHALL tell them apart by decoding the source again from the start up to the
failure, one byte at a time over the failing region; a stream that ends before the
failure has trailing data, anything else is corruption. On a non-seekable source it
cannot re-read, and the failure SHALL stay `CorruptionError`.

`.Z` (unix-compress) is outside this requirement: it has no end marker, so bytes
after the data decode as more codes.

#### Scenario: trailing data after a single-file stream

| Case | Expected |
| --- | --- |
| Valid stream of any listed codec + `b"appended signature\n"` | Full payload; one `ARCHIVE_TRAILING_DATA`, `observed_bytes` = compressed length |
| Same, `DiagnosticPolicy.strict()` | The read raises `DiagnosticRaisedError` |
| Valid stream + 4096 zero bytes | Full payload; no diagnostic |
| Valid stream + zeros + junk | One report at the first non-zero byte |
| Two concatenated `.gz` / `.bz2` / `.lzma` / `.zst` / `.lz4` streams + junk | Both payloads; one report after the second |
| `.gz` / `.bz2` / `.zst` / `.lz4` / `.lzma` / `.lz` stream + zero bytes + another stream, accelerator `OFF`, `AUTO` or `ON` | The first payload; one report at the second stream's first byte; under strict the read raises |
| Two `.xz` streams with zero bytes between them | Both payloads; no report (xz Stream Padding) |
| `.bz2` stream + empty streams + zero bytes | Full payload; no diagnostic |
| `.zst` with a skippable frame between two frames | Both payloads; no report |
| `.xz` / `.lz` + junk within 1 MiB | Size known, seek works, full payload, one report |
| Two concatenated `.xz` streams (with or without stream padding) or `.lz` members, last footer or trailer damaged | Size and CRC unknown; data up to the damage, then `CorruptionError` from the read or `SEEK_END` |
| `.lz` member + zero bytes + a damaged member | Size and CRC of the first member; its payload and one report |
| `.xz` / `.lz` + more than 1 MiB of junk | Size unknown; seeking reports `SEEK_INDEX_DEGRADED`; payload and one report |
| `.lz` + 900 000 zero bytes + junk | Size known, seek works, full payload, one report |
| `.xz` + 1 MiB of `00 00 59 5A`, `.lz` + 1 MiB of 8 zero bytes and one byte, repeated | Size unknown; payload and one report, after at most 4096 candidates checked |
| `.tar.xz` with junk after the xz stream | Members read; one report with `format="xz"` |
| Brotli + junk from a pipe | `CorruptionError` |
| Brotli damaged mid-stream | `CorruptionError` |
| `.Z` + junk | Not this requirement |
