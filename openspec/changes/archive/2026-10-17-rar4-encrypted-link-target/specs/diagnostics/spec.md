## MODIFIED Requirements

### Requirement: Immutable diagnostic values with stable codes and safe typed context

Every advisory event SHALL be an immutable `Diagnostic`: opaque process-local
`occurrence_id`, stable `DiagnosticCode`, human `message`,
and code-specific frozen `DiagnosticContext`. Codes are the machine contract;
messages are not stable.

Contexts are a closed typed union of JSON-safe immutable scalar/tuple values.
Raw bytes use an explicitly named base64 field. `to_dict()` on diagnostic and
context SHALL be `json.dumps`-safe without a custom encoder.

| Code | Variant and required fields |
| --- | --- |
| `MEMBER_NAME_NORMALIZED` | `NameNormalizationContext`: `kind="name_normalization"`, `archive_name`, `member_name`, `member_id`, `raw_name_base64`, `presented_name`, `normalized_name` |
| `MEMBER_NAME_ENCODING_INFERRED` | `NameEncodingContext`: `kind="name_encoding"`, `archive_name`, `member_name`, `member_id`, `raw_name_base64`, `inferred_encoding`, `declared_encoding` |
| `MEMBER_NAME_BIDI_CONTROL` | `MemberNameControlsContext`: `kind="member_name_controls"`, `archive_name`, `member_name`, `member_id`, `raw_name_base64`, `controls`, `field` (`"name"` or `"link_target"`: which text carries the controls; emitted once per member for each), `category="bidi"` |
| `MEMBER_NAME_ZERO_WIDTH_CHARACTER` | `MemberNameControlsContext` as for `MEMBER_NAME_BIDI_CONTROL`, with `category="zero_width"`: `controls` lists the occurrences of U+200B, U+2060 and U+FEFF |
| `MEMBER_NAME_INVISIBLE_CHARACTER` | `MemberNameControlsContext` as for `MEMBER_NAME_BIDI_CONTROL`, with `category="invisible"`: `controls` lists the other characters of Unicode category Cf that are not bidi controls (for example U+00AD, U+200C, U+200D, the tag characters) |
| `FORMAT_EXTENSION_CONFLICT` | `FormatConflictContext`: `kind="format_conflict"`, `source_name`, `extension`, `extension_format`, `detected_format` |
| `EXPLICIT_FORMAT_LISTED_EMPTY` | `UnconfirmedFormatContext`: `kind="unconfirmed_format"`, `archive_name`, `format`, `chosen_by="argument"`, `detected_format` |
| `EXTENSION_FORMAT_UNCONFIRMED` | `UnconfirmedFormatContext`: `kind="unconfirmed_format"`, `archive_name`, `format`, `chosen_by="extension"`, `detected_format=None` |
| `PROBE_FORMAT_UNCONFIRMED` | `UnconfirmedFormatContext`: `kind="unconfirmed_format"`, `archive_name`, `format`, `chosen_by="content_probe"`, `detected_format` |
| `EMPTY_ARCHIVE` | `EmptyArchiveContext`: `kind="empty_archive"`, `archive_name`, `format` |
| `ENCODING_ARGUMENT_UNUSED` | `UnusedArgumentContext`: `kind="unused_argument"`, `archive_name`, `argument="encoding"`, `format`, `reason` |
| `PASSWORD_ARGUMENT_UNUSED` | `UnusedArgumentContext`: `kind="unused_argument"`, `archive_name`, `argument="password"`, `format`, `reason` |
| `SCAN_DIRECTORY_VANISHED` | `ScanRaceContext`: `kind="scan_race"`, `archive_name`, `relative_path`, `entry_kind="directory"` |
| `SCAN_ENTRY_VANISHED` | `ScanRaceContext`: `kind="scan_race"`, `archive_name`, `relative_path`, `entry_kind="entry"` |
| `ARCHIVE_EOF_MARKER_MISSING` | `ArchiveEofContext`: `kind="archive_eof"`, `archive_name`, `format`, `expected_marker` ∈ `{"two_zero_blocks","second_zero_block","end_of_archive_block","end_of_central_directory","zip64_end_of_central_directory","central_directory"}`, `expected_bytes`, `observed_bytes`, `observed_kind` |
| `ARCHIVE_TRAILING_DATA` | `ArchiveEofContext`: `kind="archive_eof"`, `archive_name`, `format`, `expected_marker` ∈ `{"zeros_to_eof","end_of_stream"}`, `expected_bytes=0`, `observed_bytes`, `observed_kind="nonzero"` |
| `ARCHIVE_UNLISTED_DATA` | `UnlistedDataContext`: `kind="unlisted_data"`, `archive_name`, `format`, `member_name`, `member_id` (the name and id the pass gave the entry before dropping it), `offset` (of its local header), `compressed_size` |
| `MEMBER_TIMESTAMP_INVALID` | `MemberTimestampContext`: `kind="member_timestamp"`, `archive_name`, `member_name`, `member_id`, `field` ∈ `{"modified","accessed","created","ctime"}` (the `ArchiveMember` attribute the value would have filled, in every format), `source`, `value_repr` |
| `MEMBER_HEADER_RECORD_SKIPPED` | `MemberHeaderRecordContext`: `kind="member_header_record"`, `archive_name`, `member_name`, `member_id`, `record`, `record_id`, `reason`, `list_truncated` |
| `SYMLINK_TARGET_UNAVAILABLE` | `SymlinkTargetContext`: `kind="symlink_target"`, `archive_name`, `member_name`, `member_id`, `reason` |
| `DIGEST_UNVERIFIABLE` | `DigestContext`: `kind="digest"`, `archive_name`, `member_name`, `member_id`, `algorithm`, `reason` |
| `MEMBER_DIRECTORY_DATA_IGNORED` | `DirectoryDataContext`: `kind="directory_data"`, `archive_name`, `member_name`, `member_id`, `size` (declared; 0 when none is declared and the body is larger than an empty one of its method), `compressed_size` |
| `MEMBER_SPECIAL_FILE_HAS_DATA` | `SpecialFileDataContext`: `kind="special_file_data"`, `archive_name`, `member_name`, `member_id`, `special_file_type` ∈ `{"fifo","char_device","block_device","socket","unknown"}`, `size` |
| `MEMBER_LOCAL_NAME_DIFFERS`, `MEMBER_LOCAL_CRC_USED` | `LocalHeaderContext`: `kind="local_header"`, `archive_name`, `member_name`, `member_id`, `field` (`"name"` for the first, `"crc32"` for the second), `local_raw_name_base64` (the local header's name bytes, `field="name"` only), `local_crc32` (`field="crc32"` only) |
| `ARCHIVE_OFFSET_WRAPPED` | `OffsetWrapContext`: `kind="offset_wrap"`, `archive_name`, `format`, `member_name`, `member_id` (empty and `None` for the directory), `record` ∈ `{"local_header","central_directory"}`, `stored_offset`, `corrected_offset` |
| `MEMBER_SIZE_WRAPPED` | `SizeWrapContext`: `kind="size_wrap"`, `archive_name`, `format`, `member_name`, `member_id`, `stored_compressed_size`, `compressed_size`, `stored_size`, `size` (`None` when only a read can tell) |
| `ENCRYPTED_MEMBER_UNVERIFIED` | `EncryptedVerificationContext`: `kind="encrypted_verification"`, `archive_name`, `member_name`, `member_id`, `check`, `reason` |
| `SEEK_INDEX_DEGRADED` | `SeekIndexContext`: `kind="seek_index"`, `archive_name`, `member_name`, `member_id`, `codec`, `scan`, `error_type` |
| `STREAM_REWIND_REDECOMPRESSES` | `StreamRewindContext`: `kind="stream_rewind"`, `archive_name`, `member_name`, `member_id`, `codec`, `from_offset`, `to_offset`, `accelerator` |

(`str | None` / `int | None` as in the typed variants.) `DiagnosticContext` is
exactly this union — no backend-defined variants. `observed_kind` ∈
`{"absent","short","nonzero"}`. `expected_marker` is symbolic (`"two_zero_blocks"`
for the trailer check; `"second_zero_block"` for a TAR trailer whose first block is
zero and ends the members and whose second block is full and non-null, with
`observed_kind="nonzero"` and both byte counts 512, where every member is listed;
`"end_of_archive_block"` for a RAR5 archive or volume that ends without its end-of-archive
block, with `format="rar"`, `observed_kind="absent"` and both byte counts 0, or whose
end-of-archive block fails its header CRC, with `observed_kind="nonzero"` (a block is
there, but it is not a valid end block), `observed_bytes` the offset where that block
starts in its volume (counted from that volume's first byte, unlike member offsets,
which count across the whole set; the message names the volume), and `expected_bytes` 0;
`"end_of_central_directory"` for a ZIP end record that does not match the archive, with
`format="zip"`: `observed_kind="nonzero"` when its entry count differs from the central
directory's (`observed_bytes` the record's offset, `expected_bytes` 0), or
`observed_kind="short"` when its comment length runs past the end of the file
(`expected_bytes` 22 plus the declared comment length, `observed_bytes` the bytes from
the record to the end of the file); `"zip64_end_of_central_directory"` for a ZIP64 end
record a locator announces that is missing or does not match it, the classic record
used instead (`observed_kind="absent"`, `observed_bytes` the locator's offset,
`expected_bytes` 0); `"central_directory"` for a ZIP central-directory
entry whose name, extra field or comment length runs past the directory size the end
record gives, with `observed_kind="nonzero"`, `expected_bytes` that size and
`observed_bytes` where the entry would end, both counted from the directory's start;
`"zeros_to_eof"` for the trailing-bytes check after an archive's end (a TAR
trailer, a 7z next header or last packed stream, whichever is later, a RAR
end-of-archive block, an ISO image's volume space and partitions, or a ZIP end record
and its declared comment), whose `format`
names the archive format and whose `observed_bytes` is the offset of the first non-zero
byte past that end;
`"end_of_stream"` for bytes after a compressed stream's end, whose `format` is the
codec name, such as `"gzip"`, and whose `observed_bytes` is the offset of the first
non-zero byte after that end).
`member_id` MAY be `None` only before registration.
`controls` SHALL be the comma-joined `U+XXXX` spellings of the bidi codepoints
found, in the order they occur, so a caller can tell an override from a mark
without re-scanning the name. `chosen_by` ∈ `{"argument","extension","content_probe"}`;
`detected_format` is `None` when detection refuses the bytes outright.

