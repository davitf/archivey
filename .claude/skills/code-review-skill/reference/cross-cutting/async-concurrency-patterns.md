# Concurrency — review guide

Optional. The public API is sync-only (`AGENTS.md` §Conventions); do not suggest async
for library code. Concurrency here is threads over one archive and child processes for
native decoders. Background: `dev-docs/investigations/parallel-reader.md`.

## Threads over one reader

`open_archive(concurrent_members=True)` declares `MemberStreams.CONCURRENT`: first-touch
materialization is coordinated, then workers may read members in parallel. Forward-only
and streaming passes stay single-owner.

- [ ] Shared handles: `LockedStream` holds its lock across every read, seek and tell
  where seek-then-read must be atomic (TAR, ISO); `SharedSource` gives independent
  positions over one file (ZIP-style). A new backend picks one deliberately
- [ ] No reader state mutated outside the lock once the snapshot is published
- [ ] No lock held across a decompress of unbounded size, and a consistent lock order
  where two are taken
- [ ] A test that claims thread safety runs real threads against the shared path, not a
  sequential loop

## Child processes

`internal/streams/child_process.py` starts and ends every child: the pyppmd and
rapidgzip workers, and the external programs (`unrar`, `unar`).

- [ ] A child starts through that module (`python_argv` passes `-P`, so a sibling module
  cannot shadow the standard library) and is always reaped, including on an exception
  and on early close
- [ ] Only a crash is a verdict on the data; a child ended from outside (the OOM killer,
  a signal) is not reported as corruption
- [ ] Pipes are drained or closed so neither side blocks on a full buffer

## Tests, benchmarks and tooling

Async and pools are fine there. Check that tasks and threads are joined, concurrency is
bounded, and the parallelism does not leak into library defaults.
