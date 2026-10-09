# new_docs (experiment)

A rewrite of the user docs, grown one agreed paragraph at a time. Not published and not in
`mkdocs.yml`. `docs/` stays the live site until this replaces it.

Pages linked from these but not written yet: `install.md` and `api.md`. No check looks at
links in this tree, so a broken one goes unnoticed.

When this tree replaces `docs/`, two pointers outside it move too: `SECURITY.md` links the known
limits at `docs/extracting.md`, and `dev-docs/threat-model.md` names `docs/extracting.md` as its
public half. Both now live in `security.md` and `extracting.md` here.

`opening.md` describes how names are decoded after a change that hasn't landed: a name that is
valid UTF-8 stays UTF-8 even with `encoding=`. Today ZIP, USTAR TAR and RAR 1.5-4 8-bit names let
`encoding=` override it, as the `format-zip` spec requires. This tree can't replace `docs/` until
the code and that spec change, which is tracked internally.
