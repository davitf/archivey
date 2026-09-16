# Verdicts — Worker D (Errors, diagnostics, translation)

Config for this pass: **`[all]`** (`uv sync --group dev --extra all`), unless a row notes
otherwise. Spec citations preferred over `src/` when both speak (O-26). Spec line numbers
in Settles-it drift; matched by **requirement title/text**. `[code]` rows were executed
with `uv run --no-sync`. No `[TM]` rows in this cluster.

| # | V | Evidence |
|---|---|---|
| D-1 | verified | `error-handling` Single rooted archive exception hierarchy: every library-detected archive/environment failure is under `ArchiveyError`; `except ArchiveyError` catches them. Guide + `index.md` pointer match. |
| D-2 | verified | `[code]` `errors-and-diagnostics.md:11-19` ran on junk `maybe.7z`; caught `ArchiveyError` (`CorruptionError`). |
| D-3 | verified | Hierarchy: `OpenError` → `FormatDetectionError`, `UnsupportedFormatError`, `StreamNotSeekableError`. Guide table matches. |
| D-4 | verified | Hierarchy places `EncryptionError` under `ReadError`; format-zip/rar/7z + diagnostics unused-password matrices: required / wrong / provider-`None` → `EncryptionError`. Docstring: “Password required or wrong password.” |
| D-5 | verified | Hierarchy: `CorruptionError` / `TruncatedError` under `ReadError`; compressed-streams translation: corrupt → `CorruptionError`, unexpected EOF → `TruncatedError`. |
| D-6 | verified | `compressed-streams` Missing optional backends raise `PackageNotInstalledError` (package/extra/tool); `packaging-and-extras` RARLAB `unrar` missing → same. Guide “package or tool” matches. |
| D-7 | verified | Hierarchy: `FilterRejectionError` → `PathTraversalError`, `SymlinkEscapeError`, `SpecialFileError` (plus two more — see D-8). Guide names the three path/special cases correctly. |
| D-8 | verified | Defect/omission claim is true: hierarchy also has `UnportableNameError` and `DeceptiveNameError` under `FilterRejectionError`; table at `:29` does not name them. (Inventory note: 27 exception classes in `exceptions.py`; table names a subset; `api.md` renders 5 — evidence for `scope.md` Q3, not decided here.) |
| D-9 | verified | `safe-extraction` `abort_on`: `NAME_COLLISION` → `NameCollisionError`, `NAME_SANITIZED` → `NameRewrittenError`; without `abort_on`, collision/rewrite recorded in results. Matches guide + `extracting.md` abort table. |
| D-10 | verified | Hierarchy + meaning table: `ResourceLimitError` for listing/extraction resource limits; `safe-extraction` bomb-guard matrices. |
| D-11 | verified | `error-handling` Caller misuse remains outside `ArchiveyError`: `ArchiveyUsageError` / `ConcurrentAccessError`. Spot-check: `issubclass(ConcurrentAccessError, ArchiveyError) is False`. Co-cited `support-matrix` / `access-and-cost` agree. |
| D-12 | verified | Hierarchy includes `UnsupportedOperationError` under `ArchiveyError`; meaning: archive/backend/mode cannot provide the operation (not caller-bug). |
| D-13 | verified | `diagnostics` Immutable diagnostic values + Complete initial warning taxonomy (no advisory log-only); `logging` Warning logs are ordered projections of diagnostics. Guide + gotchas/extracting pointers match. |
| D-14 | verified | `diagnostics` Complete per-code policy: dispositions include `RAISE` → `DiagnosticRaisedError`; taxonomy codes are matchable. Any listed code can be escalated via `DiagnosticPolicy`. |
| D-15 | verified | `diagnostics` Report an empty listing: `EMPTY_ARCHIVE` on zero members without error; empty tar byte-identical to zero-filled junk; not an error. |
| D-16 | verified | Same + format-detection unconfirmed-format: extension-only empty listing → `EXTENSION_FORMAT_UNCONFIRMED`. Spot-check: 32 KiB zeros `z.tar` → both codes. |
| D-17 | verified | Same requirement: explicit `format=` + empty + detection disagrees → `EXPLICIT_FORMAT_LISTED_EMPTY`; `format=` stays an override. |
| D-18 | verified | `diagnostics` Report unused explicit arguments: `ENCODING_ARGUMENT_UNUSED` when backend `USES_ENCODING` is false; reason explains alternate decoding (7z/RAR/directory/single-file). |
| D-19 | verified | format-detection magic (`ustar`@257) + unconfirmed-format matrix: `detect_format()` on zero-filled **bytes with no usable name/extension** → `FormatDetectionError`; open of empty/zeros reaches TAR via extension or `format=`. Spot-check: nameless/`z.bin` refused; `z.tar` path → extension `GUESS` (harvest: practical “call detect_format on the path” advice is overbroad). |
| D-20 | verified | External OCI/Docker practice + `diagnostics` empty-listing prose: 1024-byte empty tar is the Docker/OCI empty layer (Go `archive/tar`); metadata-only history uses `empty_layer`. |
| D-21 | verified | `diagnostics` empty-listing: GNU `tar -b 64` → 32768 zero bytes, legitimate empty archive. |
| D-22 | verified | `diagnostics` Complete initial warning taxonomy placement: per-member extraction outcomes live only on `ExtractionResult` / report results; no duplicate diagnostic codes. `safe-extraction` report model. |
| D-23 | verified | Same placement + `safe-extraction` report authority: read `results`, not `report.diagnostics`, for per-member outcomes. |
| D-24 | verified | `diagnostics` Lifecycle-aware aggregation: extraction collector still carries read-time advisories (timestamp/symlink/digest/rewind codes in taxonomy). Those have no per-member result home. |
| D-25 | verified | Taxonomy placement + `safe-extraction` `abort_on` named opt-in for blocked / collision / sanitized. Matches `extracting.md`. |
| D-26 | verified | `[code]` import `ArchiveyConfig, DiagnosticPolicy, ARCHIVE_INTEGRITY_CODES` and `DiagnosticPolicy.strict()` constructed successfully. |
| D-27 | verified | `diagnostics` Named diagnostic policy presets: `strict()` RAISE on `ARCHIVE_INTEGRITY_CODES`, COLLECT otherwise. Spot-check: 13 RAISE codes == frozenset membership. |
| D-28 | verified | Same: `pedantic()` RAISE on every code. Spot-check: all 18 `DiagnosticCode` values resolve to RAISE. |
| D-29 | verified | Spec excluded set is exactly five: `EMPTY_ARCHIVE`, `PASSWORD_ARGUMENT_UNUSED`, `ENCODING_ARGUMENT_UNUSED`, `EXPLICIT_FORMAT_LISTED_EMPTY`, `STREAM_REWIND_REDECOMPRESSES`. Spot-check matches. |
| D-30 | verified | Spec: caller MAY build policy from `ARCHIVE_INTEGRITY_CODES`; exported on `archivey` and in `diagnostics.__all__`. |
| D-31 | verified | Same requirement Taxonomy growth: new codes MAY appear in minor releases; `default=RAISE` not version-stable; `strict()` recommended; removing a code is breaking. |
| D-32 | verified | `error-handling` Terminal archive listing errors; `archive-reading` MemberListReport; `documentation` complete-or-raise: `members()` / `scan_members()` raise, no partial list. Guide + opening-and-listing pointer. |
| D-33 | verified | `[code]` `members_report()` recipe ran; `report.error` is the attribute (`MemberListReport`). Spec requires prefix in members + failure in `error`. |
| D-34 | verified | Same listing requirement: `__iter__` / `stream_members` yield recovered members then raise. |
| D-35 | verified | Same: MUST NOT use diagnostics alone as the primary honesty channel for terminal listing failure. |
| D-36 | verified | `cli` salvage flag reserved without behavior; `documentation` salvage out of scope; guide + `cli.md` / `migrating.md` agree. |
| D-37 | verified | `format-tar` / `access-mode-and-cost` / `documentation`: RA `extract_all` fail-closed on terminal listing error (no partial writes). Spot-check: mid-archive TAR corruption → `CorruptionError`, dest empty. |
| D-38 | verified | `compressed-streams` Content faults raise from read, never from close. |
| D-39 | verified | Same + digest-at-EOF: end means `read(-1)`, read until `b""`, or reading declared size. |
| D-40 | verified | Digest requirement: raise on detectable mismatch; unverifiable/missing digest paths; formats without checksum. Matches gotchas “cannot fully deliver.” |
| D-41 | verified | Hierarchy: both under `ReadError`; compressed-streams translation is best-effort shape split. Guide: catch `archivey.ReadError`. |
| D-42 | verified | Size-declared / size-unknown read contracts: prefix delivered before terminal raise is unverified (not known-good). |
| D-43 | verified | Digest at clean EOF: full-length successful return implies computable checksum matched. |
| D-44 | verified | Size-declared truncation: first ask-past-available returns short quietly; next empty `read` raises. Guide + reading-members agree. |
| D-45 | verified | Size-declared corruption: reaching read raises and **withholds**; truncation: short return. Codec tests `test_verify_sized_mismatch_withholds_on_reaching_read` / short-size paths pass. |
| D-46 | verified | `[code]` chunked loop from errors + reading-members ran; `except archivey.ReadError` caught `CorruptionError` on CRC-bad ZIP. |
| D-47 | verified | Complete-stream `read()` / `read(-1)` raises with no bytes on digest/content fault. Spot-check: plain `stream.read()` → `CorruptionError`, nothing returned. |
| D-48 | wrong | Guide names `VerificationMode.STRICT` as shipped API that verifies a whole member before returning bytes. **No** such type in `src/` or current `openspec/specs/`; ADR 0014 points at open `verification-integrity-mode` proposal. Cited `compressed-streams` digest-at-EOF does not define this mode. (Guide prose wrong / premature.) |
| D-49 | verified | `compressed-streams` Content faults raise from read matrix covers all 14 cells (7 calls × corrupt/truncated). Spot-check via codec suite (withhold, short-then-raise, quiet partial+close). |
| D-50 | verified | `format-single-file-compressors` many codecs report `member.size is None`; compressed-streams size-unknown path cannot self-certify from bounded `read(n)` alone — use `read(-1)` / until `b""`. |
| D-51 | verified | Silence claim true: full translation contract not on errors page; specs + CONTRIBUTING state known third-party → `ArchiveyError` tree, unrecognized propagate, `OSError`/`KeyboardInterrupt`/`MemoryError` pass through (except where a spec says otherwise), `ArchiveyUsageError` outside tree. `extracting.md:74-75` is a partial pointer only. |
| D-52 | verified | Silence claim true: `error-handling` Exception messages inert + `diagnostics` Diagnostic messages inert — escape at construction so terminal print cannot move cursor/forge output. No guide page states it. |
| D-53 | verified | Silence claim true: `error-handling` escaped exactly once; `cli` Archive-derived text escaped before terminal display (CLI must not re-escape library messages). No guide page states it. |
| D-54 | verified | `format-tar` / listing honesty vs stdlib quiet stops; `error-handling` terminal listing loud. Guide gotchas + migrating truncated-archives contrast hold. |
| D-55 | verified | `logging` projections of diagnostics; `diagnostics` queryable surfaces. Prefer `reader.diagnostics` / extraction report over logs. |

## Notes for coordinator

### Wrong rows
- **D-48** — `VerificationMode.STRICT` documented as if shipped; not in specs or library (open proposal only).

### `[TM]` left unverified
- *(none in cluster D)*

### Config notes (`cfg`)
- Everyday verification: **`[all]`**.
- `[code]` / spot-checks used system `unrar`/`7z` present per SESSION; no optional-package absence runs for D-6 (spec text sufficient).

### Cross-cluster / process
- **D-8 / Q3:** table omission is real; do not decide `scope.md` Q3 here — inventory counts recorded in harvest.
- **D-19:** guide mechanism verified; harvest the path-vs-bytes detect_format caveat.
- **D-51–D-53:** silence rows verified as silence; inbound guide content for coordinator.
- Spec **line numbers drift**; matched by requirement titles.
- No false “conflicts with” / SPLIT cases in this cluster.
- Cross-page: D-46 duplicate on `reading-members` already flagged `→ page`; D-11 overlap with support-matrix / access-and-cost.

### Counts
- **verified:** 54
- **wrong:** 1
- **unverifiable:** 0
- **left for TM:** 0
