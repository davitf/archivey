## ADDED Requirements

### Requirement: A 7z folder is decoded at most once for its link targets

A 7z symlink's target is stored as the member's data, often in the middle of a solid
folder. This refines the folder-decode budget of "Stream solid folders with bounded
memory" for link targets. For its link targets, a 7z folder SHALL be decoded at most once
per reader, and not past the end of its last link member. The consumer's own reads are
covered by the bullets below.

- Random-access listing (`members()`, `scan_members()`) SHALL decode no more of the
  folder for link targets than the end of its last link member.
- A streaming pass SHALL read link targets through its own folder decode. Per folder,
  the pass SHALL decode from the start to the later of the end of the consumer's reads and
  the end of the last link member it reads, and SHALL decode nothing more at EOF. This
  holds when the consumer reads no data, when a link is the last member with data in its
  folder, and when a link is alone in its folder.
- Which links a streaming pass reads when the caller's selector excludes them is set by
  `archive-reading`, "Bounded-memory sequential streaming via stream_members".
- With `read_link_targets=False`, listing and a pass advancing SHALL decode nothing for
  link targets. `extract_all` still reads the targets of the links it accepts. It always
  drives a pass, in both access modes, so it SHALL read them through that pass's own
  folder decoder, within the streaming bullet's bound, counting only accepted links.
  `open()` following a link is an ordinary member read, which MAY re-decode from the folder
  start as "Stream solid folders with bounded memory" allows.

#### Scenario: 7z link-target decode matrix

| Case | Expected |
| --- | --- |
| `members()` on a solid 7z with links before, between and after its file members | Decoded bytes equal each folder's last-link end offset, not the sum of every link's end offset |
| Streaming pass over the same 7z, reading every stream | Every link target resolved; decoded bytes equal the folder sizes, each folder decoded once |
| Streaming pass over the same 7z, reading no stream | Every link target resolved; decoded bytes equal each folder's last-link end offset |
| Non-solid 7z (`-ms=off`) with links | Each link's own folder decoded once, in both modes |
| `read_link_targets=False`, `members()` then a pass reading no stream | No bytes decoded for link targets |
| `read_link_targets=False`, `extract_all()` accepting every member, either mode | Each folder decoded once; accepted links resolved; no second decode for their targets |
