## MODIFIED Requirements

### Requirement: Use RARLAB unrar only for member data that needs it

The system SHALL read stored, uncompressed, unencrypted members directly as raw
bytes through the shared pass-through backend. All other member data SHALL be
read by invoking a system RARLAB decompressor: `unrar` if a usable binary is on
`PATH`, otherwise `rar`. A usable binary is identified by a RARLAB banner
(`Alexander Roshal` or `RARLAB`, plus a standalone `UNRAR` or `RAR` token that
does not match inside `UNRAR`) whose parsed major.minor is 6.0 or later.
If a decompressor is required and missing or incompatible, the system SHALL raise
`PackageNotInstalledError` naming RARLAB `unrar` or `rar`. Archivey MUST NOT
silently use `unrar-free`, `unar`, `bsdtar`, `7z`, or a degraded backend. The
spawn SHALL be the `p` (print to stdout) command only. This requirement applies
when `ArchiveyConfig.rar_decompressor` is `unrar` (the default); `unar` is
covered by `Read RAR member data with unar only when selected`.

#### Scenario: unrar dependency matrix

| Case | Expected |
| --- | --- |
| Stored member, `unrar`/`rar` missing | Raw bytes are returned without invoking either |
| Compressed member, both missing | `PackageNotInstalledError` names `unrar` or `rar` |
| PATH `unrar` is not RARLAB `unrar`, and no usable `rar` | `PackageNotInstalledError` names RARLAB `unrar` or `rar` |
| RARLAB `unrar` older than 6.0 and no usable `rar`, or a RARLAB banner with no parseable version | `PackageNotInstalledError` names the floor and the version found; refused at identification |
| RARLAB `rar` 6.0+ on `PATH`, `unrar` missing | Used for compressed/encrypted member data; spawn is `rar p` |
| RARLAB `unrar` 6.0+ and RARLAB `rar` both on `PATH` | `unrar` is used |
| Listing only, both missing | No data dependency is checked |

## ADDED Requirements

### Requirement: Read RAR member data with unar only when selected

When `ArchiveyConfig.rar_decompressor` is `unar`, the system SHALL read compressed
member data by invoking `unar` 1.10 or later, identified on `PATH` by its `unar -h`
banner with the same probe timeout and stat-keyed cache as RARLAB `unrar`. Stored,
unencrypted, unsplit members SHALL still be read directly. The system MUST NOT use
`unrar` in that mode, and MUST NOT use `unar` in any other mode; a missing or
unidentified `unar` SHALL raise `PackageNotInstalledError` naming `unar`.

The argv SHALL be `unar -o - -q -nr -k skip [-i] -- <absolute path> [index …]`:
members named by decimal entry index in parse order, never by stored name. The
system SHALL refuse with `UnsupportedFeatureError`, before spawning `unar`:

- a member that needs a password, and every member of a solid pass over an archive
  that has one (`unar` takes a password only on its command line);
- in a RAR5 solid archive, a member with data that follows an empty file, a
  directory or a link;
- a compressed member whose extract version is below 20 (RAR 1.5 algorithm);
- any member of a multi-volume set that has a prefix before the RAR.

A solid pass that includes a refused member SHALL name only the readable payload
members, so `unar` never decodes the refused one. A single archive with a prefix
SHALL be copied from the RAR's start before `unar` reads it. Every member read
through `unar` SHALL be checked against its declared size and stored digest,
because `unar` exits 0 on some failures.

#### Scenario: unar selection matrix

| Case | Expected |
| --- | --- |
| Default config, compressed member | `unrar` is spawned; `unar` is not |
| `rar_decompressor="unar"`, `unar` missing, `unrar` present | `PackageNotInstalledError` names `unar`; `unrar` is not used |
| `unar` selected, member name contains `*` | Read by index; no `rar_allow_glob_member_concatenation` needed |
| `unar` selected, encrypted member | `UnsupportedFeatureError` naming the password reason |
| `unar` selected, RAR5 solid, empty file first | Members with data after it are refused; listing is not |
| `unar` selected, member before the first empty entry in a RAR5 solid pass | Read correctly from a run that names only readable members |
| `unar` selected, RAR 1.5 compressed member | `UnsupportedFeatureError` |
| `unar` selected, single archive after a 4 KiB prefix | Read from a copy that starts at the RAR |
