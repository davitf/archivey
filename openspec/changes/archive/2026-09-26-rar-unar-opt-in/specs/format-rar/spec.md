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
when `ArchiveyConfig.rar_decompressor` is `unrar` (the default), or `auto` with a
usable RARLAB binary on `PATH`; `unar` is covered by `Read RAR member data with unar only when selected`.

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
| Listing only, both missing, archive has a compressed RAR 1.5/2.x comment | The comment is `None`; nothing else depends on a data program |

### Requirement: Serve random access and extraction with bounded explicit temp use

The system SHALL serve non-solid random reads by invoking `unrar` for the target
member **with that member's path as the sole path argument**, doing
O(member_size) data work. For solid random reads, the system SHALL decode from
archive start to the target member (named `unrar p … <member>`). Each such read
is its own decode: the reader SHALL NOT amortize repeated solid reads by
extracting members into a temporary directory and serving later reads from disk.
`extract_all()` SHALL be served by the same `stream_members()` pass as any other
caller, plus a second `stream_members()` pass when a selected hardlink's source
was excluded and has to be re-read; on a solid archive each pass is one unnamed
`unrar p` addressed at the whole archive, decoding only as far as the last
member it needs. Which members a pass names on the `unrar` command line — and
which need no spawn at all — is governed by `Constrain unrar argv by call site`.
Any temp materialization SHALL be a declared RAR strategy, not an implicit
in-memory buffer; the only one the reader implements is copying a non-path
archive *source* to disk so `unrar` can seek it, the deferred small-member
optimization being the other strategy this capability declares.

A non-path stream source SHALL NOT be copied to disk at open. Both stream shapes —
a single stream and an ordered set of stream volumes — SHALL defer the copy to the
first member read that `unrar` has to serve, and a caller that only lists SHALL
write nothing. An `open()` the reader refuses before spawning `unrar` — a name it
cannot address through an include mask, or one whose mask would pull in earlier
members — is not such a read and SHALL write nothing either. Listing SHALL be served from the source the caller supplied; for a
volume set the reader SHALL read each volume as its own bounded view over that
source rather than reopening or copying it.

When the copy does happen for a volume set it SHALL write the whole set, because
`unrar` resolves sibling volumes by name. The copies SHALL be named in the set's own
scheme: `name.partN.rar`, or `name.rar`, `name.r00`, … for a RAR 1.5-2.x set whose
main header lacks the new-numbering flag, because `unar` looks for the next volume
only under that scheme.

When the archive is opened from a
non-path stream source, `ar.cost.notes` SHALL include a human-readable disk-copy
caveat **at open** (path sources SHALL NOT, except the prefixed file that
`Read RAR member data with unar only when selected` copies): a single stream source SHALL warn
that reading a compressed member will copy the whole archive to disk; ordered
stream volumes SHALL warn that reading a compressed member will copy every volume
to a temp directory. The note is a
static open-time caveat, not an occurrence log:
it SHALL be present even if only stored members are read, and SHALL NOT appear
after materialization if it was absent at open. Mixed-password
nonsolid archives MUST NOT demultiplex one unnamed `unrar p` ALL pipe against the
full member list (wrong-password members are omitted from stdout and would
desynchronize sizes).

#### Scenario: random/extract matrix

| Case | Expected |
| --- | --- |
| Random `open()` in non-solid RAR | `unrar p … <archive> <member>`; work is O(member_size) |
| Repeated random opens in solid RAR | Each open is its own `unrar p` decode from archive start; no tempdir cache, and the re-decode is reported as `RewindWarning.min_redecode_bytes` |
| `extract_all()` | The same `stream_members()` pass as any other caller, plus a second pass when a selected hardlink's source was excluded and must be re-read; no `unrar x` |
| Mixed-password nonsolid stream/open | Per-member named `unrar` (or equivalent); no ALL-pipe demux |
| Single non-path stream, at open | `ar.cost.notes` warns a compressed read will copy to disk; nothing is written yet |
| Ordered stream volumes, at open | `ar.cost.notes` warns a compressed read will copy every volume; nothing is written yet |
| Ordered stream volumes, listing only | No temp directory is created |
| Solid `stream_members()` pass, no member read | Nothing is written, even from a stream source |
| Ordered stream volumes, first compressed read | The whole set is written once; later reads reuse it; close removes it |
| Stream source, `open()` refused before any spawn | Nothing is written; the refusal raises without materializing |
| Path source | `ar.cost.notes` has no disk-copy caveat (under `unrar`) |

## ADDED Requirements

### Requirement: Read RAR member data with unar only when selected

