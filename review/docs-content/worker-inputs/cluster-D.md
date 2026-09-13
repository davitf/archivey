# D. Errors, diagnostics, translation

Specs: `error-handling`, `diagnostics`, `logging`.
Pages: `errors-and-diagnostics`, `extracting`, `gotchas`, `opening-and-listing`,
`reading-members`, `support-matrix`.

| # | Claim | Stated at | Settles it | Ruling | V |
|---|---|---|---|---|---|
| D-1 | **Every failure from the archive or its environment derives from `ArchiveyError`**, so one `except` covers them all | `errors-and-diagnostics.md:8-9`, `index.md:60-61` | `error-handling:20` | Keep | |
| D-2 | `[code]` the `except ArchiveyError` block runs | `errors-and-diagnostics.md:11-19` | — (executable) | Keep | |
| D-3 | `OpenError` covers `FormatDetectionError`, `UnsupportedFormatError`, `StreamNotSeekableError` | `errors-and-diagnostics.md:25` | `src/archivey/exceptions.py:105-119`, `error-handling:20` | Keep — the table is the exception tree's only reference until §D is settled | |
| D-4 | `EncryptionError` is raised when a password is required, missing, or wrong | `errors-and-diagnostics.md:26` | `src/archivey/exceptions.py:133` | Keep | |
| D-5 | `CorruptionError` / `TruncatedError` mean the archive is malformed or cut short | `errors-and-diagnostics.md:27` | `src/archivey/exceptions.py:125`, `:129` | Keep | |
| D-6 | `PackageNotInstalledError` means an optional package **or tool** is absent (e.g. the `unrar` binary) | `errors-and-diagnostics.md:28` | `src/archivey/exceptions.py:224`, `compressed-streams:124`, `packaging-and-extras:142` | Keep | |
| D-7 | `FilterRejectionError` means extraction blocked an unsafe member, and covers `PathTraversalError`, `SymlinkEscapeError`, `SpecialFileError` | `errors-and-diagnostics.md:29` | `src/archivey/exceptions.py:149-163` | Keep | |
| D-8 | **Completeness question for the D-7 row:** the tree also carries `UnportableNameError` and `DeceptiveNameError` under `FilterRejectionError`, which the table does not name | `errors-and-diagnostics.md:29` (omission) | `src/archivey/exceptions.py:165`, `:174` | Keep — coupled to §D (`scope.md` Q3) | |
| D-9 | `NameCollisionError` / `NameRewrittenError` are **raised only when you opted in with `abort_on`**; without it a collision or rewrite is recorded in the result, not raised | `errors-and-diagnostics.md:30`, `extracting.md:135-143` | `safe-extraction:951`, `src/archivey/exceptions.py:190`, `:201` | Keep | |
| D-10 | `ResourceLimitError` means a listing or extraction safety limit was exceeded | `errors-and-diagnostics.md:31` | `src/archivey/exceptions.py:211`, `safe-extraction:479` | Keep | |
| D-11 | **Mistakes in your code are deliberately outside the hierarchy**: misuse raises `ArchiveyUsageError` (e.g. `ConcurrentAccessError`), which is **not** an `ArchiveyError`, so a blanket `except ArchiveyError` never swallows a bug | `errors-and-diagnostics.md:33-37`, `support-matrix.md:128-132`, `access-and-cost.md:128` | `error-handling:84`, `src/archivey/exceptions.py:237`, `:259` | Keep (`support-matrix.md:128-132` → `→ page`) | |
| D-12 | When an **archive** genuinely cannot provide an operation — seeking a non-seekable member, a format that cannot list — that is a real `ArchiveyError`: `UnsupportedOperationError` | `errors-and-diagnostics.md:37-39` | `src/archivey/exceptions.py:228`, `error-handling:20` | Keep | |
| D-13 | Diagnostics are **structured, queryable advisories** on the reader and on the extraction report, not only log lines | `errors-and-diagnostics.md:43-46`, `gotchas.md:105-106`, `extracting.md:227-228` | `diagnostics:21`, `logging:40` | Keep | |
| D-14 | Each listed condition has a `DiagnosticCode` you can match on, and **any** of them can be escalated to an exception with a `DiagnosticPolicy` | `errors-and-diagnostics.md:50-52` | `diagnostics:153`, `src/archivey/diagnostics.py:58`, `:470` | Keep the table; trim the cells | |
| D-15 | `EMPTY_ARCHIVE` — the listing finished with no error and no members; an empty tar is real and **byte-identical to a zero-filled junk file of the same size** | `errors-and-diagnostics.md:56`, `gotchas.md:91-95` | `diagnostics:286` | Keep table / `Trim to ~3 + link` on `gotchas` | |
| D-16 | `EXTENSION_FORMAT_UNCONFIRMED` — the format came from the filename, nothing in the bytes confirmed it, and the listing came back empty | `errors-and-diagnostics.md:57`, `gotchas.md:97-99` | `format-detection:289`, `diagnostics:286` | Keep table | |
| D-17 | `EXPLICIT_FORMAT_LISTED_EMPTY` — you passed `format=`, the listing was empty, detection disagrees; `format=` stays an override so this tells you rather than refusing | `errors-and-diagnostics.md:58`, `gotchas.md:99-100` | `diagnostics:286` | Keep table | |
| D-18 | `ENCODING_ARGUMENT_UNUSED` — you passed `encoding=` to a backend that decodes names another way (7z stores UTF-16, RAR decodes in its own parser, directory and single-file names come from the filesystem) | `errors-and-diagnostics.md:60` | `diagnostics:253` | Keep table | |
| D-19 | **`detect_format()` does refuse zero-filled bytes**, because a tar's `ustar` magic lives inside a member header — so an empty tar reaches the TAR reader only by extension or explicit `format=` | `gotchas.md:100-103` | `format-detection:147`, `format-detection:117` | `Trim to ~3 + link` | |
| D-20 | Empty tars are common in practice: **Docker/OCI images carry a 1024-byte one as the empty layer** behind every metadata-only instruction | `gotchas.md:94-97` | — (external fact; no spec line — verify against the OCI image spec) | `Trim to ~3 + link` — the one clause `scope.md` rules kept | |
| D-21 | `tar`'s `-b` blocking factor makes every block-aligned zero length legitimate (`tar -b 64` writes a 32 768-byte empty archive) | `gotchas.md:93-95` | `format-tar:125` | `Trim` — the derivation leaves; recorded so it is a decision | |
| D-22 | **Per-member extraction outcomes are not diagnostics**: `ExtractionReport.results` is the **sole** record of what happened to each member — blocked, failed, collided, renamed, rewritten | `errors-and-diagnostics.md:63-69` | `diagnostics:153` (admission), `safe-extraction:595` | `Trim to ~6` | |
| D-23 | Practically: **read `results`, not `report.diagnostics`** | `errors-and-diagnostics.md:71-72` | `diagnostics:153`, `safe-extraction:755` | `Trim to ~6` — the actionable sentence | |
| D-24 | The summary still carries what was observed **while reading** during extraction (invalid timestamps, unresolvable symlinks, unverifiable digests, stream rewinds) — the events with no per-member result to live on | `errors-and-diagnostics.md:72-74` | `diagnostics:115`, `src/archivey/diagnostics.py:74-78` | `Trim to ~6` | |
| D-25 | Escalation is not lost: **`abort_on` is the named opt-in** for being stopped by a blocked member, a collision, or a name rewrite | `errors-and-diagnostics.md:76-78`, `extracting.md:113-122` | `safe-extraction:951` | Keep | |
| D-26 | `[code]` the `DiagnosticPolicy.strict()` block runs, and its `import` resolves `ArchiveyConfig, DiagnosticPolicy, ARCHIVE_INTEGRITY_CODES` | `errors-and-diagnostics.md:85-89` | `src/archivey/diagnostics.py:335`, `:470` | Keep, tighten | |
| D-27 | **`DiagnosticPolicy.strict()` raises on `ARCHIVE_INTEGRITY_CODES`** — the codes reporting the archive's own bytes or metadata as anomalous — and collects the rest | `errors-and-diagnostics.md:91-92` | `diagnostics:333`, `src/archivey/diagnostics.py:486-500` | Keep, tighten | |
| D-28 | **`DiagnosticPolicy.pedantic()` raises on everything** | `errors-and-diagnostics.md:93` | `diagnostics:333` | Keep, tighten | |
| D-29 | **Exactly five codes are outside the strict set**, and they are `EMPTY_ARCHIVE`, `PASSWORD_ARGUMENT_UNUSED`, `ENCODING_ARGUMENT_UNUSED`, `EXPLICIT_FORMAT_LISTED_EMPTY`, `STREAM_REWIND_REDECOMPRESSES` | `errors-and-diagnostics.md:95-100` | `src/archivey/diagnostics.py:335-360`, `diagnostics:333` | Keep, tighten | |
| D-30 | `ARCHIVE_INTEGRITY_CODES` **is exported**, so a caller can build their own policy from it | `errors-and-diagnostics.md:100-101` | `src/archivey/diagnostics.py:586` (`__all__`) | Keep, tighten | |
| D-31 | **New codes may appear in minor releases**, so `default=RAISE` is not version-stable; `strict()` is versioned alongside the taxonomy and is the recommended strict mode; **removing** a code stays a breaking change | `errors-and-diagnostics.md:103-106` | `diagnostics:333` | Keep | |
| D-32 | `members()` / `scan_members()` assert a **complete** listing and raise on terminal archive damage | `errors-and-diagnostics.md:116-117`, `opening-and-listing.md:155-156` | `documentation:103`, `archive-reading:199`, `error-handling:184` | Keep — canonical home | |
| D-33 | `[code]` the `members_report()` recipe runs, and `report.error` is the documented attribute | `errors-and-diagnostics.md:120-127` | `archive-reading:199`, `src/archivey/diagnostics.py:542` | Keep — canonical home | |
| D-34 | `__iter__` / `stream_members()` **yield the prefix then raise** on the same failures | `errors-and-diagnostics.md:129`, `opening-and-listing.md:158-159` | `error-handling:184`, `archive-reading:199` | Keep — canonical home | |
| D-35 | **Diagnostics alone are not the primary signal** for damage | `errors-and-diagnostics.md:129-130` | `error-handling:184` | Keep | |
| D-36 | **This is not salvage** (no resync past damage); `--salvage` remains reserved | `errors-and-diagnostics.md:130-131`, `cli.md:48`, `migrating.md:173-174` | `cli:247` | Keep | |
| D-37 | **Random-access extract fail-closes before writing** when listing ends in terminal damage | `errors-and-diagnostics.md:131-132` | `error-handling:184`, `safe-extraction:21` | Keep | |
| D-38 | **Errors always come from `read()`, never from `close()`** — a `finally` block cannot mask one | `errors-and-diagnostics.md:139-140` | `compressed-streams:155` | Keep | |
| D-39 | **"To its end" means** `read(-1)`, reading until `read()` returns `b""`, or — for a member with a declared size — reading that many bytes | `errors-and-diagnostics.md:142-143` | `compressed-streams:254`, `archive-reading:435` | Keep | |
| D-40 | **We try to raise on every error we can detect — not on every error.** Some formats store no checksum, and some damage decodes into something valid-looking | `errors-and-diagnostics.md:146-148`, `gotchas.md:56-57` | `compressed-streams:254` | Keep | |
| D-41 | **`CorruptionError` vs `TruncatedError` is a best-effort guess, not a diagnosis** — do not branch on which one you got; `except archivey.ReadError` catches both | `errors-and-diagnostics.md:149-152` | `src/archivey/exceptions.py:121-131`, `compressed-streams:137` | Keep | |
| D-42 | **Bytes delivered before the error are of unknown quality** — not known-good, not known-bad (O-17's worked example, and O-16's safety-claim class) | `errors-and-diagnostics.md:153-156` | `compressed-streams:254` | Keep | |
| D-43 | **A full-length return means the checksum matched** | `errors-and-diagnostics.md:157-158` | `compressed-streams:254` | Keep | |
| D-44 | **A short return with no exception does not mean "complete"**: `read(member.size)` on a truncated member returns quietly; the *next* read raises | `errors-and-diagnostics.md:159-161`, `errors-and-diagnostics.md:189`, `reading-members.md:106-110` | `compressed-streams:155`, `archive-reading:435` | Keep | |
| D-45 | `read(member.size)` **raises on corruption and withholds the chunk that reached the size**, but returns a short buffer on truncation | `errors-and-diagnostics.md:190`, `errors-and-diagnostics.md:195-198`, `reading-members.md:106-109` | `compressed-streams:155` | Keep — D-e's named exception, stays on both pages | |
| D-46 | `[code]` the chunked-loop block runs and `archivey.ReadError` is the right except clause | `errors-and-diagnostics.md:167-175`, `reading-members.md:114-122` | — (executable) | `errors` = Keep, canonical home; `reading-members.md:114-126` = `→ page` (byte-identical duplicate) | |
| D-47 | A plain `stream.read()` with no argument asks for the whole member, so a damaged one **raises and you get nothing back** | `reading-members.md:124-126` | `compressed-streams:155` | `→ page` | |
| D-48 | `VerificationMode.STRICT` **verifies a whole member before returning any of it** | `errors-and-diagnostics.md:177-179` | `compressed-streams:254` | Keep — canonical home | |
| D-49 | The call × failure matrix is correct in all 14 cells, for a member of declared size 500 truncated after 110 | `errors-and-diagnostics.md:183-193` | `compressed-streams:155`, `compressed-streams:254` | Keep | |
| D-50 | **Members with no declared size** cannot self-certify from `read(n)` — use `read(-1)` or read until `b""` | `errors-and-diagnostics.md:200-201` | `format-single-file-compressors:87`, `compressed-streams:254` | Keep | |
| D-51 | **§B row 5's survivor, unwritten:** known third-party exceptions are translated into the `ArchiveyError` tree; unrecognized ones **propagate raw** rather than being swallowed by a catch-all; `OSError` / `KeyboardInterrupt` / `MemoryError` pass through unchanged except where a spec says otherwise; `ArchiveyUsageError` is deliberately outside the tree | *no page states it* — receives `extracting.md:74-75` | `error-handling:259`, `error-handling:84`, `compressed-streams:137`, `CONTRIBUTING.md:221-230` | **Guide** (inbound to `errors-and-diagnostics.md`); `extracting.md:74-75` is `→ page` | |
| D-52 | **§B row 9's survivor, unwritten (`#236`):** `ArchiveyError` / `ArchiveyUsageError` escape archive-derived text **at construction** and `Diagnostic` escapes its `message`, so printing one to a terminal cannot move the cursor or forge output | *no page states it* | `error-handling:273`, `error-handling:311`, `diagnostics:384`, `src/archivey/escaping.py` | **Guide** (inbound, ~3 lines) | |
| D-53 | Archive-derived text is **escaped exactly once** — no double-escaping between library and CLI | *no page states it* | `error-handling:311`, `cli:164` | **Guide** (part of D-52's inbound) | |
| D-54 | **Archivey is stricter than the stdlib about damage**: where `tarfile` and `gzip` often stop quietly, archivey raises or emits a diagnostic, so ported code may start seeing errors | `gotchas.md:59-61`, `migrating.md:90-91` | `format-tar:125`, `error-handling:184` | Keep | |
| D-55 | Prefer `reader.diagnostics` and the extraction report **over logs** — advisories are queryable data | `gotchas.md:105-107`, `errors-and-diagnostics.md:43-45` | `logging:40`, `diagnostics:21` | Keep | |

## D — problems and gaps met while extracting

- **D-8 is a completeness claim the page does not know it is making.** The 7-row subtype
  table is the exception tree's only published reference (21 of the 26 types have no
  `api.md` entry), so an omission there is invisible in a way it would not be if `api.md`
  enumerated them. This is exactly the evidence `scope.md` Q3 says would settle §D's
  shape: the inventory is supposed to say *how much of the table is accurate and how many
  types a reader is ever told about*. Counted here: **26 exception classes in
  `src/archivey/exceptions.py`; the table names 12; `api.md` renders 5.**
- **D-51, D-52, D-53 are silence rows.** They are recorded with an empty `Stated at`
  because a worklist that only lists written prose loses them — the trap the brief names
  three times.

---

