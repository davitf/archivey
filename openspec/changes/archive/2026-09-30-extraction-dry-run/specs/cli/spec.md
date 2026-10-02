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

## MODIFIED Requirements

### Requirement: The CLI uses only public API

The `archivey.cli` package SHALL import nothing from `archivey.internal`, with one
exception: `--track-io` imports `archivey.internal.measurement`, because the CLI is
also a debugging tool for the library and IO measurement is not public API. What it
needs beyond `archivey.__all__` SHALL otherwise come from a public module, such as
`archivey.terminal` for terminal-safe display. The CLI is the example other
front ends copy, and an internal import would let an internal refactor break it
without touching any public name. `extract --dry-run` also reads one private field,
`ExtractionReport._dry_run_top_level`: the entries the dry run left at the top of its
scratch copy of the destination. A dry run writes nothing the CLI could look at
instead, and renaming the field breaks the dry run's hoist line and summary.

#### Scenario: CLI import boundary

| Case | Expected |
| --- | --- |
| Any module under `src/archivey/cli/` | No `import archivey.internal…` or `from archivey.internal… import`, except the one allowlisted measurement import |
| One is added | `tests/test_cli_uses_public_api.py` fails, naming the file and line |
| The allowlisted import is removed | The same test fails until the allowlist entry goes too |
