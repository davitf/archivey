# TypeGuard honesty

Three guards. One is fine. Two over-claim. None currently produces the #324
failure (`str` out of `read()` on a handle the checker believed was `BinaryIO`).

Probes: replacing `TypeGuard[T]` with `bool` is both-error at callers in all
three cases — the guards are doing work. The question is whether a `True`
return is always a `T`.

## G1 — `is_filename` (`binaryio.py:276`) — fine

```python
def is_filename(obj: Any) -> TypeGuard[str | bytes | os.PathLike]:
    return isinstance(obj, (str, bytes, os.PathLike))
```

The runtime check is the declared union. `os.PathLike` is an ABC;
`isinstance` is the specified test. No known True-but-not-T case.

Param `Any` tightens to `object` (inventory A10). Keep the `TypeGuard`.

## G2 — `is_stream` (`binaryio.py:452`) — leftover of #324

```python
def is_stream(obj: Any) -> TypeGuard[BinaryIO]:
    if isinstance(obj, io.TextIOBase):
        return False
    if isinstance(obj, io.IOBase):
        return True
    if is_filename(obj):
        return False
    if not all(callable(getattr(obj, m, None)) for m in _IO_METHODS):
        return False
    return hasattr(obj, "closed")
```

#324 made the `TextIOBase` cut. The PR body left two True cases that are not
`BinaryIO` in the sense callers need (`read() -> bytes`):

1. **Write-only `io.IOBase`.** `open(path, "wb")` is `IOBase`, not `TextIOBase`,
   so this returns True. `ensure_binaryio` then passes it through. A later
   `read()` fails at runtime. Not a silent `str`; still a lying guard.
2. **Duck-typed objects whose `read()` returns `str`.** The method-set check
   does not inspect the return type of `read`. A text duck with `read` / `seek`
   / `tell` / `close` / `readable` / `writable` / `seekable` / `readinto` /
   `closed` passes. That is the original #324 shape, minus the stdlib text
   classes.

**Fix direction (staged PR 4).** Prove readable-binary, not "looks like IO":

- Reject `IOBase` that is not readable (`readable()` is False), or that is
  `TextIOBase` (already).
- For the duck branch, sample is not acceptable on the hot path. Options: keep
  the method-set check and document that a text duck is a caller bug; or wrap
  rather than pass through unless `isinstance(obj, (io.RawIOBase, io.BufferedIOBase))`.
  Wrapping a text duck still yields `str` from `BinaryIOWrapper.read` unless
  that wrapper asserts `bytes`. A `bytes` assertion in `BinaryIOWrapper.read`
  is the backstop that makes a lying duck fail at the wrapper, not three layers
  down.

Recommendation: readable-`IOBase` cut + `bytes` check in `BinaryIOWrapper.read`
(and `readinto`). Needs tests; this PR is inventory only.

## G3 — `_is_source_sequence` (`volumes.py:470`) — element type unproved

```python
def _is_source_sequence(source: OpenSourceInput) -> TypeGuard[SourceSequence]:
    if isinstance(source, (str, Path, bytes)):
        return False
    if is_stream(source):
        return False
    return isinstance(source, Sequence)
```

`SourceSequence = Sequence[SourceItem]` and `SourceItem = str | Path | BinaryIO`.
The guard proves `Sequence` after excluding the three scalers that are also
sequences (`str`/`bytes`) or look like one (`Path`). It does not prove the
elements.

`bytearray` is a `Sequence`, is not `bytes` (the class check is exact), is not
a stream, so the guard returns True. `resolve_source` then iterates integers
and `_coerce_path_or_stream` blows up. Loud, not silent.

**Fix direction.** After the `Sequence` check, reject `bytearray` / `memoryview`
explicitly (they are the realistic False-friend), or inspect the first element
against `SourceItem`. Empty sequence is already an `ArchiveyUsageError`. Staged
PR 4; add a test for `open_archive(bytearray(…))` so the error stays an
`ArchiveyUsageError` rather than a TypeError from `ensure_full_count_reads`.
