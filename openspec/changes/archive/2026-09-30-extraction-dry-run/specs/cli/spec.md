## ADDED Requirements

### Requirement: extract --dry-run reports without writing

`archivey extract --dry-run` SHALL run the extraction with `dry_run=True`. It SHALL
print the same per-member lines and return the same exit code as the same command
without `--dry-run` would against an empty destination, and its closing summary SHALL
say that nothing was written. With no `-d`, it SHALL name the smart default destination
it would use, and SHALL NOT move anything. Where a real run would move a single
top-level entry out of that destination, it SHALL name where that entry would land,
and use that place in the closing summary. It SHALL NOT check for collisions with
entries already at that place.

#### Scenario: extract dry-run matrix

| Case | Expected |
| --- | --- |
| `archivey extract <archive> --dry-run` | stderr names `would extract into <stem>/`; summary begins `dry run, nothing written:`; nothing created in the cwd |
| `archivey extract <archive-with-traversal> --dry-run` | `blocked:` line; exit `3` |
| `archivey extract <archive> -d out --dry-run` | `out` is not created |
| `archivey extract <tar-with-single-root-src> --dry-run` | stderr names `would move to src/`; summary ends `→ src/`; nothing created in the cwd |
