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
| Digests | None listed. Every gzip member's CRC-32 is checked on read, and a zlib stream's Adler-32 too except under `rapidgzip` (§5) |
| Metadata | gzip only: `MTIME` → `modified`, `FNAME` → `raw_name` and `extra["gzip.original_filename"]` |
| Truncation | Always raised by the standard library engine. Through `rapidgzip`, raised by a backstop that is sure for a one-member gzip and best-effort otherwise (§2.3) |
| Refuses | Nothing gzip-specific. A zlib stream with a preset dictionary fails to decode, since archivey holds no dictionary |

**Four things a reader might expect and will not find.** The gzip trailer's CRC-32 is not
in `member.hashes`, even for a one-member file: proving there is one member means reading
the whole file (§6). `member.size` is `None` although the trailer holds a size, for the
same reason. With `rapidgzip`, a truncated stream is reported with the same certainty as
without it only for a one-member gzip; a truncated zlib stream read alone through
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
member the decoder follows `GzipFile`'s rules: NUL bytes are skipped (tape padding), `1f 8b`
starts the next member, and anything else is a `CorruptionError`, raised after the bytes of
the members before it are delivered. zlib and raw DEFLATE use `ZlibDecoder` with
`wbits=15` and `-15`. Truncation is certain on this path: a member that did not reach its
trailer arms a `TruncatedError` at the end of input.

**When `rapidgzip` is used.** `use_rapidgzip` is an `AcceleratorMode`, `AUTO` by default:

| Mode | gzip, zlib, raw DEFLATE |
| --- | --- |
| `OFF` | The standard library engine, always |
| `ON` | `rapidgzip`, whatever the size; `PackageNotInstalledError` without it, `ResourceLimitError` if no child process can start, `StreamNotSeekableError` on a source that cannot seek (a pipe, or a member stream of an outer archive opened without `seekable_members`) |
| `AUTO` | `rapidgzip` only when all hold: seeking was declared (`seekable_members=True`, `open_stream(seekable=True)`); the source is seekable; `rapidgzip` is installed; the compressed input is known to be at least 16 MiB (`RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE`); and the decoded length can be checked, either because the container declared it or because a gzip trailer is readable. Otherwise the standard library engine, silently |

The 16 MiB gate is the break-even of the child process: it costs about 45 ms to start and
open, and saves about 3.4 ms per MB of compressed input on a full read, so below about
13 MB the standard library is faster (`scripts/bench_rapidgzip_child.py`). The last `AUTO`
condition keeps a bare zlib or raw DEFLATE stream on the standard library, because nothing
could catch `rapidgzip` ending one early (below).

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
`RapidgzipChildStream` (`internal/streams/rapidgzip_child.py`), which runs
`rapidgzip_worker.py` in a separate Python (`python -P`, importing nothing from archivey).
The abort then costs the member, not the caller. bzip2 has not been seen to abort and stays
in-process ([`bzip2.md`](bzip2.md) §2.3).

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
  out-of-memory killer), anything else `ReadError`. Every later call raises the same error.

Where no child can start — a frozen application, no `sys.executable`, archivey imported
from a zip so the worker is not a file, or a spawn or temporary file the system refuses —
`AUTO` uses the standard library and logs one warning per process on the `archivey.streams`
logger; `ON` raises `ResourceLimitError`.

**Truncation through `rapidgzip`.** Besides aborting, `rapidgzip` often ends a truncated
stream softly, by design: `read()` returns `b""` or a prefix with no error. For a gzip
source that is seekable, `_GzipTruncationCheckStream` backs it up:

1. If the stream ends before a single byte came out, the reader switches to the standard
   library engine over a fresh view of the source, which recovers the prefix and raises.
2. If it ends after some bytes, their count mod 2³² is compared with the ISIZE trailer,
   read at open. A mismatch is a `TruncatedError`, unless a further `1f 8b 08` appears
   anywhere in the file, in which case the file may be multi-member and nothing is raised.
3. A source shorter than 18 bytes that yielded bytes is truncated; a source whose length
   cannot be read is never called truncated.

A seek away from the sequential position disarms the check, and once it has raised it
raises again at every later end of data. A container member does not need it: the
container declared the size, and `VerifyingStream` checks length and CRC. A bare zlib or
raw DEFLATE stream has neither, which is why `AUTO` never gives one to `rapidgzip`.

**What of a cut stream a caller gets back.** The standard library engine delivers
everything up to the last complete block before the cut. `rapidgzip` decodes ahead in
parallel, so it can reach the cut and abort while the parent is still waiting for earlier
bytes: measured under `ON`, a cut 2 or 8 MB gzip gave no data before the error, and a cut
32 MB one gave 22 to 31 MB. What it does deliver is correct.

