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
