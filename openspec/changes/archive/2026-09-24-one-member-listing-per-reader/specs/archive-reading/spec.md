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

### Requirement: Link targets stored as member data are read only when configured

`ArchiveyConfig` SHALL carry `read_link_targets: bool = True`. Like `listing_limits`, it
is fixed for the reader's lifetime: the reader's own config is the only one it reads.
It governs every symlink whose target the format stores as member data rather than in
the header (ZIP, 7z, RAR3/4), in both access modes.

- `True`: the reader reads such a target the way the format reads member data. On ZIP and
  7z this includes decompression and the full password sequence, provider included. A
  target it cannot read stays unset with `SYMLINK_TARGET_UNAVAILABLE`. RAR3/4 reads only
  stored, unencrypted, single-volume bytes, as before.
  When `extract_all` accepts a link whose target is still unread (a streaming pass
  reaches a ZIP or 7z link before the pass finalizes), it SHALL read that target before
  writing the link, as under `False` below, rather than failing it as a link with no
  target.
- `False`: the reader SHALL NOT read member data for a link target as a side effect of
  listing (the peek, `members()`, `scan_members()`, `members_report()`, `get()`,
  `__iter__`) or of a pass advancing (`stream_members()`, including the child pass
  `extract_all` drives). Such a link keeps `link_target=None`, no
  `SYMLINK_TARGET_UNAVAILABLE` is emitted for it, and the skipped read is not recorded as
  an attempt. This covers RAR3/4 stored targets too, although that read needs no
  decompression or password. Under `False`, a `link_target` set by listing then means
  exactly that the header carries it, whatever the compression method.
  Header-carried targets (RAR5, TAR, ISO) are unaffected.
- Under `False` the reader SHALL read a link's target only when the caller asks for that
  member:
  - `extract_all` SHALL call its `members` selector and its `filter` on the link, with
    `link_target=None`, before reading the target, and SHALL read it only for a link both
    accept. A target it cannot read fails that member as one whose target the archive
    carries but the reader cannot reach, under `OnError`.
  - `open()` / `read()` on a link SHALL follow it as "Transparent link following"
    requires, reading its target first.

  Such a read SHALL fill `link_target` in place on the member, like any late-bound field,
  and a filled target SHALL NOT be read again. A report taken afterwards therefore shows
  targets for the links read this way and `None` for the rest. `False` is a promise about
  what the reader reads on its own, not about what a member ends up holding.
- Under either setting, a read made for extraction can show that the member is not a
  link: a reparse-flagged member whose data is no reparse buffer, which listing would
  have re-typed to a file. `extract_all` SHALL then re-type it the same way, call its
  `filter` again on the re-typed member, and write it as a file. In random access it
  opens the member for its content. A streaming pass has already passed that content,
  so it SHALL fail the member under `OnError`; it SHALL NOT report it as a link with no
  target.

#### Scenario: link-target setting matrix

| Case | Expected |
| --- | --- |
| ZIP with an encrypted symlink, default config, no password, provider supplied | Provider consulted; on failure `link_target` unset with `SYMLINK_TARGET_UNAVAILABLE` |
| Same archive, `read_link_targets=False`, `members()` | No member data read; provider not consulted; `link_target` unset; no diagnostic |
| Same archive, `read_link_targets=False`, password supplied, `extract_all()` | The filter sees the link with `link_target=None`, then the target is read and the link is written |
| Same archive, `read_link_targets=False`, filter rejects members with `link_target is None` | The target is never read; provider not consulted; the link is not written |
| Same archive, `read_link_targets=False`, no password, `extract_all()` | The symlink member fails under `OnError`, as a locked target |
| RAR5 symlink, `read_link_targets=False` | `link_target` set from the header |
| RAR4 stored symlink, `read_link_targets=False`, `members()` | `link_target=None`; no member data read |
| ZIP with two symlinks, `read_link_targets=False`, `extract_all(members=["link-a"])`, then `members()` | `link-a` has its target; `link-b` has `link_target=None` |
| ZIP symlink, `read_link_targets=False`, password supplied, `reader.open("link")` | The target is read, then the link is followed |
| Streaming `extract_all()` over a ZIP symlink, default config | The target is read before the link is written; the link is extracted |
| ZIP member flagged as a reparse point whose data is no reparse buffer, `read_link_targets=False`, `extract_all()` | The filter sees it as a link, then again as a file; random access writes its content; a streaming pass fails it under `OnError` |

## MODIFIED Requirements

### Requirement: Explicit configuration object

The system SHALL define these complete frozen schemas:

