# gzip, zlib and raw DEFLATE

Current maintainer truth for the DEFLATE family: gzip (`.gz`) and zlib (`.zz`) as
single-file formats, and raw DEFLATE as the coder inside ZIP and 7z members. All three are
the same compressed format (RFC 1951) in different wrappers, decoded by the same two
backends: the standard library's `zlib`, driven by archivey's own engine, and the optional
`rapidgzip` accelerator, which runs in a child process. What they share with the other
codecs, the one-member reader, the seek table and the truncation contract, is on
[`single-file.md`](single-file.md). Registers keep the status; this page states the
behaviour and links the row.

## At a glance

| | |
| --- | --- |
| Read | gzip and zlib as one-member archives; raw DEFLATE as a ZIP or 7z coder, and inside `.tar.gz` |
| Write | **Not shipped** |
| Backends | The standard library's `zlib` under `DecompressorStream`, always available. `rapidgzip` from `[seekable]`, used when `use_rapidgzip` selects it (§2.3) |
| Seeking | Without `rapidgzip`, a backward seek decodes again from the start. With it, from the nearest point of the index it builds while decoding |
| Size | `None`, for both. gzip's ISIZE is the last member's size mod 2³²; zlib has no size field |
| Digests | None listed. Every gzip member's CRC-32 is checked on read, and a zlib stream's Adler-32 too; under `rapidgzip`, archivey checks the Adler-32 when the stream is read to its end (§2.3) |
| Metadata | gzip only: `MTIME` → `modified`, `FNAME` → `raw_name` and `extra["gzip.original_filename"]` |
| Truncation | Always raised by the standard library engine. Through `rapidgzip`, raised by a backstop that a seek does not turn off. It stands down only when it finds a further member that zlib confirms, so it is best-effort for a multi-member gzip (§2.3) |
| Refuses | A member whose method is not 8 (deflate) or whose header sets a reserved FLG bit, as `UnsupportedFeatureError` (gzip: "-- not supported"; `gzip_error`). A zlib stream with a preset dictionary fails to decode, since archivey holds no dictionary |

**Four things a reader might expect and will not find.** The gzip trailer's CRC-32 is not
in `member.hashes`, even for a one-member file: proving there is one member means reading
the whole file (§6). `member.size` is `None` although the trailer holds a size, for the
same reason. With `rapidgzip`, a truncated stream is reported with the same certainty as
without it only for a one-member gzip; a truncated raw DEFLATE stream read alone through
`rapidgzip` under `ON` can come back short with no error (§5). And under `AUTO`,
`rapidgzip` never decodes a stream opened without declared seeking, whatever its size
(§2.3), so a plain open read front to back gets the standard library.

## 1. Shape

Four properties generate most of this page.

```
gzip member:  1f 8b 08 FLG MTIME(4) XFL OS  [FEXTRA][FNAME\0][FCOMMENT\0][FHCRC]
              DEFLATE blocks …  CRC-32(4)  ISIZE(4)          ← repeat for the next member
zlib stream:  CMF FLG [DICTID(4)]  DEFLATE blocks …  Adler-32(4, big-endian)
raw DEFLATE:  DEFLATE blocks …  (the container knows the sizes)
```

**DEFLATE can only be decoded from the start.** Each block may copy from anywhere in the
32 KiB before it, so resuming at a block needs the 32 KiB window that preceded it. Nothing
in the stream records one. So a backward seek decodes again from byte zero unless someone
saved windows on the way; `rapidgzip` does, which is what its index is (§2.3). gzip writers
that reset the window at known points (`bgzip`, `pigz -i`) make restart points exist, but
archivey does not look for them (§7).

**A gzip file is a run of members, and nothing counts them.** `cat a.gz b.gz` is a valid
gzip file whose content is both payloads, and bgzip writes every 64 KiB as its own member.
Each trailer covers only its own member: CRC-32 and ISIZE, the size mod 2³². No header says
whether another member follows, and the member magic `1f 8b 08` is three bytes, which a
large compressed body contains by chance about once per 16 MiB. So the file's size and
digest are not in any one place, and are not listed (§6).

**The header carries a name and a time, in the writer's encoding.** `FNAME` is specified as
ISO 8859-1, and GNU gzip writes the filename's bytes as they are, UTF-8 on a modern Linux.
`MTIME` is seconds since 1970, or 0 for none (`gzip -n` writes 0 and no name).

**zlib is DEFLATE with a two-byte header and an Adler-32, and raw DEFLATE is neither.** The
zlib header is not a magic: 66 of the 65 536 two-byte values are legal headers, so detection
decodes to confirm one (§2.1). There is no size field and no defined concatenation. Raw
DEFLATE has no framing at all; its container supplies the compressed and uncompressed sizes
and the CRC, which is what lets `rapidgzip` read it safely (§2.3).

## 2. The pipeline here

### 2.1 Identify

gzip is the magic `1f 8b` at offset 0, reported `CERTAIN`. The inner-TAR probe then decodes
512 bytes and upgrades a match to `TAR_GZ` ([`single-file.md`](single-file.md) §2.1). Both
`.gz` and `.tgz` / `.tar.gz` are registered extensions.

zlib has no magic and is found by a content probe, the second of the three after LZMA Alone.
It first checks the two header bytes against the RFC 1950 grammar
(`_zlib_header_plausible`): compression method 8, a window of at most 32 KiB (`CINFO <= 7`),
and `(CMF * 256 + FLG) % 31 == 0`. The grammar is stated rather than listed, because a list
of header values is easy to get wrong: the common four leave out six of the seven window
sizes `zlib` will write. The grammar also admits `CINFO = 0`, which `zlib` cannot write.
`FDICT` passes the gate; a stream needing a preset dictionary then fails the decode and falls
through. Only then does the probe decode the sample ([`single-file.md`](single-file.md)
§2.1). A zlib match is `PROBABLE`.

Raw DEFLATE is never detected; it exists only inside a container that names it.

### 2.2 Open and list

`GzipCodec.extract_metadata` reads the first 512 bytes of the source once and takes:

