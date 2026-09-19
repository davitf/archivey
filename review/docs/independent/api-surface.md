# Public API surface (code-derived)

Derived from `src/archivey/__init__.py` `__all__`, `pyproject.toml`, and the
implementing modules. Citations are `path:line`. Demoted-but-importable symbols
(kept out of `__all__`) are listed separately.

Package: **archivey 0.2.0.dev0**, Python **≥3.11**, CPython classifiers through
3.14 (`pyproject.toml:7–38`). Core is zero-dependency; optional extras gate
codecs / formats / CLI polish / accelerators (`pyproject.toml:52–113`). Console
script: `archivey = archivey.cli.main:main` (`pyproject.toml:49–50`). Module
entry: `python -m archivey` → `archivey.__main__` (`src/archivey/__main__.py:1–7`).

---

## Top-level entry points

### `open_archive(source, *, format=None, streaming=False, member_streams=MemberStreams(0), password=None, encoding=None, config=None) -> ArchiveReader`

`src/archivey/core.py:96–275`

Opens a path, directory, binary stream, or ordered multi-volume sequence for
reading. Auto-detects format unless `format=` is set; a directory path always
forces `ArchiveFormat.DIRECTORY` (`core.py:187–191`).

**Preconditions / fail-fast**

| Condition | Error |
|---|---|
| `streaming=True` + `MemberStreams.CONCURRENT` | `ArchiveyUsageError` (`core.py:168–173`) |
| Multi-volume source and container ∉ {7z, RAR} | `UnsupportedFeatureError` (`core.py:202–206`) |
| Static password(s) on a format with `SUPPORTS_PASSWORD=False` | `UnsupportedOperationError` (`core.py:221–229`) |
| Non-seekable stream + `streaming=False` | `StreamNotSeekableError` (`core.py:234–243`) |
| Non-seekable stream + format that cannot stream without seek (ZIP/ISO/7z/…) | `StreamNotSeekableError` (`core.py:244–251`) |
| Format has no installed backend | `PackageNotInstalledError` / `UnsupportedFormatError` via registry |

Returns a context-manager `ArchiveReader`. Caller must close it (and any
member streams).

### `open_stream(source, *, format=None, seekable=False, config=None) -> ArchiveStream`

`src/archivey/core.py:278–355`

Single-file compressed payload (`.gz` / `.bz2` / `.xz` / …), not a container.
`seekable=True` requires a seekable source (`core.py:326–331`). Container
`ArchiveFormat` or detected container → `ArchiveyUsageError` /
`UnsupportedFormatError` (`core.py:367–380`). Uncompressed stream format →
`UnsupportedFormatError` (`core.py:334–338`). Missing path →
`FileNotFoundError` (`core.py:310–311`). Wrong type → `TypeError`
(`core.py:315–318`).

### `extract(source, dest, *, policy=STRICT, overwrite=ERROR, on_error=STOP, format=None, password=None, encoding=None, on_progress=None, config=None, limits=None) -> ExtractionReport`

`src/archivey/core.py:384–441`

One-shot extract of **all** members. No `members=` parameter by design
(`core.py:400–403`). Non-seekable stream sources are opened with
`streaming=True` automatically (`core.py:415–417`). Returns
`ExtractionReport` whose diagnostics span detect+open+extract
(`core.py:436–441`).

### `detect_format(source, *, config=None) -> FormatInfo`

Re-exported from `src/archivey/internal/detection.py:325–`. Magic-first
(`CERTAIN`), content probe (`PROBABLE`), extension (`GUESS`). Raises
`FormatDetectionError` when nothing matches (`detection.py:333–334`). Does not
consume stream bytes (seek restore / peek).

### Format queries

| Symbol | Role | Site |
|---|---|---|
| `list_supported_formats()` | Formats with support FULL or PARTIAL | `registry.py:288–290` |
| `list_known_formats()` | All registered formats including NONE | `registry.py:293–295` |
| `format_availability(fmt)` | Tri-state + `MissingComponent` install hints | `registry.py:283–285` |
| `FormatSupport` | `FULL` / `PARTIAL` / `NONE` | `registry.py:151–157` |
| `FormatAvailability` | `format`, `support`, `missing` | `registry.py:160–167` |
| `MissingComponent` | `name`, `install_hint`, `unlocks` | `types.py:185–199` |
| `FormatInfo` | Detect result | `detection.py:82–92` |
| `DetectionConfidence` | `CERTAIN` / `PROBABLE` / `GUESS` | `detection.py:76–79` |

---

## `ArchiveReader` (`src/archivey/reader.py:32–209`)

Abstract; constructed only via `open_archive`. Cannot be instantiated
(`tests/test_public_api.py:15–18`).

