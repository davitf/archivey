# Archive Reading

## Purpose

Uniform interface for opening and reading archives. `ArchiveReader` presents ZIP,
TAR, RAR, 7z, ISO, directories, and single-file compressed streams with consistent
metadata, iteration, and data-access semantics.

This spec is the **caller-facing** `ArchiveReader` surface. Cross-cutting
machinery lives elsewhere:

| Concern | Spec |
| --- | --- |
| `streaming` legality × method table, cost receipts | `access-mode-and-cost` |
| Diagnostic values, retention budget, watermarks | `diagnostics` |
| Detection → reader diagnostic handoff | `format-detection` |
| `MemberStreams.CONCURRENT`, ownership, free-threaded opens | `reader-concurrency` |
| Extraction filters / bomb limits | `safe-extraction` |

## Requirements

### Requirement: Opening an archive for reading

The system SHALL expose:

```python
archivey.open_archive(
    source: str | Path | BinaryIO | Sequence[str | Path | BinaryIO],
    *,
    format: ArchiveFormat | None = None,
    streaming: bool = False,
    seekable_members: bool = False,
    concurrent_members: bool = False,
    password: PasswordInput = None,
    encoding: str | None = None,
    config: ArchiveyConfig | None = None,
) -> ArchiveReader
```

`source`, multi-volume ordering, `streaming`, password candidates/providers,
encoding, configuration precedence, and backend selection retain their existing
contracts. `format=None` auto-detects; an explicit format bypasses detection.

**An explicit argument the resolved backend cannot act on is handled by its
*intent*, and the rule SHALL be applied to every such argument:**

> **Refuse** when the argument is an **assertion about this archive**. **Permit, and
> record a diagnostic**, when it is a **resource offered for use if needed.**

| Argument | Intent | Behaviour when the backend cannot act on it |
| --- | --- | --- |
| `format=` | assertion — "I claim this is a ZIP" | refuse when it cannot hold (see the directory rule below) |
| `password=` | resource — a keyring | permit in **every** form; `PASSWORD_ARGUMENT_UNUSED` for a concrete value, none for a provider |
| `encoding=` | resource — a hint for name decoding | permit; `ENCODING_ARGUMENT_UNUSED` |

`password=` on a format with no encryption SHALL NOT raise, in any of its forms — a
single value, an ordered sequence, and a provider callable SHALL open identically
(accepted, never consulted). A single value or a sequence records one diagnostic; a
provider callable records none, because it offers a password only if asked. A *wrong* password on an *encrypted*
archive is unaffected and still raises. Each backend SHALL declare whether it consumes
`encoding` (`ReadBackend.USES_ENCODING`) the same way it declares
`ReadBackend.SUPPORTS_PASSWORD`, so the check is central rather than per-backend
silence.

A **directory path** resolves to `ArchiveFormat.DIRECTORY`. An explicit `format=`
naming anything else SHALL raise `ArchiveyUsageError` rather than being discarded:
silently overruling it returns a reader over the directory tree to a caller who
asserted a different format, so every read downstream succeeds on the wrong data.
`format=ArchiveFormat.DIRECTORY` and `format=None` both remain valid. This is the
assertion half of the rule above, not a special case.

**Diagnostics at open (observable):** On success, advisory events from automatic
detection (if any) appear in this reader's cumulative `diagnostics` for its
lifetime and are not duplicated. Explicit `format=` skips detection, so open
adds no detection diagnostics. Unused-argument diagnostics are emitted before the
reader is returned, so they are readable without listing anything. If open raises,
no reader is returned.

Handoff mechanics (one shared collector/budget, no copy/re-seed): see
`format-detection` and `diagnostics`.

#### Scenario: open matrix

| Case | Expected |
| --- | --- |
| Auto-detect succeeds | Detection events visible on `reader.diagnostics`; not duplicated |
| `format=ArchiveFormat.ZIP` succeeds | No detection diagnostics from open |
| Open raises | No reader returned |
| `password="secret"` | Returned reader uses that password for encrypted members |
| `password=` a value or a sequence, format with no encryption | Opens; `PASSWORD_ARGUMENT_UNUSED`; no raise |
| `password=` a provider callable, format with no encryption | Opens; no diagnostic; provider never called |
| `encoding=` on a backend that decodes names another way | Opens; `ENCODING_ARGUMENT_UNUSED`; names unchanged |
| Directory path, no `format=` | Opens as `DIRECTORY` |
| Directory path, `format=ArchiveFormat.DIRECTORY` | Opens as `DIRECTORY` |
| Directory path, `format=ArchiveFormat.ZIP` | `ArchiveyUsageError`, naming the path and the requested format |

### Requirement: Declared member-stream capabilities

`open_archive()` SHALL accept two keyword-only booleans, both defaulting to `False`:

- `concurrent_members=True` — any number of member streams may be open simultaneously
  (full contract: `reader-concurrency`)
- `seekable_members=True` — every member stream from random `open()` is seekable

The system SHALL NOT expose a flag-enum parameter for this purpose. `open_stream` SHALL
keep its `seekable: bool` parameter, and both entry points SHALL use the same `seekable`
vocabulary for the same concept; concurrency has no meaning for a single standalone
stream, so `open_stream` MUST NOT gain a concurrency parameter.

The `MemberStreams` flag type is the internal representation the booleans map to at
the entry point. It SHALL stay importable from `archivey.types` for backends and tests
and SHALL NOT be re-exported from `archivey` or listed on the API page. It is no longer
an input to `open_archive`. It is NOT required to appear on `CostReceipt` or in
diagnostics — neither carries it, and no requirement SHALL claim otherwise. No public
reader attribute SHALL expose the declared capabilities; the two booleans on
`open_archive` are the whole contract.

**Default (neither declared), every format including directory:** at most one live member
data stream per reader; streams are forward-only. "Live" spans `open()` →
stream `close()`/context exit (not EOF, not GC). A second overlapping `open()`
SHALL raise `ConcurrentAccessError` at the later call and leave the first stream
untouched/readable — the gate never resolves contention by closing a held stream.
The refusal SHALL happen before the member is opened: a refused `open()` SHALL NOT
construct a member data stream, spawn a helper process, or read member data.
(This is a rule about *contention*, not lifetime: `reader.close()` does close
member streams — see "Context-manager and close lifecycle".) Every member
stream (random `open()` and `stream_members()` yields) SHALL report
`seekable() is False`; `seek()` SHALL raise `io.UnsupportedOperation`; `tell()`
SHALL work. Sequential `open → read → close → open next` is unaffected.

With `seekable_members=True`, every file member stream from random `open()` SHALL
report `seekable() is True` and `seek()` SHALL work, including a backward seek
that returns the same bytes. The cost MAY be a full re-decode from the member
start (loud-slow-rewind). `stream_members()` yields are a single-pass decode;
SEEKABLE does not require those handles to seek.

`ConcurrentAccessError`'s message SHALL name the parameter a caller would pass to
allow the operation (`concurrent_members=True`), not an internal type.

`open_archive()` SHALL capture the caller stack once; `ConcurrentAccessError`
SHALL include that `file:line`. Full stack is retained on the reader for
diagnostics (no config knob). Capabilities are per-archive intent only — no
`ArchiveyConfig` equivalent, no per-`open()` flag. Access cost never determines
legality; the cost receipt describes expense.

**Internal ops exempt:** `extract_all()` (incl. hardlink recovery), symlink-target
reads, password confirmation, and other library-internal opens run under internal
scopes and need no declared capability.

**Out of gate scope:** non-overlapping open *order* on solid archives (each
re-decode from block start) stays under `AccessCost` / `solid_block_count` /
`stream_members()` steer. Docs for the capability booleans SHALL state this.

#### Scenario: capability gate matrix

| Case | Expected |
| --- | --- |
| Overlapping second `open()` without `concurrent_members` (ZIP/TAR/ISO/single-file/dir) | `ConcurrentAccessError` at later `open()` with open_archive `file:line`; first stream remains readable |
| Refused second `open()` without `concurrent_members` | Raises before the member is opened — no member stream constructed, no helper process spawned, no member data read |
| Non-overlapping open/read/close loop, no capabilities declared | All opens succeed |
| Stream without `seekable_members` (incl. real directory file) | `seekable()` false; `seek()` → `io.UnsupportedOperation`; `tell()` + forward reads OK |
| Same member via random `open()` with `seekable_members=True` | `seekable()` true; backward seek rereads; loud-slow-rewind when there is no index/accelerator |
| `extract_all()` with nothing declared | Completes; internal opens ungated |
| `open_archive(p, member_streams=...)` | `TypeError` — the parameter no longer exists |

### Requirement: Multi-volume and multi-source input

`open_archive()` SHALL accept a multi-volume archive either way and present one
logical `ArchiveReader`:

- **Single path in a volume set** (e.g. `name.7z.001`, `name.exe.001`,
  `name.part1.rar`, `name.part1.sfx`, `name.rar` + `name.r00`…, or an old-scheme
  SFX first volume `name.exe` / `name.sfx` + `name.r00`): discover
  siblings in natural order
