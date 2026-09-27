# archive-reading — fewer exception types

## MODIFIED Requirements

### Requirement: Declared member-stream capabilities

`open_archive()` SHALL accept two keyword-only booleans, both defaulting to `False`:

- `concurrent_members=True` — any number of member streams may be open simultaneously
  (full contract: `reader-concurrency`)
- `seekable_members=True` — every member stream from random `open()` is seekable;
  `stream_members()` yields stay forward-only (below)

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
SHALL raise `ArchiveyUsageError` at the later call and leave the first stream
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
start (loud-slow-rewind).

A `stream_members()` handle SHALL report `seekable() is False` and its `seek()` SHALL
raise `io.UnsupportedOperation`, regardless of `seekable_members` and of `streaming`,
on every format including directory and single-file; `tell()` SHALL work. The pass is a
single-pass decode and owns the position: a seek would decode again behind the
iterator's back, and on a solid or piped member it cannot be done at all. The rule is
uniform so that callers cannot come to rely on a handle that seeks on some formats
only. A caller that needs to seek opens the member with random `open()` under
`seekable_members=True`.

`ArchiveyUsageError`'s message SHALL name the parameter a caller would pass to
allow the operation (`concurrent_members=True`), not an internal type.

`open_archive()` SHALL capture the caller stack once; `ArchiveyUsageError`
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
| Overlapping second `open()` without `concurrent_members` (ZIP/TAR/ISO/single-file/dir) | `ArchiveyUsageError` at later `open()` with open_archive `file:line`; first stream remains readable |
| Refused second `open()` without `concurrent_members` | Raises before the member is opened — no member stream constructed, no helper process spawned, no member data read |
| Non-overlapping open/read/close loop, no capabilities declared | All opens succeed |
| Stream without `seekable_members` (incl. real directory file) | `seekable()` false; `seek()` → `io.UnsupportedOperation`; `tell()` + forward reads OK |
| Same member via random `open()` with `seekable_members=True` | `seekable()` true; backward seek rereads; loud-slow-rewind when there is no index/accelerator |
| `stream_members()` handle with `seekable_members=True`, `streaming=False` or `True`, file source, every format | `seekable()` false; `seek()` → `io.UnsupportedOperation`; `tell()` + forward reads OK |
| `extract_all()` with nothing declared | Completes; internal opens ungated |
| `open_archive(p, member_streams=...)` | `TypeError` — the parameter no longer exists |
| Seek before the start of a random `open()` stream with `seekable_members=True` | Relative (`SEEK_CUR` / `SEEK_END`) underflow clamps to 0, as `io.BytesIO`; negative `SEEK_SET` or unknown `whence` → `ValueError`, never a translated archive error. Directory member: relative underflow is the OS file's `OSError` |

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
`ArchiveyUsageError` uniformly. Only one forward pass
(`__iter__`/`stream_members` or one `extract_all`) is allowed, with
`scan_members()` / `members_report()` to finish/return it and
`members_report_if_available()` anytime.
Canonical access-mode × method table: `access-mode-and-cost`.

#### Scenario: iteration / access-mode matrix

| Method / action | `streaming=False` | `streaming=True` |
| --- | --- | --- |
| `__iter__` | Yields in order; after successful complete materialization, from cache; terminal archive error → yield prefix then raise | Single-use forward pass; terminal archive error → yield prefix then raise; second `__iter__`/`stream_members`/`extract_all` → `ArchiveyUsageError` |
| `members()` | Full scan if needed; complete list or raise (no partial return) | `ArchiveyUsageError` |
| `scan_members()` | Same fully-resolved list as `members()` when complete; raise on terminal archive error | Finishes/drains pass; complete list or raise; pass consumed |
| `members_report()` | Always returns `MemberListReport` (prefix + `error`) | Always returns report; may consume the pass |
| `scan_members()` after early `break` | n/a | Drains remainder; complete list or raise on terminal error |
| `members_report_if_available()` after completed **successful** pass | Complete report (`error is None`) if indexed/cached | Complete report (not `None`); forward-link finalization visible on yielded objects |
| `members_report_if_available()` after incomplete (error) pass already ran | Incomplete report (prefix + `error`) | Incomplete report (prefix + `error`) |
| `members_report_if_available()` after abandoned pass / before any materialization | `None` (unless upfront index) | `None` |
| `len(ar)` | `TypeError` | `TypeError` |
| `list(ar)` | Iterates (may raise after prefix) | Iterates (consumes the single pass; may raise after prefix) |

### Requirement: Name lookup and member identity

No `__getitem__` (duplicates break mapping; dunders are probed implicitly).
`open()`/`read()` accept a name and raise `KeyError` when absent.

```python
def get(self, name: str, default=None) -> ArchiveMember | None: ...
def __contains__(self, member: ArchiveMember) -> bool: ...  # identity, O(1), any mode
```

