# Publish the How it works page

## Why

The docs plan (`review/docs/DECISIONS.md` D2) gives the guide one page on how
Archivey is built, so a reader deciding whether to trust the library does not have to
read the maintainer docs. The plan's one-line-per-ADR summary was dropped in review:
the recorded ADRs are a small, uneven part of the decisions actually made. The page
was never written. The `documentation` spec lists the narrative pages the guide
covers, so adding one is a spec change.

## What changes

- `documentation`: the end-user narrative also covers how the library is built
  (`docs/how-it-works.md`), placed after Philosophy and before the API reference. The
  page carries the design philosophy, architecture rationale and how the library is
  tested, and links to maintainer depth only through absolute GitHub URLs.

## Impact

Docs: `docs/how-it-works.md` (new), `docs/index.md`, `mkdocs.yml`. No code changes.