- **Stub-only SFX** (`name.exe` / `name.sfx` with no archive magic) beside
  exactly one of `name.exe.001`, `name.7z.001`, `name.zip.001`: open that
  first volume's set, including when `format=` is set. Two of those names
  SHALL raise `UnsupportedFeatureError`. A stub that itself contains archive
  magic SHALL open as that archive (no redirect). If `format=` names a
  different container than the sibling, raise `ArchiveyUsageError`.
- **Explicit ordered `source` sequence**: use that order as volumes

Joining is format-specific (`format-7z` / `format-rar`): 7z concatenates a split
byte stream; RAR parses self-describing volumes in order and stitches
boundary-spanning members. Incomplete/out-of-order sets SHALL raise
`UnsupportedFeatureError` or a truncated/corrupt error — never a partial result.

An explicitly passed sequence gets no discovery, so it SHALL be required to name the
parts of one archive, and a sequence mixing two sets SHALL raise
`ArchiveyUsageError` naming both. Part numbers alone cannot detect this —
`alpha.zip.001` and `beta.zip.002` are a valid `1, 2` — and the result would be
bytes belonging to neither archive. All three volume naming schemes SHALL be covered,
by three rules:

- parts carrying a number SHALL all be in the same scheme, the parts of one set being
  all named the same way;
- within that scheme their bases SHALL agree, compared case-insensitively against the
  base that scheme reads;
- a name carrying no part number but shaped like a first volume (`<base>.rar` /
  `.exe` / `.sfx`) SHALL be required to share its stem with the `.rNN` parts present,
  and SHALL be refused beside parts of another scheme — except that an `.exe` /
  `.sfx` beside a numbered set is the 7-Zip stub, whose name is not derived from
  theirs, and is allowed.

A `<base>.partN.rar` names both a `.partN` part and an old-scheme first volume based
on `<base>.partN`, so it SHALL be read as the latter when the sequence carries `.rNN`
parts on that base, which is the reading discovery produces from those `.rNN` names.

A sequence in which no name carries a part number SHALL NOT be subject to these rules,
nothing in it saying that any of the names is a volume.

The check is on names only: parts of one set in different directories remain valid, an
item whose name matches none of the above is passed through in the position given (and
suspends the completeness check for that sequence), and a sequence containing an open
stream is not checked, a stream having no name to compare. Completeness (numbered
`1..N` with no gaps) applies to the numbered scheme only, RAR volumes being
self-describing.

#### Scenario: volume input matrix

| Case | Expected |
| --- | --- |
| `open_archive("disc.7z.001")` with siblings present | One reader for the whole set |
| `open_archive("vol.exe.001")` (or `.7z.001` / `.zip.001`) with no siblings | `TruncatedError` names the missing parts |
| `open_archive("archive.exe")` with `archive.r00` siblings | One logical RAR archive; `.exe` / `.sfx` is volume 1 |
| `open_archive("vol.exe")` with a stub-only exe and `vol.exe.001` / `vol.7z.001` / `vol.zip.001` | One reader for that set |
| `open_archive("vol.exe", format=ZIP)` with a stub-only exe and a zip first volume | One reader for that set |
| `open_archive("vol.exe", format=SEVEN_Z)` beside a zip first volume | `ArchiveyUsageError` |
| `open_archive("vol.exe", format=ZIP)` with embedded ZIP SFX and a sibling volume | Opens the stub; no redirect |
| `open_archive("vol.exe")` with two of those first-volume names | `UnsupportedFeatureError` |
| `open_archive([vol1, vol2, vol3])` in order | One archive in that order |
| `open_archive([alpha.zip.001, beta.zip.002])` | `ArchiveyUsageError` naming both bases |
| `open_archive([alpha.part1.rar, beta.part2.rar])`, or `[alpha.rar, beta.r00]` | `ArchiveyUsageError` naming both bases |
| `open_archive([movie.part1.rar, movie.part2.rar, readme.rar])`, or `[alpha.zip.001, beta.part1.rar]` | `ArchiveyUsageError`: two sets |
| `open_archive([stub.exe, vol.7z.001, vol.7z.002])` | One archive in that order; the stub is not a second set |
| `open_archive([Show.part1.rar, Show.part1.r00, Show.part1.r01])` | One archive in that order; volume 1 of the `.rNN` set |
| `open_archive([Show.part1.rar, Show.part2.rar, Show.part1.r00])` | `ArchiveyUsageError`: two sets |
| `open_archive([alpha.rar, beta.rar])` | One stream over both; no part number, so no set to check |
| `open_archive([a/alpha.zip.001, b/alpha.zip.002])` across directories | One archive in that order |
| Missing volume | Raise at open or first dependent read; no partial member list |

### Requirement: Archive metadata access

The system SHALL expose read-only:

```python
@property
def info(self) -> ArchiveInfo: ...

@property
def cost(self) -> CostReceipt: ...

@property
def format(self) -> ArchiveFormat: ...

@property
def format_info(self) -> FormatInfo | None: ...
```

`info` is format/version/solid/member count/comment/encryption/multivolume/cost.
`cost` is listing/access/stream capability/solid block count. `format` is the
`(container, stream)` pair. `format_info` is the `FormatInfo` from the detection
`open_archive` ran, kept rather than repeated, so it equals what `detect_format`
reports for the same source; it is `None` when `format=` was passed (no detection
ran), and a directory reports the same fixed `DIRECTORY` / `CERTAIN` / `"directory"`
answer `detect_format` gives.

#### Scenario: metadata after open

| Case | Expected |
| --- | --- |
| Successful open | `ar.info`, `ar.cost`, `ar.format`, `ar.format_info` available immediately without extra I/O |
| Opened with `format=` | `ar.format_info is None` |
| Opened by detection | `ar.format_info == detect_format(source)`, without a second detection |

### Requirement: MemberListReport surfaces partial listings with terminal errors

The system SHALL expose an immutable listing report and a materializing accessor
that always returns both recovered members and any terminal archive-level error:

```python
@dataclass(frozen=True)
class MemberListReport:
    members: tuple[ArchiveMember, ...]
    error: ArchiveyError | None
    diagnostics: DiagnosticSummary

def members_report(self) -> MemberListReport: ...
```

`members_report()` SHALL recover every member the backend can list before a
terminal archive-level failure, put them in `members` (archive order), set
`error` to that failure or `None` when the listing is complete, and attach a
point-in-time `diagnostics` snapshot for the operation. It MUST NOT raise for
terminal archive-level listing errors covered by this requirement (those belong
on `error`). Open-time failures and `ResourceLimitError` from `ListingLimits`
SHALL still raise (limits are not the damage story).

`error is None` SHALL mean the listing is complete. Callers MUST treat a
non-`None` `error` as an incomplete listing even when `members` is non-empty.
The report SHALL iterate, index, and size as its `members` sequence (same
ergonomics as `ExtractionReport` vs its results).

Members in the report SHALL be identity-stamped for this reader (`member in
reader`) so `open(member)` works for recovered `FILE` members. An incomplete
report (`error` set) MUST NOT be treated as a successful complete materialization:
subsequent `members()` / `scan_members()` / `get(name)` MUST still raise the
terminal error rather than return a silent partial list.
`members_report_if_available()` SHALL return a `MemberListReport | None`: the stored
report when one exists without scanning — complete (`error is None`) **or**
incomplete (`error` set) from a prior pass — or the upfront index as a complete
report for backends that carry one; `None` only when nothing is materialized and a
scan would be required. Returning an incomplete report to a caller MUST NOT change
the complete-or-raise behaviour of `members()` / `scan_members()` / `get(name)`;
the report self-labels via `error` and those methods still raise.

On `streaming=True`, `members_report()` MAY start or finish the single forward
pass (like `scan_members`) and thereby consume it; it still returns a report
instead of raising on terminal archive-level listing errors.

#### Scenario: members_report / MemberListReport matrix

| Case | Expected |
| --- | --- |
| Clean archive | `error is None`; `members` is the full fully-resolved list |
| TAR rejected mid/final header after prefix (Option F) | `members` = recoverable prefix; `error` is `CorruptionError`; report stored incomplete |
| Absent/short TAR trailer with `ARCHIVE_EOF_MARKER_MISSING` set to `RAISE` | `DiagnosticRaisedError` raised: the caller's policy firing, not listing damage, so it is not carried on `error` |
| `members_report()` then `members()` on same RA reader after incomplete | `members()` raises the terminal error (not a partial list) |
| `open(report.members[i])` for a recovered FILE after incomplete | Succeeds by identity |
| `get(name)` after incomplete | Raises terminal error / does not pretend completeness |
| `members_report_if_available()` after incomplete pass already ran | Returns the incomplete report (prefix + `error`); count is a floor |
| `members_report_if_available()` with no materialization and no upfront index | `None` |
| `ListingLimits.max_members` exceeded during `members_report` | `ResourceLimitError` raised (not soft-returned on `error`) |
| Streaming `members_report` after recoverable prefix + terminal error | Report with prefix + error; pass consumed |

### Requirement: Sequential in-order iteration

