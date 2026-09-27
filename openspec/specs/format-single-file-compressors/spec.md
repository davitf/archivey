# Single-File Compressor Format Behavior

## Purpose

Single-file compressors (GZ, BZ2, XZ, ZST, LZ4, LZIP, ZLIB, BR, Z) are exposed
as one-member pseudo-archives through the unified `ArchiveReader` /
`ArchiveWriter` interface. Each source contains exactly one file member whose
name is inferred from the source filename.

This capability is the standalone-stream side of `compressed-streams`. Raw
Deflate and raw LZMA1/LZMA2 are not standalone formats because they lack
self-framing; they appear only inside containers such as ZIP or 7z. Unix-compress
(`.Z`, LZW) is decoded natively in core, streams from non-seekable sources under
`streaming=True`, and cannot signal truncation because the format has no length or
checksum trailer.

## Related specs

| Spec | Relationship |
| --- | --- |
| `archive-reading` | One-member reader behavior, random vs streaming access modes |
| `access-mode-and-cost` | Listing/access cost and non-seekable legality |
| `compressed-streams` | Codec descriptors, decoder availability, metadata hooks |
| `format-detection` | Bare stream detection and combined TAR-compressor detection |
| `packaging-and-extras` | Optional codec extras such as Brotli, LZ4, Zstd |

## Requirements

### Requirement: Present each compressor as a one-member archive

The system SHALL present any GZ, BZ2, XZ, ZST, LZ4, LZIP, LZMA Alone, ZLIB, BR,
or Z source as an archive containing exactly one `ArchiveMember` of type
`MemberType.FILE`. No directory members SHALL be synthesized.

The member name SHALL be inferred from the source filename:

| Source filename | Member name |
| --- | --- |
| Ends in `.gz`, `.bz2`, `.xz`, `.zst`, `.lz4`, `.lz`, `.lzma`, `.zz`, `.br`, or `.Z` (case-insensitive) | Strip exactly that recognized compression extension |
| Ends in a recognized extension, but the remaining stem is entirely dots and spaces (`..gz`, `....gz`, ` .gz`) | Append `.uncompressed` instead; `.` and `..` are not member names, and an all-dots segment is refused under `STRICT` |
| Has a filename but no recognized compressor extension | Append `.uncompressed`; do not strip arbitrary extensions |
| Anonymous stream | `data` |

Combined names such as `.tar.gz` / `.tgz` / `.tar.lzma` / `.tlz` are a
`format-detection` concern, not single-file member naming.

Raw Deflate and raw LZMA1/LZMA2 (`FORMAT_RAW`) remain container-only; LZMA Alone
is a framed standalone stream and is in scope here.

#### Scenario: one-member naming matrix

| Case | Expected |
| --- | --- |
| Open `data.txt.gz` | One file member named `data.txt` |
| Open `data.txt.lzma` | One file member named `data.txt` |
| Open compressed `mystery.bin` detected by content | One file member named `mystery.bin.uncompressed` |
| Open `....gz` | One file member named `....gz.uncompressed`, under every extraction policy |
| Open anonymous non-seekable stream with `streaming=True` | One file member named `data` |
| Iterate any supported single-file compressor | Exactly one file member is yielded |

### Requirement: Report single-file compressor properties

The backend SHALL expose these properties for every single-file compressor:

| Property | Value |
| --- | --- |
| Listing cost | `INDEXED`; exactly one member |
| Access cost | `DIRECT`; no inter-member dependency exists |
| Supports write | No — writing is not shipped for any format (`PLAN.md` phase 9) |
| Requires seek | Random access (`streaming=False`) requires seek; forward-only `streaming=True` accepts non-seekable sources for every supported single-file codec including `.Z` |

Random access over a non-seekable source SHALL fail fast at open with
`StreamNotSeekableError`; the backend MUST NOT buffer an unbounded source to
simulate repeatable reads. Under `streaming=True`, every supported single-file
codec including unix-compress `.Z` SHALL stream from non-seekable sources.

Member-stream seekability is a stream-level property from index- or
accelerator-backed decoders (for example xz indexes, CLEAR seek points for
unix-compress, `indexed_bzip2`, `rapidgzip`, seekable zstd), not an archive-level
`CostReceipt` field.

