# `binaryio` `Any` and the typeshed `BinaryIO` split

Two clusters, one cause: this codebase needs a "binary stream" type, and
typeshed gives three that do not coincide (`typing.BinaryIO`, `io.IOBase`,
`typing.IO[bytes]`).

S5b already decided not to drop the `BinaryIO` base (64 errors on each
checker). This page is what that decision implies for the remaining hatches.
Do not re-derive S5b.

## Cluster 1 — `binaryio.py` takes `Any` because sources are not one type

`is_filename` / `is_stream` / `is_seekable` / `source_name` / `source_byte_size`
/ `ensure_binaryio` accept paths, fds-as-objects, stdlib streams, and duck
file-likes. A union of everything they handle is not shorter than `object`
plus the TypeGuards.

Probe: `Any` → `object` is **both-clean** on every helper except:

- `source_byte_size` — the cheap `SEEK_END` path calls `outer.tell()` /
  `outer.seek()` directly. getattr those two, or a Protocol with `tell`/`seek`.
- `BinaryIOWrapper.__init__(raw)` — the wrapper exists to adapt a partial
  file-like; it needs a `read` Protocol, which is the class's job.

Staged PR 2 is the both-clean set. The two leftovers go with PR 5.

Returning `Any` from `_peel_passthrough` / `_under_buffer` also tightened to
`object` cleanly. They only flow back into the same helpers.

## Cluster 2 — `cast(..., BinaryIO)` where typeshed says `IO[bytes]`

These are not sloppy casts. Both checkers name the mismatch when the cast is
removed:

| What we have | What typeshed says | Sites |
|---|---|---|
| `ZipFile.fp` / `ZipFile.open` | `IO[bytes]` | C12, C13, C14 |
| `ensure_bufferedio` | `io.BufferedIOBase` | C4, C20, C22 |
| `SpooledTemporaryFile[bytes]` | not `BinaryIO` | C15 |
| `Popen[bytes].stdout` | `IO[Any]` | C3 |
| pycdlib member handle | `PyCdlibIO` | C2 |

`full_count.py:172` already comments this ("BufferedIOBase is a BinaryIO at
runtime; typeshed models the two separately"). The others should get the same
one-line reason when PR 7 lands. Do not invent a repo-wide Stream Protocol to
delete them — S5b measured that cost.

## What is *not* this gap

Four casts are a **local** missing overload, not typeshed:

```python
def _track_source_seeks(self, source: Path | BinaryIO) -> Path | BinaryIO
```

Callers that passed a `BinaryIO` still see `Path | BinaryIO` out. `@overload`
deletes C5, C6, C10, C11. Staged PR 3. That is the preferred FIX-IN-CODE the
brief asked for, and it does not reopen S5b.
