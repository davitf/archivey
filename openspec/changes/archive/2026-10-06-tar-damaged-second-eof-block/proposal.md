# List a TAR whose second end-of-archive block is damaged

## Why

A TAR whose first end-of-archive block is zero and whose second block is not raised
`CorruptionError`, the same as a header tarfile rejected mid-archive. In random access
that lost the whole listing, although every member was there and whole. GNU tar ("A
lone zero block") and 7-Zip list every member with a warning and exit 0. The maintainer
ruled on 2026-10-06 to keep the listing: the damaged block is the
`ARCHIVE_EOF_MARKER_MISSING` diagnostic, and the strict policy refuses it, as was done
for a RAR end-of-archive block that fails its CRC.

## What changes

The reader records whether tarfile's last header parse stopped on a zero block. When it
did, after at least one member, a non-null block after it is reported as a damaged
end-of-archive marker under the ordinary diagnostic policy. A rejected header stays
`CorruptionError`.

## Impact

- `format-tar`: "Detect truncated TAR archives" gains the damaged-second-block case.
- Code: `internal/backends/tar_reader.py`.
- Docs: `dev-docs/formats/tar.md`; a note on the archived 2026-07-19
  `decide-strict-archive-eof-default` design.
