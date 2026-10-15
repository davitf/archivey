# ZIP Format Behavior

## Purpose

ZIP archives are read through the unified `ArchiveReader` API using archivey's
own parser for the end record, central directory and local headers
(`internal/backends/zip_parser.py`) and archivey's shared codec layer for
member data. ZIP listing is indexed, member access is direct, and a seekable
source is read through its central directory. A non-seekable source is read under
`streaming=True` in one forward pass over the local headers. Read-only: writing is
not shipped for any format.

## Related specs

| Spec | Relationship |
| --- | --- |
| `archive-reading` | Reader API, password candidates, weak-check confirmation, bounded storage |
| `access-mode-and-cost` | Random-access vs streaming rules; non-seekable random-access failure; the forward pass's cost receipt |
| `diagnostics` | Timestamp and symlink-target diagnostic values / policy |
| `backend-registry` | `format_availability(ZIP)` FULL/PARTIAL from optional member codecs |
| `compressed-streams` | ZIP member-data decode path (method id → `StreamCodec`) |

## Requirements

### Requirement: Report ZIP format properties

The ZIP backend SHALL expose these properties for every opened ZIP archive:

| Property | Value |
| --- | --- |
| Backend dependency | None: archivey's parser for the structure (no `zipfile` import); shared codec layer for member data |
| Listing cost | `ListingCost.INDEXED` — end record read at open, central directory walked as members are listed |
| Access cost | `AccessCost.DIRECT` — independent local file offsets |
| Stream capability | `StreamCapability.SEEKABLE`; `FORWARD_ONLY` for a non-seekable source |
| Read source | Seekable in both access modes; non-seekable under `streaming=True` only; no implicit buffering/spooling |
| Write support | No — writing is not shipped for any format (`dev-docs/investigations/archive-writing-design.md`) |

`reader.get()` and other name lookups SHALL use the central-directory-derived
member map without extra archive I/O. Unencrypted ZIP member data SHALL decode
through the shared `compressed-streams` codec layer (bounded local-header parse
+ slice + method-id dispatch), including extended codecs when their backends are
installed: Deflate64 (method 9, via `[recommended]`/`inflate64`), ZSTD (method 93), PPMD
(method 98, ZIP PPMd8 framing via `[recommended]`/`pyppmd`). A missing optional backend
SHALL raise `PackageNotInstalledError`. An unknown/unsupported method id SHALL
raise `UnsupportedFeatureError`. `format_availability(ZIP)` SHALL report FULL
when every optional ZIP member codec is installed, else PARTIAL with the missing
components listed. Encrypted members (ZipCrypto / WinZip AE) SHALL decode the same
methods through the same codec layer, after a decrypt stage.

#### Scenario: ZIP property matrix

| Case | Expected |
| --- | --- |
| Open valid ZIP | `cost.listing_cost=INDEXED`, `cost.access_cost=DIRECT`, `cost.stream_capability=SEEKABLE` |
| `reader.get("some/member.txt")` | Satisfied from the in-memory central-directory name map; no additional archive I/O |
| Member uses unknown method id | Listing succeeds; reading raises `UnsupportedFeatureError` |
| Deflate64/Zstd/PPMd member, backend present | Decodes via the shared codec layer |
| Deflate64/Zstd/PPMd member, backend absent | `PackageNotInstalledError` |
| Optional ZIP codecs all installed | `format_availability(ZIP)` is FULL |

### Requirement: Decode ZIP member bodies through the shared codec layer

The ZIP backend SHALL decode unencrypted member data through the shared
`compressed-streams` codec layer rather than stdlib `zipfile`'s internal
decoders. It SHALL locate a member's raw compressed bytes via a bounded
local-file-header parse (fixed header + local name/extra lengths, with an
absurd data-offset cap) and a slice over the source, then dispatch by ZIP
method id to the codec's default backend.

Member reads SHALL verify `member.hashes["crc32"]` through the shared
`VerifyingStream` when a CRC is surfaced. A corrupt member body SHALL raise
`CorruptionError` (or `TruncatedError` when the payload is cut short) via the
shared translation. Traditional ZipCrypto and WinZip AES (method 99) members
SHALL decrypt natively (see below) and then feed the codec layer. A ZipCrypto
member SHALL seek under `seekable_members=True`: a backward seek restarts
decryption from the member's start, a forward seek decrypts what it skips, and
`AUTO` accelerators stay off over the decrypt stage. A WinZip AES member SHALL
seek too: CTR restarts at the target's block (counter `1 + offset // 16`). A
seek SHALL NOT give up the HMAC, whether the caller or an accelerator made it:
the HMAC covers the ciphertext, so the read that returns the member's last byte
SHALL complete it by reading, without decrypting, the ciphertext the seeks
skipped, and raise `CorruptionError` on a mismatch. A read that stops short of
the end gives no verdict.

Codec settings 7-Zip writes or names but this reader cannot decode SHALL raise
`UnsupportedFeatureError`, not `CorruptionError`. An LZMA (method 14) member whose
properties byte is a valid `lc`/`lp`/`pb` triple with `lc + lp` over 4 (`7z a -tzip
-mm=LZMA:lc=8`; liblzma decodes up to 4) is one, decided by the same check as a 7z LZMA
coder; a properties byte of 225 or more stays `CorruptionError`. A PPMd (method 98)
member with restore method 2 is another (7-Zip: "Unsupported Method"); a restore method
above 2 SHALL raise `CorruptionError` (7-Zip: "Data Error"). Under ZipCrypto these
settings are decrypted data, so both SHALL raise `CorruptionError` there, which the
password confirmation counts as the candidate failing (see below).

A member's compressed data SHALL be one stream of its codec and nothing else, as 7-Zip
23.01 reads it (DR-3). A byte of it the codec leaves after the stream's end, a zero
byte too, and a further stream there (a second Zstd frame, a skippable one too), SHALL
raise `CorruptionError` (`DataAfterEndError`) once the output before it is read,
whatever the declared size and CRC cover, for every method and for encrypted members
too, with an accelerator on or off; under ZipCrypto the password confirmation counts it
as the candidate failing, as above. An LZMA member without the end-marker flag
(general-purpose bit 1) and a PPMd member end at their declared size: their input
SHALL end there too (after an end marker right at that size for LZMA, and after PPMd8's
end mark, which a member SHALL carry), so a declared size short of the stream's data
SHALL raise `CorruptionError`, not `TruncatedError`. One zero byte after LZMA data
without an end marker reads, as 7-Zip's encoder sometimes flushes it past the
decoder's last read.

