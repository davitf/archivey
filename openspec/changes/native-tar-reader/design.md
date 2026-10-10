# Design — native TAR reader

Evidence for the change: the tarfile workaround inventory (20 places, about 650 of the
backend's 1 810 lines on `main`, 7 of them on private tarfile API), summarised in
`proposal.md`. This page is how the replacement is built. Line counts are estimates at
this repo's docstring density.

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
| `internal/backends/tar_parser.py` (new) | Pure functions and frozen dataclasses. No I/O, no archivey reader types. Header block decode, number fields, checksum, PAX records, GNU sparse maps (all four encodings), the old-style directory rule | ~550 |
| `internal/streams/streamtools/sparse.py` (new) | `SparseStream`: a member's logical bytes over its stored bytes, holes as zeros | ~180 |
| `internal/backends/tar_reader.py` (rewritten) | `_TarWalker` (the I/O loop over one byte stream), `TarReader` (the `BaseArchiveReader` hooks), EOF and trailing-data policy, metadata mapping | ~1 100, from 1 810 |

The split follows `rar_parser.py` / `rar_reader.py` and `sevenzip_parser.py` /
`sevenzip_reader.py`: the parser module is what the fuzz target drives, and has no
reason to import the reader. `SparseStream` goes in `streamtools/` because it knows
nothing about TAR: it maps logical ranges to stored ranges over any byte stream.

## The parser (`tar_parser.py`)

### Header block

`parse_header_block(block: bytes, offset: int) -> HeaderBlock | ZeroBlock | Rejected`.
One 512-byte block in, one of three results out. No exception for an expected outcome:
the walker decides what a rejected block means at its position.

- **Zero block:** all 512 bytes are NUL.
- **Checksum:** the sum of the block with the checksum field read as eight spaces. Both
  the unsigned and the signed sum are accepted, as GNU tar and tarfile accept both. A
  mismatch is `Rejected(reason="checksum")`.
- **Magic:** `ustar\0` + `00` is POSIX ustar (the `prefix` field joins the name).
  `ustar  \0` is old GNU (no prefix; the slots at 345 to 494 are `atime`, `ctime`,
  `offset`, `longnames`, four sparse entries, `isextended` and `realsize`). Anything
  else is v7: name, mode, ids, size, mtime, typeflag and link name only.
- **Numbers:** octal, NUL- or space-terminated, leading spaces allowed, an all-NUL or
  all-space field is 0 (tarfile and GNU tar agree). GNU base-256: first byte `0x80` is
  positive, `0xFF` negative, two's complement over the rest of the field. A field that
  is neither is `Rejected(reason="number", field=...)`. A negative `size` is rejected.
- **Strings** stay `bytes`, cut at the first NUL. Decoding happens in the reader, once,
  with the name's source known (§"Names and encodings").

`HeaderBlock` is a frozen slotted dataclass of the decoded fields plus `format`
(`V7`, `USTAR`, `GNU`), `typeflag: bytes` and the GNU sparse slots when present.

### Extended headers

Typeflags `x` and `X` (Solaris) carry per-member PAX records, `g` global ones, `L` a GNU
long name and `K` a GNU long link name. The walker reads their data (§"The walker") and
hands it to:

- `parse_pax_records(data: bytes) -> list[tuple[bytes, bytes]]`. Records are
  `"<len> <key>=<value>\n"`. The length is decimal, covers the whole record including
  itself, and must land on the `\n`; anything else is `Rejected(reason="pax")`. Keys and
  values stay bytes. An empty value is kept: in a `g` header it deletes the key, in an
  `x` header it overrides a global with nothing (POSIX). Duplicate keys: the last one
  wins, except the `GNU.sparse.offset` / `GNU.sparse.numbytes` pairs of sparse 0.0,
  which are kept in order.
- `parse_gnu_long(data: bytes) -> bytes`: the name up to the first NUL.

### GNU sparse

`SparseMap` is a pair of `array("q")` (offsets and lengths), not a list of tuples: 16
bytes per entry instead of about 150. The budget keeps charging today's 24 bytes per
entry, so the caps trip where PR 704's tests expect. Parsers:

| Encoding | Where the map is | Parsed |
| --- | --- | --- |
| Old GNU, typeflag `S` | 4 slots in the header, then 21-slot extension blocks while `isextended` | At walk time, block by block |
| PAX 0.0 | Repeated `GNU.sparse.offset` / `GNU.sparse.numbytes` records | At walk time, with the records |
| PAX 0.1 | `GNU.sparse.map` (comma list) | At walk time, with the records |
| PAX 1.0 | Decimal lines at the start of the data area, padded to a block | At open time (§"Sparse 1.0 map is read at open") |

Every parser takes the entry budget and refuses before it allocates: the 1.0 count line,
the 0.1 comma count, the 0.0 record count and each old-GNU extension block are weighed
first, as PR 704 weighs them. A 1.0 number longer than 20 digits is `Rejected`, as GNU
tar refuses it.

`validate_sparse_map(map, realsize, stored_size) -> SparseError | None` checks what PR
716 checks today, now against exact sizes: entries non-negative, in order and not
overlapping (an out-of-order or overlapping map stays `UnsupportedFeatureError`: such
maps are crafted only, and tarfile's reading of them is wrong bytes), every chunk inside
`realsize`, and the chunks summing to exactly the stored size (minus the 1.0 map's own
blocks). The last check is what closes the 511-byte padding gap: tarfile overwrites
`size` and leaves only the block-rounded end.

### Final member decisions

`resolve_member(block, pending) -> TarEntry` applies, in this order, what the chain
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
header offset, `data_offset`, `stored_size` (exact), `size` (logical), the raw name and
link bytes with their source (`ustar`, `gnu_long`, `pax`) and that block's
`hdrcharset`, the member's own PAX records, a reference to the global records in force,
and the sparse map or the 1.0 marker.

## The walker (`_TarWalker` in `tar_reader.py`)

One loop, no recursion. `next_entry(budget) -> TarEntry | TarEnd`.

1. Skip the rest of the previous member's data area: `data_offset + stored_size` rounded
   up to a block. On a seekable plain tar this is one forward seek. Over a
   decompressor, or in streaming, it is a read-and-discard in 64 KiB steps that raises
   `TruncatedError` at the first short read (today's `_read_through_member_data`, now
   the only skip there is). An offset past 2**63 - 1 is `CorruptionError` before any
   seek (today's `_BoundedTarFileobj.seek` check).
2. Read one block and parse it. A zero block, a rejected block or a short read ends the
   walk with a `TarEnd` that says which, at which offset. The reader turns that into the
   existing EOF classification (§"EOF and trailing data").
3. An extended header: read its data with `read_within_reach` in steps (never one read
   sized from the field), after charging the declared size to the member's budget. A
   chain draws on one budget, so four 300 KB PAX headers under a 1 MiB cap raise
   `ResourceLimitError` as PR 704 makes them raise. A `g` header replaces the walker's
   global records with a new immutable snapshot; members built after it share that
   snapshot (PR 704's sharing, without the `_proc_builtin` override). Loop to 2.
4. A member header: resolve it, return the `TarEntry`.

The walker reads the header stream through `ensure_bufferedio` so that a block is one
`read(512)` against a buffer, not a syscall or a decompressor call. Header text is
weighed exactly (bytes read for extended headers, plus the retained name, link and
records) instead of the low estimate `_header_text_bytes` makes today.

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

### Sparse 1.0 map is read at open

The 1.0 map is the first blocks of the member's data area, so reading it during a
listing would read data the caller may never ask for, and on a seekable plain tar turn
a seek-only walk into a read of every sparse member. The walker records the member as
sparse 1.0 with its `realsize`; the map is parsed and validated when the member is
opened, against the same per-member budget, and a bad map raises at open (streaming: on
the first read), which is when a bad map of any encoding raises today. `is_sparse` and
`size` come from the PAX records, so the listing is unchanged.

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
message: `CorruptionError` (rejected header, bad PAX record, bad sparse map),
`TruncatedError` (short read inside a header or data area), `ResourceLimitError`
(budget), `UnsupportedFeatureError` (out-of-order or overlapping sparse map). That
deletes `_translate_exception`, `_translate_open_error`, `_raised_by_tarfile` and
`_passes_through_tarfile`, and with them the traceback-frame inspection and the matching
of tarfile's message text. An `OSError` from the source passes through (DR-15a). A
non-tar at offset 0 keeps raising what it raises today, so detection and `open_archive`
error types do not move.

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
| 12 | `_drop_unweighed_link_name` | Deleted: structural in `resolve_member` |
| 13 | Passing `encoding="utf-8"` to tarfile | Becomes the reader's decoding default |
| 14 | `_recover_raw_name`, `_pax_field_is_utf8`, name inference | Deleted: names keep their bytes and source |
| 15 | Re-parsing a PAX `mtime` tarfile turned into 0 | Deleted: PAX times are parsed once, here |
| 16 | Masking a base-256 mode | Kept (one line) |
| 17 | `ensure_bufferedio` for short reads | Kept, for speed, not as a workaround |
| 18 | `is_seekable` catching tarfile's `AttributeError` | Deleted from `binaryio.py` once nothing passes an `ExFileObject` |
| 19 | Member `seek` clamped to the size | Fixed: `SlicingStream` seeks |
| 20 | `TarFile.members` duplicate list | Fixed: the walker keeps nothing behind the listing |
| PR 704 | Copied `_proc_sparse` / `_proc_gnusparse_10`, SHA-256 pins of stdlib source, `_GlobalPaxRecords` | Deleted; the read-only `extra["tar.pax_headers"]` value stays |
| PR 706 | `frombuf` and `_frombuf(dircheck=)` overrides, `header_depth` | Deleted: the rule is one branch in `resolve_member` |
| PR 716 | Map ordering and overlap checks | Moved into `validate_sparse_map` |

## Behaviour that changes

All of these are fixes; none changes a public name or signature.

| Change | Rule |
| --- | --- |
| A member `seek` past the end returns the target; the next read returns `b""` | DR-5: same as every other format. Removes a `known-issues.md` entry |
| A sparse member never serves its own padding as data | DR-1 |
| `raw_name` is the stored bytes in every case, including a block that repeats a global `hdrcharset=BINARY` | DR-1; removes a test-pinned exception |
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
  above whitelisted by name. Sparse members compared byte for byte against
  `tarfile.extractfile` and against `tar -xf --sparse`.
- **Fuzzing:** `tests/fuzz_tar_parser.py` beside the RAR and 7z parser fuzzers, and the
  existing atheris TAR target pointed at the new reader.
- **Memory:** the PR 704 memory tests (`tests/test_tar_header_memory.py`) are the
  acceptance bar for DR-9a, plus a streaming-retention test that fails if a second
  member list comes back.
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
   benchmarks compared. This PR is large because the switch cannot be half done: a
   reader that lists natively and reads through `extractfile` would keep every private
   hook. Its diff is mostly deletions.
5. **Docs.** Handbook `formats/tar.md` (§2.2, §2.3, §5, §6, §7), `known-issues.md`,
   threat-model O1 / O15 / O16 TAR notes, `docs/formats.md`, the `format-tar` spec
   purpose, `backends/__init__.py` docstring, `code-map.md`. Then archive this change.

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