`PROBE_FORMAT_UNCONFIRMED` is a **separate code**, not a widening of
`EXTENSION_FORMAT_UNCONFIRMED`. The two describe different provenance and fire on
different events: the extension code keys on `detected_by="extension"` **and an empty
listing or a decode failure** (see `error-handling`, *A decode failure on an
extension-only format names its provenance*); the probe code keys on **probe-only
provenance** — `detected_by="content_probe"` with nothing corroborating the claim (no
matching extension, no inner-TAR upgrade) — **and a decode failure**. A probe-only read
failure MUST NOT double-report under the extension code.

**Confidence is not part of that trigger.** The probe code fires on a probe-only failure at
*any* `DetectionConfidence`, so an LZMA Alone hit (always `PROBABLE`) and a compressed-first
Brotli hit (`PROBABLE`) are stamped exactly as an uncorroborated `GUESS` hit is. Confidence
grades how strong the evidence looked; this code answers whether anything independent
corroborated it. Keying the diagnostic on confidence left 53% of measured fabrications
unsignalled, and made a probe's confidence value double as a switch for error behaviour.
This matches `error-handling`'s `format_unconfirmed`, and the two SHALL agree by
construction: the diagnostic is emitted while stamping that attribute, never on a separate
test.

`ExtractionOutcomeContext`, `NameCollisionContext` and `NameSanitizedContext` SHALL
NOT exist: per-member extraction outcomes are carried by `ExtractionResult` (see
`safe-extraction`), not by this channel.