```python
def __iter__(self) -> Iterator[ArchiveMember]: ...     # sequential, in-order
def members(self) -> list[ArchiveMember]: ...          # materialize (RA only)
def scan_members(self) -> list[ArchiveMember]: ...      # fully-resolved, either mode
def members_report(self) -> MemberListReport: ...         # prefix + error report
def members_report_if_available(self) -> MemberListReport | None: ...  # report peek
```

`__iter__` MUST yield in archive order without loading all members into a
*caller-visible* complete cache before the first yield when a terminal
archive-level error will follow a recoverable prefix. In **random-access** and
**streaming**, after yielding every recovered member, a terminal archive-level
listing error SHALL propagate (yield-then-raise). In **random-access**,
`members()` MAY scan formats without a central directory; after **successful
complete** materialization, later `__iter__` calls MUST use the cache. In
**streaming**, no cache-replay: `__iter__` is part of the single forward pass (see
`access-mode-and-cost`).

`members()` and `scan_members()` SHALL remain **complete-or-raise**: they return
a fully-resolved `list[ArchiveMember]` only when the listing completes; on a
terminal archive-level listing error they SHALL raise that error and MUST NOT
return a partial list. Prefer `members_report()` when both the prefix and the
error are required.

`scan_members()` SHALL return the fully-resolved list (`link_target_member` filled
where the target exists, incl. forward-pointing and last-wins symlinks)
when complete. In RA it equals `members()`. On `streaming=True` it returns the
cache if the pass completed successfully, else **finishes that pass** (from
start or draining an interrupted one), resolves links, and returns the list —
or raises on terminal archive-level listing error. It is the only
complete-or-raise method permitted after an iteration method has started;
running it consumes/finishes the pass.

A live forward pass leaves forward-pointing symlinks unresolved at yield time.
Completing a pass **successfully** via `__iter__`, `stream_members`,
`extract_all`, or `scan_members` SHALL store a complete `MemberListReport`
(`error is None`) finalized in place on already-yielded objects so
`members_report_if_available()` returns it. An abandoned pass (early `break`, no
`scan_members()`) SHALL NOT finalize. A pass that ends in a terminal
archive-level listing error after a prefix SHALL store an incomplete report
(`error` set) — never a complete one (see `MemberListReport` requirement).

No `__len__` / `__getitem__` (not a collection; protocols are probed implicitly —
`list(reader)` probes `__len__` for preallocation). `len(ar)` → Python `TypeError`
in every mode; use `len(ar.members())`, `ar.info.member_count`, or count while
iterating. `list(ar)` just iterates (and may raise after yielding a prefix).

`members_report_if_available()` is a report peek: it returns the stored
`MemberListReport` (complete or incomplete) when one exists without scanning, or
the upfront index as a complete report for backends that carry one, else `None`.
It never scans, reads member data, or starts/consumes the forward pass — an
incomplete report is only returned when a prior pass already stored it, so the
never-scan promise holds. Report members may have unresolved links when targets
live in member data (see `access-mode-and-cost`); link resolution is independent
of `error` (completeness).

With `streaming=True`, `members()` / `get()` / `open()` / `read()` SHALL raise
`UnsupportedOperationError` uniformly. Only one forward pass
(`__iter__`/`stream_members` or one `extract_all`) is allowed, with
`scan_members()` / `members_report()` to finish/return it and
`members_report_if_available()` anytime.
Canonical access-mode × method table: `access-mode-and-cost`.

#### Scenario: iteration / access-mode matrix

| Method / action | `streaming=False` | `streaming=True` |
| --- | --- | --- |
| `__iter__` | Yields in order; after successful complete materialization, from cache; terminal archive error → yield prefix then raise | Single-use forward pass; terminal archive error → yield prefix then raise; second `__iter__`/`stream_members`/`extract_all` → `UnsupportedOperationError` |
| `members()` | Full scan if needed; complete list or raise (no partial return) | `UnsupportedOperationError` |
| `scan_members()` | Same fully-resolved list as `members()` when complete; raise on terminal archive error | Finishes/drains pass; complete list or raise; pass consumed |
| `members_report()` | Always returns `MemberListReport` (prefix + `error`) | Always returns report; may consume the pass |
| `scan_members()` after early `break` | n/a | Drains remainder; complete list or raise on terminal error |
| `members_report_if_available()` after completed **successful** pass | Complete report (`error is None`) if indexed/cached | Complete report (not `None`); forward-link finalization visible on yielded objects |
| `members_report_if_available()` after incomplete (error) pass already ran | Incomplete report (prefix + `error`) | Incomplete report (prefix + `error`) |
| `members_report_if_available()` after abandoned pass / before any materialization | `None` (unless upfront index) | `None` |
| `len(ar)` | `TypeError` | `TypeError` |
| `list(ar)` | Iterates (may raise after prefix) | Iterates (consumes the single pass; may raise after prefix) |

### Requirement: Listing resource limits

The system SHALL define frozen `ListingLimits` and apply them from the reader's
open `ArchiveyConfig.listing_limits` when registering members into a
materialized or resolved member list (`members()`, `scan_members()`, and any
path that materializes via `_get_members_registered` / equivalent). There is no
per-call listing-limits override.

```python
@dataclass(frozen=True)
class ListingLimits:
    max_members: int | None = 1_048_576
    max_metadata_bytes: int | None = 64 * 2**20  # 64 MiB
    UNLIMITED: ClassVar["ListingLimits"]
```

`None` on a field disables that guard. `ListingLimits.UNLIMITED` disables both.
Crossing either guard SHALL raise `ResourceLimitError` naming the knob and
limit. Format-local parser bounds (e.g. 7z header-size checks) MAY still raise
at parse/open for nonsensical or hostile headers and are complementary, not a
substitute. 7z and RAR apply `listing_limits.max_members` at parse
(`format-7z`, `format-rar`); `None` disables that bound. Other formats that
build an internal member table during `open_archive()` MAY still allocate up
to their own parser ceilings before spine `ListingLimits` are evaluated on
materialization (`members()` / extract-prep). Being indexed (ZIP central
directory) is not the same as applying `max_members` at parse.

**Unguarded by design:** `stream_members()` / `streaming=True` / forward-only
iteration MUST NOT enforce `ListingLimits` (O(1) escape hatch). Callers that
need a full resolved list use `members()` / `scan_members()` and accept the
caps. Formats that apply `max_members` at parse (7z and RAR) fail at
`open_archive` instead, so `stream_members()` / `streaming=True` are not an
escape hatch there.

#### Scenario: listing-limits matrix

| Case | Expected |
| --- | --- |
| Default config, archive with ≤1_048_576 members and metadata under 64 MiB | `members()` / `scan_members()` succeed |
| Registered member count would exceed `max_members` | `ResourceLimitError` before/at that registration, or at `open_archive` on formats that apply `max_members` at parse (`format-7z`, `format-rar`); no full cache published |
| Cumulative retained metadata would exceed `max_metadata_bytes` | `ResourceLimitError` naming `max_metadata_bytes` |
| RAR archive whose compressed RAR 1.5/2.x comments declare more than `max_metadata_bytes` in total | `ResourceLimitError` naming `max_metadata_bytes` at `open_archive` (`format-rar`) |
| `ListingLimits.UNLIMITED` | Count and metadata guards disabled |
| `stream_members()` / `streaming=True` over an archive that would fail `members()` under defaults | Iteration proceeds without listing-limit errors, except formats that already applied `max_members` at parse (7z and RAR), which raise at `open_archive`, and RAR's compressed-comment budget, which also raises there |
| `extract_all` path that materializes members first | Same listing caps as `members()` before extraction bomb guards |

### Requirement: Listing metadata-byte accounting

The system SHALL measure `max_metadata_bytes` as a **safety-oriented weight** of
retained string/bytes fields accumulated as members are registered, plus
archive-level `ArchiveInfo.comment` once when known. Exact UTF-8 encoding of
every field is not required — the cap exists to bound metadata bombs, not to
mirror an allocator — but the weight MUST NOT under-count UTF-8 size:

- `str` fields `name`, `comment`, `link_target`, `uname`, `gname`: a cheap
  upper bound on UTF-8 length — `len(s)` when `s` is ASCII, otherwise
  `4 * len(s)` (UTF-8 is at most 4 bytes per code point). Implementations MAY
  use a stricter exact encode; they MUST NOT use a measure that can be smaller
  than UTF-8 (plain `len(s)` on non-ASCII would under-count a Unicode name bomb).
- `raw_name`: `len(raw_name)` when not `None` (stored archive bytes; already exact)
- `extra`: lengths of `str` / `bytes` values under the same rules; for a one-level
  `dict` value, nested `str` / `bytes` values only
- Exclude: `_raw`, `hashes`, diagnostics, Python object overhead

A field filled in after its member was registered SHALL be weighed when it is filled
in, under the same enforcement as registration. The case that exists is a symlink
target stored as member data (ZIP, 7z, RAR3/4), which is read only once every member is
registered: a target resolved while materializing `members()` / `scan_members()` SHALL
count toward `max_metadata_bytes` before that list is published.

