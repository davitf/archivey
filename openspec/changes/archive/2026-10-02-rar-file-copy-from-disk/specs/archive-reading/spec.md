## MODIFIED Requirements

### Requirement: Bounded-memory sequential streaming via stream_members

```python
def stream_members(
    self,
    members: MemberSelector | None = None,
    *,
    file_copy_streams: bool = True,
) -> Iterator[tuple[ArchiveMember, ArchiveStream | None]]: ...
```

Yields `(member, stream)` in archive order with bounded memory. Solid blocks
decompress progressively (never buffered whole); peak = decoder working set + one
in-flight chunk. Non-file members yield `None`.

`file_copy_streams=False` SHALL also yield `None` for a `FILE` member that the archive
stores as a copy of an earlier member (`extra["is_file_copy"]`; only `format-rar` has
them), and the pass SHALL keep nothing for such copies. The copy's bytes and digests are
those of its source, `link_target_member`. The default yields the copy's bytes. A value
that is not a `bool` SHALL raise `ArchiveyUsageError` at the call.

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
| `stream_members(file_copy_streams=False)`, RAR5 file copy | The copy is yielded with stream `None`; its source with its bytes |
| `stream_members(file_copy_streams=0)` | `ArchiveyUsageError` at the call |
