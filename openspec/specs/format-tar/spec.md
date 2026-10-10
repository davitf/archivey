# TAR Format Behavior

## Purpose

TAR archives (`.tar`, `.tar.gz`, `.tar.bz2`, `.tar.xz`, `.tar.zst`) are read
through the unified archive APIs with archivey's own header parser, over the source
or archivey's decompressor for the codec. TAR has no central directory: listing walks
headers sequentially, compressed variants are solid streams, and extraction preserves
TAR-specific hardlink and EOF semantics.

## Related specs

| Spec | Relationship |
| --- | --- |
| `archive-reading` | Reader API, link-following semantics, declared member-stream capabilities |
| `access-mode-and-cost` | Cost axes and streaming vs random-access method rules |
| `safe-extraction` | Pull-based extraction coordinator, `OnError`, hardlink outcomes |
| `diagnostics` | Timestamp and archive-EOF diagnostic values / policy |
| `reader-concurrency` | `MemberStreams.CONCURRENT`, operation ownership, lock boundaries |

## Requirements

### Requirement: Report TAR format properties

The TAR backend SHALL expose these properties for every opened TAR archive:

| Format | Read from | Listing cost | Access cost |
| --- | --- | --- | --- |
| Plain `.tar` | The source | `REQUIRES_SCANNING` | `DIRECT` |
| `.tar.gz` | archivey's gzip decompressor | `REQUIRES_DECOMPRESSION` | `SOLID` |
| `.tar.bz2` | archivey's bzip2 decompressor | `REQUIRES_DECOMPRESSION` | `SOLID` |
| `.tar.xz` | archivey's xz decompressor | `REQUIRES_DECOMPRESSION` | `SOLID` |
| `.tar.zst` and every other codec | archivey's decompressor for it | `REQUIRES_DECOMPRESSION` | `SOLID` |
| Any of the above with `streaming=True` | The same stream, forward only | As above | As above |

TAR is read-only here: writing is not shipped for any format (`dev-docs/investigations/archive-writing-design.md`).
Compressed variants remain solid even when the source is seekable: random member
opens may re-decompress earlier bytes, while `stream_members()` is the preferred
progressive path.

#### Scenario: TAR property matrix

| Case | Expected |
| --- | --- |
| Open `TAR` | `cost.listing_cost=REQUIRES_SCANNING`; `cost.access_cost=DIRECT`; members are slices of the source |
| Open `TAR_GZ`, `TAR_BZ2`, `TAR_XZ`, or `TAR_ZST` | `cost.listing_cost=REQUIRES_DECOMPRESSION`; `cost.access_cost=SOLID`; archivey's decompressor for that codec |
| Open plain `.tar` | No decompression wrapper |

### Requirement: Map TAR member metadata to ArchiveMember

The TAR backend SHALL map each parsed member (its header, with the PAX records and GNU
long names in force applied) to `ArchiveMember` with these field rules:

| Field | Mapping |
| --- | --- |
| `mode` | The header's `mode`, lower 12 bits |
| `modified` | The header's `mtime` as timezone-aware UTC |
| PAX `mtime` | Overrides the header's `mtime`, preserving sub-second precision / timezone information |
| `uname`, `gname`, `uid`, `gid` | From the header; a PAX record of the same name overrides it (a PAX `uid` or `gid` that is not a number is ignored) |
| `type` | TAR type byte (`REGTYPE`, `DIRTYPE`, `SYMTYPE`, `LNKTYPE`, etc.) to `MemberType` |
| hardlink target | `LNKTYPE` maps to `MemberType.HARDLINK`; `link_target` from the PAX `linkpath`, else the GNU long link name, else the header's `linkname` |
| old-style directory | An `AREGTYPE` (typeflag NUL) header whose final name (after a PAX `path` or a GNU long name) ends in `/` is a `DIRECTORY`, on every Python version, and the data blocks its `size` declares are skipped, as GNU tar does. `extra["tar.type"]` is the stored `b"\x00"`. A `DIRTYPE` header that declares a size makes the listing raise `CorruptionError`; in random access no member is listed. GNU tar reports an error and keeps listing; 7-Zip stops |
| `extra["tar.pax_headers"]` | The member's PAX records, the global (`g`) records in force included. Read-only: a change raises `TypeError`. Members with no records of their own share one per set of global records: one copy per member cost the global records again for every 512-byte member header. Read-only so the sharing cannot be seen: a change through one member could otherwise show on the others. It is a `dict` subclass, so `json.dumps` takes it, and a copy, deep copy or pickle round trip gives a plain `dict` |
| `raw_name` | The stored bytes of whatever supplied the name: the PAX `GNU.sparse.name` or `path` record, else the GNU long name, else the header's `name` (with the `prefix` field joined, under the ustar magic only). Never `None` |

