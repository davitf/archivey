## Context

A 7z folder is a coder graph over packed streams. The pipeline accepts only a linear
chain: every coder has one input and one output, `packed_indices == [0]`, and the bind
pairs are exactly `(i+1 → i)` (`sevenzip_pipeline.plan_folder`). The reader refuses a
folder with more than one pack stream before planning (`_folder_pack_view`). BCJ2 fails
both checks.

### What BCJ2 is

7-Zip's x86 branch converter, method `0x0303011B`. The encoder splits an executable
into four streams:

| Input | Holds | Compressed by 7-Zip `-mx9` with |
| --- | --- | --- |
| 0 main | every output byte except the 4-byte targets that were moved out | LZMA2 |
| 1 call | big-endian absolute targets of converted `E8` (CALL rel32) | LZMA, `lc0:lp2` |
| 2 jump | big-endian absolute targets of converted `E9` (JMP rel32) and `0F 8x` (Jcc rel32) | LZMA, `lc0:lp2` |
| 3 rc | one range-coded bit per branch candidate: was it converted? | stored raw |

To decode, copy `main` up to and including the next candidate opcode. Then decode one
bit, using one of 258 probability contexts: one for each possible preceding byte when
the opcode is `E8`, one for `E9`, and one for `Jcc`. On a 1, take four bytes from
`call` (for `E8`) or `jump`, subtract `position + 4`, and emit the result
little-endian. The byte before the next candidate is then the top byte of that target,
not the last `main` byte. The range coder is LZMA's: 11-bit probabilities, move bits 5,
a 5-byte start. That is the whole state machine. It is the 7-Zip 9.20 form of
`Bcj2_Decode` (`C/Bcj2.c`); later 7-Zip restructured the code but not the format.

### What 7-Zip writes

Measured 2026-09-23 with 7-Zip 23.01, parsed with archivey's own parser. This is the
folder for `/usr/bin/git` at `-mx9`:

```
coders       0: LZMA  1→1   1: LZMA  1→1   2: LZMA2  1→1   3: BCJ2  4→1
bind pairs   (in 5 ← out 0)  (in 4 ← out 1)  (in 3 ← out 2)
packed       [2, 6, 1, 0]    main, rc, call, jump in file order
unpack       [231700, 240688, 3593844, 4066232]
```

The BCJ2 coder is the root, and each of its first three inputs is a one-coder chain.
With `-p… -mhe=on`, the same folder gets four `7zAES` coders, one on each pack stream,
so each branch becomes a two-coder chain (AES, then LZMA or LZMA2), and `rc` becomes a
one-coder chain (AES). With `-ms=off`, each member gets its own four-stream folder.

7-Zip picks BCJ2 at `-mx9` only for a file it takes to be an executable. On Linux the
execute bit is part of that test: the same ELF bytes got BCJ2 at mode 0755 and plain
LZMA2 at mode 0644. So a test fixture has to `chmod +x` its input, or
force the coder with `-m0=BCJ2 -m1=LZMA2 -m2=LZMA -m3=LZMA -mb0s0:1 -mb0s1:2 -mb0s2:3`.

### The prototype

`prototype/bcj2.py` is a streaming `Bcj2DecoderStream(main, call, jump, rc,
unpack_size=…)` on `ReadOnlyIOStream`. `prototype/check_against_7zip.py` writes
archives with the `7z` CLI, resolves each BCJ2 folder's coder tree, decodes the three
LZMA branches in memory, and runs the decoder with input block sizes of 64 KiB, 4 KiB,
7 bytes and 1 byte, and with mixed read sizes. It covers a `-mx9` executable, a solid
`-mx9` folder of three executables, forced BCJ2 on an executable whose last byte is
`E8` and on one whose last byte is `0F`, and forced BCJ2 on 300 KB of random bytes.
All 20 runs match the original bytes.

## Goals / Non-Goals

**Goals**

- Every BCJ2 folder 7-Zip writes reads on a core install, encrypted or not, solid or
  not.
- The pipeline takes on only what BCJ2 needs: a tree of linear chains, one view per
  pack stream.
- Hostile input cannot make BCJ2 return wrong bytes silently, or allocate beyond what
  its declared sizes allow.

**Non-Goals**

