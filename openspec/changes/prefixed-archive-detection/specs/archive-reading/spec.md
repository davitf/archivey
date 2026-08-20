## ADDED Requirements

### Requirement: An exhaustive prefix scan is available and off by default

Detection's forward scan is bounded by `SFX_MAX` and gated on a prefix cue, because reading
that much from every source a caller opens is not free. A caller who knows better — someone
holding a firmware image, a disk image, or a file with an unrecognised wrapper — SHALL be
able to ask for an unbounded scan through an explicit opt-in on `open_archive` and
`detect_format`.

The option SHALL default to off. When enabled it SHALL search the whole source for the same
validated container signatures the cued scan uses, and a hit SHALL be subject to the same
structural validation (`format-detection`) — the opt-in changes *how far* detection looks,
never *how much evidence* it demands. A hit found only this way SHALL report
`prefix_kind = UNKNOWN`.

Because the cost is unbounded in the size of the source, the system SHALL NOT enable this
implicitly — not as a retry after `FormatDetectionError`, and not because an extension
suggested a format that was not found.

#### Scenario: exhaustive scan matrix

| Case | Expected |
| --- | --- |
| Archive magic beyond `SFX_MAX`, option off | `FormatDetectionError`; the source is not read past the window |
| Same source, option on | Detected, `payload_offset` at the payload, `prefix_kind == UNKNOWN` |
| Option on, magic present but validation fails | No claim; the scan continues and then fails normally |
| Option on, plain archive at offset 0 | Found at tier 1 as usual; no scan performed |
| `FormatDetectionError` with the option off | SHALL NOT silently retry with it on |
