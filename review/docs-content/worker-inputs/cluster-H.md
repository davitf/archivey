# H. Command line

Spec: `cli`. Page: `cli` (48 lines — the thinnest page against the largest recent change
to CLI output).

| # | Claim | Stated at | Settles it | Ruling | V |
|---|---|---|---|---|---|
| H-1 | The six verb forms in the block are correct: bare path = `list`, `l`, `t`, `x`, `info`/`detect`, `--version -v` | `cli.md:6-13` | `cli:16`, `cli:213`, `cli:233`, `src/archivey/cli/main.py:229-330` | Keep | |
| H-2 | `archivey t` is a **full-read integrity check** | `cli.md:9` | `cli:16`, `src/archivey/cli/test_cmd.py` | Keep | |
| H-3 | `archivey info` reports format / identity **and access cost** | `cli.md:11` | `cli:213` | Keep | |
| H-4 | `archivey --version -v` prints version **plus the format availability matrix for this install** | `cli.md:12` | `cli:233` | Keep · `cfg` | |
| H-5 | **CLI extract defaults are `policy=strict`, `overwrite=rename`, `on_error=continue`** | `cli.md:18` | `src/archivey/cli/main.py:267-292`, `cli:16` | Keep, restructure | |
| H-6 | **must-explain #23, unwritten as its own block:** those CLI defaults **diverge from the library**, which defaults to `ERROR` / `STOP` — "it is what breaks scripts ported from one to the other" | `cli.md:18-22` states the CLI half **inside a bash comment**; the divergence is never stated as such | `src/archivey/cli/main.py:267-292` vs `src/archivey/internal/extraction_types.py:75`, `:94` | **Guide, ~6 lines** — `scope.md` §B row 7 | |
| H-7 | **With no `-d`, a multi-entry archive lands in `./<stem>/` rather than the current directory** (tarbomb-safe) | `cli.md:18-20` | `cli:16`, `src/archivey/cli/extract_cmd.py:96-142` | Keep, restructure | |
| H-8 | **Hostile/corrupt members are reported and skipped; remaining members are still extracted** | `cli.md:20-21` | `cli:16`, `safe-extraction:712` | Keep, restructure | |
| H-9 | `-d .` is the opt-in for classic unzip-into-cwd | `cli.md:25-26` | `cli:16` | Keep | |
| H-10 | `--stop-on-error` is all-or-nothing on member **failures** (library `STOP`); **policy blocks are still reported and skipped** | `cli.md:28-30` | `cli:277`, `safe-extraction:712` | Keep | |
| H-11 | **Filters:** positionals are includes, `--exclude` subtracts; unmatched includes warn on stderr; **extract/test exit 1 when nothing matched** while list warns but stays 0; a sole unmatched pattern that looks like a destination gets a `-d` hint | `cli.md:32-35` | `cli:16`, `cli:277`, `src/archivey/cli/filters.py` | Keep | |
| H-12 | `[code]` all six bash invocations in the demo run and behave as their comments say | `cli.md:17-37` | — (executable) | Keep, restructure | |
| H-13 | **Verbs are bare words**; dash-prefixed forms like `-x` are not mode selectors, and a file whose name is a verb word is reached with an explicit verb (`archivey list ./x`) | `cli.md:41-43` | `cli:261`, `src/archivey/cli/main.py:229-330` | Keep | |
| H-14 | **Exit codes:** `0` success · `1` operation failed or extract aborted on a member failure · `2` usage (argparse) · `3` extract **completed** with ≥1 policy block and no member failure, under CONTINUE or STOP · **`≥4` reserved** | `cli.md:44-47` | `cli:277`, `src/archivey/cli/exit_codes.py:5-11` | Keep — exit `3` is the one an automation author must handle | |
| H-15 | `--salvage`, stdin (`-`), and `hash` / `create` / `convert` are **reserved for later** | `cli.md:48`, `errors-and-diagnostics.md:130-131`, `migrating.md:173-174` | `cli:247`, `cli:261`, `cli:308` | Keep | |
| H-16 | **Unwritten, `scope.md` §10 item:** **passwords on argv are visible to `ps`** | *no page states it* | `src/archivey/cli/password.py`, `format-rar:145` | **Guide, ~2 lines** | |
| H-17 | **Unwritten, `scope.md` §10 item (`#236`):** the CLI prints archive-derived names and messages, and escaping happens at message construction, so its output is terminal-safe | *no page states it* | `cli:164`, `error-handling:311`, `src/archivey/escaping.py` | **Guide, ~1 line + link** | |

## H — problems and gaps met while extracting

- **H-6 is the sharpest instance of "silence is a claim".** The CLI's three divergent
  defaults *are* stated — inside a bash comment, at `cli.md:18` — but the fact that they
  diverge is not, and that is what breaks a ported script. The claim row therefore has
  both a `Stated at` (the comment) and an unwritten half.
- The whole page predates `#236`. **Three of its 17 rows (H-4, H-16, H-17) touch output
  the escaping change moved**, and none of them is stated today.

---

