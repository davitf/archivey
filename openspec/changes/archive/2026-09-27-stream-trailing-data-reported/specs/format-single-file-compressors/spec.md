## ADDED Requirements

### Requirement: Bytes after a compressed stream are read past and reported

For gzip, zlib, bzip2, xz, lzip, LZMA Alone, zstd, LZ4 and Brotli, a
member read SHALL deliver the whole payload of every stream before the end, and SHALL
then stop reading the source. Bytes after the end SHALL be classified this way:

- the start of another stream of the same codec is more data and is read: its magic,
  or for LZMA Alone, which has none, a valid properties byte and a zero first byte of
  range-coder data, as `lzma.LZMAFile` reads a second stream;
- zero bytes are padding and SHALL NOT be reported;
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
delivers the payload and reports the trailing data.

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
| `.zst` with a skippable frame between two frames | Both payloads; no report |
| `.xz` / `.lz` + junk within 1 MiB | Size known, seek works, full payload, one report |
| `.xz` / `.lz` + more than 1 MiB of junk | Size unknown; seeking reports `SEEK_INDEX_DEGRADED`; payload and one report |
| `.xz` + 1 MiB of `00 00 59 5A`, `.lz` + 1 MiB of zeros and one byte | Same, after at most 4096 candidates checked |
| `.tar.xz` with junk after the xz stream | Members read; one report with `format="xz"` |
| Brotli + junk from a pipe | `CorruptionError` |
| Brotli damaged mid-stream | `CorruptionError` |
| `.Z` + junk | Not this requirement |

## MODIFIED Requirements

### Requirement: A single-file source is validated at open, not at first read

`open_archive` on a single-file compressor SHALL establish that the source is decodable as
the detected codec before returning a reader, and SHALL raise the translated error
(`CorruptionError` / `TruncatedError` / the codec's `PackageNotInstalledError`) from
`open_archive` rather than from a later read.

Validation depth is **one decoded byte, or a proof that one cannot exist**:

- the reader SHALL pull at least one byte from a codec stream over the source, because
  every stdlib codec validates its header on first read and not at construction;
- `read` returning empty is accepted as a valid empty stream only because every codec's
  decoder raises on a source too short to hold its own header (`unix-compress` included:
  its decoder raises `TruncatedError` below the 3-byte header) and the accelerated bzip2
  path confirms an empty result with the stdlib decoder. A codec whose decoder reads a
  zero-byte input as an empty stream SHALL reject a source shorter than its minimum
  header on length before it is admitted.

The check SHALL run after `open_archive` has recorded the format's provenance, so an
open-time decode failure is stamped `format_unconfirmed` exactly as a read-time one
would be. The error SHALL NOT name a member, since none was requested.

A genuinely valid empty stream SHALL still open and read as empty.

Non-seekable sources are out of scope: the opened stream is handed to the first
`open_member`, so a probe read there would consume a byte the caller expects. The
obligation applies to seekable sources, and the reader SHALL say so where it is stated.

#### Scenario: open-time validation matrix

| Source, named for each of the ten codecs | Expected |
| --- | --- |
| Valid stream | Opens; member reads its content |
| Valid **empty** stream | Opens; member reads `b""` |
| 40 000 zero bytes | `open_archive` raises `CorruptionError`, except LZMA Alone, whose first 18 zero bytes are a complete empty stream: it opens, reads `b""`, and the rest is padding with no diagnostic |
| Zero-byte source, codec whose decoder rejects it | `open_archive` raises the translated error |
| Zero-byte source, `unix-compress` | `open_archive` raises `TruncatedError` (the decoder rejects a source shorter than its header) |
| Non-seekable source | Validation deferred to the first read |

#### Scenario: the failure carries honest provenance

The open-time raise is stamped with the same provenance a read-time one would carry:
`format_unconfirmed` follows the decode failure whichever call surfaces it. Which evidence
sets that flag is the detection contract's to define, not this one's.

| Case | Expected |
| --- | --- |
| `backup.gz` of zeros, format chosen by extension alone | Raises at `open_archive`, not on a later read |
| Same, `format_unconfirmed` on that exception | Whatever the detection contract assigns to extension-only evidence (today `False`) |
| A probe-only format (no corroborating extension) whose first byte does not decode | Raises at `open_archive` with `format_unconfirmed=True` and emits `PROBE_FORMAT_UNCONFIRMED` |
| `member_name` on the open-time exception | `None` |
| Listing is never reached for an undecodable source | The empty-listing diagnostic channel is not the reporting path for this class |
| A source whose first byte decodes and that fails later | A read-time failure |
