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

- **`ArchiveyConfig.spool_limits`** (`SpoolLimits`, with a `SpoolLimits.UNLIMITED`
  preset): bounds the temp copy a RAR opened from a stream needs so `unrar` can read it.
  `SpoolLimits.max_bytes` defaults to 1 GiB, counted across a volume set. An archive over
  it raises the new `SpoolLimitExceededError`, a `ResourceLimitError` subclass, before
  anything is written; `None` removes the limit. Before this the copy had no bound. Path
  sources are never copied.
- **`ArchiveReader.format_info`**: the `FormatInfo` that `open_archive`'s own detection
  produced (confidence, `detected_by`, `payload_offset`), or `None` under `format=`.
  `archivey info` prints it instead of detecting the file a second time.
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
  2 GiB, `DecoderLimits.UNLIMITED` to opt out, `ResourceLimitError` when exceeded.
  Enforced on both PPMd paths, where the refusal is not optional (under a memory
  cap a rejected allocation kills the interpreter from inside pyppmd instead of
  raising), and on the LZMA dictionary of 7z, ZIP, xz, `.lzma` and lzip, where the
  dictionary fills as output is written and so bounds how much of it stays resident.
- `ArchiveyConfig.read_link_targets` (default `True`). ZIP, 7z and RAR4 store a symlink's
  target as member data, so reading it while listing can decompress data and consult the
  password provider. Set it to `False` and the reader reads none of those targets on its
  own: the links list with `link_target=None`, `extract_all` reads the target of each link
  its selector and filter accept, and `open()` reads the target of a link it follows.
  When extraction is the first to read a link's data, under this setting or in any
  streaming pass, and the data shows a reparse-flagged "link" is really a file,
  `extract_all` calls the filter again on the file and writes it; a streaming pass,
  already past its content, fails that member under `on_error`.
- `ArchiveyConfig.detection_budget` sets how much work format detection may do, as a
  `DetectionBudget` from `archivey.detection_cost` (`BALANCED_BUDGET`, the default,
  `FAST_BUDGET` or `THOROUGH_BUDGET`). It governs `detect_format` and every detection
  `open_archive` and `open_stream` run, which before this always used the default.

### Fixed

- **A truncated gzip, zlib or deflate stream no longer kills the process through
  rapidgzip.** rapidgzip 0.16 aborts (`std::terminate`) on such a stream, whatever the
  source, so `seekable_members=True` on a cut `.gz` of 1 MiB or more, or a cut ZIP
  deflate member read with the accelerator on, ended the interpreter. archivey now runs
  rapidgzip for these codecs in a child process: the read raises `TruncatedError` (or
  `CorruptionError` where the abort does not say why). A child killed by SIGKILL raises
  `ResourceLimitError`; one ended any other way raises `ReadError`. Starting the child
  costs about 45 ms per accelerated stream, so `AUTO` now uses rapidgzip only from
  16 MiB of compressed input (was 1 MiB); smaller streams use the standard library.
  Under `AUTO` a frozen application, which cannot start a child, uses the standard
  library; `ON` there, or a spawn the OS refuses, raises `ResourceLimitError`. bzip2
  stays in-process. `use_rapidgzip=OFF` avoids the child entirely.

- **Pre-1970 Unix timestamps list their date on Windows too.** TAR, the ZIP extended
  timestamp field, RAR, gzip and the directory backend converted Unix seconds with
  `datetime.fromtimestamp`, which goes through `gmtime()` on Windows and rejects a
  negative value, so a member dated 1969 listed as `modified=None` with
  `MEMBER_TIMESTAMP_INVALID` there only. The conversion is now epoch plus `timedelta`
  on every platform; a value outside `datetime`'s range still reports as before.

- **A seek before the start of a member follows `io.BytesIO`.** `seek(-n, SEEK_CUR)` or
  `seek(-n, SEEK_END)` past the start of a compressed member raised `ValueError`, which
  the ZIP backend reported as `CorruptionError` on an undamaged archive; ISO did the same
  for every member, and a RAR member read through `unrar` raised `ValueError`. They now
  clamp to position 0, as stored members already did. A negative `SEEK_SET` offset, or
  an unknown `whence`, raises `ValueError` on every format, directory members included,
  instead of `CorruptionError` on ZIP and ISO. The one difference left is a relative
  seek before the start of a directory member: that member is the file itself, so the
  OS refuses the seek with `OSError`.

