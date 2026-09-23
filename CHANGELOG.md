# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

How to update this file for a release: see
[`dev-docs/release-checklist.md`](dev-docs/release-checklist.md)
(commit walk since the previous tag + performance numbers vs that release).

## [Unreleased]

First public release will be **0.2.0**. Until then, notable work accumulates here;
at cut time the checklist moves this section under a dated `## [0.2.0]` heading.

This repository is the **v2** rewrite. The earlier v1 / alpha line (previously
published from what is now
[`davitf/archivey-old`](https://github.com/davitf/archivey-old)) is a separate
codebase — not a SemVer predecessor of this tree. There is no compatibility
promise with that line; treat `0.2.0` as the first release of this library.

### Added

- Unified archive reading for ZIP, TAR, RAR, 7z, ISO, directory trees, and
  single-file compressed streams (gzip / bzip2 / xz / lzip / zstd / lz4 / compress).
- Safe extraction defaults (`archivey.extract`) with policy-driven path and
  overwrite controls; CLI (`archivey list|test|extract`) as a safer unzip demo.
- Native 7z and RAR metadata readers (stdlib codecs for common 7z filters;
  external `unrar` for RAR member data).
- Four optional extras — `[recommended]` (every format and codec that installs
  everywhere), `[seekable]` (rapidgzip), `[free-threaded]` (the measured GIL-safe
  subset), and `[all]`. There is deliberately no extra per format: member codecs are
  shared across containers. See `docs/formats.md`.
- Declarative corpus + mutation / Hypothesis / Atheris testing contract;
  three-configuration CI (`[all]`, `[all-lowest]`, `[core-only]`).
- Benchmark harness: PR structural gate + change-guarded nightly wall-ratio drift.
- `FormatAvailability.required_source` — the weakest source shape a format can be read
  from, so "can I read this straight from a pipe?" is a query instead of a
  `StreamNotSeekableError` to catch. `StreamCapability` is now ordered
  (`FORWARD_ONLY < SEEKABLE`), so the test is
  `availability.required_source <= reader.cost.stream_capability`.
- **`extra["is_reparse_point"]`** and the matching `ArchiveMember.is_reparse_point`, true
  when the archive recorded a member as a Windows symlink or junction rather than a POSIX
  symlink. Set from metadata the archive always carries — the
  `FILE_ATTRIBUTE_REPARSE_POINT` bit for ZIP and 7z, the redirect type for RAR5, the live
  entry for a directory scan on Windows — so unlike `extra["is_junction"]` it needs
  nothing read from the member's data. Every junction is a reparse point, but a junction
  written by 7-Zip carries only this key, because the tag that would identify it as a
  junction is in data that writer does not store. Not gated on the member's type: an entry
  the archive flags whose data turns out not to be a link buffer is presented as an
  ordinary file, and the flag still records what the archive said.
- **`DecoderLimits`**, on `ArchiveyConfig.decoder_limits`, caps how much working
  memory a decoder may allocate because the *archive's own header* asked for it —
  the 7z PPMd window, the ZIP method-98 megabyte count, the LZMA dictionary size.
  Those numbers are not bounded by the file's size (a 153-byte 7z can ask for 4 GiB)
  and the allocation happens on `open()` and `read()`, which `ExtractionLimits` does
  not cover, so it is a type of its own rather than another bomb guard. Default
  1 GiB, `DecoderLimits.UNLIMITED` to opt out, `ResourceLimitError` when exceeded.
  Enforced so far on both PPMd paths, where the refusal is not optional: under a
  memory cap a rejected allocation kills the interpreter from inside pyppmd instead
  of raising.

### Fixed

- **A Windows symlink in a ZIP or a 7z now reports its real target.** Both formats store
  such a link as a `REPARSE_DATA_BUFFER` — the Win32 structure, not a bare path — and
  neither backend parsed it. 7z decoded those ~92 binary bytes as UTF-8 and handed the
  result back as `link_target`, which extraction would then have used as a path; ZIP did
  not recognise the member as a link at all and presented the buffer as its file content.
  Both now decode it, which also yields `extra["is_junction"]` from the reparse tag.
  A directory reparse point — an NTFS junction, or a directory symlink — is surfaced as a
  link with `link_target` unset and a `SYMLINK_TARGET_UNAVAILABLE` diagnostic, because
  7-Zip stores no reparse data for those at all: measured, not assumed, against archives
  built on Windows (`tests/fixtures/external/README.md`). For the same reason
  `is_junction` stays unset for a junction written by 7-Zip — the tag that would identify
  it is in the data the writer discarded. That diagnostic is in
  `ARCHIVE_INTEGRITY_CODES`, so a strict policy refuses such an archive rather than
  reading a link whose target is gone; previously 7z reported an empty target for it and
  ZIP reported a directory, and neither said anything.
- **A link for which the archive records no target no longer fails extraction.** It is
  recorded as the new `ExtractionStatus.LINK_TARGET_UNAVAILABLE` and the rest of the archive still
  extracts, under either `OnError` value — nothing can be written for such a member, and
  nothing about the extraction went wrong. It also no longer disturbs an existing
  destination: the check happens before overwrite resolution, so `OverwritePolicy.REPLACE`
  does not unlink an entry for a member that is not going to be written. A link whose
  target the archive *does* carry but the reader could not reach — encrypted, compressed,
  split across volumes, or, in a streaming read, stored in data the pass has gone past —
  stays the per-member failure it was, because dropping it under a
  non-failure status would report success while losing a member the archive describes in
  full. Either way the loss is reported as `SYMLINK_TARGET_UNAVAILABLE`, which a strict
  `DiagnosticPolicy` refuses. Only ZIP used to report it: 7z returned quietly on an
  encrypted link, and RAR3/4 did the same whenever the target's bytes were out of reach,
  which now names which of four causes it was (encrypted, split across volumes, compressed
  rather than stored, or absent). Previously extraction raised `LinkTargetNotFoundError`
  for the member, which under the library default aborted the whole operation.

### Changed

- **`ArchiveMember.extra` / `ArchiveInfo.extra` are `MemberExtra` / `ArchiveInfoExtra`.**
  Known keys narrow on a subscript read. Assign a `MemberExtra({...})` rather than a
  bare dict; mutating the existing bag in place is unchanged. Only type-checking
  changes: both are `dict[str, object]` subclasses, equal to the plain dicts they
  replace, and `copy`, `deepcopy`, `pickle` and `json.dumps` behave as before.
- **A RAR member whose stored name is a glob is refused by default when the glob also
  matches an earlier member.** `unrar` addresses a member by an include mask built from
  its name, so a name containing `*` or `?` makes it decompress every match and emit
  them concatenated. Names like this are almost always constructed. On a non-solid
  archive the extra decode is also unbounded and outside `ExtractionLimits`, which do
  not reach `open()` / `read()`. Such a read now raises `UnsupportedFeatureError`. On a
  non-solid archive the error names the byte count; on a solid archive those bytes are
  already inside `AccessCost.SOLID`, so the error names the flag rather than an
  avoidable extra decode. Set `ArchiveyConfig.rar_allow_glob_member_concatenation=True`
  to restore the old behaviour. A glob name that matches no other member is unaffected,
  and so is a solid `stream_members()` pass, which builds no mask at all.
- **`password=` no longer raises on a format with no encryption.** All three forms — a
  single value, a list of candidates, a `PasswordProvider` — are now accepted, never
  consulted, and recorded as a `PASSWORD_ARGUMENT_UNUSED` diagnostic. Previously a
  static value or a list raised `UnsupportedOperationError` while a provider callable
  opened fine; the permissive behaviour already existed and was reachable only by
  wrapping your password list in a lambda. `password=` is a keyring offered, not an
  assertion that this archive is encrypted, and a batch caller passing one keyring
  across mixed input should not fail on the one plain `.tar`. A *wrong* password on an
  *encrypted* archive still raises `EncryptionError`.
- **`STREAM_REWIND_REDECOMPRESSES` is now cost-based.** It used to fire on the codec's
  *identity*, decided once at open, so xz / lzip / unix-compress never emitted — even
  though a single-block `.xz` (what `lzma.compress` and un-threaded `xz` produce) has one
  seek point at the origin and rewinds exactly like a codec with no index. Measured while
  fixing this, `rapidgzip`'s index has the same property: three block offsets across a
  5 MB stream, so a backward seek can discard megabytes with the accelerator engaged and
  the old rule said nothing there either. The predicate is now the decoded progress the
  rewind discards, against an absolute 1 MiB threshold, uniformly across codecs — so
  small rewinds that used to warn are now quiet, and large ones that used to be silent
  now report. The diagnostic is still **recorded** once per stream, but a `RAISE` policy
  is now evaluated on **every** qualifying seek: a tripwire that disarms after firing once
  is not a tripwire.
- **`strict_archive_eof=True` now asserts what it documents.** It used to check only
  that the two-block TAR trailer was present, so 4 KiB of arbitrary appended bytes passed
  silently under the flag you set for "a provably complete listing". Every byte from the
  trailer to EOF must now be zero; the first non-zero one emits the new
  `ARCHIVE_TRAILING_DATA` diagnostic and raises `CorruptionError`. Zero padding still
  passes (`tar` writes 10 KiB records), and concatenated archives now fail — deliberately,
  since they are two archives and only the first was listed. **The flag is now
  O(tail length)** rather than O(512 bytes), and on a compressed tar the tail is
  decompressed to inspect it; `strict_archive_eof=False` is unchanged, including the cost.
- Six new diagnostic codes (simplicity & consistency review): `EMPTY_ARCHIVE`,
  `EXTENSION_FORMAT_UNCONFIRMED`, `EXPLICIT_FORMAT_LISTED_EMPTY`,
  `PASSWORD_ARGUMENT_UNUSED`, `ENCODING_ARGUMENT_UNUSED`, and
  `MEMBER_NAME_BIDI_CONTROL` — which promotes the library's last log-only advisory to
  queryable, escalatable data.
- `encoding=` passed to a backend that decodes names another way (7z, RAR, ISO,
  directory, single-file) is still accepted, but the discard is now recorded rather
  than silent.
- Four refusals that crossed the API untyped or mistyped now match the spelling the
  rest of the library already uses (simplicity & consistency review, F3/F4/F11):
  `open_archive([])` and an empty volume-path sequence raise `ArchiveyUsageError`
  instead of a bare `ValueError`; a non-seekable volume in a sequence raises
  `StreamNotSeekableError`, matching the single-source refusal; closing the handle
  under a live ZIP reader raises `ArchiveyUsageError` instead of `CorruptionError`
  (a lifecycle fault, not archive damage); and `open_stream()` on a directory says so
  instead of `FileNotFoundError: Compressed stream not found`.
- `member.compressed_size` on a single-file compressed archive is filled from any
  **seekable** source, not only from a `Path` — the same rule the trailer/CRC probes
  beside it already used. A non-seekable source still reports `None`.
- `seekable_members` no longer changes member **metadata** (review F1). The `.xz` stream
  index and the `.lz` trailer are read from any seekable source, so `member.size` and
  `member.hashes` are the same with and without the flag — `.lz` now reports its
  whole-member CRC-32 on a plain `open_archive()`, which is what the dedupe use case
  wanted. A pipe still reports `size=None` and no digest; nothing forces a decode pass.
- Performance claims are **aspirational peer-ratio bands** with a published
  measured table in `docs/access-and-cost.md` / `VISION.md` (nightly realistic ratios;
  refresh at release time per the checklist).
- GitHub repository renamed from `archivey-2` → `archivey` (canonical name);
  the prior v1 repo was renamed to `archivey-old`.

### Security

- **7z `NumUnpackStreams` no longer allocates an unbounded list.** `kNumUnPackStream`
  was not bounded by remaining header bytes: with no `kSize`/`kCRC`, the parser did
  `[None] * N` (and `[True] * N` on the CRC all-defined path) from a few header
  bytes. N = 2²⁰ allocated in 0.016 s; N = 2⁴⁰ was an untranslated `MemoryError`.
  Unpack-stream, pack-stream, folder, and file counts now reject against the
  header buffer size (`CorruptionError`). Folder, unpack-stream, and file
  counts also reject against `listing_limits.max_members` (`ResourceLimitError`;
  `None` / `ListingLimits.UNLIMITED` disables that bound) before that
  allocation. Pack streams keep the header-size bound only — a BCJ2 folder has
  four, so `max_members` is not applied to that count.
  Per-folder coder counts still reject above `_MAX_NUM_STREAMS` (65536).
  Threat-model O13.
- **7z encoded-header decode no longer hangs on a self-copy.** A 66-byte COPY
  encoded header whose packed bytes are itself looped until killed (`7z l` reports
  "Headers Error" in ~0.2 s). One encoded layer is unrolled (a second
  `EncodedHeader` is `CorruptionError`); folder unpack sizes
  are summed against the 64 MiB next-header cap, not only per folder. Threat-model
  O14.
- **RAR member-table ceiling follows `listing_limits.max_members`.** The native
  parser used a hardcoded 1 048 576 cap (`CorruptionError`) independent of
  config. `open_archive` now raises `ResourceLimitError` when the archive has
  more members than the reader's `listing_limits.max_members`; `None` /
  `ListingLimits.UNLIMITED` lifts the bound, which the old hardcoded ceiling
  did not allow. `stream_members()` / `streaming=True` are not an escape
  hatch (the table is built at open). Threat-model O1.
- **Encrypted 7z password confirmation no longer materialises the folder.** 7z AES
  has no check value, so a candidate is still judged by decoding and CRCing, but
  confirm now streams 64 KiB chunks instead of `read_exact`ing the whole folder
  (~3× unpack size: 630 MB peak on a 200 MiB LZMA+AES archive). `ExtractionLimits`
  still do not cover this path, and wall time is unchanged: up to
  `folder_size × candidate_count` for store/copy+AES, where nothing rejects a wrong key
  before the CRC. Compressed folders are far cheaper — the codec rejects a wrong key
  within a few bytes, and confirmation stops at the first member CRC that fails.
  Threat-model O12.
- **Bidi override filenames are refused during extraction.** A member name or link
  target containing a Unicode bidi override or isolate (U+202A–202E, U+2066–2069) is
  rejected with the new `DeceptiveNameError` — those characters reorder surrounding
  text, which is how `evil‮gnp.exe` displays as a `.png`. The three *directional marks*
  (U+061C, U+200E, U+200F) are deliberately **not** rejected: they reorder nothing and
  appear in legitimate Arabic and Hebrew filenames. Listing and reading still present
  every name exactly as stored, with `MEMBER_NAME_BIDI_CONTROL`.
- Threat model and open residuals: `dev-docs/threat-model.md`.
- Root [`SECURITY.md`](SECURITY.md) — private vulnerability reporting via
  [GitHub Security Advisories](https://github.com/davitf/archivey/security/advisories/new),
  scope, and guidance that optional `[seekable]` accelerators are not part of
  the defended fuzz surface for hard-latency untrusted input.

<!--
After 0.2.0 is tagged, add:

## [0.2.0] - YYYY-MM-DD

…and link compare URLs at the bottom, e.g.:

[Unreleased]: https://github.com/davitf/archivey/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/davitf/archivey/releases/tag/v0.2.0
-->
