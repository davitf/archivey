# Safe Extraction

## Purpose

Safe extraction writes archive members to a destination directory while enforcing
non-bypassable path safety, link safety, overwrite rules, permission transforms,
decompression-bomb limits, progress callbacks, diagnostics, and per-member
results. It is the caller-facing path for putting archive contents on disk.

## Related specs

| Spec | Relationship |
| --- | --- |
| `archive-reading` | `open_archive()`, `ArchiveReader`, selectors, reader diagnostics, access methods |
| `access-mode-and-cost` | `extract_all()` as a forward-pass method and streaming legality |
| `diagnostics` | Diagnostic values, retention budgets, watermarks, extraction outcome codes |
| `error-handling` | Exception classes and ordered diagnostic/exception behavior |
| `format-tar` | TAR hardlink ordering, link recovery, and TAR-specific extraction constraints |
## Requirements

### Requirement: One-Shot Extraction API

The top-level API SHALL expose one-shot extraction and return an immutable
`ExtractionReport` on success:

```python
archivey.extract(
    source: str | Path | BinaryIO | Sequence[str | Path | BinaryIO],
    dest: str | Path,
    *,
    policy: ExtractionPolicy = ExtractionPolicy.STRICT,
    overwrite: OverwritePolicy = OverwritePolicy.ERROR,
    on_error: OnError = OnError.STOP,
    format: ArchiveFormat | None = None,
    password: str | bytes | Sequence[str | bytes] | PasswordProvider | None = None,
    encoding: str | None = None,
    on_progress: Callable[[ExtractionProgress], None] | None = None,
    config: ArchiveyConfig | None = None,
    limits: ExtractionLimits | None = None,
) -> ExtractionReport
```

The call SHALL extract all members, deliberately has no `members=` selector, and
uses the same source/password/encoding/config precedence, default `STRICT`
policy, default `ERROR` overwrite policy, and automatic streaming mode for
non-seekable supported sources as the reader APIs. Subset extraction goes through
`ArchiveReader.extract_all()`.

The call SHALL use one diagnostic collector and one retention budget for
detection, backend open, reading, and extraction. The final report uses the
reader collector's cumulative snapshot/range; phases do not seed, copy, merge, or
re-retain events. If an always-stop condition or `OnError.STOP` raises, no report
is returned.

#### Scenario: one-shot extraction matrix

| Case | Expected |
| --- | --- |
| `archivey.extract(source, dest)` completes | Returns `ExtractionReport(results=(...), diagnostics=...)` with all detection/open/read/extraction diagnostics from that call |
| Detection emits one retained conflict and extraction emits one retained failure | One occurrence order and one budget from before detection; no duplicated phase handoff events |
| Non-seekable supported source | Opens in streaming mode automatically and extracts in one forward pass |
| Caller wants only some members | Caller opens the archive and calls `reader.extract_all(dest, members=...)`; top-level `extract()` has no selection parameter |
| `encoding="cp932"` for a TAR with CP932 names | Disk paths match `open_archive(..., encoding="cp932")` followed by `extract_all()` |

### Requirement: Per-Reader Extract-All Helper

`ArchiveReader.extract_all()` SHALL expose per-reader extraction with optional
member selection and filtering:

```python
def extract_all(
    dest: str | Path,
    *,
    members: MemberSelector | None = None,
    filter: MemberFilter | None = None,
    policy: ExtractionPolicy = ExtractionPolicy.STRICT,
    overwrite: OverwritePolicy = OverwritePolicy.ERROR,
    on_error: OnError = OnError.STOP,
    on_progress: Callable[[ExtractionProgress], None] | None = None,
    config: ArchiveyConfig | None = None,
    limits: ExtractionLimits | None = None,
) -> ExtractionReport: ...
```

The helper SHALL record a diagnostic watermark at call start and return a report
whose summary contains exact count/retained deltas for this extraction call only.
`reader.diagnostics` remains cumulative. A per-call config may change new-event
policy/callback behavior but MUST use the reader's existing collector and
retention maximum.

Selection, filter ordering, one-pass selected extraction, reader-config
inheritance, and per-call limits precedence retain their existing contracts.
There is no single-member `reader.extract()` method.

#### Scenario: extract_all matrix

| Case | Expected |
| --- | --- |
| Reader emitted a diagnostic before `extract_all()` and another during extraction | Report summary includes only the extraction occurrence; `reader.diagnostics` includes both |
| `reader.extract_all(dest, members=["a", "b"])` on a solid archive | Only selected members are extracted in one decompression pass |
| Caller wants one file | Uses `reader.extract_all(dest, members=[name])`; no separate single-member API |
| `extract_all(config=...)` overrides diagnostic policy/callback | New extraction events use the override while retention remains under the reader's original budget |

### Requirement: Extraction reads limits and strictness from the configuration object

`archivey.extract()` and `ArchiveReader.extract_all()` SHALL accept both
`config: ArchiveyConfig | None` and `limits: ExtractionLimits | None`. Per-call
`limits` takes precedence over `config.extraction_limits`, then the
reader/library default. `ExtractionLimits.UNLIMITED` disables byte, ratio,
archive-wide ratio/live-ratio, and entry-count guards. Policy, overwrite,
`on_error`, progress, and member-selection/filter arguments remain operational
arguments outside config.

Top-level `extract()` SHALL use the supplied config for its one collector.
`extract_all()` uses the reader config by default; an explicit config affects
new-event policy/callback behavior but not the existing collector or retention
maximum. Both APIs always return `ExtractionReport` with an accumulated immutable
result tuple on success; there is no no-tracking mode.

#### Scenario: limits/config matrix

| Case | Expected |
| --- | --- |
| `extract_all(limits=...)` on an existing reader | Limits apply to this extraction; report remains a watermark range over the existing collector |
| `extract(..., config=ArchiveyConfig(extraction_limits=ExtractionLimits(max_extracted_bytes=10 * 2**30)))` | Cumulative byte limit is 10 GiB |
| Reader config has limits, call passes `limits=ExtractionLimits(max_extracted_bytes=50 * 2**20)` | 50 MiB governs this run; later calls without `limits` revert to reader config |
| `limits=ExtractionLimits.UNLIMITED` | Archives that would trip default guards complete without bomb-guard error |
| Reader opened with custom config and `extract_all(dest)` | Reader config, including extraction limits, governs the run |