- **`max_metadata_bytes` weighs PAX keywords, not only their values.** TAR keeps
  every PAX record in `extra["tar.pax_headers"]`, and a PAX keyword can be as long as
  its value. A member with a 100 000-byte keyword and a one-byte value weighed 4 bytes,
  so a 514 KiB `.tar.gz` could list under a 1 MiB cap while holding about 300 MB of
  keywords. The keys of a dict nested in `extra` now count, and the TAR header walk
  stops at the cap on keywords as it does on values. Top-level `extra` keys are fixed
  per format and still do not count, so no format's baseline weight moves.

- **A corrupt or hostile PPMd member no longer crashes the Python process.** pyppmd
  segfaults when asked to keep decoding after a corrupt stream has ended early, and
  random bytes (a wrong password, or a crafted 7z or ZIP member) reach that state.
  archivey now hands pyppmd a member of up to 16 MiB of compressed data in one piece,
  so it never makes that call, and decodes larger members in a child process, where a
  crash becomes `CorruptionError`; a child killed by SIGKILL (usually the out-of-memory
  killer) or unable to allocate the member's model raises `ResourceLimitError`, and one
  ended any other way (SIGTERM, a plain exit status) raises `ReadError`. Output
  still streams. The new `DecoderLimits.max_ppmd_in_process_input` sets the size; where
  no child process can be started (a frozen app, an interpreter that does not know its
  own path, a spawn the OS refuses, or a child that cannot import pyppmd), a larger
  member raises `ResourceLimitError`, and `None` (as in `DecoderLimits.UNLIMITED`)
  decodes every member in-process.

- **ISO: bootable images read and extract.** An El Torito boot catalog listed as a file
  but raised `CorruptionError` when read, so `extract_all()` stopped on every bootable
  image. Its bytes are now read from the image.
- **ISO: files of 4 GiB or more are no longer cut short.** Such a file is stored in
  several extents, and only the first was listed and read, with no error. `size` and the
  data now cover every extent; extents that are not contiguous are refused with
  `UnsupportedFeatureError`.
- **ISO: a truncated image lists the sizes its records declare, and a cut file raises
  `TruncatedError`.** pycdlib clamps a file running past the end of the image, so a cut
  image listed smaller or negative sizes and read short with no error. The declared
  length is read back from the directory record; a file the cut reaches reads the bytes
  that survive and then raises `TruncatedError`, and files before the cut read normally.
- **ISO: `ArchiveInfo.format_version` is `None`.** It reported pycdlib's guess at the
  interchange level, which reads 3 on nearly every image; ISO 9660 does not store one.
- **A self-extracting 7z whose stub embeds another 7z archive opens the real one.** When
  more than one 7z signature validates, detection now prefers the one whose archive ends
  at the end of the file, and falls back to a shorter one only when nothing ends there.
- **Detecting an empty file says there are no bytes to read**, rather than that nothing
  matched. A handle already read to its end gets the same message.
- **Text files are no longer detected as Brotli.** Brotli has no magic, so detection
  decodes the start of a source to recognise it, and a 256-byte sample let ordinary text
  through: 7 of the first 800 Perl modules under `/usr/share/perl` detected as `BROTLI`
  and then failed to read. The probe now decodes the whole 4 KiB detection window, and a
  content-probe hit on a source up to 64 KiB is checked against the whole source, which
  also turns away a truncated stream the window alone cannot tell apart. The detection
  budget's decode-input limit (`max_decode_input`) now bounds the content probes too, as
  one allowance for the call; a probe's output stays bounded per probe by the codec's
  drain, not by `max_decode_output`.
- **A read that fails on a format guessed from the filename says so.** When magic bytes
  and content probes all declined and the extension decided (40 000 zero bytes named
  `backup.gz`), the read error now has `format_unconfirmed=True`, its message says the
  identification rested on the extension only, and `EXTENSION_FORMAT_UNCONFIRMED` is
  emitted, as it already was for an empty listing. It used to look like a damaged gzip
  file. The same now holds for a real Brotli file named `x.br` that was cut short: once
  less than the detection budget's completion window remains, the probe declines it and
  the name decides, so its read error carries the flag too.
