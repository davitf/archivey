# Early checkpoint — sample entries + voice check

**Stop point:** first ~18 draft entries after mining `dev-docs/history/`
(ARCHITECTURE, ASYNC, COMPARISON, SPEC; `index.md` is a router). Investigations,
threat-model, ADRs, and proposal `## Why` blocks are still `unread` in
`sources.md`.

**Ask for the maintainer:** does field 1 pass the neutrality test? The three
worked examples in `brief.md` §The neutrality rule are the bar. If the voice is
wrong, steer before the rest of the tree is translated.

Entries below are **drafts** — not yet centrally deduped against later sources,
and field 4 is provisional (history often names the answer; later ADRs/changes
will refine it). IDs are temporary (`P-hist-NN`); stable catalogue IDs come at
merge time.

---

## P-hist-01 — Late-bound member metadata

- **Category:** format quirk · API and usage pattern
- **Problem:** Some archive formats leave a member's final size, checksum, or
  link target unknown until that member's payload has been read — gzip streams
  and ZIP data-descriptor entries omit size/CRC from the local header; several
  formats store symlink/hardlink targets inside (sometimes encrypted) member
  data rather than in the directory entry. A caller who only saw the directory
  listing therefore holds incomplete metadata until after the read.
- **Symptom:** After listing, `size`/`crc`/`link_target` are missing or
  provisional; they appear only after the member bytes are consumed. A frozen
  snapshot taken at list time never receives the late values.
- **Evidence:** `dev-docs/history/SPEC.md` §3.2 / §4.4 (late-bound fields);
  `dev-docs/history/ARCHITECTURE.md` §2.1; ZIP APPNOTE data descriptors;
  gzip RFC 1952 trailer CRC/ISIZE.
- **How answered today (strippable):** Mutable member objects filled in place
  during the streaming pass; callers treat as read-only and use copy-on-edit for
  transforms. → ADR 0007; `archive-data-model`.

## P-hist-02 — Solid compression shared blocks

- **Category:** format quirk · performance & memory
- **Problem:** Solid archives pack many files into one compressed block so that
  reading member *N* requires decompressing from the start of that block (and
  often earlier members). A full sequential pass can still be one decompression;
  random per-member opens pay the block cost again.
- **Symptom:** Opening the *k*-th file in a solid 7z/RAR (or a `.tar.gz`) is
  much slower than opening an equivalent ZIP local entry; naive “open each by
  name” loops re-decompress the same block repeatedly and can exhaust memory if
  the implementation caches whole blocks.
- **Evidence:** `dev-docs/history/SPEC.md` §3.2 memory-profile table and §4.5–4.6
  (`SOLID` access cost); `dev-docs/history/COMPARISON.md` §4.4; 7-Zip and RAR
  format docs on solid archives.
- **How answered today (strippable):** Bound memory on sequential iteration
  (decode once, demux); random `open` re-decodes from block start with a
  warning; cost receipt exposes solidity. → ADRs 0001/0002; access-mode-and-cost.

## P-hist-03 — Format detection consumes non-rewindable bytes

- **Category:** API and usage pattern
- **Problem:** Detecting an archive format requires reading a prefix of magic
  (and sometimes a decompression probe). On a pipe, socket, or other
  non-rewindable source those bytes are gone unless the caller buffered them;
  the same source must then be handed to the reader still containing that
  prefix.
- **Symptom:** After a standalone “what format is this?” call on a pipe, the
  subsequent open misses the header and fails or mis-parses; or detection
  silently leaves the stream mid-header.
- **Evidence:** `dev-docs/history/SPEC.md` §8.1 / §8.3; `dev-docs/history/ARCHITECTURE.md` §2.5.
- **How answered today (strippable):** Opener wraps non-seekable sources in a
  peek/replay buffer shared by detection and the backend; standalone detection
  on a raw non-seekable stream requires the caller to supply that buffer.
  → format-detection spec; ADR 0010 adjacent.

## P-hist-04 — Platform streams that lie about seekability

- **Category:** platform & filesystem
- **Problem:** Some stream wrappers and OS pipe handles report themselves as
  seekable when they are not. A `BufferedReader` over a non-seekable raw stream
  can claim seekability; on Windows, an OS pipe reader may report
  `seekable()==True` and even return plausible offsets from `seek()`, while
  later reads still come from the unmoved position and `SEEK_END` yields a
  bogus size.
- **Symptom:** Code that trusts `seekable()` and repositions for random access
  or size detection silently reads the wrong bytes or believes a pipe has a
  finite size; POSIX pipes fail honestly with `ESPIPE`.