If the header's `mtime` cannot be represented as a Python `datetime`, `modified`
SHALL be `None` and `MEMBER_TIMESTAMP_INVALID` SHALL be emitted with typed,
JSON-safe member identity and source/value context. Under default policy it is
collected/logged and may attach to the member; under `RAISE`, listing halts with
`DiagnosticRaisedError`. The same SHALL hold for a PAX `mtime`, `atime`, `ctime` or
`LIBARCHIVE.creationtime` record that is not a number or is out of range: the field it
fills SHALL be `None` and `MEMBER_TIMESTAMP_INVALID` SHALL be emitted. Its `field`
SHALL name the member attribute (`modified`, `accessed`, `ctime`, `created`), as
in every format; the record name appears only in the message.

#### Scenario: TAR metadata matrix

| Case | Expected |
| --- | --- |
| PAX `mtime` present | `member.modified` derives from PAX value, overriding the header's `mtime` |
| No PAX `mtime` | `member.modified` is timezone-aware UTC from the header's `mtime` |
| PAX `ctime` present | `ctime` is timezone-aware UTC; it never fills `created` |
| PAX `LIBARCHIVE.creationtime` present (bsdtar, where the OS has a birth time) | `created` is timezone-aware UTC |
| Neither PAX record | `created is None` and `ctime is None` |
| `LNKTYPE` entry | `member.type=MemberType.HARDLINK`; `member.link_target=linkname` |
| `AREGTYPE` entry `d/` with 15 bytes of data, then a file | `d/` is a `DIRECTORY`; the file after it lists and reads, in both access modes. The same holds when the slash comes from a PAX `path` or a GNU long name |
| PAX name `日本語.txt`, `encoding="latin-1"` | Lists; `raw_name` is the UTF-8 bytes the PAX record holds |
| ustar name, `encoding="latin-1"` | `raw_name` is the latin-1 bytes |
| PAX `path` holding the non-UTF-8 bytes `caf\xe9.txt`, `encoding="latin-1"` | `raw_name == b"caf\xe9.txt"` |
| Old GNU or v7 header with bytes at offsets 345 to 500 (GNU incremental `tar -G` fills `atime` there) | Not joined to the name: `d/f.txt`, not `15262452373/d/f.txt` |
| PAX global header, then members with no records of their own | Each member's `extra["tar.pax_headers"]` holds the global records, and a later global header does not change it. Changing it raises `TypeError` |
| PAX sparse 1.0 map holding a number longer than 20 digits | `CorruptionError` while the header is parsed. GNU tar reads each number into a 20-digit buffer and refuses a longer one; this structural bound keeps a number with no newline from growing one buffer for the rest of the archive |
| Out-of-range `mtime` | `modified is None`; `MEMBER_TIMESTAMP_INVALID` counted and may attach |
| PAX `atime`, `ctime` or `LIBARCHIVE.creationtime` not a number or out of range | That field is `None`; `MEMBER_TIMESTAMP_INVALID` counted with `field` set to the member attribute it would have filled |
| PAX `mtime` not a number | `modified is None`, not the Unix epoch; `MEMBER_TIMESTAMP_INVALID` counted |
| Timestamp diagnostic resolves to `RAISE` | Listing halts with `DiagnosticRaisedError` |

### Requirement: Extract TAR hardlinks with a pull-based coordinator

The system SHALL support TAR hardlink extraction through the `safe-extraction`
coordinator as a pull-based sink: it drives the reader forward, may inspect
`members_report_if_available()` only when that report is free, and checks re-read
possibility only if an orphaned hardlink exists. It MUST NOT use a push-model
deferred-state machine, force an upfront scan, or depend on the
`SOLID`/`DIRECT` axis for correctness.

Only a `members` selector or `filter` can orphan a hardlink by selecting the link
while excluding its source. Unfiltered extract-all SHALL resolve TAR hardlinks in
one sequential pass because the source precedes the link.

