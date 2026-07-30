## MODIFIED Requirements

<!-- Callers declare concurrency as `open_archive(concurrent_members=True)`; the
`CONCURRENT` / `SEEKABLE` flag names are retained below wherever they denote the
reader's *capability state* rather than the call that produced it. This spec is the
implementer contract and the flags remain the internal representation. -->

### Requirement: Multiple concurrently-open member streams

Every **random-access** reader (`streaming=False`) opened with
`concurrent_members=True` SHALL support any number of member streams from that
reader held open simultaneously and read interleaved without corrupting one
another. Without that flag the single-live-stream default in `archive-reading`
applies. Access cost never determines legality: declared capabilities are
honored on every format; `AccessCost` / `solid_block_count` only describe
expense.

**Post-materialization worker seam.** After one owner has completed `members()`
or `scan_members()` and the reader has published its member list/name index,
concurrent calls from multiple threads to `open(member_or_name)` SHALL be
supported. Streams from different opens SHALL have independent logical
positions/state: workers MAY concurrently call `read`, `readinto`, and `close`
on **different** stream objects, plus `seek`/`tell` when
`seekable_members=True` was declared and that stream supports positioning. Non-seekable
streams retain normal `BinaryIO` behavior (`seekable()` false;
unsupported positioning → `io.UnsupportedOperation`). Simultaneous operations on
the **same** stream object require caller synchronization (ordinary file
semantics). Supported behavior SHALL NOT rely on the GIL.

**Materialization boundary.** Members and name index SHALL be built in
private/local state, completed (including link resolution), and published
together exactly once as immutable internal containers. A public API whose
return type is `list` SHALL return a copy that cannot structurally mutate those
cache containers. `ArchiveMember` objects retain backend-populated late-bound
fields. Under `CONCURRENT`, overlapping first-touch materialization is
coordinated (see next requirement). Without `CONCURRENT`, a second overlapping
materialization SHALL raise `ArchiveyUsageError`. Distinct reader-wide passes
remain single-owner. Late-bound random-open updates to a member MUST be
idempotent and synchronized; conflicting last-writer-wins updates are
forbidden. Materialization state is exactly `UNMATERIALIZED` / `MATERIALIZING` /
`MATERIALIZED`; reader lifecycle is separate and MUST NOT add `CLOSED` to the
cache state. A failed build discards private state and returns to
`UNMATERIALIZED`.

**Backend compliance.** Archivey-owned byte-range backends MUST use views with
per-view positions and atomic shared-source handle operations. External-library
backends MUST provide equivalent coordination: ZIP MAY rely on stdlib
`_SharedFile` for seek/read and MUST serialize `ZipFile.open` /
member-stream close / `ZipFile.close` under `CONCURRENT` so free-threaded
`_fileRefCnt` updates cannot race; random-access TAR and ISO MUST use the
one-per-reader lock specified by `tar-concurrent-open`, covering every operation
on the shared library handle. A solid format satisfies correctness by giving each
returned stream independent logical position/state. It MAY use per-open decoders
or a synchronized, bounded/spooled shared decode/materialization strategy; the
contract neither requires one decoder per open nor promises elimination of
redundant decompression.

**Reader-wide operation ownership.** Distinct reader-wide passes (`__iter__`,
`stream_members`, `extract_all`) and `scan_members` /
`members_report_if_available` initialization remain single-owner and cannot overlap
one another or the random worker seam. Under `CONCURRENT`, first-touch
materialization is coordinated (wait/share) and `reader.close()` drains
in-flight worker calls rather than rejecting them. The base reader SHALL
represent ownership with an explicit unforgeable root token, not thread
identity. Private helpers MAY receive that token to enter child scopes:
materialization may perform link-data reads; a random worker `open()` may do
name lookup/link following and late link-data reads; `extract_all` may inspect
available members/source counters and drive one or more `stream_members`
passes; and a pass may advance and perform I/O/close on its yielded stream. An
unrelated/reentrant public call has no token even on the owner thread and is
rejected. The later conflicting operation SHALL raise `ArchiveyUsageError`
before changing state; the earlier root and children remain usable.

Random `open()` and each operation on a random-open stream SHALL hold a
short-lived worker token only while that call executes. An idle open stream owns
a lifecycle lease, not active operation ownership. It carries a private
lease-bound entry capability so later stream I/O remains admissible after
`reader.close()`. Under `CONCURRENT`, `close()` waits for in-flight worker
tokens to drain, then closes; without `CONCURRENT`, close is rejected while a
worker call is executing. Closure does not enable any new reader API.

"Overlap" means concurrent method/I/O execution, not the lifetime of an idle
open member stream: a non-concurrent `reader.close()` MAY run while member
streams remain open, and their leases preserve resources for later stream I/O.
This is not a blanket thread-safety guarantee for every reader method.

