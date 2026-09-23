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
archives with the `7z` CLI, resolves each BCJ2 folder's coder tree, decrypts and
decodes the branches in memory, and runs the decoder with input block sizes of 64 KiB,
4 KiB, 7 bytes and 1 byte, and with mixed read sizes. It covers a `-mx9` executable, a
solid `-mx9` folder of three executables, an encrypted `-mx9 -mhe=on` folder (the
eight-coder layout), forced BCJ2 on an executable whose last byte is `E8` and on one
whose last byte is `0F`, and forced BCJ2 on 300 KB of random bytes. Each folder's
output must equal its members' files joined in the archive's own file-table order.
All 24 runs match. Three negative checks also pass: a `call` or a `main` stream one
byte short raises `TruncatedError`, and a `main` stream with a 16 MiB tail raises
`CorruptionError` without reading the tail past the block already buffered.

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
  `pylzma` could become one later, behind the same stream (D8, open question 3).
- Seeking inside a BCJ2 folder.
- BCJ2 in an encoded header.

## Decisions

### D1. Plan a tree of linear chains

`plan_folder` resolves the graph from the unbound output down. Two error types, split
the way the rest of this reader splits them: a graph that cannot be a valid folder is
`CorruptionError`, and a valid graph shape the planner will not run is
`UnsupportedFeatureError`.

1. The root is the coder whose output no bind pair consumes. None (every output is
   consumed, so the graph has a cycle) is `CorruptionError`. More than one is
   `UnsupportedFeatureError`: a folder with two final outputs is a shape no writer
   produces, not a contradiction.
2. For each input of a coder, in order, the input is either in `packed_indices` (a leaf
   reading pack stream `packed_indices.index(i)`), or bound to exactly one coder's
   output (recurse there). Each of these is `CorruptionError`: an input that is neither,
   an input in both, an output bound to two inputs (a coder with two consumers is the
   same condition), and a coder reached again while resolving its own inputs (a cycle).
3. A 1-in coder with a 1-in parent extends its parent's chain, so every branch is a
   linear run. The existing rules plan that run, including LZMA1+BCJ staging and the
   COPY skip, **with the run's own indices**. Today `plan_folder` takes a `SINGLE`
   stage's input length from `folder.unpack_sizes[index - 1]`, the previous coder in
   list order. That is only right while the folder is one chain. In a tree, the
   previous coder in the list is usually a sibling branch's (in 7-Zip's layout, coder 1
   is `call`'s LZMA and coder 2 is `main`'s LZMA2). So each branch is planned from its
   own list of coder indices, and "the preceding coder" means the previous coder in
   that branch. A `[7zAES, BZip2]` `main` branch is the case that would otherwise
   read the wrong length.
4. A multi-input coder is accepted only when it is BCJ2 with four inputs and one output.
   Any other multi-input coder is `UnsupportedFeatureError` naming its method ID.