**Other `rapidgzip` workarounds.** Its input is clipped to the known compressed length
(`_bound_rapidgzip_source`), because it reads past the end of a raw DEFLATE stream looking
for another member, and a 7z AES stage's padding would look like one. Its error messages
differ by platform (ISA-L on Linux, a different decoder on macOS, a bare
`RuntimeError("Unknown exception")` for a near-end truncation on Windows), and
`_translate_rapidgzip` maps each; the Windows one becomes `CorruptionError`, not
`TruncatedError`, since the detail is lost.

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
| A member followed by `junk` | `CorruptionError` after the whole payload; under `rapidgzip`, `TruncatedError`. `gzip -t` calls it "trailing garbage ignored" and exits 2 |
| GNU gzip of `café.txt` | `extra["gzip.original_filename"] == "cafÃ©.txt"`, `raw_name == b"caf\xc3\xa9.txt"` |
| `zlib.compress` | Detected by the probe, `PROBABLE`; reads |

**Large `.gz` files look multi-member by chance.** A body of hundreds of MiB almost
always contains `1f 8b 08` somewhere, which is why the rapidgzip backstop's scan concludes
"maybe multi-member" on big files and stays quiet, and why archivey does not try to prove a single
member at open (§6).

## 4. Threat surface

gzip-specific only; the shared items are [`single-file.md`](single-file.md) §4.

- **`rapidgzip` is native code outside the defended surface.** It aborts on a truncated
  stream (contained by the child process), and the mutation harness found it busy-looping
  on crafted input. A loop in the child is still a loop: there is no timeout. The fuzzers
  run with accelerators off, and `SECURITY.md` tells callers with a latency budget to keep
  them off for untrusted input (threat-model O5).
- **The ISIZE backstop trusts the trailer.** A crafted file whose ISIZE matches the bytes
  `rapidgzip` delivered before a soft end passes it. The standard library engine checks
  every member's CRC-32 and ISIZE and has no such gap.
- **The multi-member scan reads the whole file.** When ISIZE disagrees, the backstop scans
  forward for a member magic in 1 MiB blocks, on an independent view. It runs once per
  stream, at the end.
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
| A truncated multi-member `.gz` read through `rapidgzip` can end short with no error | **library** / **archivey** | `rapidgzip` ends softly; the backstop stands down when a second member may exist. Summing each member's ISIZE is deferred (§7) |
| A truncated bare zlib or raw DEFLATE stream under `use_rapidgzip=ON` can end short with no error | **library** | No size to check it against (§2.3). `AUTO` never does this |
| A damaged bare zlib stream under `use_rapidgzip=ON` reads back wrong bytes with no error | **library** / **archivey** | `rapidgzip` does not check the Adler-32, and archivey does not check it after `rapidgzip`. `AUTO` never gives a bare zlib stream to `rapidgzip`. Found by `tests/test_nested_archives.py`; fix undecided |
| A cut `.gz` delivers less before the error under `rapidgzip` than without it | **library** | It decodes ahead and aborts early (§2.3). Use `OFF` to salvage the most |
| Trailing junk after a `.gz` is `CorruptionError`, and `TruncatedError` under `rapidgzip` | **archivey** | The standard library engine follows `GzipFile`; the cross-codec picture is [`single-file.md`](single-file.md) §3 |
| One warning on `archivey.streams` that `rapidgzip` cannot run | **archivey** | No child process can start here; `AUTO` used the standard library. `use_rapidgzip=OFF` silences it |
| A zlib stream with a preset dictionary is not detected, and fails when opened by name | **format** | archivey holds no dictionary |
| A large `.gz` opened without `seekable_members=True` is no faster with `[seekable]` installed | **archivey** | `AUTO` uses `rapidgzip` only on declared seeking (§2.3). Declare seeking, or set `use_rapidgzip=ON` |
| A cold backward seek under `rapidgzip` still re-decodes megabytes, and the log says so | **library** | Its index is sparse: three points over 5 MB of `gzip.compress` output |

## 6. Decisions

