# 0020 — Native ZIP reader; stdlib `zipfile` leaves the library

- **Status:** accepted
- **Date:** 2026-10 (implementation via the `native-zip-reader` OpenSpec change)
- **Provenance:** OpenSpec `native-zip-reader`; `dev-docs/formats/zip.md` §6;
  supersedes [0006](0006-stdlib-zipfile.md)

## Context

ADR 0006 kept stdlib `zipfile` for the central directory because it cost no dependency.
By 0.2.0 archivey already decoded every member itself (codec layer, ZipCrypto, WinZip
AES), and the reader kept `zipfile` only to list. That listing set behaviour archivey
could not change from outside: the whole directory was parsed at open, before
`ListingLimits` saw a member; one damaged entry made the archive unlistable; a lying
UTF-8 flag cost every name; the outcome of several malformed shapes depended on the
Python version; and the reader reached into private names (`_EndRecData`,
`_RealGetContents`, `_ECD_*`) and matched exception text to tell damage from a spanned
set.

## Decision

Parse ZIP structure natively in `internal/backends/zip_parser.py`, pure Python, as 7z
and RAR already are (ADR 0001, 0002). The reader walks the central directory as members
are listed and keeps a small locator per member; member data still goes through the
shared codec layer. `zipfile` stays a test oracle and a fixture writer, never imported
from `src/`.

## Consequences

- Still zero-dependency (ADR 0011); the parser is ours to maintain.
- A damaged directory lists the entries before the damage, then raises, as TAR and RAR
  do. `ListingLimits.max_members` is charged as the directory is walked, and an end
  record declaring more entries than the limit is refused at open.
- Bytes after the end record and its comment are reported as `ARCHIVE_TRAILING_DATA`.
- The outcome no longer depends on the Python version.
- Later stages of the change read a ZIP from a pipe with `streaming=True` and replace
  ADR 0010's refusal for ZIP; until then a non-seekable source is still refused.
- Multi-disk sets stay refused; the 7-Zip `-v` join from 0006's amendment is unchanged.
