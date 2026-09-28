# Refuse a directory walk into a subdirectory that was replaced

## Why

The directory walk listed a subdirectory, then scanned it by path. When the
subdirectory was replaced by a symlink in between, the scan followed the link and the
listing published names, sizes and times from outside the root. Reads already refused
a replaced path; the walk did not. The `format-directory` spec named only two race
outcomes, a vanished entry and a genuine error, so it said nothing about this case.

## What changes

On POSIX, the walk opens each subdirectory without following a symlink and checks that
it is the directory the parent's scan recorded (device and inode). A subdirectory that
is now a symlink, or a different directory, stops the listing with an `OSError`
carrying `errno.ESTALE`. A vanished subdirectory is still skipped with
`SCAN_DIRECTORY_VANISHED`, because nothing outside the root is read in that case.

## Impact

- `format-directory`: the scan-race requirement gains the replaced-subdirectory
  outcome, and the matrix gains a row.
- Code: `DirectoryReader` in `internal/backends/directory_reader.py`.
