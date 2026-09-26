## MODIFIED Requirements

### Requirement: Policy-Specific Metadata Transforms

The system SHALL apply policy-specific permission and ownership transforms to one
transient `ArchiveMember` copy after universal checks pass and before I/O. The
copy receives the policy transform and user `filter` in that order and supplies
the on-disk identity (`name`, mode, timestamps, destination path). The original
mutable member is used for `BombTracker.start_member()` and recorded in
`ExtractionResult`, so late-bound size/CRC/source metadata remain accurate.

```python
class ExtractionPolicy(Enum):
    STRICT = "strict"
    STANDARD = "standard"
    TRUSTED = "trusted"
```

Policies SHALL parallel Python `tarfile`'s `data` / `tar` / `fully_trusted`
mental model while applying uniformly to all formats and retaining Archivey's
non-bypassable safety checks.

| Behavior | `STRICT` default | `STANDARD` | `TRUSTED` |
| --- | --- | --- | --- |
| Path, absolute-path, link-escape, special-file rejection | Always | Always | Always |
| Missing file/dir mode | File `0o644`, dir `0o755` | File `0o644`, dir `0o755` | Apply as stored |
| Permission normalization | Files max `0o644`; dirs `0o755`; strip file execute | Preserve ordinary execute bits | Apply as stored |
| setuid/setgid/sticky | Strip all | Strip all | Preserve |
| uid/gid | Strip | Strip | Apply only when running as root; otherwise skip silently |

#### Scenario: metadata policy matrix

| Case | Expected |
| --- | --- |
| FILE `mode=0o755` under `STRICT` | Written as `0o644` |
| FILE `mode=0o755` under `STANDARD` | Execute bits preserved; setuid/setgid/sticky stripped |
| FILE with uid/gid under `TRUSTED` as root | uid/gid applied |
| Any policy, unsafe path/link/special file | Universal safety rejection still applies |
