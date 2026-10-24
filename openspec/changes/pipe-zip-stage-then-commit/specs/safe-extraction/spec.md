## MODIFIED Requirements

### Requirement: A ZIP extracted from a non-seekable source is settled at the end of the pass

A ZIP read from a non-seekable source (`streaming=True`) gives each member's type, mode,
host and comment, and the order that decides `is_current`, only in its central
directory, after the last member. During the pass, `extract_all` SHALL write the data of
each member the selection may take into one staging directory, named `.archivey-tmp-`
and a random suffix, directly under the destination root, and SHALL write nothing else
into the destination. Once the directory has been read, it SHALL place the members in
listing order, as the extraction of the same archive from a file places them, each read
from its staged data:

- The selection, the caller's `filter`, the policy's checks, the overwrite policy, name
  collisions and `abort_on` SHALL each act on the member as the directory describes
  it, when the member is placed. A symlink that escapes the destination is therefore
  refused from a pipe as from a file, and nothing is written through a link before
  every member is known.
- During the pass, the universal path-safety checks and the name policy SHALL also run
  on each member as its local header names it, before its data is staged. A member
  they refuse SHALL NOT be staged, and `AbortOn.BLOCKED_MEMBER`, `AbortOn.NAME_SANITIZED`
  and `OnError.STOP` SHALL act on that refusal at once, as on a refusal at placement.
  A member's data failure while it is staged SHALL stop an `OnError.STOP` run at once
  too. So a hostile or damaged archive is refused without decoding the rest of it.
- Five things are known only once the directory has been read, and each SHALL fail
  the member it names, under "Error Policy (OnError)" above: a size or CRC from a
  data descriptor that differs from the central entry; a local header whose CRC, sizes
  or encryption flag the central entry contradicts; two same-name members whose
  directory order reverses their file order (the current one fails, the other becomes
  `SUPERSEDED`); a directory entry with no local entry; and a local header and
  directory entry that store different names. The member's result SHALL be the one
  the extraction from a file gives it, or `FAILED` with `CorruptionError` where that
  result needs bytes the pass did not stage; the overwrite policy decides first. Under
  `OnError.STOP` the first such failure SHALL raise `CorruptionError` when placement
  reaches it.
- A local entry no directory entry points to SHALL NOT be placed, and SHALL be reported
  as `ARCHIVE_UNLISTED_DATA`.
- A run that stops before placement begins SHALL leave nothing it wrote in the
  destination. One that stops during placement SHALL keep the members already placed,
  as an extraction from a file keeps the members before its stop. The staging directory
  SHALL be removed when the run ends, whatever the outcome; one the filesystem refuses
  to remove stays, with a warning.
- The bomb limits SHALL count staged data as decoded output, and each staged file
  toward `max_entries`, when it is staged: a staged file is an entry on disk. A staged
  member that is not placed SHALL stop counting toward `max_extracted_bytes` and
  `max_entries`, and SHALL still count toward the archive-wide ratio. Placing a staged
  member adds nothing to any count; a member placed with no staged data (a directory, a
  link, an empty file) SHALL count toward `max_entries` when it is placed.

Where the two reads MAY differ (DR-5a, shapes only crafted archives have): the
descriptor mismatch and the reversed pair fail a member the extraction from a file
writes; a refusal on the local header's name, or a data failure, can stop a run for a
member the directory then does not select or does not list; a member refused on the
local header's name and accepted on the directory's fails, its data not staged; and the
bomb limits count what the pass stages, so a member staged and then not placed can
still stop a run under `max_entries` or `max_extracted_bytes`, a member the filter skips
included.

#### Scenario: ZIP from a pipe, settled at the end of the pass

| Case | Expected |
| --- | --- |
| The directory types a member staged as a file as a symlink to `../../etc/passwd` | Refused when placed, as from a file; nothing of it reaches the destination |
| Two same-name members `M1`, `M2` in file order, the directory makes `M1` current, `OnError.CONTINUE` | `M1` `FAILED` with `CorruptionError`, `M2` `SUPERSEDED`; the path is empty |
| A STORED bit-3 member whose data embeds a descriptor for a prefix of itself | `FAILED` with `CorruptionError`; nothing placed for it; under `OnError.STOP` raised when placement reaches it |
| A directory entry with no local entry, selected | A `FAILED` result is added for it |
| A local entry the directory does not list | Not placed; `ARCHIVE_UNLISTED_DATA` |
| The run stops before the directory is read (`abort_on`, `on_error="stop"`, a limit) | The destination holds nothing the run wrote; the caller's entries are as they were |
| First member `../../etc/passwd`, `abort_on={BLOCKED_MEMBER}` | `FilterRejectionError` at that member, before any later member is decoded |
| `overwrite="replace"` over a caller's file, for a member the selection drops once the directory is read | The caller's file is untouched |
| The staging directory cannot be removed at the end of the run | It stays, named `.archivey-tmp-…`, with a warning |

