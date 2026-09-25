# Docstring cross-references render as links

## Why

The docstrings use Sphinx cross-reference roles (`:class:`~archivey.ArchiveMember``).
mkdocstrings does not understand them, so 129 reached the published API page as literal
text, and `mkdocs build --strict` stayed green: to Markdown a role is plain text. The
`documentation` spec promised that the strict build catches broken cross-references, but
these references never reached the build as cross-references at all.

## What changes

- `documentation`: a Griffe extension renders each docstring role as a link when its
  target has an anchor on the site, and as inline code otherwise.
- `documentation`: the CI requirement gains a check on the built site. The strict build
  cannot see these references, because a role becomes an *optional* reference, and an
  optional reference that finds no anchor is not a warning. The check fails on a role
  left as text, and on a role whose target does not resolve unless that target is on a
  recorded list of targets known to have no anchor.

## Impact

Code: `scripts/griffe_extensions.py`, `scripts/check_docs_rendered.py`,
`scripts/check.sh`, `.github/workflows/ci.yml`. Docs: `docs/formats.md`,
`CONTRIBUTING.md`, `AGENTS.md`.