- A general coder-graph executor. No writer produces a graph that shares an output
  between two inputs, and supporting one means buffering, which is a decode engine
  rather than a planner.
- A C accelerator. The measured speed is acceptable for what BCJ2 is used on (below).
  An optional accelerator can come later behind the same stream.
- Seeking inside a BCJ2 folder.
- BCJ2 in an encoded header.

## Decisions

### D1. Plan a tree of linear chains

`plan_folder` resolves the graph from the unbound output down:

1. Exactly one coder output is unbound. That coder is the root. Zero or more than one
   is `UnsupportedFeatureError`.
2. For each input of a coder, in order, the input is either in `packed_indices` (a leaf
   reading pack stream `packed_indices.index(i)`), or bound to exactly one coder's
   output (recurse there). An input that is neither, or an output bound twice, is
   `CorruptionError`. A coder reached twice (a cycle, or two parents) is
   `UnsupportedFeatureError`.
3. A 1-in coder with a 1-in parent extends its parent's chain, so every branch is a
   linear run. The existing rules plan that run, including LZMA1+BCJ staging and the
   COPY skip.
4. A multi-input coder is accepted only when it is BCJ2 with four inputs and one output.
   Any other multi-input coder is `UnsupportedFeatureError` naming its method ID.

A linear folder resolves to one branch and plans exactly as today, so every existing
folder keeps its current plan. The result is a small tree: `_Bcj2Stage(branches=[4 ×
list[_Stage]], unpack_size)` over per-branch stage lists that each start at a pack
index. `plan_folder` still opens no streams.

**Rejected: special-casing the 7-Zip layout** (coder 3 is BCJ2, coders 0–2 feed it).
The encrypted layout already differs (eight coders, a different bind order), and the
bind pairs make the tree cheap to read anyway.

### D2. One `SharedSource` view per pack stream

The four pack streams are stored one after another, and the decoder reads all four at
once: `main` through LZMA2 in large blocks, the others a few bytes at a time. Each
branch gets its own `SharedView` from the reader's existing `SharedSource`, so each
keeps its own position, and every read re-seeks under the source lock. That is the
"slicing and multiplexing" part, and nothing in `streamtools` changes.
`_folder_pack_view(folder_index)` becomes
`_folder_pack_views(folder_index) -> list[BinaryIO]` (pack `k` is `_folder_pack_starts[folder_index] + k`), and
`open_folder_pipeline` takes that list. The password check calls the same function, so
it covers BCJ2 folders with no code of its own. It decodes the folder and checks the
CRC (`_verify_decoded_folder`).

**Rejected: one view read in order.** The streams are interleaved in time, so a single
position would thrash between them or need all but one stream buffered in memory.

### D3. A pure-Python decoder in `internal/streams/bcj2.py`

The prototype moves in close to as-is. It is codec-level, not `streamtools`, because it
raises archivey's `TruncatedError` and `CorruptionError`. The cost is per **candidate**,
not per byte: a precompiled regex (`[\xe8\xe9]|\x0f[\x80-\x8f]`) finds the next
candidate in C, and the Python loop body runs once per `E8`/`E9`/`0F 8x`. The one place
the regex cannot decide alone is the first byte after a converted branch, or at an
input block boundary, where the preceding byte is the loop's `prev` rather than
`main[i-1]`. The loop checks that byte by hand before searching.

Measured 2026-09-23, CPython 3.11 on a Linux x86-64 container, best of three:

| Input | Size | BCJ2 stage | Whole folder (liblzma branches + BCJ2) | Same file, `-mf=BCJ`, through archivey |
| --- | --- | --- | --- | --- |
| `/usr/bin/git` | 4.1 MB | 13.7 MB/s | 10.1 MB/s | 33.4 MB/s |
| `python3.11` | 6.6 MB | 17.1 MB/s | 12.5 MB/s | 42.4 MB/s |
| 300 KB random, forced BCJ2 | 0.3 MB | ~30 MB/s | — | — |
| hostile: every `main` byte `E8` | 2 MB | 1.5 MB/s | — | — |
| hostile: `main` all `0F 80` | 2 MB | 1.9 MB/s | — | — |