| Method / property | Behaviour | Key errors |
|---|---|---|
| `format` | Detected `ArchiveFormat` | — |
| `info` | `ArchiveInfo` without full scan | — |
| `cost` | `CostReceipt` | — |
| `diagnostics` | Cumulative `DiagnosticSummary` snapshot | — |
| `__iter__` | Members in archive order | streaming: single-pass |
| `members()` | Complete list or raise | `UnsupportedOperationError` if streaming; terminal listing errors raise |
| `members_report()` | Always returns report; `error is None` ⇒ complete | streaming ok via pass |
| `scan_members()` | Resolved list; finishes streaming pass | listing limits |
| `members_report_if_available()` | Index peek or `None`; never scans | — |
| `__contains__(member)` | Identity O(1); not name lookup | `TypeError` if not `ArchiveMember` |
| `get(name, default=None)` | Last-entry-wins by normalized name | `UnsupportedOperationError` if streaming |
| `open(member)` | Binary `ArchiveStream`; follows links | `KeyError` unknown name; `ArchiveyUsageError` wrong-reader / closed; `ConcurrentAccessError` overlapping without flag |
| `read(member)` | Full `bytes` (unbounded) | same as `open` |
| `stream_members(members=None)` | `(member, stream\|None)` in archive order | stream valid only until next yield |
| `extract_all(dest, …)` | Safe extract; optional `members` / `filter` | bomb / filter / overwrite / OnError |
| `io_stats()` | `IoStats` or `None` | — |
| `close` / context manager | Idempotent close | post-close ops → `ArchiveyUsageError` |

`MemberSelector` = collection of names/`ArchiveMember`, predicate, or `None`
(`reader.py:27–29`).

---

## Configuration (`src/archivey/config.py`)

| Symbol | Defaults / notes |
|---|---|
| `ArchiveyConfig` | Frozen; `use_rapidgzip`/`use_indexed_bzip2`=`AUTO`; `strict_archive_eof=False`; `zip_unflagged_fallback_encoding="cp437"`; default limits; diagnostic policy; `max_retained_diagnostic_references=256` (`config.py:121–147`) |
| `DEFAULT_ARCHIVEY_CONFIG` | Module singleton (`config.py:150`) |
| `ExtractionLimits` | `max_extracted_bytes=2GiB`, `max_ratio=1000`, `ratio_activation_threshold=5MiB`, `max_entries=1_048_576`; `.UNLIMITED` (`config.py:78–97`) |
| `ListingLimits` | `max_members=1_048_576`, `max_metadata_bytes=64MiB`; `.UNLIMITED` (`config.py:100–118`) |
| `AcceleratorMode` | `AUTO`/`ON`/`OFF` + `enabled_for(...)` (`config.py:16–66`) |
| `RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE` | `1<<20` — demoted from `__all__` (`config.py:75`) |
| `PasswordInput` / `PasswordProvider` / `PasswordRequest` | str/bytes / sequence / callable (`config.py:153–168`) |

---

## Extraction value types (`src/archivey/internal/extraction_types.py`, re-exported)

| Symbol | Role |
|---|---|
| `ExtractionPolicy` | `STRICT` (default) / `STANDARD` / `TRUSTED` (`extraction_types.py:36–54`) |
| `OverwritePolicy` | `ERROR` (library default) / `SKIP` / `REPLACE` / `RENAME` (`:56–70`) |
| `OnError` | `STOP` (default) / `CONTINUE` — failures only (`:73–84`) |
| `ExtractionStatus` | `EXTRACTED` / `NOT_OVERWRITTEN` / `SUPERSEDED` / `BLOCKED` / `FAILED` (`:87–94`) |
| `ExtractionProgress` | Progress callback snapshot (`:97–118`) |
| `ExtractionResult` | Per-member outcome (`:121–142`) |
| `MemberFilter` | `(ArchiveMember) -> ArchiveMember \| None` (`:33`) |
| `ExtractionReport` | Results + diagnostics; sequence-like (`diagnostics.py:393–417`) |

Universal path safety always applies, including under `TRUSTED`
(`filters.py:49–55`, `tests/test_extraction.py:184`).

---

## Types (`src/archivey/types.py`)

| Symbol | Role |
|---|---|
| `MemberStreams` | Flag: `CONCURRENT`, `SEEKABLE`; default `0` = one live forward-only stream (`:15–41`) |
| `ContainerFormat` / `StreamFormat` | Enums (`:44–66`) |
| `ArchiveFormat` | `(container, stream)` with named class attrs (`:69–174`) |
| `ArchiveMember` | Mutable slots dataclass; callers treat as read-only; use `.replace()` (`:311–488`) |
| `ArchiveInfo` | Archive-level metadata + embedded `cost` (`:491–523`) |
| `MemberType` | `FILE`/`DIRECTORY`/`SYMLINK`/`HARDLINK`/`OTHER`/`ANTI` (`:215–228`) |
| `CompressionAlgorithm` / `CompressionMethod` | Codec chain (`:244–276`) |
| `CreateSystem` / `HashAlgorithm` / `crc32_digest` | Metadata helpers (`:231–241`, `:279–302`) |

