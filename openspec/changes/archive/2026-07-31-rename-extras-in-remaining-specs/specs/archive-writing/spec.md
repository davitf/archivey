## MODIFIED Requirements

### Requirement: CompressionSpec model and convenience constants

The system SHALL define `CompressionSpec` for writer compression choices. Its
`algo` field SHALL reuse `CompressionAlgorithm` from `archive-data-model` and be
nullable (`None` means backend auto-selects). Its `level` field SHALL accept either
a numeric value or a format-agnostic `CompressionLevel` enum.

```python
class CompressionLevel(Enum):
    STORE = "store"
    FAST = "fast"
    DEFAULT = "default"
    MAX = "max"

@dataclass
class CompressionSpec:
    algo: CompressionAlgorithm | None = None
    level: int | CompressionLevel = CompressionLevel.DEFAULT

CompressionSpec.STORED = CompressionSpec(algo=CompressionAlgorithm.STORED)
CompressionSpec.DEFLATE = CompressionSpec(algo=CompressionAlgorithm.DEFLATE, level=6)
CompressionSpec.DEFLATE_MAX = CompressionSpec(
    algo=CompressionAlgorithm.DEFLATE,
    level=CompressionLevel.MAX,
)
CompressionSpec.LZMA = CompressionSpec(
    algo=CompressionAlgorithm.LZMA2,
    level=CompressionLevel.DEFAULT,
)
```

Convenience constants SHALL be class attributes. `compression=None` at `create()` or
`add_*` SHALL be equivalent to `CompressionSpec(algo=None, level=DEFAULT)`.

| `algo` | `level` | Behavior |
| --- | --- | --- |
| `None` | `STORE` / `FAST` / `DEFAULT` / `MAX` | Backend chooses a format-appropriate available algorithm for the requested effort; `STORE` selects `STORED` |
| `None` | numeric `int` | Backend uses the format default algorithm at that numeric level, or the algorithm implied by the level |
| set | `STORE` | Resolves to `STORED`; emits `logging.WARNING` for the contradiction |
| set | `FAST` / `DEFAULT` / `MAX` | Uses that algorithm, mapping the symbolic level to the nearest concrete level |
| set | numeric `int` | Uses that algorithm at that level; out-of-range values raise `ValueError` and are not clamped |

When the caller names an explicit `algo` whose backend is unavailable or whose
target format cannot represent it, `create()` or the first `add_*` that would use
it SHALL fail fast with `PackageNotInstalledError` or `UnsupportedFeatureError`.
The system MUST NOT silently substitute a different algorithm or degrade to the
format default. With `algo=None`, the backend SHALL choose an available algorithm.

#### Scenario: compression-resolution matrix

| Case | Expected |
| --- | --- |
| Explicit `ZSTD` without the zstd backend | `PackageNotInstalledError`; no archive written; no fallback codec |
| `algo=None`, `level=MAX` for ZIP | Backend selects an appropriate available ZIP algorithm at maximum effort |
| `compression=None` or omitted | Treated as backend auto algorithm at default effort |
| `CompressionSpec.DEFLATE` | Entries use DEFLATE level 6 |
| Explicit `LZMA2` with `level=STORE` | Entry written uncompressed and warning emitted |
| Numeric level outside algorithm range | `ValueError`; no silent clamp |