- **Evidence:** `dev-docs/history/ARCHITECTURE.md` §2.5;
  `tests/test_stream_inputs.py::test_windows_pipe_seek_characterization` (cited
  there); Windows vs POSIX `lseek` behaviour on FIFOs.
- **How answered today (strippable):** Central seekability predicate unwraps
  buffers, special-cases mmap, and overrides pipe/FIFO claims via `fstat` when
  `seekable()` is True. → streamtools `is_seekable`.

## P-hist-05 — ZIP central directory lives at EOF

- **Category:** format quirk
- **Problem:** A ZIP's authoritative member index is the central directory at
  the end of the file. A forward-only byte source that cannot rewind therefore
  cannot open a ZIP in random-access mode without first materializing the whole
  archive somewhere seekable.
- **Symptom:** Opening a ZIP from a pipe/socket with “random access” requested
  fails immediately; callers who expected streaming ZIP listing get a seek
  error instead of a progressive member walk.
- **Evidence:** `dev-docs/history/SPEC.md` §10.1 (non-seekable ZIP);
  APPNOTE.TXT EOCD / central directory layout.
- **How answered today (strippable):** Fail fast with a typed non-seekable
  error; do not silently spool. Streaming mode / explicit buffering is the
  escape. → format-zip; ADR 0010.

## P-hist-06 — Path traversal and chained symlink escapes on extract

- **Category:** security / hostile input
- **Problem:** Archive member names can contain `..`, absolute paths, or drive
  letters; members can also plant symlinks that later members write through,
  so that a write intended for the destination tree lands outside it (classic
  zip-slip / TOCTOU symlink chains).
- **Symptom:** Extraction creates or overwrites files outside the chosen
  directory; or an intermediate symlink redirects a later write after a naive
  string check already passed.
- **Evidence:** `dev-docs/history/SPEC.md` §7.1; `dev-docs/history/ARCHITECTURE.md`
  §4.1; widely documented zip-slip class of bugs.
- **How answered today (strippable):** Universal name checks, resolved-path
  containment, post-symlink re-resolve; filter policies on top.
  → safe-extraction.

## P-hist-07 — Decompression bombs (ratio and total volume)

- **Category:** security / hostile input
- **Problem:** Highly compressible hostile payloads expand by orders of
  magnitude relative to their packed size, and multi-member archives can sum to
  enormous extracted volume even when each member looks modest. Tiny legitimate
  files can also show extreme ratios, so a naive ratio check false-positives.
- **Symptom:** Extract fills the disk or OOMs; or a 10-byte → 15 KiB text file
  is rejected as a “bomb” by a ratio-only guard.
- **Evidence:** `dev-docs/history/SPEC.md` §7.3; `dev-docs/history/ARCHITECTURE.md`
  §4.2 / §5.5; classic 42.zip / zip-bomb literature.
- **How answered today (strippable):** Cumulative byte + entry caps; per-member
  ratio only after an activation floor. Limits apply to extract paths, not raw
  `read`/`open`. → safe-extraction BombTracker.

## P-hist-08 — Hardlinks refer only to earlier members

- **Category:** format quirk
- **Problem:** In the TAR hardlink model (and archives that follow it), a
  hardlink entry always names a file already seen earlier in archive order. An
  extractor that has not yet written that source, or that filtered the source
  out while keeping the link, cannot create a correct link without extra policy.
- **Symptom:** Extraction fails on hardlinks when the target was skipped by a
  filter; or streaming extract cannot find a source that “should” exist later.
- **Evidence:** `dev-docs/history/SPEC.md` §3.2 link-following / hardlink note;
  `dev-docs/history/ARCHITECTURE.md` §2.6; POSIX/`tar` hardlink semantics.
- **How answered today (strippable):** Single forward pass with recorded source
  paths; if source unselected, stage content at first selected link without
  leaking the excluded name. → safe-extraction / archive-reading.

## P-hist-09 — Duplicate member names are legal

- **Category:** format quirk · API and usage pattern
- **Problem:** TAR and 7z (among others) allow multiple members with the same
  path string. Name-keyed lookup cannot identify which member the caller meant;
  hardlink resolution (“most recent earlier member with that name”) depends on
  stable identity beyond the path.
- **Symptom:** `get("foo")` returns one of several foos unpredictably; filters
  or conversion pipelines collapse duplicates; hardlinks resolve to the wrong
  payload.
- **Evidence:** `dev-docs/history/COMPARISON.md` §4.2 (member_id);
  `dev-docs/history/SPEC.md` member identity discussion in §4.4 area;
  tar/7z allowing duplicate names in the wild.