- **`modified`** from `MTIME`, when it is not 0.
- **`raw_name`** as the `FNAME` bytes, and **`extra["gzip.original_filename"]`** as those
  bytes decoded as Latin-1, which is what RFC 1952 specifies. A UTF-8 name therefore reads
  as mojibake there: `café.txt` from GNU gzip is `cafÃ©.txt` (§3). The member's `name` is
  not taken from `FNAME` ([`single-file.md`](single-file.md) §2.4). A name that ends past
  byte 512, behind a large `FEXTRA`, is not reported.

`FCOMMENT`, `XFL`, `OS` and `FEXTRA` subfields (bgzip's `BC` block size among them) are not
reported. `size` is `None` and `hashes` is empty (§6). A zlib stream reports nothing beyond
the shared fields.

### 2.3 Member data

**The standard library engine.** `GzipDecompressorStream` is `DecompressorStream` over
`GzipDecoder`: `zlib.decompressobj(16 + MAX_WBITS)`, which parses each member's header and
checks its CRC-32 and ISIZE. It is not `gzip.GzipFile`, because `GzipFile.read()` of a
truncated file discards the prefix it decoded and it validates only on read. After each
member the decoder follows `GzipFile`'s rules for what comes next: NUL bytes are skipped
(tape padding) and `1f 8b` starts the next member. Anything else ends the stream there:
every member before it is delivered and the bytes are reported as `ARCHIVE_TRAILING_DATA`
([`single-file.md`](single-file.md) §2.3), where `GzipFile` would raise. zlib and raw
DEFLATE use `ZlibDecoder` with `wbits=15` and `-15`. Truncation is certain on this path:
a member that did not reach its trailer arms a `TruncatedError` at the end of input.

**When `rapidgzip` is used.** `use_rapidgzip` is an `AcceleratorMode`, `AUTO` by default:

| Mode | gzip, zlib, raw DEFLATE |
| --- | --- |
| `OFF` | The standard library engine, always |
| `ON` | `rapidgzip`, whatever the size; `PackageNotInstalledError` without it, `ResourceLimitError` if no child process can start, `StreamNotSeekableError` on a source that cannot seek (a pipe, or a member stream of an outer archive opened without `seekable_members`) |
| `AUTO` | `rapidgzip` only when all hold: seeking was declared (`seekable_members=True`, `open_stream(seekable=True)`); the source is seekable; `rapidgzip` is installed; the compressed input is known to be at least 16 MiB (`RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE`); and the decoded length can be checked, either because the container declared it or because a gzip trailer is readable. Otherwise the standard library engine, silently |

The 16 MiB gate is the break-even of the child process: it costs about 45 ms to start and
open (about 25 ms of it starting the child), plus about 70 µs per round trip, which the
parent's read-ahead buffer reduces, and saves about 3.4 ms per MB of compressed input on a
full read, so below about 13 MB the standard library is faster
(`scripts/bench_rapidgzip_child.py`; the numbers are in the design of the archived
`rapidgzip-deflate-child-process` OpenSpec change). The last `AUTO`
condition keeps a bare zlib or raw DEFLATE stream on the standard library: nothing could
catch `rapidgzip` ending a raw DEFLATE stream early, and a zlib stream's Adler-32 is checked
only once the stream is read to its end (below).

**Without declared seeking, `AUTO` never uses `rapidgzip`.** `AUTO` resolves against
declared seek demand, not against how the caller then reads: a member stream's seek
machinery is built only on that declaration (xz and lzip build their member-stream seek
index the same way), and `rapidgzip` is that machinery here. It is also a faster decoder,
since it decodes in parallel: the 3.4 ms per MB above is a full-read saving, which a
front-to-back read gets as well as a seeking one. A caller who reads large streams front
to back and wants that speed declares seeking (`seekable_members=True`,
`open_stream(seekable=True)`), which keeps the 16 MiB gate, or sets `use_rapidgzip=ON`,
which pays the child's start on every stream, small ones included.

**Why a child process.** `rapidgzip` 0.16 calls `std::terminate` when it decodes a DEFLATE
stream that ends early. The throw comes from a destructor, so it happens for a path, a
file object and a `BytesIO` alike; on an 8 MB gzip, 27 of 30 random cuts aborted the
interpreter, and no `try` can catch it. So gzip, zlib and raw DEFLATE go through
`RapidgzipChildStream` (`internal/streams/codecs/rapidgzip_child.py`), which runs
`rapidgzip_worker.py` in a separate Python (`python -P`, importing nothing from archivey).
The abort then costs the member, not the caller. bzip2 has not been seen to abort and stays
in-process ([`bzip2.md`](bzip2.md) §2.3). The child turns off its own core dumps before it
imports `rapidgzip`: an expected abort is no use to anyone as a core, and a crash handler
that `core_pattern` pipes to (apport, systemd-coredump) gets the whole address space, about
4 GB with every core decoding, whatever `RLIMIT_CORE` says, while the parent waits for it
([`investigations/rapidgzip-worker-deaths.md`](../investigations/rapidgzip-worker-deaths.md)).

What crosses the boundary:

- **Parent to child**: `OPEN` with a path, which the child opens itself, or a request to read
  the parent's stream; then `READ`, `SEEK` and `RESUME` (the nearest index point, for the
  rewind report). Frames are `<BqI>`: tag, argument, payload length.
- **Child to parent**: decoded bytes (at most 1 MiB per round trip, read ahead in 64 KiB
  minimum once reads are sequential), positions, and errors as type name, errno and
  message. When the source is a stream, the child asks the parent for each read, seek and
  tell, and the parent answers from the caller's stream. An exception from that stream
  stays in the parent and is raised to the caller as itself, marked so no translator
  rewrites it.
- **How the child ended**, when it dies: its exit status and its standard error, scanned
  for `rapidgzip`'s abort message. An abort that names the truncation is `TruncatedError`,
  another crash `CorruptionError`, `SIGKILL` `ResourceLimitError` (usually the
  out-of-memory killer), the exit status `MEMORY_LIMIT_EXIT` `ResourceLimitError` (the
  memory cap below), anything else `ReadError`. Every later call raises the same error.

