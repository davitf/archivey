## ADDED Requirements

## MODIFIED Requirements

### Requirement: Zero-dependency core

The system SHALL install with no third-party runtime dependencies when no extras are
requested. Bare `pip install archivey` MUST fully support every native or
stdlib-backed reader: ZIP, TAR including `tar.gz` / `tar.bz2` / `tar.xz` / `tar.Z`,
single-file GZ / BZ2 / XZ / Z (unix-compress), directories, and 7z reading for common
codecs (LZMA/LZMA2/BCJ/Delta/Deflate/BZip2/STORED) with CRC32 verification.

The system SHALL parse RAR metadata/listing natively in core with CRC32
verification. Reading RAR member data additionally requires an external RARLAB
`unrar` or `rar` system binary at runtime; no pip extra supplies that binary. RAR
members that carry only Blake2sp hashes are verified in core: BLAKE2sp is computed
natively on stdlib `hashlib` and needs no third-party package.

The build SHALL use `hatchling` and the distribution name `archivey`.

#### Scenario: core install matrix

| Case | Expected |
| --- | --- |
| `pip install archivey` with no extras | No third-party runtime packages installed |
| Core read of ZIP/TAR/GZ/BZ2/XZ/Z/directory/common-codec 7z | Fully functional |
| Core read of `.tar.Z` / bare `.Z` | Native LZW decode; no `uncompresspy` |
| Core RAR listing | Native metadata/listing works |
| Core RAR data read with no `unrar` or `rar` on `PATH` | Clear error says the external RARLAB `unrar` or `rar` tool is required |
| Core-only 7z write | Unavailable (writing not shipped); 7z reading still works |

### Requirement: RAR data uses RARLAB unrar only

The system SHALL treat RARLAB `unrar` or RARLAB `rar` as the supported external
decompressors for RAR member data. It MUST identify the binary on `PATH` as a
RARLAB build before use (`unrar` first, then `rar`) and MUST NOT implement a
fallback matrix to `unrar-free`, `unar`, `bsdtar`, `7z`, or other tools.
Member-data spawns SHALL use the `p` command only.

#### Scenario: single-tool matrix

| Case | Expected |
| --- | --- |
| RARLAB `unrar` 6.0+ on `PATH` | Used for compressed/encrypted member data |
| RARLAB `rar` 6.0+ on `PATH`, `unrar` missing | Used for compressed/encrypted member data |
| RARLAB `unrar` 6.0+ and RARLAB `rar` both on `PATH` | `unrar` is used |
| RARLAB `unrar`/`rar` older than 6.0, or a RARLAB banner with no parseable version | `PackageNotInstalledError` naming the floor and the version found; refused at identification |
| Only `unrar-free` / `unar` / `7z` on `PATH` | `PackageNotInstalledError` naming RARLAB `unrar` or `rar`; no silent fallback |
| Listing without data reads | Succeeds without invoking any external decompressor |
