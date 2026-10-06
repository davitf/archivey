## MODIFIED Requirements

### Requirement: Extraction reads limits and strictness from the configuration object

`ArchiveReader.extract_all()` SHALL accept `limits: ExtractionLimits | None`. Per-call
`limits` takes precedence over the reader's `config.extraction_limits`, then the
library default. `ExtractionLimits.UNLIMITED` disables byte, ratio,
archive-wide ratio/live-ratio, and entry-count guards. Policy, overwrite,
`on_error`, progress, and member-selection/filter arguments remain operational
arguments outside config.

`extract_all()` runs under the config the reader was opened with and takes no
`config=` of its own. It always returns `ExtractionReport` with an accumulated
immutable result tuple on success; there is no no-tracking mode.

There is no top-level `archivey.extract()`. Opening the archive and calling
`extract_all()` is the one way to extract (ADR 0019).

#### Scenario: limits/config matrix

| Case | Expected |
| --- | --- |
| `extract_all(limits=...)` on an existing reader | Limits apply to this extraction; report remains a watermark range over the existing collector |
| `open_archive(..., config=ArchiveyConfig(extraction_limits=ExtractionLimits(max_extracted_bytes=10 * 2**30)))` then `extract_all(dest)` | Cumulative byte limit is 10 GiB |
| Reader config has limits, call passes `limits=ExtractionLimits(max_extracted_bytes=50 * 2**20)` | 50 MiB governs this run; later calls without `limits` revert to reader config |
| `limits=ExtractionLimits.UNLIMITED` | Archives that would trip default guards complete without bomb-guard error |
| Reader opened with custom config and `extract_all(dest)` | Reader config, including extraction limits, governs the run |

### Requirement: Extraction as a Composable Module

The system SHALL implement safe extraction in a dedicated coordinator module
separate from reader backends and format detection. `ArchiveReader.extract_all()`
delegates to `ExtractionCoordinator`, which drives one unified forward pass over
`(member, stream)` pairs in streaming and random-access modes.

The coordinator SHALL own member selection, transient metadata transforms, user
filter application, `BombTracker` calls, progress callbacks, result accumulation,
and extraction diagnostics. Reader generators yield original mutable members so
backend late-bound updates remain visible; copy-producing transforms/filters do
not detach streamed members from backend updates.

#### Scenario: coordinator matrix

| Case | Expected |
| --- | --- |
| `extract_all()` in random-access mode | Uses `ExtractionCoordinator.run()` forward pass |
| `extract_all()` in streaming mode | Uses the same coordinator pass and consumes the streaming pass per `access-mode-and-cost` |
| Backend fills late-bound fields while streaming | Original member in `ExtractionResult` and `BombTracker` sees the final source metadata |

### Requirement: Enforce Cumulative Max-Extracted-Bytes Limit

The system SHALL track total bytes written across a single
`extract_all()` call and raise `ResourceLimitError` at the chunk boundary where
the total exceeds `max_extracted_bytes`. The default is 2 GiB
(2,147,483,648 bytes). Callers override it through `ExtractionLimits`; `None` via
`ExtractionLimits.UNLIMITED` disables this guard.

The limit SHALL be tracked by one `BombTracker` per extraction call. It is a
global resource guard: when it trips, extraction halts and no later members are
processed regardless of `OnError`.

A copy that a streaming pass takes back as superseded SHALL stop counting toward the
limit once no entry on disk holds its bytes ("Skip non-current members by default"). The
written-byte total that progress reports still includes it.

#### Scenario: cumulative byte limit matrix

| Case | Expected |
| --- | --- |
| Running written-byte total crosses `max_extracted_bytes` | Immediate `ResourceLimitError`; extraction halts |
| `ExtractionLimits(max_extracted_bytes=10 * 2**30)` | Enforced cumulative limit is 10 GiB |
| `ExtractionLimits.UNLIMITED` | Cumulative byte guard is disabled |

### Requirement: Bomb Protection Scope Limited to Extraction Paths

The system SHALL apply `ExtractionLimits` bomb guards only during
`ArchiveReader.extract_all()`. `ArchiveReader.read()`
and `ArchiveReader.open()` return decompressed data/streams without byte, ratio,
or entry-count enforcement; callers are responsible for guarding direct reads.
Listing materialization caps are separate (`ListingLimits` in `archive-reading`)
and do not apply to `read()` / `open()` either.

#### Scenario: bomb-scope matrix

| Case | Expected |
| --- | --- |
| `reader.read(member)` on extreme-ratio data | Raw decompressed bytes returned or normal read error; no extraction bomb guard |
| `reader.open(member)` | Stream delivers decompressed data without extraction limits |
| `reader.members()` on a metadata bomb | `ListingLimits` / `ResourceLimitError` per `archive-reading`, not `ExtractionLimits` |

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

### Requirement: A listing that ends in damage extracts its prefix, then raises

When an archive's listing ends in terminal damage (`CorruptionError` / `TruncatedError`
after a recovered prefix, per `archive-reading`), `extract_all()` SHALL
write the members listed before the damage, in either access mode and for every format,
and then raise the listing's own error, under either `OnError`. No report is returned, so
the members after the damage, which were never listed, have no result. This is the order
`stream_members()` gives (the prefix, then the error), and what unrar and 7-Zip do.
Ruled by the maintainer (davitf, 2026-10-03); the rationale and the rejected
alternatives are in `dev-docs/formats/rar.md` §6.