So a BCJ2 folder reads at about 30% of the speed of the same file under BCJ. That is
slow next to liblzma, but it only applies to executables written at `-mx9`, which
today do not open at all. With 64 KiB input blocks the stream reads at full speed. At
7-byte blocks the speed drops to about 6 MB/s, and at 1-byte blocks to about 1.5 MB/s,
so the real implementation keeps its inputs block-buffered, as the prototype does.

### D4. Forward-only

`Bcj2DecoderStream.seekable()` is False. `_open_member` already handles a non-seekable
folder stream: it skips forward to the member's prefix. So `open()` on a BCJ2 member
decodes from the folder start, as a solid folder does today, and a sequential
`stream_members()` pass decodes each folder once. Seeking could be added later by
rewinding all four branches, but nothing needs it now.

### D5. What the decoder refuses

- An input that runs out before the declared `unpack_size` is produced raises
  `TruncatedError` naming the stream.
- After the last output byte, bytes left in `main`, `call` or `jump` raise
  `CorruptionError`. Their sizes follow from the same conversion decisions, so a
  leftover means the four streams disagree. Measured: 7-Zip 23.01 output leaves all
  three at exactly zero in every folder the prototype decoded.
- `rc` leftovers and a non-zero final range-coder `code` are **not** checked in the
  first version. In 7-Zip 23.01 output both were zero every time. But the prototype
  normalizes lazily, and output from an older encoder (7-Zip 9.20 / p7zip 16.02)
  has not been measured. Open question 1 below.
- A converted target that runs past `unpack_size` is truncated, as 7-Zip 9.20's
  decoder does. The member CRC catches a hostile case.
- Allocation: output is bounded by the coder's declared `unpack_size`, and every read is
  a bounded block. No declared size drives an allocation.

The member CRC32 stays the integrity backstop, as for every other coder.

### D6. Where the cost goes, and the limits that bound it

- **CPU.** The worst case is one loop iteration per output byte, about 1.5 MB/s, from a
  `main` that is all candidates. LZMA2 compresses that to almost nothing, so a small
  archive can declare a large, slow member. `ExtractionLimits` (`max_extracted_bytes`,
  `max_ratio`) bound it in `extract_all`. A caller reading `open()` to the end has no
  limit, as with any decompression bomb: the amount is the same, only the rate is
  lower. The threat model gets a row.
- **Memory.** Three LZMA decoders run at once, each with its own declared dictionary
  (`LZMA2:22` + 2 × `LZMA:20` for git, up to `LZMA2:26` at `-mx9 -md=64m`).
  `DecoderLimits` guards PPMd only today. When the LZMA dictionary guard lands, which
  is its next planned consumer, a BCJ2 folder must count the sum of its branches.
  This change does not add the LZMA guard. It leaves a task to check that the guard's
  unit is the folder.

### D7. The encoded header stays linear

`decode_encoded_header` and `encoded_folder_slices` assume one pack stream per folder.
No writer puts BCJ2 on a header, and the header decode is bounded work that runs before
anything lists. So a multi-pack encoded-header folder keeps raising
`UnsupportedFeatureError`.

## Risks / Trade-offs

- **An older encoder's `rc` tail.** If 7-Zip 9.20-era output ends with bytes the lazy
  decoder never needs, a strict `rc` check would refuse good archives. Mitigation: the
  first version does not check `rc` (D5), and open question 1 asks for a measurement.
- **Speed expectations.** A user comparing with `7z x` will see about 3× slower on these
  members. The cost is recorded in `7z.md` §5, like the solid-folder re-decode.
- **More moving parts per member.** Four codec stacks share one handle. The source
  lock serializes their reads, which costs seeks, not correctness. The main branch
  reads 64 KiB blocks, so the lock's cost is small.

## Open Questions

1. **Does older 7-Zip output leave bytes in `rc`, or a non-zero `code`?** Answer it by
   building fixtures with 7-Zip 9.20 or p7zip 16.02, neither of which this container
   has (its `p7zip-full` is a transitional package for 7-Zip 23.01). If both are clean,
   add the strict check as an `ARCHIVE_INTEGRITY_CODES` diagnostic, so strict policy
   refuses and the default reports.
2. **Is 1.5 MB/s worst case acceptable without a cap?** The recommendation is yes: the
   same bytes read through `open()` are unbounded for every codec, and extraction is
   already bounded. The alternative is a `DecoderLimits` field for "work per output
   byte", which no other codec has.
