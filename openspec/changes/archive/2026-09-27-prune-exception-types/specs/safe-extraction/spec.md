# safe-extraction — fewer exception types

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
string checks SHALL raise `FilterRejectionError`, never a raw
`UnicodeEncodeError`/`ValueError`.

| Constraint | Violation type | Condition |
| --- | --- | --- |
| Path traversal | `FilterRejectionError` | Any `..` component, escaping or internal |
| Absolute path | `FilterRejectionError` | Leading `/`, Windows drive path, or UNC path |
| Null byte | `FilterRejectionError` | `member.name` contains `\x00` |
| Unrepresentable name | `FilterRejectionError` | `member.name` cannot be encoded by the platform filesystem encoding |
| Link-target NUL / unrepresentable | `FilterRejectionError` | SYMLINK/HARDLINK `link_target` contains `\x00` or cannot be encoded by the platform filesystem encoding |
| Symlink escape | `FilterRejectionError` | SYMLINK whose fully resolved target escapes `dest` |
| Hardlink escape | `FilterRejectionError` | HARDLINK whose target path resolves outside `dest` |
| Special file | `FilterRejectionError` | `MemberType.OTHER` device/FIFO/socket/etc. |

**Bidi overrides are rejected by the *policy*, not universally.** Every other
constraint in this requirement makes the **write itself** dangerous or impossible — it
escapes the destination, carries a NUL the OS truncates on, or names a device. A bidi
override does neither: the member lands inside `dest` under exactly its stored bytes, and
what is compromised is the name a person **reads back afterwards**. That is a
presentation property, and presentation is the axis `ExtractionPolicy` owns.

The rejection therefore lives in the portable-name policy below, which means
`ExtractionPolicy.TRUSTED` — defined as *faithful bytes, no name rejection or rewrite* —
SHALL extract such a member unchanged, while `STRICT` (the default) and `STANDARD` SHALL
reject it with `FilterRejectionError`. Running after the caller filter also means a filter
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
| `"../evil"` or `"../../etc/passwd"` | `FilterRejectionError`; no write; all policies |
| `"foo/../bar"` | `FilterRejectionError` under reject/raise behavior even if it would stay in root |
| Leading `/`, Windows drive, UNC path | `FilterRejectionError`; no write; all policies |
| Earlier member creates symlink `foo` outside `dest`; later member writes `foo/x` | Parent resolution rejects `foo/x` with `FilterRejectionError` |
| Name with lone surrogate unencodable by the platform filesystem encoding | `FilterRejectionError` before path resolution; never raw `UnicodeEncodeError` |
| SYMLINK/HARDLINK `link_target` with `\x00` or unencodable surrogate | `FilterRejectionError`; never raw `ValueError`/`UnicodeEncodeError` |
| Name using only `surrogateescape` round-trip low surrogates (`\udc80`–`\udcff`) | Accepted when otherwise safe (representable on disk) |
| `MemberType.OTHER` | `FilterRejectionError`; all policies |

#### Scenario: bidi name matrix

| Case | Expected |
| --- | --- |
| `"invoice‮cod.exe"` extracted under `STRICT` / `STANDARD` | `apply_name_policy` raises `FilterRejectionError`; a `BLOCKED` result and no write |
| The same member extracted under `TRUSTED` | **Extracts**, under the stored name, unmodified — faithful bytes |
| `"a⁦b⁩.txt"` (isolates) extracted | Same split |
| Symlink whose `link_target` contains U+202E | Same split |
| A caller filter renames it to a clean name | Extracts at every policy — the check runs on the final name, after the filter |
| `"‏דוח.pdf"` (RLM, a directional mark) extracted | Extracts; `MEMBER_NAME_BIDI_CONTROL` was reported at listing |
| `"فهرس.txt"` (Arabic script, no controls) extracted | Extracts; no diagnostic, no rejection |
| Any of the above listed rather than extracted | Name presented exactly as stored |
| Bidi-override rejection under either `OnError` | `BLOCKED` result, like any other `FilterRejectionError`; extraction proceeds unless `AbortOn.BLOCKED_MEMBER` is set |

