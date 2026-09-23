## MODIFIED Requirements

### Requirement: members_report_if_available() — a report peek

`members_report_if_available() -> MemberListReport | None` is a **report peek**:
no forward scan, no member-data reads, never consumes the pass. It returns the
stored `MemberListReport` (complete or incomplete) when one exists without scanning,
or the upfront index as a complete report for backends that carry one; else `None`.
Guaranteed fully-resolved complete list → `members()` (RA) or `scan_members()`
(either mode).

| Index topology | Availability |
| --- | --- |
| Leading (ISO) | Both modes, as complete report |
| Scan-based (directory) | `None` until a pass completes — a filesystem walk is not an index (its `listing_cost` is `REQUIRES_SCANNING`), so it has nothing to peek at |
| Trailing (ZIP CD, 7z EOF header) | Both modes today, as complete report (those backends require seekable sources; `SUPPORTS_STREAMING_NON_SEEKABLE` is false). Future trailing+non-seekable → `None` on non-seekable |
| No-index (TAR), no prior materialization/pass | `None` |
| No-index after completed successful pass / `scan_members` / `members` | Complete report |
| No-index after a terminal archive error was stored after a recoverable prefix | Incomplete report (`members` is prefix, `error` set); count is a floor |

Index-only listings SHALL leave data-stored link targets unset (`link_target` /
`link_target_member`); resolving them needs member-data reads that
`members()`/`scan_members()` perform. The members of an upfront-index report SHALL be
the same `ArchiveMember` objects that every other listing method and pass on this
reader returns, and a repeated peek SHALL return the same objects. When
`members()`/`scan_members()` later resolve data-stored link targets, they SHALL fill
them in place on those objects (`archive-data-model`: members are live objects). An
upfront-index listing that ends in terminal archive damage SHALL be returned as the
stored incomplete report (prefix plus `error`), not raised. Returning an incomplete
report to a caller MUST NOT change the complete-or-raise behaviour of `members()` /
`scan_members()` / `get(name)`; the report self-labels via `error` and those methods
still raise.

#### Scenario: index-only listing matrix

| Case | Expected |
| --- | --- |
| Streaming ZIP (upfront index) | Full list; no scan/data read; forward pass still available |
| No-index, not yet iterated | `None` |
| Directory archive, either mode, not yet iterated | `None` — consistent with its own `listing_cost=REQUIRES_SCANNING` |
| No-index after completed pass / `scan_members` | Complete fully-resolved report |
| No-index after incomplete pass already ran | Incomplete report with recovered prefix and `error` |
| ZIP symlink via `members_report_if_available` | Link fields unset; `members`/`scan_members` resolve them |
| `members_report_if_available()` twice on an upfront index | Same member objects both times |
| ZIP symlink held from a peek, then `members()` | `members()` returns that same object, now with its link fields set |
| Upfront index whose listing ends in terminal damage | Incomplete report (prefix plus `error`); `members()` still raises |

## ADDED Requirements

### Requirement: A 7z folder is decoded at most once for its link targets

A 7z symlink's target is stored as the member's data, often in the middle of a solid
folder. Reading the link targets of a 7z folder SHALL decode that folder at most once per
reader, from its start to the end of its last link member.

- Random-access listing (`members()`, `scan_members()`) SHALL decode no more of the
  folder for link targets than the end of its last link member.
- A streaming pass SHALL read link targets through its own folder decode. Per folder,
  the pass SHALL decode from the start to the later of the end of the consumer's reads and
  the end of the last link member, and SHALL decode nothing more at EOF. This holds when
  the consumer reads no data, when a link is the last member with data in its folder, and
  when a link is alone in its folder.

#### Scenario: 7z link-target decode matrix

| Case | Expected |
| --- | --- |
| `members()` on a solid 7z with links before, between and after its file members | Decoded bytes equal each folder's last-link end offset, not the sum of every link's end offset |
| Streaming pass over the same 7z, reading every stream | Every link target resolved; decoded bytes equal the folder sizes, each folder decoded once |
| Streaming pass over the same 7z, reading no stream | Every link target resolved; decoded bytes equal each folder's last-link end offset |
| Non-solid 7z (`-ms=off`) with links | Each link's own folder decoded once, in both modes |
