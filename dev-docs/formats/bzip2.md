# bzip2

Current maintainer truth for bzip2 (`.bz2`) as a single-file format and as a coder inside
TAR, ZIP and 7z. Two backends decode it: the standard library's `bz2`, always available,
and the bzip2 decoder bundled in the optional `rapidgzip` package, which runs in this
process and gives random access. What bzip2 shares with the other codecs, the one-member
reader, the seek table and the truncation contract, is on [`single-file.md`](single-file.md).
Registers keep the status; this page states the behaviour and links the row.

## At a glance

| | |
| --- | --- |
| Read | bzip2 as a one-member archive, as a ZIP and 7z coder, and inside `.tar.bz2` |
| Write | **Not shipped** |
| Backends | The standard library's `bz2.open`. `rapidgzip.IndexedBzip2File` from `[seekable]`, used when `use_indexed_bzip2` selects it (§2.3) |
| Seeking | Without the accelerator, a backward seek decodes again from the start. With it, from the nearest block of the index it builds while decoding |
| Size | `None`. bzip2 has no size field |
| Digests | None listed. Every block's CRC and the stream's combined CRC are checked on read |
| Metadata | None beyond the shared fields |
| Truncation | Always raised: `TruncatedError` from the standard library, `CorruptionError` through the accelerator (§5) |
| Refuses | Nothing bzip2-specific |

**Three things a reader might expect and will not find.** `member.size` is `None` until the
whole stream has been read: nothing in the file records it. The error for a cut file
depends on the accelerator: the same file raises `TruncatedError` with it off and
`CorruptionError` with it on. And the accelerator has no size threshold the way `rapidgzip`
has for gzip: under `AUTO` it is used for any bzip2 stream where seeking was declared,
however small.

## 1. Shape

Three properties generate most of this page.

```
stream:  "BZh" level('1'..'9')
         block:  pi(48 bits) CRC(32) randomised(1) origPtr(24) Huffman tables … data
         block:  …                                 ← not byte-aligned
         end:    sqrt(pi)(48 bits) combined CRC(32) padding to a byte
stream:  "BZh" …                                   ← a following stream is legal
```

**Blocks are independent, but not byte-aligned.** Each block is a Burrows–Wheeler
transform of up to 900 kB of input (the level digit times 100 kB), with its own Huffman
tables and CRC. A decoder can start at any block without the ones before it. But the
blocks are packed bit by bit: a block starts at a 48-bit magic that can sit at any bit
offset. So finding blocks means a bit-level scan, and a seek table stores a bit offset.
That is what `rapidgzip`'s bundled decoder does, and why the standard library, which does
not, re-decodes from the start on a backward seek.

**The first output needs a whole block.** The Burrows–Wheeler transform is inverted per
block, so no byte of a block comes out until the whole block has been read and sorted
back. The first byte of a stream costs up to 900 kB of decoded output. Open-time
validation (§2.2) and detection's inner-TAR probe (§2.1) both pay that cost.

**A file can be several streams, and nothing counts them.** `pbzip2` and `lbzip2` write one
stream per chunk, and `cat a.bz2 b.bz2` is valid. No stream records the uncompressed size;
the combined CRC covers only its own stream. So neither a size nor a whole-file digest
exists without decoding everything.

## 2. The pipeline here

### 2.1 Identify

bzip2 is the magic `BZh` at offset 0, reported `CERTAIN`. The level digit is not checked.
The inner-TAR probe then decodes 512 bytes to see whether a TAR header follows
([`single-file.md`](single-file.md) §2.1). That probe may read up to 1 MiB of compressed
input for any codec, a bound sized for bzip2's worst case: a single block of level-9 input
can be around 900 kB compressed, and the first byte of output comes only at its end. The
other codecs produce the TAR header from the detection prefix, so in practice only bzip2
reads that far. A `.tar.bz2`
whose first block is that large is still found as `TAR_BZ2`, from a pipe too. `.bz2`,
`.tbz`, `.tbz2` and `.tar.bz2` are the registered extensions.

### 2.2 Open and list

The member has no metadata of its own: `size` is `None`, `modified` is `None`, and
`hashes` is empty. The name comes from the file name ([`single-file.md`](single-file.md)
§2.2).