### Requirement: Anti-item extraction is delete-only-if-written

For `is_anti` members, extraction SHALL NOT write payload. It SHALL delete the
destination only if this same extraction wrote that path (file or empty dir via
`lstat`/`unlink`); otherwise it is a success no-op. Pre-existing, populated, or
out-of-root paths MUST NOT be deleted. `MemberType.ANTI` SHALL NOT raise
`FilterRejectionError` (only `OTHER` does).

A delete SHALL also release the destination's collision claim, so a later member
resolving to the same key does not collide against content that no longer exists.
The claim and the on-disk entry are two records of the same fact and SHALL be
cleared together; a stale claim would otherwise abort under `AbortOn.NAME_COLLISION`
with the destination empty, or revise an already-deleted member to `OVERWRITTEN`.

#### Scenario: anti extraction matrix

| Case | Expected |
| --- | --- |
| Anti path missing / pre-existing not written this run | Success no-op; pre-existing untouched |
| Earlier member this run wrote the path, then anti | Just-created file/empty dir removed |
| Same case, then a later member with the same collision key | No collision: the delete released the claim |
| Anti no-op (nothing written this run at that path) | Unrelated claims untouched |
| `check_universal` on `ANTI` | No `FilterRejectionError` for type alone |
| `MemberType.OTHER` | Still `FilterRejectionError` under all policies |

### Requirement: Symlink Escape Re-Validated at Extraction Time

The system SHALL validate a SYMLINK member after `os.symlink(link_target,
dest_path)` creates the link on disk. It resolves the created link target with
`Path.resolve()` and, if the resolved path escapes `dest`, immediately unlinks the
new link and raises `FilterRejectionError`. Resolution failures from symlink loops
or platform equivalents (`OSError` such as `ELOOP`, or `RuntimeError`) SHALL fail
safe the same way: unlink the just-created link and reject the member.

This post-creation check SHALL catch chained symlink attacks where earlier archive
members influence later target resolution, without allowing writes through an
escaping link.

#### Scenario: symlink revalidation matrix

| Case | Expected |
| --- | --- |
| Created symlink resolves outside `dest` | Link is unlinked; `FilterRejectionError`; no later data written through it |
| Chained symlink attack through earlier member | Post-creation resolution catches the escape and raises `FilterRejectionError` |
| Cyclic links (`a -> b`, `b -> a`) make `Path.resolve()` raise | Just-created link is unlinked; `FilterRejectionError`; no uncaught OS/runtime error |

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
    collided_with: Path | None = None

class ExtractionStatus(str, Enum):
    EXTRACTED = "extracted"
    NOT_OVERWRITTEN = "not_overwritten"
    SUPERSEDED = "superseded"
    OVERWRITTEN = "overwritten"
    BLOCKED = "blocked"
    FAILED = "failed"
    LINK_TARGET_UNAVAILABLE = "link_target_unavailable"
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
`ArchiveyError` or permitted filesystem `OSError`; `LINK_TARGET_UNAVAILABLE` is a member the archive
describes but does not carry enough information to write — a symlink for which it
records no target at all (`path=None`, `error=None`, `requested_path` set). `NOT_OVERWRITTEN`,
`SUPERSEDED`, `OVERWRITTEN` and `LINK_TARGET_UNAVAILABLE` are not failures.

A symlink for which **the archive records no target** SHALL be recorded
`LINK_TARGET_UNAVAILABLE` rather than raised as a per-member failure, under either `OnError`
value, and SHALL NOT disturb an existing destination: the check happens before overwrite resolution, so `OverwritePolicy.REPLACE`
does not unlink an entry for a member that is not going to be written. The archive's
omission is reported through the diagnostics channel
(`SYMLINK_TARGET_UNAVAILABLE`, an archive-integrity code), which is where an anomaly in
the archive's own metadata belongs; the extraction result records only what extraction
did about it. This is not confined to one cause: a writer that discarded the target
(7-Zip records none for a directory reparse point), a reparse buffer that names nothing
and a member carrying no data at all leave extraction with the same nothing to write.