A field that is expanded before any member is registered MAY be weighed by the size
its header declares, before it is expanded, when expanding it is itself the cost to
bound. The case that exists is RAR 1.5/2.x compressed old-style comments, each decoded
by a separate `unrar` process: `format-rar` sums their declared unpacked sizes at
`open_archive` and refuses the archive before decoding any. That check is separate
from the running total above; the decoded comments are weighed again at registration.

#### Scenario: metadata accounting matrix

| Case | Expected |
| --- | --- |
| Member with long `name` + `raw_name` | Both weights count |
| Huge `ArchiveInfo.comment` alone | Counts toward the budget once |
| `extra` holds opaque non-str/bytes object | Not counted |
| ASCII-only name | Weight equals `len(name)` (exact UTF-8) |
| Non-ASCII / surrogateescape name | Weight ≥ UTF-8-with-surrogateescape byte length (upper-bound OK) |
| Symlink target read from member data after registration | Weighed when read; over the cap → `ResourceLimitError` naming `max_metadata_bytes` |
| RAR compressed old-style comments whose declared sizes sum past the cap | Refused at `open_archive` before any is decoded (`format-rar`) |

### Requirement: Name lookup and member identity

No `__getitem__` (duplicates break mapping; dunders are probed implicitly).
`open()`/`read()` accept a name and raise `KeyError` when absent.

```python
def get(self, name: str, default=None) -> ArchiveMember | None: ...
def __contains__(self, member: ArchiveMember) -> bool: ...  # identity, O(1), any mode
```

`get()` looks up by normalized name; duplicates → **last** (sequential extraction
winner). On `streaming=True` SHALL raise `UnsupportedOperationError` regardless of
loaded index. For a no-scan peek use `members_report_if_available()`.

`member in reader` is identity membership (yielded by this reader), O(1), any mode.
Non-`ArchiveMember` (notably a name string) SHALL raise `TypeError` pointing to
`get()`. `__contains__` MUST exist — without it, `in` falls back to `__iter__` and
would consume a streaming pass.

#### Scenario: lookup / membership matrix

| Case | Expected |
| --- | --- |
| `get` existing name | That `ArchiveMember` |
| `get` missing | `default` / `None`; `open`/`read` of missing name → `KeyError` |
| `get` on `streaming=True` | `UnsupportedOperationError` |
| `member in ar` (yielded by `ar`) | `True`; foreign member → `False`; no scan |
| `"file.txt" in ar` | `TypeError` → use `get()`; never iterate |

### Requirement: Reading member data

`ArchiveStream` SHALL implement `BinaryIO`, remain caller-closed, and expose an
immutable operation-filtered diagnostic snapshot:

```python
class ArchiveStream(BinaryIO):
    @property
    def diagnostics(self) -> DiagnosticSummary: ...

def read(self, member: str | ArchiveMember) -> bytes: ...
def open(self, member: str | ArchiveMember) -> ArchiveStream: ...
```

Unknown name → `KeyError`; foreign `ArchiveMember` → `ValueError`. `read()`
materializes the full payload without extraction bomb checks (small trusted
members). `open()` streams in bounded chunks. Full reads verify supported digests;
streaming verification raises `CorruptionError` only on the terminal read after
valid chunks; `read()` raises without returning bytes.

After symlink/hardlink following, if the **resolved** member is
`DIRECTORY`, `ANTI`, or `OTHER`, `open()` / `read()` SHALL raise
`ArchiveyUsageError`. They MUST NOT return empty bytes, and MUST NOT leak raw
`IsADirectoryError` or format `CorruptionError` for directory paths. A link whose
target is missing SHALL still raise `LinkTargetNotFoundError` (`ArchiveyError`).

**Diagnostics (observable):** A reader-owned stream's `diagnostics` shows only
that open operation's events; the same events also appear on the reader's
cumulative snapshot without being retained twice. A standalone `ArchiveStream`
(not owned by a reader) has its own lifetime summary. Retention/budget rules:
`diagnostics`.

#### Scenario: read / open matrix

| Case | Expected |
| --- | --- |
| `open("data.bin")` succeeds | `ArchiveStream` as `BinaryIO`; `stream.diagnostics` = that operation only |
| Reader-owned stream emits rewind diagnostic | Visible on stream and reader snapshots; retained once |
| `read("readme.txt")` | Full uncompressed `bytes` |
| `open(member)` from a different reader | `ValueError` |
| `open`/`read` directory (ZIP/TAR/ISO/directory/7z) | `ArchiveyUsageError` |
| `open`/`read` `MemberType.ANTI` or `OTHER` | `ArchiveyUsageError` |
| Symlink resolves to a file | Follow succeeds; returns file stream/bytes |
| Symlink target missing in archive | `LinkTargetNotFoundError` |

### Requirement: Non-file stream_members yield None

`stream_members` SHALL pair every non-file member (`DIRECTORY`, `SYMLINK`,
`HARDLINK`, `OTHER`, `ANTI`) with `stream is None` (no empty `ArchiveStream`).

#### Scenario: non-file stream matrix

| Case | Expected |
| --- | --- |
| Directory member | Stream `None` |
| `MemberType.ANTI` | Stream `None` |

### Requirement: Bounded-memory sequential streaming via stream_members

```python
def stream_members(
    self,
    members: MemberSelector | None = None,
) -> Iterator[tuple[ArchiveMember, ArchiveStream | None]]: ...
```

Yields `(member, stream)` in archive order with bounded memory. Solid blocks
decompress progressively (never buffered whole); peak = decoder working set + one
in-flight chunk. Non-file members yield `None`.

`members` is a selector (names/identities or predicate), not a transform. Streams
are lazy: unselected/unread members are not opened/decompressed and do not request
passwords. Yields the original mutable `ArchiveMember` so late-bound fields stay
visible.