#### Scenario: ZIP codec-layer decoding

| Case | Expected |
| --- | --- |
| STORED / DEFLATE / BZIP2 / LZMA member, unencrypted | Decodes via the shared codec layer; CRC verified through `VerifyingStream` |
| LZMA member with compressed data after its end marker (a second stream, or one zero byte), whatever the declared size covers | `CorruptionError`, as 7-Zip reports "Data Error" |
| LZMA member without an end marker (bit 1 clear), with junk after its data or a declared size 1000 bytes short of it | `CorruptionError`, not `TruncatedError` (7-Zip: "Data Error") |
| PPMd member with junk or zero bytes after its end mark, a declared size 1000 bytes short of its data, or no end mark | `CorruptionError`, not `TruncatedError` (7-Zip: "Data Error") |
| DEFLATE, Deflate64, BZIP2 or Zstd member with junk, zero bytes or a second stream (a Zstd frame, a skippable one too) after its stream, accelerator off or on | `CorruptionError` (7-Zip: "There are some data after the end of the payload data") |
| DEFLATE64 (method 9) member, `inflate64` backend present | Decodes; absent backend → `PackageNotInstalledError` |
| ZSTD (method 93) / PPMD (method 98) member, backend present | Decodes; absent backend → `PackageNotInstalledError` |
| Unsupported/unknown method id | `UnsupportedFeatureError`; no guessed output |
| Corrupt member body | `CorruptionError` / `TruncatedError` |
| LZMA member, `lc + lp` over 4 (`-mm=LZMA:lc=8`) | `UnsupportedFeatureError` naming `lc` and `lp` |
| PPMd member, restore method 2 / 3–15 | `UnsupportedFeatureError` / `CorruptionError` |
| Encrypted ZipCrypto member, any of the methods above | Decrypts natively, decodes via the codec layer; CRC verified through the fused verifier |
| ZipCrypto or WinZip AES member, `seekable_members=True` | Seeks backward and forward; content matches a sequential read |

### Requirement: Read WinZip AES-encrypted members

The ZIP backend SHALL read WinZip AES (AE-x) encrypted members: compression
method 99 with the AES extra field `0x9901` giving vendor version (AE-1/AE-2),
key strength (128/192/256), and the actual underlying compression method.
Decryption SHALL derive keys via PBKDF2-HMAC-SHA1 (1000 iterations) over the
password and per-member salt (strength/16 bytes) into encryption key ‖
authentication key ‖ 2-byte verification value, decrypt with AES-CTR
(little-endian counter), and authenticate the ciphertext with HMAC-SHA1
truncated to 10 bytes. Decrypted bytes SHALL be decompressed through the shared
codec layer for the actual method.

A wrong password SHALL fail fast on the 2-byte verification value with
`EncryptionError` (no bytes returned). A ciphertext HMAC mismatch SHALL raise
at the terminal read (`CorruptionError`), whether one password or several were
tried: a wrong password passes the verification value once in 65 536, so a member
that every candidate passing it fails is reported as damaged, with the type the
damage has on the one-password path (`TruncatedError` for data that ends before its
declared size, `CorruptionError` otherwise). AE-2 members SHALL surface no
`crc32` (the ZIP CRC is 0; integrity is the HMAC) and run no CRC check; AE-1
members SHALL surface and verify `crc32` in addition to the HMAC. AES
decryption requires `cryptography` (`[recommended]`); when it is absent an AE member SHALL raise
`PackageNotInstalledError` (detection still identifies the member as
AES-encrypted), except that a member no library can read, one whose declared size cannot
hold salt, verification value and HMAC, SHALL raise `CorruptionError` with or without
`cryptography`. With several possible passwords, WinZip AES candidates SHALL be
confirmed as "Confirm multi-candidate ZipCrypto passwords" describes, not accepted on
the verification value alone.

#### Scenario: WinZip AES matrix

| Case | Expected |
| --- | --- |
| AE-1 or AE-2 member, 128/192/256, correct password, `cryptography` present | Decrypts, decompresses via codec layer, HMAC verified at EOF |
| Wrong password | `EncryptionError` on the 2-byte verification value; no bytes |
| Tampered ciphertext, correct password | HMAC mismatch → `CorruptionError` at terminal read |
| Tampered ciphertext, several candidates including the correct one | `CorruptionError` naming the member as most likely damaged |
| Declared size past the stored data, one password or several | `TruncatedError` |
| Declared size too small for the encryption header | `CorruptionError` naming the member |
| Tampered ciphertext, partial read then `close()` | Quiet; `close()` is teardown, not a verdict (ADR 0014) |
| AE-2 member | `crc32` absent; no CRC check; HMAC is the integrity signal |
| AE-1 member | `crc32` present and verified alongside the HMAC |
| AES member without `cryptography` installed | `PackageNotInstalledError`; still reported as encrypted |
| Same, declared size too small for the encryption header | `CorruptionError` |
| Several candidates, a wrong one passing the verification value first | Wrong candidate rejected by the confirm; the right one reads |

### Requirement: Refuse PKWARE Strong Encryption

The ZIP backend SHALL NOT decrypt PKWARE Strong Encryption (APPNOTE §7). An
encrypted member (general-purpose bit 0) that also sets bit 6 or carries an extra
field `0x0017` SHALL list with `is_encrypted=True`, and opening it SHALL raise
`UnsupportedFeatureError` naming Strong Encryption, whatever passwords were given.
Listing a symlink of this kind SHALL leave `link_target` unset and emit
`SYMLINK_TARGET_UNAVAILABLE` with reason `"target_data_encrypted"`. When an archive
extra data record (`PK\x06\x08`) sits where the central directory starts (the position
of the end record that follows the directory, minus the directory size that record
gives; for a ZIP64 archive both come from the ZIP64 end record), opening the archive
SHALL raise `UnsupportedFeatureError` naming Strong Encryption rather than
`CorruptionError`.