When `ArchiveyConfig.rar_decompressor` is `unar`, or `auto` with no usable RARLAB
`unrar` or `rar` on `PATH`, the system SHALL read compressed
member data by invoking `unar` 1.10 or later, identified on `PATH` by its `unar -h`
banner with the same probe timeout and stat-keyed cache as RARLAB `unrar`. Stored,
unencrypted, unsplit members SHALL still be read directly. The system MUST NOT use
`unrar` in that mode, and MUST NOT use `unar` in any other mode; a missing or
unidentified `unar` SHALL raise `PackageNotInstalledError` naming `unar`. `auto` SHALL
choose once per reader, when the archive opens; a read `unar` refuses MUST NOT be
retried with `unrar`, and with neither program present `auto` SHALL raise the
`PackageNotInstalledError` that names RARLAB `unrar` or `rar`.

The argv SHALL be
`unar -o - -q -nr -k skip [-p <password>] [-i] -- <absolute path> [index …]`:
members named by decimal entry index in parse order, never by stored name. The
password is on the command line because `unar` takes it nowhere else, so other local
users can read it in the process list; the documentation SHALL say so. The native
RAR5 password check SHALL reject a wrong password before `unar` runs where the archive
stores one; otherwise, when `unar` produces no data for a non-empty encrypted member,
the read SHALL raise `EncryptionError`. The
system SHALL refuse with `UnsupportedFeatureError`, before spawning `unar`:

- an encrypted RAR 2.x-4.x member, and every member of a solid pass over such an
  archive (`unar` 1.10 returns no data for it, and exits 0, even with the right
  password);
- a member or solid pass that needs a password that is not ASCII, or contains NUL
  (`unar` 1.10 does not decrypt with it);
- in a RAR5 solid archive, a member with data that follows an empty file, a
  directory or a link;
- a compressed member whose extract version is below 20 (RAR 1.5 algorithm);
- any member of a multi-volume set that has a prefix before the RAR.

A solid pass that includes a refused member SHALL name only the readable payload
members, so `unar` never decodes the refused one, and SHALL name at most 4000 of
them to stay inside `ARG_MAX`. A readable member past the 4000th SHALL be refused in
that pass with `UnsupportedFeatureError`; opening it on its own is not affected. A
single archive with a prefix SHALL be copied from the RAR's start before `unar`
reads it, and `ar.cost.notes` SHALL say so at open, for a path source too. Every member read through `unar` SHALL be checked against its declared
size and stored digest, because `unar` exits 0 on some failures.

A compressed RAR 1.5/2.x old-style comment SHALL be decoded by the selected
program, so with `unar` selected `unar` decodes it. The decoded text SHALL be used
only when its stored CRC16 matches; otherwise, or when the selected program is
missing, the comment SHALL be `None`, as it is with `unrar`.

#### Scenario: unar selection matrix

| Case | Expected |
| --- | --- |
| Default config, compressed member | `unrar` is spawned; `unar` is not |
| `rar_decompressor="unar"`, `unar` missing, `unrar` present | `PackageNotInstalledError` names `unar`; `unrar` is not used |
| `rar_decompressor="auto"`, RARLAB `unrar` present | `unrar` is spawned; `unar` is not |
| `rar_decompressor="auto"`, only `unar` present | `unar` is spawned |
| `rar_decompressor="auto"`, neither present | `PackageNotInstalledError` names RARLAB `unrar` or `rar` |
| `unar` selected, member name contains `*` | Read by index; no `rar_allow_glob_member_concatenation` needed |
| `unar` selected, encrypted RAR5 member, right password | Read correctly; the password is passed with `-p` |
| `unar` selected, encrypted RAR5 member, wrong password | `EncryptionError` |
| `unar` selected, encrypted RAR 2.x-4.x member | `UnsupportedFeatureError` naming the RAR 2.x-4.x reason |
| `unar` selected, non-ASCII password | `UnsupportedFeatureError` naming the password reason |
| `unar` selected, RAR5 solid, empty file first | Members with data after it are refused; listing is not |
| `unar` selected, member before the first empty entry in a RAR5 solid pass | Read correctly from a run that names only readable members |
| `unar` selected, RAR 1.5 compressed member | `UnsupportedFeatureError` |
| `unar` selected, single archive after a 4 KiB prefix | Read from a copy that starts at the RAR; `ar.cost.notes` warns of the copy at open |
| `unar` selected, solid pass with a refused member and more than 4000 readable members | Members past the 4000th refused in the pass; each still opens on its own |
| `unar` selected, compressed RAR 1.5 archive comment | Decoded by `unar` and checked against its CRC16 |