Symlink targets stored as member data (ZIP, 7z, RAR3/4) are the one exception, and only
while `read_link_targets` is `True` (see "Link targets stored as member data are read
only when configured"). A pass that finalizes then reads every such target, selected or
not, so the complete report matches random access. That read MAY decompress data the
caller did not select and MAY consult the password provider. On 7z it decodes the link's
folder up to the link, within the budget of `format-7z` "A 7z folder is decoded at most
once for its link targets". With `read_link_targets=False` the promise holds without
exception.

Yielded streams are iterator-owned and valid only until advance: the iterator SHALL
close/invalidate the previous stream before the next yield. MUST NOT retain a
growing decompressed-block cache until reader close. On solid archives, random
`open()` may re-decode from block start; the cost is silent, and callers are
directed to `stream_members()` by `reader.cost.access_cost` and by the `open()` /
`read()` docstrings rather than by a runtime warning.

A `stream_members()` invocation is an exclusive one-pass/data-path operation in
both modes. It SHALL NOT overlap random `open()`, materialization, another
iteration/data pass, unrelated extraction, or reader close. An `extract_all()`
owner MAY invoke it as a child pass and MAY read/close the yielded child stream.
Unrelated overlap SHALL raise `ArchiveyUsageError` at the later op and leave the
active pass/stream valid. (Unlike random `open()`, whose independently owned
streams may coexist when `CONCURRENT` is declared — see `reader-concurrency`.)

#### Scenario: stream_members matrix

| Case | Expected |
| --- | --- |
| Yielded file stream emits diagnostic before advance | Stream + reader snapshots share one retained occurrence |
| Selector excludes member / stream unread | No open/decompress; no data-path diagnostic. With `read_link_targets=True`, a data-stored symlink target is still read at finalization (see the exception above) |
| Solid archive | Progressive decode; peak = decompressor state + one chunk |
| `stream_members(lambda m: m.name.endswith(".txt"))` | Only `.txt`; unselected never opened, except data-stored symlink targets when `read_link_targets=True`; original mutable members |
| Fully read stream, then inspect member | Late-bound fields (e.g. size/CRC) visible on same object |
| Advance after one yield | Prior stream closed/invalidated first |
| Random `open()` during active pass | `ArchiveyUsageError`; pass remains usable |
| Close/abandon partial generator | Current stream closed; pass ownership released once |
| Random `open()` into solid block | Re-decode from block start + skip; no diagnostic, no warning — discoverable via `reader.cost.access_cost` and the `open()` docstring |
| Unencrypted solid 7z, selector excludes a symlink, pass to the end (default config) | The link's target is resolved; its folder is decoded up to the link once |
| Encrypted solid 7z `[a.txt, link, b.txt]`, `read_link_targets=False`, `stream_members(lambda m: False)` to the end | Nothing decoded; provider never consulted; `link_target` unset; no `SYMLINK_TARGET_UNAVAILABLE` |

### Requirement: Transparent link following

`open()` / `read()` SHALL follow symlinks and hardlinks through shared reader
logic; `open()` SHALL keep returning `ArchiveStream` after following.

**Hardlinks (positional):** most recent matching target **strictly before** the
link (TAR/RAR5 model). Malformed later-only source: RA falls back to the later
member (extraction recovers — see `format-tar`); streaming cannot resolve forward
and fails per `OnError`. Modes SHALL agree on hardlink resolution for the same
archive.

**Symlinks:** RA → last matching target overall; streaming → latest seen so far
(forward stays `link_target_member is None`). Forward-visibility difference is
inherent to a single pass.

**Target-name resolution:** hardlink targets are archive-root relative and in
the member-name namespace: `.` and empty segments are dropped and `..` is
**retained**, exactly as `normalize_member_name` treats a name, so `a/../b` names
the member stored as `a/../b` and never the member `b`. Symlink targets are
filesystem paths: they join to the link's directory first and `..` is collapsed.
Absolute/`..`-escaping targets of either kind stay unresolved (`None`; open →
`LinkTargetNotFoundError`); the escape test runs on the collapsed form. Directory
lookup tries bare and `/`-suffixed forms.

Follow chains recursively; detect cycles by **member id** (not name); no arbitrary
depth limit. Missing target → `LinkTargetNotFoundError`; cycle → `ReadError`.
Terminal fully-dereferenced target (when known) is `member.link_target_member`
(see `archive-data-model`). Diagnostics for a linked open cover follow + read of
that one `open()` operation.

#### Scenario: link resolution matrix

| Case | Expected |
| --- | --- |
| Valid chain to file data | One `ArchiveStream`; diagnostics cover follow + read of that open |
| Hardlink → earlier file | Stream yields that file's data |
| Missing target | `LinkTargetNotFoundError` |
| Chain revisits member id | `ReadError` (cycle); no infinite recursion |
| Symlink → file in archive | Stream yields target file data |
| Symlink `dir/link` → `file` / `./file` | Lookup `dir/file`, not root-relative `file` |
| Hardlink → `a/../b`, archive holds both `a/../b` and `b` | Resolves to the `a/../b` member; never `b` |
| Symlink `dir/link` → `../file` | Lookup `file` |
| Absolute / `..`-escaping symlink | `link_target_member is None`; open → `LinkTargetNotFoundError` |
| Duplicate names, hardlink | Most recent occurrence strictly before the link |
| Duplicate names, symlink (RA) | Last occurrence overall |
| Hardlink source only later | RA falls back to later member; streaming cannot resolve |
| Two distinct same-named members on one chain | Not a cycle (id-based tracking) |

### Requirement: Context-manager and close lifecycle

The reader SHALL implement `__enter__` / `__exit__` / `close()`. `close()` SHALL
be idempotent.

**Caller observables:**

- Exiting `with open_archive(...)` closes the reader.
- After reader close, every new reader operation or property (including
  `__enter__`, iteration/listing/lookup, metadata/cost, `open`/`read`,
  `stream_members`, extraction) SHALL raise `ArchiveyUsageError`. Repeated
  `close()` / `__exit__` are no-ops.
- `close()` SHALL close every member stream still open on that reader, in the
  order they were opened, and SHALL do so only after the reader has actually
  transitioned to closed (so a `close()` that raises leaves streams untouched).
  A member stream SHALL NOT outlive its reader — reading one afterwards raises
  as it would for any closed stream. This matches `zipfile.ZipFile.close()` and
  `tarfile.TarFile.close()`.
- Each stream close releases that stream's lease, so backend teardown still runs
  once, after the last stream closes — the source is never torn down underneath
  a stream still reading through it.
- A member-stream close failure SHALL NOT prevent the remaining streams from
  being closed; a single failure propagates, several surface as a
  `BaseExceptionGroup`.
- Archivey SHALL never close a caller-supplied `BinaryIO`. If the caller closes
  it early, a later operation raises `ArchiveyUsageError` for the closed source;
  concurrent external close with I/O is unsupported.
- `__exit__` always calls `close()`. Close failure propagates on normal exit;
  during body-exception unwind the body exception remains via normal chaining.

**Under `MemberStreams.CONCURRENT`:** `reader.close()` drains in-flight worker
`open()`/`read()` before transitioning to closed (see `reader-concurrency`).
Without `CONCURRENT`, concurrent close with an actively executing worker call is
rejected.

Lease/token/teardown once-guards and dual-failure `ExceptionGroup` rules:
`reader-concurrency`.

#### Scenario: lifecycle matrix

| Case | Expected |
| --- | --- |
| Open stream, then close reader (no concurrent I/O) | New reader ops → `ArchiveyUsageError`; the stream is closed by that `close()`; backend released after it |
| Idle open stream + `reader.close()` | Close succeeds and closes the stream; a later read raises; `stream.close()` is a no-op |
| Several open streams + `reader.close()` | All are closed; teardown runs once, after the last |
| `close()` raises (active pass/worker) | Reader stays open; member streams untouched |
| Stream dropped without close | Finalizer reclaims it; the stream must not be kept alive by its own finalizer |
| Caller-supplied `BinaryIO`, all closed | Library does not call `close()` on that source |
| `open_archive()` context exits | Reader closed; any member stream still open is closed with it, then the backend is released |
| Op after reader close | `ArchiveyUsageError` |

### Requirement: Password candidates and provider

`password` SHALL accept a single `str | bytes`, an **ordered sequence**, and/or a
**provider** `PasswordProvider = Callable[[PasswordRequest], str | bytes | None]`:

```python
@dataclass(frozen=True)
class PasswordRequest:
    member: ArchiveMember | None  # None for archive-level (header) decryption
    attempt: int                  # 1 on first ask for this unit; increments on each later ask
```

Per encrypted unit (member / 7z folder / archive header), try in order: per-archive
**known-good** list (successes this open, most recent first), then remaining sequence
candidates, then provider repeatedly until `None`. Successful passwords SHALL join
known-good for the rest of the operation so a provider is consulted once per *new*
password rather than once per member. A provider answer that already failed for the
unit SHALL NOT be decrypted again; the provider SHALL be asked again, with the next
`attempt`. A provider that gives an answer it already gave for the unit SHALL be
treated as having no more answers, the same as `None`. Exhaustion (or provider `None`) →
`EncryptionError`. No per-call password on `open()`/`read()`.

**Concurrent use (observable):** After materialization, workers MAY open
differently encrypted members concurrently; known-good promotions are shared;
provider callbacks are serialized, and a worker that needs the provider while
another worker's call runs waits for it. Reentry from inside a provider into a
password-requiring operation on the same reader raises `ArchiveyUsageError` where
it can be recognized; which reentry that is, and what a provider that blocks on a
helper thread gets, is in `reader-concurrency`.

#### Scenario: password matrix

| Case | Expected |
| --- | --- |
| `password=[pw_a, pw_b]`, members use different passwords, one streaming pass | Each unit matches; pass completes without RA |
| Provider + unknown password needed | Called with that member's `PasswordRequest`; success → known-good; later same-pw members skip provider |
| Provider password fails, consulted again | New request has incremented `attempt` |
| Provider answers with a password that already failed for this unit (known-good from an earlier unit, or a listed candidate) | Not decrypted again; provider asked again with incremented `attempt` |
| Provider gives the same answer twice for one unit | Treated as `None`: `EncryptionError` for that unit, no second decrypt |
| Provider returns `None` | `EncryptionError` for that unit |
| Header-encrypted archive, provider only | Request with `member is None` |
| Concurrent opens of different encrypted units (post-materialization) | Each decrypts correctly; promotions shared without races |
| Two workers need the provider at once | Second waits for the first call; neither raises |
| Provider starts another password op on same reader, same thread | Nested op → `ArchiveyUsageError` |

### Requirement: Confirm candidates when a weak check permits retries

When a format's password check can admit wrong values, a candidate SHALL NOT be
accepted or added to known-good on that weak check alone if another distinct
candidate may be tried.

**Confirmation is a rejection filter, not a proof.** Its job is to stop a wrong candidate
from shadowing a correct one; the authoritative digest still runs on the caller's own
stream. Confirmation SHALL therefore be bounded, and SHALL obey "Bounded implicit
temporary storage" — no plaintext buffering proportional to unit size, and no decode work
proportional to unit size where a cheaper signal decides the same question.

A confirmation step SHALL yield one of three verdicts:

| Verdict | Meaning |
| --- | --- |
| `REJECTED` | this candidate is wrong; try the next |
| `CONFIRMED` | a signal of at least 2⁻³² strength matched; accept |
| `INCONCLUSIVE` | the candidate survived its budget without reaching a deciding signal |

The budget has **two** halves, and a spec that names only the first does not bound the
work. The plaintext half is `PASSWORD_CONFIRM_PREFIX_BYTES` (64 KiB of decoded output): not one
byte, and not `DetectionBudget.max_prefix_bytes`, since confirm measures decompressed
output where detection measures source peeks. The compressed half is a cap on how much
input may be consumed to produce that output, and it SHALL be stated too.

A block-transform codec emits nothing until it has consumed a whole block, so an
output-only bound does not constrain it: decoding 64 KiB of output from a bzip2 stream
consumes 905 KB of input (13.8× the bound), against 1.1× for LZMA2. The inner-TAR content
probe already carries a cap sized for exactly this (`_INNER_TAR_MAX_PROBE_BYTES`, 1 MiB,
chosen for bzip2's worst-case block), and confirm SHALL use a bound of that shape rather
than re-derive one.

Backends SHALL apply the strongest signal reachable, in this order:

1. **Cheap key check** — an O(1) test that confirms the *key* without decoding payload
   data. Confirms only at ≥ 2⁻³²; SHALL NOT reject on its own where a format permits
   writers to vary the bytes it inspects.
2. **Integrity anchor** — a stored checksum over a decodable prefix of the unit. The
   **earliest sufficient** anchor SHALL be used, and a plan SHALL stop once checksum-verified
   bytes reach 4; a shorter verified prefix carries less than 32 bits and SHALL NOT
   terminate the plan on its own. If that anchor sits **inside** the budget, decode to
   it and stop. If it sits **past** the budget: a chain with a rejecting codec SHALL NOT
   walk it (rung 3 settles a wrong key without reading the unit); a chain with no
   rejecting codec SHALL walk it anyway (nothing else can tell a wrong key from a
   right one).
3. **Codec rejection** — a chain *rejects* iff it contains a decompressor measured to
   fail on random input. Surviving a bounded prefix yields `INCONCLUSIVE`, not
   `CONFIRMED`. Filters (Delta, BCJ) never reject. A codec not yet measured is treated
   as non-rejecting until a test says otherwise.

**When no digest exists anywhere in the unit**, the backend SHALL NOT invent an unbounded
decode to discover that. A rejecting chain uses the prefix (`INCONCLUSIVE` on survive); a
non-rejecting chain stops at the budget and the caller's read-time digest is
authoritative. Candidate ambiguity does not change this: an unbounded multi-candidate pass
exists to find whose output matches a stored checksum, and there is none to match.

**When a digest exists but sits past the budget**, rung 2 governs, and the unbounded pass a
non-rejecting chain runs there is per candidate when the set is ambiguous.

A `CONFIRMED` candidate SHALL be added to known-good. An `INCONCLUSIVE` candidate
SHALL be added to known-good only when the candidate set is unambiguous. The
candidate loop remains `_PasswordCandidates.attempt`; confirmation supplies the
probe. A second driver SHALL NOT be introduced.

Accepting an `INCONCLUSIVE` candidate SHALL emit `ENCRYPTED_MEMBER_UNVERIFIED` if the
caller then abandons the member's stream before its declared digest is reached.

After confirmation, backend MAY re-open/re-decode the accepted candidate for the
caller's stream. Returned stream SHALL keep ordinary read-time integrity checking.

"Another candidate may be tried" includes ≥2 distinct known-good/static values and
a provider that can return another answer. Provider stays lazy (no advance
enumeration). Duplicates are not distinct. Provider-raised `EncryptionError` is
provider failure — propagate unchanged, not as candidate exhaustion.

If confirmation fails and candidates are exhausted, report the irreducible
ambiguity (wrong password **or** corrupt unit). MAY use `EncryptionError`. SHALL
NOT return an unvalidated candidate. A single distinct static candidate MAY keep
the format's normal lazy streaming path.

#### Scenario: weak-check confirmation matrix

| Case | Expected |
| --- | --- |
| Wrong candidate passes weak check first of two | Reject via confirmation; stream from correct candidate |
| Large member, many candidates | Confirmation bounded — not proportional to member size |
| Provider answer fails confirmation | Request next answer without pre-enumerating; accept only after confirm |
| Anchor reachable within budget | Decode to the anchor only, never past it |
| Unit carries both an early per-item checksum and a whole-unit checksum | The earlier one decides |
| Checksum-verified prefix shorter than 4 bytes | Plan continues to the next anchor |
| Rejecting codec, CRC past budget | Prefix only; wrong key `REJECTED`; survivor `INCONCLUSIVE` |
| Block-transform codec (bzip2), 64 KiB output requested | Input consumed stays within the compressed cap, not 64 KiB |
| Non-rejecting codec, CRC past budget | Walk to the CRC |
| Rejecting codec, no digest at all | Prefix; survivor `INCONCLUSIVE`; no unbounded decode |
| Non-rejecting codec, no digest, one distinct candidate | Stop at budget; caller's digest is authoritative |
| Non-rejecting codec, no digest, several candidates | First candidate; `DIGEST_UNVERIFIABLE`; no unbounded decode |
| Non-rejecting codec, CRC at end, several candidates | Unbounded pass; the matching checksum wins |
| Cheap key check matches for one candidate at full strength | `CONFIRMED` with no payload decode |
| Cheap key check matches no candidate | Ladder continues with every candidate; no candidate dropped |
| `INCONCLUSIVE` accepted, another candidate may still be tried | Not added to known-good |
| Confirmation fails, then provider raises `EncryptionError` | Provider exception propagates unchanged |
| All candidates fail confirmation | Ambiguity message; no candidate bytes returned |
| One distinct static value (incl. duplicates) | No eager consume for disambiguation; ordinary read-time errors |

### Requirement: Bounded implicit temporary storage

Reader ops SHALL NOT consume memory or temp storage proportional to member/archive
size as an implicit side effect of open/read/validate/password-confirm. Silently
spooling plaintext to a temp file is forbidden. A per-format strategy that
inherently needs proportional temp storage (e.g. `format-rar`'s documented copy of
a non-path archive source to disk, so `unrar` can seek it) is allowed only when
declared in that format's capability spec. Caller's own buffering of a returned
stream is unrestricted.

#### Scenario: bounded storage matrix

| Case | Expected |
| --- | --- |
| Encrypted member, many candidates | Confirmation temp use bounded by a constant |
| Backend can only serve via materialization | Strategy declared in format spec, not adopted silently |

### Requirement: Explicit configuration object

The system SHALL define these complete frozen schemas:

```python
@dataclass(frozen=True)
class ExtractionLimits:
    max_extracted_bytes: int | None = 2 * 2**30
    max_ratio: float | None = 1000.0
    ratio_activation_threshold: int = 5 * 2**20
    max_entries: int | None = 1_048_576
    UNLIMITED: ClassVar["ExtractionLimits"]

@dataclass(frozen=True)
class ListingLimits:
    max_members: int | None = 1_048_576
    max_metadata_bytes: int | None = 64 * 2**20
    UNLIMITED: ClassVar["ListingLimits"]

@dataclass(frozen=True)
class DecoderLimits:
    max_decoder_memory: int | None = 2 * 2**30
    max_key_derivation_rounds: int | None = 2**27
    max_ppmd_in_process_input: int | None = 16 * 2**20
    UNLIMITED: ClassVar["DecoderLimits"]

@dataclass(frozen=True)
class ArchiveyConfig:
    use_rapidgzip: AcceleratorMode = AcceleratorMode.AUTO
    use_indexed_bzip2: AcceleratorMode = AcceleratorMode.AUTO
    zip_unflagged_fallback_encoding: str = "cp437"
    rar_allow_glob_member_concatenation: bool = False
    read_link_targets: bool = True
    extraction_limits: ExtractionLimits = ExtractionLimits()
    listing_limits: ListingLimits = ListingLimits()
    decoder_limits: DecoderLimits = DecoderLimits()
    detection_budget: DetectionBudget = BALANCED_BUDGET
    diagnostic_policy: DiagnosticPolicy = DiagnosticPolicy()
    max_retained_diagnostic_references: int = 256
    on_diagnostic: Callable[[Diagnostic], None] | None = None
```

`max_retained_diagnostic_references` SHALL be non-negative. Policy/default/override
mappings and the dataclasses SHALL be defensively immutable. `config=None` →
immutable library default. No mutable global/context-local diagnostic policy or
callback.

A reader carries its open config, all of it, for its lifetime. Reader methods
SHALL NOT take a `config=`: `extract_all(limits=...)` is the one per-call
override, and it replaces only the extraction limits for that call.
`decoder_limits` SHALL bound the working memory a codec allocates on the
strength of a number the archive declares, and SHALL be enforced before that
allocation is made. `max_key_derivation_rounds` SHALL bound the total
password-to-key hashing rounds one reader runs, counted as the archive declares
them (RAR5 `2**kdf_count` PBKDF2 rounds plus the `+16`/`+32` offsets, 7z
`2**NumCyclesPower`, RAR3 its fixed `2**18`), summed over the derivations that
actually run: a key the reader already derived for the same password, salt and
cost SHALL cost nothing, and every candidate password tried SHALL count. The
check SHALL run before the derivation that would cross the cap, and SHALL raise
`ResourceLimitError`, which SHALL NOT be treated as a wrong password by
candidate iteration. `max_ppmd_in_process_input` SHALL bound the compressed bytes of
one PPMd member the process holds to decode it in-process; a larger member SHALL decode
in a child process, where a crash of the native decoder SHALL surface as
`CorruptionError`, and where no child process can be started it SHALL raise
`ResourceLimitError`. `None` SHALL decode every member in-process. Per-call `limits`
still beat `config.extraction_limits`, then reader/library default. Other
per-call operational args stay outside `ArchiveyConfig`.
`detection_budget` SHALL bound what format detection spends, for `detect_format` and for
every detection `open_archive` and `open_stream` run (see `detection-cost`): the
auto-detection itself, and under `format=` the stub-volume check and the rescan that
confirms an empty listing. It is annotated as a `DetectionBudget`, like the accelerator
fields beside it: a preset member or its name is converted at construction, so the field
always holds a budget.
`read_link_targets` SHALL decide whether the reader reads, on its own, a symlink target
the format stores as member data (see "Link targets stored as member data are read only
when configured"); like `listing_limits`, it holds for the reader's lifetime.

`on_diagnostic` runs synchronously after count/retention/logging updates. Snapshot
reads from a callback are allowed. Starting another operation on the same
emitting reader/stream SHALL be rejected: the reader's operation gate raises
`ArchiveyUsageError`, and a re-entrant call that gets as far as emitting a diagnostic
of its own raises `UnsupportedOperationError` from the collector; other readers OK.
Callbacks hold no Archivey collector/reader/stream/backend/registry lock
(`diagnostics` / `reader-concurrency`).

#### Scenario: config matrix

| Case | Expected |
| --- | --- |
| `ArchiveyConfig()` | AUTO accelerators; documented extraction and listing defaults; COLLECT; budget 256; no callback |
| `extract(..., extraction_limits=ExtractionLimits(max_ratio=100))` | 100:1 per-member ratio enforced (`safe-extraction`) |
| Reader opened with `listing_limits=ListingLimits(max_members=10)` | Listing caps stay at 10 for the reader lifetime; `extract_all()` has no `config=` to change them |
| Reader opened with `read_link_targets=False` | No data-stored link target is read by listing or a pass for the reader lifetime |
| Header-encrypted RAR5 set of four parts, one encryption record repeated, `max_key_derivation_rounds` one round short of key + PswCheck | `ResourceLimitError` at `open_archive`; at exactly key + PswCheck the set lists |
| 7z PPMd member of 200 KB compressed, `max_ppmd_in_process_input=1024`, no child process possible | `ResourceLimitError` on the first read |
| Password list `["wrong", right]`, budget covering only the right candidate's derivations | `ResourceLimitError`, not `EncryptionError`; the list does not continue |

### Requirement: Reader-lifetime cumulative diagnostic snapshots

Every successfully created `ArchiveReader` SHALL expose:

```python
@property
def diagnostics(self) -> DiagnosticSummary: ...
```

Each access SHALL return a fresh immutable cumulative snapshot. Counts SHALL
include automatic-detection events that led to this reader (if any) plus every
subsequent open/list/read/stream/extract event it owns. Previously returned
snapshots SHALL not change. A stream returned by the reader SHALL expose an
operation-filtered `diagnostics` view of the same lifetime — not a separately
retained copy of the aggregate.

Value shape, retention budget, watermarks, and attachment rules: `diagnostics`.

#### Scenario: diagnostics matrix

| Case | Expected |
| --- | --- |
| Detection conflict + scan + rewind diagnostics | Later `reader.diagnostics` has exact cumulative counts in emission order; earlier snapshot unchanged |
| Two streams emit different diagnostics | Each stream sees only its op; reader sees both |
| Callback reads `diagnostics` then `reader.read(...)` | Snapshot OK (incl. current event); reentry → `ArchiveyUsageError` naming the callback |

### Requirement: Collection form of MemberSelector

`MemberSelector` SHALL accept a predicate or `Collection[str | ArchiveMember]`,
normalized to a predicate at the API boundary:

- `str` matches **every** member with that normalized name (duplicates all match;
  extraction keeps sequential last-wins-on-disk)
- A `str` entry SHALL match the stored name exactly. A directory member's normalized
  name carries a trailing `/`, so `"dir"` does not select `dir/`. `get(name)` and
  `open(name)` match the same way. The missing `/` is reported like any other
  unmatched entry.
- `ArchiveMember` matches by **identity** (`archive_id` + `member_id`; members are
  unhashable → id set, never member set)
- String and member entries MAY mix

A collection entry that matches no member SHALL be reported as
`MEMBER_SELECTOR_UNMATCHED` (`diagnostics`), once for each entry, after every member
has been offered to the selector:

- `stream_members()` reports at the end of a pass that reached the last member. A pass
  that the caller stops early SHALL NOT report, because a later member could match.
- `extract_all()` reports before it writes any member when the member list is available
  without a scan, and otherwise at the end of the pass. Under a `RAISE` disposition the
  first case refuses the call before the destination is created. In the second case
  the members already written stay on disk and `DiagnosticRaisedError` replaces the
  report.
- Under a `RAISE` disposition, `stream_members()` yields every selected member and
  then raises `DiagnosticRaisedError` from the iterator.
- A predicate selector SHALL NOT be reported.

#### Scenario: selector matrix

| Case | Expected |
| --- | --- |
| `stream_members(members=["a.txt"])` with two `a.txt` | Both yielded, archive order |
| Specific `ArchiveMember` among duplicates | Only that identity |
| `members=["dir"]` on an archive holding `dir/` | Nothing selected; `MEMBER_SELECTOR_UNMATCHED` for `dir`, naming `dir/` |
| `members=["x/"]` on an archive holding only the file `x` | Nothing selected; one `MEMBER_SELECTOR_UNMATCHED` for `x/` |
| `members=["a.txt", "typo.txt", "typo.txt"]`, pass to the end | `a.txt` selected; one `MEMBER_SELECTOR_UNMATCHED` for `typo.txt` |
| Same selector, caller breaks after the first member | No `MEMBER_SELECTOR_UNMATCHED` |
| `ArchiveMember` from another reader | Nothing selected; `MEMBER_SELECTOR_UNMATCHED` with `entry_kind="member"` |
| `extract_all(members=["typo.txt"])` on ZIP with `MEMBER_SELECTOR_UNMATCHED` set to `RAISE` | `DiagnosticRaisedError` before any member is written |
| `extract_all(members=["a.txt", "typo.txt"])` on TAR with the code set to `RAISE` | `a.txt` written, then `DiagnosticRaisedError`; no report |

### Requirement: Honour detection payload_offset at open

When `detect_format` returns `payload_offset > 0`, `open_archive` (auto-detect
path) SHALL open the archive at that byte offset by either (a) passing an
explicit start-offset argument into `backend.open_read`, or (b) handing the
backend a bounded offset view / slice whose byte 0 *is* the payload. A bare
`seek` on a shared handle is **not** sufficient: backends that perform absolute
seeks (notably 7z’s `read_signature_and_next_header`, which `seek(0)`s then
seeks to `_SIGNATURE_HEADER_SIZE + next_header_offset`) discard caller
positioning. The system SHALL NOT copy the remainder of the source to a
temporary file solely to strip an SFX stub.

An explicit `format=` that bypasses detection retains each backend’s own
start-offset / SFX rules (`format-rar`, `format-7z`).

#### Scenario: payload_offset hand-off

| Case | Expected |
| --- | --- |
| Auto-detect SFX RAR/7z/ZIP with `payload_offset == N` | Backend opens via start-offset arg or offset view; real members listed |
| `payload_offset == 0` | Unchanged open-at-current-position behaviour |
| Explicit `format=` | Detection skipped; backend SFX/start-offset rules apply |
| Bare seek only (no start-offset / no offset view) | Insufficient for 7z; MUST NOT be the sole hand-off mechanism |

### Requirement: Bounded symlink-target reads from member data

A backend that reads a symlink's target from the member's data (ZIP, 7z, RAR3/4)
SHALL bound that read. For a plain target, a member whose declared size is over
`MAX_LINK_TARGET_BYTES` (4096) SHALL NOT be opened for its target at all, and any other
read SHALL ask for at most 4096 + 1 bytes. A Windows reparse buffer is bounded
differently, by its own header, as the paragraph after next says.

A target longer than 4096 bytes SHALL be treated as corrupt or malicious: `link_target`
SHALL stay unset, and `SYMLINK_TARGET_UNAVAILABLE` SHALL be emitted with
`reason="target_too_long"`. The target SHALL NOT be truncated. The member keeps its
link type, and since the archive does record a target, extraction SHALL fail that
member (`LinkTargetNotFoundError`) rather than report `LINK_TARGET_UNAVAILABLE`.
`SYMLINK_TARGET_UNAVAILABLE` is in `ARCHIVE_INTEGRITY_CODES`, so
`DiagnosticPolicy.strict()` refuses the archive.

A Windows reparse buffer stored as member data SHALL be read as far as its own header
declares: the 8-byte header, then the payload length its 16-bit `ReparseDataLength`
states (plus one byte, so a member that is exactly one buffer reaches end of stream and
is verified). A header whose tag is not a symlink or a junction declares nothing to
read. The buffer SHALL NOT be refused for its size, because a member flagged as a
reparse point may turn out to hold ordinary file content. The target parsed from a
buffer SHALL be held to the same 4096-byte cap, measured in UTF-8.

A member whose data outruns a declared size under the cap is corrupt, not over-long:
where the backend verifies data against the declared size (ZIP always, 7z whenever
checksums are verified), the read SHALL fail with `CorruptionError` on reaching that
size, as for any other member, and the cap does not decide the outcome.

Targets stored in a header (TAR `linkname`, RAR5 redirection records, Rock Ridge) are
outside this requirement: the header parser has already allocated them, and
§"Listing metadata-byte accounting" weighs them at registration.

#### Scenario: symlink-target cap matrix

| Case | Expected |
| --- | --- |
| Data-stored target of exactly 4096 bytes | `link_target` set, no diagnostic |
| Data-stored target of 4097 bytes | `link_target is None`; `SYMLINK_TARGET_UNAVAILABLE`, `reason="target_too_long"` |
| Compressed target declaring 64 MiB | Refused without decoding any of it |
| ZIP target whose data outruns a declared size under the cap | `CorruptionError` naming the declared size; nothing past it decoded |
| No declared size, data longer than the cap | Read stops at 4097 bytes; refused as over-long |
| Over-long target under `DiagnosticPolicy.strict()` | Listing raises `DiagnosticRaisedError` |
| Over-long target, `extract_all(on_error=CONTINUE)` | That link `FAILED` with `LinkTargetNotFoundError`; other members extract |
| Reparse buffer whose target is over 4096 UTF-8 bytes | Refused as over-long |
| Reparse buffer followed by more data | Only the declared buffer and one byte are read |
| Reparse-flagged member whose data is not a buffer, any size | Re-typed to its fallback with all its content readable; only its header read while listing |

### Requirement: Each member is listed once per reader

A reader SHALL build one `ArchiveMember` object per archive member and hand out that same
object from every listing method and pass: `members_report_if_available()`,
`members_report()`, `members()`, `scan_members()`, `get()`, `__iter__`,
`stream_members()` and `extract_all()`. Each member SHALL be registered (its
`member_id` and `archive_id` stamped, its presentation checks run and its listing-limit
accounting done) exactly once, before any of those methods returns or yields it.
Typing-time and presentation diagnostics for a member SHALL therefore be emitted once
per reader, whichever methods are called and in whatever order, so
`DiagnosticSummary.counts` stays exact. A typing-time diagnostic's context SHALL carry,
as `member_id`, the id the member is registered with, on every backend.

A backend's member walk SHALL run at most once per reader when it completes. A walk that
fails before completing, without terminal archive damage, MAY be repeated on the next
call only in random-access mode, and only when none of its members was handed out.
A repeated walk SHALL replay the typing-time diagnostics of the positions the failed walk
reached rather than emit them again. Until the walk ends it SHALL keep, for a member it
has built, only the codes of those diagnostics, and a full `Diagnostic` only for the
member being typed. That record is not a library-retained reference and uses none of the
`max_retained_diagnostic_references` budget. A streaming walk, which is never repeated,
SHALL keep none.

#### Scenario: listing identity matrix

| Case | Expected |
| --- | --- |
| `members_report_if_available()` then `members()` (ZIP, ISO, 7z, RAR) | Same objects, in the same order |
| `extract_all()` on an upfront-index archive | The backend walks its index once |
| `stream_members()` on a fresh 7z or solid RAR (either mode) | Every yielded member has `member_id` set; `member in reader` is true |
| ZIP or ISO member whose name is normalized, listed by `extract_all()` | `MEMBER_NAME_NORMALIZED` counted once for that member |
| Member name with a bidi control, peeked then materialized | `MEMBER_NAME_BIDI_CONTROL` counted once and attached to the object the caller holds |
| Typing-time diagnostic on ZIP, 7z, RAR, ISO | Context `member_id` equals the member's `member_id` |
| Random-access walk interrupted by `ResourceLimitError` / `KeyboardInterrupt`, then retried | Retry walks again; ids and objects are consistent with a single walk |
| Streaming pass whose walk fails without terminal damage | Later listing calls raise; the partial prefix is never published as complete |

### Requirement: Last-entry-wins is stamped once, when the member walk ends

Last-entry-wins `is_current` SHALL be stamped once per reader, when the member walk ends.
The walk ends when it completes or when it stops on terminal archive damage
(`CorruptionError` / `TruncatedError`). On terminal damage the stamp SHALL cover the
recovered prefix that the incomplete report holds. Every member yielded or returned after
that point SHALL carry its final value.

#### Scenario: is_current stamping matrix

| Case | Expected |
| --- | --- |
| Streaming `extract_all()` over a ZIP holding `a.txt` twice | `SUPERSEDED`, then `EXTRACTED`, as in random access (today it raises `ExtractionError`) |
| Streaming pass over a truncated TAR holding `a.txt` twice before the damage, then `members_report()` | Incomplete report; the first `a.txt` reads `is_current=False` |

### Requirement: A streaming pass finalizes on its own cursor

Completing the member walk SHALL NOT by itself finalize a streaming pass. The pass
finalizes when its own consumer passes the last member. A peek that drains the walk
followed by an abandoned pass SHALL run no link finalization: it resolves no links and
publishes no complete report. On backends that read link targets only at finalization
(ZIP, ISO), such a pass reads no link data. A 7z pass reads a link member's data as it
passes that member, so an abandoned 7z pass may already have read link data for the
members it passed.

#### Scenario: pass finalization matrix

| Case | Expected |
| --- | --- |
| Streaming ZIP: peek inside the `stream_members()` loop, then `break` | No complete report published; no link-target reads |

### Requirement: Link targets stored as member data are read only when configured

`ArchiveyConfig` SHALL carry `read_link_targets: bool = True`. Like `listing_limits`, it
is fixed for the reader's lifetime: the reader's own config is the only one it reads.
It governs every symlink whose target the format stores as member data rather than in
the header (ZIP, 7z, RAR3/4), in both access modes.

- `True`: the reader reads such a target the way the format reads member data. On ZIP and
  7z this includes decompression and the full password sequence, provider included. A
  target it cannot read stays unset with `SYMLINK_TARGET_UNAVAILABLE`. RAR3/4 reads only
  stored, unencrypted, single-volume bytes, as before.
  When `extract_all` accepts a link whose target is still unread (a streaming pass
  reaches a ZIP or 7z link before the pass finalizes), it SHALL read that target before
  writing the link, as under `False` below, rather than failing it as a link with no
  target.
- `False`: the reader SHALL NOT read member data for a link target as a side effect of
  listing (the peek, `members()`, `scan_members()`, `members_report()`, `get()`,
  `__iter__`) or of a pass advancing (`stream_members()`, including the child pass
  `extract_all` drives). Such a link keeps `link_target=None`, no
  `SYMLINK_TARGET_UNAVAILABLE` is emitted for it, and the skipped read is not recorded as
  an attempt. This covers RAR3/4 stored targets too, although that read needs no
  decompression or password. Under `False`, a `link_target` set by listing then means
  exactly that the header carries it, whatever the compression method.
  Header-carried targets (RAR5, TAR, ISO) are unaffected.
- Under `False` the reader SHALL read a link's target only when the caller asks for that
  member:
  - `extract_all` SHALL call its `members` selector and its `filter` on the link, with
    `link_target=None`, before reading the target, and SHALL read it only for a link both
    accept. A target it cannot read fails that member as one whose target the archive
    carries but the reader cannot reach, under `OnError`.
  - `open()` / `read()` on a link SHALL follow it as "Transparent link following"
    requires, reading its target first.

  Such a read SHALL fill `link_target` in place on the member, like any late-bound field,
  and a filled target SHALL NOT be read again. A report taken afterwards therefore shows
  targets for the links read this way and `None` for the rest. `False` is a promise about
  what the reader reads on its own, not about what a member ends up holding.
- Under either setting, a read made for extraction can show that the member is not a
  link: a reparse-flagged member whose data is no reparse buffer, which listing would
  have re-typed to a file. `extract_all` SHALL then re-type it the same way, call its
  `filter` again on the re-typed member, and write it as a file. In random access it
  opens the member for its content. A streaming pass has already passed that content,
  so it SHALL fail the member under `OnError`; it SHALL NOT report it as a link with no
  target.

#### Scenario: link-target setting matrix

| Case | Expected |
| --- | --- |
| ZIP with an encrypted symlink, default config, no password, provider supplied | Provider consulted; on failure `link_target` unset with `SYMLINK_TARGET_UNAVAILABLE` |
| Same archive, `read_link_targets=False`, `members()` | No member data read; provider not consulted; `link_target` unset; no diagnostic |
| Same archive, `read_link_targets=False`, password supplied, `extract_all()` | The filter sees the link with `link_target=None`, then the target is read and the link is written |
| Same archive, `read_link_targets=False`, filter rejects members with `link_target is None` | The target is never read; provider not consulted; the link is not written |
| Same archive, `read_link_targets=False`, no password, `extract_all()` | The symlink member fails under `OnError`, as a locked target |
| RAR5 symlink, `read_link_targets=False` | `link_target` set from the header |
| RAR4 stored symlink, `read_link_targets=False`, `members()` | `link_target=None`; no member data read |
| ZIP with two symlinks, `read_link_targets=False`, `extract_all(members=["link-a"])`, then `members()` | `link-a` has its target; `link-b` has `link_target=None` |
| ZIP symlink, `read_link_targets=False`, password supplied, `reader.open("link")` | The target is read, then the link is followed |
| Streaming `extract_all()` over a ZIP symlink, default config | The target is read before the link is written; the link is extracted |
| ZIP member flagged as a reparse point whose data is no reparse buffer, `read_link_targets=False`, `extract_all()` | The filter sees it as a link, then again as a file; random access writes its content; a streaming pass fails it under `OnError` |
