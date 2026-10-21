## MODIFIED Requirements

### Requirement: Explicit configuration object

The system SHALL define these complete frozen schemas:

```python
@dataclass(frozen=True)
class ExtractionLimits:
    max_extracted_bytes: int | None = 2 * 2**30
    max_ratio: float | None = 1000.0
    ratio_activation_threshold: int = 5 * 2**20
    max_entries: int | None = 262_144
    live_ratio_activation_threshold: int = 256 * 2**20
    UNLIMITED: ClassVar["ExtractionLimits"]

@dataclass(frozen=True)
class ListingLimits:
    max_members: int | None = 262_144
    max_metadata_bytes: int | None = 64 * 2**20
    UNLIMITED: ClassVar["ListingLimits"]

@dataclass(frozen=True)
class DecoderLimits:
    max_decoder_memory: int | None = 2 * 2**30
    max_key_derivation_rounds: int | None = 2**25
    max_ppmd_in_process_input: int | None = 16 * 2**20
    UNLIMITED: ClassVar["DecoderLimits"]

@dataclass(frozen=True)
class SpoolLimits:
    max_bytes: int | None = 2**30
    UNLIMITED: ClassVar["SpoolLimits"]

class ProcessIsolation(Enum):
    KNOWN_CRASHES = "known_crashes"
    NATIVE_DECODERS = "native_decoders"
    NONE = "none"

@dataclass(frozen=True)
class ArchiveyConfig:
    use_rapidgzip: AcceleratorMode = AcceleratorMode.AUTO
    use_indexed_bzip2: AcceleratorMode = AcceleratorMode.AUTO
    process_isolation: ProcessIsolation = ProcessIsolation.KNOWN_CRASHES
    zip_unflagged_fallback_encoding: str = "cp437"
    rar_allow_glob_member_concatenation: bool = False
    read_link_targets: bool = True
    extraction_limits: ExtractionLimits = ExtractionLimits()
    listing_limits: ListingLimits = ListingLimits()
    decoder_limits: DecoderLimits = DecoderLimits()
    spool_limits: SpoolLimits = SpoolLimits()
    detection_budget: DetectionBudget = BALANCED_BUDGET
    always_probe_content: bool = False
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
candidate iteration. Under `process_isolation=KNOWN_CRASHES` (the default) and
`NATIVE_DECODERS`, every PPMd member SHALL decode in a child process (`compressed-streams`,
"Native decoders run where process_isolation puts them"), where a crash of the native
decoder (a fault signal such as SIGSEGV, or the Windows status for the same fault) SHALL
surface as `CorruptionError`. A child killed by SIGKILL, or one that dies allocating the
member's model, SHALL raise `ResourceLimitError`, as SHALL a member where no child process
can be started. A child that ends any other way (another signal, a plain exit status)
SHALL raise `ReadError`, which is not a verdict on the data. `max_ppmd_in_process_input`
SHALL bound the compressed bytes of one PPMd member the process holds to hand to pyppmd
whole, in the child or, under `process_isolation=NONE`, in-process; a larger member, and
one whose size is known only once it has been read (a ZIP member with bit 3 set, read from
a non-seekable source), SHALL be fed to pyppmd in pieces. `None` SHALL hold every member
whose size is known.
`process_isolation` SHALL be converted from the member or its name at construction, as
the accelerator fields are. `process_isolation=NONE` together with `use_rapidgzip=ON` or
`use_indexed_bzip2=ON` SHALL raise `ArchiveyUsageError` at construction: rapidgzip runs
only in a child process.
Per-call `limits` still beat `config.extraction_limits`, then reader/library default.
Other per-call operational args stay outside `ArchiveyConfig`.
`detection_budget` SHALL bound what format detection spends, for `detect_format` and for
every detection `open_archive` and `open_stream` run (see `detection-cost`): the
auto-detection itself, and under `format=` the stub-volume check and the rescan that
confirms an empty listing. It is annotated as a `DetectionBudget`, like the accelerator
fields beside it: a preset member or its name is converted at construction, so the field
always holds a budget.
`always_probe_content` SHALL decide whether `detect_format` and `open_archive` run every
content probe or only the probe of the format the source's extension names
(`format-detection`); `open_stream` SHALL run every probe whatever it holds. Like the other
switches it SHALL be a real `bool`.
`spool_limits` SHALL bound the bytes one reader writes to temporary storage as a copy of
its source (today, `format-rar`'s copy of a stream source for `unrar`), totalled across a
volume set and across attempts: a copy refused once SHALL stay refused for that reader
without writing again. The same limit SHALL bound the decoded member data a declared
strategy keeps on disk (today, `format-rar`'s kept file-copy sources in a solid pass),
counted while the data is on disk: kept data SHALL count from its first written byte
until it is deleted, and SHALL NOT count after that. A keep over the limit SHALL be
declined, not refused: the read falls back to decoding again. `None` SHALL disable the
guard; `SpoolLimits.UNLIMITED` sets it to `None`. A copy over the limit SHALL raise
`ResourceLimitError`, naming `SpoolLimits.max_bytes`, before any byte is written when the
size is known, and otherwise before the written total passes the limit, with the partial
copy removed. A path source that is read in place is not copied and SHALL NOT be refused
by it; a path source that has to be copied (`format-rar`: a prefixed archive read with
`unar`, or explicit volume files that cannot be linked side by side) is bounded like a
stream source.
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
| `open_archive(..., config=ArchiveyConfig(extraction_limits=ExtractionLimits(max_ratio=100)))` then `extract_all(dest)` | 100:1 per-member ratio enforced (`safe-extraction`) |
| Reader opened with `listing_limits=ListingLimits(max_members=10)` | Listing caps stay at 10 for the reader lifetime; `extract_all()` has no `config=` to change them |
| `detect_format(BytesIO(zlib_bytes))`, then with `always_probe_content=True` | `FormatDetectionError`, then `ZLIB` / `content_probe` |
| Reader opened with `read_link_targets=False` | No data-stored link target is read by listing or a pass for the reader lifetime |
| Header-encrypted RAR5 set of four parts, one encryption record repeated, `max_key_derivation_rounds` one round short of key + PswCheck | `ResourceLimitError` at `open_archive`; at exactly key + PswCheck the set lists |
| 7z PPMd member of 200 KB compressed, no child process possible | `ResourceLimitError` on the first read, naming `process_isolation` |
| Same member; the child crashes / is killed by SIGKILL / by SIGTERM | `CorruptionError` / `ResourceLimitError` / `ReadError`, not `CorruptionError` |
| Same member, `process_isolation=NONE`, `max_ppmd_in_process_input=1024` / default | In-process, fed in pieces / held and decoded whole |
| Valid 7z PPMd member of 8 MiB that pyppmd fed in pieces stops short of, default config | Held, decoded whole in a child, read in full |
| `ArchiveyConfig(process_isolation="none")` | Holds `ProcessIsolation.NONE` |
| `ArchiveyConfig(process_isolation=ProcessIsolation.NONE, use_rapidgzip=AcceleratorMode.ON)` | `ArchiveyUsageError` at construction |
| Password list `["wrong", right]`, budget covering only the right candidate's derivations | `ResourceLimitError`, not `EncryptionError`; the list does not continue |