- **A Windows symlink to a network share keeps its `//server/share` target.** A ZIP or
  7z reparse buffer that named its target only as `\??\UNC\server\share` listed
  the link as pointing at the relative path `UNC/server/share`, and extraction created
  that relative link. The target is absolute now, so safe extraction blocks the member
  with `SymlinkEscapeError`, as it does any link that leaves the destination.
- **A 7z AES coder whose properties are one byte, with no salt and no IV, opens.** 7-Zip
  reads that as an empty salt and a zero IV; archivey refused it as corrupt.
- **`FormatInfo` and `DetectionConfidence` are on the API page**, with each field
  documented. Both were public and returned by `detect_format`, but undocumented.
- **A usage error no longer suggests calling the wrong class.** Passing
  `limits=ListingLimits` where an `ExtractionLimits` belongs said "did you mean
  ListingLimits()?"; the hint now appears only when that call would be accepted.
- **Errors keep their real cause in four places where a blind `except` changed it.**
  Reading a bzip2 stream through the rapidgzip accelerator from a file object whose own
  `read` failed aborted the Python process; the file object's error now propagates, as it
  already did for gzip. A corrupt member read with `read()` raised `TruncatedError`
  where `read(n)` raised `CorruptionError`; both now raise `CorruptionError`. A RAR with
  encrypted headers cut inside a header's salt or IV raised `EncryptionError` even with
  the right password; it now raises `CorruptionError`, and a `bytes` password that is not
  UTF-8 counts as a wrong candidate there instead of escaping as `UnicodeDecodeError`.
  An `OSError` or `MemoryError` on the check for data past a member's declared size was
  taken as "no more data"; it now propagates.
- **A reader builds each member once, and every listing method hands out the same
  objects.** `members_report_if_available()`, `members()`, `get()`, `stream_members()`
  and `extract_all()` now share one member list filled by one walk of the archive's
  index, where `extract_all` used to walk it twice and a peek returned objects that
  `members()` then replaced. A `member_id` is set on every member a 7z or solid RAR
  stream pass yields, a link target is filled in on the member you already hold, and
  per-member diagnostics are counted once. Each typing-time diagnostic now carries the
  member's `member_id` on every backend.
- **Streaming extraction handles a name stored twice** the way random access does: the
  earlier entry is `SUPERSEDED` and the later one extracted, where it used to raise
  `ExtractionError` on the second copy. A ZIP, 7z or RAR lists every member before the
  pass starts, so the earlier copy is never written. A streaming TAR has no index, so it
  writes the earlier copy and takes it back when the later one arrives: the later copy
  replaces it, and it no longer counts toward `max_entries`, nor toward
  `max_extracted_bytes` unless a hardlink written in between still holds its bytes.
  The few cases where the result can still differ, all involving something that
  depended on the earlier copy before the later one arrived, are listed in the
  `safe-extraction` spec.
- **Streaming extraction writes symlinks whose target is stored as member data.** A ZIP
  or 7z link reached by a streaming pass before its target had been read failed as
  having no target; the target is now read before the link is written.
- **Listing a 7z reads each folder's link targets in one decode**, up to the folder's
  last link, instead of re-decoding from the folder start for every link. A streaming
  pass reads a link's bytes from its own decoder as it passes the link.
- **An encrypted RAR derives each key once per open.** RAR5 key derivation costs what
  the archive declares, up to 2²⁴ PBKDF2 rounds (a few seconds each). A header-encrypted
  volume set derived the header key and password check again on every part, so a
  four-part set ran eight derivations where two do, and an `-hp` archive repeated the
  header's password check for its member data. One cache per reader now serves the
  header parse, every volume and every member read. RAR3 volume sets re-derived per part
  the same way and are covered by the same cache.
