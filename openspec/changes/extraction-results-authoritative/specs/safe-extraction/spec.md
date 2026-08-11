## MODIFIED Requirements

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
`failure_group_size=N`; otherwise both are `None`. The id SHALL keep the shipped
`str` shape and generation (`uuid.uuid4().hex`) it has on
`ExtractionOutcomeContext` today — the field moves, its type does not change. It is
opaque: callers MAY compare ids for equality to join a group, and SHALL NOT rely on
ordering, format, or cross-run stability.

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