A TAR hardlink SHALL resolve only to a member before it (the last one of that name before
it, as GNU tar resolves it), in random access as in a streaming pass. A hardlink whose
only same-named member comes after it has no `link_target_member`; opening it raises
`LinkTargetNotFoundError`, and extraction fails it the same way. The `linkname` is
looked up as a member name, `..` and a leading `/` included, and never checked as a
path; a hardlink whose source extraction refuses is refused with it (`safe-extraction`,
"Hardlink Two-Pass Extraction").

The core algorithm SHALL write selected members in one forward pass, recording
every written FILE path per source. A selected hardlink to an already-written
source is created with `os.link()`. If a selector/filter orphans a selected link:

| Source capability | Behavior |
| --- | --- |
| Re-readable / random-access | Collect orphan links and resolve all of them in one second pass after the main pass; plain TAR re-scans headers, compressed TAR re-decompresses at most once more |
| Forward-only | Treat as a per-member failure under configured `OnError` (`STOP` raises `ExtractionError`; `CONTINUE` records `FAILED` and proceeds) |

When a free member list is available and a selector/filter is in use, the
coordinator MAY plan up front: apply selection/filter policy, write an excluded
source's bytes to the first selected link path while they stream past, and
`os.link()` remaining selected links to that staged path. This optimization SHALL
not create the excluded source at its own name and SHALL not replace the core
correctness path.

For cross-device links, the coordinator SHALL try `os.link()` against the recorded
on-disk paths for the source, newest first. If all fail with `EXDEV`, or one fails with
`EMLINK` (Windows `winerror` 1142) because the file already has as many links as the
filesystem allows (1024 names on NTFS, the first included), it SHALL copy from an
existing copy and append the new path for reuse, so an archive with more links than NTFS
allows extracts on every OS. Chained links on that device can then link to the sibling
copy. Device bookkeeping MAY skip doomed attempts but is not required for correctness.

#### Scenario: TAR hardlink extraction matrix

| Case | Expected |
| --- | --- |
| Unfiltered extract-all | Hardlinks resolve in one pass; no upfront member list fetch |
| Filter excludes source but selects link and a free member list exists | One planned pass writes source bytes to first selected link path; remaining links use `os.link`; source name not created |
| Filter orphans links on seekable plain/compressed TAR with no free list | No speculative scan; all orphans resolved in one second pass; compressed stream decompressed at most twice total |
| Filter does not orphan any link | Single pass; no second pass; no upfront list fetch |
| Orphaned link on forward-only source | Per-member failure follows `OnError` |
| `B -> A` copied cross-device, then `C -> A` on B's device | `C` is created with `os.link(B, C)` rather than copying A again |
| Every recorded path fails with `EXDEV` | Copy source content to link destination and record that path |
| A recorded path fails with `EMLINK` (the 1025th name for one file on NTFS) | Same copy, with no path older than that one tried; later links link to the copy |
| Hardlink before the only member it names, random access or streaming | That link fails with `LinkTargetNotFoundError`; the later member extracts normally |
| Hardlink to `../x` (any policy) or `/x` (`STRICT`), selected or not, either mode | `BLOCKED` ("Hardlink target was refused"); never orphaned, so the second pass never writes `../x`'s bytes |

### Requirement: Detect truncated TAR archives

After full iteration, a missing or invalid TAR end marker SHALL emit
`ARCHIVE_EOF_MARKER_MISSING` on the reader operation aggregate and SHALL NOT
attach to `ArchiveInfo`, `CostReceipt`, or a member. Context SHALL be
`ArchiveEofContext(kind="archive_eof", format="tar",
expected_marker="two_zero_blocks", expected_bytes=1024, observed_bytes=...,
observed_kind=...)` plus best-effort archive display name. `observed_kind` SHALL
be `"absent"`, `"short"`, or `"nonzero"`; raw trailing bytes SHALL NOT be
retained. The damaged second trailer block (below) SHALL instead carry
`expected_marker="second_zero_block"`, `expected_bytes=512`, `observed_bytes=512`,
`observed_kind="nonzero"`, so that a whole listing is told from a shortened one by
the context: TAR's `"two_zero_blocks"` with `observed_kind="nonzero"` is always
escalated to `CorruptionError`.

The backend SHALL classify the end of the archive by why its header walk stopped (on a
zero block, on a block that is not a header, or at the end of the stream, whole or inside
a block), then by the block after the stop, rather than by a single monolithic flag.
That reason is the same in both access modes, so the classification SHALL NOT depend on
the access mode:

