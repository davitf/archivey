## ADDED Requirements

### Requirement: List every ISO directory record as its own member

The ISO backend SHALL walk directory records, not names: each record is one
member, and its name is a rendering of the record rather than a key looked up
again. A name that does not round-trip through pycdlib's own path lookup (a Rock
Ridge name holding `/`, two entries sharing one Rock Ridge name) SHALL NOT cost
any other member, and each directory extent SHALL be descended at most once.

Member type SHALL come from the Rock Ridge PX mode when one is present: a
directory or symlink as already recognised, a regular file as `FILE`, and any
other file type (device node, FIFO, socket) as `OTHER` with `size=None`.

In the plain ISO 9660 namespace the `;N` file version SHALL be removed from the
presented name together with the `.` of an empty extension (`FOO.;1` is `FOO`),
and recorded as `extra["iso.version"]`. When a directory holds several versions
of one name, the highest takes the bare name and the others SHALL be presented
as `name;N` with `is_current=False`, the RAR file-version history shape.

In the Rock Ridge namespace the relocation directory (a root-level directory
every child of which is a relocated directory, its `..` carrying a PL record)
SHALL NOT be listed; the relocated subtrees appear at their logical place.

#### Scenario: ISO record matrix

| Case | Expected |
| --- | --- |
| Rock Ridge name `a/a` beside `bbb` | Both list; `bbb` reads |
| Two directories with Rock Ridge name `dup` | Both list as `dup/` |
| PX mode `0o020666` (char device) | `type=OTHER`, `size=None`; extraction skips it |
| Plain `FOO.;1` and `FOO.;2` | `FOO` (version 2, current) and `FOO;1` (version 1, `is_current=False`); extraction writes version 2 |
| Rock Ridge tree 12 directories deep | Logical tree lists in full; no `rr_moved` member |