Planning errors must stay outside the password check's error mapping. Inside
`_password_for_folder`, `confirm` turns any `ArchiveyError` other than
`UnsupportedFeatureError` (and two others) into "Wrong password or corrupt 7z folder",
and the password manager then tries the next candidate. A structural
`CorruptionError` mapped that way would use up every candidate and still report a
password problem. Today `plan_folder` runs inside `open_folder_pipeline`, which
`confirm` calls *before* its `try`, so a planning error already reaches the caller as
itself (`sevenzip_reader.py`, `_password_for_folder`). The tree planner keeps that
property, and task 1.5 pins it with an encrypted malformed folder.

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
  three at exactly zero in every folder the prototype decoded. **The check reads at
  most one byte from each input**: it looks at what is left in the block buffer, and
  when that is empty it calls `read(1)`. It never drains an input. `main` is an LZMA2
  decoder whose own declared size comes from the header, and bytes decoded inside the
  BCJ2 stage never reach the folder stream that `ExtractionLimits` counts, so a drain
  would decompress a hostile branch in full with no limit watching it. The prototype
  does this in `_check_inputs_finished`. `leftover()`, which drains, exists only for
  the verification script.
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
  This change does not add the LZMA guard, so it roughly triples an unguarded,
  header-declared allocation per BCJ2 folder until that guard exists. Two tasks record
  it: a note on the `dev-docs/IDEAS.md` entry that tracks the LZMA guard ("Threat-model
  row for archive-declared decoder memory") saying the unit is the folder, and a
  threat-model row for this memory cost beside the CPU one.

### D7. The encoded header stays linear

`decode_encoded_header` and `encoded_folder_slices` assume one pack stream per folder.
No writer puts BCJ2 on a header, and the header decode is bounded work that runs before
anything lists. So a multi-pack encoded-header folder keeps raising
`UnsupportedFeatureError`.

### D8. Our own decoder, not `pylzma` or `libarchive-c`

Two C-backed packages decode BCJ2. Both were measured 2026-09-23 in a scratch venv on
the same archives as the prototype (`git` and `python3.11` at `-mx9`, a solid folder,
output ending on `E8` or `0F`, forced BCJ2 on random data). Both return the correct
bytes on every one.

| | `pylzma` 0.6.1 (`bcj2_decode`) | `libarchive-c` 5.3 over libarchive 3.7.2 | prototype |
| --- | --- | --- | --- |
| What it is | One function over four in-memory buffers, from LZMA SDK 25.01 | A whole second 7z reader, in C | A stream over four streams |
| BCJ2 stage speed | 530–900 MB/s | not separable | 14–17 MB/s |
| Whole archive read | — | 11–31 MB/s through `get_blocks()` | 10–12 MB/s |
| Memory | All four inputs plus the whole output | libarchive's own buffers | Bounded blocks |
| `dest_len` of 1 TiB | `MemoryError`: it allocates the declared size | — | Nothing allocated from a size |
| Truncated or damaged input | `TypeError("bcj2 decoding failed")` for any of the four | libarchive's error | `TruncatedError` naming the stream, or `CorruptionError` |
| Encrypted 7z | n/a (archivey does the AES) | Refused ("Damaged 7-Zip archive"; header-encrypted: "not supported") | Works through the existing AES stage |
| Packaging | LGPL-2.1. No wheel for CPython 3.11 on Linux x86-64: pip built it from source here | Loads the system `libarchive` with ctypes; the wheel ships none | Nothing new |

`pylzma` survived 3,000 random mutations of the four inputs with no crash: it either
returned the declared length or raised the `TypeError`. It is the fast and plausible
option. The problems are its API and its packaging, not its correctness. It takes
whole buffers, so a folder's four streams and its output all sit in memory at once.
It sizes its output from `dest_len`, which is the archive's declared unpack size. That
is the same shape as the blocking sweep findings, where a header number drives an
allocation before validation, and here the allocation would be the whole folder. Its
errors do not say which stream failed, and a missing wheel means a C compiler at install
time for a core codec.

`libarchive-c` is the fallback reader that ADR 0001 and `7z.md` §6 already reject: a
second decompressor stack with its own parser, bugs and cost model, reading hostile
input in C. It also cannot open encrypted 7z at all, so it would cover only part of the
BCJ2 cases.

**Decision:** decode BCJ2 in Python, as in D3. Keep `pylzma` in mind as an optional
accelerator for the BCJ2 stage only (open question 3).

### D9. What `member.compression` says for a BCJ2 folder

`_build_folder_compression` walks `folder.coders` in list order and flattens every
coder into one tuple. For a tree, that puts sibling branches side by side. Measured on
a 7-Zip `-mx9` archive of `/usr/bin/git`, which lists today because listing never
reaches `plan_folder`: `(lzma, lzma, lzma2, bcj2)`. The two `lzma` entries are the
`call` and `jump` branches, and nothing says so.

The field's contract already answers this. `CompressionMethod`'s docstring
(`types.py`) orders the tuple in the pack direction ("pre-filters first, packing codec
last, closest to the stored bytes") with `7z (BCJ2, LZMA2)` as its example. The
`archive-data-model` compression matrix pins the same case: "7z member uses BCJ2 +
LZMA2 → `(CompressionMethod(BCJ2), CompressionMethod(LZMA2))`". So for a tree the
tuple is **the root coder, then its `main` (first-input) branch, in the pack
direction**. The `call`, `jump` and `rc` side streams are not listed. They are part of
BCJ2, not codecs a member was packed with, and listing their LZMA coders would
suggest two more compression passes that never happened. AES stays out, as it is today.
The `format-7z` BCJ2 scenario pins `(BCJ2, LZMA2)` exactly.

**Found alongside, and not this change's to fix:** today's 7z tuple for a *linear*
folder is in the **decode** direction, the reverse of the docstring and the spec. A
`-mf=BCJ` folder lists `(lzma2, bcj)`, and `-m0=Delta:4 -m1=LZMA2` lists `(lzma2, delta)`.
That is a public-field bug on today's archives, independent of BCJ2, and it is tracked
internally as its own fix. The rule above assumes that fix: a root-plus-main-branch tuple
in the pack direction is the same ordering it restores for linear chains.

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
3. **Should `pylzma` be an optional accelerator for the BCJ2 stage?** It is 30–60×
   faster and correct, but it takes whole buffers and allocates `dest_len` up front
   (D8). Using it safely would mean calling it only for folders whose declared size
   fits under a cap (a `DecoderLimits` field, or the existing 2 GiB decoder cap), and
   adding a dependency with no Linux wheel. The recommendation is not now: first ship the
   pure-Python stream, then decide on measured demand.
