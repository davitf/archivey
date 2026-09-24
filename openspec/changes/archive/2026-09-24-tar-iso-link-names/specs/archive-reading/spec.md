## MODIFIED Requirements

### Requirement: Transparent link following

`open()` / `read()` SHALL follow symlinks and hardlinks through shared reader
logic; `open()` SHALL keep returning `ArchiveStream` after following.

**Hardlinks (positional):** most recent matching target **strictly before** the
link (TAR/RAR5 model). Malformed later-only source: RA falls back to the later
member (extraction recovers — see `format-tar`); streaming cannot resolve forward
and fails per `OnError`. Modes SHALL agree on hardlink resolution for the same
archive.

**Symlinks:** RA → last matching target overall; streaming → latest seen so far
(forward stays `link_target_member is None`). Forward-visibility difference is
inherent to a single pass.

**Target-name resolution:** hardlink targets are archive-root relative and in
the member-name namespace: `.` and empty segments are dropped and `..` is
**retained**, exactly as `normalize_member_name` treats a name, so `a/../b` names
the member stored as `a/../b` and never the member `b`. Symlink targets are
filesystem paths: they join to the link's directory first and `..` is collapsed.
Absolute/`..`-escaping targets of either kind stay unresolved (`None`; open →
`LinkTargetNotFoundError`); the escape test runs on the collapsed form. Directory
lookup tries bare and `/`-suffixed forms.

Follow chains recursively; detect cycles by **member id** (not name); no arbitrary
depth limit. Missing target → `LinkTargetNotFoundError`; cycle → `ReadError`.
Terminal fully-dereferenced target (when known) is `member.link_target_member`
(see `archive-data-model`). Diagnostics for a linked open cover follow + read of
that one `open()` operation.

#### Scenario: link resolution matrix

| Case | Expected |
| --- | --- |
| Valid chain to file data | One `ArchiveStream`; diagnostics cover follow + read of that open |
| Hardlink → earlier file | Stream yields that file's data |
| Missing target | `LinkTargetNotFoundError` |
| Chain revisits member id | `ReadError` (cycle); no infinite recursion |
| Symlink → file in archive | Stream yields target file data |
| Symlink `dir/link` → `file` / `./file` | Lookup `dir/file`, not root-relative `file` |
| Hardlink → `a/../b`, archive holds both `a/../b` and `b` | Resolves to the `a/../b` member; never `b` |
| Symlink `dir/link` → `../file` | Lookup `file` |
| Absolute / `..`-escaping symlink | `link_target_member is None`; open → `LinkTargetNotFoundError` |
| Duplicate names, hardlink | Most recent occurrence strictly before the link |
| Duplicate names, symlink (RA) | Last occurrence overall |
| Hardlink source only later | RA falls back to later member; streaming cannot resolve |
| Two distinct same-named members on one chain | Not a cycle (id-based tracking) |