### Requirement: Non-Bypassable Universal Path-Safety Constraints

The system SHALL run universal safety checks on the faithful stored
`member.name` before any policy transform, user filter, or filesystem write;
`ExtractionPolicy.TRUSTED` does not bypass them. The default path-safety behavior
is reject/raise. A future sanitize policy is outside v1 scope and is not part of
this contract.

The implementation SHALL enforce defense in depth: first a string check rejects
absolute paths, Windows drive/UNC roots, any `..` component split on `/` or `\`,
null bytes, and names/link targets the platform filesystem encoding cannot represent; then
`(dest / member.name).parent.resolve()` must remain within `dest.resolve()` to catch
symlinked intermediate components without following a final-component symlink; link
targets are rechecked as described in the symlink and hardlink requirements. These
string checks SHALL raise typed `FilterRejectionError` subclasses, never a raw
`UnicodeEncodeError`/`ValueError`.

| Constraint | Violation type | Condition |
| --- | --- | --- |
| Path traversal | `PathTraversalError` | Any `..` component, escaping or internal |
| Absolute path | `PathTraversalError` | Leading `/`, Windows drive path, or UNC path |
| Null byte | `PathTraversalError` | `member.name` contains `\x00` |
| Unrepresentable name | `PathTraversalError` | `member.name` cannot be encoded by the platform filesystem encoding |
| Link-target NUL / unrepresentable | `SymlinkEscapeError` | SYMLINK/HARDLINK `link_target` contains `\x00` or cannot be encoded by the platform filesystem encoding |
| Symlink escape | `SymlinkEscapeError` | SYMLINK whose fully resolved target escapes `dest` |
| Hardlink escape | `SymlinkEscapeError` | HARDLINK whose target path resolves outside `dest` |
| Special file | `SpecialFileError` | `MemberType.OTHER` device/FIFO/socket/etc. |

**Bidi overrides are rejected by the *policy*, not universally.** Every other
constraint in this requirement makes the **write itself** dangerous or impossible — it
escapes the destination, carries a NUL the OS truncates on, or names a device. A bidi
override does neither: the member lands inside `dest` under exactly its stored bytes, and
what is compromised is the name a person **reads back afterwards**. That is a
presentation property, and presentation is the axis `ExtractionPolicy` owns.

The rejection therefore lives in the portable-name policy below, which means
`ExtractionPolicy.TRUSTED` — defined as *faithful bytes, no name rejection or rewrite* —
SHALL extract such a member unchanged, while `STRICT` (the default) and `STANDARD` SHALL
reject it with `DeceptiveNameError`. Running after the caller filter also means a filter
that renames the member rescues it, which is the natural remedy for a name that is a lie.

Without this split a caller who wants the bytes — a mirroring tool, a format converter, a
forensic extract — has no route at *any* policy. That is the outcome ADR 0013 rejected for
unrepresentable names ("extracting beats refusing"), and it would couple two unrelated
axes. See ADR 0017.

**The rejected set is the reordering controls only.** Unicode bidi controls are not one
category, and the difference is load-bearing:

| Subset | Codepoints | Extraction |
| --- | --- | --- |
| Overrides and isolates — reorder *surrounding* text; what a `…gnp.exe` disguise requires | U+202A–U+202E, U+2066–U+2069 | **Rejected** |
| Directional marks — set the direction of one neutral character, reorder nothing, and occur in legitimate Arabic and Hebrew filenames | U+061C, U+200E, U+200F | **Accepted**; `MEMBER_NAME_BIDI_CONTROL` already reported it at listing |

The reject set SHALL be defined by enumerating those two ranges, and MUST NOT be derived
by subtracting from the library's broader advisory set: a subtraction leaves the three
marks one editing mistake away from rejecting legitimate RTL filenames.

Right-to-left **script** is unaffected: an Arabic or Hebrew filename takes its direction
from its own letters' properties, and contains no bidi control at all.

Listing and reading SHALL continue to present the name exactly as stored. Rejection
belongs to extraction, which is where a name becomes a filesystem path a person will
read back.

#### Scenario: universal safety matrix

| Case | Expected |
| --- | --- |
| `"../evil"` or `"../../etc/passwd"` | `PathTraversalError`; no write; all policies |
| `"foo/../bar"` | `PathTraversalError` under reject/raise behavior even if it would stay in root |
| Leading `/`, Windows drive, UNC path | `PathTraversalError`; no write; all policies |
| Earlier member creates symlink `foo` outside `dest`; later member writes `foo/x` | Parent resolution rejects `foo/x` with `PathTraversalError` |
| Name with lone surrogate unencodable by the platform filesystem encoding | `PathTraversalError` before path resolution; never raw `UnicodeEncodeError` |
| SYMLINK/HARDLINK `link_target` with `\x00` or unencodable surrogate | `SymlinkEscapeError`; never raw `ValueError`/`UnicodeEncodeError` |
| Name using only `surrogateescape` round-trip low surrogates (`\udc80`–`\udcff`) | Accepted when otherwise safe (representable on disk) |
| `MemberType.OTHER` | `SpecialFileError`; all policies |

#### Scenario: bidi name matrix

| Case | Expected |
| --- | --- |
| `"invoice‮cod.exe"` extracted under `STRICT` / `STANDARD` | `apply_name_policy` raises `DeceptiveNameError`; a `BLOCKED` result and no write |
| The same member extracted under `TRUSTED` | **Extracts**, under the stored name, unmodified — faithful bytes |
| `"a⁦b⁩.txt"` (isolates) extracted | Same split |
| Symlink whose `link_target` contains U+202E | Same split |
| A caller filter renames it to a clean name | Extracts at every policy — the check runs on the final name, after the filter |
| `"‏דוח.pdf"` (RLM, a directional mark) extracted | Extracts; `MEMBER_NAME_BIDI_CONTROL` was reported at listing |
| `"فهرس.txt"` (Arabic script, no controls) extracted | Extracts; no diagnostic, no rejection |
| Any of the above listed rather than extracted | Name presented exactly as stored |
| `DeceptiveNameError` under either `OnError` | `BLOCKED` result, like any other `FilterRejectionError`; extraction proceeds unless `AbortOn.BLOCKED_MEMBER` is set |

### Requirement: Filesystem refusal of a member name is a typed error

A member name can pass `check_universal` (it encodes via `os.fsencode`, e.g. undecodable
archive bytes carried as `surrogateescape` low surrogates) and still be refused by the
destination filesystem at write time — a UTF-8-enforcing filesystem (APFS) rejects the
byte sequence with `EILSEQ`. Extraction SHALL translate that refusal into a typed
`ExtractionError` (carrying the member name and the original `OSError` as cause) rather
than letting the raw `OSError` escape. Under `OnError.CONTINUE` it is an ordinary
per-member failure result. On filesystems that accept arbitrary bytes (typical Linux),
the member extracts normally; the refusal is an environment outcome, not a property of
the archive.

`EINVAL` is deliberately not auto-translated: it is a broad errno that can arise from
unrelated syscalls during extraction.

Renaming the member to a representable name instead of failing is deliberately not part
of this requirement — it belongs to the future opt-in `SANITIZE` extraction policy
(post-v1, see `IDEAS.md`), not to a bespoke option.

#### Scenario: UTF-8-enforcing filesystem refuses a surrogateescape name

- **WHEN** a member whose name carries undecodable bytes (`surrogateescape`) is extracted
  to a filesystem that enforces valid UTF-8 names
- **THEN** extraction raises a typed `ExtractionError` whose cause is the filesystem's
  `OSError` with `EILSEQ` (never a raw `OSError`), or records a failure result under
  `OnError.CONTINUE`

#### Scenario: byte-preserving filesystem extracts the same member

- **WHEN** the same member is extracted on a filesystem that accepts arbitrary name bytes
- **THEN** the member extracts successfully with its bytes preserved

### Requirement: Skip non-current members by default

`extract` / `extract_all` SHALL skip members with `is_current is False` by default
(`ExtractionStatus.SUPERSEDED`; no write; no bomb-limit counting for the skip). This
is **hardwired coordinator behavior**, not the policy `filter` / `MemberFilter`
pipeline: the skip happens after the optional user `filter` runs so callers can
inspect or rewrite non-current members, then the coordinator still skips writing
them unless a future explicit opt-in lands. `SUPERSEDED` is distinct from
`ExtractionStatus.NOT_OVERWRITTEN` (an existing destination left in place under
`OverwritePolicy.SKIP`).

How surfaces interact:

| Surface | Non-current members |
| --- | --- |
| `members()` / `__iter__` / `get` | Visible (metadata + `is_current=False`) |
| `members=` selector | May select them; they still participate in the extract walk |
| User `filter` (`MemberFilter`) | **Invoked** on them (same as current members) |
| Default extract write | Skipped after filter; `SUPERSEDED` result |
| `open`/`read` on superseded `FILE` | Still allowed (payload exists); not gated by `is_current` |

There is no extract-all flag in this change to force writing non-current
revisions; callers that need those bytes use `open`/`read` (or a future opt-in).

#### Scenario: non-current skip matrix

| Case | Expected |
| --- | --- |
| Content superseded by later same-name or anti | `SUPERSEDED` on extract; path absent on fresh dest |
| User `filter` receives non-current member | Filter is called; returning the member does not force a write |
| `open` superseded content `FILE` | Bytes returned (random access still works) |

### Requirement: Anti-item extraction is delete-only-if-written

For `is_anti` members, extraction SHALL NOT write payload. It SHALL delete the
destination only if this same extraction wrote that path (file or empty dir via
`lstat`/`unlink`); otherwise it is a success no-op. Pre-existing, populated, or
out-of-root paths MUST NOT be deleted. `MemberType.ANTI` SHALL NOT raise
`SpecialFileError` (only `OTHER` does).

#### Scenario: anti extraction matrix

| Case | Expected |
| --- | --- |
| Anti path missing / pre-existing not written this run | Success no-op; pre-existing untouched |
| Earlier member this run wrote the path, then anti | Just-created file/empty dir removed |
| `check_universal` on `ANTI` | No `SpecialFileError` for type alone |
| `MemberType.OTHER` | Still `SpecialFileError` under all policies |

### Requirement: Symlink Escape Re-Validated at Extraction Time

The system SHALL validate a SYMLINK member after `os.symlink(link_target,
dest_path)` creates the link on disk. It resolves the created link target with
`Path.resolve()` and, if the resolved path escapes `dest`, immediately unlinks the
new link and raises `SymlinkEscapeError`. Resolution failures from symlink loops
or platform equivalents (`OSError` such as `ELOOP`, or `RuntimeError`) SHALL fail
safe the same way: unlink the just-created link and reject the member.

This post-creation check SHALL catch chained symlink attacks where earlier archive
members influence later target resolution, without allowing writes through an
escaping link.

#### Scenario: symlink revalidation matrix

| Case | Expected |
| --- | --- |
| Created symlink resolves outside `dest` | Link is unlinked; `SymlinkEscapeError`; no later data written through it |
| Chained symlink attack through earlier member | Post-creation resolution catches the escape and raises `SymlinkEscapeError` |
| Cyclic links (`a -> b`, `b -> a`) make `Path.resolve()` raise | Just-created link is unlinked; `SymlinkEscapeError`; no uncaught OS/runtime error |

### Requirement: Hardlink Two-Pass Extraction

The system SHALL support TAR-style hardlinks through the extraction coordinator as
a pull-based sink over reader streams. Ordinary FILE/DIR/SYMLINK members are
written as reached; each written FILE path is recorded under its source. A
HARDLINK whose source already has recorded paths tries `os.link()` against them
in order; if all fail with cross-device `EXDEV`, the coordinator falls back to
`shutil.copy2()` and records the copy for later links on that device.

When a selected HARDLINK's source was excluded by `members` or `filter`, the
system MUST NOT materialize the excluded source at its own destination. It SHALL
make the source content available only through selected link destinations: write
the bytes to the first selected link path allowed by `OverwritePolicy`, record
`NOT_OVERWRITTEN` links under `SKIP`, link further selected links to the
materialized path, and write nothing if every selected link is skipped. The
materialized file gets the selected link's transformed metadata. An equivalent
hidden temp inside `dest` is permitted.

The coordinator SHALL avoid wasted passes: if a free member list exists
(`members_report_if_available()`), recovery is planned in one forward pass; otherwise
a seekable source may use one conditional second pass; a forward-only source makes
the orphaned link unrecoverable and therefore a per-member failure governed by
`OnError`. A hardlink that merely precedes its selected source is linked after the
source is written, with one read and one bomb-limit count for the source bytes.

#### Scenario: hardlink matrix

| Case | Expected |
| --- | --- |
| HARDLINK reached after its source was extracted | Try `os.link()` against recorded source paths; fallback to copy on all-`EXDEV` |
| Selected hardlink source was excluded but recoverable | Source content appears at selected link path(s); excluded source path is never created |
| First selected link destination exists under `OverwritePolicy.SKIP` | That link result is `NOT_OVERWRITTEN`; content moves to the next allowed link; all skipped means no write |
| Excluded source on a forward-only stream | Per-member failure: `STOP` raises; `CONTINUE` records `FAILED` and proceeds |
| HARDLINK appears before its also-selected source | After the pass it links to the extracted source inode; source bytes read and counted once |

### Requirement: Policy-Specific Metadata Transforms

The system SHALL apply policy-specific permission and ownership transforms to one
transient `ArchiveMember` copy after universal checks pass and before I/O. The
copy receives the policy transform and user `filter` in that order and supplies
the on-disk identity (`name`, mode, timestamps, destination path). The original
mutable member is used for `BombTracker.start_member()` and recorded in
`ExtractionResult`, so late-bound size/CRC/source metadata remain accurate.

```python
class ExtractionPolicy(Enum):
    STRICT = "strict"
    STANDARD = "standard"
    TRUSTED = "trusted"
