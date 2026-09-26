# Verdicts — Worker A (Opening, detection, sources)

Config for this pass: **`[all]`** (`uv sync --group dev --extra all`), unless a row notes
otherwise. Spec citations preferred over `src/` when both speak (O-26). `[code]` rows
were executed with `uv run --no-sync`.

| # | V | Evidence |
|---|---|---|
| A-1 | verified | `archive-reading` open + `__iter__` archive-order (`spec.md` Opening / Sequential in-order iteration). Spot-check: ZIP fixture yielded `subdir/a.txt` then `b.txt` in write order. |
| A-2 | verified | `archive-reading` complete-or-raise for `members()`; `documentation` §Document complete-or-raise listing. Spot-check: `members()` returned full list on clean ZIP. |
| A-3 | verified | `archive-reading` Name lookup: `get()` by normalized name. Spot-check: `get("subdir/a.txt")` → that member. |
| A-4 | verified | `archive-reading` Archive metadata access (`format`, `cost`); `access-mode-and-cost` CostReceipt. Spot-check: both present immediately after open. |
| A-5 | verified | `access-mode-and-cost` Declaring access mode: `streaming=False` default = random access. Spot-check: out-of-order `read("b.txt")` then `read("a.txt")` on ZIP. |
| A-6 | wrong | Coordinator verdict (absolute reading): no-buffering half is pipe/ADR-0010-scoped; absolute reading fails (RAR-from-seekable-stream / E-71). Copied unchanged. |
| A-7 | verified | `format-single-file-compressors` one-member archive; name from source filename. Spot-check: `access.log.gz` → one member `access.log`. |
| A-8 | verified | `compressed-streams` `open_stream` returns stream (`BinaryIO`/`ArchiveStream`), not an archive. Spot-check: `open_stream(...gz).read()` → decompressed bytes. |
| A-9 | verified | `[code]` `opening-and-listing.md:36-39` two-liner: `open_archive("logs.tar.gz")` → `TAR_GZ`; `open_stream("access.log.gz")` → bytes. Ran successfully. |
| A-10 | verified | `format-directory` Present directory as ArchiveReader; iterate → members under root. Spot-check: tree opened as `DIRECTORY` with file and dir members (`f1.txt`, `sub/`, `sub/f2.txt`). “Per file” prose is slightly soft (dirs are members too) but the claim holds. |
| A-11 | verified | `archive-reading` directory + explicit non-DIRECTORY `format=` → `ArchiveyUsageError`. Spot-check: `format=ZIP` on a directory raised with path + requested format. |
| A-12 | verified | `archive-reading` / `format-detection` non-consuming: seekable stream archive begins at current position; `open_archive` zero-origin wrap. Spot-check: `BytesIO(prefix+zip)` seeked past prefix opened members correctly. |
| A-13 | verified | Same stream-position contract: API has start-at-current, no end-bound parameter (`format-detection` Detection never consumes; `open_archive` docstring). Advice to wrap if trailing payload follows matches “no matching end bound.” |
| A-14 | verified | `access-mode-and-cost` streaming on non-seekable; TAR/`format-tar` and single-file compressors `SUPPORTS_STREAMING_NON_SEEKABLE` → `required_source=FORWARD_ONLY`. Spot-check: NonSeek `tar.gz` and `.gz` with `streaming=True` listed. |
| A-15 | verified | `format-zip` / `format-7z` / `format-rar` / `format-iso` require seek; `format_availability` → `SEEKABLE` for all four. Spot-check: ZIP/7z/RAR NonSeek + `streaming=True` → `StreamNotSeekableError`. |
| A-16 | wrong | Coordinator verdict: `access-and-cost.md:145-146` names only ZIP+ISO as always needing seek; implies 7z/RAR pipe-ok under `streaming=True`. They are not. `opening-and-listing.md:66-68` correct. Copied unchanged. |
| A-17 | verified | `FormatAvailability.required_source` docstring in `registry.py` + session measure: weakest source shape; ZIP/7z/RAR/ISO=`SEEKABLE`, TAR/GZ=`FORWARD_ONLY`. Replaces try/except as documented. |
| A-18 | wrong | `[code]` as written fails: `format_availability(detect_format(head))` passes a `FormatInfo` but API requires `ArchiveFormat` → `ArchiveyUsageError`. Needs `.format` (or equivalent). **Prose/code-sample wrong.** |
| A-19 | verified | `access-mode-and-cost` StreamCapability ordering matrix; `cost.py` total_ordering. Spot-check: `FORWARD_ONLY < SEEKABLE`; comparison vs `reader.cost.stream_capability` works. |
| A-20 | verified | Multi-volume support only in `format-7z` / `format-rar`; `format-zip` Reject multi-volume ZIP. Spot-check: two-ZIP sequence → `UnsupportedFeatureError` does not support multi-volume. |
| A-21 | verified | `format-7z` / `format-rar` volume matrices + `volumes.discover_volume_siblings`. Spot-check: `.7z.00N` discovery from any part; `tinyvol.part1/2.rar` from either part; `tinyvol_rnn.rar` + `.r00` from `.rar` or `.r00` when `.rar` present. |
| A-22 | verified | `format-7z` missing volume → error not partial; `join_volumes` gap → `TruncatedError` Incomplete 7z multi-volume set (also `tests/test_volumes.py`). *Message generalised since this pass: it now reads `Incomplete multi-volume set for {base}`, one wording for 7z, RAR and ZIP.* |
| A-23 | verified | Old-scheme discovery: without `<base>.rar`, `discover_volume_siblings(.rNN)` is `None` (`volumes.py` + `test_discover_rnn_without_first_volume_is_not_a_set`). Not joined as a set. (Opening a real continuation `.r00` alone then raises `UnsupportedFeatureError` Need first volume — still “not as part of a set.”) |
| A-24 | verified | `archive-reading` Multi-volume: explicit ordered `source` sequence, that order, no discovery. |
| A-25 | verified | `open_archive` docstring + `archive-reading`: length-1 sequence = single source. Spot-check: `[zip_path]` opened as ordinary ZIP. |
| A-26 | verified | `core.py` multi-volume gate for non-7z/RAR → `UnsupportedFeatureError`. Spot-check: `[zip, zip]` raised. |
| A-27 | verified | `format-detection` `detect_format()` → `FormatInfo` with `.format` and `.confidence`. Spot-check: ZIP → `CERTAIN`. |
| A-28 | verified | `format-detection` Magic-first with extension fallback. Spot-check: ZIP bytes under `.tar` → ZIP. |
| A-29 | verified | `format-detection` FORMAT_EXTENSION_CONFLICT on genuine magic/extension mismatch; diagnostic names both. Spot-check: ZIP bytes as `fake.tar` emitted conflict with TAR vs ZIP. (Guide’s `.jpg` example does not emit a conflict — `.jpg` is not a mapped archive extension; claim row itself is about disagreement when extension maps.) |
| A-30 | verified | `format-detection` same format open would use (handoff). Spot-check: `detect_format(tar.gz).format == open_archive(...).format` (`TAR_GZ`). |
| A-31 | verified | `format-detection` Compressed streams probed for inner TAR; missing decompressor → bare compressor. `tests/test_detection.py::test_inner_tar_probe_skipped_when_codec_missing` (monkeypatch `_zstd=None` → `ZST`). **cfg `[all]`**: zstd present (`backports.zstd`), so live `.tar.zst` detects as `TAR_ZST`; absence path covered by that test (same as `[core-only]` would see for ZST). |
| A-32 | verified | Opening NONE single-codec format raises `UnsupportedFormatError` naming package (`tests/test_registry.py::test_compressed_tar_none_when_stream_codec_missing`). Note: Settles-it `compressed-streams:124` describes `PackageNotInstalledError` for mid-decode missing codecs — different path; open-time NONE uses `UnsupportedFormatError` (matches the guide). |
| A-33 | wrong | Alleged same-situation conflict **does not reproduce**. `formats.md:36-37` / `:58-59` = member-codec / AES absence → `PackageNotInstalledError` (`compressed-streams`). `opening-and-listing.md:128-130` = format support NONE at open → `UnsupportedFormatError` (registry). Two exception types, **two situations**. No SPLIT of one fact. |
| A-34 | wrong | Spec `format-detection` SFX requirement matches the prose, but shipped `detect_format` has **no SFX scan** (module comment: deferred). Trigger is **not** only `FormatDetectionError`: low-entropy `MZ`+`0x90` stub → silent misdetect as `BROTLI` and successful open with fabricated `*.uncompressed` member; varied stub → `FormatDetectionError`; forced `format=RAR` works (parser SFX window); forced `SEVEN_Z` → `CorruptionError` (no 7z SFX scan). **Spec/prose ahead of detection code; silent path is the severe one.** See QUESTIONS A-34 matrix (F2). |
| A-35 | verified | Editorial duplication: `formats.md` §Detection (`:222-228`) restates magic-first + confidence already on `opening-and-listing.md` Detection. Confirmed by reading both. |
| A-36 | verified | `archive-reading` Password candidates and provider; unused-password identical for str / sequence / provider (`diagnostics` unused-argument matrix). Spot-check: all three on plain tar → one `PASSWORD_ARGUMENT_UNUSED` each. |
| A-37 | verified | `archive-reading` try order (known-good, then sequence); `format-7z` KDF cost / try known-good first. Guide “most likely first” matches. |
| A-38 | verified | `diagnostics` / `archive-reading`: password on non-encrypting format → accept + `PASSWORD_ARGUMENT_UNUSED`. Spot-check on TAR. |
| A-39 | verified | `archive-reading` password exhaustion → `EncryptionError`; exceptions hierarchy. Spot-check: encrypted RAR + wrong password → `EncryptionError`. |
| A-40 | verified | `[code]` `opening-and-listing.md:191-194` `is_current` filter. Ran on tar with duplicate `notes.txt`; current list correct. |
| A-41 | verified | `[code]` history-view loop `:198-203` ran; superseded tag printed on first `notes.txt`. (Still recorded though ruling is trim-to-one.) |
| A-42 | verified | `[code]` `index.md:6-12` open+list ran on `photos.zip`. |
| A-43 | verified | `[code]` `index.md:37-40` pipe + `streaming=True` + `stream_members()` ran on NonSeek `tar.gz` (stdin.buffer stand-in). |
| A-44 | verified | `[code]` `migrating.md:25-41` before/after: same names/sizes/bytes; `extract_all` wrote tree. After half is idiomatic equivalent of before. |

