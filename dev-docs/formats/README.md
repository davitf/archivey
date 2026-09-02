# Format handbook pages

Living, **no-fluff** per-format notes for maintainers and pair agents. Rewrite in
place when behaviour changes. This is the preferred home for format decisions —
not a new ADR per choice.

Published user-facing format matrix: [`docs/formats.md`](../../docs/formats.md).
Dense agent contracts (optional reading): `openspec/specs/format-*`.

## Page skeleton

Create `dev-docs/formats/<format>.md` (e.g. `7z.md`, `rar.md`) with:

1. **Role here** — what archivey supports and refuses  
2. **How it works in this repo** — entrypoints (link [`code-map`](../code-map.md)), solid/seek, codecs  
3. **Consequences** — performance, memory, password/crypto, bomb edges, platform traps  
4. **Decisions (light)** — bullets: choice → why → rejected alternative  
5. **Open pitfalls** — including known doc/code lies  
6. **Verify** — tests or commands that pin the claims  

## Pilot

Next change that touches **7z** or **RAR**: create that page and fold useful lines from
`dev-docs/decisions/0001-*` / `0002-*` and relevant `investigations/` into it. Leave old
ADRs in place until the page is trusted, then treat them as historical.

## Index (fill as pages appear)

| Format | Page | Status |
| --- | --- | --- |
| ZIP | — | not started |
| TAR / compressed TAR | — | not started |
| 7z | — | pilot candidate |
| RAR | — | pilot candidate |
| ISO | — | not started |
| Directory | — | not started |
| Single-file compressors | — | not started |
