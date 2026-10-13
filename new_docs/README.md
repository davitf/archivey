# new_docs (experiment)

A rewrite of the user docs, grown one agreed paragraph at a time. Not published and not in
`mkdocs.yml`. `docs/` stays the live site until this replaces it.

Some sentences describe behaviour that another PR brings, so they aren't true of `main` until it
merges. Check each before the switch, and empty this list:

- `reading.md`, "Reading once": `open`, `read` and `members()` work inside a `stream_members` loop
  under `concurrent_members=True` (PR 786).
- `security.md`, the listing limits on `stream_members()` (PR 773).

Pages linked from these but not written yet: `install.md` and `api.md`. No check looks at
links in this tree, so a broken one goes unnoticed.

When this tree replaces `docs/`, two pointers outside it move too: `SECURITY.md` links the known
limits at `docs/extracting.md`, and `dev-docs/threat-model.md` names `docs/extracting.md` as its
public half. Both now live in `security.md` and `extracting.md` here.
