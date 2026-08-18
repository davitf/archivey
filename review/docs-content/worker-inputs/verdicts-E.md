# Verdicts — Worker E (Formats, codecs, stored digests)

Config for this pass: **`[all]`** (`uv sync --group dev --extra all`), unless a row notes
otherwise. Spec citations preferred over `src/` when both speak (O-26). Spec line numbers
in Settles-it have drifted; requirements were matched by **title/text**. `[code]` rows
were executed with `uv run --no-sync`. `[TM]` → `left for TM`. E-71 copied unchanged.

| # | V | Evidence |
|---|---|---|
| E-1 | wrong | 11-row shape and most cells match the seven `format-*` specs + `packaging-and-extras` Optional extras + session `format_availability` (ZIP/7z/RAR/ISO `SEEKABLE`; compressors/`TAR*` `FORWARD_ONLY`; ISO needs `pycdlib`; `.Z` core; `.lz4` optional; `.zst` `[recommended]` on 3.11). **False cell:** Directory Listing = “indexed”, but `format-directory` Present a filesystem directory… sets `ListingCost.REQUIRES_SCANNING` (“no O(1) index”). Spot-check: `dirroot` → `REQUIRES_SCANNING` / `DIRECT`. |
| E-2 | verified | `format-zip` Report ZIP format properties: `ListingCost.INDEXED` (central directory), `AccessCost.DIRECT`, Reject non-seekable ZIP. Spot-check: NonSeek ZIP + `streaming=True` → `StreamNotSeekableError`. |
| E-3 | verified | `format-zip` Purpose + Decode ZIP member bodies through the shared codec layer: stdlib `zipfile` for CD/listing; member data via shared codecs. Matches `migrating.md` ZIP row. |
| E-4 | verified | `format-zip` Reject non-seekable ZIP: non-seekable + `streaming=True` still rejected. Spot-check OK. **Cross-page:** co-cited `access-and-cost.md:145-146` is A-16-wrong for implying only ZIP+ISO need seek; the ZIP half of E-4 is still true. |
| E-5 | verified | `format-zip` Decode ZIP member bodies + `packaging-and-extras` Optional extras: Deflate64/`inflate64`, PPMd/`pyppmd`, Zstd/`backports.zstd` (or 3.14+ stdlib) under `[recommended]`; missing → `PackageNotInstalledError`. **cfg `[all]`:** packages present. Absence path: `tests/test_zip_native_codecs.py` monkeypatches `_inflate64`/`pyppmd`/zstd → `PackageNotInstalledError` (A-31 pattern). |
| E-6 | verified | `format-zip` Reject multi-volume ZIP. Spot-check + `test_split_segment_name_rejected`: `.z01` → `UnsupportedFeatureError` … multi-volume. |
| E-7 | verified | `format-zip` Report/Decode matrices: unknown method → listing succeeds; reading → `UnsupportedFeatureError`. |
| E-8 | verified | `format-zip` Map ZIP member metadata: DOS base; NTFS `0x000A` / Extended Timestamp `0x5455` override. Tests: `test_extended_timestamp_beats_ntfs`, `test_ntfs_timestamps_used_when_no_extended_timestamp`. |
| E-9 | verified | `format-zip` Decode unflagged ZIP member names by UTF-8-validity sniff; default fallback cp437. Spot-check: `ArchiveyConfig.zip_unflagged_fallback_encoding == "cp437"`; `test_unflagged_utf8_name_is_sniffed`. |
| E-10 | verified | Diagnostic code `member_name_encoding_inferred` (`DiagnosticCode.MEMBER_NAME_ENCODING_INFERRED`); sniff scenario emits it. Spot-check: unflagged `Español.txt` → count 1. |
| E-11 | verified | Same UTF-8 sniff requirement: explicit `encoding=` authoritative, disables sniff. Spot-check + `test_explicit_encoding_disables_sniff`: `encoding="latin-1"` on `caf\xe9.txt` → `café.txt`, no inference diagnostic. |
| E-12 | verified | Guide fact: wrongly-set bit 11 + invalid UTF-8 → stdlib `zipfile` raises `UnicodeDecodeError` while reading the CD (repro: even a second good member never lists); archivey wraps as archive-wide `CorruptionError`. Spec UTF-8 sniff requirement settles the flag path; the unlistable residual is prose-accurate. Roadmap clause at formats:53-54 is editorial Cut (not re-judged here). |
| E-13 | verified | `format-zip` Confirm multi-candidate ZipCrypto passwords (STORED expensive confirmation). Matches `access-and-cost.md` passwords section. |
| E-14 | verified | `format-zip` Read WinZip AES-encrypted members: AE-1/AE-2, PBKDF2+AES-CTR+HMAC; AE-2 omits `crc32`; without crypto → `PackageNotInstalledError` but still listed encrypted. **cfg `[all]`:** `cryptography` present; `test_aes_without_crypto_raises` covers absence. |
| E-15 | verified | `format-tar` Report TAR format properties: plain `.tar` → `AccessCost.DIRECT` via `tarfile` `r:`. |
| E-16 | verified | `format-tar` Extract TAR hardlinks…; `safe-extraction` hardlink coordinator: unfiltered `extract_all` resolves in one pass. |
| E-17 | verified | `format-tar` Serialize shared tarfile handle… under `concurrent_members=True`; `format-iso` Serialize shared pycdlib handle… — same per-reader lock shape. |
| E-18 | verified | `format-tar` Detect truncated TAR archives: stdlib treats corrupt non-first header as clean EOF; archivey backstops. Matches gotchas TAR residual. |
| E-19 | verified | Same requirement: rejected non-null header → `CorruptionError` by default; random-access probe catches final-block case. `test_corrupt_final_header_raises_corruption_by_default`. |
| E-20 | verified | Same: missing two-block trailer → `ARCHIVE_EOF_MARKER_MISSING` (warn), not raise; trailer-less / `cat`-joined / boundary truncation byte-identical. Diagnostic code present. |
| E-21 | verified | `format-tar` Detect truncated… + Under strict EOF…: `strict_archive_eof=True` escalates absent/short → `TruncatedError`. Default `ArchiveyConfig.strict_archive_eof is False`. `test_missing_eof_blocks_strict_archive_eof_raises`. |
| E-22 | verified | `format-tar` Under strict EOF, nothing but zeros may follow the trailer → trailing junk / concatenated → `CorruptionError`. |
| E-23 | verified | Same requirement: zero padding after trailer still passes (`tar` 10 KiB records rationale in spec). |
| E-24 | verified | Same: check reads to EOF; O(tail); compressed tar decompresses the tail — why opt-in. |
| E-25 | verified | `format-tar` Detect truncated…: truncation inside member data → `TruncatedError` during iteration, independent of the flag. |
| E-26 | verified | Same: streaming limitation — rejected **final** header surfaces as missing-trailer (`absent`) in streaming, `CorruptionError` in random-access. Spec streaming matrix. Roadmap clause at formats:92 is Cut (not re-judged). |
| E-27 | verified | `format-7z` Parse 7-Zip headers natively + Decode folder coder chains: native header; LZMA/LZMA2/BCJ/Delta/Deflate/BZip2/stored via stdlib path; no `py7zr` on read. Co-cites on index/migrating/acknowledgements match. |
| E-28 | verified | `format-7z` codec table + `packaging-and-extras`: `[recommended]` adds PPMd, Deflate64, Zstd, Brotli, AES. **cfg `[all]`:** backends present; `test_ppmd_without_pyppmd_raises` / `test_aes_without_crypto_raises` for absence. |
| E-29 | verified | `format-7z` Reject unsupported codecs: BCJ2 → `UnsupportedFeatureError`, no garbage. `test_bcj2_folder_is_rejected`. |
| E-30 | verified | `format-7z` Stream solid folders with bounded memory: `stream_members()` once per folder; random mid-folder `open()` may re-decode from folder start. |
| E-31 | verified | `format-7z` Decrypt AES-encrypted 7z…: store/copy + no folder digest + no member CRC → bytes + `DIGEST_UNVERIFIABLE` `reason="no_integrity_anchor"`. `tests/test_crypto_findings.py` asserts that reason. |
| E-32 | verified | Same matrix: header-encrypted wrong password → zero file records → `EncryptionError` (never silent empty listing). `test_header_encrypted_empty_decoded_header_rejected`. |
| E-33 | verified | Documented residual in gotchas/formats: non-empty plausible wrong-password header can still parse; follows from the zero-records gate in `format-7z` Decrypt AES… (only the empty case is rejected as password). |
| E-34 | left for TM | `[TM]` `NumCyclesPower` ≤24 / `0x3F` / 25–62 → `UnsupportedFeatureError` is in `format-7z` Decrypt AES… and Bound header count fields; left for threat-model edit per cluster. |
| E-35 | verified | `format-7z` Declare 7-Zip format properties: write not shipped; `py7zr` oracle-only. Attempt write → `UnsupportedOperationError` in matrix. |
| E-36 | verified | `format-rar` Parse RAR headers natively + Use RARLAB unrar only for member data: listing without `unrar`. Spot-check: `basic_nonsolid__.rar` listed 6 members. Multi-page co-cites consistent. |
| E-37 | verified | `format-rar` Use RARLAB unrar only… + `packaging-and-extras` RAR data uses RARLAB unrar only: not `unrar-free`/`unar`/`7z`; no pip extra. Matches install/formats/acknowledgements. |
| E-38 | verified | Implemented in `rar_unrar.py`: bare `-p`, password (+ newline) on stdin, not argv. Spec `format-rar` Constrain unrar argv… settles member-path argv; password-stdin is **code-true** (openspec silent on the stdin feed itself — harvest). |
| E-39 | verified | `format-rar` Decrypt RAR5 header-encrypted archives natively via `[recommended]`/`cryptography`. **cfg `[all]`:** crypto present; header fixture lists with password. |
| E-40 | verified | Native `blake2sp` on stdlib `hashlib` (`internal/hashing/blake2sp.py`); `format-rar` Report RAR cost… Blake2sp on `member.hashes`; packaging notes no package. Spot-check: `blake2sp.rar` → `HashAlgorithm.BLAKE2SP` present. |
| E-41 | verified | Tweaked HASHMAC digests omitted from `member.hashes` (stashed under `extra`); verified with password via native ConvertHashToMAC-equivalent transforms. Spot-check: `encryption_blake2sp.rar` → `hashes={}`, `rar.tweaked_blake2sp` in extra; `test_f1_*`. Spec `format-rar` Report RAR cost… does **not** name HASHMAC (guide/code ahead of that requirement — harvest). UnRAR function-name wording → TM per ruling. |
| E-42 | verified | `format-rar` Expose RAR file-version history…; `safe-extraction` skips `is_current=False` by default. Spot-check: `file_version__.rar` → `file.txt;1` / `;2` with `rar.file_version`, live `file.txt` current; extract-skip tests pass. |
| E-43 | verified | `format-rar` Stream solid RAR… one `unrar p` pipe; Serve random access… may use explicit temp materialization for solid random opens. |
| E-71 | wrong | Coordinator verdict: **silence is a claim.** RAR stream temp spill via `_ensure_archive_path` (whole archive to temp `.rar`), absent from `CostReceipt.notes` and diagnostics; stored/`_can_direct_read` skips it. Copied unchanged — repro not re-run. |
| E-44 | verified | `format-rar` Declare RAR format properties: read-only / no writer. |
| E-45 | verified | `format-iso` Declare ISO format properties: needs `pycdlib` (`[recommended]`), seekable source. **cfg `[all]`:** ISO `FormatSupport.FULL`, `required_source=SEEKABLE`. Co-cite `access-and-cost` seek sentence still A-16-incomplete for 7z/RAR. |
| E-46 | verified | `format-iso` Auto-select richest namespace: Rock Ridge → Joliet → ISO 9660; `ArchiveInfo.extra["iso.namespace"]`. Spot-check: RR image → `rock_ridge`. |
| E-47 | verified | `format-iso` Read raw .bin… Mode 1 strip to 2048; unsupported layouts → `UnsupportedFeatureError`. |
| E-48 | verified | Process-global pycdlib hang-safety guard on `import archivey` (`iso_reader._install_pycdlib_directory_cycle_guard`); gotchas accurate. **`formats.md` §ISO is silent** while gotchas links there — scope row 10 gap (harvest). Spec `format-iso` does not document the patch. |
| E-49 | verified | `format-directory` Keep directory reader constraints as strict as archive readers: default forward-only member streams / one live stream until `SEEKABLE`/`CONCURRENT`. Spot-check: default member `seekable() is False`; `seekable_members=True` → True. |
| E-50 | verified | `format-single-file-compressors` Present each compressor as a one-member archive; name from path or `data` for anonymous. Spot-check: `access.log.gz` → `access.log`; anon GZ → `data`. |
| E-51 | verified | Same + Surface gzip stored metadata: `FNAME` → `extra["gzip.original_filename"]`, not member name. `test_gzip_stored_filename_surfaced`. |
| E-52 | verified | `format-single-file-compressors` Surface stored decompressed digests: single-member seekable/path `.gz` → `crc32`; multi-member and non-seekable omit. Tests pass. |
| E-53 | verified | `seekable-decompressor-streams` Accelerator errors…: rapidgzip gzip truncation best-effort (empty→stdlib + ISIZE); weaker than stdlib alone; `use_rapidgzip=OFF` for certainty. Matches formats + gotchas. **cfg `[all]`:** `rapidgzip` present; OFF path is the documented escape. Load-bearing O-2 subject — no softening. |
| E-54 | verified | Caveat scoped to bare `.gz` / `open_stream` / zlib/raw deflate; ZIP/7z members verify via CRC/size + `VerifyingStream` (`compressed-streams` Decompressed output digests…). |
| E-55 | verified | `format-single-file-compressors` Surface stored…: `.lz` CRC whenever source seekable (path / BytesIO), not pipe. |
| E-56 | verified | Same requirement: surfacing depends on source shape, not `seekable_members`; xz size from stream index analogous. Spec scenario: `.lz` hashes identical with/without `seekable_members=True`. |
| E-57 | verified | Multi-member lzip: combined CRC equals `crc32` of concatenated payloads (`combine` algebra in spec + `test_multi_member_lzip_exposes_combined_crc32`). Derivation detail may Trim to TM/spec per ruling. |
| E-58 | verified | Spec stored-digest table: `.bz2`/`.xz`/zlib/brotli/`.Z` → no cheap whole-member digest. |
| E-59 | verified | Same: zlib Adler-32 verified by decompressor, not on `member.hashes`. Spot-check: `ArchiveFormat.ZLIB` member `hashes == {}`. |
| E-60 | verified | `format-single-file-compressors` Use one backend… + `packaging-and-extras` Zero-dependency core: `.Z` native LZW core. `format_availability(Z)` FULL on `[all]` (and core). |
| E-61 | verified | `format-single-file-compressors` Report member size… / `seekable-decompressor-streams` Unix-compress CLEAR…: nonzero leftover bits → `TruncatedError` on next `read()`; zero-leftover silent. Matches gotchas. |
| E-62 | verified | Same: forward decode on non-seekable; CLEAR seek points when seekability declared. |
| E-63 | verified | `compressed-streams` open_stream is forward-only unless seekability is requested. Spot-check: default `seekable() is False`; `seekable=True` → True. |
| E-64 | verified | `archive-data-model` ArchiveMember hashes: `Mapping[HashAlgorithm, bytes]`; `crc32_digest` four big-endian bytes (`types.py`). Spot-check: ZIP CRC values are 4-byte `bytes`. |
| E-65 | verified | `format-single-file-compressors` Surface stored… + `documentation` Document the stored-digest matrix: readable without decompress; not computed; full `read()` still verifies. |
| E-66 | verified | Matrix rows match specs/tests: ZIP FILE/SYMLINK `crc32` (spot-check symlink+file); 7z FILE `crc32` (`format-7z` Decode folder coder chains); RAR5 crc32/blake2sp (`format-rar` Report RAR cost…); `.gz` single-member-seekable; `.lz` seekable (incl. multi combine); none row for bz2/xz/zlib/brotli/Z/TAR/directory. |
| E-67 | verified | `[code]` `formats.md` `content_key` recipe ran on `/tmp/e-verify/backups.zip`: both files returned `("stored", "crc32", …)` via `HashAlgorithm` membership, `reader.open`, `is_file`/`is_current`. |
| E-68 | verified | Provenance advice matches `documentation` stored-digest / cheap-dedupe requirement (stored weaker/format-specific vs computed stronger/full decode). |
| E-69 | verified | Home list (gzip, bzip2, xz, zstd, lz4, lzip, zlib, brotli, Unix compress) matches `format-single-file-compressors` Purpose one-member set. `compressed-streams` default-backend table omits named **lzip** / wrapper **zlib** rows (spec drift harvest); guide list still complete vs the single-file capability. |
| E-70 | verified | `[code]` `migrating.md` py7zr before/after After half: `archivey.extract("a.7z", "out/")` wrote `a.txt` (`hello world\n`) from `/tmp/e-verify/a.7z`. |

