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
    prefix_kind: PrefixKind | None = PrefixKind.NONE
    payload_offset: int | None = 0
    extra: dict[str, Any] = field(default_factory=dict, compare=False)
```

`extra` keys SHALL be namespaced strings and excluded from equality.
`member_count` SHALL be `None` when computing it requires a full scan.

`prefix_kind` and `payload_offset` SHALL describe where the archive proper begins inside
the source, and SHALL be present on every archive regardless of format, so a caller may
read them without first testing whether the format can carry a prefix. `PrefixKind` is the
enum defined by `format-detection`, **with its meanings unchanged**: in particular
`UNKNOWN` means *a prefix that matched no cue*, which always has `payload_offset > 0`.

*Not established* SHALL be spelled as **absence**, not as an enum member: `None`.
Overloading `UNKNOWN` would give one member two meanings and make it disagree with
`FormatInfo`, which is the cross-surface inconsistency this change exists to avoid.

**The two fields are established independently.** Where the payload starts and what
precedes it are answered by different work: the offset falls out of opening the archive,
while the kind requires inspecting the prefix, which only detection does. A caller MUST
therefore test them separately:

| state | `prefix_kind` | `payload_offset` |
| --- | --- | --- |
| archive begins at the open position | `NONE` | `0` |
| begins later, prefix classified by detection | `EXECUTABLE` / `SCRIPT` / `OTHER_FORMAT` | `> 0` |
| begins later, prefix matched no cue | `UNKNOWN` | `> 0` |
| begins later, kind never established | `None` | `> 0` |
| origin never established | `None` | `None` |

The invariants SHALL be:

- `prefix_kind is NONE` ⟺ `payload_offset == 0`
- `payload_offset is None` ⟹ `prefix_kind is None` (the converse does **not** hold)
- `prefix_kind is None` SHALL mean *the kind was not established*, and SHALL NOT be read as
  "no prefix" — that is `NONE`

Neither absence SHALL be reported as `NONE` / `0`: a backend that opened a prefixed archive
without establishing where the payload began MUST NOT claim it started at byte zero, and one
that did not inspect the prefix MUST NOT claim there was none. A format that cannot carry a
prefix SHALL report `NONE` and `0`.

`payload_offset` SHALL be measured **from the start of `source` as handed to
`open_archive`** (after any stream start-position fix-up) — not relative to a view, slice,
or the position a parser was handed. A backend that resolves its origin inside a view
SHALL report the sum, so the value does not depend on which door the caller used.

These fields are open-time structural facts about the archive, not runtime diagnostics.
`payload_offset` SHALL be populated identically whether the format was detected or supplied
by the caller; `prefix_kind` SHALL be populated where the prefix was classified, which today
means the detected path (`archive-reading`).

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
| SFX 7z at offset N, `format=SEVEN_Z` | `payload_offset == N` (same as auto-detect); `prefix_kind is None` — nothing classified the prefix |
| SFX RAR at offset N, auto-detected | `prefix_kind is EXECUTABLE` and `payload_offset == N` |
| Prefixed ZIP at offset N, auto-detected | `payload_offset == N` |
| Prefixed ZIP at offset N, `format=ZIP` | `payload_offset == N`; `prefix_kind is None` |
| Empty ZIP behind a prefix, `format=ZIP` | `prefix_kind is None` and `payload_offset is None` |
| Any archive | `(prefix_kind is NONE) == (payload_offset == 0)` |
| Any archive | `payload_offset is None` implies `prefix_kind is None`; the converse does not hold |
