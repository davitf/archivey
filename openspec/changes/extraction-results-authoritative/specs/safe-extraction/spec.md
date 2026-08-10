## MODIFIED Requirements

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
    failure_group_id: int | None = None
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
`failure_group_size=N`; otherwise both are `None`.

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
unchanged. Global resource guards (`ResourceLimitError` for cumulative bytes,
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

## ADDED Requirements

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

`NameCollisionError` and `NameRewrittenError` SHALL subclass `ExtractionError`.
`BLOCKED_MEMBER` SHALL propagate the original rejection unchanged, matching
`OnError.STOP`'s propagate-the-original behaviour.

Abort SHALL be immediate: partial output for the triggering member is removed, no
later member is processed, and no `ExtractionReport` is returned. `abort_on` SHALL be
independent of `OnError` and of `DiagnosticPolicy` — a blocked member aborts under
either `OnError` value when `BLOCKED_MEMBER` is set, and never aborts when it is not.

`AbortOn` SHALL NOT carry a member for extraction *failures*: `OnError.STOP` already
expresses "raise on the first failure", and a second spelling of one behaviour is
what this change exists to remove.

#### Scenario: abort-on matrix

| Case | Expected |
| --- | --- |
| Absolute-path member, `abort_on={BLOCKED_MEMBER}`, `OnError.CONTINUE` | `FilterRejectionError` raised at that member; no report; later members untouched |
| Same archive, `abort_on=()` | `BLOCKED` result; extraction completes; report returned |
| `REPLACE` collision, `abort_on={NAME_COLLISION}` | `NameCollisionError` at the second member; no report |
| Trailing-dot name under `STRICT`, `abort_on={NAME_SANITIZED}` | `NameRewrittenError` at that member; no report |
| Collision under `TRUSTED`, `abort_on={NAME_COLLISION}` | No collision event, so no abort |
| `abort_on={BLOCKED_MEMBER}` with `OnError.STOP` and a corrupt member | Failure still raises via `OnError`; abort applies only to blocks |
