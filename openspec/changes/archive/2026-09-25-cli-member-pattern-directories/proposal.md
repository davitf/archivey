# CLI member patterns select a directory by name

## Why

The library's `members=` matches stored names exactly, and a directory's stored name
ends in `/`. The CLI passed its patterns straight to `fnmatchcase`, so
`archivey extract a.zip docs` selected nothing when the archive held `docs/`. A person
at a shell expects `tar`'s behaviour: naming a directory selects it and its contents.
On Windows people also type `\` as the separator.

## What changes

- `cli`: a positional pattern or `--exclude` also matches with the trailing `/`
  removed and `/` or `/*` appended, so `docs` selects `docs/` and everything under it.
- `cli`: on Windows a `\` in a pattern is read as `/`. On other systems it stays
  literal, because a TAR member name can contain one.
- The unmatched-pattern warning uses the same matching.
- The library's `members=` is unchanged and stays exact. The maintainer chose to keep
  this matching in the CLI for now.

## Impact

Code: `src/archivey/cli/filters.py`. Tests: `tests/test_cli_member_patterns.py`.
Docs: `docs/cli.md`.
