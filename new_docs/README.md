# new_docs (experiment)

A rewrite of the user docs, grown one agreed paragraph at a time. Not published and not in
`mkdocs.yml`. `docs/` stays the live site until this replaces it.

Some sentences will go stale when work in progress lands. Check each before the switch, and empty
this list:

- Every ZIP claim in the tree, once the native ZIP reader is complete: reading from a pipe,
  damaged or missing end records, and bytes after the archive are all expected to change.

Pages linked from these but not written yet: `install.md`. `install.md` should say
why RAR needs an outside program (RARLAB doesn't publish the compression format, and the UnRAR
licence forbids re-creating it from that source), as `docs/` does. `migrating.md` isn't linked
from `index.md` yet; the site navigation covers it at the switch. The access-cost material in
`docs/access-and-cost.md` has no page here yet; the API reference lists its types without a guide
link. No check looks at
links in this tree, so a broken one goes unnoticed.

When this tree replaces `docs/`, three pointers outside it move too. `SECURITY.md` links the known
limits at `docs/extracting.md`, and `dev-docs/threat-model.md` names `docs/extracting.md` as its
public half. Both now live in `security.md` and `extracting.md` here. `dev-docs/design-rules.md`
(DR-5 rulings) cites `new_docs/reading.md`, which becomes `docs/reading.md`.
