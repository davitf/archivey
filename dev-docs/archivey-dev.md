# Reference repository: `archivey-dev`

`archivey-dev` is the **v1 / DEV** codebase that v2 selectively ported from and whose
`openspec/changes/` contain the native-reader explorations. The port is done; open it
when you need the reasoning behind the native 7z and RAR parsers or an old test fixture.
The v1 tree users knew is [`davitf/archivey-old`](https://github.com/davitf/archivey-old).

**How to access it.** It is a separate repository and is not in an agent session's
GitHub-tool scope. A plain HTTPS `git clone` works:

```bash
git clone https://github.com/davitf/archivey-dev.git /tmp/archivey-dev
```

- The GitHub **API** (and WebFetch against `api.github.com`) is rate-limited for
  unauthenticated calls and returns `403`. That is rate limiting, not a private repo; use
  `git clone` instead.
- Pin to a specific commit when you compare against it. The specs here were written
  against `730275b7a755f8b5b8d08d3d4d9b267b5bdadb0d` (default branch HEAD; the clone
  carries no release tags).

High-value paths inside it:

- `openspec/changes/sevenzip-native-reader/` and
  `openspec/changes/rar-native-metadata-reader/` (+ `docs/*-native-reader-design.md`) —
  the native-parser designs this repo's `format-7z` / `format-rar` specs follow.
- `src/archivey/` — the v1 source this repo was ported from.
- `tests/` — the declarative test harness and fixtures.