#### Scenario: property matrix

| Case | Expected |
| --- | --- |
| Open any supported single-file compressor | `listing_cost=INDEXED`; `access_cost=DIRECT` |
| Non-seekable source with `streaming=False` | `StreamNotSeekableError` |
| Non-seekable source with `streaming=True` (including `.Z`) | Opens and `stream_members()` yields data |
| Seekable `.Z` with declared member-stream seekability | Member stream is seekable via CLEAR seek points |

### Requirement: Report member size with codec caveats

The system SHALL populate `member.size` according to codec metadata and
format-specific reliability limits:

| Codec | `member.size` behavior |
| --- | --- |
| GZ | Always `None`; stored ISIZE is modulo 2^32 and may be wrong |
| BZ2, ZLIB, BR, Z | `None` until full decompression; `.Z` has no size trailer (best-effort truncation via nonzero leftover bits) |
| XZ, ZST | Header size when encoder wrote it; otherwise `None` |
| LZ4 | Frame content-size field when present; otherwise `None` |
| LZIP | Available from the trailer on a seekable source |
| LZMA Alone | 8-byte Alone header size when not the unknown marker (`0xFFFFFFFFFFFFFFFF`); otherwise `None` |

Availability of an index/trailer-derived size SHALL be decided by **the source's shape**,
never by the caller's declared member-stream capability. `seekable_members` /
`open_stream(seekable=…)` declare intent to `seek()` inside a member stream; they select
an indexed decompressor backend and resolve accelerator `AUTO`, and they MUST NOT change
what metadata a member reports. A seekable source SHALL yield the same `member.size` with
and without the declaration; a non-seekable source SHALL yield `None` for every
index/trailer-derived size, and no probe SHALL force a decompression pass to obtain one.

When a decoder learns the true uncompressed size after EOF, the member MAY be
updated to that byte count.

#### Scenario: size matrix

| Case | Expected |
| --- | --- |
| `.gz` opened | Single member size is `None` |
| `.bz2` before full decompression | Size is `None` |
| `.bz2` fully read to EOF | Size may update to actual uncompressed byte count |
| `.lz` opened from a seekable source | Size is available from the trailer |
| `.xz` / `.lz`, seekable source, with and without `seekable_members=True` | Same `member.size` both ways |
| `.xz` / `.lz` from a pipe | Size is `None`; no decode pass is forced |
| Alone stream with known header size | `member.size` equals that size |
| Alone stream with unknown-size marker | Size is `None` until EOF may update it |
| Truncated `.Z` with nonzero leftover bits | Available bytes delivered; next `read()` raises `TruncatedError` |

### Requirement: Surface gzip stored metadata without trusting it as the name

The system SHALL surface gzip `FNAME` when present: the decoded value appears in
`member.extra["gzip.original_filename"]` and the undecoded bytes appear in
`member.raw_name`. By default, `member.name` SHALL still come from the source
filename; embedded gzip names are not automatically trusted because they may
disagree with the container filename. A configuration option MAY prefer the
gzip-stored name for `member.name`. Other single-file compressors SHALL not set
these fields from header data because they carry no embedded filename.

#### Scenario: gzip metadata matrix

| Case | Expected |
| --- | --- |
| `.gz` with `FNAME="report.csv"` opened from `archive.gz` | `extra["gzip.original_filename"] == "report.csv"`; `raw_name` holds undecoded bytes; default `name == "archive"` |
| `.gz` without `FNAME` | No gzip original filename extra; name is derived from source filename |
| `.xz` / `.zst` / `.lz4` / `.lz` / `.bz2` | No gzip filename fields are set |

### Requirement: Get per-codec metadata from codec descriptors

`SingleFileBackend` SHALL obtain per-codec metadata from each
`compressed-streams` codec descriptor rather than a reader-local dispatch table.
Descriptor hooks SHALL preserve existing surfaced metadata: gzip `FNAME`,
`raw_name`, optional gzip mtime, xz/zst/lz4/lzip size hints, and the
format-specific size-availability rules. A codec with no extra metadata SHALL
register no hook.

