# Threat model and security/compatibility gap register

> The trust boundaries archivey defends, what is already enforced, and — importantly —
> the **known open gaps** identified in the 2026-07 architecture review, recorded here so
> they are not lost. Each open item should become an OpenSpec change (usually a
> `safe-extraction` or `archive-reading` delta) when tackled; this document is the
> holding area and the rationale, not the normative spec.

> **The user-facing half of this document is published.** Trust boundaries and the
> full "what is enforced" list now live on
> [`docs/extracting.md`](../docs/extracting.md) (review/docs `DECISIONS.md`
> D8) — unpublishing was about *audience*, not secrecy, and an evaluating user is
> exactly who needs the enforced-guarantees statement. What remains here is the
> maintainer register: what is still open, and what is left to implement.

## OPEN gaps — security

### O1. Listing-time resource exhaustion (metadata bombs) — mitigated

`ListingLimits` on `ArchiveyConfig` (`max_members`, `max_metadata_bytes`) are enforced
when members are registered into a materialized / resolved list (`members()`,
`scan_members()`, extract-prep materialization). Crossing a cap raises
`ResourceLimitError`. Defaults match extract `max_entries` on the count side
(`1_048_576`) and budget 64 MiB of retained string/bytes metadata.
`stream_members()` / forward-only iteration remain unguarded by design (O(1) escape
hatch). Format-local parser bounds (e.g. 7z `num_files` vs header size →
`CorruptionError`; RAR member-count ceiling at parse) stay as defense-in-depth.
Indexed formats (7z/RAR) may still allocate up to those parser ceilings during
`open_archive()` before spine listing caps apply. `max_metadata_bytes` budgets
*retained* member metadata; it does not see a transient decode buffer discarded
before any member exists. RAR3 compressed Unicode names used to expand ~100×
that way (listing-time CPU at the `uint16` `name_size` ceiling, not unbounded
memory). Decode now fails closed on overrun (PR #292); O1's status is unchanged.

`read()` / `open()` stream sizes remain unbounded (follow-on); prefer chunked
reads for untrusted member payloads.

### O2. Case-insensitivity and Unicode-normalization collisions at extraction — implemented

Two members whose names differ only by case (`README` / `readme`) or Unicode
normalization form (NFC vs NFD `café`) are distinct in the archive but the **same file**
on default Windows/macOS filesystems. Pre-fix behavior under `OverwritePolicy.ERROR`
was a confusing "already exists"; under `REPLACE`, a silent merge on case-insensitive
systems only.

**Implemented** (`cross-platform-name-safety` / ADR 0013 / PR #109): the coordinator
tracks a casefolded+NFC key per written path and, under `STRICT`/`STANDARD`, treats a
collision as a first-class event on **all platforms** (`TRUSTED` keys on the exact path
and defers to the local OS): apply the `OverwritePolicy` deliberately, record the
outcome on the `ExtractionResult`, and support `OverwritePolicy.RENAME` (extract as
`photo (1).jpg`, counter before the suffix). Since
`extraction-results-authoritative`, `results` is the **sole** record — there is no
collision diagnostic. A `REPLACE` merge revises the clobbered member's result to
`ExtractionStatus.OVERWRITTEN` (`path=None`, `requested_path` kept as the join to the
member that took the destination), so the case-insensitive merge is observable rather
than two members both reporting `EXTRACTED` at one path; a `RENAME` shows as
`requested_path != path`. A caller who wants a collision to be **fatal** passes
`abort_on={AbortOn.NAME_COLLISION}`, which fires on every non-`TRUSTED` collision
whatever resolution follows. Only content-bearing members (file/symlink/hardlink, including the
deferred orphan-hardlink pass) are tracked; **directories are intentionally untracked**
(they merge structurally), so a *file* `Foo` vs a *directory* `foo/` collision stays
OS-dependent — a known, deferred residual (ADR 0013).

### O3. Windows name mangling: reserved names, trailing dots/spaces — implemented

`CON`, `NUL`, `COM1`… are device names; `foo.` and `foo ` are silently stripped by
Win32 to `foo` (silent clobber / mismatch between reported and actual path).

**Implemented** (ADR 0013, revised 2026-07 / PR #109 + #123): reserved device names and
`:` are *unsafe* (device capture / NTFS ADS) → rejected under `STRICT` and `STANDARD` on
every platform. A trailing dot/space is a *legitimate* macOS/Linux name Win32 merely
trims → `STRICT` **strips** it to the portable spelling (`stuff_etc.` → `stuff_etc`),
deterministic per-OS, collision-tracked, and recorded as
`ExtractionResult.presented_name` — the full relative name *before* the rewrite, which
is the only signal that survives a caller `filter` rename (archive name, filter output,
and on-disk spelling are three different strings). An all-dots segment like `...` has
no portable spelling and is still rejected; `STANDARD`/`TRUSTED` keep it faithful. A
caller who refuses any rewritten on-disk name passes
`abort_on={AbortOn.NAME_SANITIZED}`, documented as a narrow escape hatch rather than
part of ordinary strict extraction.

### O4. NTFS alternate data streams — implemented (folded into O3)

A member name containing `:` (`file.txt:hidden`) would write an invisible alternate data
stream on NTFS.

**Implemented** as part of O3: `:` in names is rejected under `STRICT` and `STANDARD` on
all platforms (it is never a portable filename character).

### O5. Fuzzing — mutation + Hypothesis + Atheris gate landed; OSS-Fuzz later

The safety claims rest on curated tests plus three complementary fuzz layers. Remaining
work before any public "safe" claim is release packaging (OSS-Fuzz onboarding);
disclosure docs are in place (`SECURITY.md`). The in-tree gate:

1. **Landed:** the corpus **mutation harness** (`tests/test_mutation_fuzz.py`) — every
   corpus archive is deterministically mutated (truncations, bit flips, zeroed blocks,
   garbage prefixes/suffixes) and driven through open/list/read/extract + detection,
   asserting *typed `ArchiveyError` or success — never a raw exception, never a hang*. It
   exercises archivey's own **deterministic zero-dep parsing path** (accelerators forced
   off) and already found and fixed a batch of untranslated-exception bugs in the ZIP and
   ISO backends. `ARCHIVEY_FUZZ_MUTATIONS` deepens the sweep; green at 500 mutations/kind.
   Env-gated 7z parser mutation (`ARCHIVEY_FUZZ=1` / `tests/fuzz_sevenzip_parser.py`)
   remains available for local deepening.
2. **Landed:** property-based tests (Hypothesis) for the pure safety logic
   (`tests/test_property_safety.py` — `normalize_member_name`, `check_universal`,
   `resolve_link_target_name`, volume discovery, detection over arbitrary prefixes).
3. **Landed:** coverage-guided **Atheris** harness (`tests/atheris_fuzz/`) over native 7z
   and RAR header parse (CRC mutate-then-fixup), 7z/RAR open+members (CI installs
   RARLAB `unrar` so RAR open is not skipped), `detect_format`, ZIP open+list+bounded
   member read (native codec/AES), TAR/ISO open+list, and standalone stream/codec
   targets (unix-compress, xz, lzip, gzip, bzip2, lzma-alone, zlib; optional
   zstd/brotli/lz4/deflate64 skip-clean when absent). CI runs a **short** partition on
   every **pull request** (sharded for wall time), and the **full** partition on a
   **change-guarded nightly** (skip unless default-branch HEAD moved in ~3 days) plus
   **`workflow_dispatch`** — same pattern as the benchmark wall job; not an always-on
   nightly and not a full run on every `main` push. `atheris` lives in the PEP 735
   `fuzz` dependency group only — never a runtime extra. See
   `openspec/specs/testing-contract/spec.md`.
4. **Landed (disclosure):** root
   [`SECURITY.md`](https://github.com/davitf/archivey/blob/main/SECURITY.md) —
   private reporting via GitHub Security Advisories (preferred), scope, and caller
   guidance (including accelerator-off for hard-latency untrusted input).
5. **Still open (public release):** OSS-Fuzz onboarding. Accelerator hang sandbox
   (below) remains a separate follow-up.

**Accelerator hang (found by the mutation harness).** The optional `[seekable]`
accelerators (`rapidgzip`, and its bundled bzip2 decoder) are third-party C++ that can
**busy-loop on crafted input** — a hang no Python-level translator can convert into an
`ArchiveyError`, and one that SIGALRM/pytest-timeout cannot cleanly interrupt (the loop is
in a C++ thread). So the mutation and Atheris harnesses run with accelerators **off**, and
fuzzing that native code is deferred to a **resource-limited subprocess sandbox**
(wall-clock + memory capped, killed on breach). Until then: the accelerators are an
opt-in performance path, not part of the defended parsing surface for untrusted input —
callers processing untrusted archives under a hard latency budget should leave them off
(`AcceleratorMode.OFF`) or enforce their own timeout. Surfaced in
[`SECURITY.md`](https://github.com/davitf/archivey/blob/main/SECURITY.md).

**pycdlib directory-cycle hang (found by the mutation harness).** `pycdlib` can **loop
forever** in ``_walk_directories`` whenever corrupt directory records form a back-edge
(plain ISO 9660 PVD, Rock Ridge PVD, Joliet SVD — any namespace ``open_fp`` walks). The
harness found a Joliet case (`bitflip@71746:0x01` on `basic-iso`); the same one-bit
corruption in ``/subdir``'s directory extent reproduces on plain-only and Rock-Ridge-only
images built the same way (`tests/test_iso.py::test_pycdlib_directory_cycle_does_not_hang`
parametrizes all three). The ISO backend installs a one-time guard that skips
re-enqueueing a directory extent already scheduled (valid trees never revisit an extent).

**Destination-root poisoning via `"."` file member (found by the mutation harness).**
Corrupted headers can surface a *file* (not a directory) whose normalized name is `"."`
— e.g. `bitflip@107:0x10` on `adversarial-tar.tar.gz`. Extracting it would write through
the destination path itself, replacing the extraction directory with a regular file
("poisoned dest"). `check_universal` now rejects non-directory members that name the
extraction root; the parametrized fuzz loop also asserts the destination stays a
directory after any successful extract. Unit coverage:
`test_check_universal_rejects_root_named_file` and `test_extract_error_when_dest_is_a_file`
in `tests/test_extraction.py`.

### O6. Nested-archive amplification

Opening archives-inside-archives is supported (and `size` advertisement makes it
cheap); recursion is caller-driven, so a zip-quine (`droste.zip`) only loops if the
caller loops. Still worth an explicit documented stance + a recipe for bounded
recursive processing, since "index my backups" — the founding use case — does exactly
this.

### O7. Names representable as bytes but not by the target filesystem — implemented

`check_universal` rejects names that cannot be `os.fsencode`d at all (a lone surrogate
outside the surrogateescape range — see `internal/filters.py`). Names that *are*
fsencodable but that some filesystems refuse at `write()` (e.g. surrogateescape
`caf\udce9.txt` → `EILSEQ` on APFS) used to surface as a platform-dependent per-member
write failure.

**Implemented** (ADR 0013 / PR #109):

- Write-time `OSError` (`EILSEQ`) for a filter-accepted but unrepresentable name is
  translated to a typed `ExtractionError` naming the member
  (`test_unrepresentable_name_oserror_is_translated`).
- Under `STRICT`/`STANDARD`, non-UTF-8 bytes are **percent-escaped** to a deterministic
  reversible portable spelling (`%XX`; literal `%` → `%25`), applied on every platform
  and collision-tracked like O2; only names that cannot be `os.fsencode`d at all are
  still rejected. `TRUSTED` attempts the faithful bytes and lets the local OS decide.

Residual: a public un-escape helper is deferred (addable non-breakingly). User-facing
notes: [Gotchas — Extraction](../docs/gotchas.md#extraction), ADR 0013.

### O8. 7z wrong header-decryption password can silently yield an *empty* archive — mitigated

The 7z format has **no password check value** (unlike RAR5's `pswcheck` or WinZip
AES's verifier bytes), so wrong-password detection on a header-encrypted archive
(`-mhe=on`) is heuristic: decrypt with the derived key, LZMA-decode the garbage,
and rely on the decode or the header parse failing. There are two lines of
defense today:

1. **Decoded-folder CRC** — `decode_folder_to_bytes` verifies the encoded-header
   folder's `kCRC` digest when the writer stored one
   (`sevenzip_pipeline.py`). Reference 7-Zip writes it → detection is
   deterministic (2⁻³²) for those archives. **py7zr does not**
   (`digest_defined: False` on the encoded-header folder), and the only 7z archives
   *we* produce are test fixtures written through py7zr (7z writing is not shipped),
   so those share the gap.
2. **Structural parse failure** of the garbage — which usually works, but not
   always.

Measured (2026-07-18, loop of fresh py7zr header-encrypted archives, wrong
password): **~0.3% of salts slip through both checks** (2/300, 3/1,110 across
runs — the AES IV/salt is random per write, so the rate is per-archive, not
per-attempt). Every observed slip-through decodes to a degenerate header that
parses as **an archive with zero members**: `open_archive(...,
password="wrong")` returns member_count=0 with no error. Hazard: not traversal
or corruption, but *silent data invisibility* — a backup-verification or sweep
tool concludes "empty archive" instead of "wrong password" and reports success.
This is also the root cause of the flaky
`test_header_encrypted_wrong_password_mentions_header` (seen on Windows
py3.14 CI for PR #139; the flake predates that PR — same rate measured on
`main`).

*Mechanism (not a `num_files == 0` field):* wrong-key AES still feeds LZMA, which
often emits the full claimed unpack size (e.g. 105 bytes) of garbage.
`parse_header_block` only inspects the **leading property id** and stops:

- `0x00` (`END`) → empty `PlainHeader` immediately; the remaining ~104 bytes are
  **never read**.
- `0x01 0x00` (`HEADER` + `END`) → `_parse_plain_header` exits on the next `END`
  with empty streams/files; trailing garbage likewise ignored.

Empirically (8 slips in ~2k py7zr writes): 7/8 were leading `END`, 1/8 was
`HEADER`+`END`. Rate ≈ 1/256 matches “first garbage byte is `0x00`”. A real
decoded header for the same fixture is a full 105-byte structure
(`HEADER` → `MAIN_STREAMS_INFO` → `FILES_INFO` → `END`) with **no** trailing
unread bytes — so the slip is an early terminator in random output, not a
zeroed `FILES_INFO` count.

*Mitigation:* after decoding a `kEncodedHeader`, a parsed result with **zero file
records** is treated as a rejected password (`EncryptionError`). Legitimate writers
never encrypt an empty header (empty archives use `nextHeaderSize == 0` or a plain
header). Residual: garbage that parses into a *non-empty* plausible header survives
in principle (inherent to the format absent a check value); requiring the parser to
consume the entire decoded buffer (reject trailing bytes), stricter property
bounds, and upstream py7zr writing `kCRC` for the encoded-header folder remain
optional hardenings. See `format-7z` ("never a silent empty listing") and
`test_header_encrypted_empty_decoded_header_rejected`.

### O9. Attacker-controlled bytes reaching the terminal via messages — implemented

Member names are attacker-controlled, and a name may carry ANSI control sequences: a
`README\x1b[2K\rSUCCESS.txt` printed raw lets the archive erase the line it is being
reported on and author what the operator sees in its place. `cli/format.py`'s
`escape_member_name` exists for this (GNU `ls` / `tar` quote for the same reason). PR #235
(whose subject is `extraction-results-authoritative` — the escaping rode in on it) routed
the **report-line** print sites through it: the report lines themselves, the error detail
appended to `failed:` / `blocked:`, and the hoist's messages.

**Implemented** (`escape-cli-log-records`): archive-derived text is escaped where it
**becomes a message**, not where a message is displayed. `ArchiveyError` and
`ArchiveyUsageError` escape their `message` at construction, `Diagnostic` escapes its
`message`, and the primitive moved to `archivey/escaping.py` so both can reach it. The
guarantee is written down as the `error-handling` and `diagnostics` requirements
*"… messages are inert for terminal display"* and the `cli` requirement *"Archive-derived
text is escaped before terminal display"*. The same change escaped the print sites that
render an **exception** rather than a report line — `archivey test`'s `FAIL` detail, the
extract abort notice, and `main()`'s top-level handlers — which the report-line pass had
left raw. The gap it closed is below.

The library's own `logging` records used to bypass the print-site escaping.
`extraction.py` emits

```python
logger.warning("Skipping %s %r: %s", original.type.value, original.name, error)
```

and `cli/logging_config.py` attaches a `StreamHandler` to the same stderr the reports go
to. The name is safe there by accident — `%r` makes Python's `repr` escape it — but the
**third** field is not: an `ExtractionError` message embeds the destination path, and that
path is built from the member's name. Reproduced on `main`:

```
WARNING: Skipping file 'EV\x1b[2KIL\rHARMLESS.TXT': Destination already exists: /…/out/ev<ESC>[2Kil<CR>HARMLESS.txt
failed: EV\x1b[2KIL\rHARMLESS.TXT: Destination already exists: /…/out/ev\x1b[2Kil\rHARMLESS.txt
```

The second line is the fixed print site; the first is the same fact, unescaped, one line
earlier. Any library log record or exception message embedding a member-derived path has
the same shape — this is not specific to that one call.

**Not platform-specific, and not gated on the write succeeding.** Windows refuses control
bytes in filenames (`WinError 123`), but the failure path *is* a reporting path: the name
still reaches stderr, in the log line reporting that it could not be written. The same
holds for any member that is blocked, superseded, or listed rather than extracted.

*Why the message and not the handler.* The first attempt escaped in a `logging.Formatter`
installed by the CLI. That guards only records reaching a handler someone configured, and
the likeliest route an archivey message takes to a terminal has no handler at all: an
uncaught exception, with the interpreter printing the traceback whose final line is
`str(exc)`. `print(exc)` in embedding code and third-party error reporters are the same
shape. Escaping at construction covers every route with no configuration, and it needs one
place — `ArchiveyError.__init__` — rather than one per call site.

The two layers cannot coexist: a formatter escaping an already-escaped message doubles
every backslash in it. The formatter was removed, and `format_error_detail` in the CLI
decides by type in one place, so a call site holding an `ArchiveyError | OSError` union
(`ExtractionResult.error`) does not have to. Only non-archivey exceptions are escaped at
the display site, because only they arrive unescaped.

*What stays raw.* The exceptions' `archive_name` / `member_name` / `source_format` and
`Diagnostic.context` — the structured channels, for callers acting on a value rather than
printing it. Library log records are likewise unaltered, so an embedding app's handler or
a test's `caplog` still sees exactly what was emitted.

*Closed residual — `exc_info` tracebacks.* The handler-side design had to accept that a
rendered traceback's final line is the exception's message and may be archive-derived.
Escaping at construction closes it: that line is the escaped message.

*Native paths in messages.* Escaping doubles a backslash, so a native Windows path
interpolated raw would render `C:\\Users\\out\\a.txt`. Every path in a message is
rendered `/`-separated first by `escaping.display_path()`, leaving the escape nothing to
double; a backslash that survives is then a character in a *name*, which is what the
escape is for. Print sites already followed this rule by rendering relative to the
extraction root. Guarded by a static sweep, since the failure is invisible on Linux.

*Escape exactly once.* Escaping already-escaped text doubles the backslashes the first
escape wrote. Review found this was not a rare cosmetic edge: **52 message sites**
interpolated an archive-derived name with `{name!r}`, which `repr` escapes before the
message escape escapes its backslashes — essentially every safety error in `filters.py`,
`extraction.py`, `base_reader.py` and `reader_state.py`. `escaping.quoted()` supplies the
delimiting quotes without escaping, and all 52 were converted; `raw_message` /
`raw_message_of()` do the same job for a caught exception embedded in a new message (two
broad `except Exception` sites in `rar_parser.py` can catch an `ArchiveyError`). `!r`
stays where the value is not archive-derived.

The **inverse** rule applies to `logger.*` call sites: the CLI handler no longer escapes,
so `%r` is what makes an interpolated name inert there and must be kept. Both rules are
guarded by static tests in `tests/test_escaping.py`, since either failure is invisible
except against a hostile archive.

*Escaping correctness.* Rendering delegates to `repr`, whose escape set is exactly
`not str.isprintable()` (verified across the whole code space), after review found three
bugs in the hand-rolled table: astral code points emitted a five-hex-digit `\uXXXXX` that
reads back as a different character (955,086 code points affected); the surrogateescape
range was `U+DC00`–`U+DFFF` rather than the `U+DC80`–`U+DCFF` that `surrogateescape`
actually produces, reversing 768 code points into bytes they never came from; and the
losslessness claim was false, since `U+009B` and a surrogateescaped byte `0x9B` both
render `\x9b`. The guarantee is now stated as inertness, not unique recoverability.

Tests for the fixed print sites: `tests/test_cli.py::test_extract_escapes_*`. Those use a
Windows-legal U+2028 for the cross-platform cases and keep the ANSI/CR spoof in a
Unix-only test, because a name containing control bytes cannot be created on NTFS.

### O10. A content probe fabricates a member from arbitrary attacker bytes — narrowed

Brotli has no magic number, so `detect_format` recognizes it by a content probe. Before
the framing gate that accepted **~8.2%** of random data and **~3.5%** of a real `/usr`
tree. `open_archive` listed one fabricated `<name>.uncompressed` member.

**Mitigation shipped (framing + completeness + chain walk):** when the source length is
known, a first meta-block that declares more bytes than the source holds is rejected; a
fully visible source that does not decode to completion is rejected; and a bounded
self-describing block-chain walk rejects later overruns / trailing bytes. Residual after
`probe-completeness-gate` on a re-measured tree (150 623 files): **29 fabricated claims
(0.019%)**, down from 128 (0.193%) after the first-block gate alone. Probe-only confidence
is `GUESS` for the uncompressed/metadata-first class; a decode failure there sets
`format_unconfirmed=True` and emits `PROBE_FORMAT_UNCONFIRMED`. Structured residual
families named in the investigation (OLE/CFB, COFF) are usually claimed end-to-end by the
**LZMA Alone** probe at `PROBABLE`; after `probe-provenance-unconfirmed` those failures
stamp too — measured, **0 of 29** fabricated probe claims on the re-measured tree carry
no signal.

**Three clauses remain:** the listing can be wrong; a full read raises; **and** a prefix
of fabricated bytes (65 536 measured) may already have been produced before that raise.
Not a silent success — but also not “every read failed with no output.”

Product triage: `open-issues.md` P12. Investigation:
[`investigations/brotli-content-probe-results.md`](investigations/brotli-content-probe-results.md).
Changes: `openspec/changes/archive/2026-08-23-brotli-probe-framing-gate/`,
`openspec/changes/archive/2026-08-25-probe-completeness-gate/`.

**Adjacent and already closed:** the *archive-behind-a-stub* case (Topic 8 A-34) via
`sfx-format-detection`.

### O11. Detection-time decode work is unbounded — open

O1 scopes to *listing*-time metadata bombs; `ExtractionLimits` scopes to `extract`.
Nothing covers the work `detect_format` may do while deciding what a source is.

Measured under a 2 MiB scan window packed with back-to-back decoys: **209 715** valid
gzip headers, and decoding each to a 64 KiB per-candidate cap costs **1.26 s / 1 365 MiB
of successful decoding — 683-fold amplification**. Memory is not the problem (each
candidate's output is discarded); time is. A per-candidate decode cap cannot bound the
aggregate.

`detection-prefix-workspace` ships the `DetectionBudget` / `DetectionCostReceipt` and a
fuzz assertion that aggregate detection cost stays inside the declared budget. The bound
itself — and whether limits are per-detection aggregates or per-candidate — belongs to
`detection-evidence-ledger`, which owns the scan tiers where candidates multiply. Until
that lands, a hostile prefix can still force unbounded decode work under the default
budget's scan path once those tiers are enabled.

## OPEN gaps — compatibility

### C1. The RAR decompressor matrix (and unrar licensing) — won’t-do / closed

RAR member data requires an external tool. `unrar` is **non-free** (freeware license);
`unrar-free` handles little of RAR5; `7z`/`bsdtar` coverage varies by build; `unar`
exists on macOS. A multi-tool fallback matrix would otherwise degrade into "works on my
machine" plus divergent solid/password behavior.

*Decision (closed):* Archivey supports **RARLAB `unrar` only** for RAR member data.
Non-RARLAB binaries on `PATH` raise `PackageNotInstalledError` naming RARLAB `unrar`;
there is no silent fallback to `unrar-free` / `unar` / `7z`. Licensing remains a
documented system dependency (archivey itself stays permissively licensed). See
ADR [`0002-native-rar-metadata-unrar-data`](decisions/0002-native-rar-metadata-unrar-data.md)
and OpenSpec `format-rar`.

### C2. Warnings that should be data — addressed

Addressed by the lifecycle-aware diagnostics capability (`diagnostics-warnings-as-data`):
advisories are immutable `Diagnostic` values with stable codes, attached to
lifecycle-appropriate surfaces (`FormatInfo`, `ArchiveReader`/`ArchiveStream`,
`ArchiveMember`, `ExtractionReport`), with per-code policy (`IGNORE`/`COLLECT`/`RAISE`)
and a shared retention budget. Logging remains the zero-config projection.

### C3. Metadata fidelity boundary (xattrs/ACLs/forks)

PAX xattrs currently survive only inside `extra["tar.pax_headers"]`; ACLs, macOS
resource forks, and NTFS ADS are untouched. Read-side promotion to a first-class field
later is additive/cheap; applying xattrs at extraction is moderate (policy
interactions); true fidelity only binds when **writing** lands (deferred, possibly
post-1.0). Decision recorded in `IDEAS.md`; revisit at writing-spec time.

### C4. Free-threaded Python

`3.13t+` makes data races visible and parallel pure-Python decode realistic.
On readers that declare `MemberStreams.CONCURRENT`, after random-access member
materialization, concurrent `open()` plus independent operations on different member
streams are data-race-free on ordinary builds and on backend/runtime combinations covered
by the required Linux CPython `3.13t` `free-threaded-concurrency` job; optional backends
are not claimed covered until a dedicated free-threaded job can run them. The undeclared
default is one live member stream (a second overlapping open raises `ConcurrentAccessError`),
so accidental cross-thread stream sharing fails fast instead of racing. Iteration,
materialization, extraction, `stream_members()`, and reader close remain single-owner,
with explicit private child scopes allowing extraction to drive its pass and
yielded-stream I/O. Implementation
must use real synchronization rather than relying on the GIL. Parallel extraction scheduling
remains future, and speed claims require measurements proportionate to the mechanism changed.
Accelerator close-before-finalize
(`known-issues.md`) still applies, so member-stream lifecycle leases defer backend teardown
until the final stream closes. See [`parallel-reader.md`](investigations/parallel-reader.md) §4.
