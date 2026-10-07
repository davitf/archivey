## ADDED Requirements

### Requirement: Read RAR without any external program

When `ArchiveyConfig.rar_decompressor` is `none`, the system MUST NOT start `unrar`,
`rar` or `unar` for that reader: not to identify them at open, not to decode a
comment, and not to read member data. Opening and listing SHALL work as with any
other setting, including header-encrypted archives given the right password. A
stored member that is not encrypted, not part of a solid stream and not split across
volumes SHALL be read directly, as it is under every setting. Every other member read
SHALL raise `UnsupportedFeatureError` before any process starts or any source is
copied, naming why the member needs a program (compressed, encrypted, solid or split)
and the `unrar` setting that reads it. A compressed RAR 1.5/2.x old-style comment
SHALL be `None`. When the archive has a file member that this setting refuses,
`ar.cost.notes` SHALL say so at open.

#### Scenario: no external program matrix

| Case | Expected |
| --- | --- |
| Non-solid archive, stored plaintext members, path or stream source | Every member reads; no process starts; `ar.cost.notes` is empty |
| Compressed member | `UnsupportedFeatureError` naming "compressed"; no process starts |
| Encrypted member, stored or compressed | `UnsupportedFeatureError` naming "encrypted" |
| Stored member split across volumes | `UnsupportedFeatureError` naming "split across volumes" |
| Solid archive, `stream_members()` | Each member's read is refused on its own; no solid pass starts |
| Header-encrypted archive, right password | Lists; no process starts |
| Compressed RAR 1.5 archive comment | `None` |