#### Scenario: descriptor metadata matrix

| Case | Expected |
| --- | --- |
| `.gz` with stored `FNAME` and mtime | Gzip descriptor hook populates extra filename, `raw_name`, and `modified` |
| `.bz2` source | No hook is needed; default single-file member shell is used |
| Size-aware lzip source | Lzip descriptor hook supplies trailer-derived size |

### Requirement: Use one backend for every standalone codec

The system SHALL implement single-file compressor reading as one
`SingleFileBackend` whose `FORMATS` tuple lists every standalone-stream codec.
The backend SHALL infer the one-member shell, then delegate decompression and
metadata to the stream codec resolved from the member's stream format. Detection
tables and availability SHALL derive from codec descriptors: the backend remains
registered, while a format whose required codec backend is missing reports
support `NONE`. Adding a new standalone codec descriptor SHALL make that format
readable without adding another `ReadBackend` subclass.

#### Scenario: backend matrix

| Case | Expected |
| --- | --- |
| Open `.gz`, `.bz2`, `.xz`, `.lzma` | Same `SingleFileBackend` class serves each format with per-codec metadata |
| Register a new standalone codec descriptor | Existing backend reads it through the descriptor |
| Required codec backend is missing | Format availability reports `NONE`, not a separate backend failure |

### Requirement: Surface stored decompressed digests without decompression

The single-file backend SHALL surface a codec's stored (or cheaply derived-from-stored)
decompressed-content digest(s) on `member.hashes` when readable without decompressing,
and SHALL omit them otherwise. This serves cheap dedupe (`VISION.md` "hashes without
decompression") and never triggers a decompression pass.

Keys and value types follow the public `HashAlgorithm` / `bytes` contract (api-coherence
hashes typing). Surfacing SHALL NOT change read behavior: a full read still verifies via
the existing path; stored/derived values are metadata only.

Whether a digest is cheaply readable SHALL depend only on the codec and **the source's
shape**, never on the caller's declared member-stream capability — the founding dedupe
caller does a plain `open_archive()` and never asks to `seek()`.

- **GZIP:** SHALL NOT carry a digest, before or after a read, and opening SHALL NOT
  read the compressed data to look for a second member. The trailer `CRC32` covers the
  whole member only when the file holds one gzip member, and proving that at open costs
  a pass over the whole file (a scan for the three-byte member magic that also
  false-matches in large compressed data). After a full read it would add nothing: the
  decoder has already checked every member's CRC, and a digest is worth having only
  before a read (to skip one) or to verify one.
- **LZIP:** on a seekable source, surface `CRC32` of the whole synthetic member from the
  lzip index. For multi-member files, the value SHALL equal
  `crc32(concat(member payloads))` derived by combining per-trailer CRC-32 values with
  each member's exact uncompressed `data_size` (combine algebra). Single-member
  degenerates to the trailer CRC.
- **Non-seekable source:** omit digests that require a trailer/index peek (no forced
  decode).
- **BZ2, XZ, ZLIB, BR, `.Z`:** no cheap whole-member stored digest — omit. (Zlib's
  RFC 1950 Adler-32 trailer is verified by the decompressor on read; it is not surfaced
  on `member.hashes` because the wrapper has no size fields for a reliable
  single-stream boundary when concat/trailing junk is possible.)

#### Scenario: stored-digest surfacing by codec

| Case | `member.hashes` |
| --- | --- |
| Any `.gz` (one member or several), listed or after a full read | no digest key |
| Opening any `.gz` | no scan of the compressed data for a second member |
| `.gz` non-seekable | no digest key |
| Single-member `.lz`, seekable source | `CRC32` present (= trailer) |
| Multi-member `.lz`, seekable source | `CRC32` present (= combine of per-member trailers) |
| `.lz` seekable, with and without `seekable_members=True` | Same `hashes` both ways |
| `.lz` from a pipe | no digest key |
| `.bz2` / `.xz` / `.zlib` / `.br` / `.Z` | no digest key |
| Any of the above, full `read()` | verification unchanged; hashes are metadata only |

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
