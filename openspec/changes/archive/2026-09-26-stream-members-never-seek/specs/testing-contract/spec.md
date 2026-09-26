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
files. With `seekable_members=True`, every member stream from random `open()`
MUST report `seekable() is True` and positioning MUST work (loud-slow-rewind
when there is no index). A `stream_members()` handle MUST report `seekable() is False`
and raise `io.UnsupportedOperation` from `seek()` on every format with
`seekable_members=True` too, both with `streaming=False` and with `streaming=True`,
and its `tell()` MUST work. One parametrized test SHALL cover all five cases (default
`open()`, default `stream_members()`, declared `open()`, declared `stream_members()`,
declared streaming `stream_members()`) over one matrix of format fixtures, so a
format cannot pass one case and be left out of another. `extract_all()`, including
hardlink recovery and symlink-target reads,
MUST succeed on readers with no declared capabilities. `ArchiveyUsageError` and
`ConcurrentAccessError` MUST NOT be `ArchiveyError` subclasses. Accelerator/index
activation MUST be demand-driven and match `seekable-decompressor-streams`.

#### Scenario: capability-gate matrix

| Case | Expected |
| --- | --- |
| Second overlapping `open()` on each implemented format without `CONCURRENT` | `ConcurrentAccessError` names the open site; first stream remains readable |
| Refused second `open()` on each implemented format | The member is never opened: no member data stream constructed, no helper process spawned |
| Sequential open/read/close loop without declarations | Succeeds on every implemented format |
| `ConcurrentAccessError` inside `except ArchiveyError` | Propagates out of that handler |
| Undeclared accelerator-eligible source | No seek index instantiated |
| Declared `SEEKABLE` accelerator-eligible source | `AUTO` accelerator resolves as specified |
| Each format fixture × {default `open()`, default pass, declared `open()`, declared pass, declared streaming pass} | Only declared `open()` seeks; every other handle is forward-only with a working `tell()` |