Notable `ArchiveMember` fields: `is_current` (last-entry-wins), `is_encrypted`,
`hashes`, `extra` (e..g. `is_junction`), `modified_utc()`, `is_junction`
(`types.py:373–484`).

---

## Cost (`src/archivey/cost.py`)

Orthogonal axes on `CostReceipt`: `ListingCost` (`INDEXED` /
`REQUIRES_SCANNING` / `REQUIRES_DECOMPRESSION`), `AccessCost` (`DIRECT` /
`SOLID`), `StreamCapability` (`SEEKABLE` / `FORWARD_ONLY`), optional
`solid_block_count`, `notes` (`cost.py:15–81`). Observed per-format table pinned
in `tests/test_cost_receipt.py:80–184`.

---

## Diagnostics (`src/archivey/diagnostics.py`)

| Symbol | Role |
|---|---|
| `DiagnosticCode` | Stable string codes (`:57–74`) |
| `DiagnosticSeverity` | Currently only `WARNING` (`:77–84`) |
| `DiagnosticDisposition` | `IGNORE` / `COLLECT` / `RAISE` (`:87–92`) |
| `DiagnosticPolicy` | Default + per-code overrides (`:377–390`) |
| `Diagnostic` / `DiagnosticSummary` | Occurrence + snapshot (`:338–374`) |
| `OnDiagnostic` | Optional callback (`:446–447`) |
| `MemberListReport` | Members + optional terminal `error` + diagnostics (`:420–443`) |
| Context dataclasses | One per `kind`; demoted from `__all__` but importable |

Escalation via policy raises `DiagnosticRaisedError` (always-stop;
`exceptions.py:173–177`).

---

## Exceptions (`src/archivey/exceptions.py`)

Two roots:

1. **`ArchiveyError`** — archive/environment/format problems (`:24–52`).
   Subtree: `OpenError` → `FormatDetectionError` / `UnsupportedFormatError` /
   `StreamNotSeekableError`; `ReadError` → `CorruptionError` / `TruncatedError` /
   `EncryptionError` / `LinkTargetNotFoundError`; `ExtractionError` →
   `FilterRejectionError` → `PathTraversalError` / `SymlinkEscapeError` /
   `SpecialFileError` / `UnportableNameError`; siblings `ResourceLimitError`,
   `UnsupportedFeatureError`, `PackageNotInstalledError`,
   `UnsupportedOperationError`, `DiagnosticRaisedError`; `WriteError` (write API
   not shipped — demoted from `__all__`).
2. **`ArchiveyUsageError`** — caller misuse, **not** under `ArchiveyError`
   (`:150–162`). Subclass: `ConcurrentAccessError` (`:165–170`).

---

## Streams & measurement

| Symbol | Role |
|---|---|
| `ArchiveStream` | Public binary stream: translate/stamp errors, optional verify, optional rewind warning (`archive_stream.py:75–94`) |
| `IoStats` | `bytes_decompressed`, `compressed_bytes_consumed`, `source_seek_count` (`measurement.py:34–50`) |
| `enable_measurement()` | Context manager; zero overhead when unused (`measurement.py:1–18`) |

---

## Packaging extras (runtime)

From `pyproject.toml:52–113` (what they unlock, from codec/backend requirements):

| Extra | Packages / effect |
|---|---|
| `[7z]` | pyppmd, inflate64, brotli, lz4, cryptography, pybcj, backports.zstd&lt;3.14 — PPMd/Deflate64/Brotli/… for 7z (and shared ZIP codecs) |
| `[rar]` | cryptography (header crypto); **RARLAB `unrar` binary on PATH is separate** (`rar_unrar.py:21–23`) |
| `[crypto]` | cryptography |
| `[iso]` | pycdlib |
| `[zstd]` / `[lz4]` | bare stream codecs |
| `[cli]` | tqdm (progress bars) |
| `[seekable]` | rapidgzip (gzip + bundled indexed bzip2) |
| `[recommended-lite]` / `[recommended]` / `[all]` | aggregates |

---

## CLI surface (`archivey` / `python -m archivey`)

Implemented verbs (`cli/main.py`): **list** (`l`, default), **test** (`t`),
**extract** (`x`), **info** (`i`). Reserved stubs: `detect`, `hash`, `create`,
`convert`, `cat`.

Notable CLI≠library deltas (must be documented for CLI users):

- Default overwrite is **`rename`**, library default remains **`error`**
  (`cli/main.py` help text ~273).
- Default dest is **smart enclosing directory** / cwd, not a required `-d`
  (`extract_cmd.py:89–142`).
- Exit codes: `0` ok, `1` fail, `2` usage, `3` policy-blocked-only
  (`cli/exit_codes.py:5–10`).

---

## Demoted but importable (not in `__all__`)

Allowlisted in `tests/test_public_api.py:70–86`:

- All `*Context` diagnostic payloads
- `RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE`
- `WriteError`

These remain `from archivey import …` compatible without crowding the curated
API reference.