What the archive records is the condition, not whether this read produced a target.
An unset `link_target` has two other causes, and in both the archive carries a target
this read could not produce: **not resolved yet** — a ZIP or 7z link read in streaming
mode carries its target in the member's data, which that mode has already passed — and
**resolved but out of reach**, where the reader looked and the bytes were compressed,
split across volumes or encrypted. Recording either `LINK_TARGET_UNAVAILABLE` would
report success while dropping a member the archive describes in full, so both SHALL
stay a per-member failure. The reader SHALL therefore report which of the two an empty
lookup was, rather than leaving extraction to infer it from the lookup having run.

"Not resolved yet" is about the target's *bytes*, so it SHALL NOT be reached for a
member whose absent target the header already states. Where a reader can tell from
metadata alone that the archive records no target — a reparse point a writer stored no
data for is the case that exists — it SHALL settle that while typing the member, not in
a lookup that reads data. Otherwise the two paragraphs above disagree in a streaming
pass, whose lookup runs at EOF: the member the first one names would take the second
one's per-member failure, and the library default would abort the archive on exactly
the entry this outcome was added for.

That is also the bound on the read modes. `LINK_TARGET_UNAVAILABLE` holds in a
streaming pass exactly for the members a reader settles from metadata; where the
archive's omission is legible only in the member's *data* — a reparse buffer that names
nothing, bytes that are not a link buffer at all, a RAR3/4 link carrying none — a
streaming pass does not learn it until EOF, by which time the member has already been
written or not. Those SHALL take the per-member failure that an unresolved target
takes, and the library default aborts the archive there. Settling them in a streaming
pass would mean holding a reparse point's data until the member is written, which is a
different guarantee and is not required here.

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

`collided_with` SHALL carry the already-written destination this member collided
with, and SHALL be `None` when nothing this run held the name. It SHALL be set under
exactly the condition that constitutes a collision *event* — a destination claimed by
a member of this same run, under a non-`TRUSTED` policy — and therefore for **every**
resolution: `SKIP`, `ERROR`, a `REPLACE` merge, and `RENAME` alike. An obstacle that
was already on disk before extraction started is not a collision event and SHALL
leave the field `None`; the destination is recorded in `requested_path` regardless.

This is what makes the collision *cause* a property of the result rather than an
inference over the report: without it, a member blocked by another member of this run
and a member blocked by a pre-existing file produce identical results under every
policy. Callers MAY join to the blocking member by plain path equality against another
result's `path`, or its `requested_path` when that member was itself later revised to
`OVERWRITTEN` and no longer holds a live path.

`failure_group_id` / `failure_group_size` SHALL both be set only when one failed
hardlink source causes `N` `FAILED` link results, which SHALL share one group id and
`failure_group_size=N`; otherwise both are `None`. The id SHALL be a `str` generated
as `uuid.uuid4().hex` — the shape and generation the field carried on the diagnostics
channel before it moved here; relocating it did not change its type. It is opaque:
callers MAY compare ids for equality to join a group, and SHALL NOT rely on ordering,
format, or cross-run stability.

`ExtractionResult` has no diagnostics field; `status`, `error` and the fields above
are the per-result outcome.

#### Scenario: collision cause is recorded on the result

| Case | `collided_with` |
| --- | --- |
| Blocked by a member of this run (`SKIP` / `ERROR` / `REPLACE` / `RENAME`) | the prior member's written path |
| Blocked by an entry already on disk before extraction | `None` |
| No collision at all | `None` |
| Any collision under `ExtractionPolicy.TRUSTED` | `None` (no collision event) |

#### Scenario: result/status matrix

| Case | Expected |
| --- | --- |
| User filter returns `None` | No `ExtractionResult`; no result-count impact (like a selector exclusion) |
| Selector excludes member | No `ExtractionResult`; no result-count impact |
| Member blocked by `FilterRejectionError` under `CONTINUE` | Result is `BLOCKED` with matching error; no diagnostic emitted |
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
