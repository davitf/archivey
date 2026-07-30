## MODIFIED Requirements

### Requirement: Caller misuse remains outside ArchiveyError

The system SHALL define `ArchiveyUsageError(Exception)` outside `ArchiveyError`
for detected caller-code bugs. `except ArchiveyError` MUST NOT swallow misuse.

`ConcurrentAccessError(ArchiveyUsageError)` SHALL be raised when a second member
stream opens while another is live on a reader opened without
`concurrent_members=True`. Its message SHALL include the recorded `open_archive()` call
site (`file:line`) and SHALL name `concurrent_members=True` as the parameter that would
have allowed the operation.

`ArchiveyUsageError` SHALL also cover:

- reader-wide exclusive pass overlap (`__iter__`, `stream_members`,
  `extract_all`, materialization, active worker calls);
- close overlapping an active worker call without declared concurrency;
- any reader operation/property after `close()` except repeated `close()` /
  `__exit__`;
- same-reader password-provider reentry into password-requiring work;
- using an `ArchiveMember` from another reader;
- member I/O after a caller closes its supplied source early;
- `open_archive(streaming=True, concurrent_members=True)`;
- `open()` / `read()` of a resolved non-payload member (`DIRECTORY`, `ANTI`,
  `OTHER`). A symlink/hardlink that fails to resolve remains
  `LinkTargetNotFoundError` (`ArchiveyError`) — that is an archive property,
  not caller misuse. A link that resolves to a non-`FILE` then hits the
  non-payload rule above.

The later operation SHALL fail before changing state and MUST leave the earlier
operation/stream usable. Internal owner-child operations are exempt only through
explicit internal tokens; public reentry does not inherit them. Closed stream I/O
continues to raise `ValueError`, and unsupported stream positioning continues to
raise `io.UnsupportedOperation`.

#### Scenario: usage error matrix

| Case | Expected |
| --- | --- |
| `except ArchiveyError` wraps code that raises `ArchiveyUsageError` | Usage error propagates past the handler |
| Second overlapping member stream without `concurrent_members=True` | `ConcurrentAccessError` with open-site `file:line` naming `concurrent_members=True`; first stream still readable |
| Exclusive pass/materialization is active and conflicting public op begins | Later op raises `ArchiveyUsageError`; active op remains valid |
| Operation/property after `reader.close()` | `ArchiveyUsageError`; already-open member stream follows lifecycle lease |
| Repeated `reader.close()` | No error; no repeated backend teardown |
| Unsupported `seek()` on a stream | `io.UnsupportedOperation`, not archivey-typed |