On a seekable source, the reader decodes one byte at open to prove the source is bzip2
([`single-file.md`](single-file.md) §2.2). For bzip2 that one byte costs the first block.
A file with valid first blocks and damage later opens and lists; the damage raises on
read.

### 2.3 Member data

**The standard library engine.** `FramedDecompressorStream` runs one `bz2.BZ2Decompressor`
per stream, which checks each block's CRC and the stream's combined CRC, and starts
another when the bytes after a stream are `BZh` and a block size digit. Anything else
after the last stream is reported as `ARCHIVE_TRAILING_DATA` unless it is zeros
([`single-file.md`](single-file.md) §2.3); `bz2.open`, which this replaces, ignored it
without a word. A stream that ends before its end marker is `TruncatedError`. An `OSError`
saying "Invalid data stream" is `CorruptionError`. It can seek, but a backward seek
decodes again from the start, and the rewind report says so.

**When the accelerator is used.** `use_indexed_bzip2` is an `AcceleratorMode`, `AUTO` by
default:

| Mode | bzip2 |
| --- | --- |
| `OFF` | The standard library, always |
| `ON` | `rapidgzip.IndexedBzip2File`, or `PackageNotInstalledError` without `rapidgzip`, or `StreamNotSeekableError` on a source that cannot seek (a pipe, or a member stream of an outer archive opened without `seekable_members`) |
| `AUTO` | The accelerator when seeking was declared (`seekable_members=True`, `open_stream(seekable=True)`), the source is seekable and `rapidgzip` is installed. Otherwise the standard library, silently |

There is no size threshold and no child process, unlike the DEFLATE family
([`gzip.md`](gzip.md) §2.3). The in-process decoder has not been seen to abort on a cut or
corrupt stream: 40 runs of the truncation sweep and the corpus mutation harness produced
Python exceptions only. So it runs in the caller's process, with three guards around it.

- **A corrupt source must not read as empty.** The bundled decoder returns no output and no
  error for input that is not bzip2 at all: 40 000 zero bytes, a zero-byte file, a bare
  `BZh9`. Its size, compressed position and block offsets for that input are the same as
  for a valid empty stream, so nothing it reports tells them apart.
  `_Bzip2EmptyStreamCheck` therefore hands the first read that comes back empty, before any
  byte was delivered, to the standard library over a fresh view of the source. That engine
  raises for garbage and reads a genuinely empty stream as `b""`. A seek away from 0 before
  the first read disarms the check, but the decoder clamps every seek to 0 on such input, so
  garbage cannot slip past that way.
- **Bytes after the last stream must be reported the same way.** The decoder skips them
  and prints a warning to standard error, which archivey cannot catch. At the end of data,
  `_Bzip2EmptyStreamCheck` asks the decoder for its compressed position
  (`tell_compressed()`, in bits, rounded up to a byte; exact after the end, measured with
  `parallelization=0`), scans a fresh view of the source from there for the first non-zero
  byte, and reports it at the offset the standard library engine would.
- **An exception from the caller's source must not abort the process.** `rapidgzip` calls
  `std::terminate` when a Python file object it reads from raises. The source is wrapped in
  `_TrappingSource`, which parks the exception, hands the decoder an end of data, and lets
  `_AcceleratorStream` raise the parked exception to the caller after the call returns,
  unchanged and not translated.
- **An unclosed decoder must not abort at shutdown.** `rapidgzip` threads outlive an object
  that was never closed and abort the interpreter at exit. `_AcceleratorStream` closes it
  through `weakref.finalize`.

The input is also clipped to the known compressed length (`_bound_rapidgzip_source`).
Otherwise a 7z AES stage's padding after the end marker reaches the decoder, which prints
"Trailing garbage after EOF ignored!" to standard error, outside archivey's diagnostics.

**Errors through the accelerator.** Its messages carry no exception type archivey can
match on, so `Bzip2Codec._translate_accelerator` matches the text. "Calculated CRC",
"[BZip2 block", "Huffman", "magic", "bit string", "bad optional access" and a bare
`std::exception` or "Unknown exception" are `CorruptionError`; each was seen on a corrupt
file. A cut stream is one of these: under the accelerator every cut in the truncation
sweep came back as `RuntimeError("std::exception")`, which says nothing about where the
input ended, so it is `CorruptionError`, not `TruncatedError` (§5). A non-seekable source
is `StreamNotSeekableError`.

