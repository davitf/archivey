# backend-registry — wrong-typed `format=` argument delta

## MODIFIED Requirements

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
- ZIP SHALL remain PARTIAL until Phase 6 routes member decompression through the
  shared codec layer, even if all optional member-codec packages are installed.
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
| ZIP with every optional member codec installed | PARTIAL with empty missing list until Phase 6 |
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

## ADDED Requirements

### Requirement: A format argument outside its declared type is a usage error

Four public functions take a format argument. Each SHALL reject a value outside the
types its own signature declares, raising `ArchiveyUsageError` — which sits outside
`ArchiveyError` (`error-handling`), so `except ArchiveyError` cannot swallow a caller
bug — **before** resolving, peeking or reading the source:

| Call | Accepts | Anything else |
| --- | --- | --- |
| `format_availability(format)` | `ArchiveFormat` | `ArchiveyUsageError` |
| `open_archive(source, format=…)` | `ArchiveFormat`, `None` (auto-detect) | `ArchiveyUsageError` |
| `extract(source, dest, format=…)` | `ArchiveFormat`, `None` (auto-detect) | `ArchiveyUsageError` |
| `open_stream(source, format=…)` | `StreamFormat`, raw-stream `ArchiveFormat`, `None` | `ArchiveyUsageError` |

`open_stream`'s wider argument is by design, not an inconsistency to remove: a raw
compressed stream has no container, so the codec alone identifies it. A container
`ArchiveFormat` there remains a usage error for the separate reason it already was.

The rejection SHALL be a refusal, never a substitute answer: the call MUST NOT fall
back to auto-detection, return a fabricated record, or let an internal `AttributeError`
reach the caller.

The message SHALL name **what was passed** and **what was expected**. For a
`StreamFormat` passed where an `ArchiveFormat` was required — the likeliest mistake,
since `open_stream` accepts one — it SHALL also name the predefined `ArchiveFormat`
pairs built on that codec, so one message ends the mistake:

```
format_availability() takes an ArchiveFormat, but got StreamFormat.ZSTD. A StreamFormat
is only the codec half of an ArchiveFormat's (container, stream) pair, so pass the pair
instead: ArchiveFormat.ZST (a raw .zst stream) or ArchiveFormat.TAR_ZST (a tar
compressed with it).
```

Those names SHALL be derived from the predefined `ArchiveFormat` instances rather than
a separate table, so a codec added later is named here without a second edit.

#### Scenario: wrong-typed format argument matrix

| Case | Expected |
| --- | --- |
| `format_availability(StreamFormat.ZSTD)` | `ArchiveyUsageError` naming `StreamFormat.ZSTD`, `ArchiveFormat.ZST` and `ArchiveFormat.TAR_ZST` |
| `format_availability(None)` | `ArchiveyUsageError` — the query has no auto-detect form |
| `open_archive(path, format=StreamFormat.ZSTD)` | `ArchiveyUsageError`, not `AttributeError: 'StreamFormat' object has no attribute 'container'` |
| `extract(path, dest, format=StreamFormat.ZSTD)` | `ArchiveyUsageError`; nothing written to `dest`, source never read |
| `open_stream(src, format="zst")` | `ArchiveyUsageError`; the source is not read and detection does not run |
| `open_stream(src, format=StreamFormat.GZIP \| ArchiveFormat.GZ \| None)` | Opens as before |
| `open_archive(path, format=ArchiveFormat.ZIP \| None)` | Opens as before |
| `except ArchiveyError` around any of the refusals | Does not catch it |