- **Rejected header → `CorruptionError`, whatever the diagnostic policy.** When the
  walk stopped on a header that does not parse (a bad checksum or number field, a
  negative size, PAX records that do not parse, a bad number in an old GNU sparse
  extension block, or an extended header followed by a block that is not a header), ending the
  listing there would shorten it silently. A
  conformant, complete tar never produces this (its two-or-more null trailer blocks
  end the scan first). It SHALL be `CorruptionError` in **both** access modes whatever
  follows the rejected header: more members, nothing (the bad header is the archive's
  **final** block), or a zero block (a member whose data starts with 512 zero bytes,
  which would otherwise read as the second trailer block). Emitted with
  `observed_kind="nonzero"` after the diagnostic's normal
  count/retention/log/callback ordering, then escalated to `CorruptionError`.
- **Damaged second trailer block → ordinary diagnostic.** When the walk stopped on a
  zero block (the first trailer block) after at least one member, and the block after
  it is full and non-null, the listing is whole and only the end-of-archive marker is
  damaged. Every member SHALL be listed and readable in both access modes, and the
  backend SHALL emit `ARCHIVE_EOF_MARKER_MISSING` with
  `expected_marker="second_zero_block"` and `observed_kind="nonzero"` under ordinary
  diagnostic disposition, with no escalation of its own: a warning by default,
  `DiagnosticRaisedError` after delivery when the code resolves to `RAISE` (as under
  `DiagnosticPolicy.strict()`), a count alone under `IGNORE`. GNU tar ("A lone zero
  block") and 7-Zip list the same archive with a warning. The backend SHALL tell this
  case from a rejected header by why the walk stopped, not by the bytes read, so it
  holds in streaming too. With no member before the zero block
  the non-null block stays `CorruptionError`, with `expected_marker="two_zero_blocks"`
  and a message that names that cause rather than a rejected header.
- **Missing / short trailer → ordinary diagnostic.** A stream that ended cleanly on a member
  boundary with no valid two-block trailer (`observed_kind="absent"` for EOF,
  `"short"` for a partial block) is the irreducibly ambiguous residual: a
  complete-but-trailer-less tar and a tar truncated exactly at a member boundary are
  byte-identical, so no reader can tell them apart. It SHALL follow ordinary diagnostic
  disposition with no escalation of its own: a warning by default,
  `DiagnosticRaisedError` after delivery when the code resolves to `RAISE` (as under
  `DiagnosticPolicy.strict()`), a count alone under `IGNORE`.

The rejected-header escalation to `CorruptionError` SHALL take precedence over
`DiagnosticRaisedError`, including when the diagnostic disposition is `IGNORE` or
`RAISE`. Logging-handler or callback exceptions propagate at their earlier ordered step.

The archive-level EOF check runs at the end of the member scan, so its escalation is a
terminal listing error carried through the `partial-members-and-errors` report model:

- `members()` / `scan_members()` are complete-or-raise — they raise the stored escalation.
- `members_report()` (and `members_report_if_available()`) return the recovered prefix plus
  the terminal `error`, so a caller can still inspect the salvageable members.
- `__iter__` (both access modes) yields the recovered members, then raises.
- `extract_all` runs one forward pass in **both** access modes: random access enforces the
  listing limits as members arrive instead of listing first, so a compressed tar is
  decoded once. The EOF check therefore runs at the end of that pass: the salvageable
  members are written first, and then the call raises. It does not fail closed.

The check SHALL raise the escalation from the member scan (so the report model records it as
`error`); it SHALL NOT record the archive-level EOF only on a separate report field.

Truncation *inside* a member's data or across a partial header block is out of scope of
this end-of-marker check: it raises `TruncatedError` **during iteration**, whatever the
diagnostic policy, in both random-access and streaming modes.

#### Scenario: TAR EOF matrix

| Case | Mode | `observed_kind` | Default policy | Code set to `RAISE` |
| --- | --- | --- | --- | --- |
| Valid two-block null marker (incl. minimal `tar -b1`, trailing record padding) | both | — (OK) | No diagnostic or error | No diagnostic or error |
| Missing marker / truncated at member boundary | both | `absent` | `ARCHIVE_EOF_MARKER_MISSING`; pass completes | `DiagnosticRaisedError` after delivery |
| Partial trailing block | both | `short` | Warn as above; pass completes | `DiagnosticRaisedError` after delivery |
| Rejected non-first header, data follows | both | `nonzero` | `CorruptionError` after delivery | `CorruptionError` after delivery |
| Rejected non-first header, a zero block follows (member data starting with 512 zero bytes) | both | `nonzero` | `CorruptionError` after delivery; later members are not listed | `CorruptionError` after delivery |
| Rejected **final** header, nothing after (incl. after a GNU sparse member) | both | `nonzero` | `CorruptionError` after delivery | `CorruptionError` after delivery |
| Zero block, then a non-null block, after at least one member | both | `nonzero` (`expected_marker="second_zero_block"`) | `ARCHIVE_EOF_MARKER_MISSING`; every member listed and read; `extract_all` writes every member; trailing scan runs past the block | `DiagnosticRaisedError` after delivery |
| Zero block, then a non-null block, no member | both | `nonzero` | `CorruptionError` after delivery | `CorruptionError` after delivery |
| Truncation inside member data / partial header | both | — | `TruncatedError` during iteration | `TruncatedError` during iteration |
| A size field puts the next header past the largest file the filesystem holds (base-256 size of 2**62): ext4 refuses the seek, APFS and a `BytesIO` take it | both | — | `TruncatedError` during iteration, from every source on every OS | `TruncatedError` during iteration |
| Corruption during `extract_all` | both | `nonzero` | Salvageable members written, then `CorruptionError` | same |
| Diagnostic code resolves to `IGNORE`, rejected header | both | `nonzero` | Count increments without delivery; `CorruptionError` raises | same |
| Diagnostic code resolves to `IGNORE`, `absent`/`short` | both | `absent`/`short` | Count increments without delivery; no error | — |
| Marker issue discovered after iteration | both | any | `reader.diagnostics` changes; frozen `ArchiveInfo` / `CostReceipt` unchanged | same |

### Requirement: Serialize shared TAR handle operations for concurrent reads

For random-access TAR readers that allow concurrent member streams under
`MemberStreams.CONCURRENT`, the backend SHALL serialize every operation that touches
the shared archive handle with one per-reader lock.

The lock SHALL cover archive initialization/failure cleanup, the header walk,
strict-EOF direct reads, member stream creation, member `read` / `readinto` / `seek` /
`tell`, member close, archive close, and any operation that repositions or closes the
shared handle. The lock surrounds each complete operation, not individual raw
seek/read calls. Archivey buffering/error/lifecycle wrappers sit outside it; exception
translation, diagnostics/logging, lifecycle release, callbacks, and finalizers run after
the lock is released.

Compressed TAR remains `SOLID`; locking guarantees correctness but not parallel
throughput. Streaming TAR (`streaming=True`) remains one forward pass and does not gain
random concurrent open.

#### Scenario: TAR handle-lock matrix

| Case | Expected |
| --- | --- |
| Two file members opened and read interleaved from plain RA TAR | Each yields its exact bytes in order |
| Two file members opened and read interleaved from compressed RA TAR | Each yields exact bytes; serialization is acceptable |
| Multiple threads open/read distinct TAR members under `MemberStreams.CONCURRENT` after materialization | No data races on the shared handle |
| Materialization then strict EOF verification | The header walk and the EOF read use the same lock |
| Member operation raises/closes | Translation/logging/lifecycle/callback work runs without the TAR handle lock held |
| GNU sparse member opened | Stream yields the member's logical bytes, holes as zeros |
| `streaming=True` TAR | Forward-only contract unchanged; no concurrent random-open behavior |
| Contention on shared handle | Correctness guaranteed; no correctness speed threshold |

### Requirement: Report non-zero bytes past the trailer

After a complete two-block null end-of-archive trailer, or after a damaged second
trailer block (a zero block, then a non-null one, after at least one member), the
backend SHALL scan the bytes that follow, up to 1 MiB past the trailer, whatever the
configuration. The first non-zero byte in that window SHALL emit
`ARCHIVE_TRAILING_DATA` under ordinary diagnostic disposition, with no escalation of
its own: a warning by default, `DiagnosticRaisedError` after delivery when the code
resolves to `RAISE` (as under `DiagnosticPolicy.strict()`), a count alone under
`IGNORE`.

The check SHALL run only after a complete two-block null trailer has been confirmed, or
after the damaged-second-block diagnostic has been emitted. It SHALL NOT run after an
`absent` or `short` trailer, or after a non-null block that is `CorruptionError`. After a
damaged second block it runs from the block after that one, because on a compressed tar
it is where the whole-stream checksum over the members already listed is usually
reached.

The 1 MiB bound is an effort limit, not a ceiling: past it the scan SHALL stop, SHALL
NOT report trailing data, and SHALL NOT refuse the archive. It is a module constant, not
a configuration field. On a compressed tar whose codec can carry a whole-stream checksum
(gzip, bzip2, xz, zstd, lz4, lzip, zlib), a scan that stops at the bound with the stream
not at its end SHALL emit `DIGEST_UNVERIFIABLE` (`reason="trailing_scan_limit"`): that
checksum was never checked. A tail that fails to decode — on a compressed tar, junk
after the compressed stream or a missing footer — SHALL end the scan with no error and,
except on bzip2 and xz (below), no diagnostic: every member was already read whole, and
that is not trailing tar data. A whole-stream checksum that fails in the scan (gzip
CRC-32 or ISIZE, zlib Adler-32, zstd or lz4 content checksum, lzip CRC-32) is not such a
tail: it covers the members already read, and SHALL raise `CorruptionError`. bzip2 and
xz check each block, and the last block's check can also be reached in the scan, but
those codecs report a failed check the same way as junk after the stream. On a
`.tar.bz2` or `.tar.xz` a tail that fails to decode SHALL therefore emit
`DIGEST_UNVERIFIABLE` (`reason="trailing_decode_failed"`) instead of ending the scan
silently. Where that check is reached depends on how far the codec has read ahead
when the last member's bytes are delivered, not on the archive: when it is reached
during the member read, the read SHALL raise `CorruptionError` instead. Either way the
failure SHALL NOT pass silently.

The code is not a truncation: nothing is truncated, the file is *longer* than the
listing accounts for.

#### Scenario: trailing-bytes matrix

| Case | Default policy | `DiagnosticPolicy.strict()` |
| --- | --- | --- |
| Valid tar, trailer, EOF | No diagnostic | No diagnostic |
| Valid tar + 4 KiB of zeros (`tar` pads to 10 KiB records) | No diagnostic | No diagnostic |
| Valid tar + 4 KiB of `b"JUNK"` | `ARCHIVE_TRAILING_DATA` | `DiagnosticRaisedError` |
| Damaged second trailer block, then junk | `ARCHIVE_EOF_MARKER_MISSING` (`"second_zero_block"`), then `ARCHIVE_TRAILING_DATA` | `DiagnosticRaisedError` |
| `.tar.gz` with a bad CRC-32 and a damaged second trailer block | `CorruptionError` | `DiagnosticRaisedError` (the marker diagnostic is raised first) |
| Valid tar + zeros + one non-zero byte, within 1 MiB | `ARCHIVE_TRAILING_DATA` | `DiagnosticRaisedError` |
| First non-zero byte more than 1 MiB past the trailer | No diagnostic; the scan stopped (a compressed tar: `DIGEST_UNVERIFIABLE`) | No diagnostic (a compressed tar: raises on `DIGEST_UNVERIFIABLE`) |
| Two tars concatenated | `ARCHIVE_TRAILING_DATA`; the first is listed | `DiagnosticRaisedError` |
| Legitimately empty tar (10240 zeros), or 32 KiB of zeros | No diagnostic; all zeros | No diagnostic |
| A real ISO opened as TAR | Empty listing plus `ARCHIVE_TRAILING_DATA`: its zeros stop at 32768 | Raises |
| Missing / short trailer | `ARCHIVE_EOF_MARKER_MISSING`; the scan does not run | Raises on that code |
| `.tar.gz`, junk inside the gzip stream after the trailer | Tail decompressed, at most 1 MiB; `ARCHIVE_TRAILING_DATA` | `DiagnosticRaisedError` |
| `.tar.gz`, junk after the gzip stream or a missing gzip footer | No diagnostic, no error | No diagnostic, no error |
| `.tar.gz` / `.tar.zst` / `.tar.lz4`, a member byte damaged, stream checksum reached within 1 MiB of the trailer | `CorruptionError` | `CorruptionError` |
| `.tar.bz2` / `.tar.xz`, the last block's check failing, reached in the trailing scan (within 1 MiB of the trailer), or junk after the stream | `DIGEST_UNVERIFIABLE` | Raises on `DIGEST_UNVERIFIABLE` |
| `.tar.bz2` / `.tar.xz`, the same failing check reached while the last member is read (decided by the codec's read-ahead, so by member size) | `CorruptionError` from the read | `CorruptionError` from the read |

### Requirement: Decode TAR member names as UTF-8 by default

When the caller does not pass `encoding=`, the TAR backend SHALL decode ustar and GNU
long-name fields, and the other header strings (`uname`, `gname`, `linkname`), as UTF-8 with `errors="surrogateescape"`. The result MUST
NOT depend on the process locale or `sys.getfilesystemencoding()`. These fields do not
declare an encoding, so a field whose bytes are valid UTF-8 SHALL be decoded as UTF-8
whatever `encoding=` says, and a caller-passed `encoding=` SHALL replace the
surrogate-escaped UTF-8 decode, with the same error handler, only for a field whose bytes
are not valid UTF-8. When a caller-passed `encoding=` would have given a different name
than that UTF-8 reading, the backend SHALL emit `MEMBER_NAME_ENCODING_INFERRED` with
`inferred_encoding="utf-8"` and `declared_encoding` set to the caller's encoding, as ZIP
does. A PAX record SHALL be decoded strictly as UTF-8 first; when that
fails it SHALL be decoded with the same archive codec and error handler, so `encoding=`
(or the UTF-8 default) applies to a PAX record only when its bytes are not UTF-8. A PAX
record under `hdrcharset=BINARY`, in its own header or in a global header before it that
no later global header reset, declares no encoding and is decoded as a ustar field is.

#### Scenario: TAR default name decoding

| Case | Expected |
| --- | --- |
| ustar or GNU long name stored as UTF-8 `café.txt`, no `encoding=`, filesystem encoding Latin-1 or ASCII | `name == "café.txt"`; `raw_name` is the UTF-8 bytes |
| ustar name stored as Latin-1 `caf\xe9.txt`, no `encoding=`, any locale | `name == "caf\udce9.txt"`; `raw_name == b"caf\xe9.txt"` |
| ustar or GNU long name stored as UTF-8 `café.txt`, `encoding="latin-1"` | `name == "café.txt"`; `raw_name` is the UTF-8 bytes; one `MEMBER_NAME_ENCODING_INFERRED` naming `latin-1` |
| ustar name stored as Latin-1 `caf\xe9.txt`, `encoding="latin-1"` | `name == "café.txt"`; `raw_name == b"caf\xe9.txt"` |
| ustar `linkname`, `uname` or `gname` stored as UTF-8, `encoding="latin-1"` | Decoded as UTF-8 |
| PAX `path` record holding the non-UTF-8 bytes `caf\xe9\xe9.txt`, no `encoding=`, any locale | `name == "caf\udce9\udce9.txt"`; `raw_name == b"caf\xe9\xe9.txt"` |
| The same PAX record, `encoding="latin-1"` | `name == "caféé.txt"` |
| A global header with `hdrcharset=BINARY`, then a member whose PAX `path` holds UTF-8 `café.txt`, `encoding="latin-1"` | `name == "café.txt"`; one `MEMBER_NAME_ENCODING_INFERRED` naming `latin-1`, as under the member's own `hdrcharset=BINARY` |

### Requirement: Parse TAR headers natively

The TAR backend SHALL parse headers with archivey's own parser, one header at a time
and without recursion, and SHALL NOT read through stdlib `tarfile`. It SHALL read the
encodings below, and SHALL give the same listing on every supported Python version.
The `prefix` field SHALL be joined to the name only under the ustar magic, as GNU tar
reads it.

| Encoding | Read |
| --- | --- |
| v7, POSIX ustar (with `prefix`), old GNU | Header fields; checksum as unsigned or signed sum |
| Numbers | Octal (NUL- or space-terminated, empty is 0) and base-256 with a first byte of `0x80` or `0xFF` |
| PAX `x` / `X` / `g` | Length-validated records; globals persist, an empty global value deletes the key |
| GNU `L` / `K` | Long name and long link name |
| GNU sparse | Old GNU `S` with extension blocks; PAX 0.0, 0.1 and 1.0 |

#### Scenario: native parse matrix

| Case | Expected |
| --- | --- |
| The same archive on Python 3.11 to 3.15, any patch release | Same members, same bytes |
| A chain of extended headers | Read in a loop; each header is charged to the member's `max_metadata_bytes` budget before it is read |
| A member `seek` past its end | Returns the target; the next read returns `b""` |
| A GNU incremental archive (`tar -G`), whose old GNU headers hold `atime` where ustar has `prefix` | Members listed under their own names |

### Requirement: Reject TAR headers that do not parse

The header walk SHALL stop on a header that does not parse, for `Detect truncated TAR
archives` to classify. These are headers that do not parse:

| Case | Example |
| --- | --- |
| Header block | A bad checksum; a number field that is neither octal nor base-256; a negative size |
| PAX records | A record length that does not land on its newline |
| Old GNU sparse | A map number, in the header or an extension block, that is neither octal nor base-256 or is past 2**63 - 1 |
| Header chain | An extended header followed by a block that is not a header |

The walk SHALL raise `TruncatedError` when the stream ends inside a header or a data
area, or right after an extended header, and `CorruptionError` for a PAX `size` that is
not a number. An `x`, `X`, `L` or `K` header followed by a zero block SHALL end the
walk as an end-of-archive marker, as GNU tar reads it. A `g` header describes no member,
so a zero block after it is an ordinary end-of-archive marker.

#### Scenario: header refusal matrix

| Case | Expected |
| --- | --- |
| A rejected header after the first member | `CorruptionError` after the members before it, in both access modes |
| A PAX `x`, global `g` or GNU long-name header followed by the end-of-archive marker | A clean end after the members before it |
| A PAX header followed by a block that is not a header | `CorruptionError` after the members before it |
| The stream ends right after a PAX header or a GNU long-name header | `TruncatedError` |

### Requirement: Read GNU sparse maps

Every sparse map SHALL be parsed during the header walk, a PAX 1.0 map from the first
blocks of the member's data area, and SHALL be charged to the member's
`max_metadata_bytes` budget, 24 bytes per entry, before its entries are kept. A PAX
map (0.0, 0.1 or 1.0) that does not parse SHALL raise `CorruptionError` during the
listing: a number that is not decimal or is past 2**63 - 1, a 0.0 record with more than
one number, an odd count of 0.1 numbers, or a 1.0 number longer than 20 digits, which
GNU tar refuses too. An old GNU map that does not parse is a header that does not
parse (`Reject TAR headers that do not parse`).

A PAX member whose `GNU.sparse.major` is 1 or more SHALL be read as 1.0, whatever its
minor version, as GNU tar 1.35 reads it. A `GNU.sparse.major` of 0, or one that is not
a number, with no 0.x map SHALL raise `CorruptionError`, never serve the map as content.

A sparse member's map SHALL be checked before any of its data is returned: when the
member is opened, or in a streaming pass on its first read, so a consumer that skips
the member is unaffected. The check SHALL raise `CorruptionError` when the map has a
negative entry, a chunk (empty or not) that ends past the logical size, or chunks that
do not add up to exactly the bytes stored for them, or when the logical size is past
2**63 - 1. GNU tar 1.26 to 1.35 and bsdtar write the exact sum in every encoding, so
bytes the map does not name are damage (DR-3). An empty chunk past the logical size
loses no bytes, but the map contradicts its own declared size (DR-1).

The check SHALL raise `UnsupportedFeatureError` when a non-empty chunk starts before the
previous non-empty chunk ends (out of order or overlapping) and the map has none of the
damage above. GNU tar 1.35 reads such a map, placing each chunk at the offset the map
gives, but serving the chunks in logical order on the streaming path would need
buffering up to the logical size (DR-9). The map is valid data archivey does not serve
(DR-4). An empty entry is exempt from the order check, because GNU tar ends a map with
`(realsize, 0)` when the file ends in a hole and the old GNU header pads its unused
slots with `(0, 0)`.

#### Scenario: sparse map matrix

| Case | Expected |
| --- | --- |
| A sparse member | Logical bytes with holes as zeros; never bytes past the member's stored size |
| A sparse map out of order or overlapping | `UnsupportedFeatureError` on open |
| A chunk, empty or not, that ends past the logical size | `CorruptionError` on open |
| Chunks that add up to more than the member stores | `CorruptionError` on open |
| A negative offset or length | `CorruptionError` on open |
| A logical size past 2**63 - 1 | `CorruptionError` on open |
| A sparse map whose chunks name 1 to 511 bytes fewer than the member stores | `CorruptionError` on open |
| A PAX 1.0 map that does not parse | `CorruptionError` during the listing |
| A PAX 0.1 map holding a number past 2**63 - 1 | `CorruptionError` during the listing |
| An old GNU extension block with a number that does not parse | The walk stops on a rejected header, as `Detect truncated TAR archives` classifies it |
| `GNU.sparse.major=2`, `GNU.sparse.minor=0` with a 1.0 map | Read as 1.0 |
| `GNU.sparse.major=0` with no 0.x map | `CorruptionError` during the listing |
| A PAX 1.0 map of more entries than the budget allows | `ResourceLimitError` during the listing, before the entries are read |
