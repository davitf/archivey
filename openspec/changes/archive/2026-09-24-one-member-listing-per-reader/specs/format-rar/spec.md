## MODIFIED Requirements

### Requirement: Resolve RAR link targets when possible at list time

The system SHALL set `ArchiveMember.link_target` during member registration /
`_ensure_link_target` whenever the target is available without interactive input:

| Variant | Source of `link_target` |
| --- | --- |
| RAR5 symlink / Windows symlink / junction | native `file_redir` target string |
| RAR5 hardlink / `FILE_COPY` | native `file_redir` target string (`MemberType.HARDLINK`) |
| RAR4 Unix symlink | stored member bytes (direct read when M0 / readable without `unrar`), only while `read_link_targets` is `True` |

Encrypted link targets without a usable password MAY leave `link_target` unset and emit
the existing symlink-target diagnostic; listing MUST still succeed. With
`read_link_targets=False`, listing reads no RAR4 member bytes for a link target and emits
nothing for it; RAR5 `file_redir` targets are set as before (`archive-reading`, "Link
targets stored as member data are read only when configured").

#### Scenario: link-target resolution matrix

| Case | Expected |
| --- | --- |
| RAR5 symlink | `type=SYMLINK`, `link_target` set from `file_redir` |
| RAR5 hardlink or `FILE_COPY` | `type=HARDLINK`, `link_target` set; `open()` follows to target data |
| RAR4 stored symlink | `link_target` equals stored target bytes decoded as text |
| Encrypted RAR4 symlink, no password | `link_target` may be unset; no crash on list |
| RAR4 stored symlink, `read_link_targets=False` | `link_target=None`; no member bytes read; RAR5 targets still set |