### Requirement: Progress Reporting via on_progress Callback

The system SHALL accept optional `on_progress` callbacks on both extraction APIs
and report progress with `ExtractionProgress`:

```python
@dataclass
class ExtractionProgress:
    member: ArchiveMember
    bytes_written: int
    total_bytes_estimated: int | None
    members_done: int
    members_total: int | None
    member_bytes_written: int
    members_extracted: int
    members_blocked: int
```

`bytes_written` is cumulative for the operation. `member_bytes_written` is the
output bytes written for the **current** member so far. `total_bytes_estimated`
is `None` when the format lacks uncompressed size information; `members_total` is
`None` when the attempted member count cannot be known without a scan. When a
free member list exists and a `members` selector is provided, totals SHALL cover
only selected members. `members_done` counts every selected member processed,
including user-filter skips and failures, so it reaches `members_total`;
selector-excluded members are invisible. `members_extracted` / `members_blocked`
are completed-outcome tallies of `EXTRACTED` / `BLOCKED` results so far (on
intra-member reports they exclude the in-flight member); other statuses advance
`members_done` without incrementing either. Predicate selectors evaluated against
an upfront index MUST be pure functions of the member.

For a ZIP read from a non-seekable source ("A ZIP extracted from a non-seekable source
is settled at the end of the pass"), a member's reports SHALL be made while its data is
staged, its intra-member reports and terminal report together, so `members_done` counts
the members staged and can count one that is then not placed. `members_extracted` and
`members_blocked` count placed outcomes: they stay 0 during the pass, and placement
SHALL make one more report when it ends, naming the last member it handled, with the
final tallies.

For a FILE member with a streamed body, the callback MAY be invoked **more than
once** as bytes are written: intra-member reports carry `member` = the current
member, `members_done` = the number of members fully completed *before* this one,
and a non-decreasing `member_bytes_written` that has not yet reached the member's
size. Each processed member SHALL additionally produce a terminal report in which
`member_bytes_written` equals the member's `size` (or, when `size` is unknown,
the final observed byte count), so a consumer can always complete a per-member
progress bar. Members without a streamed body (directories, symlinks, hardlinks)
SHALL produce a single report with `member_bytes_written == 0`. The reporting
frequency is bounded by the extraction copy chunk size; when `on_progress` is
`None`, no additional per-chunk work is performed beyond existing byte counting.

#### Scenario: progress matrix

| Case | Expected |
| --- | --- |
| `extract_all(..., on_progress=cb)` | `cb` called with cumulative bytes, per-member bytes, and counters |
| Large FILE member streamed | `cb` invoked multiple times with non-decreasing `member_bytes_written`, ending at the member `size` |
| FILE member smaller than one copy chunk | `cb` invoked once with `member_bytes_written == size` |
| Directory / symlink / hardlink member | Single report with `member_bytes_written == 0` |
| Member with unknown `size` (late-bound / streaming) | `member_bytes_written` still reported; terminal report equals final observed byte count |
| Format cannot provide uncompressed sizes | `total_bytes_estimated is None` |
| Free list + selector | Totals cover selected members only; filter skips/failures still advance `members_done` |
| Terminal report after `EXTRACTED` / `BLOCKED` | `members_extracted` / `members_blocked` match completed outcome tallies |
| `on_progress is None` | No callback; no extra per-chunk work beyond byte counting |
| ZIP from a pipe, three members, the directory drops the second | Each member's reports made in order while staged; one more report after placement with the final tallies |

### Requirement: Enforce Maximum Entry Count

The system SHALL count members actually written to disk during one extraction call
and raise `ResourceLimitError` once the count exceeds `max_entries`. The default is
`262_144`; callers override through `ExtractionLimits`, and `None` disables the
guard. The counter protects against inode/per-directory/syscall bombs made of many
tiny entries and is independent of byte and ratio limits.

