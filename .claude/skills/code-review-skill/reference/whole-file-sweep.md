# Whole-file sweeps: one `SWEPT` marker per file

> For a sweep batch — reading whole files rather than a diff. How to report, severity and
> posting are in [`SKILL.md`](../SKILL.md); read the code as a first look does
> ([`code-pr.md`](code-pr.md) pass 1 and its checklists), with no PR body to read after.

A **sweep batch** is a cold whole-file reading pass rather than a diff review — the `S*`
batches posted to [#315](https://github.com/davitf/archivey/pull/315). Findings post exactly
as any review's do (`SKILL.md` §6), one inline thread each. What a sweep posts *in
addition* is a per-file record that the file was read at all, **whether or not it found
anything**:

- **One top-level comment on #315 per file**, posted when you finish reading that file and
  before you start the next one. Not inline: a whole-file read has no line to anchor to.
- **Writing about the hub elsewhere?** Never reproduce a closing phrase next to its number
  in a commit message or a pull request body — it closes the hub, silently, and quoting one
  counts. The rule lives in [`AGENTS.md`](../../../../AGENTS.md) §Review workflow; it bit
  twice on 2026-09-21. Inline comments like the ones above are not parsed, so findings and
  markers are unaffected.
- **The marker is always its own comment, including for a file that produced findings.**
  Never put the `SWEPT` line in a review body, a finding, or a reply. A file with findings
  therefore gets its findings *and* a marker comment, which is the point: the marker says
  the file was read end to end, and the findings say what was in it. Keeping markers in one
  comment type is what makes the whole set fetchable in one call — the counting command in
  [`open-work-inventory.md`](../../../../dev-docs/open-work-inventory.md) reads the issue
  comments and nothing else.
- Its **first line** is the marker, in exactly this shape:

  ```
  **SWEPT** `src/archivey/internal/backends/zip_reader.py` — pass=S1 date=2026-09-17 lines=1612 findings=3 ids=S1-F1,S1-F2,S1-F3 reviewer=cursor head=94468bd
  ```

  `**SWEPT**`, the repo-relative path in backticks, an em dash, then the fields as
  `key=value` in that order, space separated, **no spaces inside a value**.

  | Field | What |
  |---|---|
  | `pass=` | The batch id — `S1`, `S3`, … The 2026-09-08 pass is `S0`. Split a batch as `S3a` / `S3b` |
  | `date=` | The ISO date you read the file |
  | `lines=` | The file's line count at `head` |
  | `findings=` | How many block-2 findings you raised against this file |
  | `ids=` | Their IDs, comma separated; `-` when `findings=0` |
  | `reviewer=` | `claude-code` or `cursor` |
  | `head=` | The commit you read the file at |

- Under the marker, three to five lines of prose: what you read, what you checked that came
  back clean, and anything you deliberately left to another batch. **A clean file's comment
  is the short one and the valuable one** — it is the only thing that distinguishes a file
  that was read and found sound from a file nobody opened.
- **A sweep finding's ID is `<pass>-<your initial><n>`** — `S16-K1`, `S17-C1` — which
  carries both prefixes this file already requires: the batch, so two batches on #315
  cannot collide, and the reviewer's initial, so two reviewers in one batch cannot either.
  **This applies from the next batch and renames nothing.** An ID that is already anchored
  in a posted thread is never renumbered (`SKILL.md` §6 "Stable IDs"), so a batch that used
  another form keeps it and says so in its markers. `ids=` therefore carries whatever IDs
  the findings actually have, and nothing reading a marker may assume they share the
  `pass=` value.
- **One marker per file per pass**, and a batch posts each file's marker once. Re-sweeping
  a file in a later batch posts a **new** marker rather than editing the old one: the newest
  wins and the older stays as the record of what was true then. `sweep_coverage.py` counts a
  path once however many markers it carries, so a slip cannot inflate the figure — it warns
  instead when one path carries two markers from the same pass.

`lines=` is recorded as read, not looked up later, because coverage decays: the first
backfill showed seven of nine files from the 2026-09-08 pass more than 10% away from the
shape that pass read, all in the one package whose findings had since been fixed. Draining
a sweep's threads rewrites the code the sweep read, so a marker that could only say "swept"
would overstate the subsystem where the follow-up was most thorough. Record the drift; when
to act on it is the maintainer's to schedule, and as of 2026-09-19 the answer is after the
first pass over the whole codebase, not during it.

**A marker for a read you did not perform carries `backfilled=<date>` as a final field**, and
its prose says in as many words that nobody re-read the file. That form exists for one event
— the sixteen files swept on 2026-09-08 and 2026-09-17, before the convention, backfilled on
2026-09-19 at the maintainer's decision from the batch scope tables and the paths the threads
landed on. A pass that predates stable finding IDs writes `ids=untagged`, and one whose
reviewing host is not recorded writes `reviewer=unknown`. Do not reach for any of the three
when recording your own read.

**Why this exists.** Findings are evidence of a read; the absence of findings is not. The
coverage figure on [`open-work-inventory.md`](../../../../dev-docs/open-work-inventory.md)
was overstated by twelve points in two consecutive snapshots because threads were counted as
coverage, and `backends/rar_parser.py` — the largest file in the repository and its most
exposed hostile-input surface — was believed swept when no agent had ever read it. Two sweep
threads also reached opposite conclusions about the same two files from the same evidence.
Markers make "was this file swept?" answerable by looking rather than by inference.

The marker line carries the reviewer and the head, so on this comment type it **replaces**
the "Open every comment with a header" opener (`SKILL.md` §6) rather than sitting under it
— a sweep marker comment has no round and no verdict, and the marker line already carries
the reviewer and the head. Attribution and footer rules are unchanged.

Coverage is counted from these markers, never from thread counts —
[`dev-docs/open-work-inventory.md`](../../../../dev-docs/open-work-inventory.md) §How sweep
coverage is counted, and `scripts/sweep_coverage.py`, which does the counting.
