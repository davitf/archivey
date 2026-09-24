## ADDED Requirements

### Requirement: The CLI uses only public API

The `archivey.cli` package SHALL import nothing from `archivey.internal`. What it
needs beyond `archivey.__all__` SHALL come from a public module, such as
`archivey.terminal` for terminal-safe display. The CLI is the example other
front ends copy, and an internal import would let an internal refactor break it
without touching any public name.

#### Scenario: CLI import boundary

| Case | Expected |
| --- | --- |
| Any module under `src/archivey/cli/` | No `import archivey.internal…` or `from archivey.internal… import` |
| One is added | `tests/test_cli_uses_public_api.py` fails, naming the file and line |
