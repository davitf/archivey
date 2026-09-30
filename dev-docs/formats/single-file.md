# Single-file compressors

Current maintainer truth for the backend that reads a bare compressed stream: `.gz`,
`.zz`, `.bz2`, `.xz`, `.lz`, `.lzma`, `.zst`, `.lz4`, `.br` and `.Z`. None of them is an
archive. Each is one compressed byte stream with, at most, a name and a time in its
header, and archivey presents it as an archive with exactly one member. This page keeps
what the ten codecs share: the one-member reader, the codec layer they all plug into, the
decode engine and its seek table, the truncation contract, and detection. Each codec's own
behaviour is on its page, linked from §2. Registers keep the status; this page states the
behaviour and links the row.

| Codec page | Covers |
| --- | --- |
| [`gzip.md`](gzip.md) | gzip, zlib, and raw DEFLATE as the ZIP and 7z coder; the `rapidgzip` accelerator and its child process |
| [`bzip2.md`](bzip2.md) | bzip2, and `rapidgzip`'s in-process bzip2 decoder |
| [`xz.md`](xz.md) | xz, lzip and LZMA Alone: the LZMA family, with archivey's own seekable xz and lzip decoders |
| [`zstd-lz4.md`](zstd-lz4.md) | zstd and LZ4: frame formats read through their libraries, with no seek index |
| [`brotli.md`](brotli.md) | Brotli: no magic, found by a content probe |
| [`unix-compress.md`](unix-compress.md) | Unix `compress` (`.Z`): archivey's own LZW decoder |

A compressed TAR (`.tar.gz` and the rest) runs its outer stream through the same codec
layer; what differs is on [`tar.md`](tar.md) §2.3.

## At a glance

| | |
| --- | --- |
| Read | Yes, ten codecs, one reader (`SingleFileReader`). Each is `ContainerFormat.RAW_STREAM` plus a `StreamFormat` (`ArchiveFormat.GZ`, …) |
| Write | **Not shipped**, for any format ([writing design](../investigations/archive-writing-design.md)) |
| Source | Any. Random access needs a seekable source; a pipe needs `streaming=True` and gives one forward pass. `start_offset` is refused |
| Listing cost | `INDEXED`. The one member is built at open without decoding; the few fields that need the source come from its header or its end (§2.2) |
| Access cost | `DIRECT` — one member, nothing solid in front of it |
| Stream capability | `SEEKABLE` on a seekable source, `FORWARD_ONLY` on a pipe. What a backward seek costs depends on the codec (§2.3) |
| Core dependencies | The standard library reads gzip, zlib, bzip2, xz, lzip, LZMA Alone and `.Z`. zstd uses `compression.zstd` on Python 3.14+ and `backports.zstd` from `[recommended]` below it. LZ4 (`lz4`) and Brotli (`brotli`) are `[recommended]` |
| Optional | `[seekable]`: `rapidgzip`, a seek index for gzip, zlib, raw DEFLATE and bzip2 |
| Refuses | `start_offset` · random access on a non-seekable source · a second open of the member of a non-seekable source (`StreamNotSeekableError`) · writing |
| Accepts and ignores | `password=` (`PASSWORD_ARGUMENT_UNUSED`) · `encoding=` (`ENCODING_ARGUMENT_UNUSED`) |

**Five things a reader might expect and will not find.** `member.size` is `None` for most
codecs: only xz and lzip (from their index, on a seekable source) and an LZMA Alone header
that declares it give one, although zstd and LZ4 frames can carry a content size too
(§2.2). `member.hashes` is empty for every codec but lzip; gzip's trailer CRC-32 is left
out on purpose ([`gzip.md`](gzip.md) §6). Bytes after the last stream are neither an error
nor ignored: the payload reads in full and the bytes are reported as
`ARCHIVE_TRAILING_DATA`, although `xz` and `zstd` refuse such a file (§2.3, §3). A
backward seek decodes again from the start of the stream unless the codec has resume
points in front of the target, and most have none (§2.3). And the gzip header's stored
filename is reported, never used: the member's name comes from the name of the file
archivey was given (§2.2).

