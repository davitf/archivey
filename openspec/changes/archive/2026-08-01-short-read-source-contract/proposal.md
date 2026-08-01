# short-read-source-contract

## Why

`read(n)` on a raw stream is an *up-to-n* contract, and real sources use it — sockets,
FUSE mounts, user-written wrappers. Header parsers assumed the full count and read a
short return as EOF, so a **healthy** archive supplied as such a stream was reported as
`CorruptionError` / `TruncatedError`: 26 of 27 committed RAR/ZIP fixtures, the `tar` /
`zip` / `iso` corpus formats, and `open_stream(bz2, seekable=True)`. PR #219 fixes it by
buffering seekable caller sources at the source boundary (`ensure_full_count_reads`).

Nothing in `tests/` returned short — `NonSeekableBytesIO` and `CountingBytesIO` both
delegate to `BytesIO`, which is always full-count — which is why this was invisible for
the whole of Phase 2. That blind spot is what the specs should close, so the sweep is
required coverage rather than a file someone can delete.

## What changes

- `testing-contract`: **ADDED** a short-returning source requirement, mirroring the
  existing non-seekable coverage requirement. Fixes the coverage gap, not a behaviour.
- `access-mode-and-cost`: **MODIFIED** the open-mode requirement so the buffering it
  already forbids (materializing a *non-seekable* source to fake seekability) reads
  clearly against the buffering it now permits (a fixed-size read buffer over a
  *seekable* stream). The two sentences are one edit apart and would otherwise look
  contradictory.

No public API change. The behaviour is already implemented in PR #219; this records the
contract it satisfies.

## Impact

| Capability | Change |
| --- | --- |
| `testing-contract` | +1 requirement (short-returning source coverage) |
| `access-mode-and-cost` | 1 requirement clarified (source buffering) |

Affected code (already landed in #219): `streamtools/binaryio.py`
(`ensure_full_count_reads`), `internal/volumes.py`, `core.py`,
`tests/test_short_read_sources.py`, `tests/streams_util.py`.