Only members that will create disk entries SHALL count: selector exclusions, user
filter skips, and members dropped before writing do not increment the counter. The
exception is a ZIP read from a pipe, whose pass stages a file for each member the
selection may take before the filter runs ("A ZIP extracted from a non-seekable source
is settled at the end of the pass"): each staged file counts while it is on disk, and
stops counting if its member is not placed.
Every written FILE, DIR, SYMLINK, and HARDLINK counts. This is a global resource
guard and halts even under `OnError.CONTINUE`. The same limit separately bounds the
symlink rechecks of "Symlink Escape Re-Validated at Extraction Time".

#### Scenario: entry-count matrix

| Case | Expected |
| --- | --- |
| More than `max_entries` members are written | `ResourceLimitError` once the count crosses the limit; extraction halts under any `OnError` |
| `ExtractionLimits(max_entries=100)` | Error after the 100th written member when the 101st would be written |
| Selector chooses one member from many | Extraction can complete with `max_entries=1` because only selected written entries count; every member the pass walks still counts against `ListingLimits` (`archive-reading`), in both access modes |
| Many tiny files stay below byte/ratio limits but exceed entry count | Entry-count guard still raises |

### Requirement: Abort-on-event opt-in for extraction

`extract_all()` SHALL accept `abort_on: Collection[AbortOn] = ()`,
halting the whole extraction the first time a named event occurs.

```python
class AbortOn(StrEnum):
    BLOCKED_MEMBER = "blocked_member"
    NAME_COLLISION = "name_collision"
    NAME_SANITIZED = "name_sanitized"
```

| Member | Fires when | Raises |
| --- | --- | --- |
| `BLOCKED_MEMBER` | a member is blocked by a universal path-safety check or a policy filter | the underlying `FilterRejectionError` |
| `NAME_COLLISION` | a second member resolves to an already-written collision key (non-`TRUSTED`) | `NameCollisionError` |
| `NAME_SANITIZED` | a name is rewritten to its portable spelling, or an absolute name is re-rooted | `NameRewrittenError` |

`NAME_SANITIZED` is deliberately unlike the other two: it fires on a **successful**
safety rewrite rather than on a refusal or an ambiguity. It SHALL be documented as a
narrow escape hatch for callers who refuse any on-disk name differing from the archive's — mirroring tools, forensic extracts, byte-fidelity
checks — and SHALL NOT be presented as part of ordinary strict extraction or implied
by any preset or policy level. A caller wanting to *audit* rewrites reads
`presented_name`; only a caller who wants them to be **fatal** sets this.

`NAME_COLLISION` SHALL fire on **every** non-`TRUSTED` collision event, whatever
`OverwritePolicy` resolution follows — replaced, skipped, errored or renamed. The
trigger is the collision itself, not its outcome. `TRUSTED` keys on the exact path and
produces no collision event, so it never aborts.

`NameCollisionError` and `NameRewrittenError` SHALL subclass `ExtractionError`.
`BLOCKED_MEMBER` SHALL propagate the original rejection unchanged, matching
`OnError.STOP`'s propagate-the-original behaviour.

Abort SHALL be immediate: partial output for the triggering member is removed, no
later member is processed, and no `ExtractionReport` is returned. `abort_on` SHALL be
independent of `OnError` and of `DiagnosticPolicy` — a blocked member aborts under
either `OnError` value when `BLOCKED_MEMBER` is set, and never aborts when it is not.

Output written for **earlier** members SHALL remain on disk, matching `OnError.STOP`:
abort stops the run, it does not roll it back. For a collision abort this means the
first member's bytes are typically still present at the contested path, since that
member completed normally before the collision was detected. A ZIP read from a pipe
places its members only once the central directory has been read ("A ZIP extracted
from a non-seekable source is settled at the end of the pass"): an abort during its
pass leaves nothing in the destination, and an abort during placement keeps the members
placed before it, in listing order, as above.

Because no report is returned, an abort SHALL have no observable result-side effect:
in particular the earlier member is not revised to `OVERWRITTEN` anywhere the caller
can see. `OVERWRITTEN` is a property of a completed report, and `abort_on` and
`OVERWRITTEN` are therefore mutually exclusive for the same collision.

`AbortOn` SHALL NOT carry a member for extraction *failures*: `OnError.STOP` already
expresses "raise on the first failure".

#### Scenario: abort-on matrix

| Case | Expected |
| --- | --- |
| Absolute-path member, `abort_on={BLOCKED_MEMBER}`, `OnError.CONTINUE` | `FilterRejectionError` raised at that member; no report; later members untouched |
| Same archive, `abort_on=()` | `BLOCKED` result; extraction completes; report returned |
| `REPLACE` collision, `abort_on={NAME_COLLISION}` | `NameCollisionError` at the second member; no report; first member's bytes remain on disk |
| `SKIP` collision, `abort_on={NAME_COLLISION}` | Aborts — the trigger is the collision, not the resolution |
| `RENAME` collision, `abort_on={NAME_COLLISION}` | Aborts, even though the rename loses nothing; parity with the escalation this replaces |
| `ERROR` collision, `abort_on={NAME_COLLISION}` | Aborts with `NameCollisionError`, not the overwrite error |
| Any aborted collision | No result is observable; the earlier member is never seen as `OVERWRITTEN` |
| Trailing-dot name under `STRICT`, `abort_on={NAME_SANITIZED}` | `NameRewrittenError` at that member; no report |
| Collision under `TRUSTED`, `abort_on={NAME_COLLISION}` | No collision event, so no abort |
| `abort_on={BLOCKED_MEMBER}` with `OnError.STOP` and a corrupt member | Failure still raises via `OnError`; abort applies only to blocks |
| ZIP from a pipe, `abort_on={NAME_COLLISION}`, two members colliding | `NameCollisionError` when the second is placed; the first stays placed |
