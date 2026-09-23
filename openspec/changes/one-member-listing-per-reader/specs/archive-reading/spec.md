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
finalizes when its own consumer passes the last member. A peek that drains the walk
followed by an abandoned pass SHALL run no link finalization: it resolves no links and
publishes no complete report. On backends that read link targets only at finalization
(ZIP, ISO), such a pass reads no link data. A 7z pass reads a link member's data as it
passes that member, so an abandoned 7z pass may already have read link data for the
members it passed.

#### Scenario: pass finalization matrix

| Case | Expected |
| --- | --- |
| Streaming ZIP: peek inside the `stream_members()` loop, then `break` | No complete report published; no link-target reads |

## MODIFIED Requirements

### Requirement: Bounded-memory sequential streaming via stream_members

```python
def stream_members(
    self,
    members: MemberSelector | None = None,
) -> Iterator[tuple[ArchiveMember, ArchiveStream | None]]: ...
```

Yields `(member, stream)` in archive order with bounded memory. Solid blocks
decompress progressively (never buffered whole); peak = decoder working set + one
in-flight chunk. Non-file members yield `None`.

`members` is a selector (names/identities or predicate), not a transform. Streams
are lazy: unselected/unread members are not opened/decompressed and do not request
passwords. Yields the original mutable `ArchiveMember` so late-bound fields stay
visible.

Symlink targets are the one exception. On backends that store a symlink's target as
member data (ZIP, 7z), a pass that finalizes resolves every symlink's target, selected
or not, so the complete report matches random access. Reading an unselected link's target
MAY decompress data the caller did not select; on 7z that is the link's folder up to the
link, within the budget of `format-7z` "A 7z folder is decoded at most once for its link
targets". An unselected link whose target needs a password SHALL NOT consult the password
provider. Known-good and sequence candidates MAY be tried. When none opens it,
`link_target` stays unset and `SYMLINK_TARGET_UNAVAILABLE` is emitted, as for any link
whose target cannot be read.

Yielded streams are iterator-owned and valid only until advance: the iterator SHALL
close/invalidate the previous stream before the next yield. MUST NOT retain a
growing decompressed-block cache until reader close. On solid archives, random
`open()` may re-decode from block start; the cost is silent, and callers are
directed to `stream_members()` by `reader.cost.access_cost` and by the `open()` /
`read()` docstrings rather than by a runtime warning.

A `stream_members()` invocation is an exclusive one-pass/data-path operation in
both modes. It SHALL NOT overlap random `open()`, materialization, another
iteration/data pass, unrelated extraction, or reader close. An `extract_all()`
owner MAY invoke it as a child pass and MAY read/close the yielded child stream.
Unrelated overlap SHALL raise `ArchiveyUsageError` at the later op and leave the
active pass/stream valid. (Unlike random `open()`, whose independently owned
streams may coexist when `CONCURRENT` is declared — see `reader-concurrency`.)

#### Scenario: stream_members matrix

| Case | Expected |
| --- | --- |
| Yielded file stream emits diagnostic before advance | Stream + reader snapshots share one retained occurrence |
| Selector excludes member / stream unread | No open/decompress; no data-path diagnostic. A symlink's target is still read at finalization on ZIP and 7z (see the exception above) |
| Solid archive | Progressive decode; peak = decompressor state + one chunk |
| `stream_members(lambda m: m.name.endswith(".txt"))` | Only `.txt`; unselected never opened except for symlink targets read at finalization; original mutable members |
| Fully read stream, then inspect member | Late-bound fields (e.g. size/CRC) visible on same object |
| Advance after one yield | Prior stream closed/invalidated first |
| Random `open()` during active pass | `ArchiveyUsageError`; pass remains usable |
| Close/abandon partial generator | Current stream closed; pass ownership released once |
| Random `open()` into solid block | Re-decode from block start + skip; no diagnostic, no warning — discoverable via `reader.cost.access_cost` and the `open()` docstring |
| Encrypted solid 7z `[a.txt, link, b.txt]`, no password, `stream_members(lambda m: False)` to the end | Provider never consulted; `link_target` unset; `SYMLINK_TARGET_UNAVAILABLE` for the link |
| Unencrypted solid 7z, selector excludes a symlink, pass to the end | The link's target is resolved; its folder is decoded up to the link once |
