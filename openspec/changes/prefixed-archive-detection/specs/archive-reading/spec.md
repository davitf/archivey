## ADDED Requirements

### Requirement: An exhaustive prefix scan is available and off by default

Detection's forward scan is bounded by `SFX_MAX` and gated on a prefix cue, because reading
that much from every source a caller opens is not free. A caller who knows better — someone
holding a firmware image, a disk image, or a file with an unrecognised wrapper — SHALL be
able to ask for an unbounded scan.

The opt-in SHALL be the `ArchiveyConfig` field `exhaustive_prefix_scan: bool = False`,
specified in `format-detection`, and SHALL NOT be a keyword argument on `open_archive`.
`detect_format` accepts no per-call operational keywords, so a flag placed on
`open_archive` alone could not be expressed on `detect_format`; a config field serves both
through the `config=` channel they already share. It joins `strict_archive_eof` as a
cost-bearing behaviour flag rather than a tuning constant.

Because it arrives through `config=`, it SHALL follow the existing rules for configuration
that a given call cannot act on: a source that is already a plain archive at offset 0 is
matched at tier 1 and the flag simply never applies, which is not an error and SHALL NOT be
reported as an unused argument.

The option SHALL default to off. When enabled it SHALL search the whole source for the same
validated container signatures the cued scan uses, and a hit SHALL be subject to the same
structural validation (`format-detection`) — the opt-in changes *how far* detection looks,
never *how much evidence* it demands. A hit found only this way SHALL report
`prefix_kind = UNKNOWN` and `detected_by = "exhaustive_scan"`.

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
