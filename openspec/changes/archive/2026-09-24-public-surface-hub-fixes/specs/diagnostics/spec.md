## MODIFIED Requirements

### Requirement: Report an empty listing as a diagnostic

When a member listing completes **without error** and contains **zero members**, the
system SHALL emit `EMPTY_ARCHIVE` exactly once per reader, whatever the format. It MUST
NOT raise: an empty tar is *all zeros*, so no predicate over the bytes separates one from
a zero-filled junk file and any "zero members is an error" rule rejects a file `tar(1)`
itself produces.

The system SHALL NOT restrict the accepted lengths to the canonical ones either. GNU
tar's `-b` blocking factor makes **every** block-aligned zero length a legitimate empty
archive — `tar -b 64 -cf e.tar --files-from /dev/null` emits 32768 zero bytes,
byte-identical to a 32 KiB junk file and listed by `tar -tvf` without error. The two
sizes seen in practice are 1024 (Go `archive/tar`, hence the Docker/OCI empty layer) and
10240 (GNU tar default, Python `tarfile`); a rule admitting only those would emit a false
advisory against valid `-b` output while still being unable to refuse anything.
See `dev-docs/decisions/0015-zero-filled-files-are-valid-empty-tars.md`.

An incomplete listing (one published with an error) SHALL NOT emit it — the member count
is not the archive's.

On an empty listing the system SHALL additionally report **how the format was chosen**,
when that choice was not confirmed against the bytes:

| How the format was chosen | Additional code |
| --- | --- |
| Explicit `format=` argument, and detection now reports a different format or refuses the bytes | `EXPLICIT_FORMAT_LISTED_EMPTY` |
| Extension fallback (`FormatInfo.detected_by == "extension"`), i.e. no magic, probe or far-magic match | `EXTENSION_FORMAT_UNCONFIRMED` |
| Magic, content probe, far magic, or a directory path | none — the bytes confirmed the format |

`format=` SHALL remain an override: neither code refuses the open. The
`EXPLICIT_FORMAT_LISTED_EMPTY` re-detection SHALL run **only** on an empty listing, and
only when the source is a filesystem path, where reopening it cannot disturb the reader;
for a stream source the check is skipped rather than reaching into a live source's
position. The path re-detected is the source as resolution left it — the first volume
of a multi-volume set, the volume a self-extracting stub was followed to — not the name
the caller passed; a joined set has no single path and skips the check. A directory
path opened with `format=DIRECTORY` counts as chosen by the filesystem, not by argument.

#### Scenario: empty listing matrix

| Case | Expected |
| --- | --- |
| `tar cf empty.tar --files-from /dev/null` | Opens, 0 members, `EMPTY_ARCHIVE`, no error |
| 32 KiB of zeros named `z.tar` | Opens, 0 members, `EMPTY_ARCHIVE` **and** `EXTENSION_FORMAT_UNCONFIRMED` |
| `open_archive(iso_path, format=TAR)` | Opens, 0 members, `EMPTY_ARCHIVE` **and** `EXPLICIT_FORMAT_LISTED_EMPTY` with `detected_format="ISO"` |
| `open_archive(tar_path, format=TAR)` on a real one-member tar | No diagnostic |
| Empty directory opened with `format=DIRECTORY` | `EMPTY_ARCHIVE` only |
| Empty ZIP / empty 7z | `EMPTY_ARCHIVE`; no format code (magic confirmed the bytes) |
| A legitimately empty tar (all zeros, so no magic) | `EMPTY_ARCHIVE` **and** `EXTENSION_FORMAT_UNCONFIRMED` — truthful: the bytes really did not confirm it |
| Listing published with an error and zero members | No `EMPTY_ARCHIVE` |