## 1. Shape

Four properties generate most of this page.

**One stream, one member, no directory.** There is no member table to read, so listing
costs nothing and cannot be wrong about what exists. The member's name is not in the
stream, except for gzip's optional `FNAME`, so archivey takes it from the outside: the
source's filename with the codec extension removed (`notes.txt.gz` → `notes.txt`), the
filename plus `.uncompressed` when the extension is not a codec's, and `data` for a stream
with no name. Its type is always `FILE`, and extraction writes one file.

**What is known about the content is at the end of the stream, if anywhere.** gzip ends
each member with a CRC-32 and the size mod 2³²; xz ends each stream with an index of block
sizes; lzip ends each member with a CRC-32, a data size and a member size. bzip2 and zlib
keep their check values inside the stream, zstd and LZ4 frames carry optional ones, and
Brotli and `.Z` have none. So `size` is reported only where the end can be read cheaply,
which needs a seekable source (§2.2); a pipe gets `size=None` for every codec but LZMA
Alone, whose size is in its header. And a
stream cut short is caught with certainty by the codecs that end in a check, and only by
luck by `.Z` ([`unix-compress.md`](unix-compress.md)).

**Most of them allow concatenation.** gzip members, bzip2 streams, xz streams (with zero
padding between them), lzip members, zstd frames and LZ4 frames can each follow one
another in one file, and the file's content is all of them joined. archivey reads every
codec's concatenation as one payload. The consequence is that a trailer describes its own
segment, not the file: gzip's ISIZE is the last member's size, which is why archivey
never reports it as the file's size.

**Decoding runs forward, and random access needs points to resume from.** A decoder
reaches offset N by decoding everything before it, unless the stream has places where
decoding can restart: xz blocks and streams, lzip members, `.Z` CLEAR codes, and the
index `rapidgzip` builds as it reads gzip or bzip2. Where there are none, and for most
files there are none (default `xz` writes one block, `lzip` one member, and `plzip` one member for a small file), a backward seek
decodes again from byte zero. The decode engine (§2.3) keeps the points it finds and
reports a backward seek that costs more than a megabyte.

## 2. The pipeline here

Each stage: who does the work, what is shared across the codecs, what is refused.

### 2.1 Identify

Seven codecs have magic at offset 0, declared on their codec descriptor in
`internal/streams/codecs.py` and aggregated by the detector: gzip `1f 8b`, bzip2 `BZh`,
xz `fd 37 7a 58 5a 00`, lzip `LZIP`, zstd `28 b5 2f fd`, LZ4 `04 22 4d 18` (and its legacy
stream `02 21 4c 18`), `.Z` `1f 9d`.
zstd also matches behind a run of skippable frames ([`zstd-lz4.md`](zstd-lz4.md) §2.1).
The other three have none that is safe to trust, and are found by a **content probe**
that decodes a bounded sample: LZMA Alone, then zlib, then Brotli, in that order. The
steps run strongest signal first — near magic, the SFX scan, far magic, content probes,
extension — so a probe only sees what nothing stronger claimed.
[`topics/detection.md`](../topics/detection.md) has the order and why.

What is codec-specific about a probe: it runs with accelerators off and decoder memory
limits lifted, because a probe decodes a bounded sample and a capped probe would call a
stream with a large dictionary "not this format" for a caller who opened it with
`DecoderLimits.UNLIMITED` (`_PROBE_STREAM_CONFIG`). The open that follows applies the
caller's limits. The shared machinery around the probes (the sample size, the
whole-source re-check, the decode allowance, and the `format_unconfirmed` stamp on a
probe-only result) is on [`topics/detection.md`](../topics/detection.md) §2.4 and §4.1.

Every match is then offered to the **inner-TAR probe** (`_probe_inner_tar`; its bounds are
on [`topics/detection.md`](../topics/detection.md) §2). Its 1 MiB input bound is sized for
bzip2, which emits no output until a whole block of up to 900 kB has been read. A pre-POSIX
tar has no `ustar`, so a v7 tar inside gzip is reported as `GZ` even when named `.tar.gz`
([`tar.md`](tar.md) §2.1).