- **How answered today (strippable):** Stable per-member ids; name lookup returns
  lists / last-wins policies documented; hardlink resolution by id order.
  → archive-data-model.

## P-hist-10 — Listing cost varies by format layout

- **Category:** format quirk · performance & memory · API and usage pattern
- **Problem:** Some formats carry an index (ZIP central directory, 7z header,
  RAR headers) so listing is cheap; others (plain TAR, compressed TAR) require
  scanning or decompressing the whole stream to know the member set. Callers
  cannot assume “list then open one file” is cheap without knowing the layout.
- **Symptom:** `members()` on a multi-gigabyte `.tar.gz` decompresses
  everything; the same call on a ZIP is near-instant. Tools that always list
  first surprise operators on sequential formats.
- **Evidence:** `dev-docs/history/SPEC.md` §4.6 CostReceipt axes;
  `dev-docs/history/ARCHITECTURE.md` §2.12.
- **How answered today (strippable):** Cost receipt (listing / access /
  stream capability) at open; streaming mode disables materializing APIs.
  → access-mode-and-cost; ADR 0004.

## P-hist-11 — Optional format support without hard dependency

- **Category:** packaging & dependency
- **Problem:** Supporting ISO, exotic codecs, or RAR *data* requires third-party
  libraries or an external binary. Importing the archive library must still
  succeed when those pieces are absent; attempting an unsupported format must
  fail with an install hint, not an ImportError deep in a backend.
- **Symptom:** `import archivey` crashes without pycdlib; or opening an ISO
  raises a raw ImportError instead of “install archivey[iso]”.
- **Evidence:** `dev-docs/history/SPEC.md` §2 / §9.1;
  `dev-docs/history/ARCHITECTURE.md` §2.13; `dev-docs/history/COMPARISON.md` §4.11.
- **How answered today (strippable):** Zero-dep core; optional backends register
  only when importable; typed unsupported/missing-package errors naming the
  extra or tool. → ADR 0011; packaging-and-extras.

## P-hist-12 — RAR metadata vs proprietary decompression

- **Category:** packaging & dependency · format quirk
- **Problem:** RAR member payloads use a proprietary compressor; listing and
  header crypto can be done from documented structures, but expanding member
  bytes still needs a licensed decompressor (commonly the system `unrar`
  binary). Environments without that binary can list but not read data.
- **Symptom:** Listing a `.rar` works; `open()` / extract fails naming a missing
  tool. CI without `unrar` silently skips data tests.
- **Evidence:** `dev-docs/history/SPEC.md` §10.5;
  `dev-docs/history/ARCHITECTURE.md` §5.7; RARLAB unrar requirement.
- **How answered today (strippable):** Native metadata parse; data via `unrar`;
  listing without the binary. → ADR 0002; format-rar.

## P-hist-13 — Sync pull-driven C decoders block true async

- **Category:** concurrency & lifetime · API and usage pattern
- **Problem:** Python’s archive and codec stacks (`zipfile`, `tarfile`, stdlib
  `lzma`/`bz2`/`zlib`, subprocess pipes) pull input through blocking `read()`
  callbacks inside C code. There is no hook to await the next compressed chunk
  mid-decode, so an archive library cannot be “async all the way down” without
  rewriting those decoders.
- **Symptom:** Putting `async` on the public API still blocks the event loop
  during decode unless work is offloaded to a worker thread; naive async wrappers
  give the illusion of concurrency without I/O overlap at the decoder.
- **Evidence:** `dev-docs/history/ASYNC.md` §3; `dev-docs/history/ARCHITECTURE.md`
  §5.3.
- **How answered today (strippable):** Sync-only v1; `asyncio.to_thread` for
  whole operations; async facade deferred as a leaf. → ADR 0005.

## P-hist-14 — In-place archive append is fragile or impossible

- **Category:** format quirk · API and usage pattern
- **Problem:** ZIP append (rewrite central directory at EOF) corrupts the
  archive if interrupted; 7z has no append mode; TAR “append” produces a
  concatenation that is not a clean multi-stream archive. Callers who want
  “update one file inside” cannot rely on a safe in-place mutate across formats.
- **Symptom:** Interrupted ZIP update leaves an unreadable archive; “append to
  7z” is simply unavailable; tools that mutate in place work for one format and
  fail for others.
- **Evidence:** `dev-docs/history/ARCHITECTURE.md` §5.4;
  `dev-docs/history/COMPARISON.md` §4.8 (writer as create + convert).
