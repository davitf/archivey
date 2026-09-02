# Format handbook pages

Living, **no-fluff** per-format notes for maintainers and pair agents. Rewrite in
place when behaviour changes. This is the preferred home for format decisions —
not a new ADR per choice.

Published user-facing format matrix: [`docs/formats.md`](../../docs/formats.md).
Dense agent contracts: `openspec/specs/format-*`.

## Page skeleton

Create `dev-docs/formats/<format>.md` (e.g. `7z.md`, `rar.md`) with:

1. **Role here** — what archivey supports and refuses  
2. **How it works in this repo** — entrypoints (link [`code-map`](../code-map.md)), solid/seek, codecs  
3. **Consequences** — performance, memory, password/crypto, bomb edges, platform traps  
4. **Decisions (light)** — bullets: choice → why → rejected alternative  
5. **Open pitfalls** — including known doc/code lies  
6. **Verify** — tests or commands that pin the claims  

## Index

Fill rows when a real page exists. Until then, route decisions to the seeds in
[`../pair-workflow.md`](../pair-workflow.md) / ADRs / investigations, then create the
page as part of that change (see
[`../discussions/2026-09-pair-workflow-adoption.md`](../discussions/2026-09-pair-workflow-adoption.md)).

| Format | Page | Status |
| --- | --- | --- |
| *(none yet)* | — | create on first real format change |