**`stream_members()` is separate.** A `stream_members()` pass owns the reader's
one-pass data path. It MUST NOT overlap random `open()` work or any other
forward/data pass. Advancing the iterator closes/invalidates the previously
yielded stream before yielding the next; this iterator-owned lifecycle does not
apply to independent streams returned by random `open()`. The yielded stream
carries a child scope so its I/O is permitted during the pass. Exhaustion,
exceptions, explicit generator close, and generator abandonment/finalization
SHALL close the current yielded stream and release the pass scope/token exactly
once. A caller needing simultaneous streams SHALL materialize and use random
`open()`.

**Cost is informational.** `AccessCost.SOLID` / `solid_block_count` tell callers
that simultaneous random streams may repeat decompression. They never disable
the guarantee. `stream_members()` remains the efficient bounded-memory,
one-decode path for a sequential solid-archive workload.

**Detectable closed-source misuse and bounds.** A live lease prevents
reader-owned backend resources from closing underneath a member stream. If a
caller-owned source is nevertheless closed externally, the reader surface SHALL
raise a typed error rather than return arbitrary or empty bytes. Archivey
shared-source views clamp bounds extending past the available source like normal
streams, so truncation produces a short view rather than a construction failure.

**Callbacks and lock scope.** Password providers, selectors/filters, progress
callbacks, logging handlers, diagnostic formatting/stamping,
`sys.unraisablehook`, and user-visible close/finalizer hooks MUST execute
without any Archivey lock held. Decode/password candidate validation is not a
callback: it MUST run without lifecycle/operation, materialization, or password
locks, but MAY hold the narrowly required backend/source lock around an atomic
decoder/handle operation. Nested reader-state order is lifecycle/operation →
materialization → password. Backend/source locks are leaves. Individual stream
state uses claim/call/publish and MUST be released before invoking lazy
`open_fn`, inner I/O/close, backend/source operations, or lifecycle lease
release.

#### Scenario: concurrency matrix

| Case | Expected |
| --- | --- |
| Post-`members()`, two threads `open()` different members | Independent correct bytes/positions; no cache/reader/source race |
| `CONCURRENT` interleaved reads, any format | Both correct; `AccessCost` describes expense only |
| Concurrent first-touch on unmaterialized `CONCURRENT` reader | One builder; others wait; all proceed on published snapshot; no overlap `ArchiveyUsageError` |
| Materialization fails before publish | Private state discarded; `UNMATERIALIZED`; waiters re-elect/see error; lifecycle stays `OPEN` |
| Workers active + `__iter__`/`stream_members`/`extract_all` | Later op → `ArchiveyUsageError`; active streams OK |
| `close()` under `CONCURRENT` with in-flight workers | Blocks until return; closes; no raise merely for active workers; idle escaped streams stay leased |
| Two members in one solid block opened together | Correct independent bytes; may re-decode; no concurrency exception |
| Same on CPython `3.13t` free-threaded job | Data-race-free; same observables as regular build |

### Requirement: Coordinated first-touch materialization

A reader opened with `concurrent_members=True` SHALL coordinate concurrent
first-touch operations on a not-yet-materialized member list by blocking all but
one caller until the immutable snapshot is published, rather than rejecting the
overlap. Materialization SHALL run exactly once. Non-concurrent and uncontended
paths SHALL be unchanged (no waiting introduced; member scan, link reads, and
callbacks still run with no reader-state lock held).

#### Scenario: first-touch matrix

| Case | Expected |
| --- | --- |
| Several threads first-touch via `open()`/`members()`/`get()` | One materializes; others wait; all proceed on snapshot; no overlap `ArchiveyUsageError` |
| Electing materialization fails (e.g. corrupt header) | Back to unmaterialized; no partial snapshot; waiters see error or re-elect |
| Default reader or uncontended path | No waiting; scan/link reads/callbacks with no reader-state lock held |

### Requirement: Draining reader close

Under declared concurrency, `reader.close()` SHALL wait for in-flight
worker `open()`/`read()` calls to return and then transition the reader to
closed, rather than raising because workers are active. Escaped open member
streams SHALL remain governed by the lifecycle-lease contract in
`archive-reading`. Close idempotency, one-shot teardown, and post-close
rejection SHALL be preserved.

#### Scenario: draining close matrix

| Case | Expected |
| --- | --- |
| `close()` while workers execute | Blocks until return; then closed; no raise merely for active workers |
| Escaped stream still open as `close()` returns | Readable until its `close()`; teardown once after final lease |
| Two threads `close()` / `__exit__` | Teardown once; both return; dual failures → one `ExceptionGroup` |
| Op after `close()` returned | `ArchiveyUsageError` (unchanged) |
