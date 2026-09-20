# Inventory — one row per site

Baseline `main` @ `94468bd0`. Probe: remove the hatch, run both checkers, restore.

**Line numbers are anchors at `94468bd0`, not at this branch's head.** The branch
later merged `main` in, and the 7z, RAR and ZIP commits that came with it moved a
lot of them: R2–R4 `rar_reader.py` 1063/1210/1294 → 1071/1218/1302, R5
`sevenzip_pipeline.py:552` → `:526`, R7–R10 `sevenzip_reader.py` 391/423/694/762 →
398/430/701/769, R12–R13 `zip_reader.py` 1425/1464 → 1421/1454. **A staged PR should
locate each site by symbol, not by line.** The hatch population itself is unchanged:
per-file `Any` and `cast` counts are identical between `94468bd0` and `main`, so no
disposition moves. The one exception is R6, below.
`Any` sites were probed by substituting `object`. `TypeGuard[T]` by substituting
`bool`. Casts by using the value. Asserts by replacing the statement with `None`.

**Checkers** = pyrefly 1.1.1 / ty (dev extra pins). `both-clean` means both exit 0.
Dispositions: DELETE / FIX-IN-CODE / TIGHTEN / KEEP-WITH-REASON.

**Sequence note, and it runs one way only.** Several `cast(member._raw)` sites are
`both-clean` only while `_raw: Any`. **C8 (`tar_reader.py:470`) has no assert to fall
back on** — the `assert isinstance(info, tarfile.TarInfo)` that R11 records is at
`tar_reader.py:763`, in `_open_member`, a different function with its own `info`.
Line 470 sits in the `_open` closure and narrows nothing. So once PR 1 deletes that
cast, tightening `_raw` to `object` (A25) makes `extractfile(info)` a fresh
both-checker error, and A25 needs a **new** assert at 470 rather than a kept one.

**A25 is therefore not part of staged PR 2.** It is its own change, after PR 1, and
it carries the new narrowing at `tar_reader.py:470` with it. Do not DELETE the
thirteen `assert isinstance` statements in either PR: once `_raw` is `object` they
are the narrowing.

## Suppressions (3)

| ID | Site | Without it | Disposition | Reason |
|---|---|---|---|---|
| IGN1 | `streamtools/base.py:241` `DelegatingStream.name` | pyrefly `bad-override`; ty clean | KEEP-WITH-REASON | S5b. Fails closed (bogus code restores the error). ty silent → no `# ty: ignore` |
| IGN2 | `streams/peekable.py:104` `PeekableStream.name` | same | KEEP-WITH-REASON | S5b |
| IGN3 | `streamtools/full_count.py:103` `FullCountStream.name` | same | KEEP-WITH-REASON | Same widening. Landed after the brief. Name it in the `ReadOnlyIOStream.name` docstring next to `PeekableStream` |

No `# type: ignore` and no `# ty: ignore` in `src/`.

## TypeGuard (3)

Honesty analysis: [`typeguards.md`](typeguards.md).

| ID | Site | Guard target | Without `TypeGuard` (→ `bool`) | Honest? | Disposition |
|---|---|---|---|---|---|
| G1 | `binaryio.py:276` `is_filename` | `str \| bytes \| os.PathLike` | callers lose narrowing (`fsdecode` etc.) | **yes** — `isinstance` matches the target | KEEP. Param `Any` → `object` (A-bin-filename) |
| G2 | `binaryio.py:452` `is_stream` | `BinaryIO` | callers lose narrowing (`PeekableStream` etc.) | **no** — True for write-only `IOBase`; True for duck objects whose `read()` returns `str`. #324 closed `TextIOBase` only | FIX-IN-CODE the predicate (staged PR 4) |
| G3 | `volumes.py:470` `_is_source_sequence` | `SourceSequence` (`Sequence[SourceItem]`) | `_coerce_path_or_stream` sees the un-narrowed union | **partial** — excludes `str`/`Path`/`bytes`/`is_stream`, then `isinstance(Sequence)`. `bytearray` is True | TIGHTEN (staged PR 4) |

## `typing.cast` (22)

