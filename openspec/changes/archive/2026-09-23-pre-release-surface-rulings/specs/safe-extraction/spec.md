## MODIFIED Requirements

### Requirement: Per-Reader Extract-All Helper

`ArchiveReader.extract_all()` SHALL expose per-reader extraction with optional
member selection and filtering:

```python
def extract_all(
    dest: str | Path,
    *,
    members: MemberSelector | None = None,
    filter: MemberFilter | None = None,
    policy: ExtractionPolicy = ExtractionPolicy.STRICT,
    overwrite: OverwritePolicy = OverwritePolicy.ERROR,
    on_error: OnError = OnError.STOP,
    on_progress: Callable[[ExtractionProgress], None] | None = None,
    limits: ExtractionLimits | None = None,
) -> ExtractionReport: ...
```

The helper SHALL record a diagnostic watermark at call start and return a report
whose summary contains exact count/retained deltas for this extraction call only.
`reader.diagnostics` remains cumulative. The call SHALL run under the reader's
open config — its collector, diagnostic policy, callback and retention maximum —
and SHALL NOT take a `config=`; `limits=` overrides only the extraction limits.

Selection, filter ordering, one-pass selected extraction, reader-config
inheritance, and per-call limits precedence retain their existing contracts.
There is no single-member `reader.extract()` method.

#### Scenario: extract_all matrix

| Case | Expected |
| --- | --- |
| Reader emitted a diagnostic before `extract_all()` and another during extraction | Report summary includes only the extraction occurrence; `reader.diagnostics` includes both |
| `reader.extract_all(dest, members=["a", "b"])` on a solid archive | Only selected members are extracted in one decompression pass |
| Caller wants one file | Uses `reader.extract_all(dest, members=[name])`; no separate single-member API |