`get()` looks up by normalized name; duplicates → **last** (sequential extraction
winner). On `streaming=True` SHALL raise `ArchiveyUsageError` regardless of
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
| `get` on `streaming=True` | `ArchiveyUsageError` |
| `member in ar` (yielded by `ar`) | `True`; foreign member → `False`; no scan |
| `"file.txt" in ar` | `TypeError` → use `get()`; never iterate |

### Requirement: Bounded implicit temporary storage

Reader ops SHALL NOT consume memory or temp storage proportional to member/archive
size as an implicit side effect of open/read/validate/password-confirm. Silently
spooling plaintext to a temp file is forbidden. A per-format strategy that
inherently needs proportional temp storage (e.g. `format-rar`'s documented copy of
a non-path archive source to disk, so `unrar` can seek it) is allowed only when
declared in that format's capability spec, and a strategy that copies the archive
source SHALL be bounded by `ArchiveyConfig.spool_limits`. Caller's own buffering of a
returned stream is unrestricted.

#### Scenario: bounded storage matrix

| Case | Expected |
| --- | --- |
| Encrypted member, many candidates | Confirmation temp use bounded by a constant |
| Backend can only serve via materialization | Strategy declared in format spec, not adopted silently |
| Declared copy of the archive source | Bounded by `SpoolLimits.max_bytes`; over it, `ResourceLimitError` |

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
class SpoolLimits:
    max_bytes: int | None = 2**30
    UNLIMITED: ClassVar["SpoolLimits"]

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
    spool_limits: SpoolLimits = SpoolLimits()
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
in a child process, where a crash of the native decoder (a fault signal such as SIGSEGV,
or the Windows status for the same fault) SHALL surface as `CorruptionError`. A child
killed by SIGKILL, or one that dies allocating the member's model, SHALL raise
`ResourceLimitError`, as SHALL a larger member where no child process can be started. A
child that ends any other way (another signal, a plain exit status) SHALL raise
`ReadError`, which is not a verdict on the data. `None` SHALL decode every member
in-process. Per-call `limits`
still beat `config.extraction_limits`, then reader/library default. Other
per-call operational args stay outside `ArchiveyConfig`.
`detection_budget` SHALL bound what format detection spends, for `detect_format` and for
every detection `open_archive` and `open_stream` run (see `detection-cost`): the
auto-detection itself, and under `format=` the stub-volume check and the rescan that
confirms an empty listing. It is annotated as a `DetectionBudget`, like the accelerator
fields beside it: a preset member or its name is converted at construction, so the field
always holds a budget.
`spool_limits` SHALL bound the bytes one reader writes to temporary storage as a copy of
its source (today, `format-rar`'s copy of a stream source for `unrar`), totalled across a
volume set and across attempts: a copy refused once SHALL stay refused for that reader
without writing again. `None` SHALL disable the guard; `SpoolLimits.UNLIMITED` sets it to
`None`. A copy over the limit SHALL raise `ResourceLimitError`, naming `SpoolLimits.max_bytes`, before any byte is written when the
size is known, and otherwise before the written total passes the limit, with the partial
copy removed. A path source is not copied and SHALL NOT be refused by it.
`read_link_targets` SHALL decide whether the reader reads, on its own, a symlink target
the format stores as member data (see "Link targets stored as member data are read only
when configured"); like `listing_limits`, it holds for the reader's lifetime.

`on_diagnostic` runs synchronously after count/retention/logging updates. Snapshot
reads from a callback are allowed. Starting another operation on the same
emitting reader/stream SHALL be rejected: the reader's operation gate raises
`ArchiveyUsageError`, and a re-entrant call that gets as far as emitting a diagnostic
of its own raises `ArchiveyUsageError` from the collector; other readers OK.
Callbacks hold no Archivey collector/reader/stream/backend/registry lock
(`diagnostics` / `reader-concurrency`).

#### Scenario: config matrix

| Case | Expected |
| --- | --- |
| `ArchiveyConfig()` | AUTO accelerators; documented extraction, listing and spool defaults (spool 1 GiB); COLLECT; budget 256; no callback |
| `extract(..., extraction_limits=ExtractionLimits(max_ratio=100))` | 100:1 per-member ratio enforced (`safe-extraction`) |
| Reader opened with `listing_limits=ListingLimits(max_members=10)` | Listing caps stay at 10 for the reader lifetime; `extract_all()` has no `config=` to change them |
| Reader opened with `read_link_targets=False` | No data-stored link target is read by listing or a pass for the reader lifetime |
| Header-encrypted RAR5 set of four parts, one encryption record repeated, `max_key_derivation_rounds` one round short of key + PswCheck | `ResourceLimitError` at `open_archive`; at exactly key + PswCheck the set lists |
| 7z PPMd member of 200 KB compressed, `max_ppmd_in_process_input=1024`, no child process possible | `ResourceLimitError` on the first read |
| Same member on the child path; the child crashes / is killed by SIGKILL / by SIGTERM | `CorruptionError` / `ResourceLimitError` / `ReadError`, not `CorruptionError` |
| Password list `["wrong", right]`, budget covering only the right candidate's derivations | `ResourceLimitError`, not `EncryptionError`; the list does not continue |
