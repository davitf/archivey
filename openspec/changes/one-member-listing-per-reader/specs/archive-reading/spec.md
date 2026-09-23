## ADDED Requirements

### Requirement: Each member is listed once per reader

A reader SHALL build one `ArchiveMember` object per archive member and hand out that same
object from every listing method and pass: `members_report_if_available()`,
`members_report()`, `members()`, `scan_members()`, `get()`, `__iter__`,
`stream_members()` and `extract_all()`. Each member SHALL be registered (its
`member_id` and `archive_id` stamped, its presentation checks run and its listing-limit
accounting done) exactly once, before any of those methods returns or yields it.
Typing-time and presentation diagnostics for a member SHALL therefore be emitted once
per reader, whichever methods are called and in whatever order, so
`DiagnosticSummary.counts` stays exact. A typing-time diagnostic's context SHALL carry,
as `member_id`, the id the member is registered with, on every backend.

A backend's member walk SHALL run at most once per reader when it completes. A walk that
fails before completing, without terminal archive damage, MAY be repeated on the next
call only in random-access mode, and only when none of its members was handed out.

#### Scenario: listing identity matrix

| Case | Expected |
| --- | --- |
| `members_report_if_available()` then `members()` (ZIP, ISO, 7z, RAR) | Same objects, in the same order |
| `extract_all()` on an upfront-index archive | The backend walks its index once |
| `stream_members()` on a fresh 7z or solid RAR (either mode) | Every yielded member has `member_id` set; `member in reader` is true |
| ZIP or ISO member whose name is normalized, listed by `extract_all()` | `MEMBER_NAME_NORMALIZED` counted once for that member |
| Member name with a bidi control, peeked then materialized | `MEMBER_NAME_BIDI_CONTROL` counted once and attached to the object the caller holds |
| Typing-time diagnostic on ZIP, 7z, RAR, ISO | Context `member_id` equals the member's `member_id` |
| Random-access walk interrupted by `ResourceLimitError` / `KeyboardInterrupt`, then retried | Retry walks again; ids and objects are consistent with a single walk |
| Streaming pass whose walk fails without terminal damage | Later listing calls raise; the partial prefix is never published as complete |

### Requirement: Last-entry-wins is stamped once, when the member walk ends

Last-entry-wins `is_current` SHALL be stamped once per reader, when the member walk ends.
The walk ends when it completes or when it stops on terminal archive damage
(`CorruptionError` / `TruncatedError`). On terminal damage the stamp SHALL cover the
recovered prefix that the incomplete report holds. Every member yielded or returned after
that point SHALL carry its final value.

#### Scenario: is_current stamping matrix

| Case | Expected |
| --- | --- |
| Streaming `extract_all()` over a ZIP holding `a.txt` twice | `SUPERSEDED`, then `EXTRACTED`, as in random access (today it raises `ExtractionError`) |
| Streaming pass over a truncated TAR holding `a.txt` twice before the damage, then `members_report()` | Incomplete report; the first `a.txt` reads `is_current=False` |

### Requirement: A streaming pass finalizes on its own cursor

Completing the member walk SHALL NOT by itself finalize a streaming pass. The pass
finalizes when its own consumer passes the last member, so a peek that drains the walk
followed by an abandoned pass publishes no complete report and reads no link data.

#### Scenario: pass finalization matrix

| Case | Expected |
| --- | --- |
| Streaming ZIP: peek inside the `stream_members()` loop, then `break` | No complete report published; no link-target reads |