```

Policies SHALL parallel Python `tarfile`'s `data` / `tar` / `fully_trusted`
mental model while applying uniformly to all formats and retaining Archivey's
non-bypassable safety checks.

| Behavior | `STRICT` default | `STANDARD` | `TRUSTED` |
| --- | --- | --- | --- |
| Path, absolute-path, link-escape, special-file rejection | Always | Always | Always |
| Missing file/dir mode | File `0o644`, dir `0o755` | File `0o644`, dir `0o755` | Apply as stored |
| Permission normalization | Files max `0o644`; dirs `0o755`; strip file execute | Preserve ordinary execute bits | Apply as stored |
| setuid/setgid/sticky | Strip all | Strip setuid/setgid | Preserve |
| uid/gid | Strip | Strip | Apply only when running as root; otherwise skip silently |

#### Scenario: metadata policy matrix

| Case | Expected |
| --- | --- |
| FILE `mode=0o755` under `STRICT` | Written as `0o644` |
| FILE `mode=0o755` under `STANDARD` | Execute bits preserved; setuid/setgid stripped |
| FILE with uid/gid under `TRUSTED` as root | uid/gid applied |
| Any policy, unsafe path/link/special file | Universal safety rejection still applies |

### Requirement: Overwrite Policy

The system SHALL enforce `OverwritePolicy` whenever a destination entry already
exists at the transformed member path:

```python
class OverwritePolicy(Enum):
    ERROR = "error"
    SKIP = "skip"
    REPLACE = "replace"
    RENAME = "rename"
