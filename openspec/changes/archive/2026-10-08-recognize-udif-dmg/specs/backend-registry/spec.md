## MODIFIED Requirements

### Requirement: Detection owns matching and registry selects by format

The system SHALL keep format detection and backend selection separate.
`detect_format()` is the authority for source format: it aggregates backend
`MAGIC`, `TRAILER`, `EXTENSIONS`, and `CONTENT_PROBES`, performs special probes through
the detection workspace, consumes no bytes, and raises `FormatDetectionError` when no
format matches. The registry SHALL map the resolved `ArchiveFormat` to a
registered available backend. If a detected format has no available backend,
lookup SHALL raise `PackageNotInstalledError` with the install hint.

A backend that declares the format and sets `READ_IMPLEMENTED` false is not a missing
dependency. `format_availability` SHALL report `NONE` with an empty `missing`, and
`reader_for_format` SHALL raise `UnsupportedFeatureError` with that backend's message
rather than `PackageNotInstalledError`.

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
| Detected backend's optional dependency is missing | `PackageNotInstalledError` names missing package and install hint |
| Detected format whose backend sets `READ_IMPLEMENTED` false (`DMG`) | `UnsupportedFeatureError` naming the format; `missing` is empty |
| No magic/probe/extension matches | `FormatDetectionError`; no backend lookup |
