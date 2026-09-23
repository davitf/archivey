# reader-concurrency — password provider reentry scope

## MODIFIED Requirements

### Requirement: Concurrent password resolution stays lock-free for providers

The candidate/provider model in `archive-reading` SHALL remain safe for
concurrent post-materialization opens. Static candidates are immutable and
ordered. Known-good snapshots/promotions and per-unit tried/attempt state are
synchronized; expensive key derivation/decryption runs without
lifecycle/operation, materialization, or password-state locks, but MAY use a
required backend/source lock around an atomic decode/handle operation.

At most one provider call SHALL run per reader at a time, with no Archivey lock
held. A worker that needs the provider while another worker's call runs SHALL
wait for that call to return rather than fail. The turn covers one call, not a
unit's run of attempts, and a waiter does not recheck known-good first: under
concurrent first-touch the provider MAY be asked, and a candidate tried, more
than once, and promotion still converges. Attempt counts remain per encrypted
unit.

A provider that starts another password-requiring operation on the same reader
SHALL raise `ArchiveyUsageError` rather than deadlock when that operation runs on
the provider's own thread or in a context copied from the provider call
(`contextvars.copy_context().run`, `asyncio.to_thread`, a thread that inherits
its starter's context). A helper thread with neither cannot be told apart from an
independent worker, so it waits like one; a provider that blocks on such a thread
while it reads the same reader deadlocks.

#### Scenario: concurrent password matrix

| Case | Expected |
| --- | --- |
| Workers concurrently open differently encrypted units after materialization | Each follows candidate order independently; promotions race-free; attempt state not overwritten |
| Two workers need the provider at once | One callback at a time, no Archivey lock; second waits, then runs |
| Provider starts another password op on same reader, same thread or copied context | Nested op → `ArchiveyUsageError` |
| Provider blocks on a context-less helper thread that reads the same reader | Helper waits for the provider's turn: deadlock, caller's construction |
