# A name selector matches the directory spelling, and an unmatched entry is reported

## Why

A directory member's name carries a trailing `/`, and a caller does not write it. So
`extract_all(members=["dir"])` and `stream_members(members=["dir"])` selected nothing on
an archive holding `dir/`. The call returned an empty result and no diagnostic, which
looks the same as a successful call on an archive without that directory. Link-target
lookup already tries both spellings; name selection did not.

The same silence hides a typo: `members=["notes.txt"]` on an archive that holds `note.txt`
selects nothing and says nothing. The maintainer ruled for both fixes.

## What changes

- `archive-reading`: a `str` selector entry that does not end in `/` also matches the
  member with that name plus `/`. An entry that ends in `/` matches only itself. This is
  the rule that link-target lookup uses.
- `diagnostics`: a new code, `MEMBER_SELECTOR_UNMATCHED`, with a new context,
  `SelectorUnmatchedContext`. One diagnostic is emitted for each collection entry that
  matched no member, after every member has been offered to the selector. It is not in
  `ARCHIVE_INTEGRITY_CODES`, because it reports the caller's argument, not the archive.
- A predicate selector is never reported. A `stream_members()` pass that the caller stops
  early reports nothing, because a later member could still have matched.

## Impact

- Code: `internal/selection.py` (the matching rule and the unmatched record),
  `internal/base_reader.py` (`stream_members`), `internal/extraction.py`
  (`extract_all`), `diagnostics.py` (the code and the context), and the public export of
  `SelectorUnmatchedContext`.
- Behaviour: `members=["dir"]` now selects `dir/`. A call that selected nothing because of
  the missing `/` now selects the directory. Under `extract_all` that creates the directory,
  but not its contents: a directory entry does not select its children.
- Docs: `docs/opening-and-listing.md` and `docs/errors-and-diagnostics.md`.