### 2.4 Extract

Nothing here is bzip2-specific ([`single-file.md`](single-file.md) §2.4).

### 2.5 Write

Not shipped.

## 3. In the wild

Measured with the tools listed on [`single-file.md`](single-file.md) §3, with the
accelerator `OFF` and `ON`.

| Producer | archivey |
| --- | --- |
| `bzip2` | Reads |
| `pbzip2`, `lbzip2` | Reads, as a run of streams |
| Two `bzip2` files concatenated | Reads both payloads |
| A stream followed by `junk` | Reads, then `ARCHIVE_TRAILING_DATA` at the same offset with the accelerator off and on. With it on, "[Warning] Trailing garbage after EOF ignored!" is also printed to standard error. `bzip2 -t` warns and exits 0 |
| A stream cut at any of 20 points | `TruncatedError` with the accelerator off, `CorruptionError` with it on. Never a short read with no error |

## 4. Threat surface

bzip2-specific only; the shared items are [`single-file.md`](single-file.md) §4.

- **The accelerator is native code in the caller's process.** Unlike the DEFLATE family,
  nothing contains an abort from the bzip2 decoder. None has been seen on cut or mutated
  input, and a raising Python source is trapped (§2.3), but a new abort would end the
  caller. The fuzzers run with accelerators off, and `SECURITY.md` tells callers who need
  that guarantee to keep them off for untrusted input (threat-model O5).
- **A small file costs a whole block.** Each block expands up to 900 kB before the first
  output byte, and detection's inner-TAR probe reads up to 1 MiB of compressed input to
  reach it. Both are bounded by the block size; the detection budget bounds the probe
  (threat-model O11).
- **Garbage must not read as empty.** Covered by `_Bzip2EmptyStreamCheck` (§2.3). Without
  it a corrupt file would pass a read as a valid empty member.

## 5. Sharp edges

*Where it lives*: **format** — inherent, no implementation fixes it · **library** — an
upstream library's behaviour, fixable only there or by replacing it · **archivey** — ours.

| What you see | Where it lives | More |
| --- | --- | --- |
| `member.size` is `None` for a `.bz2` | **format** | No size field (§1) |
| A cut `.bz2` raises `TruncatedError` with the accelerator off and `CorruptionError` with it on | **library** | The accelerator reports only `std::exception` (§2.3). Catch `ReadError` for both |
| Opening a small `.bz2` decodes up to 900 kB | **format** | Open-time validation needs the first block (§1, §2.2) |
| "[Warning] Trailing garbage after EOF ignored!" on standard error | **library** | The accelerator prints it for bytes after the last stream in a standalone file; archivey cannot route it into diagnostics. Tracked internally |
| A backward seek re-decodes from the start | **format** | Install `[seekable]` and pass `seekable_members=True` |
| A cold backward seek with the accelerator still re-decodes up to a block, and may log it | **format** | Blocks are the unit of random access |
| Trailing junk after the last stream is a warning | **archivey** | The rule every codec shares ([`single-file.md`](single-file.md) §6); `DiagnosticPolicy.strict()` raises |

## 6. Decisions

