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
arrives. At that point it SHALL remove what it wrote for the earlier member in this run
and report the earlier member `SUPERSEDED`, before the later member reaches the filter,
so both modes end with the same results and the same tree on disk. A directory that
later members were written into stays, as their parent. One difference remains: a
different name that collides with the earlier member between the two (a case variant
outside `TRUSTED`) meets that member's write in a streaming pass, not an empty key.

#### Scenario: non-current skip matrix

| Case | Expected |
| --- | --- |
| Content superseded by later same-name or anti | `SUPERSEDED` on extract; path absent on fresh dest |
| Streaming TAR holding `a.txt` twice, default overwrite policy | `SUPERSEDED`, then `EXTRACTED`; `a.txt` holds the later bytes, as in random access |
| User `filter` receives non-current member | Filter is called; returning the member does not force a write |
| `open` superseded content `FILE` | Bytes returned (random access still works) |
