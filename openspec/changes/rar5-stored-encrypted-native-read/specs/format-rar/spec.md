# format-rar — stored encrypted RAR5 native read delta

> Each MODIFIED block below is the full requirement as it will read after the change.
> The added text is the RAR5-stored-encrypted carve-out; everything else is verbatim.

## MODIFIED Requirements

### Requirement: Declare RAR format properties

The RAR backend SHALL expose these properties:

| Property | Value |
| --- | --- |
| Read dependency (metadata) | None; native RAR 1.5–RAR5 header parser |
| Read dependency (data) | RARLAB `unrar` or `rar` binary on `PATH` (`unrar` preferred), except stored members served natively — see "Use RARLAB unrar only for member data that needs it" |
| Listing cost | O(1); headers parsed natively, no member-data decompression |
| Access cost | `SOLID` for solid archives; `DIRECT` otherwise |
| Supports write | No |
| Requires seek | Yes |

#### Scenario: format property matrix

| Case | Expected |
| --- | --- |
| Open non-header-encrypted RAR without `unrar`/`rar` | Listing and metadata still work through the native parser |
| Open from a non-seekable source | Open fails because RAR header parsing requires seek |
| Attempt to create/write RAR | `UnsupportedOperationError` |

### Requirement: Use RARLAB unrar only for member data that needs it

The system SHALL read stored, uncompressed, unencrypted members directly as raw
bytes through the shared pass-through backend. A stored **RAR5** member whose data is
encrypted SHALL also be read directly, by decrypting that byte range in process with
AES-256-CBC from the member's own FILE encryption record, and SHALL NOT invoke a
RARLAB binary. That native path applies only when every one of the following holds;
otherwise the member falls back to the RARLAB spawn below, unchanged:

| Condition | Why |
| --- | --- |
| RAR5 (a parsed FILE encryption record with salt and IV) | RAR4's `LHD` salt is not parsed; RAR4 stays on `unrar` |
| Stored (M0), not solid, not split, not volume-spanning | The existing direct-read guards |
| A crypto backend is available | No `cryptography`, no in-process AES |
| A password is available | Nothing to derive a key from |

The plaintext length SHALL come from `file_size`, not from the encrypted byte range:
RAR5 pads the ciphertext to the 16-byte CBC boundary and the padding is not member data.
Where the FILE record carries a 12-byte PswCheck, a provably wrong password SHALL raise
`EncryptionError` before any plaintext is produced, never truncated or garbage bytes.
A natively decrypted member SHALL be digest-verified exactly as it is today — through the
RAR5 tweaked-checksum path — so the direct read is not less verified than the spawn it
replaces.

All other member data SHALL be
read by invoking a system RARLAB decompressor: `unrar` if a usable binary is on
`PATH`, otherwise `rar`. A usable binary is identified by a RARLAB banner
(`Alexander Roshal` or `RARLAB`, plus a standalone `UNRAR` or `RAR` token that
does not match inside `UNRAR`) whose parsed major.minor is 6.0 or later.
If a decompressor is required and missing or incompatible, the system SHALL raise
`PackageNotInstalledError` naming RARLAB `unrar` or `rar`. Archivey MUST NOT
silently use `unrar-free`, `unar`, `bsdtar`, `7z`, or a degraded backend. The
spawn SHALL be the `p` (print to stdout) command only.

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

#### Scenario: stored encrypted RAR5 matrix

| Case | Expected |
| --- | --- |
| Stored encrypted RAR5 member, correct password, no `unrar`/`rar` on `PATH` | Plaintext returned natively; no spawn, no `PackageNotInstalledError` |
| Same member, bytes compared against `unrar p` | Byte-for-byte equal, including payload lengths that are not multiples of 16 |
| Same member, `seek()` mid-stream then read | Served from the CBC restart; no respawn, no whole-member replay |
| Stored encrypted RAR5 member, wrong password, PswCheck present | `EncryptionError`; no plaintext handed to the caller |
| Stored encrypted RAR5 member, no crypto backend installed | Falls back to the RARLAB spawn (behaviour unchanged from today) |
| Stored encrypted **RAR4** member | RARLAB spawn, as today — the RAR4 salt is not parsed |
| Compressed encrypted RAR5 member | RARLAB spawn, as today |
| Stored encrypted member in a solid, split, or volume-spanning archive | RARLAB spawn, as today |
| Stored encrypted RAR5 member with a tweaked CRC32/BLAKE2sp | Digest verified through the `ConvertHashToMAC` transform, as for the spawned read |

### Requirement: Constrain unrar argv by call site

The system SHALL invoke RARLAB `unrar` with member path arguments only as follows:

| Call site | Member path args after the archive |
| --- | --- |
| Solid `stream_members()` / solid `_iter_with_data` | none (unnamed `unrar p -inul <archive>`) |
| `_open_member` for a FILE member | exactly one archive-relative member path |
| Stored M0 unencrypted member | `unrar` not invoked |
| Stored M0 RAR5 member encrypted with a parsed FILE encryption record, crypto backend and password available | `unrar` not invoked |

The system MUST NOT pass multiple member paths, globs, or `@listfile` filters in this
capability’s initial implementation. Hardlink / file-copy members are never named on the
`unrar` command line; the shared link-following layer opens the target FILE instead.

#### Scenario: unrar argv matrix

| Case | Expected |
| --- | --- |
| Solid full or filtered `stream_members()` | One `unrar p` with no member path args |
| Nonsolid `open()` / lazy stream of a FILE | `unrar p … <archive> <member>` |
| `open()` on hardlink / `FILE_COPY` | `unrar` receives the target FILE path only (after link follow), or equivalent target open |
| Symlink member | No `unrar` data read for the link payload |
| Nonsolid `open()` of a natively decrypted stored RAR5 member | No `unrar` invocation at all |

### Requirement: Serve random access and extraction with bounded explicit temp use

The system SHALL serve non-solid random reads by invoking `unrar` for the target
member **with that member's path as the sole path argument**, doing O(member_size)
data work. For solid random reads, the system SHALL decode from archive start to
the target member (named `unrar p … <member>`) or extract once with `unrar x`
into an explicitly managed temporary directory and serve later reads from disk;
that directory is cleaned up on reader close. `extract_all()` MAY use one
`unrar x` to a temporary directory. Any temp materialization SHALL be a declared
RAR strategy, not an implicit in-memory buffer. When the archive is opened from a
non-path stream source, `ar.cost.notes` SHALL include a human-readable disk-copy
caveat **at open** (path sources SHALL NOT): a single stream source SHALL warn
that reading **a member that requires the RARLAB spawn** will copy the whole archive to
disk; ordered
stream volumes SHALL state that volumes were copied at open. The note is a
static open-time caveat, not an occurrence log:
it SHALL be present even if only directly-read members are read, and SHALL NOT appear
after materialization if it was absent at open. Mixed-password
nonsolid archives MUST NOT demultiplex one unnamed `unrar p` ALL pipe against the
full member list (wrong-password members are omitted from stdout and would
desynchronize sizes).

The caveat's wording SHALL track the set in "Use RARLAB unrar only for member data that
needs it" rather than naming compression: a stored **encrypted** member triggers the copy
too, and this change removes exactly the RAR5 half of that set. Saying "compressed" would
leave the caveat understated for RAR4 stored encrypted members, which keep spawning.

#### Scenario: random/extract matrix

| Case | Expected |
| --- | --- |
| Random `open()` in non-solid RAR | `unrar p … <archive> <member>`; work is O(member_size) |
| Repeated random opens in solid RAR | Backend may use one tempdir extraction and remove it on close |
| `extract_all()` | Backend may use one-shot `unrar x` |
| Mixed-password nonsolid stream/open | Per-member named `unrar` (or equivalent); no ALL-pipe demux |
| Single non-path stream, at open | `ar.cost.notes` warns a spawned read will copy to disk |
| Ordered stream volumes, at open | `ar.cost.notes` states volumes were copied at open |
| Path source | `ar.cost.notes` has no disk-copy caveat |
| Non-path stream, RAR5 stored encrypted member read natively | No archive copy; the open-time caveat is still present (static, not an occurrence log) |
| Non-path stream, RAR4 stored encrypted member | Spawns, so the copy happens — which is why the caveat cannot say "compressed" |

### Requirement: Decrypt RAR5 header-encrypted archives natively

The system SHALL decrypt RAR5 header-encrypted archives through the optional
crypto backend when a valid password is supplied. The native parser derives the
AES key and decrypts headers itself; `unrar` is not required for listing, and for
member data it is required only where the direct-read path above does not apply.
Header-encrypted listing without a
password SHALL raise `EncryptionError`; with a password but no `cryptography`
backend (`[recommended]`), it SHALL raise `PackageNotInstalledError`. Any encrypted RAR
SHALL set `ArchiveInfo.is_encrypted` to `True`.

#### Scenario: header encryption matrix

| Case | Expected |
| --- | --- |
| Header-encrypted RAR5, no password | `EncryptionError` |
| Header-encrypted RAR5, password but no crypto backend | `PackageNotInstalledError` |
| Header-encrypted RAR5, valid password + crypto | Headers decrypt natively; members list; `is_encrypted` true |
| Read a **compressed** member from that archive | `unrar` is still required |
| Read a **stored** member from that archive, its FILE encryption record parsed | Served natively; no spawn |
