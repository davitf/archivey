# safe-extraction — streaming duplicate names delta

## MODIFIED Requirements

### Requirement: Skip non-current members by default

`extract` / `extract_all` SHALL skip members with `is_current is False` by default
(`ExtractionStatus.SUPERSEDED`; no write; no bomb-limit counting for the skip). This
is **hardwired coordinator behavior**, not the policy `filter` / `MemberFilter`
pipeline: the skip happens after the optional user `filter` runs so callers can
inspect or rewrite non-current members, then the coordinator still skips writing
them unless a future explicit opt-in lands. `SUPERSEDED` is distinct from
`ExtractionStatus.NOT_OVERWRITTEN` (an existing destination left in place under
`OverwritePolicy.SKIP`).

How surfaces interact:

| Surface | Non-current members |
| --- | --- |
| `members()` / `__iter__` / `get` | Visible (metadata + `is_current=False`) |
| `members=` selector | May select them; they still participate in the extract walk |
| User `filter` (`MemberFilter`) | **Invoked** on them (same as current members) |
| Default extract write | Skipped after filter; `SUPERSEDED` result |
| `open`/`read` on superseded `FILE` | Still allowed (payload exists); not gated by `is_current` |

There is no extract-all flag to force writing non-current revisions; callers that need those bytes use `open`/`read` (or a future opt-in).

A streaming pass learns that a member is shadowed only when the later same-name member
arrives. At that point it SHALL report the earlier member `SUPERSEDED`, before the later
member reaches the filter, and stop counting it against `max_entries` and
`max_extracted_bytes` (the archive-wide ratio still counts its decoded bytes). What the
earlier member wrote in this run SHALL stay in place until the later member is done, so
a later member that lands at the same path replaces it atomically under any overwrite
policy; if the later member does not land there, the earlier member's entry SHALL then be
removed. A directory that other members were written into stays, as their parent.

Results and the tree on disk then match random access, except where something that
happened before the later member arrived depended on the earlier member. A streaming pass
cannot undo that:

- A selection that excludes the later member: the pass never sees it, so the earlier
  member stays `EXTRACTED`.
- An entry of the caller's that the earlier member replaced under `REPLACE`: it is not
  restored when the later member does not land.
- A member between the two that met the earlier member's write: a different name that
  collides with it (a case variant outside `TRUSTED`), a member written under it as a
  directory, or a hardlink to it when it was not written (random access reads that
  source again; a streaming pass cannot).

#### Scenario: non-current skip matrix

| Case | Expected |
| --- | --- |
| Content superseded by later same-name or anti | `SUPERSEDED` on extract; path absent on fresh dest |
| Streaming TAR holding `a.txt` twice, default overwrite policy | `SUPERSEDED`, then `EXTRACTED`; `a.txt` holds the later bytes, as in random access |
| User `filter` receives non-current member | Filter is called; returning the member does not force a write |
| `open` superseded content `FILE` | Bytes returned (random access still works) |
