# archive-reading — cap symlink targets read from member data

## ADDED Requirements

### Requirement: Bounded symlink-target reads from member data

A backend that reads a symlink's target from the member's data (ZIP, 7z, RAR3/4)
SHALL bound that read. For a plain target, a member whose declared size is over
`MAX_LINK_TARGET_BYTES` (4096) SHALL NOT be opened for its target at all, and any other
read SHALL ask for at most 4096 + 1 bytes. A Windows reparse buffer is bounded
differently, by its own header, as the paragraph after next says.

A target longer than 4096 bytes SHALL be treated as corrupt or malicious: `link_target`
SHALL stay unset, and `SYMLINK_TARGET_UNAVAILABLE` SHALL be emitted with
`reason="target_too_long"`. The target SHALL NOT be truncated. The member keeps its
link type, and since the archive does record a target, extraction SHALL fail that
member (`LinkTargetNotFoundError`) rather than report `LINK_TARGET_UNAVAILABLE`.
`SYMLINK_TARGET_UNAVAILABLE` is in `ARCHIVE_INTEGRITY_CODES`, so
`DiagnosticPolicy.strict()` refuses the archive.

A Windows reparse buffer stored as member data SHALL be read as far as its own header
declares: the 8-byte header, then the payload length its 16-bit `ReparseDataLength`
states (plus one byte, so a member that is exactly one buffer reaches end of stream and
is verified). A header whose tag is not a symlink or a junction declares nothing to
read. The buffer SHALL NOT be refused for its size, because a member flagged as a
reparse point may turn out to hold ordinary file content. The target parsed from a
buffer SHALL be held to the same 4096-byte cap, measured in UTF-8.

A member whose data outruns a declared size under the cap is corrupt, not over-long:
where the backend verifies data against the declared size (ZIP always, 7z whenever
checksums are verified), the read SHALL fail with `CorruptionError` on reaching that
size, as for any other member, and the cap does not decide the outcome.

Targets stored in a header (TAR `linkname`, RAR5 redirection records, Rock Ridge) are
outside this requirement: the header parser has already allocated them, and
§"Listing metadata-byte accounting" weighs them at registration.

#### Scenario: symlink-target cap matrix

| Case | Expected |
| --- | --- |
| Data-stored target of exactly 4096 bytes | `link_target` set, no diagnostic |
| Data-stored target of 4097 bytes | `link_target is None`; `SYMLINK_TARGET_UNAVAILABLE`, `reason="target_too_long"` |
| Compressed target declaring 64 MiB | Refused without decoding any of it |
| ZIP target whose data outruns a declared size under the cap | `CorruptionError` naming the declared size; nothing past it decoded |
| No declared size, data longer than the cap | Read stops at 4097 bytes; refused as over-long |
| Over-long target under `DiagnosticPolicy.strict()` | Listing raises `DiagnosticRaisedError` |
| Over-long target, `extract_all(on_error=CONTINUE)` | That link `FAILED` with `LinkTargetNotFoundError`; other members extract |
| Reparse buffer whose target is over 4096 UTF-8 bytes | Refused as over-long |
| Reparse buffer followed by more data | Only the declared buffer and one byte are read |
| Reparse-flagged member whose data is not a buffer, any size | Re-typed to its fallback with all its content readable; only its header read while listing |

## MODIFIED Requirements

### Requirement: Listing metadata-byte accounting

The system SHALL measure `max_metadata_bytes` as a **safety-oriented weight** of
retained string/bytes fields accumulated as members are registered, plus
archive-level `ArchiveInfo.comment` once when known. Exact UTF-8 encoding of
every field is not required — the cap exists to bound metadata bombs, not to
mirror an allocator — but the weight MUST NOT under-count UTF-8 size:

- `str` fields `name`, `comment`, `link_target`, `uname`, `gname`: a cheap
  upper bound on UTF-8 length — `len(s)` when `s` is ASCII, otherwise
  `4 * len(s)` (UTF-8 is at most 4 bytes per code point). Implementations MAY
  use a stricter exact encode; they MUST NOT use a measure that can be smaller
  than UTF-8 (plain `len(s)` on non-ASCII would under-count a Unicode name bomb).
- `raw_name`: `len(raw_name)` when not `None` (stored archive bytes; already exact)
- `extra`: lengths of `str` / `bytes` values under the same rules; for a one-level
  `dict` value, nested `str` / `bytes` values only
- Exclude: `_raw`, `hashes`, diagnostics, Python object overhead

A field filled in after its member was registered SHALL be weighed when it is filled
in, under the same enforcement as registration. The case that exists is a symlink
target stored as member data (ZIP, 7z, RAR3/4), which is read only once every member is
registered: a target resolved while materializing `members()` / `scan_members()` SHALL
count toward `max_metadata_bytes` before that list is published.

#### Scenario: metadata accounting matrix

| Case | Expected |
| --- | --- |
| Member with long `name` + `raw_name` | Both weights count |
| Huge `ArchiveInfo.comment` alone | Counts toward the budget once |
| `extra` holds opaque non-str/bytes object | Not counted |
| ASCII-only name | Weight equals `len(name)` (exact UTF-8) |
| Non-ASCII / surrogateescape name | Weight ≥ UTF-8-with-surrogateescape byte length (upper-bound OK) |
| Symlink target read from member data after registration | Weighed when read; over the cap → `ResourceLimitError` naming `max_metadata_bytes` |
