# archive-reading — member selector spellings and unmatched entries delta

## MODIFIED Requirements

### Requirement: Collection form of MemberSelector

`MemberSelector` SHALL accept a predicate or `Collection[str | ArchiveMember]`,
normalized to a predicate at the API boundary:

- `str` matches **every** member with that normalized name (duplicates all match;
  extraction keeps sequential last-wins-on-disk)
- A `str` entry that does not end in `/` SHALL also match every member whose name is
  that entry plus `/`, because a directory member's normalized name carries a trailing
  `/`. An entry that ends in `/` SHALL match only that name. Link-target lookup uses the
  same rule.
- `ArchiveMember` matches by **identity** (`archive_id` + `member_id`; members are
  unhashable → id set, never member set)
- String and member entries MAY mix

A collection entry that matches no member SHALL be reported as
`MEMBER_SELECTOR_UNMATCHED` (`diagnostics`), once for each entry, after every member
has been offered to the selector:

- `stream_members()` reports at the end of a pass that reached the last member. A pass
  that the caller stops early SHALL NOT report, because a later member could match.
- `extract_all()` reports before it writes any member when the member list is available
  without a scan, and otherwise at the end of the pass.
- A predicate selector SHALL NOT be reported.

#### Scenario: selector matrix

| Case | Expected |
| --- | --- |
| `stream_members(members=["a.txt"])` with two `a.txt` | Both yielded, archive order |
| Specific `ArchiveMember` among duplicates | Only that identity |
| `members=["dir"]` on an archive holding `dir/` and `dir/f.txt` | `dir/` selected, `dir/f.txt` not; no diagnostic |
| `members=["x/"]` on an archive holding only the file `x` | Nothing selected; one `MEMBER_SELECTOR_UNMATCHED` for `x/` |
| `members=["a.txt", "typo.txt", "typo.txt"]`, pass to the end | `a.txt` selected; one `MEMBER_SELECTOR_UNMATCHED` for `typo.txt` |
| Same selector, caller breaks after the first member | No `MEMBER_SELECTOR_UNMATCHED` |
| `ArchiveMember` from another reader | Nothing selected; `MEMBER_SELECTOR_UNMATCHED` with `entry_kind="member"` |
| `extract_all(members=["typo.txt"])` on ZIP with `MEMBER_SELECTOR_UNMATCHED` set to `RAISE` | `DiagnosticRaisedError` before any member is written |
