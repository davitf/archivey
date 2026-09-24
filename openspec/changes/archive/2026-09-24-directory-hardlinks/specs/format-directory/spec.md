## ADDED Requirements

### Requirement: Report hardlinks as HARDLINK members

The directory backend SHALL list a regular file that has more than one name
inside the root once as `FILE`, under the first name the walk reaches, and every
later name as `HARDLINK` with `link_target` set to that first name and `size`
`None`, as a TAR reader lists the same names. Names are matched by
`(st_dev, st_ino)` from `lstat`. Walk order is sorted per directory, so which
name is first is deterministic. A file whose other names are all outside the
root SHALL list as `FILE`. An entry reporting `st_ino` 0 has no usable identity
and SHALL list as `FILE`. Reading a `HARDLINK` member SHALL return the first
name's content.

Extraction treats these members as it treats a TAR's: with `streaming=True`, a
`HARDLINK` whose first name the selector or filter excluded SHALL fail with
`ExtractionError` (forward-only), where before hardlink detection both names
extracted as independent files.

#### Scenario: directory hardlink matrix

| Tree (`b.txt`, `sub/c.txt` hardlinked to `a.txt`) | Expected |
| --- | --- |
| `a.txt` | `FILE`, `size` 6 |
| `b.txt`, `sub/c.txt` | `HARDLINK`, `link_target == "a.txt"`, `size is None`, `read()` returns `a.txt`'s bytes |
| One name inside the root, one outside | That name lists as `FILE` |
| Linked names on a filesystem reporting `st_ino` 0 | All list as `FILE` |
| `streaming=True`, filter drops `a.txt`, extract | `b.txt` fails with `ExtractionError` |
