# diagnostics — member selector spellings and unmatched entries delta

## ADDED Requirements

### Requirement: Report a selector entry that matched no member

The system SHALL emit `MEMBER_SELECTOR_UNMATCHED` once for each entry of a `members=`
collection that matched no member, at the points that `archive-reading` "Collection
form of MemberSelector" names. `SelectorUnmatchedContext` is part of the
`DiagnosticContext` union:

| Code | Variant and required fields |
| --- | --- |
| `MEMBER_SELECTOR_UNMATCHED` | `SelectorUnmatchedContext`: `kind="selector_unmatched"`, `archive_name`, `entry`, `entry_kind` |

`entry_kind` ∈ `{"name","member"}`. For a `str` entry, `entry` is the entry as the
caller wrote it. For an `ArchiveMember` entry, `entry` is that member's name; a member
entry is unmatched when it belongs to another reader or carries no identity. A `str`
entry that the caller repeated is reported once.

`MEMBER_SELECTOR_UNMATCHED` SHALL NOT be in `ARCHIVE_INTEGRITY_CODES`: it reports the
caller's argument, not the archive, and a job that passes one list of names to many
archives would otherwise raise on each archive that lacks one of them. Its default
disposition is `COLLECT`.

#### Scenario: unmatched selector entries

| Case | Expected |
| --- | --- |
| `extract_all(members=["a.txt", "typo.txt"])` on an archive holding `a.txt` | `a.txt` extracted; the report carries one `MEMBER_SELECTOR_UNMATCHED` with `entry="typo.txt"`, `entry_kind="name"` |
| Same call under `DiagnosticPolicy.strict()` | No raise; the diagnostic is collected |
| `stream_members(members=lambda m: False)` | No `MEMBER_SELECTOR_UNMATCHED` |
