## MODIFIED Requirements

### Requirement: ArchiveInfo carries open-time archive metadata

The system SHALL define frozen `ArchiveInfo` for metadata available immediately
after `open_archive()` without a full member scan:

```python
@dataclass(frozen=True)
class ArchiveInfo:
    format: ArchiveFormat
    format_version: str | None
    is_solid: bool
    member_count: int | None
    comment: str | None
    is_encrypted: bool
    is_multivolume: bool
    cost: CostReceipt
    prefix_kind: PrefixKind = PrefixKind.NONE
    payload_offset: int | None = 0
    extra: dict[str, Any] = field(default_factory=dict, compare=False)
```

`extra` keys SHALL be namespaced strings and excluded from equality.
`member_count` SHALL be `None` when computing it requires a full scan.

`prefix_kind` and `payload_offset` SHALL describe where the archive proper begins inside
the source, and SHALL be present on every archive regardless of format, so a caller may
read them without first testing whether the format can carry a prefix. `PrefixKind` is the
enum defined by `format-detection`; the two fields SHALL satisfy:

| state | `prefix_kind` | `payload_offset` |
| --- | --- | --- |
| archive begins at the open position | `NONE` | `0` |
| archive begins later, prefix classified | `EXECUTABLE` / `SCRIPT` / `OTHER_FORMAT` | `> 0` |
| archive begins later, prefix not classified | `UNKNOWN` | `> 0` |
| origin not established | `UNKNOWN` | `None` |

`payload_offset is None` SHALL mean *the origin was not established*, and SHALL NOT be
reported as `0`: a backend that opened a prefixed archive without learning where the
payload began (stdlib `zipfile` locating the central directory past a stub) MUST NOT claim
the archive started at byte zero. A format that cannot carry a prefix SHALL report `NONE`
and `0`.

These fields are open-time structural facts about the archive, not runtime diagnostics, and
SHALL be populated identically whether the format was detected or supplied by the caller
(`archive-reading`).

#### Scenario: archive info matrix

| Case | Expected |
| --- | --- |
| TAR archive opens without central directory | `ar.info.member_count is None` |
| ISO 9660 image richest namespace is Joliet | `ar.info.extra["iso.namespace"] == "joliet"` |

#### Scenario: payload origin matrix

| Case | Expected |
| --- | --- |
| Plain 7z / RAR / ZIP / TAR | `prefix_kind is NONE` and `payload_offset == 0` |
| SFX 7z at offset N, auto-detected | `prefix_kind is EXECUTABLE` and `payload_offset == N` |
| SFX 7z at offset N, `format=SEVEN_Z` | Identical `prefix_kind` and `payload_offset` to the auto-detected open |
| SFX RAR at offset N, either open path | `prefix_kind is EXECUTABLE` and `payload_offset == N` |
| Prefixed ZIP at offset N, auto-detected | `payload_offset == N` |
| Prefixed ZIP, `format=ZIP` (stdlib located the payload) | `prefix_kind is UNKNOWN` and `payload_offset is None` |
| Any archive | `(prefix_kind is NONE) == (payload_offset == 0)` |