- **A `.Z`, `.xz` or `.lz` source that ends before its first header is now an error**,
  never an empty stream. An empty `.Z`, and a 1–5 byte file read as lzip, used to decode
  to `b""` with no error. The error type now follows the rule for every other codec:
  `TruncatedError` when the source is empty or holds only the start of the format's magic,
  `CorruptionError` when its bytes could not start that format. An empty `.xz` or `.lz`
  therefore raises `TruncatedError` where it raised `CorruptionError` before. Short
  trailing data *after* an lzip member is still allowed, as the lzip format specifies.
- **A `.Z` file cut inside the padding after a CLEAR code now raises `TruncatedError`**
  instead of ending cleanly with a short size. Compressors always write that padding in
  full, so a stream that ends while it is owed was cut.
- **A caller's file object that implements only `read` now works as a compressed source
  of unknown size.** The input counter behind the live decompression-ratio guard called
  the inherited `readinto` of such an `io.RawIOBase` subclass, which raises
  `NotImplementedError`, instead of falling back to `read`; a non-blocking source with no
  data got a bare `TypeError` rather than archivey's `BlockingIOError`.
- **Opening an xz file with megabytes of stream padding no longer takes seconds**: the
  padding is scanned backwards in reads that grow up to 64 KiB, rather than one 4-byte
  read at a time. A stream with no padding still costs a single 4-byte read.
  Listing a `.lz` file holds no per-member state however many members it declares, where it
  used to take about nine times the file's size in memory for a file of empty members.
- **The xz seek index is now as strict as the decoder**: an index whose records do not
  fill its declared length, or a size field written in more bytes than it needs, is
  refused, as liblzma already refused both when decoding.
- **A compressed stream's seek table holds at most 262 144 entries.** A crafted `.lz` or
  `.xz` declaring millions of tiny members, streams or blocks used to grow the table
  without bound, both when a seek built the index and while a forward read recorded
  resume points (the cap covers every codec that records them, `.Z` included). Past the
  cap the table is thinned rather than dropped: points are kept a spacing apart so a
  seek decodes a little further, and a `SEEK_INDEX_DEGRADED` diagnostic says so. The
  data read is unchanged, and real files come nowhere near the cap: `xz -T0` writes
  24 MiB blocks, so 262 144 of them is 6 TiB.
- **Seeking in an `.xz` whose index scan failed no longer returns a short read with no
  error.** When the file had data the index scan could not parse (trailing garbage, for
  example), a seek resumed from block points an earlier forward read had recorded, and
  the read stopped at the end of that stream. A seek into an `.xz` now resumes from one
  block, needing only that block and its stream's footer, and decodes on to the end.
- **A password list now works when the right password is not first**, on the two
  formats where it did not: a header-encrypted 7z and RAR5 with encrypted data. On 7z, a
  wrong key decodes the header to garbage, and that failure ended the attempt instead of
  moving on to the next candidate; a password provider was not asked again either. On RAR5,
  every `unrar` spawn was given the first candidate; the reader now tests each against the
  member's password check and passes on the one that matches, and a provider is asked for
  it when no listed password matches. RAR3/4 data carries no password check, so there the
  first candidate is still the one used.
- **Two threads that both need the password provider no longer fail.** Under
  `MemberStreams.CONCURRENT`, a thread that needed the provider while another thread's call
  was running got the `ArchiveyUsageError` meant for a provider that calls back into the
  reader. It now waits for that call to finish; the reentry error stays for its real case.
- **A password provider is asked again after answering with a password already tried.**
  It used to stop on the first such answer, so a provider that offered the password that
  had opened an earlier member, and had the right one next, never got to give it; the
  member failed with a wrong-password error. A password that already failed for a member
  is still not tried on it again, and a provider that gives the same answer twice for one
  member is taken to have no more. This covers 7z, RAR and ZIP, except a ZipCrypto ZIP
  member that is stored uncompressed, which still stops on the first repeat.
- **Windows timestamps land on the right microsecond.** `modified`, `accessed` and
  `created` read from a ZIP NTFS field, a 7z, or a RAR5 FILETIME were converted through a
  float, which put more than half of present-day values one or two microseconds off.