#### Scenario: Damaged central directory without the record

- **WHEN** the central directory cannot be read and no archive extra data record sits
  where it starts
- **THEN** listing raises `CorruptionError`, not `UnsupportedFeatureError`, after the
  entries before the damage

### Requirement: Read a non-seekable ZIP source in one forward pass

A ZIP read from a non-seekable source SHALL be read under `streaming=True` in one
forward pass over the local headers, with the central directory read after the last
member. Under `streaming=False` it SHALL raise `StreamNotSeekableError` at open, naming
`streaming=True`. The library MUST NOT buffer, spool, or copy the source into seekable
storage. What the pass keeps SHALL be bounded: the directory and end records by the
listing limits, and the member data it keeps (a password-confirmation prefix, a short
member that may turn out to be a link) by fixed caps.

Each member's data ends where its local header says, or, when bit 3 defers the sizes to
a data descriptor, where its codec's end marker is (DEFLATE, Deflate64, bzip2,
Zstandard, LZMA with the end-of-stream marker, PPMd with its end mark), followed by the
descriptor; a STORED bit-3 member ends at a descriptor whose CRC and size match the
bytes before it and that a ZIP signature follows. A bit-3 member nobody reads that is
encrypted under a codec ends the same way, matched on its size, so passing it needs no
key. A bit-3 LZMA member without the end-of-stream marker has no end its codec can find:
its read SHALL raise `UnsupportedFeatureError`. A bit-3 PPMd member without an end mark
fails its decode, as from a file. A bit-3 codec member whose decode fails or cannot find
its end SHALL be passed by the same scan for its descriptor on its stored bytes, with no
key. The scan starts at the last 4 MiB the decode read and counts the bytes before them
into the size it matches; a decode that read more than that past the data's end leaves
the descriptor behind the scan's start, and the pass SHALL then end with the scan's
error. A bit-3 PPMd member is decoded in a child process, and
`max_ppmd_in_process_input=None` SHALL refuse it with `ResourceLimitError`. Several
password candidates SHALL be confirmed on a kept prefix of the member's data
(`archive-reading`, "A unit read once").