```

`ERROR` raises a per-member `ExtractionError` governed by `OnError`; `SKIP`
records a `NOT_OVERWRITTEN` result and is not a failure. Existence checks SHALL use
`lstat` semantics so dangling symlinks count as existing entries. `REPLACE` SHALL
be atomic wherever the platform permits, and SHALL never write through a symlink.

FILE and HARDLINK replacement SHALL be atomic: the new entry is built beside the
destination (a temp file for FILE data, a temp link for HARDLINK), metadata is
applied to it, and `os.replace()` moves it onto the destination. A failure part-way
through preserves the existing entry and discards only the temp. `os.replace()`
moves the entry itself, so a destination symlink is replaced rather than followed.

SYMLINK and DIRECTORY replacement SHALL remove the existing entry and create fresh,
because neither can be staged: a symlink MUST be created at its final name for the
escape re-validation's cycle check to resolve, and a directory cannot be renamed
over a non-directory at all. Replacing an existing directory with any member type
removes the directory first.

Where `REPLACE` removes an existing entry that a **member of this same run** wrote,
and the replacing write then fails, that earlier member's content is gone. Its
`ExtractionResult` SHALL be revised to `ExtractionStatus.OVERWRITTEN` even though no
member ended up holding the destination — a result SHALL NOT report `EXTRACTED` for
content that no longer exists.

`RENAME` writes a colliding entry under a deterministic derived name (`name (1)`,
inserted before the final suffix) rather than overwriting — see the cross-platform
name-safety requirement.

#### Scenario: overwrite matrix

| Case | Expected |
| --- | --- |
| Existing path under `ERROR` | `ExtractionError`; existing entry unmodified |
| Existing path under `SKIP` | `ExtractionResult.status == NOT_OVERWRITTEN`, `path=None`, no exception |
| Existing file under `REPLACE` | Fresh file is written via temp file + `os.replace()` |
| Existing entry replaced by a HARDLINK under `REPLACE` | Link is built at a temp sibling and `os.replace()`d in; a failure leaves the existing entry intact |
| Existing symlink under `REPLACE` | Symlink entry itself is replaced; bytes never follow the old link |
| `REPLACE` fails mid-stream | Existing file remains unchanged; temp is discarded |
| `REPLACE` clears a this-run destination and then fails | The earlier member is revised to `OVERWRITTEN`; no result claims `EXTRACTED` at the emptied path |
| Dangling symlink under `ERROR` or `SKIP` | Treated as existing; no write-through to target |

### Requirement: Extraction as a Composable Module

The system SHALL implement safe extraction in a dedicated coordinator module
separate from reader backends and format detection. Both `archivey.extract()` and
`ArchiveReader.extract_all()` delegate to the same `ExtractionCoordinator`, which
drives one unified forward pass over `(member, stream)` pairs in streaming and
random-access modes.

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

The system SHALL track total bytes written across a single `extract()` or
`extract_all()` call and raise `ResourceLimitError` at the chunk boundary where
the total exceeds `max_extracted_bytes`. The default is 2 GiB
(2,147,483,648 bytes). Callers override it through `ExtractionLimits`; `None` via
`ExtractionLimits.UNLIMITED` disables this guard.

The limit SHALL be tracked by one `BombTracker` per extraction call. It is a
global resource guard: when it trips, extraction halts and no later members are
processed regardless of `OnError`.

#### Scenario: cumulative byte limit matrix

| Case | Expected |
| --- | --- |
| Running written-byte total crosses `max_extracted_bytes` | Immediate `ResourceLimitError`; extraction halts |
| `ExtractionLimits(max_extracted_bytes=10 * 2**30)` | Enforced cumulative limit is 10 GiB |
| `ExtractionLimits.UNLIMITED` | Cumulative byte guard is disabled |

### Requirement: Enforce Per-ArchiveMember Max Decompression Ratio

The system SHALL raise `ResourceLimitError` when a single member's output exceeds
`max_ratio * member.compressed_size` after that member's output crosses
`ratio_activation_threshold`. Defaults are `max_ratio=1000.0` and threshold
5 MiB. The ratio is per-member output, not cumulative output, and is checked only
when `member.compressed_size` is known and greater than zero. The original member
is used so late-bound compressed-size metadata remains accurate.

This guard SHALL be independent of cumulative bytes and archive-wide ratio
guards. A ratio violation for one member is member-scoped and may be continued
under `OnError.CONTINUE`; global guards remain always-stop.

#### Scenario: per-member ratio matrix

| Case | Expected |
| --- | --- |
| Member output exceeds ratio after threshold | `ResourceLimitError` while processing that member |
| Tiny highly-compressible member stays below threshold | No ratio error; threshold prevents false positive |
| `compressed_size is None` or `0` | Per-member ratio skipped; cumulative/global guards still apply |
| `ExtractionLimits(max_ratio=100)` | Members over 100:1 trip this guard |

### Requirement: Bomb Protection Scope Limited to Extraction Paths

The system SHALL apply `ExtractionLimits` bomb guards only during
`archivey.extract()` and `ArchiveReader.extract_all()`. `ArchiveReader.read()`
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
| `extract(..., on_progress=cb)` | `cb` called with cumulative bytes, per-member bytes, and counters |
| Large FILE member streamed | `cb` invoked multiple times with non-decreasing `member_bytes_written`, ending at the member `size` |
| FILE member smaller than one copy chunk | `cb` invoked once with `member_bytes_written == size` |
| Directory / symlink / hardlink member | Single report with `member_bytes_written == 0` |
| Member with unknown `size` (late-bound / streaming) | `member_bytes_written` still reported; terminal report equals final observed byte count |
| Format cannot provide uncompressed sizes | `total_bytes_estimated is None` |
| Free list + selector | Totals cover selected members only; filter skips/failures still advance `members_done` |
| Terminal report after `EXTRACTED` / `BLOCKED` | `members_extracted` / `members_blocked` match completed outcome tallies |
| `on_progress is None` | No callback; no extra per-chunk work beyond byte counting |

### Requirement: Per-ArchiveMember ExtractionResult with Status

`ExtractionReport.results` SHALL contain one `ExtractionResult` for every
selected member the coordinator processes when the operation completes, including
members blocked by universal/policy checks before the user filter. Selector
exclusions are outside the operation and have no result; a user `filter` that
returns `None` likewise drops the member with **no** `ExtractionResult` (it is a
caller-elected exclusion, not an extraction outcome).

```python
@dataclass(frozen=True)
class ExtractionResult:
    member: ArchiveMember
    path: Path | None
    status: ExtractionStatus
    error: ArchiveyError | OSError | None = None
    requested_path: Path | None = None
    presented_name: str | None = None
    failure_group_id: str | None = None
    failure_group_size: int | None = None