**Memory.** `rapidgzip` keeps decoded chunks in memory, and a chunk is as large as its
output, so its memory follows what the data decodes to: zeros compressed to 255 KB held
285 MB, and a 1 MB file brought in the out-of-memory killer. No `rapidgzip` setting bounds
it. `chunk_size` changed the peak but did not stop it growing with four threads. An
address-space limit (`RLIMIT_AS`, `RLIMIT_DATA`) cannot bound it either: the allocator
reserves about 2 GB of address space before the first byte (1.5 GB with one thread), and a
decode under a smaller limit ends in a segmentation fault. So the child watches its own peak
resident memory from a thread, every millisecond, once the source is open, and exits with
`MEMORY_LIMIT_EXIT` when it has grown by more than `DecoderLimits.max_decoder_memory`
(`watch_memory` in `rapidgzip_worker.py`). The parent marks that death like a crash on the
data, so the standard library takes over from the last index point and reads the rest in
bounded memory. Measured on 64 MiB of zeros, the child's peak was its start-up memory plus
the cap plus about 17 MB, for caps of 8, 16 and 32 MiB; without a cap it was 87 MB. On a
busy machine the watching thread can wait for a processor, and the peak passes the cap by
more. Ordinary
data on four threads held about 55 MB over start-up, so the 2 GiB default leaves room for
about 140 threads. The peak is read with `getrusage` (Linux, macOS) or
`GetProcessMemoryInfo` (Windows); where neither works, no cap applies.

Where no child can start — a frozen application, no `sys.executable`, archivey imported
from a zip so the worker is not a file, or a spawn or temporary file the system refuses —
`AUTO` uses the standard library and logs one warning per process on the `archivey.streams`
logger; `ON` raises `ResourceLimitError`.

**Truncation through `rapidgzip`.** Besides aborting, `rapidgzip` often ends a truncated
stream softly, by design: `read()` returns `b""` or a prefix with no error. For a gzip
source that is seekable, `_GzipTruncationCheckStream` backs it up:

1. If the stream ends before a single byte came out, the reader switches to the standard
   library engine over a fresh view of the source, which recovers the prefix and raises.
