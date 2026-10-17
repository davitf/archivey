## MODIFIED Requirements

### Requirement: Resolve RAR link targets when possible at list time

The system SHALL set `ArchiveMember.link_target` during member registration /
`_ensure_link_target` whenever the target is available without interactive input,
except an encrypted RAR 2.9-4 target (below), which uses the password, and so the
password provider, that a read of the member would use:

| Variant | Source of `link_target` |
| --- | --- |
| RAR5 symlink | native `file_redir` target string |
| RAR5 Windows symlink / junction | native `file_redir` target string, normalized as a reparse buffer's (`\??\C:\Windows` → `C:/Windows`, `..\up\x` → `../up/x`) |
| RAR5 hardlink / `FILE_COPY` | native `file_redir` target string (`MemberType.HARDLINK` / `MemberType.FILE`) |
| RAR4 Unix symlink | stored member bytes (direct read when M0 / readable without `unrar`), only while `read_link_targets` is `True` |

A RAR 2.9-4 symlink whose stored target is encrypted SHALL be decrypted at listing as
its member read would be (stored, in one part, `cryptography` installed), and its target
set when the password decrypts it to bytes its CRC32 matches. Without a password the
target SHALL stay unset with `SYMLINK_TARGET_UNAVAILABLE` (`reason="password_required"`);
a password that fails the CRC SHALL leave it unset with `reason="password_or_damage"`,
since RAR 2.9-4 records no password check and a wrong key looks like damage. A target
archivey cannot decrypt itself (compressed, split, RAR 2.0's cipher, or no
`cryptography`) SHALL stay unset with `reason="target_data_encrypted"`. Listing MUST
still succeed in every case. With
`read_link_targets=False`, listing reads no RAR4 member bytes for a link target and emits
nothing for it; RAR5 `file_redir` targets are set as before (`archive-reading`, "Link
targets stored as member data are read only when configured").

#### Scenario: link-target resolution matrix

| Case | Expected |
| --- | --- |
| RAR5 symlink | `type=SYMLINK`, `link_target` set from `file_redir` |
| RAR5 hardlink | `type=HARDLINK`, `link_target` set; `open()` follows to target data |
| RAR5 `FILE_COPY` | `type=FILE`, `link_target` and `link_target_member` name the source; `open()` reads the source's data |
| RAR4 stored symlink | `link_target` equals stored target bytes decoded as text |
| Encrypted stored RAR4 symlink, right password (one, or in a list) | `link_target` set; no program started |
| Encrypted stored RAR4 symlink, no password | `link_target=None`; `reason="password_required"`; listing succeeds |
| Encrypted stored RAR4 symlink, wrong password | `link_target=None`; `reason="password_or_damage"`; listing succeeds |
| Encrypted stored RAR4 symlink, no `cryptography` | `link_target=None`; `reason="target_data_encrypted"` |
| RAR4 stored symlink, `read_link_targets=False` | `link_target=None`; no member bytes read; RAR5 targets still set |
