# ZIP Format Behavior

## Purpose

ZIP archives are read through the unified `ArchiveReader` API using stdlib
`zipfile` for the central directory and archivey's shared codec layer for
member data. ZIP listing is indexed, member access is direct, and read sources
must be seekable. Read-only: writing is not shipped for any format.

## Related specs

| Spec | Relationship |
| --- | --- |
| `archive-reading` | Reader API, password candidates, weak-check confirmation, bounded storage |
| `access-mode-and-cost` | Random-access vs streaming rules; non-seekable random-access failure |
| `diagnostics` | Timestamp and symlink-target diagnostic values / policy |
| `backend-registry` | `format_availability(ZIP)` FULL/PARTIAL from optional member codecs |
| `compressed-streams` | ZIP member-data decode path (method id → `StreamCodec`) |

## Requirements

### Requirement: Report ZIP format properties

The ZIP backend SHALL expose these properties for every opened ZIP archive:

| Property | Value |
| --- | --- |
| Backend dependency | `zipfile` (stdlib) for central-directory parsing; shared codec layer for member data |
| Listing cost | `ListingCost.INDEXED` — central directory read at open |
| Access cost | `AccessCost.DIRECT` — independent local file offsets |
| Stream capability | `StreamCapability.SEEKABLE` |
| Read source | Seekable only; no implicit buffering/spooling |
| Write support | No — writing is not shipped for any format (`PLAN.md` phase 9) |

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
method id to the codec's default backend. Central-directory parsing and
listing MAY continue to use stdlib `zipfile`.

Member reads SHALL verify `member.hashes["crc32"]` through the shared
`VerifyingStream` when a CRC is surfaced. A corrupt member body SHALL raise
`CorruptionError` (or `TruncatedError` when the payload is cut short) via the
shared translation. Traditional ZipCrypto and WinZip AES (method 99) members SHALL
decrypt natively (see below) and then feed the codec layer; stdlib `zipfile`
decodes no member data. A ZipCrypto member SHALL seek under `seekable_members=True`:
a backward seek restarts decryption from the member's start, a forward seek decrypts
what it skips, and `AUTO` accelerators stay off over the decrypt stage. A WinZip AES
member SHALL seek too: CTR restarts at the target's block (counter `1 + offset // 16`).
A seek SHALL NOT give up the HMAC, whether the caller or an accelerator made it: the
HMAC covers the ciphertext, so the read that returns the member's last byte SHALL
complete it by reading, without decrypting, the ciphertext the seeks skipped, and raise
`CorruptionError` on a mismatch. A read that stops short of the end gives no verdict.

#### Scenario: ZIP codec-layer decoding

| Case | Expected |
| --- | --- |
| STORED / DEFLATE / BZIP2 / LZMA member, unencrypted | Decodes via the shared codec layer; CRC verified through `VerifyingStream` |
| DEFLATE64 (method 9) member, `inflate64` backend present | Decodes; absent backend → `PackageNotInstalledError` |
| ZSTD (method 93) / PPMD (method 98) member, backend present | Decodes; absent backend → `PackageNotInstalledError` |
| Unsupported/unknown method id | `UnsupportedFeatureError`; no guessed output |
| Corrupt member body | `CorruptionError` / `TruncatedError` |
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
that every candidate passing it fails is reported as damaged. AE-2 members SHALL surface no
`crc32` (the ZIP CRC is 0; integrity is the HMAC) and run no CRC check; AE-1
members SHALL surface and verify `crc32` in addition to the HMAC. AES
decryption requires `cryptography` (`[recommended]`); when it is absent an AE member SHALL raise
`PackageNotInstalledError` (detection still identifies the member as
AES-encrypted). With several possible passwords, WinZip AES candidates SHALL be
confirmed as "Confirm multi-candidate ZipCrypto passwords" describes, not accepted on
the verification value alone.

#### Scenario: WinZip AES matrix

| Case | Expected |
| --- | --- |
| AE-1 or AE-2 member, 128/192/256, correct password, `cryptography` present | Decrypts, decompresses via codec layer, HMAC verified at EOF |
| Wrong password | `EncryptionError` on the 2-byte verification value; no bytes |
| Tampered ciphertext, correct password | HMAC mismatch → `CorruptionError` at terminal read |
| Tampered ciphertext, several candidates including the correct one | `CorruptionError` naming the member as most likely corrupt |
| Tampered ciphertext, partial read then `close()` | Quiet; `close()` is teardown, not a verdict (ADR 0014) |
| AE-2 member | `crc32` absent; no CRC check; HMAC is the integrity signal |
| AE-1 member | `crc32` present and verified alongside the HMAC |
| AES member without `cryptography` installed | `PackageNotInstalledError`; still reported as encrypted |
| Several candidates, a wrong one passing the verification value first | Wrong candidate rejected by the confirm; the right one reads |

### Requirement: Refuse PKWARE Strong Encryption

