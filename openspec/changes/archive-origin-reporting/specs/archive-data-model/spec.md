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
enum defined by `format-detection` — `NONE` / `EXECUTABLE` / `SCRIPT` / `UNKNOWN` — used here
with its meanings unchanged: `UNKNOWN` means *a prefix that matched no cue*, which always has
`payload_offset > 0`.

*Not established* SHALL be spelled as **absence**, not as an enum member: `None`.
Overloading `UNKNOWN` would give one member two meanings and make it disagree with
`FormatInfo`, which is the cross-surface inconsistency this change exists to avoid.

| state | `prefix_kind` | `payload_offset` |
| --- | --- | --- |
| archive begins at the open position | `NONE` | `0` |
| begins later, prefix cue recognised | `EXECUTABLE` / `SCRIPT` | `> 0` |
| begins later, prefix matched no cue | `UNKNOWN` | `> 0` |
| origin never established | `None` | `None` |

The invariants SHALL be:

- `prefix_kind is NONE` ⟺ `payload_offset == 0`
- `prefix_kind is None` ⟺ `payload_offset is None`
- `prefix_kind is None` SHALL mean *not established*, and SHALL NOT be read as "no prefix" —
  that is `NONE`

Neither absence SHALL be reported as `NONE` / `0`: a backend that opened a prefixed archive
without establishing where the payload began MUST NOT claim it started at byte zero. A format
that cannot carry a prefix SHALL report `NONE` and `0`.

Classifying a prefix is a pure function of its leading bytes — `MZ` / ELF / Mach-O →
`EXECUTABLE`, `#!` → `SCRIPT`, anything else → `UNKNOWN` — so a backend that knows the offset
can always establish the kind from one short read, and the two fields are established
together. `prefix_kind` SHALL NOT be inferred from `payload_offset > 0` without that read:
a `zipapp`, an executable JAR and a JPEG-with-appended-ZIP all have a non-zero offset and
none is an executable stub.

`payload_offset` SHALL be measured **from the start of `source` as handed to
`open_archive`** (after any stream start-position fix-up) — not relative to a view, slice,
or the position a parser was handed. A backend that resolves its origin inside a view
SHALL report the sum, so the value does not depend on which door the caller used.

These fields are open-time structural facts about the archive, not runtime diagnostics, and
both SHALL be populated identically whether the format was detected or supplied by the
caller (`archive-reading`).

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
| `zipapp` `.pyz`, either open path | `prefix_kind is SCRIPT` and `payload_offset` is the shebang length |
| JPEG + appended ZIP, either open path | `prefix_kind is UNKNOWN` — no cue matched; **not** `EXECUTABLE` |
| Empty ZIP behind a prefix, `format=ZIP` | `prefix_kind is None` and `payload_offset is None` |
| Any archive | `(prefix_kind is NONE) == (payload_offset == 0)` |
| Any archive | `(prefix_kind is None) == (payload_offset is None)` |
