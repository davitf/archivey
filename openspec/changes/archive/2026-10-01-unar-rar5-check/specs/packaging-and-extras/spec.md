## MODIFIED Requirements

### Requirement: RAR data uses RARLAB unrar only

The system SHALL treat RARLAB `unrar` or RARLAB `rar` as the preferred external
decompressors for RAR member data. It MUST identify the binary on `PATH`
as a RARLAB build before use (`unrar` first, then `rar`) and MUST NOT implement a
fallback matrix to `unrar-free`, `unar`, `bsdtar`, `7z`, or other tools. The one
alternative is `unar`, used when `ArchiveyConfig.rar_decompressor` selects it, or
when it is `auto` (the default) and no RARLAB binary is found, provided it passes the
RAR5 check in `format-rar`. The choice is made once when the
archive opens; a read is never retried with the other program.
Member-data spawns SHALL use the `p` command only.

#### Scenario: single-tool matrix

| Case | Expected |
| --- | --- |
| RARLAB `unrar` 6.0+ on `PATH` | Used for compressed/encrypted member data |
| RARLAB `rar` 6.0+ on `PATH`, `unrar` missing | Used for compressed/encrypted member data |
| RARLAB `unrar` 6.0+ and RARLAB `rar` both on `PATH` | `unrar` is used |
| RARLAB `unrar`/`rar` older than 6.0, or a RARLAB banner with no parseable version | `PackageNotInstalledError` naming the floor and the version found; refused at identification |
| Only `unrar-free` / `7z` on `PATH`, default config | `PackageNotInstalledError` naming RARLAB `unrar` or `rar`; they are never used |
| Only a `unar` 1.10+ that passes the RAR5 check on `PATH`, default config | `unar` is used for compressed member data |
| Only a `unar` that fails the RAR5 check on `PATH` (Debian and Ubuntu packages before 1.10.8+ds1-10), default config | `PackageNotInstalledError` naming RARLAB `unrar` or `rar`, as with neither present |
| Only `unar` on `PATH`, `rar_decompressor="unrar"` | `PackageNotInstalledError` naming RARLAB `unrar` or `rar` |
| `rar_decompressor="unar"`, a `unar` 1.10+ that passes the RAR5 check on `PATH` | `unar` is used for compressed member data; `unrar` is not |
| `rar_decompressor="auto"`, RARLAB `unrar` and `unar` on `PATH` | `unrar` is used |
| `rar_decompressor="auto"`, only a `unar` that passes the RAR5 check on `PATH` | `unar` is used |
| Listing without data reads | Succeeds without invoking any external decompressor |
