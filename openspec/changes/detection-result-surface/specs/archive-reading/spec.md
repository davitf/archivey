## ADDED Requirements

### Requirement: open_archive and open_stream retain the detection result

`open_archive()` and `open_stream()` SHALL retain the detection result on the returned
reader or stream rather than reading a few fields off it and discarding the object. They
SHALL additionally accept `detection=`:

```python
archivey.open_archive(
    source, *, format=None, detection: FormatInfo | None = None, streaming=False,
    seekable_members=False, concurrent_members=False, password=None, encoding=None,
    config=None,
) -> ArchiveReader
```

`format=` and `detection=` SHALL NOT be supplied together: they are two different claims
about the same question. `format=` is an assertion that skips detection and records
`ASSERTED`; `detection=` replays evidence archivey produced, so the reader's ledger and its
`format_unconfirmed` behaviour match a self-detecting open exactly.

`open_stream()` SHALL expose the container it detected, not only the stream codec.

#### Scenario: retention and handoff

| Case | Expected |
| --- | --- |
| `open_archive(path)` | Reader's detection field carries the winning evidence; no second detection is needed to see it |
| `open_stream(path)` | Stream's detection field present; the container is recoverable |
| `open_archive(source, detection=result)` | Detection does not run again |
| `open_archive(source, format=ZIP, detection=result)` | Usage error |
| `open_archive(source, format=ZIP)` | No detection I/O of any kind; field records `DECLARED_BY_CALLER` |
