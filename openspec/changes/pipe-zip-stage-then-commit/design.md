## Context

A ZIP read from a pipe (`streaming=True` on a non-seekable source) is read by the forward
walk in `internal/backends/zip_forward_reader.py`. Several facts about the members are
only in the central directory, after the last member:
- the type: a symlink is yielded as a `FILE` until then
- the mode, the host, and therefore whether `\` is a separator
- the order that decides `is_current`

Each member reads `member_state_final=False` until the directory has been read. The
reader sets `_REVISES_YIELDED_MEMBERS = True`, the only reader that does.

The 2026-10-02 ruling (`native-zip-reader/design.md`, "Apply central-only fields at the
end of a forward pass") chose to write each member into the destination as it arrives,
then revise. To make the files on disk match an extraction from a file, that design has
to:
- run the selection and the filter during the pass and again at the end
- move the caller's entries aside under `overwrite="replace"` and put them back
- take back what the end selection drops
- redo a member the directory renames or retypes, writing it again from its own earlier
  copy
- on a run that stops early, remove every file it wrote that could be a link

In `internal/extraction.py` at `8c0de2e`, that is `_settle_revised_members`,
`_settle_failures_at_end`, `_redo_revised_member`, `_reapply_revised_metadata`,
`_unselect_revised_member`, `_remove_created_parents`, `_hold_place`, `_set_aside`,
`_restore_set_aside`, `_take_back_possible_links`, `_may_be_link_data` and
`_remove_unsettled*`. Together with the provisional-selection helpers, about 600 lines.
It also needs seven `_RunState` fields and two `_MemberState` fields.

Its rejected alternative was "spooling the archive to find the directory first". That
is a different thing from what this change proposes; see Decision 1.

## Goals / Non-Goals

**Goals:**
- A pipe ZIP is placed by the same per-member path as any other extraction. The filter,
  the policy checks, the overwrite policy, collisions and `abort_on` each run once, on
  final members.
- Nothing the run cannot stand behind is ever in the destination: no link written as a
  file, no caller's entry moved aside.
- Fewer DR-5a differences between a pipe and a file, and none added.
- Delete the revision machinery, not wrap it.

**Non-Goals:**
- Spooling the source, or reading any other format from a pipe (`bounded-source-spooling`,
  parked).
- Changing what `stream_members()` yields, or the reader at all.
- Changing streaming extraction for TAR or the single-file compressors. Their members are
  final when yielded. The parking of superseded duplicates (`_park_earlier_copies`) stays
  for them.

## Investigations

What stays on disk, and when, for one member of a pipe ZIP:

| Moment | Today | Staged |
| --- | --- | --- |
| Data arrives | Written at its final path (a link as a file holding its target) | Written to `.archivey-tmp-<uuid>/<index>` |
| Caller's entry at that path, `replace` | Moved aside to a temp name | Untouched |
| Run stops before the directory | Files ≤ 4096 bytes and reparse-tagged files removed; longer files stay, with no mode applied, under the local header's name | Staging directory removed; nothing of the run in the destination |
| Directory read | Each changed member revised in place | Each member placed, in listing order |
| End of run | Set-aside entries put back or removed | Staging directory removed |

Peak disk use is the same in both for the members that are placed: every staged byte is a
byte today's pass writes. Two kinds of member add to it. Superseded duplicates are parked
and removed when the later copy arrives today, and here stay staged until placement. And
members the filter drops are never written today, and here are staged, because the filter
runs only at placement (Decision 2). Both count toward `max_extracted_bytes` while staged,
so the cap bounds them.

## Decisions

### 1. Stage member output inside the destination, not the source

The pass writes each staged member's decoded data into one directory,
`<dest_root>/.archivey-tmp-<uuid4 hex>/`, created on the first staged member. Each file
is named by the member's result index. Nothing else is written into the destination
before placement.

This is extraction output, not a copy of the archive. ADR 0010 and the `format-zip` rule
("MUST NOT buffer, spool, or copy the source into seekable storage") are about making a
non-seekable source seekable behind the caller's back. Staged data is decoded, is counted
by the bomb limits like any write, and lives where the extraction was going to put it.
The `format-zip` delta states this so the two rules cannot be read as conflicting. This
reading is the maintainer's to confirm (Open Questions).

Inside `dest_root`, so placement is a rename on the same filesystem. The `.archivey-tmp-`
prefix is the one `docs/extracting.md` already calls safe to delete, and a uuid4 name
cannot be predicted by an archive.

**Rejected:** a system temp directory, which may be on another filesystem, so placement
would copy. **Rejected:** staging next to each final path, which needs the final path,
which needs the directory.

### 2. What is staged

The pass applies only the selection, as today: the provisional match of a `members=`
collection against every name a member can end with, or the predicate, which is told
`member_state_final=False`. The filter does not run during the pass.

For each member the selection may take:
- A member with data (any `FILE`, including one that may turn out to be a link) is
  decoded to its staged file. The per-member recorded-size ratio check runs here, before
  decoding, as `_write_file_atomic` runs it today.
- A `DIRECTORY` stages nothing.

A member's data failure (CRC, size, decode) is recorded against the member and its
staged file discarded. The run does not stop on it until placement: the directory may
not list the member, and an extraction from a file would then never read it. This
removes the DR-5a case "a member whose data fails as the pass writes it stops an
`OnError.STOP` run though the directory may then not list it". Listing damage that ends
the walk still ends it, as today.

**Rejected:** running the filter during the pass to save staging what it drops. It needs
the second filter call and the "filter saw the stored name" failure this change removes
(`archive-reading` delta), to save disk the cap already bounds.

### 3. Placement is the ordinary per-member path

Once the walk has updated every member, the run iterates the final listing in order. It
feeds each member the selection takes through the same per-member function the forward
pass uses: `_transform`, `_write_member` and the collision, overwrite and link-recheck
logic.

The function is the body of today's `_run_pass` loop, extracted. The member's stream is
its staged file, opened. A member with no staged data:
- needs no bytes (a directory, a link, an empty file): it gets none
- otherwise: it gets `_PassedData` with `CorruptionError` (the directory contradicts the
  stream) or `ExtractionError` (the selection left it out during the pass), as
  `_redo_revised_member` decides today

Superseded copies get `SUPERSEDED` and are not placed. The end-of-walk failures
(`reader._failed_at_end`) and missing members (`_settle_missing_member`) become results
here, in listing order, so under `OnError.STOP` the first failure in listing order stops
the run, as from a file.

A symlink's target is what the reader kept (at most the target cap, or a reparse
buffer's prefix), exactly as today, so placement never reads a staged file to make a
link.

**Rejected:** keeping `_settle_revised_members` and feeding it staged data. Keeping it
is the cost this change exists to remove.

### 4. A staged file lands by rename

When a `FILE` member's stream is its own staged file, `_write_file_atomic` takes a fast
path:
1. apply the member's metadata to the staged file
2. `_swap_into_place` it onto the destination: the same `os.replace` landing,
   read-only handling and taken-after-check rule every atomic write uses

No bytes are copied, and none are counted again: they were counted as decoded output
when staged.

A member placed twice from one staged file cannot happen, because each staged file
belongs to one result index. A member whose rename fails is `FAILED` like any landing.

### 5. Bomb limits and progress

**Bomb limits.**
- A member's staged bytes count as decoded output when staged, and toward the archive-wide
  ratio, as today.
- `max_entries` counts a member when it is staged. That bounds an archive of many tiny
  members before placement.
- A staged member that is not placed (unselected at the end, superseded, failed) is
  refunded from the byte cap and the entry count, as `BombTracker.refund` does today for a
  take-back. It still counts toward the archive-wide ratio: its bytes were decoded.

**Progress.**
- Staging emits intra-member reports as the copy loop does today: the member, its bytes
  so far, `members_done` unchanged. Bytes move while the pipe is read.
- Placement emits each member's terminal report and advances `members_done`, so
  `members_done` never counts a member the report does not hold. Today it can.

### 6. Ending the run

The staging directory is removed in the run's `finally`, after placement or after a stop.
Removal makes each entry writable first, as `_remove_scratch` does for a dry run. A
directory the filesystem refuses to remove stays, with a warning naming it.

A stop before placement therefore leaves the destination as it was before the run, apart
from directories the run created as its root (`_ensure_dest_root`, today's behaviour). A
stop during placement keeps what was placed, as a file read keeps the members before its
stop.

A dry run stages into its scratch tree as it writes everything else. Its FILE bodies go
to the null sink and the staged files are empty, which is all placement needs: a dry
run's placed files are empty already.

### 7. What is deleted

The functions listed in Context; the `_RunState` fields `revisable`, `dropped`,
`untallied`, `settled` and `set_aside`; and `_MemberState.provisional`, `recopy` and
`recopy_from`. `_PassedData` and `_possible_names` stay. `_park_earlier_copies` and its
helpers stay for TAR from a pipe.

## Risks / Trade-offs

- **[A stopped run salvages less]** Today a stop before the directory keeps the files
  longer than 4096 bytes, with no modes and possibly under the wrong name. Staged, it
  keeps nothing. → This is the DR-1 side of the trade: those files were unsettled, and
  some carried the local header's name, not the directory's. Recorded in the
  `format-zip` delta.
- **[A visible staging directory]** A concurrent observer of the destination sees one
  `.archivey-tmp-*` directory during the pass. → It is the same prefix atomic writes
  already use, and the docs call it safe to delete.
- **[More staged than placed]** An archive with many copies of one name stages all of
  them, and a filter that drops most members still has them staged. → Counted toward
  `max_extracted_bytes` while staged; a caller who selects with `members=` rather than
  `filter=` stages only what the selection may take.
- **[Members not done until the end]** A pipe extraction reports bytes during the pass
  and member completions only at placement. → See Open Questions.
- **[The per-member function]** The forward pass loop body becomes a function shared with
  placement. → The shared per-member failure handler (`_handling`) is the groundwork;
  the forward pass's tests are the regression net.

## Open Questions

- **Does staged output satisfy ADR 0010 and the `format-zip` "no copy of the source"
  rule?** Decision 1 argues it does. The maintainer rules.
- **Is it acceptable that a pipe ZIP's `members_done` moves only at placement?** The CLI
  progress bar would show bytes advancing and the member count jumping at the end.
  Today both move during the pass.
