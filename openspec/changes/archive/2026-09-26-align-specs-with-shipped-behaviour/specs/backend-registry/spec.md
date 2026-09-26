## MODIFIED Requirements

### Requirement: Backends self-register at import time

The system SHALL register all known core and optional backends when `archivey`
imports. Optional library-backed backends (ISO, ZST, LZ4) and optional 7z writing
SHALL be imported behind `try/except ImportError` guards and registered
regardless of dependency availability. Missing dependencies make a format known
but unavailable, not unknown, and import MUST NOT fail.

Each backend SHALL declare dependency metadata as data. Availability SHALL be
derived centrally from the module-or-`None` sentinel idiom (`_optional("pycdlib")`
returns module or `None`), not per-backend booleans. 7z and RAR reading are
native and known; RAR data reads additionally require the external `unrar` binary
at read time. That binary SHALL NOT be counted in support: RAR reports FULL whether or
not `unrar` is on `PATH`, and a member read that needs it raises
`PackageNotInstalledError`.

```python
pycdlib = _optional("pycdlib")

class IsoReadBackend(ReadBackend):
    FORMATS = (ArchiveFormat.ISO,)
    EXTENSIONS = {".iso": ArchiveFormat.ISO}
    MAGIC = ((32769, b"CD001", ArchiveFormat.ISO),)
    OPTIONAL_DEPENDENCY = "pycdlib"

    def open_read(self, source, format, streaming, password, encoding, archive_name):
        assert pycdlib is not None
        ...

register_reader(IsoReadBackend)
```

The sentinel SHALL type-check cleanly under Pyrefly and ty because uses occur
behind `is not None` or equivalent registry-selection guarantees.

#### Scenario: import registration matrix

| Case | Expected |
| --- | --- |
| `import archivey` with no optional extras | Core formats plus native 7z/RAR register; supported formats show FULL/PARTIAL where applicable |
| `pycdlib` absent at import | No `ImportError`; ISO absent from supported formats but present in known formats with NONE support and install hint |

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
    required_source: StreamCapability = StreamCapability.SEEKABLE

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
- ZIP follows the same rule: FULL with every optional member-codec package (Deflate64,
  Zstd, PPMd) installed, PARTIAL when any is missing.
- Requirements a format needs only for some members or at read time — `cryptography`
  for encrypted members, the `unrar` binary for RAR member data — SHALL NOT lower
  support.
- By-design unsupported features such as 7z BCJ2 and unknown 7z method IDs SHALL
  not lower support; members using them raise `UnsupportedFeatureError`.

`list_supported_formats()` SHALL return FULL plus PARTIAL formats.
`list_known_formats()` SHALL return every known format including NONE.

`format_availability()` SHALL answer only for an `ArchiveFormat`. Any other argument —
a `StreamFormat`, `None`, or a value of an unrelated type — SHALL raise
`ArchiveyUsageError` per the boundary rule below, and MUST NOT produce a
`FormatAvailability`: returning one would put a value in its `format` field that
violates the type declared above, and a fabricated `NONE` with an empty `missing` is
indistinguishable from a legitimate unsupported verdict. The refusal SHALL key on the
argument's **type**, never on the shape of the verdict: `ArchiveFormat.UNKNOWN` answers
`NONE` with an empty `missing`, and that is a real answer.

`required_source` SHALL report **the weakest source shape the format can be read
from**, so that the split between formats readable from a pipe and formats that must
seek is queryable as data rather than discovered by catching
`StreamNotSeekableError`. It SHALL be derived from the backend's
`SUPPORTS_STREAMING_NON_SEEKABLE` declaration — the same fact `open_archive()`
enforces — and MUST NOT be declared separately per backend:

| `SUPPORTS_STREAMING_NON_SEEKABLE` | `required_source` | Formats |
| --- | --- | --- |
| `True` | `StreamCapability.FORWARD_ONLY` | TAR and its compressed combos, the single-file compressors |
| `False` | `StreamCapability.SEEKABLE` | ZIP, ISO, 7z, RAR, directory |

`required_source` SHALL be reported independently of `support`: a format whose
optional dependency is missing still answers the source-shape question. For a format
with no registered backend at all, `required_source` SHALL be `SEEKABLE` — the
conservative answer.

#### Scenario: format support matrix

| Case | Expected |
| --- | --- |
| 7z availability without the optional 7z packages | PARTIAL; missing names each absent package and `[recommended]`; LZMA2/bzip2/copy members still read |
| ZSTD availability before Python 3.14 without zstd backend | NONE with `backports.zstd` / `pip install archivey[recommended]` hint |
| GZIP availability | FULL; no missing components |
| 7z with the optional 7z packages installed | FULL even though BCJ2 still raises `UnsupportedFeatureError` |
| ZIP with every optional member codec installed | FULL; no missing components |
| ZIP missing deflate64 and/or zstd packages | PARTIAL; missing names absent codec packages; stored/deflate members still list/read |
| `format_availability(StreamFormat.ZSTD)` | `ArchiveyUsageError`; no `FormatAvailability` returned |
| `format_availability(ArchiveFormat.UNKNOWN)` | `NONE` with an empty `missing` — a hintless NONE is an answer, not a fabrication |

#### Scenario: required source matrix

| Case | Expected |
| --- | --- |
| `format_availability(TAR).required_source` | `FORWARD_ONLY` |
| `format_availability(TAR_GZ).required_source` | `FORWARD_ONLY` |
| `format_availability(GZ).required_source` | `FORWARD_ONLY` |
| `format_availability(ZIP \| ISO \| SEVEN_Z \| RAR \| FOLDER).required_source` | `SEEKABLE` |
| ISO queried without `pycdlib` | `support=NONE` **and** `required_source=SEEKABLE` — the answer does not depend on installability |
| `required_source <= reader.cost.stream_capability` for a format opened successfully from that source | `True` for every format/source pair the library accepts |