## Notes for coordinator

### Wrong rows
- **A-6** (coordinator copy) — absolute no-buffering reading
- **A-16** (coordinator copy) — `access-and-cost.md` seek list
- **A-18** — executable sample must use `detect_format(head).format` (or unpack)
- **A-33** — conflict claim false; different situations (see below)
- **A-34** — SFX “detected” overstated for `detect_format` / auto-open

### A-33 resolution
**Not a contradiction of one fact.** Keep both exception names on their real homes:
`PackageNotInstalledError` for optional member/decrypt backends; `UnsupportedFormatError`
when the format itself is `FormatSupport.NONE` (e.g. sole stream codec missing). Guide
readers may still confuse them — harvest item, not a SPLIT verdict.

### Config notes (`cfg`)
- Everyday verification: **`[all]`**.
- A-31 absence path: not live on `[all]` (zstd installed); confirmed via
  `test_inner_tar_probe_skipped_when_codec_missing` / registry NONE tests (same mechanism
  `[core-only]` would hit for ZST/TAR_ZST).

### Cross-cluster / process
- A-6 ↔ **E-71** (RAR seekable-stream buffering) — already flagged by coordinator.
- A-16 ↔ any Worker covering `access-and-cost` seek tables.
- A-34 may need a library change or a prose downgrade (“RAR/7z parsers accept an offset;
  `detect_format` SFX scan not shipped yet”) — do not fix in docs-only without deciding.
- A-18 is a pure docs sample bug (fixable without library change).
- Spec line numbers in Settles-it have drifted; requirements were matched by title/text.

### Counts
- **verified:** 39
- **wrong:** 5 (A-6, A-16, A-18, A-33, A-34)
- **unverifiable:** 0