2. If it ends after some bytes, the reader first asks `rapidgzip` how far into the source
   it decoded. A decode that stopped short of the end hands the rest of the read to the
   standard library engine. This is what tells a member cut short and followed by a
   complete one from a multi-member file: there the trailer and the further member are
   both real, but `rapidgzip` stopped at the cut and never decoded the rest. Otherwise the
   trailer is looked for in the file: the CRC-32 of the output, which the reader keeps as
   the output goes by, and its length mod 2³², ending the file but for zero padding. The
   trailer is the one place near the end where that CRC-32 occurs: the reader looks from
   72 bytes before the end of the non-zero data to 12 bytes after it, and finds the
   trailer only when the CRC-32 occurs there once, followed by the length. A forged copy
   of the eight bytes, appended after a wrong trailer (even bytes equal to the length),
   overlapping it, or reaching back into the compressed data (a stored block ends with the
   payload's own bytes), leaves the real CRC-32 as a second occurrence, and is turned
   down. The CRC-32 is no obstacle to a forger, who can set it with four chosen bytes of
   payload, so the rule does not lean on it being unlikely. Not excluded: a copy more than
   56 bytes after the end of the real trailer. `rapidgzip` would have to read past that
   many bytes that are not a further member without an error, and `rapidgzip` 0.16 raises
   on ten or more, which hands the read to the standard library engine. A real trailer
   turned down, when its CRC-32 occurs a second time by chance (about one file in 2²⁵) or
   is zero with padding after it, goes to the standard library engine too, which finds
   nothing wrong. Only when a seek skipped output, so that there is no CRC-32 of it, is
   the ISIZE trailer read at open (the file's last four bytes) compared with the length
   instead. A mismatch hands the rest of the read to the standard library engine, which
   raises the truncation, raises the checksum error for a wrong ISIZE, or reports bytes
   appended to the file. The exception is a file with a further member: its trailer is
   only the last member's size, so a mismatch is expected and nothing is raised.
   `gzip_has_additional_member` decides that. It looks for `1f 8b 08` after offset 0, and
   since those three bytes turn up by chance in a compressed body about once per 16 MiB,
   it hands each match to zlib's gzip decoder. The match counts only if zlib accepts the
   header (method, reserved `FLG` bits, `FHCRC`) and then reaches the member's end with
   its CRC-32 and ISIZE right, or decodes 64 KiB of input or 1 MiB of output with no
   error. Random bytes fail within a few hundred DEFLATE symbols. A match the file ends
   inside does not count: a real member cut there is a truncation. This scan was chosen
   over handing every mismatch to the standard library, because a multi-member file (bgzip
   writes one member per 64 KiB) always mismatches, and the handover would decode it a
   second time from the start.
3. A source shorter than 18 bytes that still yielded bytes is handed to the standard
   library engine as well, which raises the truncation. A source whose length cannot be
   read is never called truncated.

A seek does not turn the check off. The read that meets the end of `rapidgzip`'s output
is at that output's length whatever seeks came before, so the check compares the position
of that read, not a count of the bytes delivered (`rapidgzip` clamps a seek past the end
to the end, and `_StdlibSeekContract` keeps the caller's position). A seek
back to the start, or a forward seek that skips data, leaves the check armed. Once the
standard library engine has raised, it raises again at every later end of data. A container member does
not need it: the container declared the size, and `VerifyingStream` checks length and
CRC. A bare zlib or raw DEFLATE stream has neither, which is why `AUTO` never gives one to
`rapidgzip`.

**The zlib Adler-32 check.** `rapidgzip` does not check a zlib stream's Adler-32: a damaged
body or trailer decodes with no error, sometimes short. `_ZlibAdlerCheckStream` keeps an
Adler-32 of the output from offset 0 up to a frontier and compares it with the source's last
four bytes when the frontier reaches the end. A seek back stays behind the frontier, and a
seek forward past it reads the bytes in between, so the check survives the TAR reader, which
skips member data by seeking. The child decodes those bytes to seek past them anyway; the
read-through adds their transfer out of it. For a reader that only skips, that transfer is
the whole cost: listing a `.tar.zz` under `ON` moves the full decompressed archive out of
the child once, where the seeks alone moved nothing. `docs/access-and-cost.md` states it. A mismatch is either damage or several zlib
streams one after another (`rapidgzip` reads all of them; the trailer is the last one's), so
the standard library then decodes the source again. It reads the first stream only, as it
does with the accelerator off, where the rest is trailing data. When that stream ends
before the output `rapidgzip` delivered, and the caller has none of the bytes past its
end, the read goes to the standard library at the end of the first stream: a completing
`read()` returns the first stream's bytes, a seek to the end lands there, and the rest is
reported as `ARCHIVE_TRAILING_DATA`, as with `use_rapidgzip=OFF`. A caller that read on in
bounded reads already has bytes of the second stream when the end shows up, and they
cannot be taken back; there the decode goes on stream by stream up to the delivered length,
and that output is accepted (§5). Whichever decode runs must reproduce the delivered
bytes; failing that, the read or seek that reached the end raises `_StreamChecksumError`, a
`CorruptionError`. The TAR end-of-archive scan, which otherwise
ignores a tail that fails to decode, re-raises it, because the checksum covers members it
already handed out. A stream never read to its end is not checked, and the standard library
does not check one either: it verifies the trailer only when it consumes the end.

A cut zlib stream is the third cause of a mismatch: `rapidgzip` reads a stream cut inside
its body as a shorter whole one, with no error, and sometimes stops before output the
standard library still decodes (a cut 200 bytes in gave nothing where the standard library
gave 582 bytes). When the standard library's decode agrees with every byte delivered and
then has more output, or runs out of source inside the stream, the read goes to the
standard library at the delivered position (`switch_to_stdlib`), so the bytes and the
`TruncatedError` are those of `use_rapidgzip=OFF` (maintainer ruling, 2026-10-03). One
exception remains: with a container-declared size, the read that reaches it is the
`VerifyingStream`'s verifying event, and when its probe past the size meets a cut Adler-32
that read's chunk is withheld, as any failed verifying event withholds its chunk.

**What of a cut stream a caller gets back.** The same bytes with `rapidgzip` as without it.
The standard library engine delivers everything up to the last complete block before the
cut. `rapidgzip` decodes ahead in parallel, so it can reach the cut and abort while the
parent is still waiting for earlier bytes: measured on 4 cores, a cut 154 MB gzip gave
nothing before the abort where the standard library gave 20 MB, and the loss grows with the
thread count. So a crash of the child, like a data error it reports, hands the read to the
standard library (`_StdlibOnAcceleratorError`), which starts at the child's `resume_point`
rather than at the start of the stream: a DEFLATE block boundary from `rapidgzip`'s index
at or before what the caller has read, with the 32 KiB of output before it. The child keeps
the last four such points, because the newest can be ahead of the caller: by the read-ahead
buffer, or after a seek back. `deflate_resume.py` makes zlib start there: the window goes
in as a preset dictionary, and the block's bit offset is reached by starting the input with
empty blocks whose length ends that many bits into a byte. The second decode then costs the
distance from that point to the cut, not the file: at most `rapidgzip`'s read-ahead, plus
the spacing of the index queries, plus the spacing of the points. The child queries its
index once the output has passed the point the last query named and run on by 16 KiB per
point it holds (4 MiB at least), which keeps the queries' cost a small share of the decode.
A resumed decode that reaches the end of its DEFLATE stream cannot check the CRC-32 or
Adler-32 after it, so it starts over from the start of the stream, which checks it.

**The first member's header is checked before `rapidgzip`.** `rapidgzip` accepts a header
CRC (`FHCRC`) that does not match and reserved `FLG` bits, which RFC 1952 §2.3.1.2 says a
decoder must refuse. Before starting it, `_gzip_header_refused` feeds the source to a zlib
gzip window until the first byte of output; if zlib refuses, the stream opens on the
standard library engine instead, which raises the same error on the first read as with the
accelerator off. Later members are not checked, because `rapidgzip` does not say where
they start (§5, §7).

**A wrong ISIZE on a member before the last reads under `rapidgzip`.** It checks every
member's CRC-32 but not the ISIZE of one that another member follows, where zlib raises
"incorrect length check". With each member's output under its CRC, a wrong ISIZE there is
a malformed trailer, not damaged data, and `compressed-streams` lets the accelerator read
it (*An accelerator preserves the error contract*).

**Other `rapidgzip` workarounds.** Its input is clipped to the known compressed length
(`_bound_rapidgzip_source`), because it reads past the end of a raw DEFLATE stream looking
for another member, and a 7z AES stage's padding would look like one. Inside that bound it
still reads on past the stream's final block, where zlib stops, so a raw DEFLATE stream
finishes on the standard library engine (`_StdlibOnAcceleratorError`) when `rapidgzip`
fails on data or its output passes the size the container declared. Its error messages
differ by platform (ISA-L on Linux, a different decoder on macOS, a bare
`RuntimeError("Unknown exception")` for a near-end truncation on Windows), and
`_translate_rapidgzip` maps each; the Windows one becomes `CorruptionError`, not
`TruncatedError`, since the detail is lost.

**`rapidgzip` is told no format.** It looks at the first bytes and tries a gzip member, a
zlib stream, a bzip2 stream and then raw DEFLATE, in that order. So a raw DEFLATE source
that starts with `1f 8b`, a zlib header or `BZh` and a digit, and a zlib source whose
header `rapidgzip` does not take for zlib (none at all, or one with a preset dictionary),
decode with the standard library engine, which raises at the header as it does with the
accelerator off (`_rapidgzip_may_read_as_another_format`, `_rapidgzip_reads_as_zlib`). No
encoder starts raw DEFLATE like zlib: that first byte is a stored block with nonzero
padding bits. `BZh` and a digit is a possible start, so a real raw DEFLATE stream that
has it decodes without the accelerator. `rapidgzip` also ends a raw DEFLATE stream cut
before any output (`03`, a final block with no end code) softly, as if it were empty, so
a raw DEFLATE stream that ends before its first byte goes to the standard library too,
which reads a valid empty stream as empty and raises on a cut one. Once the standard
library has taken over, its errors leave as archivey's typed errors, so the over-run
probe of a declared size does not take a raw `zlib.error` for the end of the data. Before
that, a ZIP member declared empty with a body that is not DEFLATE read as empty. The
accelerator fuzz targets found all three.

**Every accelerator object is closed, never only joined.** `rapidgzip`'s C++ worker
threads call `std::terminate` if they are still running at interpreter finalization, and
only `close()` stops them. `_AcceleratorStream` wraps each object with a `weakref.finalize`
guard that closes it exactly once, whether the wrapper is collected or the interpreter
exits. The measurements and the canary that watches for an upstream fix are in
[`rapidgzip-upstream-report.md`](../investigations/rapidgzip-upstream-report.md) §6.

**Bytes after the stream under `rapidgzip`.** `rapidgzip` has no end of stream: it takes
bytes after a gzip member for the start of another and fails on them, or, in the child,
delivers the payload and then takes the file's last four bytes, now junk, for ISIZE. Both
cases switch to the standard library engine at the position already delivered
(`_StdlibOnAcceleratorError` in `internal/streams/codecs/stdlib_takeover.py`): a data error from
`rapidgzip` does it inside the read or a seek, and an ISIZE mismatch with no confirmed
further member does it through the truncation check. A seek meets these errors too:
`rapidgzip`'s seek through a valid file with NUL padding after it fails on the padding,
which `gzip -t` and the standard library accept, so a seek that fails on data switches as
a read does. The standard library engine then decodes
the rest and finds the junk or the cut, so a file with bytes after it reads in full and
reports once, and a truncated one still raises `TruncatedError`. The switch decodes again
from the start of the stream up to that position; it is paid only by a file that fails
under `rapidgzip`.

### 2.4 Extract

Nothing here is gzip-specific ([`single-file.md`](single-file.md) §2.4).

### 2.5 Write

Not shipped.

## 3. In the wild

Measured with the tools listed on [`single-file.md`](single-file.md) §3.

| Producer | archivey |
| --- | --- |
| `gzip` | Reads; `FNAME` and `MTIME` reported |
| `gzip -n` | Reads; no name, `modified=None` |
| `pigz`, `pigz -i` | Reads. `-i` resets the window at each block, which archivey does not use for seeking |
| `bgzip` (BGZF, the genomics format) | Reads, as a run of 64 KiB members; no `FNAME`. The block size in `FEXTRA` is not used for seeking |
| Two `gzip` files concatenated | Reads both payloads |
| A member followed by NUL padding | Reads; the padding is skipped |
| A member followed by `junk` | Reads the whole payload, then `ARCHIVE_TRAILING_DATA`, with or without `rapidgzip`. `gzip -t` calls it "trailing garbage ignored" and exits 2 |
| GNU gzip of `café.txt` | `extra["gzip.original_filename"] == "cafÃ©.txt"`, `raw_name == b"caf\xc3\xa9.txt"` |
| `zlib.compress` | Detected by the probe, `PROBABLE`; reads |

**Large `.gz` files contain the member magic by chance.** A body of hundreds of MiB almost
always contains `1f 8b 08` somewhere. That is why the rapidgzip backstop's scan has zlib
confirm each match before it takes the file for multi-member (§2.3), and why archivey does
not try to prove a single member at open (§6).

## 4. Threat surface

gzip-specific only; the shared items are [`single-file.md`](single-file.md) §4.

- **`rapidgzip` is native code outside the defended surface.** It aborts on a truncated
  stream (contained by the child process), and the mutation harness found it busy-looping
  on crafted input. A loop in the child is still a loop: there is no timeout. The Atheris
  `gzip_accel`, `zlib_accel` and `deflate_accel` targets fuzz it under libFuzzer's own
  timeout, and `SECURITY.md` tells callers with a latency budget to keep it off for
  untrusted input (threat-model O5).
- **The ISIZE backstop trusts the trailer, but only after a decode to the end.** A crafted
  file whose ISIZE matches the bytes `rapidgzip` delivered before a soft end used to pass;
  the compressed-position check now hands it to the standard library engine. Within a
  decode that did reach the end, a wrong ISIZE on a member before the last still passes
  (§5), with every member's CRC-32 checked. A wrong ISIZE on the last member of a
  one-member file does not (§2.3). In a concatenated file it can: the output's CRC-32 is
  not the last member's, so the further-member scan decides, and it stands down at the
  first further member it confirms. So, where the `rapidgzip` build does not compare the
  size itself (the Linux wheels; a build that does raises), a wrong ISIZE on the last of
  three or more members reads clean, and so does one on a last member large enough that
  zlib has decoded 64 KiB of its input by the time the probe gives its answer. Accepted:
  the case is too specific to be worth finding the last member's start for (§7).
- **The trailer lookup folds a CRC-32 over the whole output.** The reader keeps it as the
  output goes by, on the reader's thread while `rapidgzip` decodes on others, and compares
  it at the end with the eight bytes that end the file but for zero padding (§2.3). That is
  the option `gzip-padded-isize-accepted` declined as too costly ("the CRC-32 of the
  output, or a second decode"); it is the cheaper of the two. `zlib.crc32` runs at about
  3.3 GB/s on the machine measured, 0.056 s per 184 MB of output, which was 6% to 15% of
  a full accelerated read of 184 MB from a 33 MB `.gz` with four cores (0.50-0.66 s
  without it; the share is higher on a faster decoder or more cores, and the figures are
  noisy). It is paid once per stream read to its end, and not after a seek that skipped
  output. The end of the file is read in 96 bytes, and further back only over padding.
  Nothing in the benchmark gates holds this number (`targz_read_all_accel_on` is a
  structural case of 131072 bytes).
- **The multi-member scan reads the whole file.** When ISIZE disagrees, the backstop scans
  forward for a member magic in 1 MiB blocks, on an independent view, and has zlib decode
  from each match, at most 64 KiB of input and 1 MiB of output. It runs once per stream,
  at the end. The matches share a decode budget of 1 MiB plus one sixteenth of the bytes
  scanned, so a file built with a header zlib accepts every few bytes costs a bounded share
  of the scan; one that spends it is handed to the standard library engine, which decides.
- **`FNAME` is attacker-chosen bytes.** It is reported, never used as a path.
- **A source of back-to-back gzip headers** gives the SFX scan a candidate at every offset.
  The candidate search is linear in the source, and per-candidate decoding is bounded by
  the detection budget (threat-model O11).

## 5. Sharp edges

*Where it lives*: **format** — inherent, no implementation fixes it · **library** — an
upstream library's behaviour, fixable only there or by replacing it · **archivey** — ours.

| What you see | Where it lives | More |
| --- | --- | --- |
| `member.size` is `None` and `hashes` is empty for a `.gz` whose trailer holds both | **format** / **archivey** | The trailer describes the last member only (§1); decided in PR #441 (§6) |
| `extra["gzip.original_filename"]` is mojibake for a non-ASCII name | **format** | RFC 1952 says Latin-1; GNU gzip writes the bytes it was given. `raw_name` has them |
| A backward seek on a `.gz` re-decodes from the start | **format** | No restart points (§1). Install `[seekable]` and pass `seekable_members=True`; under `AUTO`, only from 16 MiB |
| The same truncated `.gz` raises `TruncatedError` on Linux and `CorruptionError` on Windows under `rapidgzip` | **library** | Windows loses the message detail (§2.3). Catch `ReadError` for both |
| A truncated multi-member `.gz` read through `rapidgzip` can end short with no error | **library** / **archivey** | `rapidgzip` ends softly. The backstop raises when the decode stopped before the end of the file; a cut whose decode still reaches the end, with zlib confirming a later member, can pass. Summing each member's ISIZE is deferred (§7) |
| A member after the first with a header CRC that does not match or reserved `FLG` bits reads clean under `rapidgzip`; the standard library raises | **library** / **archivey** | `rapidgzip` does not check these, and hides member boundaries. The first member's header is checked (§2.3); the rest is the per-member question in §7 |
| A wrong ISIZE on any member but the last reads clean under `rapidgzip` builds that do not compare the size; the standard library raises | **library** / **archivey** | Every member's CRC-32 is still checked, so the data is right; the spec accepts this difference (§2.3). `rapidgzip` 0.16.0 compares it only in its own chunk decoder, for a stream that lies wholly in one chunk; the inflate-wrapper decoder (ISA-L, in the Linux wheels) appends the footer without comparing. The last member's ISIZE is checked by the backstop, which finds the trailer by the CRC-32 of the output. After a seek that skipped output there is no CRC-32 and the file's last four bytes stand in for the ISIZE; in that case only, four bytes appended after a wrong ISIZE, equal to the length, pass for the trailer. Found by the `gzip_accel` fuzz target |
| A truncated bare raw DEFLATE stream under `use_rapidgzip=ON` can end short with no error | **library** | No size or checksum to check it against (§2.3). `AUTO` never does this. A stream cut before its first byte of output raises, as with the accelerator off |
| `rapidgzip` does not check a zlib stream's Adler-32 | **library** | archivey checks it after `rapidgzip`, once the stream is read to its end (§2.3). Found by `tests/test_nested_archives.py` |
| Several zlib streams one after another, read in bounded reads to the end under `use_rapidgzip=ON`, read as one; the standard library reads the first alone and reports the rest as bytes after the stream | **library** | RFC 1950 defines one stream per file. A completing `read()` and a seek to the end stop at the first stream as with the accelerator off; bounded reads have the second stream's bytes before the end shows up, and the Adler-32 check accepts them when the standard library reproduces them stream by stream (§2.3), so they get those bytes as content and no `ARCHIVE_TRAILING_DATA` |
| Trailing junk after a `.gz` is a warning, where `GzipFile` raises | **archivey** | The rule is shared by every codec ([`single-file.md`](single-file.md) §6); `DiagnosticPolicy.strict()` raises |
| A `.gz` with bytes after it under `rapidgzip` decodes part of the file twice | **archivey** | The switch to the standard library replays up to where `rapidgzip` failed (§2.3); a resumed replay that reaches a member's end starts over to check its CRC-32 |
| One warning on `archivey.streams` that `rapidgzip` cannot run | **archivey** | No child process can start here; `AUTO` used the standard library. `use_rapidgzip=OFF` silences it |
| A zlib stream with a preset dictionary is not detected, and fails when opened by name | **format** | archivey holds no dictionary |
| A large `.gz` opened without `seekable_members=True` is no faster with `[seekable]` installed | **archivey** | `AUTO` uses `rapidgzip` only on declared seeking (§2.3). Declare seeking, or set `use_rapidgzip=ON` |
| A cold backward seek under `rapidgzip` still re-decodes megabytes, and the log says so | **library** | Its index is sparse: three points over 5 MB of `gzip.compress` output |

## 6. Decisions

| Choice | Why | Rejected |
| --- | --- | --- |
| Never list the gzip trailer CRC-32 (PR #441) | A digest is worth having to skip a decode or to verify one. The trailer covers only the last member, proving there is one member means reading the whole file at every open (a full download for a remote source), the chance magic made large files "multi-member" anyway, and after a read the decoder has already checked every CRC | Scanning for a second member at open; adding the CRC after a full read, which changes `hashes` under a caller who already read it |
| Decode with `zlib`'s gzip window under archivey's engine, not `gzip.GzipFile` (PR #183) | Sized reads recover the prefix of a truncated file, and the engine's seek table and rewind report apply | `GzipFile`, which drops the prefix on `read()` and cannot report rewinds |
| Follow `GzipFile` after a member for NULs and the next member; report other bytes instead of raising | NULs and a next member are what `GzipFile` accepts; other bytes follow the rule every codec shares ([`single-file.md`](single-file.md) §6) | Raising, as `GzipFile` does |
| Switch to the standard library when `rapidgzip` fails on bytes after the stream | `rapidgzip` cannot tell junk from a next member or from damage; the standard library can | Refusing the file under `rapidgzip`, so the result would depend on the accelerator |
| One accelerator library, `rapidgzip`, for gzip, zlib, raw DEFLATE and bzip2 (ADR 0008) | `indexed_gzip` or `indexed_bzip2` next to it corrupt the heap on macOS | Several accelerator packages |
| Run `rapidgzip` in a child process for the DEFLATE family (PR #493) | Its abort on a cut stream is uncatchable in-process | In-process with guards, which cannot catch `std::terminate`; decoding with the standard library first and handing `rapidgzip` only proven input, which decoded the whole member at the first backward seek |
| `AUTO` needs 16 MiB of input and a checkable size | Below that the child costs more than it saves; without a size, a soft end would pass silently | 1 MiB, the in-process threshold; `AUTO` on any declared seek, with no size gate |
| `AUTO` uses `rapidgzip` only for declared-seekable streams (the concurrent-member-streams change, PR #59) | Seek machinery, indexes and accelerators alike, is built only on declared demand, for every codec | `AUTO` keyed on the `streaming` access mode. `AUTO` for large sequential reads has not been weighed, although the 16 MiB break-even above is measured on a full read |
| `AUTO` falls back to the standard library with one log warning where no child can start | The accelerator is an enhancement; a frozen application must still read `.gz` | Raising, which `ON` does |
| Report `FNAME` decoded as Latin-1, keep the bytes in `raw_name` | RFC 1952 says so, and the bytes are there for a caller who knows better | Guessing UTF-8 |

## 7. Open questions

- **Summing ISIZE per member.** It would make the `rapidgzip` backstop sure for
  multi-member files, but needs the member boundaries, which `rapidgzip`'s index does not
  expose; a byte walk of the headers is the only way. Tracked internally.
  Measured on 0.16.0 with 2- and 3-member files of distinct member sizes, read to the end:
  `block_offsets()` and `available_block_offsets()` hold seek points chosen for chunked
  random access, and no member start appears among them at any `parallelization`. With
  `parallelization=0`, archivey's setting, they hold only the start and the end of the
  stream; other settings add mid-member chunk points unrelated to member starts. There is no member or stream count accessor either:
  `add_deflate_stream_crc32` and `set_deflate_stream_crc32s` feed an imported index, they
  do not enumerate streams at decode time. So the backstop's byte scan for a further
  `1f 8b 08` stays, and the per-member sum cannot use the index. A change proposal to
  detect multi-member files through the index was closed on this finding.
- **Native random access for BGZF and other block-reset gzip.** bgzip and `pigz -i` write
  restart points into the file, and BGZF names each block's size; reading them would give
  seeking without `rapidgzip`. Tracked internally.
- **Replaying the standard library engine after a `rapidgzip` abort**, to deliver the rest
  of a cut stream's prefix. Tracked internally.

## 8. Verify

```bash
./scripts/test.sh tests/test_codecs.py tests/test_single_file.py \
    tests/test_rapidgzip_deflate_zlib.py tests/test_accelerator_corruption.py \
    tests/test_accelerator_truncation_abort.py tests/test_accelerator_shutdown.py \
    tests/test_accelerator_bug3_trap.py tests/test_accelerator_takeover.py \
    -k "gzip or zlib or deflate or rapidgzip or accelerat"
```

| Claim | Pinned by |
| --- | --- |
| Multi-member, NUL padding, trailing junk after the payload | `tests/test_codecs.py::test_gzip_multi_member_and_padding_parity`, `::test_gzip_trailing_junk_delivers_member_then_reports`, `tests/test_stream_trailing_data.py::test_rapidgzip_reads_to_the_end_and_reports`, `::test_gzip_multi_member_cross_feed_edges` |
| A truncated stream gives its prefix to sized reads and raises | `::test_truncated_gzip_large_read_recovers_prefix_like_read1`, `::test_truncated_zlib_deflate_large_read_recovers_prefix`, `::test_truncated_gzip_readall_raises` |
| `FNAME`, Latin-1, `MTIME` | `tests/test_single_file.py::test_gzip_stored_filename_surfaced`, `::test_gzip_stored_filename_non_ascii_is_latin1`, `::test_gzip_mtime_surfaced` |
| No size, no CRC, no scan at open | `::test_gz_size_is_always_none`, `::test_gzip_never_reports_a_crc32`, `::test_gzip_open_does_not_scan_for_a_second_member` |
| zlib Adler-32 checked, not listed; under `rapidgzip` too, across seeks and through the TAR end scan | `::test_zlib_omits_hashes_but_verifies_adler_on_read`, `tests/test_rapidgzip_deflate_zlib.py::test_rapidgzip_zlib_damage_raises_from_the_adler_check`, `::test_rapidgzip_zlib_adler_check_survives_seeks`, `::test_rapidgzip_zlib_concatenated_streams_pass_the_check`, `::test_tar_zz_member_damage_raises_under_rapidgzip_on`, `tests/test_nested_archives.py::test_damaged_inner_archive_raises_an_archivey_error` |
| `ON` on a source that cannot seek raises `StreamNotSeekableError`; `AUTO` uses the standard library there | `tests/test_nested_archives.py::test_accelerator_on_over_a_forward_only_member_is_refused_cleanly`, `::test_auto_ignores_seek_demand_on_a_forward_only_compressed_tar` |
| zlib header grammar and probe | `tests/test_detection.py::test_zlib_detected_at_every_legal_window_size`, `::test_zlib_grammar_admits_exactly_66_header_pairs`, `::test_zlib_grammar_accepts_a_preset_dictionary_header` |
| `AUTO` needs declared seeking; `ON` does not. A large `.gz` opened without `seekable_members=True` stays on the standard library | `tests/test_seekable_streams.py::test_accelerator_mode_auto_resolution`, `tests/test_rapidgzip_deflate_zlib.py::test_auto_on_a_large_gz_file_follows_declared_seeking` |
| `AUTO` threshold and size condition; `ON` below it | `tests/test_rapidgzip_deflate_zlib.py::test_the_auto_threshold_is_past_the_child_break_even`, `::test_auto_selects_rapidgzip_above_threshold`, `::test_auto_without_decompressed_size_uses_stdlib_even_when_large`, `::test_on_forces_rapidgzip_below_threshold` |
| The abort happens in-process, and not through archivey | `tests/test_accelerator_truncation_abort.py::test_raw_rapidgzip_aborts_on_truncated_gzip` (canary), `::test_truncated_gzip_with_seekable_members_raises_truncated`, `::test_what_a_cut_stream_delivers_before_the_abort_is_a_correct_prefix` |
| A cut stream under `rapidgzip` delivers the standard library's bytes; the takeover resumes at a checkpoint, and starts over at a member's end | `tests/test_rapidgzip_resume.py`, `tests/test_deflate_resume.py`, `tests/test_accelerator_truncation_abort.py::test_after_a_child_crash_the_standard_library_reads_on` |
| How the child's death is reported; no child → fallback and one warning | `::test_a_child_death_is_reported_by_how_it_ended`, `::test_a_child_killed_from_outside_ends_the_stream`, `::test_without_a_child_auto_uses_stdlib_and_on_refuses`, `::test_a_child_that_cannot_start_falls_back_to_stdlib_under_auto`, `::test_an_auto_fallback_warns_once_per_process` |
| The caller's source exception reaches the caller | `::test_an_exception_from_the_callers_source_reaches_the_caller_unchanged` |
| The ISIZE backstop and the empty-end fallback | `tests/test_accelerator_corruption.py::test_rapidgzip_truncation_is_reported`, `::test_rapidgzip_silent_empty_fallback_recovers_prefix`, `::test_rapidgzip_isize_soft_short_raises_on_readall`, `::test_rapidgzip_multimember_not_flagged`, `::test_gzip_backstop_keeps_raising_after_its_own_truncation`, `::test_gzip_cut_member_before_a_complete_one_raises`, `::test_gzip_cut_member_with_a_forged_isize_raises` |
| A cut zlib stream delivers the bytes and error of `OFF` under `ON` and `AUTO`, cut in the first block, a later block or the trailer; the declared-size exception | `tests/test_accelerator_takeover.py::test_a_cut_zlib_reads_as_it_does_with_the_accelerator_off` |
| Under `rapidgzip`: the gzip check survives seeks; a chance `1f 8b 08` does not silence it; a seek that fails on data is handed over; a second zlib stream is trailing data | `tests/test_rapidgzip_end_checks.py` |
| A raw DEFLATE source that looks like gzip, zlib or bzip2, a zlib source whose header `rapidgzip` does not take for zlib (none at all, or one with a preset dictionary), a raw DEFLATE stream cut before any output, and a declared-empty stream that does not decode give the bytes and error of `OFF` under `ON` | `tests/test_rapidgzip_deflate_zlib.py::test_a_stream_of_another_format_raises_as_with_the_accelerator_off`, `::test_raw_deflate_cut_before_any_output_raises_truncated`, `::test_a_declared_empty_stream_that_does_not_decode_raises` |
| A cut bare zlib stream under `ON` without a size raises | `tests/test_rapidgzip_deflate_zlib.py::test_standalone_zlib_midcut_raises_through_rapidgzip_on_without_size` |
| Close guard on shutdown; one accelerator library | `tests/test_accelerator_shutdown.py::test_accelerator_shutdown_canary`, `::test_archivey_uses_single_accelerator_library` |

**Building fixtures.** The standard library writes gzip and zlib (`gzip.compress`,
`zlib.compress`); `pigz` and `bgzip` (package `tabix`) install from the distribution.
`scripts/bench_rapidgzip_child.py` measures the child's start and round-trip cost.

## 9. References

- RFC 1952 (gzip): §2.3 member format, `FLG` bits, `MTIME`, `ISIZE`, and §2.3.1.2 on
  concatenated members
- RFC 1950 (zlib): §2.2 `CMF`/`FLG` and `FCHECK`, `FDICT`, Adler-32
- RFC 1951 (DEFLATE): §3.2 block format and the 32 KiB window
- SAM/BAM specification §4.1 (BGZF)
- [`rapidgzip`](https://github.com/mxmlnkn/rapidgzip), version 0.16
- Investigation: [`rapidgzip-upstream-report.md`](../investigations/rapidgzip-upstream-report.md)
  (soft end, Bugs 1 and 2, debugging scripts)
- Registers: [`known-issues.md`](../known-issues.md) (rapidgzip Bugs 3 and 4) ·
  [`threat-model.md`](../threat-model.md) O5, O11
- Decisions: [ADR 0008](../decisions/0008-single-accelerator-rapidgzip.md) ·
  [ADR 0014](../decisions/0014-integrity-verdicts-from-reads-not-close.md) ·
  [`library-analysis.md`](../library-analysis.md) §gzip, §raw Deflate / zlib
- Code: `internal/streams/codecs/` (`gzip_codec.py`: `GzipCodec`,
  `_GzipTruncationCheckStream`; `zlib_codec.py`: `ZlibCodec`, `DeflateCodec`;
  `rapidgzip_select.py`: the accelerator selection; `stdlib_takeover.py`;
  `deflate_decoder.py`: `GzipDecoder`, `ZlibDecoder`; `rapidgzip_child.py`,
  `rapidgzip_worker.py`)
- Handbook: [`single-file.md`](single-file.md) · [`zip.md`](zip.md) (DEFLATE members) ·
  [`tar.md`](tar.md) (`.tar.gz`)