```python
@dataclass(frozen=True)
class ExtractionLimits:
    max_extracted_bytes: int | None = 2 * 2**30
    max_ratio: float | None = 1000.0
    ratio_activation_threshold: int = 5 * 2**20
    max_entries: int | None = 1_048_576
    UNLIMITED: ClassVar["ExtractionLimits"]

@dataclass(frozen=True)
class ListingLimits:
    max_members: int | None = 1_048_576
    max_metadata_bytes: int | None = 64 * 2**20
    UNLIMITED: ClassVar["ListingLimits"]

@dataclass(frozen=True)
class DecoderLimits:
    max_decoder_memory: int | None = 2 * 2**30
    UNLIMITED: ClassVar["DecoderLimits"]

@dataclass(frozen=True)
class ArchiveyConfig:
    use_rapidgzip: AcceleratorMode = AcceleratorMode.AUTO
    use_indexed_bzip2: AcceleratorMode = AcceleratorMode.AUTO
    zip_unflagged_fallback_encoding: str = "cp437"
    rar_allow_glob_member_concatenation: bool = False
    read_link_targets: bool = True
    extraction_limits: ExtractionLimits = ExtractionLimits()
    listing_limits: ListingLimits = ListingLimits()
    decoder_limits: DecoderLimits = DecoderLimits()
    diagnostic_policy: DiagnosticPolicy = DiagnosticPolicy()
    max_retained_diagnostic_references: int = 256
    on_diagnostic: Callable[[Diagnostic], None] | None = None
```

`max_retained_diagnostic_references` SHALL be non-negative. Policy/default/override
mappings and the dataclasses SHALL be defensively immutable. `config=None` →
immutable library default. No mutable global/context-local diagnostic policy or
callback.

A reader carries its open config, all of it, for its lifetime. Reader methods
SHALL NOT take a `config=`: `extract_all(limits=...)` is the one per-call
override, and it replaces only the extraction limits for that call.
`decoder_limits` SHALL bound the working memory a codec allocates on the
strength of a number the archive declares, and SHALL be enforced before that
allocation is made. Per-call `limits`
still beat `config.extraction_limits`, then reader/library default. Other
per-call operational args stay outside `ArchiveyConfig`.
`read_link_targets` SHALL decide whether the reader reads, on its own, a symlink target
the format stores as member data (see "Link targets stored as member data are read only
when configured"); like `listing_limits`, it holds for the reader's lifetime.

`on_diagnostic` runs synchronously after count/retention/logging updates. Snapshot
reads from a callback are allowed. Starting another operation on the same
emitting reader/stream SHALL raise `UnsupportedOperationError`; other readers OK.
Callbacks hold no Archivey collector/reader/stream/backend/registry lock
(`diagnostics` / `reader-concurrency`).

#### Scenario: config matrix

| Case | Expected |
| --- | --- |
| `ArchiveyConfig()` | AUTO accelerators; documented extraction and listing defaults; COLLECT; budget 256; no callback |
| `extract(..., extraction_limits=ExtractionLimits(max_ratio=100))` | 100:1 per-member ratio enforced (`safe-extraction`) |
| Reader opened with `listing_limits=ListingLimits(max_members=10)` | Listing caps stay at 10 for the reader lifetime; `extract_all()` has no `config=` to change them |
| Reader opened with `read_link_targets=False` | No data-stored link target is read by listing or a pass for the reader lifetime |

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

Symlink targets stored as member data (ZIP, 7z, RAR3/4) are the one exception, and only
while `read_link_targets` is `True` (see "Link targets stored as member data are read
only when configured"). A pass that finalizes then reads every such target, selected or
not, so the complete report matches random access. That read MAY decompress data the
caller did not select and MAY consult the password provider. On 7z it decodes the link's
folder up to the link, within the budget of `format-7z` "A 7z folder is decoded at most
once for its link targets". With `read_link_targets=False` the promise holds without
exception.

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
| Selector excludes member / stream unread | No open/decompress; no data-path diagnostic. With `read_link_targets=True`, a data-stored symlink target is still read at finalization (see the exception above) |
| Solid archive | Progressive decode; peak = decompressor state + one chunk |
| `stream_members(lambda m: m.name.endswith(".txt"))` | Only `.txt`; unselected never opened, except data-stored symlink targets when `read_link_targets=True`; original mutable members |
| Fully read stream, then inspect member | Late-bound fields (e.g. size/CRC) visible on same object |
| Advance after one yield | Prior stream closed/invalidated first |
| Random `open()` during active pass | `ArchiveyUsageError`; pass remains usable |
| Close/abandon partial generator | Current stream closed; pass ownership released once |
| Random `open()` into solid block | Re-decode from block start + skip; no diagnostic, no warning — discoverable via `reader.cost.access_cost` and the `open()` docstring |
| Unencrypted solid 7z, selector excludes a symlink, pass to the end (default config) | The link's target is resolved; its folder is decoded up to the link once |
| Encrypted solid 7z `[a.txt, link, b.txt]`, `read_link_targets=False`, `stream_members(lambda m: False)` to the end | Nothing decoded; provider never consulted; `link_target` unset; no `SYMLINK_TARGET_UNAVAILABLE` |