The ZIP backend SHALL NOT decrypt PKWARE Strong Encryption (APPNOTE §7). An
encrypted member (general-purpose bit 0) that also sets bit 6 or carries an extra
field `0x0017` SHALL list with `is_encrypted=True`, and opening it SHALL raise
`UnsupportedFeatureError` naming Strong Encryption, whatever passwords were given.
Listing a symlink of this kind SHALL leave `link_target` unset and emit
`SYMLINK_TARGET_UNAVAILABLE` with reason `"target_data_encrypted"`. When stdlib
cannot read the central directory and an archive extra data record
(`PK\x06\x08`) sits where stdlib reads the directory (the EOCD position minus
the recorded directory size), opening the archive SHALL
raise `UnsupportedFeatureError` naming Strong Encryption rather than
`CorruptionError`.

#### Scenario: Damaged central directory without the record

- **WHEN** stdlib cannot read the central directory and no archive extra data
  record sits where it reads it
- **THEN** opening raises `CorruptionError`, not `UnsupportedFeatureError`

### Requirement: Reject non-seekable ZIP read sources

The ZIP central directory is at EOF, so the ZIP reader SHALL raise
`StreamNotSeekableError` at open when reading from a non-seekable source. The
library MUST NOT buffer, spool, or copy a non-seekable ZIP source into seekable
storage implicitly. Callers must provide a seekable source or choose an access
path that does not require opening ZIP as random-access. Any future spooling
convenience must be an explicit opt-in, not default behavior.

#### Scenario: non-seekable read matrix

| Case | Expected |
| --- | --- |
| Non-seekable ZIP with default `streaming=False` | `StreamNotSeekableError` at open; no reader |
| Non-seekable ZIP with `streaming=True` | Still rejected because this backend cannot provide ZIP reading without seek |
| Implementation lacks seekable source | No implicit seekable-copy, temp-file, or spool fallback |

### Requirement: Map ZIP member metadata to ArchiveMember

The ZIP backend SHALL map each `ZipInfo` to `ArchiveMember` with these field
rules:

| Field | Mapping |
| --- | --- |
| `mode` | `external_attr >> 16` only for Unix entries with non-zero attrs; otherwise `None` |
| timestamps | DOS `date_time` base (naive local wall-clock, 2s granularity, 1980 sentinel → `None`); NTFS extra `0x000A` UTC FILETIMEs override present fields; Extended Timestamp `0x5455` UTC Unix times override present fields |
| `type` | Infer from Unix mode when available; otherwise directory marker and symlink hints |
| `compression` | `compress_type` mapped to `CompressionMethod` |
| `is_encrypted` | `flag_bits & 0x1 != 0` |

Invalid DOS or NTFS timestamp values SHALL fall through to the next valid
precedence layer or `None` and emit `MEMBER_TIMESTAMP_INVALID`. With
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
| `flag_bits & 0x1` | `member.is_encrypted is True` |
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
rejoin-first message rather than mis-read data or surface stdlib `BadZipFile`:
Info-ZIP `.zNN` segment names, non-zero classic EOCD disk fields (`0xFFFF` is the
ZIP64 sentinel, not a disk number), and ZIP64 locator `disks > 1`. Info-ZIP
`zip -s` writes a genuinely spanned
set addressed by `(disk, offset-within-disk)`, which stdlib `zipfile` cannot
resolve; a linear join lists correctly and then reads only whichever members
happen to sit on the last disk. It stays deferred to a native ZIP reader.

#### Scenario: multi-volume ZIP join and refusal

`open_archive` on a complete 7-Zip `.zip.NNN` or SFX `.exe.NNN` set — named at any
part — lists and
reads its members, including data spanning a part boundary. An incomplete such set
raises `TruncatedError`; every other split/spanned signal raises
`UnsupportedFeatureError`. Neither surfaces `CorruptionError`,
`FormatDetectionError`, or a raw stdlib `BadZipFile`.

### Requirement: Confirm multi-candidate ZipCrypto passwords

For traditional ZipCrypto, the per-open verification byte is weak and the
authoritative check is CRC-32 plus decompressor completion. When another
distinct candidate may be tried, the ZIP reader SHALL confirm a candidate that
passes the byte check before accepting it, following `archive-reading` weak-check
confirmation and bounded-storage rules. With one distinct static candidate
(duplicates included), the reader SHALL keep the normal lazy stream path, with no
confirmation read. Because only the verification byte vouched for that password, a
candidate failure (defined below) on the caller's `read`, `readinto` or forward
`seek` SHALL raise `EncryptionError` explaining that the password may be wrong or
the member may be corrupt, not `CorruptionError`; for an `LZMA` or `PPMd` member, whose
codec header is read when the member opens, the open raises it. It SHALL NOT be marked
as a wrong-password verdict: nothing in the archive tells a colliding wrong password
from a damaged member. A seek off the read frontier forfeits the CRC (`compressed-streams`,
ADR 0014), so a STORED member's seek does not raise; closing that stream emits
`ENCRYPTED_MEMBER_UNVERIFIED`.

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
it SHALL raise `CorruptionError` instead, as "Read WinZip AES-encrypted members" says.

