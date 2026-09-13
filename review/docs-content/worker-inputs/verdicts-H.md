# Verdicts — Worker H (Command line)

Config for this pass: **`[all]`** (`uv sync --group dev --extra all`), unless a row notes
otherwise. Spec citations preferred over `src/` when both speak (O-26). `[code]` rows
were executed with `uv run --no-sync` / `.venv/bin/archivey`. Spec line numbers in
Settles-it have drifted; requirements matched by title.

| # | V | Evidence |
|---|---|---|
| H-1 | verified | `cli` archivey command…: bare path → `list`; aliases `l`/`t`/`x`; `info`/`detect`; `--version` (+ `-v` matrix). Ran all six forms on a multi-entry ZIP: list/l listed; `t` → `4 OK, 0 failed`; `x` extracted; `info`/`detect` identity; `--version -v` → version + `formats:`. |
| H-2 | verified | Spec: `test` / `t` = full-read integrity check (digests via shared verification). Ran `archivey t` → quiet summary, exit `0`. |
| H-3 | verified | Spec info/detect: format/identity + `access:` CostReceipt line. Ran `info`/`detect` → `format: zip`, `access: random (indexed)`, no full listing. |
| H-4 | verified | Spec version: `--version -v` → version + `formats:` matrix. **cfg `[all]`**: all listed formats `full`. Ran `--version` and `--version -v` / `-v --version`. |
| H-5 | verified | Spec extract defaults: `strict` / `rename` / `OnError.CONTINUE`. `main.py` argparse defaults match. Spot-check: default extract completed under those defaults. |
| H-6 | verified | Silence/Guide gap true: CLI half is only a bash comment (`cli.md:18`); divergence vs library never stated as such. Library `extract()` defaults `OverwritePolicy.ERROR` + `OnError.STOP` (`core.py`); CLI `rename` + continue. Same `policy=strict`. must-explain #23 / scope §B row 7. |
| H-7 | verified | Spec smart dest: multi top-level → `./<stem>/`. Ran default extract of multi-entry `photos.zip` → `extracting into photos/`; members under `photos/`. |
| H-8 | verified | Spec CONTINUE + safe-extraction stop-on-failure-not-policy. Repro: traversal ZIP → `blocked:` + safe members extracted, exit `3`; CRC-corrupt ZIP → `failed:` + good member extracted, exit `1`. |
| H-9 | verified | Spec: `-d .` = classic cwd splatter opt-in. Ran → members landed in cwd, no `./photos/` wrapper. |
| H-10 | verified | Spec + safe-extraction: `--stop-on-error` = `OnError.STOP` on **failures** only; blocks always continue. Repro: traversal ZIP + `--stop-on-error` → still `blocked:` + safe extract, exit `3`. |
| H-11 | verified | Spec filter rules. Ran: `*.py --exclude '*_test.py'` → only `mod.py`; unmatched include extract/test → exit `1` + warning; list unmatched → warning, exit `0`; sole dir-like unmatched → `(did you mean -d …?)`. |
| H-12 | verified | `[code]` Safer extract demo (`cli.md:17-37`): all **five** runnable lines behave as their comments (default wrap; `-d .`; `--stop-on-error` on clean archive; filters; `--policy trusted -d …`). Claim text says “six”; the block has five invocations (count slip, not a behaviour fail). |
| H-13 | verified | Spec known-verb-wins + bare verbs; `-x` not a mode selector. Ran: `-x` → usage exit `2` with bare-word hint; file named `x` listed via `archivey list ./x` (bare `x` is the extract verb). |
| H-14 | verified | Spec exit codes + `exit_codes.py`. Spot-check: success `0`; unmatched/CRC failure `1`; `--badflag` / missing archive `2`; policy-only blocks `3`. `≥4` reserved in code comment. |
| H-15 | verified | Spec salvage / stdin / reserved verbs. Ran: `--salvage`, `list -`, `hash`/`create`/`convert` → not-implemented / not-supported messages, nonzero. Co-cited `errors-and-diagnostics.md:130-131` / `migrating.md:173-174` agree on salvage (subset). |
| H-16 | verified | Silence/Guide gap true: no `docs/` page states argv/`ps` visibility. Fact true: `--password` is a CLI string flag (`password.py` returns it verbatim). **Note:** `--help` already says “visible in process lists” — guide still silent. Settles-it `format-rar` unrar-argv constraint is a different surface (E-38). |
| H-17 | verified | Silence/Guide gap true: no page states CLI terminal-inert output / `#236`. Spec `cli` Archive-derived text escaped… + `error-handling` construction escape. Repro: member `ev\x1b[2Kil\rSUCCESS.txt` listed as `ev\x1b[2Kil\rSUCCESS.txt` — no raw ESC/CR on stdout. |

## Notes for coordinator

### Wrong rows
- None.

### Silence / Guide gaps (verified as unwritten)
- **H-6** — CLI↔library default divergence (comment-only today)
- **H-16** — passwords on argv / `ps` (pages silent; `--help` already warns)
- **H-17** — terminal escaping / `#236` on the thinnest CLI page

### Config notes (`cfg`)
- Everyday verification: **`[all]`**.
- H-4 matrix: every format line printed `full` under this install.

### Cross-cluster / process
- H-16 ≠ E-38: E-38 is unrar password-on-stdin; H-16 is archivey’s own `--password` argv.
- H-17 ↔ D-52 (errors-page `#236` inert-messages gap) — same change, two guide homes.
- Spec scenario `archivey ./x` → extract vs code (`./x` is a path → default `list`) — soft drift; docs H-13 wording matches code.

### Counts
- **verified:** 17
- **wrong:** 0
- **unverifiable:** 0
