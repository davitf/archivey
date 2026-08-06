# Close member streams when the reader closes

## Why

`reader.close()` left member streams open and readable. That was specified — the
lifecycle requirement said an escaped stream **MAY** remain usable — but it is the
wrong default, for three reasons.

**It disagrees with the stdlib.** `zipfile.ZipFile.close()` and
`tarfile.TarFile.close()` both invalidate member streams. Someone porting code gets a
behaviour they did not ask for, and `migrating.md` never mentioned it.

**It leaks.** Dropping a member stream without closing it held a file descriptor until
process exit — `+1 fd` on every backend, unchanged after three `gc.collect()` passes.
The reader's own close is where a caller expects that to be cleaned up.

**The principle it was resting on says something narrower.** `archive-reading` states
"never silently close/invalidate a held stream", but that sentence lives inside the
*concurrency gate* paragraph: it is the rule that makes a second overlapping `open()`
raise `ConcurrentAccessError` instead of quietly closing the first. It is about how
contention is resolved, not about lifetime. Reading it as a lifetime guarantee is what
produced the escaped-stream contract.

Raised by the maintainer while reviewing `docs/reading-members.md`, which is why the
page had been left deliberately silent on the question.

## What changes

`close()` closes every member stream still open on the reader, after the reader has
transitioned to closed — so a `close()` that raises on an active pass leaves streams
untouched. Each stream close releases its lease, and teardown runs once after the last
one, so the source is still never closed underneath a live stream. That ordering is
what keeps the rapidgzip abort (`known-issues.md` Bug 3) off this path: streams close
first, the source after.

**A separate bug, found while measuring the leak and fixed here.** The safety-net
finalizer could never fire. `_register_public_stream` built a close callback that
captured the *stream*, and `weakref.finalize` keeps its callback alive until it fires —
so the stream kept itself alive, and the weakref never died. `ReaderState` only ever
needed `id(stream)`, so the callback now captures the identity token instead. The
comment on `_attach_finalizer` already said "do not keep the stream alive"; the
closure two files away defeated it.

## Impact

- `src/archivey/internal/base_reader.py` — a weak registry of public streams,
  `_close_public_streams()`, and the finalizer fix.
- `src/archivey/internal/reader_state.py` — `release_live_stream_id()`.
- `openspec/specs/archive-reading/spec.md` — two requirements: the gate sentence is
  scoped to contention, and the lifecycle requirement inverts.
- `docs/reading-members.md` — states the behaviour, replacing advice written to
  survive either outcome.
- **Behaviour change.** Code that reads a member stream after closing its reader now
  fails. Six tests asserted the old contract and are inverted; two new tests cover the
  finalizer, one of which fails without the fix.
