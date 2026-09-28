# backend-registry — fewer exception types

## MODIFIED Requirements

### Requirement: Detection owns matching and registry selects by format

The system SHALL keep format detection and backend selection separate.
`detect_format()` is the authority for source format: it aggregates backend
`MAGIC`, `EXTENSIONS`, and `CONTENT_PROBES`, performs special probes through
the detection workspace, consumes no bytes, and raises `FormatDetectionError` when no
format matches. The registry SHALL map the resolved `ArchiveFormat` to a
registered available backend. If a detected format has no available backend,
lookup SHALL raise `PackageNotInstalledError` with the install hint.

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
| No magic/probe/extension matches | `FormatDetectionError`; no backend lookup |

### Requirement: ReadBackend and WriteBackend are separate ABCs

The system SHALL define separate `ReadBackend` and `WriteBackend` ABCs because
read and write lifecycles, state, and availability differ. A format may have
read support, write support, both, or read-only support such as RAR.

Read backends SHALL declare all detection signals as data, and each signal SHALL
name the `ArchiveFormat` it implies so multi-format backends can map each
extension/magic/probe to the correct format without re-inspecting the source.
Magic-less or weak-signature formats SHALL use `CONTENT_PROBES` plus extensions;
there is no weak-magic flag.

```python
class MagicSignature(NamedTuple):
    offset: int
    magic: bytes
    format: ArchiveFormat

class ReadBackend(ABC):
    FORMATS: tuple[ArchiveFormat, ...]
    EXTENSIONS: Mapping[str, ArchiveFormat] = {}
    MAGIC: tuple[MagicSignature, ...] = ()
    CONTENT_PROBES: tuple[tuple[ArchiveFormat, Callable[[bytes], bool]], ...] = ()
    SUPPORTS_STREAMING_NON_SEEKABLE: bool = False
    OPTIONAL_DEPENDENCY: str | None = None

    @abstractmethod
    def open_read(self, source, format, streaming, password, encoding, archive_name) -> ArchiveReader: ...

class WriteBackend(ABC):
    FORMATS: tuple[ArchiveFormat, ...]
    OPTIONAL_DEPENDENCY: str | None = None

    @abstractmethod
    def open_write(self, dest, compression, password, encoding) -> ArchiveWriter: ...
```

The `format` argument SHALL be the already-resolved `ArchiveFormat`; multi-format
backends use it to choose a variant, while single-format backends may ignore it.
A missing write backend SHALL raise `UnsupportedFeatureError` for read-only
formats or `PackageNotInstalledError` with an install hint for optional write
formats.

**No writer is registered for any format today.** `register_writer` is never
called, so `writer_for_format` raises `UnsupportedFeatureError` for every
format and the second branch above is unreachable — the write half of the
registry is ABC scaffolding, not a shipped path. There is no `archivey.create`.
The writer surface is parked in
[`archive-writing-design.md`](../../../dev-docs/investigations/archive-writing-design.md)
until `PLAN.md` phase 9.

#### Scenario: backend ABC matrix

| Case | Expected |
| --- | --- |
| `SingleFileBackend.MAGIC` has gzip and bzip2 signatures | Detector resolves `GZ` vs `BZ2`; both are served by one backend |
| `writer_for_format(RAR)` — or any other format | `UnsupportedFeatureError` names the format; nothing is registered to write |

### Requirement: Optional dependencies degrade gracefully

The system SHALL degrade missing optional components to NONE or PARTIAL support
rather than import crashes. Opening a format or reading a member that needs a
missing component SHALL raise an error naming the package/tool and install
command from the same metadata exposed by `format_availability()`.

| Missing component kind | Support | Later error |
| --- | --- | --- |
| Single-codec format backend/codec missing (ISO without `pycdlib`, `.zst` without zstd backend before 3.14, `.lz4` without `lz4`) | NONE | `PackageNotInstalledError` at open with hint |
| Multi-codec container missing optional member codec/tool | PARTIAL | Opens/lists; member read raises `PackageNotInstalledError` or documented missing-tool error |
| 7z writing (not yet implemented) | Read support unaffected | Write raises `UnsupportedFeatureError` |

#### Scenario: graceful degradation matrix

| Case | Expected |
| --- | --- |
| ISO magic source opened without `pycdlib` | `PackageNotInstalledError` names `pycdlib` and `pip install archivey[recommended]`; no `ImportError` |
| `list_supported_formats()` without `pycdlib` | ISO absent; native 7z/RAR and satisfied formats present |
