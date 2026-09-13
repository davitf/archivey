## MODIFIED Requirements

### Requirement: Optional dependencies degrade gracefully

The system SHALL degrade missing optional components to NONE or PARTIAL support
rather than import crashes. Opening a format or reading a member that needs a
missing component SHALL raise an error naming the package/tool and install
command from the same metadata exposed by `format_availability()`.

| Missing component kind | Support | Later error |
| --- | --- | --- |
| Single-codec format backend/codec missing (ISO without `pycdlib`, `.zst` without zstd backend before 3.14, `.lz4` without `lz4`) | NONE | `UnsupportedFormatError` at open with hint |
| Multi-codec container missing optional member codec/tool | PARTIAL | Opens/lists; member read raises `PackageNotInstalledError` or documented missing-tool error |
| 7z writing (not yet implemented) | Read support unaffected | Write raises `UnsupportedOperationError` |

#### Scenario: graceful degradation matrix

| Case | Expected |
| --- | --- |
| ISO magic source opened without `pycdlib` | `UnsupportedFormatError` names `pycdlib` and `pip install archivey[recommended]`; no `ImportError` |
| `list_supported_formats()` without `pycdlib` | ISO absent; native 7z/RAR and satisfied formats present |

### Requirement: Format support is tri-state and compositional

The system SHALL report readability as FULL, PARTIAL, or NONE:

```python
class FormatSupport(Enum):
    FULL = "full"
    PARTIAL = "partial"
    NONE = "none"

@dataclass(frozen=True)
class MissingComponent:
    name: str
    install_hint: str
    unlocks: tuple[str, ...]

@dataclass(frozen=True)
class FormatAvailability:
    format: ArchiveFormat
    support: FormatSupport
    missing: tuple[MissingComponent, ...]

def format_availability(format: ArchiveFormat) -> FormatAvailability: ...
def list_supported_formats() -> list[ArchiveFormat]: ...
def list_known_formats() -> list[ArchiveFormat]: ...
```

Support SHALL be computed across the format backend and codecs/tools:

- NONE when the format backend is unavailable, or a single-codec format's only
  codec/backend is unavailable.
- FULL for an available multi-codec container only when every optional codec/tool
  it can use is present.
- PARTIAL for available multi-codec containers with missing optional codecs/tools.
- ZIP SHALL remain PARTIAL until Phase 6 routes member decompression through the
  shared codec layer, even if all optional member-codec packages are installed.
- By-design unsupported features such as 7z BCJ2 and unknown 7z method IDs SHALL
  not lower support; members using them raise `UnsupportedFeatureError`.

`list_supported_formats()` SHALL return FULL plus PARTIAL formats.
`list_known_formats()` SHALL return every known format including NONE.

#### Scenario: format support matrix

| Case | Expected |
| --- | --- |
| 7z availability without the optional 7z packages | PARTIAL; missing names each absent package and `[recommended]`; LZMA2/bzip2/copy members still read |
| ZSTD availability before Python 3.14 without zstd backend | NONE with `backports.zstd` / `pip install archivey[recommended]` hint |
| GZIP availability | FULL; no missing components |
| 7z with the optional 7z packages installed | FULL even though BCJ2 still raises `UnsupportedFeatureError` |
| ZIP with every optional member codec installed | PARTIAL with empty missing list until Phase 6 |
| ZIP missing deflate64 and/or zstd packages | PARTIAL; missing names absent codec packages; stored/deflate members still list/read |