| ID | Site | Target | Without it | Disposition | Reason |
|---|---|---|---|---|---|
| C1 | `cli/progress.py:112` | `Callable[..., _TqdmBar]` | both-clean | DELETE | `tqdm` is untyped (`untyped-import` warning); the cast is decorative |
| C2 | `iso_reader.py:246` | `BinaryIO` | both-error: `PyCdlibIO` ↛ `DelegatingStream` inner | KEEP-WITH-REASON | pycdlib handle is not `BinaryIO` in typeshed; runtime it is a binary stream |
| C3 | `rar_unrar.py:518` | `BinaryIO` | both-error: `Popen[bytes].stdout` is `IO[Any]` | KEEP-WITH-REASON | typeshed `Popen.stdout` vs `BinaryIO` |
| C4 | `tar_reader.py:333` | `BinaryIO` | both-error: `BufferedIOBase` ↛ `_owned_stream: BinaryIO` | KEEP / same as C22 | `ensure_bufferedio` returns `BufferedIOBase`; typeshed split. Comment already exists at C22 |
| C5 | `tar_reader.py:347` | `BinaryIO` | both-error: `Path \| BinaryIO` ↛ `BinaryIO` | FIX-IN-CODE | `@overload` on `_track_source_seeks` |
| C6 | `tar_reader.py:356` | `BinaryIO` | same | FIX-IN-CODE | same overload |
| C7 | `tar_reader.py:370` | `BinaryIO` | both-error: `_EofProbeStream` ↛ `BinaryIO` | FIX-IN-CODE | make the probe stream a `BinaryIO` subclass (it likely already is at runtime) |
| C8 | `tar_reader.py:470` | `tarfile.TarInfo` | both-clean | DELETE *while `_raw` is `Any`* | redundant with A-raw. Keep the assert at R-tar if `_raw` tightens |
| C9 | `tar_reader.py:772` | `BinaryIO \| None` | both-clean | DELETE | next line is `ensure_binaryio` (`Any` param). Comment about `extractfile` → `IO[bytes]` is stale for this assignment |
| C10 | `zip_reader.py:480` | `BinaryIO` | both-error: `Path \| BinaryIO` | FIX-IN-CODE | `_track_source_seeks` overload |
| C11 | `zip_reader.py:482` | `BinaryIO` | same | FIX-IN-CODE | same |
| C12 | `zip_reader.py:840` | `BinaryIO` | both-error: `IO[bytes]` ↛ `SlicingStream` | KEEP-WITH-REASON | `ZipFile.fp` is `IO[bytes]` in typeshed |
| C13 | `zip_reader.py:1045` | `BinaryIO` | same | KEEP-WITH-REASON | same |
| C14 | `zip_reader.py:1209` | `BinaryIO` | both-error: `IO[bytes]` ↛ declared return | KEEP-WITH-REASON | `ZipFile.open` return |
| C15 | `detection_workspace.py:292` | `BinaryIO` | both-error: `SpooledTemporaryFile[bytes]` | KEEP-WITH-REASON | typeshed spool vs `BinaryIO` |
| C16 | `password.py:85` | `PasswordProvider` | **pyrefly-only** (ty clean) | FIX-IN-CODE | after str/bytes/sequence branches the remainder is the provider; pyrefly still sees `str` in the union. `assert callable` or a `TypeIs` |
| C17 | `selection.py:18` | `Callable[[ArchiveMember], bool]` | **ty-only** (`Collection ∩ Callable`); pyrefly warns `redundant-cast` | KEEP-WITH-REASON | ty cannot exclude a callable collection. Do not DELETE off the pyrefly warning |
| C18 | `selection.py:19` | `Collection[str \| ArchiveMember]` | both-clean | DELETE | after the callable return, the remainder is the collection |
| C19 | `archive_stream.py:192` | `sys.UnraisableHookArgs` | **pyrefly-only**: `SimpleNamespace` ↛ hook args | KEEP-WITH-REASON | runtime shape matches; typeshed wants the typed hook-args object. Comment already there |
| C20 | `decompressor_stream.py:282` | `BinaryIO` | both-error: `BufferedIOBase` ↛ `_inner: BinaryIO` | KEEP-WITH-REASON | same split as C4/C22 |
| C21 | `full_count.py:168` | `BinaryIO` | both-clean | DELETE | `FullCountStream` / buffer types already satisfy `BinaryIO` here |
| C22 | `full_count.py:172` | `BinaryIO` | both-error: `BufferedIOBase` ↛ `BinaryIO` | KEEP-WITH-REASON | comment on the line already names the typeshed split |

