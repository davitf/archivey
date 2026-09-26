# Tasks — member selector spellings and unmatched entries

## 1. Code

- [x] 1.1 Match the directory spelling of a bare `str` entry, with the same rule as
  link-target lookup (`member_name_keys`).
- [x] 1.2 Add `MEMBER_SELECTOR_UNMATCHED` and `SelectorUnmatchedContext`; keep the code out
  of `ARCHIVE_INTEGRITY_CODES`.
- [x] 1.3 Report unmatched entries from `stream_members()` at the end of a complete pass,
  and from `extract_all()` before writing when the member list is free, else at the end of
  the pass.

## 2. Tests and docs

- [x] 2.1 Tests for both spellings, each reporting path, early stop, predicates, and a
  member from another reader.
- [x] 2.2 Update `docs/opening-and-listing.md` and `docs/errors-and-diagnostics.md`.
- [x] 2.3 Archive this change.