The listing limits SHALL still be checked before anything is written. A hardlink in the
prefix whose source was not selected SHALL still be completed by the second pass before
the raise; a hardlink only points back, so its source is in the prefix. A member
selection that the prefix satisfies SHALL NOT stop the pass before the damage, and its
entries that match nothing in the prefix SHALL NOT be reported unmatched.

#### Scenario: damaged listing matrix

Pinned by `tests/test_extraction_damaged_listing.py`. A 7z or ZIP listing is one index
read at open, so damage there fails the open.

| Case | Expected |
| --- | --- |
| RAR4 / RAR5 / TAR listing cut or corrupt after N members, random access or streaming, listed first or not | The N members written, then the listing's error; no report |
| Same, `members=` naming one prefix member | That member written, then the listing's error |
| Same, `members=` naming a prefix hardlink whose source is not selected | The link written with the source's bytes, then the listing's error |
| Same, `members=` naming a prefix member and an entry that matches nothing, `MEMBER_SELECTOR_UNMATCHED` set to `RAISE` | The prefix member written, then the listing's error; no `MEMBER_SELECTOR_UNMATCHED` |
| Same, prefix over a listing limit | `ResourceLimitError`; nothing written |

### Requirement: Abort-on-event opt-in for extraction

`extract_all()` SHALL accept `abort_on: Collection[AbortOn] = ()`,
halting the whole extraction the first time a named event occurs.

```python
class AbortOn(str, Enum):
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
member completed normally before the collision was detected.

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

### Requirement: Dry-run extraction

`extract_all()` SHALL accept `dry_run: bool = False`, read for its
truthiness like the other boolean flags.

With `dry_run=True`, the extraction SHALL run the same pass as a real extraction into
a private scratch directory that starts empty, and SHALL NOT create or change anything
under `dest`. Directories, symlinks and hardlinks SHALL be created in the scratch
directory, so every check that consults the filesystem behaves as in a real extraction.
Every FILE body SHALL be read, decompressed, verified and counted against the
extraction limits; its bytes SHALL be discarded and the file created empty.

The returned report SHALL equal the report of a real extraction with the same
arguments into an empty `dest`, with every path in it (`path`, `requested_path`,
`collided_with`) under `dest`. An absolute link target that names a path under `dest`
SHALL be checked as naming the same path under the scratch directory. Two cases are
exempt, because the scratch directory cannot reproduce them: a link target that leaves
`dest` and comes back into it through a symlink outside `dest`, or by climbing above
the directory that holds `dest`, MAY be refused where a real extraction accepts it; and
bytes a real extraction counts for copying a hardlink across a filesystem boundary
inside `dest` are not counted. A `dest` that exists and is not a directory SHALL be
refused as a real extraction refuses it. A `dest` that does not exist SHALL be refused
with the error a real extraction gets creating it when the nearest part of its path
that exists cannot be resolved, is not a directory, or cannot be written to. Whether
it can be written to is a prediction made with `access(2)`, which checks the real user
and group ids rather than the effective ones, and on Windows it is not made; so a
refusal that comes from a process whose real and effective ids differ, from a change
after the check, or on Windows, MAY appear only in a real extraction. File names in
errors raised, recorded or logged SHALL be the ones a real extraction's errors give,
spelled from `dest` as given or as `os.path.abspath` spells it, and never the scratch
directory's, except in the warning that the scratch directory itself could not be
removed. The scratch directory SHALL be removed before the call returns or raises,
including when the archive stored modes that make its directories unwritable or its
files read-only.

#### Scenario: dry-run matrix

| Case | Expected |
| --- | --- |
| Any archive, any policy, overwrite and access mode | Report equals that of a real extraction into an empty `dest` |
| A member whose data fails its digest | `FAILED`, as in a real extraction |
| Output over `max_extracted_bytes` | `ResourceLimitError`, as in a real extraction |
| `dest` holds a file of the same name as a member | Member `EXTRACTED` at `dest/<name>`; the existing file is unchanged |
| `dest` is a regular file | `ExtractionError`; nothing created |
| `dest`'s parent is a regular file | `OSError`, as in a real extraction; nothing created |
| `dest`'s parent is a directory the caller cannot write to, or a symlink loop | The `OSError` a real extraction raises; nothing created |
| `dest` is relative, or reached through a symlink, or spelled with `..`, and a member fails with an `OSError` | Its file names match a real extraction's, spelled as those are |
| A symlink whose absolute target names a file under `dest` | `EXTRACTED`, as in a real extraction |
| A symlink at the top of `dest` whose target is `../<dest name>/<file>` | `EXTRACTED`, as in a real extraction |
| `dest` does not exist | Not created |
| Archive stores a directory with mode `0o555` or `0o000` under `TRUSTED` | Scratch directory still removed |
| `abort_on` fires | The error names paths under `dest`; scratch directory removed |

## REMOVED Requirements

### Requirement: One-Shot Extraction API

**Reason**: The top-level `archivey.extract()` was a near copy of
`open_archive()` + `extract_all()` that differed in ways callers tripped over: no
`members=` or `filter=`, a report whose diagnostics also covered detection and open,
automatic streaming mode on a non-seekable source, and argument checks duplicated so
errors could name `extract()`. The two-line alternative is cheap, and a caller who needs
diagnostics or options is better served by holding the reader. Decision recorded in
ADR 0019.

**Migration**: `with open_archive(source) as reader: reader.extract_all(dest)`. Pass
`format=`, `password=`, `encoding=` and `config=` to `open_archive`, and
`streaming=True` for a pipe or socket. Pass the extraction options to `extract_all`.
Detection and open diagnostics are on `reader.diagnostics`.