## `Any` (48)

Counted per annotation (a line with three `Any` params is three rows). Two
`TypeAlias` rows (`verify.py:68-69`) were missed by a name-startswith-uppercase
heuristic in the first census pass and are included here.

### TIGHTEN to `object` (both-clean) — staged PR 2

| ID | Site | Where | Notes |
|---|---|---|---|
| A1 | `iso_reader.py:119` | `__getattr__` → | module proxy; `object` is honest |
| A2 | `iso_reader.py:422` | `record` | unused-as-typed; `rr` on the same line is A-iso |
| A3 | `iso_reader.py:158` | `**kwargs` | signature should match `deque` instead (see A-iso-init) |
| A4 | `iso_reader.py:169` | `dir_record` | runtime `isinstance` to `DirectoryRecord` |
| A5 | `listing_limits.py:24` | `extra: dict[str, Any]` | **done** — `dict[str, object]` (companion of A26; A5 was already a TIGHTEN on its own merits — not because `dict` is invariant; see QUESTIONS.md) |
| A6 | `streamtools/base.py:76` | `write(b)` | typeshed `IO.write` takes `Any`; `object` works |
| A7 | `binaryio.py:53` | `try_readinto(stream)` | getattr-only |
| A8 | `binaryio.py:180` | `_is_fifo_or_chardev(stream)` | getattr-only |
| A9 | `binaryio.py:201` | `is_seekable(stream)` | getattr-only |
| A10 | `binaryio.py:276` | `is_filename(obj)` | TypeGuard input; `object` is the usual form |
| A11 | `binaryio.py:281` | `source_name(source)` | |
| A12 | `binaryio.py:300` | `_peel_passthrough` arg | |
| A13 | `binaryio.py:300` | `_peel_passthrough` return | |
| A14 | `binaryio.py:322` | `_metadata_end_size(stream)` | |
| A15 | `binaryio.py:355` | `_seek_end_is_cheap(stream)` | |
| A16 | `binaryio.py:371` | `_under_buffer` arg | |
| A17 | `binaryio.py:371` | `_under_buffer` return | |
| A18 | `binaryio.py:452` | `is_stream(obj)` | TypeGuard input |
| A19 | `binaryio.py:471` | `raise_if_text_stream(obj)` | |
| A20 | `binaryio.py:631` | `ensure_binaryio(obj)` | |
| A21 | `binaryio.py:679` | `ensure_bufferedio(obj)` | |
| A22 | `binaryio.py:535` | `BinaryIOWrapper.write(data)` | unused body (raises) |
| A23 | `verify.py:76` | `_algo_key(algorithm)` | better: `HashAlgorithm \| str` (FIX-IN-CODE in PR 5 if not folded here) |
| A24 | `verify.py:126` | `_make_hasher(algorithm)` | same |
| A25 | `types.py:457` | `ArchiveMember._raw` | **private**. `object` is honest. **Not in staged PR 2** — its own change after PR 1, and it must add the missing narrowing at `tar_reader.py:470`. See the sequence note above |

### Public `Any` — Q1 **DECIDED A** (tightened in this PR)

| ID | Site | Where | Notes |
|---|---|---|---|
| A26 | `types.py:451` | `ArchiveMember.extra` | **done** — `dict[str, object]` |
| A27 | `types.py:570` | `ArchiveInfo.extra` | **done** — `dict[str, object]` |
| A28 | `types.py:536` | `ArchiveMember.replace(**kwargs)` | **done** — `**kwargs: object`. Removes the `Any`, but **checks nothing**: every value was acceptable before and every value is an `object`, so a misspelled field is still only a runtime `TypeError`. Typing the names needs a per-field `TypedDict` (deferred) |

Per-format TypedDict aliases are a later option; not the field type. See [`QUESTIONS.md`](QUESTIONS.md).

### FIX-IN-CODE (both-error with `object`) — staged PR 5