`stream_members()` SHALL yield each member with what its local header gives: `mode`,
`create_system` and `comment` are `None`, the type is `FILE` or `DIRECTORY` from the
name (a name ending in `\` is a `DIRECTORY`), and `member_state_final` is `False`
(`archive-data-model`). Once the directory has been read, before the pass ends, each
member SHALL be updated in place from its directory entry, matched by offset, as the
same archive read from a file lists it, and `is_current` SHALL follow the directory
order; member ids and listing order stay those of the file. A local entry no directory
entry points to SHALL be dropped from the listing and reported as
`ARCHIVE_UNLISTED_DATA`. A member flagged as a Windows reparse point whose data is not
a reparse buffer stays a file. The cost receipt SHALL be `REQUIRES_SCANNING`, `DIRECT`,
`FORWARD_ONLY`; `member_count` and `comment` SHALL be `None` and
`members_report_if_available()` SHALL return `None` until the directory has been read.

Five things SHALL fail the member they name once the directory has been read: a size
or CRC from a data descriptor that differs from the central entry; a local header whose
CRC, sizes or encryption flag the central entry contradicts; two same-name members
whose directory order reverses their file order; a directory entry with no local
entry; and a local header and directory entry that store different names.
`stream_members()` SHALL raise `CorruptionError` at the end of the pass for each but
the reversed pair, which only updates the members. `extract_all` settles them as
`safe-extraction` states. A directory entry pointing at a local header an earlier
entry already took SHALL end the pass with `CorruptionError`.

The pass MAY differ from a file read on crafted archives (DR-5a): the descriptor
mismatch and the reversed pair fail a member the file read writes; bytes between a bit-3
codec member's end and its descriptor, refused from a file, are counted into the member;
the first of several inconclusive password candidates wins. It MAY also differ in these:
a damaged bit-3 codec member can fail with another `CorruptionError` subclass, and ends
the pass, losing the members after it, if its decode read more than 4 MiB past the
data's end; a bit-3 LZMA member without the end-of-stream marker is refused where a file
read reads it; in `extract_all`, a link member the pass wrote as a file keeps the error
its data read gave (no password: `EncryptionError`, where the file read reports
`LinkTargetNotFoundError`); and an archive-wide refusal the directory or end record
makes (a damaged ZIP64 end record or locator, a flagged name that is not valid UTF-8) is
raised by a file read before anything is written and by the pass after every member is
written, the files staying on disk.

#### Scenario: non-seekable read matrix

| Case | Expected |
| --- | --- |
| Non-seekable ZIP with default `streaming=False` | `StreamNotSeekableError` at open, naming `streaming=True`; no reader |
| Non-seekable ZIP with `streaming=True` | Opens; `stream_members()` and `extract_all()` read every member in one pass; after the pass the members equal the file read's |
| Implementation lacks seekable source | No implicit seekable-copy, temp-file, or spool fallback |
| `stream_members()` from a pipe, a Unix symlink member | Yielded as `FILE`, `member_state_final` false; after the pass `SYMLINK` with its target |
| `extract_all()` from a pipe, a Unix symlink member | The link is made at the end of the pass, after the filter runs again on it |
| Bit-3 STORED members (stdlib `zipfile` writing to a pipe) | Each ends at its descriptor; contents as from a file |
| Bit-3 LZMA member without the end-of-stream marker | `UnsupportedFeatureError` at that member |
| Bit-3 PPMd member, `max_ppmd_in_process_input=None` | `ResourceLimitError` |
| Two same-name members whose directory order reverses their file order | `is_current` from the directory order; `stream_members()` raises nothing |
| A data descriptor that understates a STORED member, `stream_members()` | `CorruptionError` at the end of the pass |
| A local entry the directory does not list | Dropped from the listing; `ARCHIVE_UNLISTED_DATA` |
| `reader.info` during the pass, then after it | `member_count` and `comment` `None`, then the directory's |

### Requirement: Map ZIP member metadata to ArchiveMember

The ZIP backend SHALL map each `ZipInfo` to `ArchiveMember` with these field
rules:

| Field | Mapping |
| --- | --- |
| `mode` | `external_attr >> 16` only for Unix entries with non-zero attrs; otherwise `None` |
| timestamps | DOS `date_time` base (naive local wall-clock, 2s granularity, 1980 sentinel → `None`); NTFS extra `0x000A` UTC FILETIMEs override present fields; Extended Timestamp `0x5455` UTC Unix times override present fields |
| `type` | Infer from Unix mode when available (a device, FIFO or socket mode is `OTHER` when the entry stores no data, `FILE` with `MEMBER_SPECIAL_FILE_HAS_DATA` when it does, `extra["special_file_type"]` whatever the type, a directory-marked or reparse-flagged entry over such a mode included); otherwise directory marker and symlink hints. The directory marker is a trailing `/` on the decoded name that `name` comes from, or a trailing `\` when the entry is DOS/Windows-origin, so the type is the same on every host OS and Python version. A directory marker with `file_size > 0`, or with `file_size == 0` and a body larger than an empty one of its method (0 stored, 2 deflated, 14 bzip2, 19 LZMA; any body for another method), stays `DIRECTORY` and emits `MEMBER_DIRECTORY_DATA_IGNORED` with both sizes; one declaring data carries the central-directory CRC-32 in `hashes` and `open()` on it decodes and verifies the data like a file's, unless the CRC field is 0, in which case it carries no digest: where that CRC was the entry's only check (unencrypted, ZipCrypto) `DIGEST_UNVERIFIABLE` (`reason="no_integrity_anchor"`) is reported and the bytes are delivered unchecked, while a WinZip AES entry (AE-1 with a zeroed field, or AE-2, which stores 0 by design) is verified by its HMAC, with no digest and no report |
| `uid`, `gid` | The first readable owner extra field: `0x7875` (Info-ZIP new Unix, version 1, ids of 1 to 8 bytes), then `0x7855` (Info-ZIP Unix type 2), then `0x000d` (PKWARE Unix) and `0x5855` (Info-ZIP old Unix); otherwise `None` |
| `uname`, `gname` | Always `None`: no ZIP extra field records them |
| `compression` | `compress_type` mapped to `CompressionMethod` |
| `is_encrypted` | `flag_bits & 0x1 != 0`, or `compress_type == 99` (WinZip AES) whatever bit 0 says |

Invalid DOS or NTFS timestamp values SHALL fall through to the next valid
precedence layer or `None` and emit `MEMBER_TIMESTAMP_INVALID`, whose `field`
SHALL name the member attribute: `modified` for the DOS stamp and the NTFS
modification time, `accessed` for the NTFS access time, and `created` or `ctime`
for the NTFS creation time, by the same host rule that fills those fields. With
`read_link_targets=True` (the default), if listing cannot read an encrypted
symlink target because no correct password is available, `link_target` SHALL
remain unset and `SYMLINK_TARGET_UNAVAILABLE` SHALL be emitted with reason
`"password_required"`. When a ZipCrypto target's data fails its integrity check
under a password only the verification byte vouched for (the ambiguous
`EncryptionError` of "Confirm multi-candidate ZipCrypto passwords"), the reason
SHALL be `"password_or_damage"` and the message SHALL name both causes. With
`read_link_targets=False`, listing reads no symlink target and emits nothing for it
(`archive-reading`, "Link targets stored as member data are read only when
configured"). Diagnostic payloads SHALL not include passwords, candidates,
provider returns, key material, or decrypted target bytes. Under `RAISE`, listing
halts with `DiagnosticRaisedError`.

#### Scenario: ZIP metadata matrix

| Case | Expected |
| --- | --- |
| Unix entry with non-zero `external_attr` | `member.mode = external_attr >> 16` |
| Non-Unix entry or missing attrs | `member.mode is None` |
| Extended Timestamp carries modification time | `member.modified` is timezone-aware UTC from `0x5455`, overriding DOS / NTFS |
| NTFS FILETIMEs present, no Extended Timestamp, DOS-attribute host | Present `modified` / `accessed` / `created` fields are timezone-aware UTC from `0x000A` |
| Creation time stored (NTFS or Extended Timestamp third time), FAT / OS2 / NTFS / VFAT host | `created` holds it (the Extended Timestamp wins); `ctime is None` |
| Creation time stored, Unix or any other host | `created is None`; `ctime` holds it (7-Zip and libarchive on Linux and macOS store `st_ctime`) |
| Written by Info-ZIP `zip` on Unix (extra field `0x7875`) | `uid` and `gid` are the file's owner ids; `uname` and `gname` are `None` |
| Owner field `0x7855` in the local header only, read from a pipe | The pass yields the local header's ids; once the directory is read they are `None`, as a file read lists them |
| `flag_bits & 0x1`, or method 99 with bit 0 clear | `member.is_encrypted is True` |
| Unix-origin entry named `a\` | `FILE` named `a\` on every host OS |
| DOS-origin entry named `a\` | `DIRECTORY` named `a/` on every host OS; any data it declares is not reachable (no `open()`, extraction writes an empty directory) and `size` still reports it |
| Header name without a trailing `/`, Unicode Path field `dir/` | `DIRECTORY` named `dir/` on every Python version |
| Out-of-range NTFS or DOS timestamp | Fallback value used; `MEMBER_TIMESTAMP_INVALID` counted and may attach to member |
| Timestamp diagnostic resolves to `RAISE` | Listing halts with `DiagnosticRaisedError` |
| Encrypted symlink target unavailable | Listing continues with `link_target=None`; `SYMLINK_TARGET_UNAVAILABLE` contains no secret |
| ZipCrypto symlink target fails its integrity check under an unconfirmed password | Listing continues with `link_target=None`; `SYMLINK_TARGET_UNAVAILABLE` with reason `"password_or_damage"` |
| Encrypted symlink, `read_link_targets=False` | Listing reads no target data; `link_target=None`; no diagnostic |

### Requirement: Join 7-Zip .zip.NNN sets; reject spanned ZIP cleanly

7-Zip's `-v` byte-splits one finished single-disk ZIP into
`name.zip.001 … name.zip.00N`, exactly as it splits `name.7z.NNN`. An SFX split
names the same slices `name.exe.001 … name.exe.00N` (stub `name.exe` is not a
sibling). Windows 7-Zip keeps the archive extension (`name.zip.001`) and writes
the stub as `name.exe`. With every
part `1..N` present beside the one named, `open_archive` SHALL concatenate them
and read the result as the ordinary ZIP it is, from any part **or from a
stub-only `name.exe` that has no archive magic** (including under an explicit
`format=ZIP`; a `format=` naming a different container is `ArchiveyUsageError`),
reporting
`ArchiveInfo.is_multivolume = True` and `ArchiveInfo.extra["zip.volume_count"] = N`
(not `ArchiveMember.extra`, which stays empty).
A gap in the numbering SHALL raise `TruncatedError`. A lone `.zip.NNN` (or
SFX `.exe.NNN`) part whose siblings are not on disk SHALL raise the same
`TruncatedError`, naming the missing parts — not a ZIP spanned-set refusal.
A named part that is not on disk itself SHALL raise `FileNotFoundError`, like
any other missing path.

Every other split/spanned signal SHALL raise `UnsupportedFeatureError` with a
rejoin-first message rather than mis-read data or raise `CorruptionError`:
Info-ZIP `.zNN` segment names, non-zero EOCD disk fields (the ZIP64 end record's
when there is one; a classic `0xFFFF` is the ZIP64 sentinel, not a disk number), and
ZIP64 locator `disks > 1`. Info-ZIP
`zip -s` writes a genuinely spanned
set addressed by `(disk, offset-within-disk)`; a linear join lists correctly and then
reads only whichever members happen to sit on the last disk. Reading such a set is not
planned.

#### Scenario: multi-volume ZIP join and refusal

`open_archive` on a complete 7-Zip `.zip.NNN` or SFX `.exe.NNN` set — named at any
part — lists and
reads its members, including data spanning a part boundary. An incomplete such set
raises `TruncatedError`; every other split/spanned signal raises
`UnsupportedFeatureError`. Neither surfaces `CorruptionError` or
`FormatDetectionError`.

### Requirement: Check the ZIP end record against the central directory

The central directory is walked entry by entry until the walk has consumed the
directory size the end record gives. The ZIP reader SHALL report, as
`ARCHIVE_EOF_MARKER_MISSING` with `format="zip"`, emitted after the listing's
last member so the listing completes, each of:

- the entry count the end record declares (the ZIP64 record's when there is one)
  differs from the number of entries the central directory holds
  (`expected_marker="end_of_central_directory"`, `observed_kind="nonzero"`). A classic
  record counts in 16 bits and old 7-Zip versions stored a larger count's low 16 bits
  there without ZIP64, which current 7-Zip accepts, so its count is compared modulo
  65536;
- the archive comment length runs past the end of the file
  (`expected_marker="end_of_central_directory"`, `observed_kind="short"`);
- a central-directory entry's name, extra field or comment length runs past the
  directory, so the field is cut at the directory's end
  (`expected_marker="central_directory"`, `observed_kind="nonzero"`).

None of them SHALL raise under the default policy; `STRICT` refuses the archive.
Info-ZIP unzip warns or errors on each and 7-Zip reports "Headers Error", after testing
every member.

The checks compare the end record the reader selected, the last end-record signature
in the file's tail. An end-record signature inside the archive comment is therefore
compared with the empty directory it describes and agrees with it: the archive lists as
empty (`EMPTY_ARCHIVE`) with no end-record diagnostic, as Info-ZIP unzip also lists it,
while 7-Zip searches further back and lists the real members.

#### Scenario: end record mismatches

| Archive | Expected |
| --- | --- |
| Classic end record declares 2 or 4 entries, directory holds 3 | All 3 members listed and readable; one `ARCHIVE_EOF_MARKER_MISSING`, `expected_marker="end_of_central_directory"`, `observed_kind="nonzero"`, `observed_bytes` the record's offset |
| ZIP64 end record declares 5 entries, directory holds 3 | Same, the message naming the ZIP64 record |
| Comment length 5000, 8 comment bytes in the file | `observed_kind="short"`, `expected_bytes` 5022, `observed_bytes` 30 |
| Last directory entry's name, extra or comment length runs past the directory | `expected_marker="central_directory"`; the message names the member and the field |
| Consistent classic or ZIP64 end record | No diagnostic |
| 65537 entries, classic record only, count 1 (65537 modulo 65536) | All 65537 members listed; no diagnostic |
| Archive comment holding an end-record signature followed by 30 zero bytes | No members listed; one `EMPTY_ARCHIVE` and no `ARCHIVE_EOF_MARKER_MISSING` |
| Any of the above under `STRICT` | `DiagnosticRaisedError` from the listing |

### Requirement: List a damaged ZIP central directory up to the damage

The central directory SHALL be walked as members are listed, so a damaged entry costs
the entries from it on, not the archive (DR-2). An entry whose signature is wrong, or
whose 46-byte fixed header the directory cuts short, SHALL make the listing raise
`CorruptionError` after the members before it: `members()` raises,
`members_report()` returns those members with the error, iteration yields them and then
raises, and `extract_all()` writes them and then raises. A listed member before the
damage SHALL read. `ArchiveInfo.member_count` SHALL be `None` when the walk did not
complete. A UTF-8-flagged name that is not valid UTF-8 SHALL raise `CorruptionError`
the same way, at its entry. A name, extra field or comment that runs past the
directory's end can only be the last entry's; it is the end-record mismatch above, not
damage. A local-header offset past the file's end SHALL raise `CorruptionError` when
that member opens, never `TruncatedError` (the directory follows every local header, so
the offset is wrong, not the file cut) and never an untyped error for an offset too
large to seek to.

#### Scenario: damaged directory

| Archive | Expected |
| --- | --- |
| Third central header's signature overwritten | `a.txt`, `b.txt` listed, then `CorruptionError` naming a bad magic number; both read |
| A 10-byte header fragment after the second entry, inside the declared directory size | The same, `CorruptionError` naming a truncated central directory |
| Either, through `for member in reader` or `extract_all()` | Two members yielded or written, then `CorruptionError` |
| Second entry's ZIP64 local-header offset 2^41, 2^63 − 1 or 2^64 − 1 | Both members listed; `a.txt` reads, `b.txt` raises `CorruptionError` when it opens |

### Requirement: Read ZIP members whatever version they say they need

"Version needed to extract" SHALL NOT refuse a member or the archive: what a member
needs is named by its method and flags, which are checked when it opens (DR-4). 7-Zip
23.01 reads a stored member marked 8.4.

#### Scenario: version needed above 6.3

| Archive | Expected |
| --- | --- |
| Stored member, both headers marking version 8.4 | Listed and read |
| The same with method 77 | Listed; reading it raises `UnsupportedFeatureError` naming method 77 |

### Requirement: Report bytes after the end of a ZIP archive

A ZIP archive SHALL be taken to end at the end of its end-of-central-directory record
plus the comment length that record declares. At open, a non-zero byte after that end
SHALL emit one `ARCHIVE_TRAILING_DATA` with `format="zip"`,
`expected_marker="zeros_to_eof"`, `observed_kind="nonzero"` and `observed_bytes` the
offset of that byte past the end. It is a warning by default and raises under
`DiagnosticPolicy.STRICT` (DR-3). Zero bytes after the end SHALL be silent. Bytes
inside the declared comment are the comment, whatever they hold. The end record is
searched for in the last 65 558 bytes of the file, so a tail longer than that hides it
and the archive is not opened; a stub before the archive moves nothing. 7-Zip 23.01
warns on any tail; Info-ZIP `unzip` says nothing.

#### Scenario: ZIP trailing bytes

| Archive | Expected |
| --- | --- |
| Ends at its end record, with or without a comment | No diagnostic |
| 4096 zero bytes after the end record | No diagnostic; `STRICT` opens it |
| 0, 100 or 60 000 zero bytes then `JUNK` after the end record or its comment | One `ARCHIVE_TRAILING_DATA`, `observed_bytes` 0, 100 or 60 000; members list and read |
| A 1000-byte stub before the archive, 7 zero bytes and `JUNK` after it | `observed_bytes` 7 |
| `JUNK` after the end record under `STRICT` | `DiagnosticRaisedError` |

### Requirement: Apply max_members while reading a ZIP directory

`ListingLimits.max_members` SHALL be charged as the central directory is walked, in
every access mode and by every walk (the listing, and the index pass a member open or
`member_count` runs when it comes first), so `stream_members()` and iteration are not
an escape hatch. An end record that declares more entries than `max_members`, in a
directory at least 46 bytes per declared entry long, SHALL be refused at
`open_archive` with `ResourceLimitError`. A declared count the directory cannot hold is
a wrong field (reported as a count mismatch, see above), and the walk charges the
entries really there: past `max_members` it SHALL raise `ResourceLimitError`. The
members listed before the limit SHALL read, also when they are opened before the
listing reaches it.

A forward pass over a non-seekable source has no directory to read first: it SHALL
charge `max_members` on the local headers as it yields members, and again on the
directory's entries as it reads them, and SHALL weigh the directory's names, extra
fields and comments against `max_metadata_bytes` as they arrive, raising
`ResourceLimitError` past either.

#### Scenario: ZIP member limit

| Archive, `max_members=2` | Expected |
| --- | --- |
| Three entries, end record declares 3 | `ResourceLimitError` at open |
| Three entries, end record declares 1 | Opens; the listing, iteration or `stream_members()` raises `ResourceLimitError` at the third entry |
| The same, `streaming=True`, each stream read as `stream_members()` yields it | The first two members read in full, then `ResourceLimitError` |
| Three entries from a non-seekable source, `streaming=True` | The first two members yielded, then `ResourceLimitError` at the third local header |

### Requirement: Confirm multi-candidate ZipCrypto passwords

For traditional ZipCrypto, the per-open verification byte is weak and the
authoritative check is CRC-32 plus decompressor completion. When another
distinct candidate may be tried, the ZIP reader SHALL confirm a candidate that
passes the byte check before accepting it, following `archive-reading` weak-check
confirmation and bounded-storage rules. With one distinct static candidate
(duplicates included), the reader SHALL keep the normal lazy stream path, with no
confirmation read. Because only the verification byte vouched for that password, a
candidate failure (defined below) on the caller's `read`, `readinto` or forward `seek`
SHALL raise `EncryptionError` explaining that the password may be wrong or the member
may be corrupt, not `CorruptionError`; for an `LZMA` or `PPMd` member, whose codec
header is read when the member opens, the open raises it. It SHALL NOT be marked as a
wrong-password verdict: nothing in the archive tells a colliding wrong password from a
damaged member. A STORED member's forward seek jumps, so a read after it pauses the CRC
until a seek back to the frontier (`compressed-streams`, ADR 0014) and that seek does
not raise; closing that stream emits `ENCRYPTED_MEMBER_UNVERIFIED`. A compressed
member's forward seek keeps the CRC, as `compressed-streams` describes.

ZIP's two per-open checks are the `archive-reading` ladder's **cheap key check** rung:
ZipCrypto's one header verification byte (2⁻⁸) and WinZip AES's two-byte `pw_verify`
(2⁻¹⁶). Neither reaches 2⁻³², so neither SHALL confirm on its own; both eliminate. Neither
cipher pads — ZipCrypto is a byte-wise stream cipher and WinZip AES is CTR, so ciphertext
length equals plaintext length in both — so ZIP has no tail-padding check to add.

A member stream whose password only one of those checks accepted, closed before EOF,
SHALL emit `ENCRYPTED_MEMBER_UNVERIFIED` (`check="weak_open_check"`): a wrong ZipCrypto
password that passes the byte check returns readable garbage from a partial read and
fails only at the CRC, and a WinZip AES member's HMAC runs only at EOF.

Compressed members SHALL confirm by decompressing a bounded plaintext prefix and
discarding it. If EOF is reached within the bound, the CRC check makes confirmation
exact. Confirmation SHALL run through `plan_password_confirm` /
`run_password_confirm_plan` as the probe under `_PasswordCandidates.attempt`, with the
member as the one substream. Only `DEFLATE`, `BZIP2` and `LZMA` count as rejecting
codecs; they are stream codecs, or bzip2, which produces output after one block, so the
compressed input a bounded prefix consumes is bounded by the format and needs no
separate cap. Any other method walks to the CRC. A compressed member larger than the
prefix yields an `INCONCLUSIVE` survivor: its stream, closed before EOF, SHALL emit
`ENCRYPTED_MEMBER_UNVERIFIED` (`check="confirm_budget_exhausted"`). The same probe
confirms WinZip AES candidates. Where an AES member's CRC is out of reach (AE-2 stores
none) and the member fits the prefix or its codec does not reject, the probe SHALL read
the member to its end, where the HMAC, which covers the whole ciphertext, confirms or
rejects the candidate. STORED members
SHALL disambiguate all surviving candidates in one shared ciphertext pass, computing each
candidate's plaintext CRC-32 in constant memory; if multiple candidates match, candidate
order wins. No candidate plaintext may be buffered. The STORED shared pass stays
ZIP-local, since running every candidate over one pass is a shape the per-candidate probe
does not model.

After confirmation, the reader SHALL open a fresh caller stream with the accepted
password, promote it to known-good when confirmation reached the member's CRC, and retain
ordinary read-time integrity checking. An `INCONCLUSIVE` survivor is not promoted: this
path runs only for an ambiguous candidate set. Confirmation failure for all candidates
SHALL raise `EncryptionError` explaining that passwords may be wrong or the member may be
corrupt. For WinZip AES, where each failing candidate had passed the 16-bit `pw_verify`,
it SHALL raise the damage instead (`CorruptionError`, or `TruncatedError` for data that
ends before its declared size), as "Read WinZip AES-encrypted members" says.

Candidate failures SHALL be the `CorruptionError` a wrong key's garbage produces (a
codec rejection, a CRC or HMAC mismatch), and a codec's `TruncatedError` while the
member's whole payload is in the file. Local-header mismatch, bad local-header magic,
overlap and other structural failures raise before decryption starts and SHALL become
`CorruptionError` immediately, with no further password iteration; a payload the file
cuts short stays `TruncatedError`. `UnsupportedFeatureError`,
`PackageNotInstalledError`, `ResourceLimitError` and `OSError` values SHALL propagate
unchanged, with two exceptions. The LZMA `lc + lp` and PPMd restore-method settings a
ZipCrypto key decrypted raise `CorruptionError`, not `UnsupportedFeatureError` (see the
codec-layer requirement), so a wrong key's garbage there is a candidate failure. On a
ZipCrypto `LZMA` or `PPMd` member the codec settings (the LZMA properties, the PPMd
order and memory size) are part of the encrypted data, so a wrong password that passes
the one-byte check decrypts them to an arbitrary size. A `ResourceLimitError` those
settings raise SHALL count as that candidate's failure, and the next candidate SHALL be
tried. When no candidate succeeds and at least
one failed that way, the reader SHALL raise the first such `ResourceLimitError`, its
message noting that under ZipCrypto the password may be wrong. It stays a
`ResourceLimitError` so that a caller who raises `DecoderLimits.max_decoder_memory`
gets a working read when the size is real. A lone password raises the same noted
`ResourceLimitError`. WinZip AES members keep propagating `ResourceLimitError`
unchanged: a garbage size needs a wrong password that passes the 16-bit `pw_verify`.
Rejected-candidate streams SHALL be closed before trying the next candidate.

#### Scenario: ZipCrypto confirmation matrix

| Case | Expected |
| --- | --- |
| Wrong candidate passes verification byte before correct one (STORED / DEFLATE / BZIP2 / LZMA) | Wrong candidate rejected; fresh stream opened with correct candidate |
| One distinct static candidate | No confirmation read; member streams lazily |
| One distinct static candidate that passes the verification byte but is wrong, or right on a corrupt member | Caller `read`/`readinto`/forward `seek` of a compressed member raises `EncryptionError` saying the password may be wrong or the member corrupt (at open for `LZMA`/`PPMd`); no wrong-password mark |
| Same, `LZMA`/`PPMd` member whose decrypted codec settings exceed `max_decoder_memory` | Open raises `ResourceLimitError` whose message notes the ZipCrypto password may be wrong |
| Wrong candidate whose decrypted `LZMA`/`PPMd` settings exceed `max_decoder_memory`, before the correct one | Wrong candidate rejected; correct candidate reads |
| No candidate succeeds, and one failed on a decoder limit from its decrypted settings | The first such `ResourceLimitError`, message noting the ZipCrypto password may be wrong |
| Same, STORED member, caller seeks and closes | Seek does not raise; `ENCRYPTED_MEMBER_UNVERIFIED` on close |
| Large compressed member | At most bounded prefix decompressed per candidate; no proportional plaintext storage; caller stream still checks CRC at EOF |
| STORED member with several surviving candidates | One shared ciphertext pass computes every candidate CRC; matching candidate accepted and reopened |
| Multiple STORED CRC matches | Earliest matching candidate in order wins |
| Corruption beyond confirmed prefix | Caller read raises what the codec layer raises for the same damage unencrypted (`CorruptionError` or `TruncatedError`) |
| ZipCrypto candidates fail confirmation | `EncryptionError` says password may be wrong or member corrupt; no bytes returned |
| WinZip AES candidates all fail after passing `pw_verify` | `CorruptionError` (`TruncatedError` for a short member); no bytes returned |
| `OSError` from the source | Propagates unchanged; failed stream is closed |
| Structural local-header damage | `CorruptionError`; no further password iteration |
| Declared size too small for the encryption header | `CorruptionError` naming the member |
| One distinct static candidate, stream closed before EOF | Data returned; `ENCRYPTED_MEMBER_UNVERIFIED` (`check="weak_open_check"`) |
| Several candidates, STORED or compressed member within the prefix, stream closed before EOF | No diagnostic (the CRC confirmed the winner) |
| Several candidates, compressed member past the prefix, stream closed before EOF | `ENCRYPTED_MEMBER_UNVERIFIED` (`check="confirm_budget_exhausted"`); winner not added to known-good |
| WinZip AES member, wrong password | Rejected by `pw_verify`; HMAC remains the authoritative check |
| WinZip AES member, stream closed before EOF | `ENCRYPTED_MEMBER_UNVERIFIED` (`check="weak_open_check"`) |

### Requirement: Decode unflagged ZIP member names by UTF-8-validity sniff

The ZIP backend SHALL decode a member name whose general-purpose bit 11 (UTF-8/EFS flag) is
**clear** by first attempting UTF-8, and only when the bytes are not valid UTF-8 falling
back to the caller's `encoding=` when one was passed, else to a configurable legacy encoding
(default cp437, per APPNOTE). The caller's `encoding=` and the configured fallback SHALL
decode with `surrogateescape`; a codec that refuses that handler (`idna`) gives way to the
next one, and cp437 decodes every byte. This sniff SHALL apply only in the absence of an
authoritative encoding signal: a set bit 11 SHALL be honored as UTF-8. When the sniff
selects an encoding archivey chose over the cp437 default — UTF-8, or a configured fallback
other than cp437 — the backend SHALL emit a `diagnostics` warning identifying the member,
the chosen encoding and the encoding it passed over (the caller's `encoding=` or the
configured fallback for a UTF-8 reading, cp437 for a configured fallback), so the decision
is observable and escalatable via `DiagnosticPolicy`. A name decoded with the caller's
`encoding=` emits no such warning. Decoding SHALL NOT raise a bare `UnicodeDecodeError`.

An Info-ZIP Unicode Path extra field (`0x7075`) in the central directory outranks both the
sniff and an explicit `encoding=` for an unflagged name: when the field is version 1, its
CRC-32 equals the CRC-32 of the stored name bytes, and its name is non-empty valid UTF-8,
the member name SHALL be that UTF-8 name, `raw_name` SHALL be its UTF-8 bytes, and
`extra["alternate_raw_name"]` SHALL hold the stored header bytes. No inference diagnostic
is emitted, since the field declares the encoding. A field whose CRC does not match or
whose version is not 1 SHALL be ignored, and the name decoded as above. A malformed field
SHALL be dropped, the name decoded as above, and `MEMBER_HEADER_RECORD_SKIPPED` emitted on
the member with `record="unicode_path"` and `record_id=0x7075`: a field shorter than its
version byte and CRC (5 bytes), whatever its first byte, since neither its version nor
its CRC can be read; or a version-1 field with a matching CRC whose name is
empty or not valid UTF-8. A version-1 field whose CRC matches but whose declared name runs
past the end of the extra field is malformed too. The outcome SHALL be the same on every
supported Python version: the archive SHALL open, and no Python `warnings` warning SHALL
be raised for the field. A
field in the local header only SHALL be ignored, and so SHALL the field of a name whose
UTF-8 flag is set. `name` is always `raw_name` decoded and normalized.

#### Scenario: UTF-8 bytes without the flag

- **WHEN** an archive stores a member name as valid UTF-8 bytes (e.g. `Español.txt`,
  `emoji_😀.txt`) with bit 11 **clear**, with or without an `encoding=` from the caller
- **THEN** the member name is decoded as UTF-8 (`Español.txt`, `emoji_😀.txt`), not cp437
  or `encoding=` mojibake, and a diagnostic records that UTF-8 was inferred for an
  unflagged name

#### Scenario: Legacy bytes without the flag

- **WHEN** an unflagged member name is not valid UTF-8
- **THEN** it is decoded with the caller's `encoding=` when one was passed, else with the
  configured legacy fallback (default cp437), and no bare `UnicodeDecodeError` escapes

#### Scenario: Authoritative signal disables the sniff

- **WHEN** bit 11 is set
- **THEN** the name is decoded as UTF-8, with no sniff and no override diagnostic

#### Scenario: A flagged name that is not UTF-8

- **WHEN** bit 11 is set and the stored name is not valid UTF-8
- **THEN** the listing raises `CorruptionError` at that entry, after the members before
  it

#### Scenario: Unicode Path extra field

| Case | Expected |
| --- | --- |
| Unflagged cp866 name `Привет.txt`, central `0x7075` version 1 with a matching CRC | `name == "Привет.txt"`; `raw_name` is its UTF-8 bytes; `extra["alternate_raw_name"]` is the cp866 bytes; no diagnostic |
| The same, `encoding="latin-1"` | The same: the field outranks `encoding=` |
| The field's CRC does not match the stored bytes, or its version is not 1 | Field ignored: cp437 decode (or `encoding=`), `raw_name` is the stored bytes, no `alternate_raw_name` |
| The field in the local header only | Field ignored |
| The field is 0 to 4 bytes long, whatever its first byte, or the CRC matches and the name is empty, not valid UTF-8, or cut short by the end of the extra field | The archive opens on every Python version; field dropped: the name decoded as above, no `alternate_raw_name`, `MEMBER_HEADER_RECORD_SKIPPED` attached to that member only, no Python warning; `DiagnosticPolicy.STRICT` refuses |
| The same malformed field on a name with the UTF-8 flag set | Field not consulted: no diagnostic |

### Requirement: Decode ZIP comments as unflagged names are decoded

APPNOTE puts a member comment under the same bit 11 as its name, and gives the archive
comment no flag. A member comment whose bit 11 is clear, and the archive comment, SHALL
decode as an unflagged name does: UTF-8 when the bytes are valid UTF-8, else the caller's
`encoding=`, else `zip_unflagged_fallback_encoding` (default cp437). The `encoding=` and
fallback steps SHALL be the ones a name uses. A byte the chosen codec leaves undefined
SHALL survive as a lone surrogate, as it does in a name. A member comment whose bit 11 is
set SHALL decode as UTF-8, with cp437 for bytes that are not valid UTF-8, and SHALL
ignore `encoding=`. A comment is not a name, so decoding it SHALL NOT emit
`MEMBER_NAME_ENCODING_INFERRED`.

#### Scenario: A flagged member comment ignores `encoding=`

- **WHEN** a member with bit 11 set has a comment whose bytes are not valid UTF-8, and
  the archive is opened with `encoding="cp1251"`
- **THEN** the comment is the cp437 reading of the bytes, not the cp1251 one