| Choice | Why | Rejected |
| --- | --- | --- |
| The bzip2 accelerator is `rapidgzip`'s bundled decoder (ADR 0008) | `indexed_bzip2` and `rapidgzip` loaded together corrupt the heap on macOS; `rapidgzip` covers both codecs | The separate `indexed_bzip2` package |
| Keep the bzip2 accelerator in-process (PR #493) | It has not been seen to abort on cut or corrupt input, and a child costs a process start per stream | Moving it to the child with the DEFLATE family |
| Fall back to the standard library on a first empty read (PR #461) | The decoder's own state cannot tell garbage from an empty stream, and an accelerator must not change whether a corrupt source raises | Trusting the empty result; checking the magic by hand, which misses a valid header followed by garbage |
| Trap the caller's source exception (PR #462) | `rapidgzip` terminates the process when a Python source raises | Letting the exception cross the native boundary |
| No size threshold for `AUTO` | The in-process decoder costs no child start; seeking was asked for | Reusing the 16 MiB DEFLATE gate |
| Let the inner-TAR probe read up to 1 MiB of compressed input, for every codec (PR #32) | bzip2's first output comes only after a whole block; one bound for all codecs needs no per-codec branch | Probing only the detection prefix, which called a `.tar.bz2` with a large first block plain `BZ2` |
| Find the accelerator's end from its compressed position and scan the source | Its warning goes to standard error, and the offset must match the standard library engine's | Clipping the source to the end, which is found only by decoding |
| Translate the accelerator's errors by message text | They carry no distinct type; each string was seen on a real corrupt file | Treating every `RuntimeError` as corruption, which would hide archivey's own bugs |

## 7. Open questions

- **An upstream report for the garbage-reads-as-empty behaviour.** The workaround is in
  place; no report has been drafted. Tracked internally.
- **Standard error output from the accelerator** on trailing bytes. Removing it needs an
  upstream option or clipping a standalone file to its last end marker, which is only found
  by decoding. Tracked internally.
- **Telling truncation from damage under the accelerator.** It would need `rapidgzip` to
  report where decoding stopped; today `std::exception` carries nothing.

## 8. Verify

```bash
./scripts/test.sh tests/test_single_file.py tests/test_accelerator_corruption.py \
    tests/test_accelerator_truncation_abort.py tests/test_seekable_streams.py \
    tests/test_detection.py tests/test_exception_handlers.py -k "bz"
```

| Claim | Pinned by |
| --- | --- |
| Size is `None` before a full read | `tests/test_single_file.py::test_bz2_size_none_before_full_read` |
| Garbage raises in every accelerator mode; a valid empty stream reads empty | `::test_corrupt_bz2_raises_whatever_the_accelerator_mode`, `tests/test_accelerator_corruption.py::test_bzip2_not_a_stream_raises_in_every_accelerator_mode`, `::test_indexed_bzip2_valid_empty_stream_reads_empty` |
| A seek before the first read does not bypass the check | `::test_indexed_bzip2_seek_before_read_still_raises` |
| Accelerator errors become `CorruptionError`; intact files read clean | `::test_indexed_bzip2_corrupt_translates_to_corruption`, `::test_indexed_bzip2_intact_reads_clean` |
| The caller's source exception reaches the caller unchanged | `::test_bzip2_callers_source_exception_reaches_the_caller_unchanged`, `tests/test_exception_handlers.py::test_bzip2_accelerator_traps_a_failing_caller_source` |
| bzip2 stays in-process | `tests/test_accelerator_truncation_abort.py::test_bzip2_stays_in_process` |
| The rewind report with the accelerator off; `ON` without the package | `tests/test_seekable_streams.py::test_bzip2_accelerator_off_warns_on_rewind`, `::test_bzip2_accelerator_on_without_package_raises` |
| A `.tar.bz2` with a large first block is found, from a pipe too; a bare one stays `BZ2` | `tests/test_detection.py::test_inner_tar_over_bzip2_large_block_is_tar_bz2`, `::test_inner_tar_over_bzip2_large_block_non_seekable`, `::test_bare_bzip2_large_block_stays_bare_bz2` |

**Building fixtures.** The standard library writes bzip2 (`bz2.compress`). `pbzip2` and
`lbzip2` install from the distribution.

## 9. References

- The bzip2 format has no formal specification; Joe Tsai's "BZip2 format" notes
  (github.com/dsnet/compress, `doc/bzip2-format.pdf`) describe the block and stream
  layout, and `bzip2` 1.0.8's source is the reference implementation
- [`rapidgzip`](https://github.com/mxmlnkn/rapidgzip), version 0.16, `IndexedBzip2File`
- Registers: [`known-issues.md`](../known-issues.md) (accelerator bugs 1 to 3) ·
  [`threat-model.md`](../threat-model.md) O5, O11
- Decisions: [ADR 0008](../decisions/0008-single-accelerator-rapidgzip.md) ·
  [`library-analysis.md`](../library-analysis.md) §bzip2
- Code: `internal/streams/codecs.py` (`Bzip2Codec`, `_Bzip2EmptyStreamCheck`,
  `_TrappingSource`, `_AcceleratorStream`, `_bound_rapidgzip_source`)
- Handbook: [`single-file.md`](single-file.md) · [`gzip.md`](gzip.md) (the DEFLATE side of
  `rapidgzip`) · [`tar.md`](tar.md) (`.tar.bz2`)
