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
| Which batches exist (S1–S14) and in what order | [`dev-docs/open-work-inventory.md`](../../dev-docs/open-work-inventory.md) |
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

## Open question — the files swept before the markers existed

The 2026-09-08 pass (nine files) and batches S1 and S2 (seven files) were read before this
convention. Backfilling markers for them would make the count machine-readable in one step,
but it asserts a read that whoever writes the marker did not perform, which is the same class
of claim that caused the original miscount. **Waiting on the maintainer.** Until then
`sweep_coverage.py` reports those sixteen files as unswept and the figure on the inventory
page is the hand-reconstructed one.