class ExtractionStatus(str, Enum):
    EXTRACTED = "extracted"
    NOT_OVERWRITTEN = "not_overwritten"
    SUPERSEDED = "superseded"
    OVERWRITTEN = "overwritten"
    BLOCKED = "blocked"
    FAILED = "failed"
```

`ExtractionReport.results` SHALL be the **sole authoritative record** of per-member
extraction outcomes. No per-member extraction fact SHALL additionally be reported
through the diagnostics channel.

Statuses SHALL mean: `EXTRACTED` created an entry (`path` set, `error=None`);
`NOT_OVERWRITTEN` left an existing destination in place because
`OverwritePolicy.SKIP` found one (`path=None`, `error=None`); `SUPERSEDED` is a
non-current duplicate skipped by the hardwired last-entry-wins rule (`path=None`,
`error=None`); `OVERWRITTEN` was written and then had its destination replaced by a
later member under `OverwritePolicy.REPLACE` (`path=None`, `error=None`);
`BLOCKED` is a continued `FilterRejectionError` (a universal path-safety check or a
policy filter blocked the member); `FAILED` is a continued non-rejection per-member
`ArchiveyError` or permitted filesystem `OSError`. `NOT_OVERWRITTEN`, `SUPERSEDED`
and `OVERWRITTEN` are not failures.

`requested_path` carries the destination the coordinator intended before
overwrite/rename resolution; it equals `path` for an ordinary write, and
`requested_path != path and status == EXTRACTED` marks an `OverwritePolicy.RENAME`
(see the cross-platform name-safety requirement). On an `OVERWRITTEN` result it
retains the destination the member did write to, so a caller can join it to the
replacing member's `path`.

`presented_name` SHALL carry the member's full relative name **before** portable
rewriting, and SHALL be `None` when no rewrite occurred. It is distinct from
`member.name` (the archive's spelling) and from `path` (the final on-disk spelling):
a caller `filter` rename followed by a portable rewrite produces three spellings, and
only `presented_name` records the middle one.

`failure_group_id` / `failure_group_size` SHALL both be set only when one failed
hardlink source causes `N` `FAILED` link results, which SHALL share one group id and
`failure_group_size=N`; otherwise both are `None`. The id SHALL be a `str` generated
as `uuid.uuid4().hex` — the shape and generation the field carried on the diagnostics
channel before it moved here; relocating it did not change its type. It is opaque:
callers MAY compare ids for equality to join a group, and SHALL NOT rely on ordering,
format, or cross-run stability.

`ExtractionResult` has no diagnostics field; `status`, `error` and the fields above
are the per-result outcome.

#### Scenario: result/status matrix

| Case | Expected |
| --- | --- |
| User filter returns `None` | No `ExtractionResult`; no result-count impact (like a selector exclusion) |
| Selector excludes member | No `ExtractionResult`; no result-count impact |
| Member blocked by `PathTraversalError` under `CONTINUE` | Result is `BLOCKED` with matching error; no diagnostic emitted |
| Member write raises `OSError` under `CONTINUE` | Result is `FAILED` with matching error; no diagnostic emitted |
| Member written successfully | Result is `EXTRACTED`, `path` points to created entry |
| Existing destination under `OverwritePolicy.SKIP` | Result is `NOT_OVERWRITTEN`, `path=None` |
| One failed source causes three hardlink results to fail | Three `FAILED` results sharing one `failure_group_id` with `failure_group_size=3` |

#### Scenario: replaced-member matrix

| Case | Expected |
| --- | --- |
| `A.txt` then `a.txt` under `REPLACE` (non-`TRUSTED`) | `A.txt` revised to `OVERWRITTEN` (`path=None`, `requested_path` kept); `a.txt` is `EXTRACTED` at that path |
| Same pair under `SKIP` | `A.txt` stays `EXTRACTED`; `a.txt` is `NOT_OVERWRITTEN` with `requested_path` set |
| Same pair under `RENAME` | Both `EXTRACTED`; second has `requested_path != path` |
| Same pair under `ERROR` | `A.txt` stays `EXTRACTED`; `a.txt` is `FAILED` with the error |
| Same pair under `TRUSTED` | No collision event; local OS behavior; no `OVERWRITTEN` |
| Result ordering after a retroactive revision | Results stay in member-processing order; only the revised member's fields change |

### Requirement: Error Policy (OnError) for extraction failures

`OnError.STOP` and `OnError.CONTINUE` SHALL govern per-member **failures** only — a
non-rejection member-scoped `ArchiveyError`, a permitted read/write `OSError`, or a
per-member ratio violation. A policy **block** (a `FilterRejectionError` from a universal
path-safety check or a policy filter) is NOT a failure: it SHALL always be recorded as a
`BLOCKED` `ExtractionResult`, have its partial output removed, and let extraction
proceed — under **either** `OnError.STOP` or `OnError.CONTINUE`. `OnError.STOP`
therefore never raises on a blocked member; a STOP run can complete and return an
`ExtractionReport` whose results include `BLOCKED`. Aborting the whole extraction on the
first unsafe member (fail-closed strict security) SHALL be expressed through
`AbortOn.BLOCKED_MEMBER`, not through `OnError`.

Under `CONTINUE`, a member-scoped failure records `FAILED`, removes partial output,
and proceeds.

Under `STOP`, a genuine member failure raises immediately and is not converted to a
continued result. Logging-handler and diagnostic-callback exceptions propagate
unchanged.

Diagnostic disposition SHALL remain authoritative for the diagnostics that still fire
during extraction. Per-member extraction *outcomes* are no longer diagnostics, but
reading an archive while extracting still emits reading diagnostics (invalid
timestamps, unresolvable symlinks, unverifiable digests, stream rewinds); a `RAISE`
disposition on any of those SHALL emit `DiagnosticRaisedError` and halt immediately,
even under `OnError.CONTINUE`, returning no report. Global resource guards (`ResourceLimitError` for cumulative bytes,
archive-wide/live ratio, and max entries), `KeyboardInterrupt`, `MemoryError`, and
unexpected programming exceptions are always-stop and are not swallowed.

#### Scenario: OnError matrix

| Case | Expected |
| --- | --- |
| Member blocked by policy/path-safety under `STOP` | `BLOCKED` result; partial output removed; extraction does **not** halt; later members continue |
| Member blocked by policy/path-safety under `CONTINUE` | `BLOCKED` result; partial output removed; later members continue |
| First member blocked, remaining members extractable, under `STOP` | Run completes; report contains `BLOCKED` + later `EXTRACTED`; no exception escapes |
| Corrupt member under `CONTINUE` | Partial output removed; `FAILED` result; later members continue |
| Default `STOP` member failure (e.g. `CorruptionError`) | Original error propagates immediately; failing partial file removed; earlier outputs remain |
| Filesystem `OSError` while writing under `CONTINUE` | Partial output removed; `FAILED` result; extraction proceeds |
| Cumulative bytes/live ratio/max entries exceed limit under any `OnError` | `ResourceLimitError` propagates and halts; no later member processed |
| Mixed good/corrupt/blocked archive under `CONTINUE` | Extractable members written; report includes `EXTRACTED` plus `FAILED`/`BLOCKED`; no per-member exception escapes |
| Reading diagnostic resolves to `RAISE` under `CONTINUE` (e.g. `MEMBER_TIMESTAMP_INVALID`) | `DiagnosticRaisedError` halts; no report returned |

### Requirement: ExtractionReport is an immutable operation result

The system SHALL define:

```python
@dataclass(frozen=True)
class ExtractionReport:
    results: tuple[ExtractionResult, ...]
    diagnostics: DiagnosticSummary