- **A 7z member's `compression` chain is now in compress order**, as documented on
  `ArchiveMember.compression` and the way 7-Zip itself lists it: a BCJ member reads
  `(BCJ, LZMA2)`, filters first and packing codec last. It used to come back reversed,
  because a 7z folder stores its coders in decode order and the reader copied them as
  stored. ZIP, RAR, TAR and ISO members carry at most one codec and were unaffected.
  A 7z coder archivey does not recognise, such as the ARM64 filter, is now listed as
  `UNKNOWN`, as ZIP and RAR already did; it used to be dropped, so such a member listed
  as plain LZMA and then refused to read.
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
- **A directory tree of any depth lists.** The directory reader walked the tree by
  recursion, so a tree about 990 levels deep raised a bare `RecursionError` and lost the
  whole listing. Extracting an archive of `a/a/a/…/file` is enough to produce one. The
  walk now uses an explicit stack, and the order of members is unchanged.
- **A symlink removed while a directory is listed is skipped, not fatal.** The reader
  already skipped an entry that vanished before it was inspected, with a
  `SCAN_ENTRY_VANISHED` diagnostic. A symlink that vanished between that check and the
  read of its target raised `FileNotFoundError` and lost the listing instead.
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
- **A PPMd member whose data ends before its declared size raises `TruncatedError`.**
  Reading one used to end in a bare `MemoryError` out of `pyppmd`, which reads as the
  host running out of memory. Two ways reach it: a 7z folder that declares more output
  than it holds, and a wrong password on an AES-encrypted PPMd folder, where the
  `MemoryError` also stopped password iteration before the correct candidate was tried.
  Reading a PPMd member larger than 2 GiB no longer raises `OverflowError` either.
- **A WinZip AES member cut short raises `TruncatedError`.** A payload that ends inside
  the salt, the password verifier, the ciphertext or the HMAC used to raise
  `CorruptionError`. A declared size too small to hold the AES envelope at all still raises
  `CorruptionError`: that header is impossible, not truncated. Both are `ReadError`
  subclasses, so code that catches `ReadError` is unaffected.
- **Extraction under `TRUSTED` keeps what the archive stored.** As root, a setuid or
  setgid file now keeps those bits: the mode used to be applied before the ownership,
  and Linux `chown` clears both. A file whose archive stores no mode, such as a ZIP
  entry written on Windows, now gets the mode an ordinary new file gets (`0o666` less
  the umask) instead of `0o600`.
- **`OverwritePolicy.RENAME` finds a free name in one step per member.** Each colliding
  member used to count up from `name (1)` again, so an archive of many names differing
  only in case took time quadratic in their number: 45 seconds for four thousand.
