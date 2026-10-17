# new_docs (experiment)

A rewrite of the user docs, grown one agreed paragraph at a time. Not published and not in
`mkdocs.yml`. `docs/` stays the live site until this replaces it.

Some sentences will go stale when work in progress lands. Check each before the switch, and empty
this list:

- Every ZIP claim in the tree, once the native ZIP reader is complete: damaged or missing end
  records, and bytes after the archive, are expected to change.
- `install.md` says RAR is partly supported without RARLAB's `unrar`, which is true only once PR 868
  merges.

`migrating.md`, `philosophy.md`, `how-it-works.md`, `how-it-is-built.md` and `acknowledgements.md`
aren't linked from `index.md` yet; the site navigation covers them at the switch. `reading.md`
covers `access_cost` and `listing_cost`, but not `stream_capability` or that the receipt is computed
at open and never permits or refuses an access pattern, which `docs/access-and-cost.md` explains.
The API reference doesn't link to `reading.md` for these types yet. Three limitations the old
philosophy page listed have no page here yet: archivey doesn't modify archives in place, has no
async API before 1.0, and isn't a backup engine by itself. They belong on a future limitations or
formats page. The RAR details `install.md` leaves to the formats page (what `unar` can't read,
per-release `unrar` versions, unnotarized RARLAB macOS builds) have no page here yet. Two facts from
the old pages have no page here yet: on NTFS a hard link past a file's 1,024th name is written as a
copy, and those copies count toward `max_extracted_bytes` and the archive-wide `max_ratio`; and the
old thread-safety table's `close()` row (safe to call twice, not safe to race in-flight opens),
which needs checking against the current code before it moves. No check looks at links in this tree,
so a broken one goes unnoticed.

When this tree replaces `docs/`, three pointers outside it move too. `SECURITY.md` links the known
limits at `docs/extracting.md`, and `dev-docs/threat-model.md` names `docs/extracting.md` as its
public half. Both now live in `security.md` and `extracting.md` here. `dev-docs/design-rules.md`
(DR-5 rulings) cites `new_docs/reading.md`, which becomes `docs/reading.md`.