| ID | Site | Without `Any` (`object`) | Proposed type |
|---|---|---|---|
| A29 | `iso_reader.py:211` `date` | no `gmtoffset` | pycdlib date Protocol |
| A30 | `iso_reader.py:368` `record` | no `is_dir` | pycdlib dir-record Protocol |
| A31 | `iso_reader.py:422` `rr` | no `dr_entries` | Rock Ridge Protocol |
| A32 | `iso_reader.py:447` `rr` | same | same |
| A33 | `iso_reader.py:465` `rr` | no `symlink_path` | same |
| A34 | `iso_reader.py:158` `iterable` | not `Iterable` | `Iterable[object] = ()` |
| A35 | `iso_reader.py:158` `*args` | not `int \| None` (`maxlen`) | drop `*args/**kwargs`; match `deque(iterable, maxlen=None)` |
| A36 | `zip_reader.py:808` `_zipfile_lock` → | not a context manager | `AbstractContextManager[object]`; typeshed omits `ZipFile._lock` (comment already there) |
| A37 | `decompress.py:303` `_decomp` brotli | no `process` | `Protocol` with `process` |
| A38 | `decompress.py:480` `_decomp` ppmd | no `decode` | `Protocol` with `decode` |
| A39 | `decompress.py:724` `_decomp` deflate64-ish | no `decode` | same family |
| A40 | `decompress.py:787` `_decomp` inflate64 | no `inflate` | `Protocol` with `inflate` |
| A41 | `decompressor_stream.py:53` `state: Any` | assigned to `_XzBlockBounds` | bound the generic / use the block type |
| A42 | `decompressor_stream.py:192` `scan_fn: Callable[..., list[Any]]` | no `decompressed_start` | `list[_Block]` |
| A43 | `decompressor_stream.py:193` `to_point: Callable[[Any], SeekPoint]` | same | `_Block` |
| A44 | `decompressor_stream.py:199` `include_block: Callable[[Any], bool]` | no `uncompressed_size` | `_Block` |
| A45 | `binaryio.py:391` `source_byte_size(source)` | no `tell`/`seek` on the SEEK_END path | Protocol with `tell`/`seek`, or getattr those two calls |
| A46 | `binaryio.py:514` `BinaryIOWrapper.__init__(raw)` | no `read` | a small "has `read`" Protocol; that is the wrapper's point |
| A47 | `verify.py:68` `_ExpectedHashes = Mapping[Any, bytes]` | callers pass `Mapping[HashAlgorithm, bytes]` | `Mapping[HashAlgorithm \| str, bytes]` |
| A48 | `verify.py:69` `_DigestTransforms` | same pattern | `Mapping[HashAlgorithm \| str, Callable[[bytes], bytes]]` |

## `assert isinstance` (13 at `94468bd0`, 12 at this branch's head)

All of them: **both-clean** without the assert. They are runtime checks on
backend handles, not checker hatches, given `_raw: Any`.

| ID | Site | Type | Disposition |
|---|---|---|---|
| R1 | `iso_reader.py:478` | `ns_path` is `str` | KEEP — runtime; ISO namespace path |
| R2–R4 | `rar_reader.py:1063, 1210, 1294` | `RarMemberInfo` | KEEP |
| R5 | `sevenzip_pipeline.py:552` | `PlainHeader` | KEEP |
| R6 | ~~`sevenzip_reader.py:302`~~ | `PlainHeader` | **gone** — removed by one of the 7z commits merged in from `main`. Counted in the census below and in `SUMMARY.md`; no longer a site |
| R7–R10 | `sevenzip_reader.py:391, 423, 694, 762` | `_MemberRaw` | KEEP |
| R11 | `tar_reader.py:763` | `tarfile.TarInfo` | KEEP — pairs with C8 |
| R12–R13 | `zip_reader.py:1425, 1464` | `zipfile.ZipInfo` | KEEP |

Do not DELETE these as "unused hatches". They are the loud failure if a member
reaches a backend without its handle.

## Out of census (explicit)

- `memoryview(...).cast("B")` in `binaryio.py` and `codecs.py` — runtime
  `memoryview` method, not `typing.cast`.
- `# type: ignore` / `# pyrefly: ignore` / `Any` / `cast` in `tests/`,
  `benchmarks/`, `scripts/`, `review/` — out of scope per the brief.
- `# noqa: F401` re-exports — out of scope.
- `# noqa: BLE001` — `exception-catchalls/` review.
