# ISO: contain bad Rock Ridge entries, read zisofs, take `encoding=`

## Why

`pycdlib` refuses a whole image over any System Use entry it cannot parse. A zisofs
image (`ZF`, a valid image) and a genisoimage symlink with a target over 250 bytes (its
`SL` length wraps past 255) therefore failed as `CorruptionError` for every member. Rock
Ridge names carry no charset, and ISO ignored `encoding=`, so a Latin-1 name could only
list escaped.

## What changes

- `format-iso`: System Use bytes are filtered before `pycdlib` parses them, during
  archivey's own opens only; unknown entries are skipped and a malformed entry ends its
  record's area with a diagnostic on that member.
- `format-iso`: zisofs version 1 members read decoded; other variants list and refuse
  to read.
- `format-iso`, `diagnostics`: ISO takes `encoding=` for Rock Ridge and plain names that
  are not valid UTF-8, the TAR PAX rule, so it no longer emits `ENCODING_ARGUMENT_UNUSED`.
  Without `encoding=`, such a Rock Ridge name takes the Joliet name of the same file or
  directory when one lines up.

## Impact

Code: `internal/backends/iso_reader.py`. Docs: `docs/formats.md`,
`docs/opening-and-listing.md`, `dev-docs/formats/iso.md`, `dev-docs/known-issues.md`.
Tests: `tests/test_iso.py`, `tests/test_review_simplicity_consistency.py`.
