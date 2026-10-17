# new_docs (experiment)

A rewrite of the user docs, grown one agreed paragraph at a time. Not published and not in
`mkdocs.yml`. `docs/` stays the live site until this replaces it.

Some sentences will go stale when work in progress lands. Check each before the switch, and empty
this list:

- Every ZIP claim in the tree, once the native ZIP reader is complete: damaged or missing end
  records, and bytes after the archive, are expected to change.
- Two RAR claims in `formats.md` disagree with the code and wait on a code decision. The RAR
  section says that when no RARLAB program is found, archivey uses `unar`; the error hint in
  `src/archivey/internal/backends/rar_unrar.py` says `unar` is not supported as a substitute. And
  the `unar` refusal list says every member of a solid RAR 2.x-4.x archive with encrypted,
  compressed data is refused; the code refuses those only in a `stream_members()` pass, while
  `open()` of a plain member behind an encrypted one still goes to `unar`. Settle each by changing
  the code or the sentence.

`cli.md`, `formats.md`, `migrating.md`, `philosophy.md`, `how-it-works.md`, `how-it-is-built.md` and
`acknowledgements.md` aren't linked from `index.md` yet; the site navigation covers them at the
switch. `reading.md` covers `access_cost` and `listing_cost`, but not `stream_capability` or that
the receipt is computed at open and never permits or refuses an access pattern, which
`docs/access-and-cost.md` explains. The API reference doesn't link to `reading.md` for these types
yet. Three limitations the old philosophy page listed have no page here yet: archivey doesn't modify
archives in place, has no async API before 1.0, and isn't a backup engine by itself. They belong on
a future limitations page or the formats page. Two facts from the old pages have no page here yet:
on NTFS a hard link past a file's 1,024th name is written as a copy, and those copies count toward
`max_extracted_bytes` and the archive-wide `max_ratio`; and the old thread-safety table's `close()`
row (safe to call twice, not safe to race in-flight opens), which needs checking against the current
code before it moves. No check looks at links in this tree, so a broken one goes unnoticed.

When this tree replaces `docs/`, three pointers outside it move too. `SECURITY.md` links the known
limits at `docs/extracting.md`, and `dev-docs/threat-model.md` names `docs/extracting.md` as its
public half. Both now live in `security.md` and `extracting.md` here. `dev-docs/design-rules.md`
(DR-5 rulings) cites `new_docs/reading.md`, which becomes `docs/reading.md`.
