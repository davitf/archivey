# Design — native TAR reader

Evidence for the change: the tarfile workaround inventory (20 places, about 650 of the
backend's 1 810 lines on `main` when it was taken, 7 of them on private tarfile API),
summarised in `proposal.md`. This page is how the replacement is built.

## Goals and non-goals

Goals:

- Read every header encoding that tarfile reads today, with no Python-version or
  patch-level dependence.
- Keep the reader contract: every listing, byte, diagnostic and error that the current
  test suite pins stays the same, except for the changes listed in §"Behaviour that
  changes". The tests that the open TAR fix PRs add (bounded header chains, sparse maps
  and global records; old-style directory data; sparse-map ordering) are acceptance
  tests for the new reader.
- Bound memory by construction: nothing is allocated from a size field before the
  budget allows it, and nothing is kept that the listing does not need.
- Delete the workaround layer, not wrap it.

Non-goals (each is a separate, later change):

- Salvage past a rejected header (GNU tar's "Skipping to next header"). A rejected
  header stays `CorruptionError` after the members before it, as today (DR-2). The
  walker makes resync possible later; it does not do it now.
- Reading concatenated archives (`tar -i`). The first trailer still ends the walk.
- Writing TAR. tarfile stays the fixture writer and a test oracle, as `py7zr` and
  `rarfile` are for 7z and RAR.
- Multi-volume continuation (`M`) and volume headers (`V`): they list as `OTHER`, as
  today, with their data skipped by size.

## Module layout

| Module | Contents | Size |
| --- | --- | --- |
| `internal/backends/tar_parser.py` (new) | Header block decode, number fields, checksum, PAX records, GNU sparse maps (all four encodings), the old-style directory rule, and `TarWalker`, the loop over one byte stream. No archivey reader types | 1 125 (PR 738 at `707d506`) |
| `internal/streams/streamtools/sparse.py` (new) | `SparseStream`: a member's logical bytes over its stored bytes, holes as zeros | 141 (PR 739 at `5e2f441`) |
| `internal/backends/tar_reader.py` (rewritten) | `TarReader` (the `BaseArchiveReader` hooks), EOF and trailing-data policy, metadata mapping | ~950 (estimate), from 1 917 |

Sizes as of 2026-10-10. The total is about 2 220 lines against 1 917 on `main` at
`e670df1` (PR 706 merged), and about 2 140 once PRs 704 and 716 merge: about flat, not
smaller. What goes is the workaround layer: the
20 sites in the inventory, the 7 hooks on private tarfile API, the two stdlib function
bodies PR 704 copies under source-hash pins, and the ~350 lines the open PRs add.

The split follows `rar_parser.py` / `rar_reader.py` and `sevenzip_parser.py` /
`sevenzip_reader.py`: the parser module is what the fuzz target and the differential
test drive, and has no reason to import the reader. The walker lives there too, so the
whole header path can be tested against `tarfile` before the reader switches to it. `SparseStream` goes in `streamtools/` because it knows
nothing about TAR: it maps logical ranges to stored ranges over any byte stream.

## The parser (`tar_parser.py`)

### Header block

`parse_header_block(block: bytes, offset: int) -> HeaderBlock | ZeroBlock | RejectedBlock`.
One 512-byte block in, one of three results out. No exception for an expected outcome:
the walker decides what a rejected block means at its position.

- **Zero block:** all 512 bytes are NUL.
- **Checksum:** the sum of the block with the checksum field read as eight spaces. Both
  the unsigned and the signed sum are accepted, as GNU tar and tarfile accept both. A
  mismatch is a `RejectedBlock` whose `reason` says so.
- **Magic:** `ustar\0` + `00` is POSIX ustar (the `prefix` field joins the name).
  `ustar  \0` is old GNU (no prefix; the slots at 345 to 494 are `atime`, `ctime`,
  `offset`, `longnames`, four sparse entries, `isextended` and `realsize`). Anything
  else is v7: name, mode, ids, size, mtime, typeflag and link name only. Reading
  `prefix` only under the ustar magic is GNU tar's rule (DR-6) and a listing change:
  `tarfile` joins bytes 345 to 500 for every typeflag but `L`, `K` and `S`, whatever
  the magic. GNU tar's incremental mode (`tar -G`) fills the old GNU `atime` slot, so
  today every member of such an archive lists under a directory of digits
  (`15262452373/d/f.txt` for `d/f.txt`; checked on `main` with GNU tar 1.35).
- **Numbers:** octal, NUL- or space-terminated, leading spaces allowed, an all-NUL or
  all-space field is 0 (tarfile and GNU tar agree). GNU base-256: first byte `0x80` is
  positive, `0xFF` negative, two's complement over the rest of the field. A field that
  is neither, and a negative `size`, is a `RejectedBlock` naming the field. This
  base-256 form is `tarfile`'s, on purpose: GNU tar takes any first byte with the high
  bit set and counts its low seven bits in the value, so it reads a field starting
  `0x81` that this parser rejects. Such a value is at least 2**56 in magnitude, which
  no real id, mode, size or time reaches, and every answer stays `tarfile`'s.
- **Strings** stay `bytes`, cut at the first NUL. Decoding happens in the reader, once,
  with the name's source known (§"Names and encodings").

`HeaderBlock` is a frozen slotted dataclass of the decoded fields plus `format`
(`V7`, `USTAR`, `GNU`), `typeflag: bytes` and the GNU sparse slots when present.

### Extended headers

Typeflags `x` and `X` (Solaris) carry per-member PAX records, `g` global ones, `L` a GNU
long name and `K` a GNU long link name. The walker reads their data (§"The walker") and
hands it to:

- `parse_pax_records(data, *, binary_default) -> list[tuple[bytes, PaxValue]]`. Records
  are `"<len> <key>=<value>\n"`. The length is decimal, covers the whole record
  including itself, and must land on the `\n`. Anything else is a header that does not
  parse: the walker ends with a rejected `TarEnd` at the extended header, which the
  reader classifies as it classifies tarfile's `InvalidHeaderError` today. Keys and
  values stay bytes.
  Each value carries whether its block (or the global records before it) declared
  `hdrcharset=BINARY`, as POSIX scopes it. An empty value in a `g` header deletes the
  key; in an `x` header it cancels the keyword for that member. Duplicate keys: the
  last one wins, except the `GNU.sparse.offset` / `GNU.sparse.numbytes` pairs of
  sparse 0.0, which are kept in order.
- A GNU long name is the data up to the first NUL.
- A PAX `size` that is not a number is `CorruptionError`: the walk cannot find the next
  header without it (`tarfile` reads it as 0). A PAX `uid` or `gid` that is not a
  number is ignored and the header's value kept.

### GNU sparse

`SparseMap` is a pair of `array("q")` (offsets and lengths), not a list of tuples: 16
bytes per entry instead of about 150.

`max_metadata_bytes` counts header text, not allocator bytes, and the parser charges it
one way: the bytes the archive spends on what the listing keeps. An extended header is
charged its declared size. A sparse entry is charged 24 bytes, its width in the one
fixed-width encoding (two 12-byte numbers in an old GNU header), whatever encoding it
came in; `archive-reading` already sets that weight. Parsers:

| Encoding | Where the map is | Parsed |
| --- | --- | --- |
| Old GNU, typeflag `S` | 4 slots in the header, then 21-slot extension blocks while `isextended` | At walk time, block by block |
| PAX 0.0 | Repeated `GNU.sparse.offset` / `GNU.sparse.numbytes` records | At walk time, with the records |
| PAX 0.1 | `GNU.sparse.map` (comma list) | At walk time, with the records |
| PAX 1.0 | Decimal lines at the start of the data area, padded to a block | At walk time, from the first blocks of the data area (§"Sparse maps are read during the walk") |

Every parser takes the entry budget and refuses before it allocates: the 1.0 count line,
the 0.1 comma count, the 0.0 record count and each old-GNU extension block are weighed
first, as PR 704 weighs them. A 1.0 number longer than 20 digits is `CorruptionError`,
as GNU tar refuses it. That bound is structural (without it a number with no newline
grows one buffer for as long as the archive lasts), so its reason sits at the constant
and the spec delta lists it.

`validate_sparse_map(map, realsize, stored_size) -> SparseError | None` checks what PR
716 checks today, now against exact sizes: entries non-negative, in order and not
overlapping (an out-of-order or overlapping map stays `UnsupportedFeatureError`: such
maps are crafted only, and tarfile's reading of them is wrong bytes), every chunk inside
`realsize`, and the chunks summing to exactly the stored size. The last check is what closes the 511-byte padding gap: tarfile overwrites
`size` and leaves only the block-rounded end, so PR 716 can refuse only a shortfall of
512 bytes or more. A shortfall of 1 to 511 bytes is now refused too (DR-3: bytes inside
a member that nothing names).

Every producer checked writes the exact sum: GNU tar 1.26, 1.29, 1.30 and 1.35 in all
five forms (`--format=gnu`, `oldgnu`, and `posix` with `--sparse-version` 0.0, 0.1 and
1.0), and bsdtar 3.7.2 in its only sparse form (pax 1.0; its `gnutar` format writes no
sparse members). Each wrote files whose last chunk is 1, 511, 512, 513 and 997 bytes;
the size field minus the 1.0 map's blocks equalled the chunk sum every time. `star`
was not checked (no package reachable from the build container). GNU tar itself reads
an archive with such slack silently, so DR-3 and DR-6 point different ways here; the
maintainer chose the refusal (2026-10-10, project thread), because no known writer
leaves slack.

### Final member decisions

`TarWalker._resolve` applies, in this order, what the chain
collected: GNU `L`/`K`, then PAX `path`/`linkpath`/`size`/`uid`/`gid`/`uname`/`gname`/
`mtime`, then the sparse keywords (`GNU.sparse.name`, `GNU.sparse.realsize`,
`GNU.sparse.size`). Then the type rules that depend on the final name: an `AREGTYPE`
(NUL) entry whose final name ends in `/` is a directory and its declared data is
skipped (PR 706's rule, as GNU tar 1.35 reads it), and whatever `main` decides for a
`REGTYPE` entry named `d/` by the time this lands (the directory-data change) moves here
unchanged. A link name is kept only on a link type; a long link name on any other
member is dropped before it is stored (today's `_drop_unweighed_link_name`, now
structural).

`TarEntry` is what `ArchiveMember._raw` holds: the decoded `HeaderBlock` fields, the
header offset, `data_offset`, `stored_size` (exact, and for a sparse member the chunks
alone: a 1.0 map's blocks and old GNU extension blocks come before `data_offset`),
`size` (logical), the raw name and
link bytes with their source (`ustar`, `gnu_long`, `pax`) and that block's
`hdrcharset`, the member's own PAX records, a reference to the global records in force,
and the sparse map or the 1.0 marker.

## The walker (`TarWalker` in `tar_parser.py`)

One loop, no recursion. `next_entry(budget) -> TarEntry | TarEnd`.

1. Skip the rest of the previous member's data area: `data_offset + stored_size` rounded
   up to a block. On a seekable plain tar this is one forward seek. Over a
   decompressor, or in streaming, it is a read-and-discard in 64 KiB steps that raises
   `TruncatedError` at the first short read (today's `_read_through_member_data`, now
   the only skip there is). An offset past 2**63 - 1 is `CorruptionError` before any
   seek (today's `_BoundedTarFileobj.seek` check).
2. Read one block and parse it. A zero block, a rejected block or a short read ends the
   walk with a `TarEnd` that says which, at which offset. The reader turns that into the
   existing EOF classification (§"EOF and trailing data"). After an `x`, `X`, `L` or
   `K` header the chain must end in a member header. A rejected block there is a
   rejected `TarEnd`, and the stream ending there is `TruncatedError`. A zero block
   there ends the walk as a zero block, as GNU tar 1.35 lists such an archive (DR-6;
   tarfile raises): the `TarEnd` reason names the unused header. A `g` header describes
   no member, so it starts no chain. A bad number in an old GNU extension block is a rejected `TarEnd`
   too, as tarfile rejects the header for it.
3. An extended header: read its data with `read_within_reach` in steps (never one read
   sized from the field), after charging the declared size to the member's budget. A
   chain draws on one budget, so four 300 KB PAX headers under a 1 MiB cap raise
   `ResourceLimitError` as PR 704 makes them raise. A `g` header replaces the walker's
   global records with a new immutable snapshot; members built after it share that
   snapshot (PR 704's sharing, without the `_proc_builtin` override). Loop to 2.
4. A member header: resolve it, return the `TarEntry`.

Which types have a data area follows `tarfile`, so no listing changes: links, devices,
FIFOs and directories (`1` to `6`) have none, and data declared on them is read as the
next header. GNU tar does the same for a directory and fails with "Skipping to next
header" on the others, which is the `CorruptionError` archivey gives today. So a
`DIRTYPE` header that declares a size keeps PR 706's answer: its data is read as the next
header, which does not parse, and the listing raises `CorruptionError`. Every other
typeflag, known or not, has its data skipped by size.

On a seekable stream the walker checks that the last byte of the previous member's data
area exists before reading the next header, as `tarfile` does, so a member cut short
reads as `TruncatedError` and not as a clean end. A forward-only walk gets the same
answer from its read-through.

The walker reads through a buffer of its own, so that a block is one `read(512)` against
memory, not a syscall or a decompressor call. Which stream it buffers depends on who
else reads the handle:

- **Random access, plain or compressed:** the walker gets `BufferedReader` over its own
  `SharedView` of the reader's byte stream (the source, or the codec stream), as
  `shared.py` says a consumer of many small reads should. Member streams are other
  `SharedView`s over the same stream. Every view re-seeks the shared handle to its own
  position under the handle lock before each read, so a member read moves the raw
  handle but never the walker's view, and the walker's buffer stays the bytes at the
  offsets it read. A reader with no handle lock gets a null context: one thread, the
  same re-seek per read, so interleaving members with a progressive listing stays
  correct.
- **Streaming:** the walker owns the forward stream (wrapped by `ensure_bufferedio`) and
  is its only reader. Member data is read through the walker (`open_data`), which counts
  every byte, so there is one position and nothing to invalidate.

Header text is weighed exactly (bytes read for extended headers) instead of the low
estimate `_header_text_bytes` makes today.

Because one header costs one `next_entry()` call with no tarfile list behind it, the
random-access batching (`_header_batch_size`, `_HEADER_BATCH`) exists only if the
listing benchmark says the per-header lock is still too slow. The first implementation
pulls one header per lock hold and measures it against the baseline benchmark; batching
comes back only with a number.

## How it plugs into the reader

`TarReader` keeps its `BaseArchiveReader` hooks and their contracts. What changes is
what is under them:

| Hook | Today | Native |
| --- | --- | --- |
| `__init__` | `tarfile.open(fileobj=...)` parses the first member | Build the byte stream as today (plain source, or archivey's codec stream), then `next_entry()` for the first member, so a non-tar still fails at open (DR-15b) |
| `_iter_members` / `_iter_members_progressive` | `iter(TarFile)` in batches, under `_HeaderBudget` | `while isinstance(e := walker.next_entry(budget), TarEntry): yield self._to_member(e, i)` |
| `_to_member` | Maps `TarInfo`, then re-derives what tarfile lost | Maps `TarEntry`; no inference |
| `_open_member` (random access) | `TarFile.extractfile` behind `LockedStream` | `SharedView(stream, data_offset, stored_size, lock=...)` with the handle lock (a null context when the reader has none), inside `SparseStream` when sparse |
| `_iter_with_data` (streaming) | `extractfile` on the `r\|` stream; the next header invalidates it | A `SlicingStream` at the walker's position; the walker's next skip reads whatever the consumer left. Same "good until the pass moves on" contract |
| `_verify_tar_eof` | Re-reads the block after tarfile's stop, classifies with the swallowed error | The walker's `TarEnd` already says which; only the second-block read and the trailing scan stay |
| `_close_archive` | `TarFile.close()` then the owned streams | The owned streams |

The codec layer, `_track_source_seeks`, the cost receipt, the handle lock and its
critical-section shape (`reader-concurrency`), `_drive_pass_streams`, the one-pass
`stream_members()` on a random-access reader, the hardlink rules and every diagnostic
code stay as they are.

### Member streams

- **Plain tar, random access:** a `SharedView` over the source. Seeking is the view's
  own, so a seek past the end returns the target and the next read returns `b""`, as
  every other format's member stream does. This removes the known issue where tarfile's
  `ExFileObject` clamps `seek(10)` on a 3-byte member to 3.
- **Compressed tar, random access:** a `SharedView` over the decompressor stream, which
  already seeks (resume points, `STREAM_REWIND_REDECOMPRESSES`). Unchanged cost.
- **Streaming:** a single-consumer `SlicingStream` over the forward stream.
- **Sparse:** `SparseStream(stored, map, size)` over any of the three. A read in a hole
  returns zeros without touching `stored`; a read in a chunk reads `stored` at the
  chunk's stored offset. It is seekable when `stored` is; over a forward-only stream a
  backward seek is `io.UnsupportedOperation`, as for any forward-only member. Holes
  still count as output for the ratio guard (maintainer ruling, 2026-09-25).

### Sparse maps are read during the walk

Every sparse map is parsed during the walk, as `tarfile` parses it today, and checked
against the member's sizes (`validate_sparse_map`) when the member is opened, as PR 716
checks it today. For PAX 1.0 the map is the first blocks of the member's data area: the
walker reads them, as it reads old GNU extension blocks, and the member's
`data_offset` and `stored_size` then cover the chunks alone. On a seekable plain tar
that is one extra read of the map's blocks per 1.0 member, a rare encoding. Reading it at open was
the alternative; it would move two things: a 1.0 map that does not parse would list
cleanly and fail only when opened (today it fails the listing, through
`TarInfo._proc_gnusparse_10`), and its entries would leave the listing budget that PR
704's tests pin.

## Names and encodings

`TarEntry` keeps each name's bytes and where they came from, so `_recover_raw_name`,
`_pax_field_is_utf8` and the string comparisons in `_utf8_first` go:

- `raw_name` is the stored bytes from the block that supplied the name, always.
- A PAX value is UTF-8 unless its own block says `hdrcharset=BINARY`; a global `BINARY`
  applies to the members after it, and a member block that repeats it is no longer
  confused with one that inherits it.
- A ustar or GNU field is UTF-8 when its bytes are valid UTF-8, else the caller's
  `encoding=` (default UTF-8 with `surrogateescape`), the rule every format follows
  (ruled 2026-10-07), with `MEMBER_NAME_ENCODING_INFERRED` where it applies today.

## Errors

The parser and walker raise archivey types directly, with the member offset in the
message: `CorruptionError` (a PAX sparse map that does not parse, a sparse version
with no map, a PAX `size` that is not a number), `TruncatedError` (short read inside a header or data area),
`ResourceLimitError` (budget), `UnsupportedFeatureError` (out-of-order or overlapping
sparse map). Damage that tarfile reports as an invalid header (a block that is no
header, PAX records that do not parse, an extended header followed by a block that is no
header, an old GNU sparse map number that does not parse) is a rejected `TarEnd`, not an exception, so the reader's EOF policy decides it as
today. That deletes `_translate_exception`, `_translate_open_error`,
`_raised_by_tarfile` and `_passes_through_tarfile`, and with them the traceback-frame
inspection and the matching of tarfile's message text. An `OSError` from the source
passes through (DR-15a). A non-tar at offset 0 keeps raising what it raises today, so
detection and `open_archive` error types do not move.

## EOF and trailing data

Unchanged policy (handbook §2.2), simpler input. The walker's `TarEnd` gives the reason
directly: `zero_block`, `rejected` (with the reason) or `short_read`, at an exact
offset, in both access modes, with no extra read. `_EofProbeStream`, `_TarFile.stopped_on`
and the `fromtarfile` override go. The second-block read, the "zero block then non-null
block" warning, the 1 MiB trailing scan and the stream-checksum reporting are format
policy and stay as they are.

## Which workarounds this deletes

Numbers are the rows of the workaround inventory.

| # | Workaround | Native reader |
| --- | --- | --- |
| 1 | `_proc_member` override, `_HEADER_BUDGET` ContextVar | Deleted: the budget is an argument |
| 2 | `_BoundedTarFileobj` stepped reads | Deleted: the walker reads extended headers with `read_within_reach` |
| 3 | Refusing an impossible seek | Kept as one offset check in the walker |
| 4 | `_EofProbeStream`, `stopped_on`, `fromtarfile` error capture | Deleted: `TarEnd` says why the walk stopped |
| 5 | Re-reading after tarfile's stop | Reduced to the second-block read (policy) |
| 6 | `stored_end`, `_sparse_map_error` | Replaced by exact validation in `validate_sparse_map`; the 511-byte gap closes |
| 7 | Weighing the sparse map after parsing | Deleted: weighed before allocation |
| 8 | Header batching, `_header_text_bytes` estimates | Deleted unless the benchmark needs batching back; exact weights |
| 9 | `_read_through_member_data` | Becomes the walker's normal skip |
| 10 | Frame inspection, message matching | Deleted: typed errors at the source |
| 11 | `RecursionError` from header chains | Deleted: the loop is iterative |
| 12 | `_drop_unweighed_link_name` | Deleted: structural in the walker's member resolution |
| 13 | Passing `encoding="utf-8"` to tarfile | Becomes the reader's decoding default |
| 14 | `_recover_raw_name`, `_pax_field_is_utf8`, name inference | Deleted: names keep their bytes and source |
| 15 | Re-parsing a PAX `mtime` tarfile turned into 0 | Deleted: PAX times are parsed once, here |
| 16 | Masking a base-256 mode | Kept (one line) |
| 17 | `ensure_bufferedio` for short reads | Kept, for speed, not as a workaround |
| 18 | `is_seekable` catching tarfile's `AttributeError` | Deleted from `binaryio.py` once nothing passes an `ExFileObject` |
| 19 | Member `seek` clamped to the size | Fixed: `SlicingStream` seeks |
| 20 | `TarFile.members` duplicate list | Fixed: the walker keeps nothing behind the listing |
| PR 704 | Copied `_proc_sparse` / `_proc_gnusparse_10`, SHA-256 pins of stdlib source, `_GlobalPaxRecords` | Deleted; the read-only `extra["tar.pax_headers"]` value stays |
| PR 706 | `frombuf` and `_frombuf(dircheck=)` overrides, `header_depth` | Deleted: the rule is one line in the walker's member resolution |
| PR 716 | Map ordering and overlap checks | Moved into `validate_sparse_map` |

## Behaviour that changes

All of these are fixes; none changes a public name or signature.

| Change | Rule |
| --- | --- |
| A member `seek` past the end returns the target; the next read returns `b""` | DR-5: same as every other format. Removes a `known-issues.md` entry |
| A sparse member never serves its own padding as data, and a map that leaves 1 to 511 stored bytes unnamed is refused | DR-1, DR-3; maintainer decision 2026-10-10 (§"GNU sparse") |
| `raw_name` is the stored bytes in every case, including a block that repeats a global `hdrcharset=BINARY` and a PAX `path` that is not UTF-8 read with `encoding=` (today the UTF-8 re-encoding of `tarfile`'s fallback decode) | DR-1; removes a test-pinned exception |
| A member that inherits a global `hdrcharset=BINARY` is decoded as one that declares it: its PAX values follow the ustar rule. `name` is unchanged in every case checked on `main` (UTF-8 and Latin-1 bytes, with and without `encoding="latin-1"`), and a valid-UTF-8 value under `encoding=` now emits `MEMBER_NAME_ENCODING_INFERRED`, as it does under the member's own `BINARY` | POSIX scopes `hdrcharset` this way; DR-7 and the valid-UTF-8-wins ruling (2026-10-07) for a field that declares no encoding |
| A v7 or old GNU header with bytes at 345 to 500 no longer joins them to the name as a `prefix`: a GNU incremental archive (`tar -G`) lists `d/f.txt`, not `15262452373/d/f.txt` | DR-6: GNU tar reads `prefix` only in ustar headers |
| The stream ending right after an extended header (PAX `x` or `g`, GNU `L` or `K`) raises `TruncatedError`; today it is `CorruptionError` ("empty header", from tarfile parsing the next header inside the extended one) | DR-5: the same error every other mid-walk stream end gives |
| An `x` or `L` header right before the end-of-archive marker ends the listing cleanly after the members before it, with no diagnostic; today it is `CorruptionError` | DR-6: GNU tar 1.35 lists these archives with exit 0. A new diagnostic code would be a public change, so none is added |
| The member list is the same on every Python 3.11 to 3.15 patch release | DR-5 |
| A streaming pass holds one list of members, not two | DR-9a; removes a sharp-edges row and threat-model O1's TAR note |
| Error messages name offsets and fields, not tarfile's wording | Message text is not contract |

Two handbook rows are wrong today and get corrected with the docs: a contiguous file
(`7`) lists as `FILE`, not `OTHER` (tarfile and GNU tar both read it as a regular file;
checked on `main`), and §6's "Keep `extractfile()`" row goes.

## Testing

- **Acceptance:** the whole current suite, including the tests from PRs 704, 706 and
  716, passes unchanged except where §"Behaviour that changes" says otherwise. Each such
  test edit is in the PR that changes the behaviour, with the reason.
- **Parser unit tests:** one table per encoding (v7, ustar, old GNU, PAX, base-256,
  each sparse encoding, each `Rejected` reason), from fixtures written by GNU tar 1.35
  and bsdtar where a tool can write the shape (DR-24) and by hand where only a crafted
  archive has it.
- **Differential:** every TAR fixture in the corpus, listed by the native reader and by
  tarfile (as an oracle, in the test only), field by field, with the known differences
  above named in their own tests (the incremental-archive prefix is one). Sparse
  members compared byte for byte against `tarfile.extractfile`, over fixtures GNU tar
  writes with `tar -cS --format=gnu|oldgnu` and `tar -cS --format=posix
  --sparse-version=0.0|0.1|1.0`, the producers DR-24 asks for.
- **Fuzzing:** `tests/fuzz_tar_parser.py` beside the RAR and 7z parser fuzzers, and the
  existing atheris TAR target pointed at the new reader.
- **Memory:** the PR 704 memory tests (`tests/test_tar_header_memory.py`) are the
  acceptance bar for DR-9a, unchanged: every sparse map, 1.0 included, is still charged
  to the listing budget. Plus a streaming-retention test that fails if a second member
  list comes back.
- **Speed:** the listing and extraction benchmarks against the PR 655 baseline. The
  native reader must not be slower on the ordinary 100 000-member listing.

## PR plan

One stage per PR, each with the `review` label workflow:

1. **Design** (this change). Docs only.
2. **Parser.** `tar_parser.py`, its unit tests, the differential test against tarfile
   in listing mode, the fuzz target. Not wired into the reader.
3. **Sparse stream.** `streamtools/sparse.py` and its tests, byte-compared against
   tarfile's sparse expansion. Not wired in.
4. **Switch.** `tar_reader.py` on the walker; all shims deleted; the suite green;
   benchmarks compared, and the stage-4 numbers replace the TAR half of the
   stdlib-ratio row in `docs/access-and-cost.md`. This PR is large because the switch
   cannot be half done: a reader that lists natively and reads through `extractfile`
   would keep every private hook. Most of its diff is the workaround layer going.
5. **Docs.** Handbook `formats/tar.md` (§2.2, §2.3, §5, §6, §7), `known-issues.md`,
   threat-model O1 / O15 / O16 TAR notes, `docs/formats.md`, `docs/access-and-cost.md`
   (the member-seek claim and the "wraps stdlib" row), the `format-tar` spec purpose,
   `backends/__init__.py` docstring, `code-map.md`. Then archive this change, after a
   dry-run archive on a scratch tree whose `openspec/specs/` diff shows every delta
   landed on the requirement it names.

Stages 2 and 3 can be reviewed in parallel. Stage 4 starts after PRs 704, 706 and 716
have merged, so their tests are on `main`.

## Open questions

None that block. Two are settled by rules, named here so the maintainer can object:

- **Unknown typeflags stay `OTHER` with their data skipped.** GNU tar extracts an
  unknown typeflag as a regular file with a warning. Keeping today's listing is DR-5
  (no listing change in a rewrite) and the device ruling's "useless as files"; changing
  it is a separate question with its own fixtures.
- **A rejected mid-archive header stays `CorruptionError` after the members before it.**
  GNU tar resyncs and keeps listing. That is salvage, which `IDEAS.md` plans after
  0.2.0, and DR-21 says to leave it to that change rather than widen this one.
