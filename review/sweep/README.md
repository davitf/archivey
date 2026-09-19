# `sweep/` — the whole-codebase reading pass (#315)

A cold **whole-file** reading pass over `src/`, run one batch at a time by a fresh agent,
posting to [#315](https://github.com/davitf/archivey/pull/315). Not a diff review: the point
is the code nobody has read, not the code someone just changed. #315 is the hub — its head
*is* `main` and its base is an orphan branch, so inline comments re-anchor against current
code and it is never merged.

This directory holds the conventions. The state — which files are swept, and what each
reading found — lives on #315 itself.

| | Where |
|---|---|
| The brief handed to each batch agent | [`brief-template.md`](brief-template.md) |
| What a batch posts, and the `SWEPT` marker shape | [`archivey-review-addendum.md`](../../.claude/skills/code-review-skill/reference/archivey-review-addendum.md) §10 |
| Which batches were planned (S1–S14) and in what order | [`dev-docs/open-work-inventory.md`](../../dev-docs/open-work-inventory.md) — **the plan, not the record.** Batches get re-cut as they run; the markers say what was actually read |
| How coverage is counted | Same page, §How sweep coverage is counted; [`scripts/sweep_coverage.py`](../../scripts/sweep_coverage.py) does it |

## One `SWEPT` marker per file, findings or not

Every file an agent finishes reading gets one top-level comment on #315 whose first line is
the marker. Findings stay as they were: one inline thread each, IDs prefixed by the batch and
the reviewer's initial.

The reason is a counting failure, not bookkeeping taste. Coverage was reported as 24% and
then 35% against a truth of 12% and 23%, because a file with no findings and a file nobody
opened looked identical on #315, and fifteen maintainer questions were read as sweep output.
`backends/rar_parser.py` — the largest file here, and the most exposed to hostile input — sat
in the swept column having never been read. Two sweep threads also reached opposite
conclusions about `internal/volumes.py` and `streams/crypto.py` from the same evidence.

## The sixteen files swept before the markers existed — backfilled

The 2026-09-08 pass (nine files) and batches S1 and S2 (seven files) were read before this
convention. The maintainer's call on 2026-09-19 was to **backfill** them rather than leave
them invisible or re-sweep them, and the sixteen markers are on #315.

Each one carries `backfilled=2026-09-19` and says in its own text that nobody re-read the
file — the marker records the pass that did. They were reconstructed from the paths the
threads landed on, the S1 and S2 scope tables, and `main`'s tip on the pass date. The
reconstruction reproduces the hand-derived figure on
[`open-work-inventory.md`](../../dev-docs/open-work-inventory.md) exactly — 16 files, 8 299
lines, 22.9% — which is the check that it is not a fresh guess.

Two caveats live in the markers rather than here. The 2026-09-08 pass recorded no head, so
`head=7ed4879` is inferred from the date; and its file list comes from the paths its findings
landed on, so a file it read and found clean is not represented anywhere and never can be.

## Coverage decays, and fastest where the sweep worked best

The backfill made something visible that the thread counts never could: seven of the nine
files from 2026-09-08 have drifted more than 10% since they were read, all of them in
`streamtools/` and neither native backend. The cause is the sweep's own success — five of the
nine commits that rewrote `streamtools/` since that pass are the parcels that fixed the
findings the pass produced.

So "swept" decays, and it decays furthest exactly where the follow-up was most thorough. That
is why a marker records `lines=` as read rather than pointing at the file: it is what lets
[`sweep_coverage.py`](../../scripts/sweep_coverage.py) report a file as drifted instead of
counting a stale read as current, and it is an argument for re-sweeping a subsystem after its
parcels land rather than treating one pass as permanent.
[`dev-docs/open-work-inventory.md`](../../dev-docs/open-work-inventory.md) carries the
measured table.
