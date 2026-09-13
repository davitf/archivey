## MODIFIED Requirements

### Requirement: Capability-gate behavior is tested on every format

The test suite SHALL cover the declared-capability gate uniformly for every
implemented format, including directory. A reader opened without
`concurrent_members=True` MUST raise `ConcurrentAccessError` on a second
overlapping `open()` while the first stream stays readable; sequential
`open -> read -> close -> open next` MUST succeed without any declaration. The
error message MUST include the recorded `open_archive()` call site and MUST name
`concurrent_members=True` as the parameter that would have allowed the operation.

Without `seekable_members=True`, member streams from random `open()` and
`stream_members()` MUST report `seekable() is False` and raise
`io.UnsupportedOperation` from `seek()` on every format, including real directory
files. With `seekable_members=True`, positioning MUST work where the backend
provides it. `extract_all()`, including hardlink recovery and symlink-target reads,
MUST succeed on readers with no declared capabilities. `ArchiveyUsageError` and
`ConcurrentAccessError` MUST NOT be `ArchiveyError` subclasses. Accelerator/index
activation MUST be demand-driven and match `seekable-decompressor-streams`.

#### Scenario: capability-gate matrix

| Case | Expected |
| --- | --- |
| Second overlapping `open()` on each implemented format without `CONCURRENT` | `ConcurrentAccessError` names the open site; first stream remains readable |
| Sequential open/read/close loop without declarations | Succeeds on every implemented format |
| `ConcurrentAccessError` inside `except ArchiveyError` | Propagates out of that handler |
| Undeclared accelerator-eligible source | No seek index instantiated |
| Declared `SEEKABLE` accelerator-eligible source | `AUTO` accelerator resolves as specified |

### Requirement: Concurrent member-stream correctness and free-threaded stress

The test suite SHALL exercise the supported post-materialization concurrency
contract from `reader-concurrency` for readers opened with
`concurrent_members=True`. Coverage MUST include representative backend shapes:
directory independent handles, ZIP library-coordinated handles, Archivey
`SharedSource` views for single-file and native 7z/RAR as available, and
Archivey-locked library handles for random-access TAR and ISO.

Tests SHALL cover concurrent `open()` by member and name; independent stream
`read`, `readinto`, `close`, supported positioning, and non-seekable
`io.UnsupportedOperation`; cache publication separate from lifecycle; child
operation-owner scopes; generator abandonment; lifecycle leases, failures,
finalizers, and caller-owned sources; password candidate/provider coordination; and
detected unsupported overlap. Stress tests MUST vary interleavings across threads
and assert exact bytes/state, not merely lack of exceptions.

CI SHALL define a required Linux `free-threaded-concurrency` job that installs
CPython `3.13t`, uses the zero-dependency core environment, and runs tests marked
`concurrent_reader`. The marker SHALL cover directory, ZIP, single-file stdlib
codecs, `SharedSource`, lifecycle/operation state, and TAR. The job MUST fail rather
than skip merely because the GIL is disabled. Optional backend free-threaded support
is not claimed until an equivalent dedicated job can install and run that backend.
ISO multi-thread coverage runs in the ordinary `[all]` matrix until a dedicated
extras job exists.

The TAR/ISO correctness-lock implementation SHALL record a proportionate baseline:
wall time, lock wait/hold time, and practical seek/decompression/read metrics.
There is no pass/fail performance threshold. A later optimization or speed claim
MUST include targeted before/after measurements for the mechanism it changes; peak
memory and broader DIRECT/SOLID workloads are required only when that strategy can
affect buffering, materialization, or decompression work.

#### Scenario: concurrency-test matrix

| Case | Expected |
| --- | --- |
| Available representative backend materialized and workers use distinct streams under varied interleavings | Exact bytes and independent supported positions; non-seekable streams keep standard unsupported-operation behavior |
| Required `free-threaded-concurrency` job runs under CPython `3.13t` | Passes without cache, lifecycle, password, or source-position data races |
| Multi-thread workers cover core backends (directory, ZIP, stdlib single-file, SharedSource, plain TAR) | Exact member bytes and documented misuse errors |
| TAR/ISO correctness lock implemented | Practical serialization metrics recorded without a correctness speed threshold |
| Later performance claim changes handle sharing, decoding, or locks | Focused before/after metrics for affected resources |