- **How answered today (strippable):** Create-only writers; conversion via
  read→write pipeline. → archive-writing (when shipped).

## P-hist-15 — Compressed-tar vs plain compressor ambiguity

- **Category:** format quirk
- **Problem:** A `.gz`/`.bz2`/`.xz`/`.zst` byte stream may be a single compressed
  file or a compressed TAR. Magic alone names the compressor; distinguishing
  “tar inside” requires probing decompressed content. Brotli (and similar) may
  lack a reliable magic, so detection needs a trial decompress.
- **Symptom:** Opening `foo.gz` as a single file when it is `foo.tar.gz` (or the
  reverse) yields the wrong member model; brotli detection fails or false-matches
  without a probe.
- **Evidence:** `dev-docs/history/COMPARISON.md` §4.9; `dev-docs/history/SPEC.md`
  §8 detection algorithm; `dev-docs/history/ARCHITECTURE.md` detection notes.
- **How answered today (strippable):** Composite format `(container, stream)`;
  compressed-tar probe; FormatInfo confidence/method. → format-detection.

## P-hist-16 — SFX and embedded archives

- **Category:** format quirk
- **Problem:** Self-extracting executables and other binaries can embed a RAR/7z
  (or similar) payload after a stub. Detection must find the archive payload
  inside a larger file, not only at offset 0.
- **Symptom:** Opening an `.exe` SFX as “not an archive”; or tools that only
  check leading magic miss the embedded container.
- **Evidence:** `dev-docs/history/COMPARISON.md` §4.9 (SFX detection in DEV);
  format specs for SFX/RAR/7z embedded signatures.
- **How answered today (strippable):** Detection scans for embedded signatures
  (as implemented in the detection pipeline). → format-detection.

## P-hist-17 — Per-library exception taxonomies

- **Category:** upstream library defect · API and usage pattern
- **Problem:** Each underlying codec/archive library raises its own exception
  types and message shapes (`BadZipFile`, `TarError`, `LZMAError`, subprocess
  failures, crypto errors). Callers of a uniform archive API need one typed
  error tree, while filesystem/`KeyboardInterrupt`/`MemoryError` must not be
  swallowed into “archive corrupt”.
- **Symptom:** Callers catch a dozen unrelated exceptions; or a disk-full
  `OSError` is mis-reported as corruption; or a library error leaks untyped.
- **Evidence:** `dev-docs/history/ARCHITECTURE.md` §2.11;
  `dev-docs/history/SPEC.md` §6; CONTRIBUTING error-contract (referenced from
  architecture).
- **How answered today (strippable):** Per-library translators + central context
  stamping; non-decode exceptions propagate raw. → error-handling; ADR 0012
  (usage errors outside the tree).

## P-hist-18 — Random access inside compressed streams

- **Category:** performance & memory · format quirk
- **Problem:** A multi-gigabyte `.tar.xz` / `.tar.lz` / indexed gzip may have
  internal indexes (XZ block index, lzip trailer, gzip index) that allow seeking
  to a late member without decompressing from byte zero — but only if the reader
  understands those indexes and the source is seekable. Without that, “extract
  the last member” costs a full decompress.
- **Symptom:** Extracting one late file from a huge tar.xz takes as long as
  reading the whole archive; tools that know the XZ index are orders of magnitude
  faster on the same file.
- **Evidence:** `dev-docs/history/COMPARISON.md` §1 / §4.12 (seekable decompressor
  streams); library-analysis cross-ref there.
- **How answered today (strippable):** Seekable decompressor stream layer with
  optional accelerators; cost/seek signals. → compressed-streams; ADR 0008
  (accelerator choice) later.

---

## Neutrality self-check (author)

| Entry | Archivey type/module/config named in field 1? | Notes |
|---|---|---|
| 01–18 | No (field 1) | Field 4 deliberately names ADRs/types; experiment strips it |
| Closest risks | “streaming pass”, “cost receipt” | Kept out of field 1; used only in field 4 |

If the maintainer wants field 2 more “user-visible symptom” and less
“implementer symptom”, say so — several symptoms still read engineer-facing.

## What I have not done yet

- Investigations, threat-model O1–O9, known-issues, library-analysis, open-issues,
  IDEAS, discussions
- 17 ADR context sections and 72 proposal `## Why` blocks
- 11 review SUMMARY.md files
- Central dedupe (these 18 will absorb many later restatements)
- Full neutrality second pass
- `catalogue.md` / `catalogue-neutral.md` / `experiment.md` / `SUMMARY.md`
- Topic 8 harvest (outstanding by design)

Awaiting steer (or a short wait) before continuing.