| Choice | Why | Rejected |
| --- | --- | --- |
| Never list the gzip trailer CRC-32 (PR #441) | A digest is worth having to skip a decode or to verify one. The trailer covers only the last member, proving there is one member means reading the whole file at every open (a full download for a remote source), the chance magic made large files "multi-member" anyway, and after a read the decoder has already checked every CRC | Scanning for a second member at open; adding the CRC after a full read, which changes `hashes` under a caller who already read it |
| Decode with `zlib`'s gzip window under archivey's engine, not `gzip.GzipFile` (PR #183) | Sized reads recover the prefix of a truncated file, and the engine's seek table and rewind report apply | `GzipFile`, which drops the prefix on `read()` and cannot report rewinds |
| Follow `GzipFile` after a member: skip NULs, refuse other trailing bytes | The standard library is what Python callers compare against | Ignoring trailing bytes, as `gzip -t` does with a warning |
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
- **Native random access for BGZF and other block-reset gzip.** bgzip and `pigz -i` write
  restart points into the file, and BGZF names each block's size; reading them would give
  seeking without `rapidgzip`. Tracked internally.
- **Replaying the standard library engine after a `rapidgzip` abort**, to deliver the rest
  of a cut stream's prefix. Parked in `review/backlog.md`.

## 8. Verify

```bash
./scripts/test.sh tests/test_codecs.py tests/test_single_file.py \
    tests/test_rapidgzip_deflate_zlib.py tests/test_accelerator_corruption.py \
    tests/test_accelerator_truncation_abort.py tests/test_accelerator_shutdown.py \
    tests/test_accelerator_bug3_trap.py -k "gzip or zlib or deflate or rapidgzip or accelerat"
```

| Claim | Pinned by |
| --- | --- |
| Multi-member, NUL padding, trailing junk after the payload | `tests/test_codecs.py::test_gzip_multi_member_and_padding_parity`, `::test_gzip_trailing_junk_delivers_member_then_corruption`, `::test_gzip_multi_member_cross_feed_edges` |
| A truncated stream gives its prefix to sized reads and raises | `::test_truncated_gzip_large_read_recovers_prefix_like_read1`, `::test_truncated_zlib_deflate_large_read_recovers_prefix`, `::test_truncated_gzip_readall_raises` |
| `FNAME`, Latin-1, `MTIME` | `tests/test_single_file.py::test_gzip_stored_filename_surfaced`, `::test_gzip_stored_filename_non_ascii_is_latin1`, `::test_gzip_mtime_surfaced` |
| No size, no CRC, no scan at open | `::test_gz_size_is_always_none`, `::test_gzip_never_reports_a_crc32`, `::test_gzip_open_does_not_scan_for_a_second_member` |
| zlib Adler-32 checked, not listed | `::test_zlib_omits_hashes_but_verifies_adler_on_read` |
| zlib header grammar and probe | `tests/test_detection.py::test_zlib_detected_at_every_legal_window_size`, `::test_zlib_grammar_admits_exactly_66_header_pairs`, `::test_zlib_grammar_accepts_a_preset_dictionary_header` |
| `AUTO` needs declared seeking; `ON` does not. A large `.gz` opened without `seekable_members=True` stays on the standard library | `tests/test_seekable_streams.py::test_accelerator_mode_auto_resolution`, `tests/test_rapidgzip_deflate_zlib.py::test_auto_on_a_large_gz_file_follows_declared_seeking` |
| `AUTO` threshold and size condition; `ON` below it | `tests/test_rapidgzip_deflate_zlib.py::test_the_auto_threshold_is_past_the_child_break_even`, `::test_auto_selects_rapidgzip_above_threshold`, `::test_auto_without_decompressed_size_uses_stdlib_even_when_large`, `::test_on_forces_rapidgzip_below_threshold` |
| The abort happens in-process, and not through archivey | `tests/test_accelerator_truncation_abort.py::test_raw_rapidgzip_aborts_on_truncated_gzip` (canary), `::test_truncated_gzip_with_seekable_members_raises_truncated`, `::test_what_a_cut_stream_delivers_before_the_abort_is_a_correct_prefix` |
| How the child's death is reported; no child → fallback and one warning | `::test_a_child_death_is_reported_by_how_it_ended`, `::test_without_a_child_auto_uses_stdlib_and_on_refuses`, `::test_a_child_that_cannot_start_falls_back_to_stdlib_under_auto`, `::test_an_auto_fallback_warns_once_per_process` |
| The caller's source exception reaches the caller | `::test_an_exception_from_the_callers_source_reaches_the_caller_unchanged` |
| The ISIZE backstop and the empty-end fallback | `tests/test_accelerator_corruption.py::test_rapidgzip_truncation_is_reported`, `::test_rapidgzip_silent_empty_fallback_recovers_prefix`, `::test_rapidgzip_isize_soft_short_raises_on_readall`, `::test_rapidgzip_multimember_not_flagged`, `::test_gzip_backstop_keeps_raising_after_its_own_truncation` |
| A bare zlib stream under `ON` without a size can end short | `tests/test_rapidgzip_deflate_zlib.py::test_standalone_zlib_midcut_may_short_read_through_rapidgzip_on_without_size` |
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
- Registers: [`known-issues.md`](../known-issues.md) (accelerator bugs 1 to 4, soft end,
  the index without member boundaries) · [`threat-model.md`](../threat-model.md) O5, O11
- Decisions: [ADR 0008](../decisions/0008-single-accelerator-rapidgzip.md) ·
  [ADR 0014](../decisions/0014-integrity-verdicts-from-reads-not-close.md) ·
  [`library-analysis.md`](../library-analysis.md) §gzip, §raw Deflate / zlib
- Code: `internal/streams/codecs.py` (`GzipCodec`, `ZlibCodec`, `DeflateCodec`, the
  accelerator selection, `_GzipTruncationCheckStream`) · `internal/streams/decompress.py`
  (`GzipDecoder`, `ZlibDecoder`) · `internal/streams/rapidgzip_child.py`,
  `rapidgzip_worker.py`
- Handbook: [`single-file.md`](single-file.md) · [`zip.md`](zip.md) (DEFLATE members) ·
  [`tar.md`](tar.md) (`.tar.gz`)
