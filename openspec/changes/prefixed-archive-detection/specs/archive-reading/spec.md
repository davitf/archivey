## MODIFIED Requirements

### Requirement: Opening an archive for reading

The system SHALL expose:

```python
archivey.open_archive(
    source: str | Path | BinaryIO | Sequence[str | Path | BinaryIO],
    *,
    format: ArchiveFormat | None = None,
    streaming: bool = False,
    seekable_members: bool = False,
    concurrent_members: bool = False,
    password: PasswordInput = None,
    encoding: str | None = None,
    config: ArchiveyConfig | None = None,
    budget: DetectionBudget | DetectionBudgetPreset | None = None,
) -> ArchiveReader
```

`source`, multi-volume ordering, `streaming`, password candidates/providers,
encoding, configuration precedence, and backend selection retain their existing
contracts. `format=None` auto-detects; an explicit format bypasses detection.
`budget=None` selects `BALANCED_BUDGET`. The budget types live in
`archivey.detection_cost` until `detection-result-surface` freezes the root surface;
this change threads the argument, it does not re-export the types.

**An explicit argument the resolved backend cannot act on is handled by its
*intent*, and the rule SHALL be applied to every such argument:**

> **Refuse** when the argument is an **assertion about this archive**. **Permit, and
> record a diagnostic**, when it is a **resource offered for use if needed.**
> **Permit silently** when it is a resource whose consuming stage the caller
> explicitly skipped — the caller caused the skip, so a diagnostic would be noise
> they cannot act on.

| Argument | Intent | Behaviour when the backend cannot act on it |
| --- | --- | --- |
| `format=` | assertion — "I claim this is a ZIP" | refuse when it cannot hold (see the directory rule below) |
| `password=` | resource — a keyring | permit in **every** form; `PASSWORD_ARGUMENT_UNUSED` |
| `encoding=` | resource — a hint for name decoding | permit; `ENCODING_ARGUMENT_UNUSED` |
| `budget=` | resource — a detection spend cap | unused when `format=` bypasses detection: permit silently (third case above). Detection never ran, so the bound was never consulted. Not an assertion about the archive. No `BUDGET_ARGUMENT_UNUSED`. |

`password=` on a format with no encryption SHALL NOT raise, in any of its forms — a
single value, an ordered sequence, and a provider callable SHALL behave identically
(accepted, never consulted, one diagnostic). A *wrong* password on an *encrypted*
archive is unaffected and still raises. Each backend SHALL declare whether it consumes
`encoding` (`ReadBackend.USES_ENCODING`) the same way it declares
`ReadBackend.SUPPORTS_PASSWORD`, so the check is central rather than per-backend
silence.

A **directory path** resolves to `ArchiveFormat.DIRECTORY`. An explicit `format=`
naming anything else SHALL raise `ArchiveyUsageError` rather than being discarded:
silently overruling it returns a reader over the directory tree to a caller who
asserted a different format, so every read downstream succeeds on the wrong data.
`format=ArchiveFormat.DIRECTORY` and `format=None` both remain valid. This is the
assertion half of the rule above, not a special case.

**Diagnostics at open (observable):** On success, advisory events from automatic
detection (if any) appear in this reader's cumulative `diagnostics` for its
lifetime and are not duplicated. Explicit `format=` skips detection, so open
adds no detection diagnostics. Unused-argument diagnostics are emitted before the
reader is returned, so they are readable without listing anything — except a
resource whose consuming stage the caller skipped (`budget=` when `format=` is
set; see the third case of the intent rule). If open raises, no reader is returned.

Handoff mechanics (one shared collector/budget, no copy/re-seed): see
`format-detection` and `diagnostics`.

#### Scenario: open matrix

| Case | Expected |
| --- | --- |
| Auto-detect succeeds | Detection events visible on `reader.diagnostics`; not duplicated |
| `format=ArchiveFormat.ZIP` succeeds | No detection diagnostics from open |
| Open raises | No reader returned |
| `password="secret"` | Returned reader uses that password for encrypted members |
| `password=` any form, format with no encryption | Opens; `PASSWORD_ARGUMENT_UNUSED`; no raise |
| `encoding=` on a backend that decodes names another way | Opens; `ENCODING_ARGUMENT_UNUSED`; names unchanged |
| Directory path, no `format=` | Opens as `DIRECTORY` |
| Directory path, `format=ArchiveFormat.DIRECTORY` | Opens as `DIRECTORY` |
| Directory path, `format=ArchiveFormat.ZIP` | `ArchiveyUsageError`, naming the path and the requested format |
| `format=ZIP` plus a non-default `budget=` | Opens; budget is a silent no-op (detection skipped); no unused-argument diagnostic |
| `format=None` plus a budget with `max_scan_bytes` past `SFX_MAX` | Detection uses that bound; exhaustive scan may fire |

## ADDED Requirements

### Requirement: An exhaustive prefix scan is available and off by default


Detection's forward scan is bounded by `SFX_MAX` and gated on a prefix cue, because reading
that much from every source a caller opens is not free. A caller who knows better — someone
holding a firmware image, a disk image, or a file with an unrecognised wrapper — SHALL be
able to ask for an unbounded scan.

The opt-in SHALL be a `DetectionBudget` whose `max_scan_bytes` exceeds `SFX_MAX`, passed to
`detect_format(..., budget=)` and threaded through `open_archive` the same way (see
*Opening an archive for reading* — that signature is the freeze surface; this requirement
does not restate it). It SHALL NOT be an `ArchiveyConfig` field: `#273` already gave
detection a cost-control channel, and a second flag would be two knobs for one decision.
A source already matched at offset 0 never consults the scan bound, which is not an error.

No shipped preset expresses a larger scan bound: `THOROUGH.max_scan_bytes` stays at
`SFX_MAX`, same as `BALANCED`. Raising it would make every `THOROUGH` caller scan the
whole source, which is a different product decision from enabling the ZIP tail.
Exhaustive scan is a hand-built or `replace()`d budget until
`detection-result-surface` freezes how callers spell one.

The default `BALANCED` budget SHALL leave the scan at `SFX_MAX`. When a larger bound is
set, detection SHALL search that far for the same validated container signatures the cued
scan uses — the opt-in changes *how far* detection looks, never *how much evidence* it
demands. A hit found only this way SHALL report `prefix_kind = UNKNOWN` and
`detected_by = "exhaustive_scan"`.

Because the cost is unbounded in the size of the source, the system SHALL NOT enable this
implicitly — not as a retry after `FormatDetectionError`, and not because an extension
suggested a format that was not found.

#### Scenario: exhaustive scan matrix

| Case | Expected |
| --- | --- |
| Archive magic beyond `SFX_MAX`, default budget | `FormatDetectionError`; the source is not read past the window |
| Same source, budget with `max_scan_bytes` past the window | Detected, `payload_offset` at the payload, `prefix_kind == UNKNOWN` |
| Larger bound, magic present but validation fails | No claim; the scan continues and then fails normally |
| Larger bound, plain archive at offset 0 | Found at tier 1 as usual; no scan performed |
| `FormatDetectionError` under `BALANCED` | SHALL NOT silently retry with a larger scan bound |