```

The report SHALL preserve fixed result outcomes and a point-in-time diagnostic
summary with exact operation counts after retention is exhausted. It does not
duplicate the cumulative reader collector or retain beyond the shared budget.
Immutability is structural, not deep: `ExtractionResult.member` is the original
mutable, caller-read-only `ArchiveMember`, whose documented late-bound metadata
and diagnostics may still change; `error` may be an ordinary exception object.

#### Scenario: report immutability matrix

| Case | Expected |
| --- | --- |
| Caller keeps a report and reader later does more work | Result tuple/outcomes and diagnostic summary stay unchanged; referenced member may receive documented late-bound updates |

### Requirement: Archive-wide decompression ratio for solid containers

The system SHALL evaluate a static archive-wide ratio during extraction when a
member's `compressed_size` is unknown/zero and the reader exposes a cheap
`compressed_source_size`. The denominator is the archive source byte size: path
`stat`, trusted integer `size`, `try_get_size()` from Archivey streams, or an
O(1)-safe `SEEK_END`/restore probe for real files, `BytesIO`, and `mmap`.
Anything that would decompress or scan payload to answer (for example foreign
decompressor streams) yields `None`. For compressed containers this is compressed
size; for uncompressed containers the resulting ratio is about 1:1 and harmless.

The ratio SHALL be `cumulative_bytes_written / compressed_source_size`, checked
in `BombTracker.count()` using the same `max_ratio` and cumulative
`ratio_activation_threshold` as other ratio guards. If `compressed_source_size`
is absent, the static archive-wide check is skipped. Per-member and archive-wide
ratios are independent; either may trip first. A tripped archive-wide ratio
SHALL raise `ResourceLimitError`.

#### Scenario: static archive-wide ratio matrix

| Case | Expected |
| --- | --- |
| Small `.tar.gz` file with known source size expands past `max_ratio` after threshold | `ResourceLimitError` during extraction |
| Compressed tar from non-seekable pipe with unknown size | Static archive-wide ratio skipped; cumulative byte limit still applies |
| Plain `.tar` | No meaningful compressed denominator; archive-wide ratio does not trip |
| ZIP member has known `compressed_size` | Per-member ratio applies; archive-wide ratio does not replace it |
| Nested archive opened from an Archivey member/codec stream with cheap size | Cheap source size may serve as archive-wide denominator |

### Requirement: Enforce Maximum Entry Count

The system SHALL count members actually written to disk during one extraction call
and raise `ResourceLimitError` once the count exceeds `max_entries`. The default is
`1_048_576`; callers override through `ExtractionLimits`, and `None` disables the
guard. The counter protects against inode/per-directory/syscall bombs made of many
tiny entries and is independent of byte and ratio limits.

Only members that will create disk entries SHALL count: selector exclusions, user
filter skips, and members dropped before writing do not increment the counter.
Every written FILE, DIR, SYMLINK, and HARDLINK counts. This is a global resource
guard and halts even under `OnError.CONTINUE`.

#### Scenario: entry-count matrix

| Case | Expected |
| --- | --- |
| More than `max_entries` members are written | `ResourceLimitError` once the count crosses the limit; extraction halts under any `OnError` |
| `ExtractionLimits(max_entries=100)` | Error after the 100th written member when the 101st would be written |
| Selector chooses one member from millions | Extraction can complete with `max_entries=1` because only selected written entries count |
| Many tiny files stay below byte/ratio limits but exceed entry count | Entry-count guard still raises |

### Requirement: Symlink extraction is target-independent and fails safe on unsupported filesystems

The system SHALL create SYMLINK members as symbolic references via
`os.symlink()` without requiring the target to exist, to be selected, or to be
inside the archive. A symlink may dangle and no target data is copied; the
universal resolved-target escape check remains the only safety constraint on the
link target.

If the platform or destination filesystem cannot create symlinks and
`os.symlink()` raises `OSError` or `NotImplementedError`, the member SHALL be a
per-member failure governed by `OnError`. Archivey does not silently copy target
data as Python `tarfile` may do on symlink-unsupported platforms.

#### Scenario: symlink extraction matrix

| Case | Expected |
| --- | --- |
| SYMLINK target member is excluded by selector/filter and resolved target stays in `dest` | Symlink is created and may dangle; target data is not copied |
| SYMLINK target appears later or outside the archive but stays in `dest` | Symlink is created as stored; no target materialization |
| `os.symlink` unsupported raises `OSError`/`NotImplementedError` | `STOP` raises; `CONTINUE` records `FAILED`; no copy fallback |

### Requirement: Live archive-wide decompression ratio for unknown-size streams

The system SHALL evaluate a live archive-wide ratio during extraction when no
per-member `compressed_size` and no cheap static `compressed_source_size` is
available, but the compressed backend can expose `compressed_bytes_consumed`.
This covers compressed archives from non-seekable pipes and seekable opaque
streams whose size is not cheaply knowable. Backends wrap the stream source in
the counting reader exactly when the static denominator is absent.

The ratio SHALL be `cumulative_bytes_written / compressed_bytes_consumed`, checked
after cumulative output crosses `ratio_activation_threshold` using the same
`max_ratio`. It is a cumulative global guard: if it trips, extraction halts even
under `OnError.CONTINUE` with `ResourceLimitError`. The live path complements
static checks and is not used when member compressed sizes or a cheap outer
source size provide a denominator; whichever available guard trips first wins.
Codec-layer seeks may re-read counted bytes, inflating the denominator and
weakening the guard, but never causing a false positive.

#### Scenario: live archive-wide ratio matrix

| Case | Expected |
| --- | --- |
| Highly compressible `.tar.gz` from non-seekable pipe has no static denominator | Live ratio raises `ResourceLimitError` after threshold before absolute byte cap |
| Live ratio exceeded under `OnError.CONTINUE` | `ResourceLimitError` propagates and extraction halts |
| Plain uncompressed `.tar` from a pipe | Consumed and written bytes stay about 1:1; live ratio does not trip; byte limit still applies |
| `.tar.gz` has cheap `compressed_source_size` | Static archive-wide ratio is used; live path is not engaged/double-counted |
| Seekable opaque compressed stream has no cheap size/`size`/`try_get_size()`/O(1) end seek | Source is counted live; archive is not left with only the byte cap |

### Requirement: Cross-platform name safety is deterministic across policy levels

Extraction SHALL handle destination-name hazards deterministically on every platform,
keyed off `ExtractionPolicy`, so the same archive yields the same logical outcome
(collision events, rejections, normalized spellings) regardless of the runner OS. These
rules compose with — and never bypass — the non-bypassable path-safety constraints.

**Collision determinism (O2).** Under `STRICT` and `STANDARD`, the coordinator SHALL track
a `casefold(NFC(path))` key per written destination and treat a second member resolving to
the same key as an existing destination on **all** platforms, applying `OverwritePolicy`
deliberately and recording the outcome on both members' `ExtractionResult`. `REPLACE`
SHALL NOT silently merge distinct members on case-insensitive filesystems: the earlier
member's result SHALL be revised to `ExtractionStatus.OVERWRITTEN` so the merge is
observable in `results`. Under `TRUSTED` the coordinator SHALL key on the exact `Path`
and defer to the local OS (today's behavior), so genuinely distinct files on a
case-sensitive filesystem both extract.

`OverwritePolicy` SHALL add a `RENAME` member that extracts a colliding entry under a
deterministic derived name, using the same collision key, for archives with intentional
duplicates. The derived name SHALL insert ` (N)` (`N` = 1, 2, …) **before the final suffix**
(`Path.stem` + `Path.suffix` semantics): `photo.jpg` → `photo (1).jpg`; a name with no
suffix → `photo (1)`; a leading-dot dotfile (`.bashrc`) → `.bashrc (1)` (the leading dot is
not treated as a suffix); a multi-suffix name (`archive.tar.gz`) → `archive.tar (1).gz`
(single final suffix); a directory appends to the whole segment. `N` SHALL increment to the
first name free **both on disk and in the collision map**, in member-processing order.

**Portable-name enforcement (O3/O4).** Windows-reserved device names (`CON`, `PRN`, `AUX`,
`NUL`, `COM1`–`COM9`, `LPT1`–`LPT9`; case-insensitive, with or without extension) and `:`
within a segment are **unsafe** (device capture / NTFS alternate data stream) and SHALL be
rejected under `STRICT` and `STANDARD` on **every** platform. A trailing dot or space is a
legitimate macOS/Linux name that Win32 merely trims; rejecting it would halt a legitimate
archive, so under `STRICT` each path segment's trailing dot/space SHALL be **stripped** to
its portable spelling (`stuff_etc.` → `stuff_etc`) deterministically on every platform,
collision-tracked as above, and recorded as `ExtractionResult.presented_name`; a
segment that is entirely dots/spaces (e.g. `...`) has no portable spelling and SHALL be
rejected. `STANDARD` and `TRUSTED` SHALL keep the trailing dot/space faithful (written if
the OS allows).

**Portable-name representability (O7).** Under `STRICT` and `STANDARD`, a name carrying
bytes that cannot be represented portably on the destination filesystem SHALL be normalized
to a deterministic, reversible portable spelling — each non-UTF-8 byte (a surrogateescape
char U+DC80–U+DCFF mapping to raw byte 0x80–0xFF) percent-escaped as `%XX` (uppercase hex),
and a literal `%` escaped as `%25` — applied on **every** platform, collision-tracked as
above, and recorded as `ExtractionResult.presented_name`. The scheme SHALL touch only
non-decodable bytes; valid-but-non-portable Unicode (NFC/NFD forms) SHALL NOT be rewritten
(its cross-platform folding is the O2 collision concern). `TRUSTED` SHALL attempt the
faithful bytes and let the OS decide. The reversibility SHALL be a documented property; a
public un-escape API is out of scope. Either way the outcome SHALL be deterministic and
typed (never a bare `OSError`); a name that cannot be `os.fsencode`d at all remains
rejected by the universal check.

`ExtractionResult.requested_path` carries the destination the coordinator intended before
overwrite/rename resolution. A rename SHALL be observable as `requested_path != path and
status == EXTRACTED`; a collision resolved by `SKIP`/`ERROR` SHALL set `requested_path`
with `path=None`. `presented_name` SHALL NOT be expressed through `requested_path`: the
two signals are independent, and a member MAY carry both (a portable rewrite whose
rewritten name then collides and is renamed).

#### Scenario: cross-platform name matrix

| Case | `STRICT` / `STANDARD` | `TRUSTED` |
| --- | --- | --- |
| `README` and `readme` in one archive | Second is a collision event on all platforms; `OverwritePolicy` applied; `requested_path` recorded | Local OS behavior (both extract on a case-sensitive FS) |
| NFC `café` and NFD `café` | Treated as a collision on all platforms | Local OS behavior |
| Member named `NUL` / `COM1` | Rejected on all platforms (typed error) | Written if the OS allows |
| Trailing dot/space (`foo.`, `foo `) | `STRICT` strips to portable spelling (`foo`), `presented_name="foo."`; `STANDARD` keeps faithful | Written if the OS allows |
| Segment of only dots/spaces (`.../x`) | Rejected on all platforms (no portable spelling) | Written if the OS allows |
| Name containing `:` (`file:hidden`) | Rejected on all platforms | Local OS behavior (NTFS ADS) |
| Surrogateescape `caf\udce9.txt` | Sanitized to `caf%E9.txt`; `presented_name` keeps the pre-rewrite spelling; collision-tracked | Faithful bytes attempted; OS decides |
| `REPLACE` with a casefold collision | Not a silent merge; earlier member revised to `OVERWRITTEN` | Local OS behavior |
| `RENAME` with a collision (case/NFC or exact) | Second entry written as `name (1)` before the suffix; `requested_path` = intended name | Same |
| Filter rename, then a portable rewrite | `member.name`, `presented_name`, and `path.name` are all three spellings | Faithful bytes attempted |

### Requirement: Abort-on-event opt-in for extraction

`extract()` and `extract_all()` SHALL accept `abort_on: Collection[AbortOn] = ()`,
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
| `NAME_SANITIZED` | a name is rewritten to its portable spelling | `NameRewrittenError` |

`NAME_SANITIZED` is deliberately unlike the other two: it fires on a **successful**
safety rewrite rather than on a refusal or an ambiguity. It SHALL ship anyway,
because dropping it is the only place this change would remove escalation that
exists today, and preserving escalation is why `abort_on` exists. It SHALL be
documented as a narrow escape hatch for callers who refuse any on-disk name
differing from the archive's — mirroring tools, forensic extracts, byte-fidelity
checks — and SHALL NOT be presented as part of ordinary strict extraction or implied
by any preset or policy level. A caller wanting to *audit* rewrites reads
`presented_name`; only a caller who wants them to be **fatal** sets this.

`NAME_COLLISION` SHALL fire on **every** non-`TRUSTED` collision event, whatever
`OverwritePolicy` resolution follows — replaced, skipped, errored or renamed. The
trigger is the collision itself, not its outcome. This is deliberate parity with the
escalation this change relocates: the removed diagnostic fired on all four
resolutions, so escalating it stopped the caller on all four. `TRUSTED` keys on the
exact path and produces no collision event, so it never aborts.

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
expresses "raise on the first failure", and a second spelling of one behaviour is
what this change exists to remove.

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
