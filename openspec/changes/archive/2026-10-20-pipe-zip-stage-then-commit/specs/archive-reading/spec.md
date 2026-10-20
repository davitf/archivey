## MODIFIED Requirements

### Requirement: Collection form of MemberSelector

`MemberSelector` SHALL accept a predicate or any `Iterable[str | ArchiveMember]`,
normalized to a predicate at the API boundary. The boundary SHALL read the iterable
exactly once, so a generator selects the same members as the equivalent list:

- `str` matches **every** member with that normalized name (duplicates all match;
  extraction keeps sequential last-wins-on-disk)
- A `str` entry SHALL match the stored name exactly. A directory member's normalized
  name carries a trailing `/`, so `"dir"` does not select `dir/`. `get(name)` and
  `open(name)` match the same way. The missing `/` is reported like any other
  unmatched entry.
- `ArchiveMember` matches by **identity** (the reader that listed it + `member_id`;
  members are unhashable → id set, never member set)
- String and member entries MAY mix

A collection entry that matches no member SHALL be reported as
`MEMBER_SELECTOR_UNMATCHED` (`diagnostics`), once for each entry, after every member
has been offered to the selector:

- `stream_members()` reports at the end of a pass that reached the last member. A pass
  that the caller stops early SHALL NOT report, because a later member could match.
- `extract_all()` reports before it writes any member when the member list is available
  without a scan, and otherwise at the end of the pass. A ZIP read from a non-seekable
  source has its member list once its central directory has been read, before any
  member is placed (`safe-extraction`), so it reports then, as the first case does. Under a `RAISE` disposition the
  first case refuses the call before the destination is created. In the second case
  the members already written stay on disk and `DiagnosticRaisedError` replaces the
  report.
- When the member list ends in terminal damage, `extract_all()` SHALL NOT report: an
  entry could match a member past the damage that was never listed. The call raises
  the listing's own error after writing the prefix (`safe-extraction`), so under a
  `RAISE` disposition the caller sees that error, not `DiagnosticRaisedError`.
- Under a `RAISE` disposition, `stream_members()` yields every selected member and
  then raises `DiagnosticRaisedError` from the iterator.
- A predicate selector SHALL NOT be reported.

`extract_all()` over a ZIP read from a non-seekable source SHALL make the selection
again at the end of the pass, on what each member became once the central directory
was read (its name, where a DOS host makes `\` a separator, and its type). During the
pass a `str` entry SHALL match every name a member can end with (with `\` as a
separator or not, with or without a folder's trailing `/`), so the data of any member
it may select is staged while the stream holds it (`safe-extraction`); at the end it
selects for good, what it no longer takes is not placed and gets no result, and no
entry counts as matched before then. A member a predicate left out during the pass
SHALL be asked about again at the end; taken then, it is placed when it needs no bytes
the stream has passed (a link, a folder, an empty file) and otherwise fails with
`ExtractionError`. A predicate is therefore called twice for every member, and reads
`member_state_final` as `False` on the first call. The `filter` SHALL NOT run during
the pass: it is called once per member, at placement, on the final member.

#### Scenario: selector matrix

| Case | Expected |
| --- | --- |
| `stream_members(members=["a.txt"])` with two `a.txt` | Both yielded, archive order |
| Specific `ArchiveMember` among duplicates | Only that identity |
| `members=["dir"]` on an archive holding `dir/` | Nothing selected; `MEMBER_SELECTOR_UNMATCHED` for `dir`, naming `dir/` |
| `members=["x/"]` on an archive holding only the file `x` | Nothing selected; one `MEMBER_SELECTOR_UNMATCHED` for `x/` |
| `members=["a.txt", "typo.txt", "typo.txt"]`, pass to the end | `a.txt` selected; one `MEMBER_SELECTOR_UNMATCHED` for `typo.txt` |
| Same selector, caller breaks after the first member | No `MEMBER_SELECTOR_UNMATCHED` |
| `ArchiveMember` from another reader | Nothing selected; `MEMBER_SELECTOR_UNMATCHED` with `entry_kind="member"` |
| `extract_all(members=["typo.txt"])` on ZIP with `MEMBER_SELECTOR_UNMATCHED` set to `RAISE` | `DiagnosticRaisedError` before any member is written |
| `extract_all(members=["docs/r.txt"])` on a ZIP from a pipe whose DOS-host entry stores `docs\r.txt` | `docs/r.txt` extracted, as from a file; `members=["docs\\r.txt"]` selects nothing, as from a file |
| `extract_all(filter=...)` taking only names under `docs/`, the same archive from a pipe | `docs/r.txt` extracted, as from a file: the filter sees only the final name |
| `extract_all(members=["a.txt", "typo.txt"])` on TAR with the code set to `RAISE` | `a.txt` written, then `DiagnosticRaisedError`; no report |
| The same call on a ZIP from a pipe | `DiagnosticRaisedError` before any member is placed, as from a file; nothing of the run in the destination |
| `extract_all(members=["typo.txt", "a.txt"])` on a listing that ends in damage after `a.txt`, code set to `RAISE` | `a.txt` written, then the listing's error; no `MEMBER_SELECTOR_UNMATCHED` |

### Requirement: Bounded implicit temporary storage

Reader ops SHALL NOT consume memory or temp storage proportional to member/archive
size as an implicit side effect of open/read/validate/password-confirm. Silently
spooling plaintext to a temp file is forbidden. A per-format strategy that
inherently needs proportional temp storage (e.g. `format-rar`'s documented copy of
a non-path archive source to disk, so `unrar` can seek it) is allowed only when
declared in that format's capability spec, and a strategy that copies the archive
source SHALL be bounded by `ArchiveyConfig.spool_limits`. A declared strategy that keeps
decoded member data to spare a decode it would otherwise repeat (`format-rar`: a solid
pass keeping the sources of file copies) SHALL keep it in memory only up to a constant
the format spec states, and SHALL charge anything written to temporary storage to the
same `spool_limits` budget. Such a keep is optional: when the budget has no room it SHALL
be declined, the read SHALL fall back to decoding again, and nothing SHALL be refused.
Caller's own buffering of a returned stream is unrestricted. This requirement does not
reach extraction output: the member data `extract_all` stages in the destination for a
ZIP read from a non-seekable source (`safe-extraction`) is output of the extraction,
bounded by `ExtractionLimits`, not by `spool_limits`.

#### Scenario: bounded storage matrix

| Case | Expected |
| --- | --- |
| Encrypted member, many candidates | Confirmation temp use bounded by a constant |
| Backend can only serve via materialization | Strategy declared in format spec, not adopted silently |
| Declared copy of the archive source | Bounded by `SpoolLimits.max_bytes`; over it, `ResourceLimitError` |
| Declared keep of decoded member data, over the memory constant | Written within `SpoolLimits.max_bytes`; with no room left, not kept and decoded again; never `ResourceLimitError` |