- **Under `STRICT`, trailing dots and spaces are stripped after a `\` as well.** A TAR
  name keeps `\` as a literal character and Windows writes it as a separator, so
  `foo. \bar` kept its trailing space where `foo. /bar` lost it, and an all-dots
  segment after a `\` was not refused.
- **An anti-item deletes the file its name matches under the collision rules.** Under
  `STRICT` and `STANDARD` an anti-item `readme` now removes the `README` the same
  extraction wrote, as every other name collision already treated the two.
- **Detection's cost receipt now reports what detection did.** Under a smaller
  `DetectionBudget` the inner-TAR probe still decoded up to 1 MiB, and a content probe
  on an `ArchiveStream` could buffer 1 MiB, so the receipt failed its own
  `within_budget` check with no skipped tier to explain it. Both now stay inside the
  budget and record the tier as budget-exhausted when they are cut short, as do a far
  signature past `max_far_bytes` and an SFX scan that misses in a window the budget
  shortened. A failed inner-TAR decode is now charged, `within_budget` also checks
  `far_bytes`, and a stub `.exe` followed to its split volume reports both passes' cost
  with `passes=2`, judged against two budgets.

### Changed

- **A `stream_members()` handle never seeks, on any format.** It reports
  `seekable()` as `False` and `seek()` raises `io.UnsupportedOperation`, with or
  without `seekable_members=True` and with or without `streaming=True`; `tell()` still
  works. Before, under `seekable_members=True` over a file source, the handles of ZIP,
  TAR, compressed TAR, non-solid RAR, ISO, directory and single-file archives could
  seek, and the handles of solid 7z could not. A nested archive opened from such a
  handle now gets a non-seekable source, even without a `seek()` call: ZIP, 7z, RAR
  and ISO raise `StreamNotSeekableError`, and TAR and single-file streams need
  `streaming=True`. To seek, or to open a nested archive at random, open the member
  with random `open()` under `seekable_members=True`. `tell()` on a RAR member decoded
  by `unrar` no longer raises `OSError`.

- **`created` is a birth time or nothing.** It never holds Unix `st_ctime` (inode
  change), in any format. Where a writer stores `st_ctime`, or may, the time moves to
  the new `ArchiveMember.ctime` field and `created` stays `None`: the Rock Ridge
  attribute-change time (which `created` used to fall back to), the PAX `ctime`, and
  the creation slot of a RAR from a Unix or unknown host, a 7z member whose attributes
  hold a Unix mode, and a ZIP member from any host but FAT, OS/2, NTFS or VFAT (7-Zip on
  Linux and macOS fills the NTFS creation field from `st_ctime`, libarchive the
  Extended Timestamp's). RAR, 7z and ZIP have one creation slot and fill at most one
  of the two; Rock Ridge and libarchive's PAX headers store both times and can fill
  both. A TAR member's `created` now comes from libarchive's `LIBARCHIVE.creationtime`.
  Directory listing fills `ctime` from `st_ctime` except on Windows. The
  `rar.created_is_ctime` key is gone.
- **Four names leave the public surface before the freeze**, on the API review's
  recommendation. `MemberStreams` is no longer exported from `archivey` or on the API
  page (it is the internal form of the `seekable_members` / `concurrent_members`
  booleans, still importable from `archivey.types`), and the runtime
  `reader.member_streams` property is removed with it: the two booleans you passed to
  `open_archive` are the whole record of what was declared. `DiagnosticSeverity`, the
  `Diagnostic.severity` field and the `severity` key of `Diagnostic.to_dict()` are gone:
  every diagnostic was `WARNING`, and whether one stops you is the policy's disposition.
  `WriteError` is deleted; no write API exists to raise it. `detect_format` no longer
  takes `collector=`, which typed an `internal/` class on a public signature;
  `open_archive` reaches detection through an internal entry instead. `archivey.detection_cost` is now
  documented on the API page; its mutable accumulator `MutableDetectionCostReceipt`
  moves under `internal/` and `default_detection_budget()`, which had no callers, is
  deleted (`ArchiveyConfig.detection_budget` defaults to `BALANCED_BUDGET` directly).
- **`detect_format(budget=)` is removed**; set `ArchiveyConfig.detection_budget` and pass
  `config=` instead. `DetectionBudget` loses the two fields it only reserved,
  `max_index_bytes` and `collect_nonmaximal_candidates`, and `DetectionCostReceipt` loses
  `index_bytes`. `DetectionBudgetPresetStr`, the string alias only that argument used, is
  gone too.
- **Every public class and function reports `archivey` as its `__module__`.** Seventeen
  names in `__all__` are defined under `archivey.internal` (the extraction types,
  `detect_format`, the registry queries, `ArchiveStream`, `enable_measurement`). They
  now report `archivey`, so a pickled `ExtractionResult` or policy enum records
  `archivey.OverwritePolicy` rather than an internal path that could never move, and
  `repr()` and `help()` agree. `typing.get_type_hints` still resolves on those classes.
  `inspect.getsource` on the twelve pinned classes now raises `OSError`: Python finds a
  class's source through its module, and there is no way to point it back.
- **A raw CD sector image is refused by name.** The `.bin` of a `.bin`/`.cue` pair
  used to fail detection with "no magic-byte match", which reads like a corrupt file. It
  is now recognised by its sector sync pattern and refused with
  `UnsupportedFeatureError` naming the layout (Mode 1, Mode 2 Form 1 or 2, sector size).
  Reading one, by stripping its sectors to the 2048-byte payload, is not implemented.
- **`FormatInfo` no longer has an `encoding_hint` field.** No detector ever set it, so it
  was always `None`. The member-name encoding comes from `encoding=` or the backend's own
  detection, as it already did in practice.
- **`ArchiveReader.extract_all()` no longer takes `config=`.** It honoured only the
  extraction limits and silently dropped every other field, including a per-call
  diagnostic policy or callback. A reader runs under the config it was opened with;
  pass `limits=` to override the extraction limits for one call.
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
- **TAR trailing data is reported, and `ArchiveyConfig.strict_archive_eof` is gone.**
  The end-of-archive check used to confirm only that the two-block trailer was present,
  so 4 KiB of arbitrary appended bytes passed silently. It now looks up to 1 MiB past the
  trailer, and the first non-zero byte there emits the new `ARCHIVE_TRAILING_DATA`
  diagnostic. Zero padding still passes (`tar` writes 10 KiB records); a concatenated
  archive is reported, since it is two archives and only the first was listed. A missing
  trailer stays `ARCHIVE_EOF_MARKER_MISSING`. Both are ordinary diagnostics: a warning by
  default, `DiagnosticRaisedError` when set to `RAISE` or under
  `DiagnosticPolicy.strict()`. That replaces the `strict_archive_eof` flag, which raised
  `TruncatedError` and gated the trailing scan, so `strict()` promised to raise on a code
  nothing emitted without it. On a compressed tar the 1 MiB window is decompressed to
  inspect it; a tail that does not decompress ends the check without an error.
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

- **7z password confirmation decodes only what it needs.** 7z AES has no password
  check, so the first read into an encrypted folder confirms a password by decoding.
  That decode used to walk every member CRC to the end of the folder, and then the
  folder was decoded again to serve the member: an attacker-sized cost per candidate,
  paid even by the correct password. It now stops at the first member CRC covering 4
  bytes, and a compressed folder stops at 64 KiB of output, since its codec rejects a
  wrong key within a few bytes. Store/copy+AES with its only CRC at the folder end still
  reads to that CRC per candidate. ZIP's multi-password confirmation uses the same
  planner.
- **New diagnostic `ENCRYPTED_MEMBER_UNVERIFIED`.** A ZipCrypto password that passes the
  one-byte check can be wrong, and then a partial read returns garbage with no error:
  only the CRC at EOF notices. Closing an encrypted member's stream before EOF, when the
  password was accepted on a check weaker than the member's checksum, now emits this
  code (ZipCrypto, WinZip AES, and 7z folders confirmed without reaching a CRC). It is
  outside `ARCHIVE_INTEGRITY_CODES`, so `strict()` collects it; `pedantic()` raises. A
  read that raises silences it; a seek that raises does not, since the stream stays
  usable.

- **`repr()` of a 7z reader's key cache no longer prints passwords or keys.** The cache
  is a dataclass whose generated `repr` showed every candidate password tried and every
  AES key derived from them, so a traceback with locals, a debugger dump or a debug log
  line could carry them. The field is now excluded from `repr`, as the AES key in the
  decrypt parameters already was.
- **`STRICT` extraction no longer widens a file's permissions.** A file stored as
  `0o660` (group-shared, what `umask 007` produces) was written as `0o644`, readable
  by every user. The stored mode is now masked with `0o644`, so it comes out `0o640`.
  A hardlink's stored mode gets the same treatment under `STRICT` and `STANDARD` as a
  file's: a link written as a copy (its source not selected, or on another device) used
  to keep any mode, setuid and world-write included. A mode a filter removes falls back
  to the policy's default (`0o644`, `0o755` for a directory).
- **A hardlink copied across a device boundary counts toward `max_extracted_bytes`.**
  When a link cannot be made because the destination spans two filesystems, archivey
  copies the content instead; those copies were not counted, so a fan-out of links could
  write many times the cap.
- **A symlink target stored as member data is capped at 4096 bytes.** ZIP, 7z and
  RAR3/4 keep a symlink's target in the member's data, and listing read it whole: a
  398 KiB ZIP whose one "target" was 400 MiB of deflated zeros peaked at 2 400 MiB
  inside `members()` with every listing cap set. A member declaring more than 4096
  bytes is not opened, a Windows reparse buffer is read only as far as its own header
  says it runs, and an over-long target is left unset (never truncated) with
  `SYMLINK_TARGET_UNAVAILABLE`, `reason="target_too_long"` — so
  `DiagnosticPolicy.strict()` refuses the archive and extraction fails that link. A
  target read this way now also counts toward `ListingLimits.max_metadata_bytes`,
  which used to weigh it before it was read. Threat-model O19.
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
