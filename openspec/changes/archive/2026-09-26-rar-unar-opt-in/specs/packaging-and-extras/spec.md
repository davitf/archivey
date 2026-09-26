## MODIFIED Requirements

### Requirement: RAR data uses RARLAB unrar only

The system SHALL treat RARLAB `unrar` or RARLAB `rar` as the supported external
decompressors for RAR member data by default. It MUST identify the binary on `PATH`
as a RARLAB build before use (`unrar` first, then `rar`) and MUST NOT implement a
fallback matrix to `unrar-free`, `unar`, `bsdtar`, `7z`, or other tools. The one
alternative is `unar`, used when `ArchiveyConfig.rar_decompressor` selects it, or
when it is `auto` and no RARLAB binary is found. The choice is made once when the
archive opens; a read is never retried with the other program.
Member-data spawns SHALL use the `p` command only.

#### Scenario: single-tool matrix

| Case | Expected |
| --- | --- |
| RARLAB `unrar` 6.0+ on `PATH` | Used for compressed/encrypted member data |
| RARLAB `rar` 6.0+ on `PATH`, `unrar` missing | Used for compressed/encrypted member data |
| RARLAB `unrar` 6.0+ and RARLAB `rar` both on `PATH` | `unrar` is used |
| RARLAB `unrar`/`rar` older than 6.0, or a RARLAB banner with no parseable version | `PackageNotInstalledError` naming the floor and the version found; refused at identification |
| Only `unrar-free` / `unar` / `7z` on `PATH`, default config | `PackageNotInstalledError` naming RARLAB `unrar` or `rar`; no silent fallback |
| `rar_decompressor="unar"`, `unar` 1.10+ on `PATH` | `unar` is used for compressed member data; `unrar` is not |
| `rar_decompressor="auto"`, RARLAB `unrar` and `unar` on `PATH` | `unrar` is used |
| `rar_decompressor="auto"`, only `unar` on `PATH` | `unar` is used |
| Listing without data reads | Succeeds without invoking any external decompressor |
