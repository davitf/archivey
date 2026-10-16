# new_docs (experiment)

A rewrite of the user docs, grown one agreed paragraph at a time. Not published and not in
`mkdocs.yml`. `docs/` stays the live site until this replaces it.

Some sentences will go stale when work in progress lands. Check each before the switch, and empty
this list:

- Every ZIP claim in the tree, once the native ZIP reader is complete: damaged or missing end
  records, and bytes after the archive, are expected to change.

Pages linked from these but not written yet: `install.md`. `install.md` should say why RAR needs an
outside program (RARLAB doesn't publish the compression format, and the UnRAR licence forbids
re-creating it from that source), as `docs/` does. `migrating.md`, `philosophy.md`,
`how-it-works.md` and `how-it-is-built.md` aren't linked from `index.md` yet; the site navigation
covers them at the switch. `reading.md` covers `access_cost` and `listing_cost`, but not
`stream_capability` or that the receipt is computed at open and never permits or refuses an access
pattern, which `docs/access-and-cost.md` explains. The API reference doesn't link to `reading.md`
for these types yet. Three limitations the old philosophy page listed have no page here yet:
archivey doesn't modify archives in place, has no async API before 1.0, and isn't a backup engine by
itself. They belong on a future limitations or formats page. The platform and threading table in
`docs/support-matrix.md` has no page here yet, and the pages that linked it no longer do. No check
looks at links in this tree, so a broken one goes unnoticed.

When this tree replaces `docs/`, three pointers outside it move too. `SECURITY.md` links the known
limits at `docs/extracting.md`, and `dev-docs/threat-model.md` names `docs/extracting.md` as its
public half. Both now live in `security.md` and `extracting.md` here. `dev-docs/design-rules.md`
(DR-5 rulings) cites `new_docs/reading.md`, which becomes `docs/reading.md`.