Candidate failures SHALL be the `CorruptionError` a wrong key's garbage produces (a
codec rejection, a CRC or HMAC mismatch), and a codec's `TruncatedError` while the
member's whole payload is in the file. Local-header mismatch, bad local-header magic,
overlap and other structural failures raise before decryption starts and SHALL become
`CorruptionError` immediately, with no further password iteration; a payload the file
cuts short stays `TruncatedError`. `UnsupportedFeatureError`,
`PackageNotInstalledError`, `ResourceLimitError` and `OSError` values SHALL propagate
unchanged. Rejected-candidate streams SHALL be closed before trying the next candidate.

#### Scenario: ZipCrypto confirmation matrix

| Case | Expected |
| --- | --- |
| Wrong candidate passes verification byte before correct one (STORED / DEFLATE / BZIP2 / LZMA) | Wrong candidate rejected; fresh stream opened with correct candidate |
| One distinct static candidate | No confirmation read; member streams lazily |
| One distinct static candidate that passes the verification byte but is wrong, or right on a corrupt member | Caller `read`/`readinto`/forward `seek` of a compressed member raises `EncryptionError` saying the password may be wrong or the member corrupt (at open for `LZMA`/`PPMd`); no wrong-password mark |
| Same, STORED member, caller seeks and closes | Seek does not raise; `ENCRYPTED_MEMBER_UNVERIFIED` on close |
| Large compressed member | At most bounded prefix decompressed per candidate; no proportional plaintext storage; caller stream still checks CRC at EOF |
| STORED member with several surviving candidates | One shared ciphertext pass computes every candidate CRC; matching candidate accepted and reopened |
| Multiple STORED CRC matches | Earliest matching candidate in order wins |
| Corruption beyond confirmed prefix | Caller read raises what the codec layer raises for the same damage unencrypted (`CorruptionError` or `TruncatedError`) |
| ZipCrypto candidates fail confirmation | `EncryptionError` says password may be wrong or member corrupt; no bytes returned |
| WinZip AES candidates all fail after passing `pw_verify` | `CorruptionError`; no bytes returned |
| `OSError` from the source | Propagates unchanged; failed stream is closed |
| Structural local-header damage | `CorruptionError`; no further password iteration |
| One distinct static candidate, stream closed before EOF | Data returned; `ENCRYPTED_MEMBER_UNVERIFIED` (`check="weak_open_check"`) |
| Several candidates, STORED or compressed member within the prefix, stream closed before EOF | No diagnostic (the CRC confirmed the winner) |
| Several candidates, compressed member past the prefix, stream closed before EOF | `ENCRYPTED_MEMBER_UNVERIFIED` (`check="confirm_budget_exhausted"`); winner not added to known-good |
| WinZip AES member, wrong password | Rejected by `pw_verify`; HMAC remains the authoritative check |
| WinZip AES member, stream closed before EOF | `ENCRYPTED_MEMBER_UNVERIFIED` (`check="weak_open_check"`) |

### Requirement: Decode unflagged ZIP member names by UTF-8-validity sniff

The ZIP backend SHALL decode a member name whose general-purpose bit 11 (UTF-8/EFS flag) is
**clear** by first attempting UTF-8, and only falling back to a configurable legacy encoding
(default cp437, per APPNOTE) when the bytes are not valid UTF-8. This sniff SHALL apply only
in the absence of an authoritative encoding signal: a set bit 11 SHALL be honored as UTF-8,
and an explicit caller-supplied `encoding=` SHALL be used verbatim and SHALL disable the
sniff. When the sniff selects a non-default encoding — i.e. UTF-8 for an unflagged name — the
backend SHALL emit a `diagnostics` warning identifying the member and the chosen encoding, so
the decision is observable and escalatable via `DiagnosticPolicy`. Decoding SHALL NOT raise a
bare `UnicodeDecodeError`; the fallback encoding (cp437 by default) decodes every byte.

#### Scenario: UTF-8 bytes without the flag

- **WHEN** an archive stores a member name as valid UTF-8 bytes (e.g. `Español.txt`,
  `emoji_😀.txt`) with bit 11 **clear** and the caller passes no `encoding=`
- **THEN** the member name is decoded as UTF-8 (`Español.txt`, `emoji_😀.txt`), not cp437
  mojibake, and a diagnostic records that UTF-8 was inferred for an unflagged name

#### Scenario: Legacy bytes without the flag

- **WHEN** an unflagged member name is not valid UTF-8
- **THEN** it is decoded with the configured legacy fallback (default cp437), and no bare
  `UnicodeDecodeError` escapes

#### Scenario: Authoritative signal disables the sniff

- **WHEN** bit 11 is set, **or** the caller passed an explicit `encoding=`
- **THEN** the name is decoded as UTF-8 (flag) or with the caller's `encoding` respectively,
  with no sniff and no override diagnostic