The extension is the last resort (`GUESS`): `.gz`, `.zz`, `.bz2`, `.xz`, `.lz`, `.lzma`,
`.zst`, `.lz4`, `.br`, `.Z`, compared case-insensitively.

### 2.2 Open and list

**`SingleFileReader` serves every codec.** It is codec-agnostic: the codec is picked by the
format, and everything format-specific is a method on that codec's `StreamCodec` subclass.
Adding a codec is one subclass in `codecs.py`, with no edit to the detector, the reader
or the registry.

**The source decides how members are opened.** A path is handed to the codec on every
open, so concurrent opens get independent handles. A seekable stream is wrapped once in a
`SharedSource` and every open reads its own view of it. A non-seekable stream gets one
decompressor at construction, handed to the first open; a second open raises
`StreamNotSeekableError`.

**`open_archive` decodes one byte** of a seekable source (`_validate_at_open`), so a file
that is not the codec its name or its detection claims fails at open rather than at the
first read. It checks nothing further in: damage past the first byte still fails on the
read. A valid empty stream opens and reads as `b""`. A pipe is not checked, because the
byte would come out of its one pass. The cost is one decoded block, which matters only
for bzip2 ([`bzip2.md`](bzip2.md) §2.2).

What is filled in on the member, and where from:

| Field | Source |
| --- | --- |
| `name` | The source's filename, as in §1 |
| `size` | xz: the stream index; lzip: the member trailers; LZMA Alone: the header, when it is not the "unknown" marker. `None` for every other codec |
| `compressed_size` | The source's length, from one `seek(0, SEEK_END)` on any seekable source. `None` on a pipe |
| `modified` | gzip's `MTIME`, when non-zero. `None` for every other codec |
| `raw_name`, `extra["gzip.original_filename"]` | gzip's `FNAME` ([`gzip.md`](gzip.md) §2.2) |
| `hashes` | lzip only: the CRC-32 of the whole content, combined from each member's trailer ([`xz.md`](xz.md) §2.2) |

The xz index and the lzip trailers are read by a backward peek from the end of the
source. That peek is decided by the source's shape, not by `seekable_members`: the
declaration is about seeking the member stream, and the peek hands nobody a stream. Tying
it to the flag would make the same `.xz` report `size=None` on a plain open and its size
with the flag. The peeks run with the accelerators off, since they decode nothing.

`ArchiveInfo` has `member_count=1`, `format_version=None`, `comment=None` and
`is_solid=False`.

### 2.3 Member data

**The codec layer.** `open_codec_stream(codec, source, config=…)` returns an
`ArchiveStream` around whichever backend the codec's `open` chose for this configuration.
The `ArchiveStream` translates the backend's exceptions through the translator that
matches the chosen backend (`rapidgzip` raises different ones from the standard library),
stamps them with the archive and member, and reports expensive backward seeks. The
container formats reach the same codecs through `resolve_codec` and `CodecParams`:
ZIP's DEFLATE, 7z's LZMA2, a `.tar.xz`'s outer stream.