## Notes for coordinator

### Wrong rows
- **E-1** — quick matrix Directory Listing cell says “indexed”; spec/code are `REQUIRES_SCANNING`
- **E-71** (coordinator copy) — silence is a claim (RAR stream whole-archive temp spill / P11)

### Left for TM
- **E-34** — `NumCyclesPower` clamp (cluster `[TM]`)

### Config notes (`cfg`)
- Everyday verification: **`[all]`**.
- E-5 / E-14 / E-28 / E-39 / E-45 / E-53: optional pieces present on `[all]`; absence paths confirmed via existing monkeypatch/tests where the package is installed (`test_zip_native_codecs`, `test_aes_without_crypto_raises`, `test_ppmd_without_pyppmd_raises`, `test_aes_without_crypto_raises` for 7z).

### Cross-cluster / process
- **A-16 ↔ E-4 / E-45:** `access-and-cost.md:145-146` still understates seek (ZIP+ISO only). E-4/E-45 claims about ZIP/ISO themselves are true; do not treat those rows as conflicting with A-16.
- **A-33 ↔ E-5 / E-14:** `PackageNotInstalledError` for missing member codecs / WinZip AES is the correct exception for those situations (not `UnsupportedFormatError` / format NONE). No false conflict.
- **E-53:** do not soften; O-2 / O-16 subject. Aligns with gotchas rapidgzip bullet.
- **E-71 ↔ A-6 / P11:** stream spill silence; coordinator repro stands.
- Spec Settles-it line numbers drifted throughout; matched by requirement title.
- E-38 password-on-stdin and E-41 HASHMAC are guide+code accurate; openspec `format-rar` is thin/silent on those details (harvest, not `wrong` guide).

### Counts
- **verified:** 68
- **wrong:** 2 (E-1, E-71)
- **left for TM:** 1 (E-34)
- **unverifiable:** 0