No diagnostic surface SHALL contain passwords, candidates, provider returns, keys,
KDF material, or decrypted secrets. `PASSWORD_ARGUMENT_UNUSED` therefore records
that a password argument was supplied and unused — never how many candidates, and
never any candidate value.

Copies on multiple surfaces MAY share `occurrence_id` by value; object identity
and cross-run id stability are not promised.

`ENCRYPTED_MEMBER_UNVERIFIED` SHALL be emitted when a member of an encrypted unit is
closed before its declared digest was reached **and** the password behind those bytes was
accepted on a check weaker than that digest. `check` names what accepted the password
(`"weak_open_check"`, `"confirm_budget_exhausted"`); `reason` names why the digest was
not reached (`"partial_read"`, or `"seek"` when a seek left it unreached: a read skipped
bytes past the hashing frontier and no read from it reached the end, or the stream was
closed while parked past it; a WinZip AES
HMAC survives seeks, so there `"seek"` means only that a failed seek left the position
unknown). It SHALL NOT be emitted for a partial read whose password was confirmed
against an integrity anchor — that restates what the caller already knows, which the
admission clause refuses. Nor SHALL it be emitted for a stream closed before any read
returned bytes, since nothing unchecked was delivered.

#### Scenario: value-model matrix

| Case | Expected |
| --- | --- |
| Name normalization | `MEMBER_NAME_NORMALIZED` + typed JSON-safe context; no backend/mutable mapping |
| Same occurrence on aggregate + member | Same `occurrence_id`; value equality; no object-identity promise |
| Encrypted symlink unavailable | May use reason `"password_required"` (or `"password_or_damage"` when a ZIP or RAR 2.9-4 target's data failed its check under an unconfirmed password) + member name; no secret material |
| Member blocked by a universal/policy check | No diagnostic; a `BLOCKED` `ExtractionResult` is the whole record |
| `password=["a","b"]` on a format with no encryption | `PASSWORD_ARGUMENT_UNUSED`; context carries no candidate value and no count |
| Non-zero byte within 1 MiB past a complete TAR trailer | `ARCHIVE_TRAILING_DATA` sharing `ArchiveEofContext`; distinguished by `expected_marker` |
| Non-zero byte within 1 MiB past a 7z archive's end (next header or last packed stream) | `ARCHIVE_TRAILING_DATA` with `expected_marker="zeros_to_eof"` and `format="7z"` |
| Non-zero byte within 1 MiB past a RAR volume's end-of-archive block | `ARCHIVE_TRAILING_DATA` with `expected_marker="zeros_to_eof"` and `format="rar"`, once per volume |
| Non-zero byte within 1 MiB past an ISO image's volume space and listed partitions | `ARCHIVE_TRAILING_DATA` with `expected_marker="zeros_to_eof"` and `format="iso"` |
| Non-zero byte past a ZIP end record and its declared comment | `ARCHIVE_TRAILING_DATA` with `expected_marker="zeros_to_eof"` and `format="zip"` |
| A ZIP read from a pipe holds a local entry its central directory does not list | `ARCHIVE_UNLISTED_DATA` naming the entry; the entry is dropped from the listing, as the seekable read never sees it |
| Non-zero bytes after a single-file codec's stream | `ARCHIVE_TRAILING_DATA` with `expected_marker="end_of_stream"` and the codec name as `format` |
| Probe-only single-file read raises, uncorroborated `GUESS` | `PROBE_FORMAT_UNCONFIRMED` with `chosen_by="content_probe"` |
| Probe-only single-file read raises, uncorroborated **`PROBABLE`** (compressed-first Brotli) | `PROBE_FORMAT_UNCONFIRMED` too — **changed**; confidence is not the trigger |
| Probe-only **LZMA Alone** read raises (always `PROBABLE`) | `PROBE_FORMAT_UNCONFIRMED` — **changed**; previously unsignalled |
| Extension-only empty listing | Still `EXTENSION_FORMAT_UNCONFIRMED` only — unchanged |
| Extension-only read raises (zeros named `backup.gz`) | `EXTENSION_FORMAT_UNCONFIRMED` with `chosen_by="extension"`, `detected_format=None`; no `PROBE_FORMAT_UNCONFIRMED` |
| Probe + `.br` (`PROBABLE`) read raises | No `PROBE_FORMAT_UNCONFIRMED` — the format was corroborated, and corroboration is still what matters |
| Probe hit upgraded to `TAR_*` by the inner-TAR probe, read raises | No `PROBE_FORMAT_UNCONFIRMED` — the upgrade is independent corroboration |
| Probe-only read succeeds | No diagnostic |

#### Scenario: encrypted-member verification matrix

| Case | Expected |
| --- | --- |
| ZipCrypto member, candidate accepted on the header check byte, stream closed before EOF | `ENCRYPTED_MEMBER_UNVERIFIED` (`check="weak_open_check"`, `reason="partial_read"`) |
| 7z LZMA2, CRC at 200 MiB, correct password, stream closed before EOF | `ENCRYPTED_MEMBER_UNVERIFIED` (`check="confirm_budget_exhausted"`) |
| 7z store+AES, CRC at 200 MiB, correct password, stream closed before EOF | No diagnostic (walked to the CRC) |
| 7z store+AES, no CRC, single candidate, stream closed before EOF | `ENCRYPTED_MEMBER_UNVERIFIED` (`check="confirm_budget_exhausted"`) |
| Encrypted member whose password was confirmed against an integrity anchor, stream closed before EOF | No diagnostic |
| Encrypted member read to EOF | No diagnostic; the digest decides |
| Unencrypted member, stream closed before EOF | No diagnostic |

#### Scenario: A malformed optional member-header record is reported, not raised

- **WHEN** a backend drops an optional metadata record inside a member header, or in a
  header that belongs to one member (a RAR 1.5-4 `UOW` owner header), because it could
  not be parsed, and lists the member without it
- **THEN** it SHALL emit `MEMBER_HEADER_RECORD_SKIPPED` with `MemberHeaderRecordContext`,
  attached to that member
- **AND** `record` SHALL name the record as the format names it, and `record_id` SHALL
  carry the format's numeric type where it has one, so a record the backend cannot name is
  still identifiable
- **AND** the field the record would have populated SHALL be absent rather than partially
  written: a dropped record never changes a value, it only fails to set one
