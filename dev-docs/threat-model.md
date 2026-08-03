# Threat model and security/compatibility gap register

> The trust boundaries archivey defends, what is already enforced, and — importantly —
> the **known open gaps** identified in the 2026-07 architecture review, recorded here so
> they are not lost. Each open item should become an OpenSpec change (usually a
> `safe-extraction` or `archive-reading` delta) when tackled; this document is the
> holding area and the rationale, not the normative spec.

> **The user-facing half of this document is published.** Trust boundaries and the
> full "what is enforced" list now live on
> [`docs/safe-extraction.md`](../docs/safe-extraction.md) (review/docs `DECISIONS.md`
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
`open_archive()` before spine listing caps apply.

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
and defers to the local OS): apply the `OverwritePolicy` deliberately, record
`requested_path` on the `ExtractionResult` plus an `EXTRACTION_NAME_COLLISION`
diagnostic, and support `OverwritePolicy.RENAME` (extract as `photo (1).jpg`, counter
before the suffix). Only content-bearing members (file/symlink/hardlink, including the
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
deterministic per-OS, collision-tracked, and surfaced as an `EXTRACTION_NAME_SANITIZED`
diagnostic (an all-dots segment like `...` has no portable spelling and is still
rejected); `STANDARD`/`TRUSTED` keep it faithful.

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
