# packaging-and-extras — stored encrypted RAR5 native read delta

> Full requirement as it will read after the change. The single-tool rule is unchanged;
> what moves is the set of member shapes that need an external tool at all.

## MODIFIED Requirements

### Requirement: RAR data uses RARLAB unrar only

The system SHALL treat RARLAB `unrar` or RARLAB `rar` as the supported external
decompressors for RAR member data. It MUST identify the binary on `PATH` as a
RARLAB build before use (`unrar` first, then `rar`) and MUST NOT implement a
fallback matrix to `unrar-free`, `unar`, `bsdtar`, `7z`, or other tools.
Member-data spawns SHALL use the `p` command only.

Member data that Archivey reads natively SHALL NOT count as a RARLAB dependency:
stored members, whether unencrypted or — for RAR5 with a parsed FILE encryption
record, a crypto backend and a password — encrypted, are served in process. This
narrows *when* the binary is needed and MUST NOT be read as removing the dependency
or as licensing a second external tool: every compressed member still requires RARLAB
`unrar` or `rar`, and the UnRAR licence constraint on the proprietary decompression
algorithms is untouched by a member that has nothing to decompress.

#### Scenario: single-tool matrix

| Case | Expected |
| --- | --- |
| RARLAB `unrar` 6.0+ on `PATH` | Used for compressed/encrypted member data |
| RARLAB `rar` 6.0+ on `PATH`, `unrar` missing | Used for compressed/encrypted member data |
| RARLAB `unrar` 6.0+ and RARLAB `rar` both on `PATH` | `unrar` is used |
| RARLAB `unrar`/`rar` older than 6.0, or a RARLAB banner with no parseable version | `PackageNotInstalledError` naming the floor and the version found; refused at identification |
| Only `unrar-free` / `unar` / `7z` on `PATH` | `PackageNotInstalledError` naming RARLAB `unrar` or `rar`; no silent fallback |
| Listing without data reads | Succeeds without invoking any external decompressor |
| Stored encrypted RAR5 member, `[recommended]` installed, no RARLAB binary on `PATH` | Data is returned; no `PackageNotInstalledError` |
| Stored encrypted RAR5 member, no crypto backend, RARLAB binary present | Falls back to the spawn; behaviour unchanged from today |
| Stored encrypted RAR5 member, neither a crypto backend nor a RARLAB binary | `PackageNotInstalledError` naming **both** routes (`archivey[recommended]` and RARLAB `unrar`/`rar`), since either one alone would have served the member |