**One engine.** Every codec decodes through archivey's own engine, `DecompressorStream`
(`internal/streams/decompressor_stream.py`): zlib and gzip with the standard library's
`zlib`, xz and lzip with the standard library's `lzma` driven by archivey's own framing
code, Brotli with the `brotli` package, and `.Z` with archivey's own LZW. bzip2, LZMA
Alone, zstd and LZ4 run their library's one-stream decompressor inside
`FramedDecompressorStream`, which starts another at the next stream's magic. The library
file objects (`bz2.open`, `lzma.LZMAFile`, the packages' `open`) cannot say where the last
stream ends, which reporting bytes after it needs (§2.3). The engine owns the output
buffer, the position and the seek table; a codec plugs in as a `Decoder` with
`feed`/`flush`/`recreate` and, where the format has an index, `build_index`. The
`rapidgzip` accelerator for gzip, zlib and bzip2 is the one stream outside it.

**Seek points.** The engine keeps a sorted table of `SeekPoint`s, each a decompressed
offset, the compressed offset to resume from, and whatever state the codec needs there.
A forward read adds the points it passes; xz and lzip can also fill the table from the end
of the file, without decoding (`build_index_backwards`), when a caller seeks to the end or
asks the size. A seek bisects to the nearest point before the target, rebuilds the decoder
there and decodes forward, discarding output in 64 KiB steps. The table is capped at
2¹⁸ points (`MAX_SEEK_POINTS`): past that it is thinned to an evenly spaced half, which
costs seeks some extra decoding and emits `SEEK_INDEX_DEGRADED`. A codec with no points
starts every backward seek from byte zero. Library-backed codecs seek the same way inside
the library, from the start.

Where resume points come from, per codec:

| Codec | Resume points |
| --- | --- |
| xz | Every block, and every stream in a multi-stream file. Default `xz` writes one block; `xz -T0` and `--block-size` write many |
| lzip | Every member. Default `lzip` writes one; `plzip` one per data block, many with a small `-B` |
| `.Z` | Every CLEAR code, which `compress` emits when the dictionary stops paying |
| gzip, zlib, raw DEFLATE, bzip2 | None natively. With `rapidgzip` engaged, its own index, built as it decodes |
| LZMA Alone, zstd, LZ4, Brotli | None |

**The rewind report.** A backward seek whose target is more than 1 MiB
(`REWIND_REDECODE_WARN_BYTES`) past its nearest resume point emits
`STREAM_REWIND_REDECOMPRESSES`, once per stream. The threshold is absolute, not relative
to the jump: on a 1 GB single-block `.xz`, seeking from the end back to 900 MB re-decodes
900 MB for a 100 MB jump, and a relative rule would stay quiet exactly there. For the
DEFLATE family and bzip2 the message names `rapidgzip`, and suggests installing it only
when it is absent.

**Accelerators.** `ArchiveyConfig.use_rapidgzip` (gzip, zlib, raw DEFLATE) and
`use_indexed_bzip2` (bzip2) are `AcceleratorMode`s. `OFF` never uses `rapidgzip`; `ON`
always does, and raises `PackageNotInstalledError` without it and
`StreamNotSeekableError` on a source that cannot seek; `AUTO` uses it only when
seeking was declared (`seekable_members=True`, `open_stream(seekable=True)`) and it is
installed. For the DEFLATE family `AUTO` adds two conditions, a compressed input of at
least 16 MiB and a way to check the decoded length, and `rapidgzip` runs there in a
child process. Both are on [`gzip.md`](gzip.md) §2.3; bzip2 stays in-process
([`bzip2.md`](bzip2.md) §2.3).

**Truncation.** A decoder's `flush` runs once, at compressed end of file. When the stream
is incomplete it arms a `TruncatedError` rather than raising it, and hands back whatever
it could still decode. A caller reading in sized pieces gets every byte that preceded the
cut, then the error on the next read; a caller asking for everything with `read()` gets the
error and no bytes, since a short payload handed back as the whole is the worse outcome. Once raised, the error is raised again at every later end of data,
including after a seek back: the source has not changed, so a re-read must not end
cleanly (PR #491). Integrity errors surface from reads, never from `close()` (ADR 0014).
How sure each codec is that a stream ended early differs a great deal; each page has its
row, and §3 has the table.

**Decoder memory.** `DecoderLimits.max_decoder_memory` (2 GiB by default) is checked
against a dictionary size the stream declares before the decoder is built: xz per block,
through liblzma's `memlimit`; lzip per member, whose format caps the dictionary at 512 MiB
anyway; LZMA Alone from its header, refused on the first read so the refusal carries the
probe's `format_unconfirmed` stamp ([`xz.md`](xz.md) §2.3); zstd per frame, through
libzstd's `window_log_max` ([`zstd-lz4.md`](zstd-lz4.md) §4). bzip2, LZ4, Brotli and gzip
have small fixed windows. The `.Z` decoder's table stays under about 19 MiB (about
20 MiB on a free-threaded build) whatever the stream does
([`unix-compress.md`](unix-compress.md) §4).

**Bytes after the end.** Each decoder knows where its stream ends: the gzip member's
trailer, zlib's Adler-32, the end of an xz stream, lzip member or LZMA Alone payload,
and the end the bzip2, zstd and LZ4 libraries report for one stream. For bzip2, LZMA
Alone, zstd and LZ4, `FramedDecoder` in `internal/streams/decompress.py` runs one
library decompressor per stream and starts another only when the next bytes are that
codec's magic (a zstd skippable frame counts), so a concatenated file still reads as one
payload. LZMA Alone has no magic; the next bytes start a stream when the header's
properties byte is one liblzma decodes and byte 13, the range coder's first byte, is
zero, which every LZMA encoder writes and text almost never has. That is how
`lzma.LZMAFile` reads a concatenated `.lzma` too, and a second stream's dictionary is
checked against `max_decoder_memory` like the first. Past the end, zero bytes are
padding, as `tar` pads its records; the first non-zero byte ends the stream there.
`DecompressorStream` stops reading the source, returns everything decoded, and emits one
`ARCHIVE_TRAILING_DATA` with `expected_marker="end_of_stream"` at that byte's offset.
Only a bare file, a compressed TAR's codec and `open_stream` report
(`StreamConfig.report_trailing_data`); a codec inside a ZIP or 7z member stops silently,
because the container's sizes decide there, and so do the detection and metadata probes.
Brotli and the two accelerators need more than this ([`brotli.md`](brotli.md) §2.3,
[`gzip.md`](gzip.md) §2.3, [`bzip2.md`](bzip2.md) §2.3), and xz and lzip search back for
their index through up to 1 MiB of such bytes ([`xz.md`](xz.md) §2.2). `.Z` has no end
to find ([`unix-compress.md`](unix-compress.md)).

**Compressed input is counted.** On a pipe the reader wraps the source so the extraction
ratio guard has a denominator; a path or seekable stream uses its length.

### 2.4 Extract

Nothing here is codec-specific. The one member is written under its inferred name through
the shared extraction machinery
([`safe-extraction`](../../openspec/specs/safe-extraction/spec.md)). gzip's `FNAME` is
never used as the output name: it is attacker-chosen bytes, and a caller renaming
`a.gz` to `b.gz` expects `b`.

### 2.5 Write

Not shipped, for any format.

## 3. In the wild

**Measured producers.** Each file below was built from the same 4 MB text payload with
the tool named, then listed and read through `open_archive`, plainly and with
`seekable_members=True`, on the development container (gzip 1.12, pigz 2.8, bgzip from
htslib 1.19, bzip2 1.0.8, pbzip2 1.1.13, lbzip2 2.5, xz 5.4.5, plzip 1.11 (the container's `lzip` command is plzip too, so every lzip row is plzip output),
zstd 1.5.5, lz4 1.9.4, brotli 1.1.0, ncompress 5.0). Every one read back correctly, except
where the table says otherwise; the per-codec pages carry the details.

| Producer | Result |
| --- | --- |
| `gzip`, `gzip -n`, `pigz`, `pigz -i`, `bgzip`, two `gzip` members concatenated, NUL padding after the member | Read. `bgzip` writes many small members, `pigz -i` one member in independently compressed blocks; `-n` gives no `FNAME` and `modified=None` |
| `bzip2`, `pbzip2`, `lbzip2`, two streams concatenated | Read |
| `xz`, `xz -T4 --block-size`, `-C none`, `-C sha256`, two streams with zero padding | Read; `size` from the index |
| `xz --format=lzma` | Detected by the probe (`PROBABLE`), read; `size=None` because `xz` writes the "unknown size" marker |
| `plzip`, `plzip -B` | Read; `size` and the combined CRC-32 from the trailers |
| `zstd`, `zstd --no-check`, `pzstd`, two frames concatenated, `zstd --long=31` from a file | Read |
| `zstd --long=31` from standard input (frame declares a 2 GiB window) | Read under the default 2 GiB `max_decoder_memory` ([`zstd-lz4.md`](zstd-lz4.md) §4) |
| `lz4`, `lz4 -BD`, `lz4 --content-size`, two frames concatenated | Read; `size=None` even with `--content-size` |
| `lz4 -l` (legacy frame, magic `02 21 4c 18`) | **Not detected**; named `.lz4` it opens by extension and fails with `CorruptionError` stamped `format_unconfirmed` ([`zstd-lz4.md`](zstd-lz4.md) §3) |
| `brotli` | Detected by the probe, `PROBABLE` |
| `compress`, `compress -b12` | Read |

**Bytes after the last stream.** Measured by appending `junk\n` to each file above. The
codecs' own tools disagree; archivey treats every codec with an end marker the same way:

| Codec | archivey | The codec's own tool |
| --- | --- | --- |
| gzip, zlib, bzip2, xz, lzip, LZMA Alone, zstd, LZ4, Brotli | The whole payload, then `ARCHIVE_TRAILING_DATA` at the junk's offset; `DiagnosticRaisedError` under `strict()`. xz and lzip keep `size` and seeks | — |
| gzip | as above | `gzip -t`: "trailing garbage ignored", exit 2 |
| bzip2 | as above; the accelerator also prints a warning to standard error | `bzip2 -t`: "trailing garbage after EOF ignored", exit 0 |
| xz | as above | `xz -t`: "Unexpected end of input", exit 1 |
| lzip | as above | `plzip -t`: exit 0; the lzip manual allows trailing data |
| zstd | as above | `zstd -t`: "unsupported format", exit 1 |
| Brotli from a pipe | `CorruptionError`: telling the junk from damage needs a second read ([`brotli.md`](brotli.md) §2.3) | — |
| `.Z` | `TruncatedError`, because the junk decodes as codes | — |

## 4. Threat surface

Shared by the codecs; each page adds its own.

- **A small stream decodes to a large output.** Every codec can expand by orders of
  magnitude, and a `read(n)` is bounded by `n`, not by the input. The engine decodes at
  most the requested output per call (`max_length`), Brotli included where the library
  allows it ([`brotli.md`](brotli.md) §4); extraction's ratio guard bounds what is written.
- **A declared dictionary sizes an allocation.** xz, lzip and LZMA Alone declare their
  dictionary before any data, and liblzma reserves it. `DecoderLimits` refuses one over the
  cap before the decoder is built (§2.3). Detection probes lift the cap and rely on the
  bounded sample, which bounds what is written into the dictionary but not what liblzma
  reserves.
- **A crafted index misplaces bytes on a seek.** xz and lzip seeks trust the file's own
  index; a forward read verifies it, a cold seek does not. Accepted: threat-model O17.
- **A seek table grows with the unit count the file declares.** An lzip member can be 26
  bytes, so a table could cost several times the file's size; capped by
  `MAX_SEEK_POINTS` (§2.3).
- **A content probe can fabricate a member** from bytes that are not the codec, and
  deliver a prefix of fabricated output before the read fails: threat-model O10.
  Detection-time decode work is bounded by the detection budget: O11.
- **A native decoder can take the process down.** `rapidgzip` aborts on a DEFLATE stream
  that ends early, so it runs in a child process ([`gzip.md`](gzip.md) §4). The same
  pattern isolates PPMd, a ZIP and 7z coder ([`7z.md`](7z.md)).

## 5. Sharp edges

*Where it lives*: **format** — inherent, no implementation fixes it · **library** — an
upstream library's behaviour, fixable only there or by replacing it · **archivey** — ours.

| What you see | Where it lives | More |
| --- | --- | --- |
| `member.size` is `None` for gzip, bzip2, zlib, zstd, LZ4, Brotli and `.Z`, and on a pipe for every codec but LZMA Alone | **format** / **archivey** | Most of those formats store no reliable total (§1). zstd and LZ4 frames can declare a content size, which archivey does not read ([`zstd-lz4.md`](zstd-lz4.md) §5) |
| A file `xz -t` or `zstd -t` refuses reads, with a warning | **archivey** | Bytes after the stream are reported, not refused (§6); `DiagnosticPolicy.strict()` refuses them |
| Zero bytes read as an empty `.lzma` | **format** | Eighteen zero bytes are a complete empty LZMA Alone stream, a 13-byte header and 5 bytes of range coder, and the rest is padding (§2.3) |
| A backward seek is slow, and the log says so | **format** | No resume point before the target (§2.3). Use `stream_members()` or read forward once; for gzip and bzip2, `rapidgzip` |
| The rewind warning says the codec "has no random-access index" on an `.xz` or `.lz` that has one | **archivey** | The message is chosen by codec, not by whether a resume point was found; on a multi-block `.xz` it is wrong. Tracked internally |
| A second `open()` of the member of a pipe raises `StreamNotSeekableError` | **format** | The one pass is spent; buffer the source to re-read it |
| A file that is not the claimed codec fails at `open_archive`, a damaged one only at the read | **archivey** | Open decodes one byte (§2.2) |
| `password=` or `encoding=` is accepted and has no effect | **archivey** | Shared behaviour for formats without encryption or stored names |

## 6. Decisions

| Choice | Why | Rejected |
| --- | --- | --- |
| One reader for every standalone codec, with the codec as a descriptor class (PR #16) | A new codec is one subclass; the detector, the reader and the registry read the descriptors, so they cannot disagree about a codec | Per-codec tables in each consumer |
| Present the stream as a one-member archive | Callers handle `.gz` and `.zip` with the same code, extraction included | A separate stream-only API; `open_stream` exists as well, for callers who want the bytes |
| Name the member from the source's filename, not gzip's `FNAME` | It is the name the caller chose, it exists for every codec, and it is not attacker bytes | Using `FNAME` when present |
| Read the xz index and lzip trailers whenever the source is seekable (PR #232) | Metadata must not depend on a flag about member-stream seeking | Gating them on `seekable_members` |
| Decode one byte at open, on a seekable source (PR #461) | A file that is not the claimed codec fails where the caller opened it; otherwise a `.gz` full of zeros lists one member and fails only on the read | Checking nothing until the read; decoding more, which costs every open |
| Arm truncation at end of input and raise on the next read (PR #183) | The caller gets every byte before the cut from a sized `read(n)`, and the error cannot be missed by a caller who never calls `close()`. A `read()` of everything raises and returns nothing: a short payload returned as if whole is worse than none | Raising inside the decode, which drops the decoded prefix; raising from `close()` (ADR 0014) |
| A truncation keeps raising after a seek back | The source did not change, so a second pass must not end cleanly | Clearing the error with the position (PR #491) |
| An absolute 1 MiB threshold for the rewind report (PR #232) | Wall time follows bytes re-decoded, not the ratio to the jump | A relative threshold, which goes quiet on the worst case |
| Read every stream to its end, then report non-zero bytes after it as `ARCHIVE_TRAILING_DATA`; zeros are padding | The payload is intact, so refusing it helps no one, and a diagnostic lets a caller who cares refuse through the policy. One rule for every codec, as for TAR | Refusing, as `xz` and `zstd` do; ignoring without a word, as the standard library's readers do |
| Cap the seek table and thin it, rather than refuse (PR #420) | A seek table is an optimisation; nothing becomes unreadable | A `ListingLimits` field that fails the read |

## 7. Open questions

- **Whether to report the content size zstd and LZ4 frames declare.** It changes `size`
  from `None` to a number for most `.zst` files written from a file. A frame's declared
  size is checked by the library on decode, but a multi-frame file needs every frame's
  header read, and a frame written from a pipe has none.

## 8. Verify

```bash
./scripts/test.sh tests/test_single_file.py tests/test_open_stream.py \
    tests/test_seekable_streams.py tests/test_codecs.py tests/test_codec_descriptor.py
```

| Claim | Pinned by |
| --- | --- |
| One backend, one member, names from the filename, `data` for a stream with none | `tests/test_single_file.py::test_one_backend_serves_multiple_formats`, `::test_exactly_one_member_no_directories`, `::test_name_strips_known_compression_extension`, `::test_name_appends_uncompressed_for_unknown_extension`, `::test_name_defaults_to_data_for_anonymous_stream` |
| Cost and archive info | `::test_cost_is_indexed_and_direct`, `::test_archive_info` |
| Size from the xz index and lzip trailers on any seekable source, not on a pipe | `::test_xz_size_from_header`, `::test_lzip_size_from_trailer`, `::test_cheap_size_does_not_require_a_path_source`, `::test_cheap_size_still_needs_seekability` |
| Only lzip reports a digest | `::test_lzip_exposes_stored_crc32`, `::test_other_single_file_codecs_omit_stored_digests`, `::test_gzip_never_reports_a_crc32` |
| Validation at open, one byte deep, deferred on a pipe | `::test_open_validation_table_covers_every_single_file_codec`, `::test_undecodable_source_raises_at_open`, `::test_valid_empty_stream_still_opens_and_reads_empty`, `::test_non_seekable_source_still_defers_validation_to_the_read` |
| A pipe needs `streaming=True` and gives one pass | `::test_non_seekable_gzip_requires_streaming_mode`, `::test_non_seekable_gzip_streams_fine` |
| Concurrent and re-entrant opens are independent | `::test_concurrent_open_same_member_interleaved`, `::test_reentrant_open_after_first_read` |
| `password=` accepted and unused | `::test_password_is_accepted_and_recorded` |
| `open_stream` is forward-only unless asked, and builds no index then | `tests/test_open_stream.py::test_open_stream_default_is_forward_only`, `::test_open_stream_xz_default_builds_no_index`, `::test_open_stream_xz_seekable_exposes_size` |
| Bytes after the stream: payload, one report, zeros silent, strict raises, xz and lzip keep their index, containers silent | `tests/test_stream_trailing_data.py` |
| A new codec needs only a descriptor | `tests/test_codec_descriptor.py` |
| Probe order, completion window, `format_unconfirmed` | `tests/test_detection.py`, `tests/test_brotli_framing_gate.py::test_guess_decode_failure_sets_format_unconfirmed` |

**Building fixtures.** Tests build streams with the standard library (`gzip`, `bz2`,
`lzma`, `zlib`) and the optional packages, or with the command-line tools where the
library cannot write the shape: `tests/streams_util.py` has the multi-block xz and lzip
builders. The §3 files come from the distribution's tools: `apt-get install pigz tabix
pbzip2 lbzip2 lzip plzip zstd lz4 brotli ncompress`.

## 9. References

- Specs: [`format-single-file-compressors`](../../openspec/specs/format-single-file-compressors/spec.md)
  · [`compressed-streams`](../../openspec/specs/compressed-streams/spec.md)
  · [`seekable-decompressor-streams`](../../openspec/specs/seekable-decompressor-streams/spec.md)
  · [`format-detection`](../../openspec/specs/format-detection/spec.md)
- Code: `internal/backends/single_file_reader.py` (the reader) · `internal/streams/codecs.py`
  (codec descriptors, accelerators, probes) · `internal/streams/decompressor_stream.py`
  (the engine and seek table) · `internal/streams/archive_stream.py` (translation, rewind
  report) · `internal/detection.py` (probe order, inner-TAR probe)
- Decisions: [ADR 0008](../decisions/0008-single-accelerator-rapidgzip.md) (one
  accelerator library) · [ADR 0009](../decisions/0009-zstd-stdlib-backports.md) (zstd
  via the standard library) · [ADR 0010](../decisions/0010-no-silent-buffer-nonseekable.md)
  (no silent buffering of a pipe) · [ADR 0014](../decisions/0014-integrity-verdicts-from-reads-not-close.md)
  (integrity errors from reads)
- Registers: [`threat-model.md`](../threat-model.md) O10, O11, O17 ·
  [`known-issues.md`](../known-issues.md) (accelerator crashes)
- Handbook: [`tar.md`](tar.md) (the compressed TAR outer stream) · the codec pages above
- User-facing: [`docs/formats.md`](../../docs/formats.md#single-file-compressors)
