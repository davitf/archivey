## MODIFIED Requirements

### Requirement: Detection owns matching and registry selects by format

The system SHALL keep format detection and backend selection separate.
`detect_format()` is the authority for source format: it aggregates backend
`MAGIC`, `EXTENSIONS`, and `CONTENT_PROBES`, performs special probes through
the detection workspace, consumes no bytes, and raises `FormatDetectionError` when no
format matches. The registry SHALL map the resolved `ArchiveFormat` to a
registered available backend. If a detected format has no available backend,
lookup SHALL raise `UnsupportedFormatError` with the install hint.

```python
class BackendRegistry:
    def register_reader(self, backend_cls: type[ReadBackend]) -> None: ...
    def reader_for_format(self, format: ArchiveFormat) -> type[ReadBackend]: ...
    def register_writer(self, backend_cls: type[WriteBackend]) -> None: ...
    def writer_for_format(self, format: ArchiveFormat) -> type[WriteBackend]: ...
    def list_formats(self) -> list[ArchiveFormat]: ...
    def list_writable_formats(self) -> list[ArchiveFormat]: ...
```

#### Scenario: detection/selection matrix

| Case | Expected |
| --- | --- |
| Detection reports `ArchiveFormat.SEVEN_Z` | `reader_for_format()` returns native `SevenZReadBackend` |
| Detected backend's optional dependency is missing | `UnsupportedFormatError` names missing package and install hint |
| No magic/probe/extension matches | `FormatDetectionError`; no backend lookup |
