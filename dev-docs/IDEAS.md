# Archivey — Future Ideas / Backlog

> **Status: speculative.** Nothing here is committed or scheduled. These are
> "might do later, worth remembering" notes — *not* part of the `PLAN.md` phase
> roadmap. Firm, decided v1 deferrals (async, in-place modify, sparse-file
> extraction, etc.) live in `openspec/project.md`
> ("Deferred / out of scope (v1)") and `dev-docs/history/SPEC.md` Appendix A — this file is the
> looser idea pile. Promote an item by writing a real spec/`openspec` change for it.

## Backends & format coverage

- **Native streaming ZIP reader** — a native parser that does what stdlib `zipfile`
  can't: read from **non-seekable** streams (pipes/sockets) and **truncated / no-EOCD**
  archives by walking local file headers forward, plus better coverage of data
  descriptors, ZIP64 edge cases, extra fields, and **AES/WinZip encryption** (zipfile
  only does legacy ZipCrypto). Fits the native-first direction (cf. 7z/RAR). Streaming
  mode is forward-only and sizes/CRC arrive in trailing data descriptors — i.e. the
  **late-bound `ArchiveMember` fields** + `FORWARD_ONLY`/`is_solid=False` cost model we
  already designed for. Lands as a native variant of `formats/zip_reader.py`.
  This is also the natural home for **multi-volume (split/spanned) ZIP** — the
  `.z01`…`.zip` sets that `zipfile` cannot read (it rejects multi-disk archives; see
  `format-zip`). A native parser can read the central directory disk-aware and resolve
  each *(disk-number, offset)* against a concatenation stream over the ordered segments
  — the analogue of the 7z volume-join, but with ZIP's per-disk addressing rather than a
  dumb byte-split. (For v1 we just detect these and raise `UnsupportedFeatureError`.)
  Also the home for **graceful UTF-8-flag-lie handling**: stdlib `zipfile` strictly
  decodes a name whose general-purpose bit 11 claims UTF-8, so one hostile/broken name
  makes the *whole archive* unlistable (`UnicodeDecodeError` → `CorruptionError`; the
  adversarial string corpus pins that behavior). A native parser can decode such names
  with the same cp437/`surrogateescape` fallback used for unflagged names and keep the
  archive readable — likely with a diagnostic once warnings-as-data lands.

- **libarchive backend** — `python-libarchive-c` as an **alternative / additional**
  backend for several formats (zip/tar/7z/iso/cpio/…), in the `[all]`/alternative tier
  behind a `[libarchive]` extra. Caveats: native C dependency (the packaging-finicky
  axis `[recommended]` deliberately keeps out — see `[seekable]`), stream-oriented (weak random access,
  historically no solid-RAR support).

- **Synthetic single-stream RAR → libarchive** — generalize rarfile's "hack": build a
  minimal artificial RAR stream containing a single file (or one solid block) and feed
  it to libarchive's RAR decompressor, as an alternative to shelling out to the external
  `unrar` binary. Could remove the `unrar` runtime requirement for common cases.
  **Higher-risk / research spike** — RAR decode correctness is hard and libarchive's
  RAR5 coverage is partial; `unrar` remains the reference.

- **Subprocess decompressor streams** — a single reusable `SubprocessDecompressorStream`
  that pipes compressed/uncompressed data through a system binary (`zstd`, `xz`,
  `brotli`, `lz4`, …) as an alternative to installing the Python codec libs. Same pattern
  we already use for `unrar`; valuable in locked-down environments where C-extension
  wheels won't install but CLI tools are on PATH. Forward-only; needs availability
  detection and careful subprocess/error handling. Low-priority backend tier.

- **UU / Base64 transport encodings as single-file wrappers** — classic `uuencode`
  (`.uu` / `.uue`, including `begin-base64`) shows up in old mail/Usenet drops and some
  vendor corpora; libarchive treats it as a filter and stores many of its own test
  fixtures uu-encoded (authoring hygiene, not end-user demand). Fits the existing
  one-member `SingleFileBackend` shape: peel one wrapper, yield opaque payload bytes
  (same pattern as `.iso.xz` — no general filter stacking). Decoder is trivial pure
  Python / zero-dep; the non-trivial bits are weak line-oriented detection (`begin `),
  trusting the embedded name/mode like gzip `FNAME`, and ratio-guard assumptions that
  expect compression to *shrink*. Scope as bare single-file only — not transparent
  `uu → gz → tar`. Same “legacy wrapper / open anything in old backups” niche as `.Z`;
  lower priority than anything on the native 7z/RAR / CLI path. Sibling encodings
  (xxencode, BinHex, yEnc) only if a real corpus itch appears. Promote when a backup
  corpus actually wants `detect_format` / `open_archive` to Just Work on `.uu`.

- **Opt-in legacy name-encoding detection (+ undecodable-name reporting)** — *explicitly
  post-1.0; not needed for release.* Member names that carry **no Unicode marker and are not
  valid UTF-8** currently decode via `surrogateescape` → honest but garbled (`U+DCxx`)
  spellings. Affected: **TAR** (ustar/pax has no charset field at all, so `tarfile` defaults
  to UTF-8 and everything else becomes surrogateescape), **RAR3** non-Unicode names (already
  falls back to `windows-1252` via `_decode_name`), and **ZIP** unflagged names that aren't
  valid UTF-8 (falls back to `zip_unflagged_fallback_encoding`, default cp437). The common
  *UTF-8-without-marker* case is already handled everywhere (that was the
  `zip-name-encoding-sniffing` change); this item is only about the genuinely-legacy tail.

  **Why this is NOT the default (the danger).** The shipped UTF-8 sniff is *validation*, not
  guessing — UTF-8 is self-checking, so a clean decode is near-conclusive. Legacy detection
  has **no oracle**: latin-1 / cp1252 / cp437 / cp850 / ISO-8859-x are all total functions
  over bytes (each decodes *any* input, just to different characters), and filenames are far
  too short for statistical detectors (chardet / charset-normalizer) to be reliable — often
  1–2 non-ASCII bytes. A wrong guess is strictly worse than the status quo: today's
  surrogateescape is **honest** (visibly signals "not decodable") and **lossless**
  (round-trips to the original bytes); a wrong codepage yields a **plausible-but-silently-wrong**
  name that may also be **lossy**. So surrogateescape stays the default; detection is opt-in.

  **Shape if built.** A config flag (off by default), behind a `[charset-detect]` extra
  (keeps the zero-dep core clean). Detect **archive-wide** over the concatenation of *all*
  non-UTF-8 names at once — kilobytes of same-encoding text, not one 8-byte name — and apply
  a single codepage; emit a diagnostic recording the guessed encoding + confidence. Backstop:
  `ArchiveMember.raw_name` already retains the true bytes, so even a wrong guess loses nothing.
  Naturally unifies TAR + RAR3's legacy fallback with ZIP's `zip_unflagged_fallback_encoding`
  under one "legacy name-encoding policy".

  **Corpus-gathering (the de-risking bridge — worth doing *earlier*, around release).** We
  can't tune or even justify a detector without real-world samples, and a fresh library has
  none — so surface the cases and let willing users report them. The surrogate case is already
  machine-detectable (`U+DCxx` in a decoded name) and the raw bytes are already captured in the
  diagnostic context (base64). **Never phone home** — filenames are sensitive. Instead, a
  passive affordance: a docs "how to report a name we couldn't decode" note, a one-line CLI hint
  (with an issue link) when `list`/`extract` encounters surrogate names, and/or a small helper
  that dumps the undecodable raw-name samples for a user to paste into an issue. This reporting
  affordance is cheap and safe (no guessing), should ship *before* the detector, and is exactly
  what turns "release → real cases" into the evidence for whether/how to build detection at all.

## API & ergonomics

- **Exhaustive ambiguity fallback for `open_archive()` / `open_stream()`** — when
  evidence-based detection yields two or more tied maximal candidates, the near-term
  contract should raise a dedicated ambiguity error rather than choose by registry order.
  Much later, opening could deliberately try every tied candidate and return the first
  one that validates deeply enough. This is not merely a loop: define what counts as
  success for synthetic single-file readers versus indexed containers, preserve/replay
  caller-owned and non-seekable sources, cap cumulative seeks/bytes/decode work, retain
  every failed candidate's typed error, and decide what happens when multiple candidates
  open successfully. An all-candidates inspection API likely belongs beside it, but its
  name is deliberately unsettled. Promote only with an OpenSpec change covering both
  archive and stream opening plus the cost/error model.
- **Extension-first detection ordering** — try the formats a filename's extension
  suggests before the rest, falling back to the full sweep on a miss or when there is no
  filename. Strictly better than today's fixed order *and* than a hard extension gate: a
  `.br` file stops being identified by whichever content probe happens to run first, while
  a wrongly-named file is still found — which matters, because `VISION.md`'s founding use
  case is a backup corpus where wrong extensions are normal. It is also where a magic
  *denylist* would become reasonable (compound-document files falling out of
  `detect_format` is a feature once the extension has had first refusal), which
  `brotli-probe-framing-gate` declined precisely because a sound rule should not be mixed
  with a heuristic. Not small: it restructures `_detect_format_body` for every format, so
  it wants its own proposal. Parked from that change's task 5.1.

  **Variant worth separating: an agreement short-circuit.** The entry above reorders *which
  format's checks run first*. A distinct idea is to *stop early*: when the extension's
  suggested format is confirmed by the content, return immediately and never run the
  remaining tiers. That is the one with the cost payoff, because `prefixed-archive-detection`
  makes the later tiers expensive — an always-on ZIP tail probe (seek + ≤64 KiB) and a
  hoisted far-magic peek (≤32 KiB, ahead of the content probes). A magic-less file pays both
  before a probe ever answers, so a short-circuit saves ~96 KiB per file.

  Measured on 71 983 files under `/usr`, and the numbers point in an unexpected direction:

  | | files |
  | --- | --- |
  | with an extension archivey knows | 1 303 |
  | **already answered by near magic — free, at step 2** | **1 289 (98.9%)** |
  | extension + content **probe** agree ← the short-circuit's real population | **2** |
  | no agreement | 10 |
  | of those 2, how many would skip a *different* format's exact far magic | **0** |

  So near magic already handles almost everything an extension could confirm, and the
  short-circuit only helps where near magic misses — the magic-less formats (Brotli, zlib,
  LZMA Alone). `/usr` has essentially none, so **this corpus bounds the risk well and the
  benefit badly.** A `.br`-heavy corpus (web assets) is where it would pay, and measuring one
  is the precondition for proposing this. That survey pairs naturally with
  `prefixed-archive-detection` task 5.2, which already needs corpus legwork.

  **The naive form is unsound, and the fix is cheap.** "Agree → return" lets two weakish
  signals short-circuit a stronger one never consulted — exactly the bootable-ISO defect
  `prefixed-archive-detection` fixes, where an ISO's boot-code system area is claimed by the
  Brotli probe. An ISO *misnamed* `.br` would satisfy agreement and skip the far magic that
  identifies it. Zero instances measured, so this is a bounded risk rather than an unmeasured
  one — but the sound version costs nothing to state: **agreement may skip the expensive
  structural tiers (tail probe, cued scan, exhaustive scan) but never the cheap exact-magic
  ones.** Far magic is one bounded, size-gated peek and is *exact* evidence; the tiers worth
  skipping are the ones that are both costly and no stronger than the corroboration itself.
  That keeps the ISO fix intact while capturing most of the saving.

  Stated as a rule: agreement between two independent signals outranks an unconsulted
  *expensive* alternative, never an unconsulted *cheap and stronger* one.

- **`FormatInfo.corroborated` is interim — it belongs in the detection evidence ledger** —
  `probe-provenance-unconfirmed` added an internal `corroborated: bool` to `FormatInfo` to
  key the `format_unconfirmed` channel. It cannot become public as a bool: `False` means
  both "a probe with nothing corroborating it" and "not a probe at all", so a ZIP named
  `a.zip` (extension agrees) and one named `b.tar` (extension contradicts) produce
  identical output — `magic` / `certain` / `False` — as do an extensionless Brotli probe
  hit and one whose `.zip` name contradicts it. The replacement is **not** a wider public
  field here: PR #263's analysis §1 already specifies the evidence ledger (typed
  `DetectionEvidence` on an internal `FormatCandidate`, totally ranked classes, ordered
  tie-breakers) and explicitly rejects additive scoring over correlated signals, so a
  counted bit-set would be wrong too. Recorded here only so the interim field is not
  mistaken for a settled design; the work belongs to that redesign, after
  `prefixed-archive-detection` adds its two further `detected_by` values. Full truth table
  in `probe-provenance-unconfirmed` task 5.1.

- **`SANITIZE` extraction policy: name rewriting** — the post-v1 opt-in `SANITIZE`
  policy already sketched in `safe-extraction` (re-root/collapse unsafe paths instead of
  rejecting) is also the right home for **renaming members the destination cannot
  represent**: undecodable-byte (`surrogateescape`) names that UTF-8-enforcing
  filesystems (APFS, some network mounts) refuse with `EILSEQ`/`EINVAL`, and other
  representability failures. One policy knob covering all "make it extractable by
  rewriting the name" behavior — not a bespoke argument per case. Default behavior
  stays reject-with-typed-error (see the adversarial-string-corpus-contract
  safe-extraction delta).

- **Pathlib-like navigation** — an `ArchivePath` supporting `/` joining, `iterdir()`,
  `glob()`, `read_bytes()`, `is_dir()`, … over the member tree (precedent: `zipfile.Path`).
  Read-only wrapper; needs random access, so a `DIRECT`/indexed-archive convenience.

- **fsspec integration** — three distinct directions:
  (1) **expose** an opened archive as an `fsspec` filesystem so pandas/dask/pyarrow/etc.
  read members by path (pairs with the pathlib navigation layer) — the substantial one;
  (2) **open** archives *from* an `fsspec` URL (`s3://…/a.zip`, `http(s)://`, …) as the
  `source`; (3) **extract** *to* an `fsspec` location as the `dest`.
  For (2), passing an fsspec-opened file object **already works** (the stream-input
  tests exercise fsspec objects), so the remaining value is *URL-level* opening —
  archivey calling `fsspec.open()` itself, behind an optional `[fsspec]` extra. What
  that buys beyond "hand me a stream": archivey can pick sensible fsspec caching for
  the access mode (`streaming=True` → plain forward read; random access → block/file
  cache so ZIP central-directory + member seeks don't re-fetch), and — the real
  unlock — it has **filesystem context**, which a bare stream can never provide:
  multi-volume sets (`name.7z.001`…) need `fs.ls()` to discover sibling volumes, which
  the Phase 6 volume-joining path requires. Shape TBD: a URL-string branch inside
  `open_archive()` (gated on `"://" in source` + fsspec installed) vs. a separate
  `open_archive_url()`; the separate function keeps typing/behavior of the core
  entry point simple and the dependency boundary explicit.

- **Configurable symlink-extraction behavior** — a policy knob (in the spirit of `OnError`
  / `ExtractionPolicy`) for what happens when a SYMLINK member cannot be created as a real
  symlink — most notably on filesystems/platforms without symlink support (FAT, Windows
  without the privilege). Phase 4 fixes this at "per-member `OnError` failure, never copy"
  (deliberately *deviating* from `tarfile`, which silently copies the in-archive target's
  data). A future option could offer e.g. `symlink=error|copy|skip` (copy = `tarfile`-style
  materialize-the-target, guarded so it can't reintroduce a path escape). Its own change +
  exploration — the safe default lands first.

## Performance & robustness

- **Reuse the index-only pass's members instead of rebuilding them** — on backends with
  `_MEMBER_LIST_UPFRONT`, `extract_all` lists twice: `_get_members_index_only()` for the
  extraction prep, then `_materialize_members()`. Both call `_iter_members()` afresh and
  the first pass's list is never cached, so the backend re-walks its index and builds a
  second set of `ArchiveMember` objects for the same members. Nothing decided this — it
  falls out of the index-only result not being stored — and it is unrelated to ADR 0007,
  which makes members *mutable and filled in place*. Two consequences today: the wasted
  re-walk, and a correctness trap where per-member work deduped on object identity fires
  twice (that bug shipped; see #232, now deduped on `member_id`). Caching the index-only
  list so materialization enriches those same objects would remove both. Wants care around
  the listing tracker, which deliberately *does* re-account on the second pass, and around
  `_materialize_members`'s concurrency gate. Raised while writing `dev-docs/code-map.md`;
  no decision taken.

- **`pyppmd` exit-after-green abort (mitigated)** — was: required CI’s
  `tests/test_ppmd_raw_streams.py` child finished green then SIGSEGV on teardown.
  Cause: truncated-stream `flush()` passed a large remaining `unpack_size` with the
  extra NUL (same overshoot family as unbounded decode), plus upstream
  `Ppmd7T_Free` on unfinished workers. Fixed by capping NUL recovery output and
  subprocess-isolating unfinished-decoder adversarial tests. Notes:
  `dev-docs/known-issues.md`, `dev-docs/investigations/ppmd-exit-after-green-exploration.md`.

- **rapidgzip for zlib / raw-deflate streams** — give zlib- and deflate-compressed streams
  the same fast random access rapidgzip already gives gzip. This is especially valuable for the
  future native **ZIP** parser: ZIP members are raw deflate, so a seekable deflate backend means
  random access *within* a large member, not just to its start. Investigate whether rapidgzip can
  consume zlib/raw-deflate **directly** (it already handles gzip/zlib framing; raw deflate, wbits
  -15, may need a hint or may be unsupported). If not, **synthesize a gzip stream** from the
  source — wrap raw deflate (or zlib, after dropping its 2-byte header + adler32 trailer) in a
  minimal 10-byte gzip header + 8-byte trailer so rapidgzip will index it; check whether it needs
  a *valid* CRC32/ISIZE trailer or just well-formed framing to build the seek index. No
  coexistence concern — archivey already uses rapidgzip as its single accelerator library (see
  `dev-docs/known-issues.md`). Pairs with **seek-index persistence** below.

- **Compressed-passthrough transcoding (no recompress)** — when writing a member from a source
  that is itself an archive/compressed stream, and the destination format can carry the source's
  *compressed* representation as-is (e.g. a deflate member from a ZIP/gzip → a ZIP entry, both raw
  deflate), copy the already-compressed bytes straight through instead of decompress→recompress.
  Skips the most expensive part of a format conversion entirely. Needs internal coordination
  between the read and write paths: the reader must be able to hand out the *raw compressed* block
  (codec + parameters + the bytes) rather than only a decompressed stream, and the writer must
  accept a pre-compressed payload and emit the right container framing/headers (and decide what to
  do about checksums — reuse the stored CRC vs. recompute). Only valid when codecs + parameters
  match (e.g. deflate↔deflate; not deflate→zstd), so it's an opportunistic fast path with a
  decompress-recompress fallback. Pairs with the native ZIP parser (raw-deflate access) above.

- **Parallel extraction / concurrent member streams** — the declared worker seam
  (`MemberStreams.CONCURRENT`) is committed and supported (post-materialization
  fan-out; free-threaded coverage via the Linux `3.13t` CI job). Scheduling/throughput
  for extract-independent-members remains future; any speed claim needs targeted
  measurements. Also applies to **solid archives with multiple independent blocks** —
  e.g. a 7z with several solid folders can decompress folders in parallel (py7zr does
  this); members *within* one solid block stay sequential. No benefit for a single-block
  solid archive. Misuse fails loudly (`ArchiveyUsageError` / `ConcurrentAccessError`).

- **Hold the solid-block decoder open across `open()` calls — and decide what that means
  under `concurrent_members`.** *(Status: **deferred on purpose**; direction agreed, the
  concurrency half is unbrainstormed. From the 2026-08-07 simplicity & consistency review —
  see `review/archive/2026-08-15-simplicity-consistency/open-questions-for-discussion.md` §O2b/§O2c for the
  full argument and `QUESTIONS.md` pay-list rows 17–18.)*

  **The cost, measured.** `SevenZipReader._open_member` calls `_open_folder_stream` →
  `open_folder_pipeline` **every time**, so each random `open()` builds a fresh folder
  decode pipeline and skips forward from the folder's start (`sevenzip_reader.py:535` and
  the comment at `:554` already says so: *"each from-start folder decode counts"*). On a
  single-folder solid 7z, walking every member via `open()` costs **4.5× one pass** —
  and, because the underlying stream is not held, **in-order and reverse order cost the
  same**; this is not a backward-seek problem, it is a no-reuse problem. `.tar.gz`, which
  *does* hold its stream, costs 1.0× in order. So the gap is a cross-backend
  inconsistency, not a property of solid formats.

  **The shape everyone agreed on:** keep at most **one** decoder, positioned at or before
  the requested member, and reuse it only when the target is at or ahead of the current
  position; otherwise discard and restart. Forward reuse captures the entire 4.5× → 1.0×
  win without a cache, an eviction policy, or a memory budget. Backward access stays as
  expensive as today, which is honest. Not a public-API change, so not tag-gated.

  **Why it is parked: the concurrency half.** With one member open at a time this is
  simple — one stream, one position, one decision point. Under
  `open_archive(concurrent_members=True)` it is not, and the "is it already a non-issue?"
  hope is **disproved**: the CONCURRENT fan-out is over the *listing* snapshot, not member
  data, so member reads are **not** materialized. Measured on a 6-member, single-folder
  solid 7z (200 KB per member, 1.2 MB payload): opening members 1 and 4 *simultaneously*
  succeeds, and `IoStats.bytes_decompressed` is **1 400 000** — 400 KB + 1 000 KB, i.e.
  **two independent decodes, each from the folder's start, live at the same time.**
  (Without `concurrent_members` the second `open()` raises `ConcurrentAccessError`.)

  So N concurrent opens on one solid folder means N live LZMA states, each with its own
  dictionary. The unanswered questions, and they need a real brainstorm rather than a
  patch: when those N streams close, do we keep all of them so the next `open()` can pick
  the closest preceding one (that is a cache, with an eviction rule and a memory budget),
  or only one — and if one, the furthest-advanced or the most-recently-used? Does
  "closest preceding" beat "restart" often enough to pay for the bookkeeping? Does a
  reused decoder outlive the member stream that created it, and if so what owns it and
  what must `close()` tear down? How does that interact with the single-live-stream gate
  on non-CONCURRENT readers? Same question applies to RAR solid blocks (unmeasured — the
  corpus cannot build RAR here, see `known-issues`/F16).

  Promote by writing an `openspec` change; the review's pay list keeps rows 17 (the
  optimization) and 18 (this brainstorm) with row 17 blocked on row 18.

- **Efficient seekable zstd — probably a *native* frame-index reader, not `indexed_zstd`.**
  *(Status: **scheduled** — promoted to the rescoped Phase 8 in `PLAN.md`; the analysis
  below is the basis for that phase's benchmark-first task.)*
  zstd currently has *no* fast random access: a backward seek re-decompresses from the start
  (rewind + warning), like brotli/lz4/zlib. The obvious candidate,
  [`indexed_zstd`](https://github.com/martinellimarco/indexed_zstd) (martinellimarco; the zstd
  backend ratarmount uses, wrapping `libzstd-seek`), is a heavy Cython/C++17 extension that
  statically bundles a C++ core "based on `indexed_bzip2`" — so it carries the *same class* of
  macOS dual-load symbol-collision risk that forced archivey onto a single accelerator library
  (`dev-docs/known-issues.md`) and would need its own coexistence canary.

  **But first check whether it actually buys us anything our own infrastructure can't.**
  `libzstd-seek`'s jump table maps **frame boundaries only** — its own header says records map a
  compressed to an uncompressed position where "both positions refer to frame boundaries", giving
  "constant-time random access **at zstd frame granularity**". A seek into the middle of a frame
  jumps to that frame's start and decodes forward; there is **no** intra-frame state
  checkpointing (unlike `rapidgzip`, which snapshots the inflate window mid-stream). That is
  *exactly* the granularity our `_SegmentedDecompressorStream` already delivers for **xz** (block
  index) and **lzip** (member/trailer scan): seek = jump to the segment containing the offset,
  decode forward within it. So the likely-better path is a **small native zstd reader** that
  reuses that infrastructure — build a frame index by scanning frame headers (compressed size
  per frame from its header; decompressed size from the frame's optional `Frame_Content_Size`
  field or, when present, the *Seekable Zstd* skippable-frame seek table) — getting the same
  frame-granularity seeking **for free**, with zero new heavy dependency and no macOS risk. This
  is the zstd analogue of why we wrote `xz.py`/`lzip.py` instead of depending on `python-xz`.

  Things to confirm before committing to the native route:
  - **Does `indexed_zstd` do anything a frame-index reader wouldn't?** From the docs, no — it is
    frame-granularity only (no intra-frame seeking). If that holds, the native reader loses
    nothing. (`rapidgzip`-style intra-member seeking would be the only reason to prefer a heavy
    lib, and `libzstd-seek` does not do it.)
  - **Benchmark the candidates — "same granularity" doesn't mean "same speed".** Even at equal
    seek granularity, the C++ lib might still win on raw throughput (e.g. faster libzstd decode
    of the forward run within a frame, cheaper jump-table construction, less Python-level
    overhead) enough to justify adding it as an *accelerator* (the way `rapidgzip` is, behind an
    extra) rather than rejecting it. Decide with numbers, not just the feature comparison: build a
    representative large `.zst` (and a multi-frame / *Seekable Zstd* variant), then time several
    access patterns — cold full sequential read, `SEEK_END` + tail read, a scattered set of random
    seeks-then-reads, and a backward rewind — across the **stdlib `compression.zstd` reader**, the
    **native frame-index wrapper**, and **`indexed_zstd`**, measuring wall time, peak memory, and
    index-build cost. If the native wrapper is within a small constant of `indexed_zstd`, prefer it
    (no heavy dep, no macOS risk); if `indexed_zstd` is dramatically faster on a real workload,
    weigh it as an optional accelerator. A `benchmarks/`-style script (cf. DEV's `bench_xz.py`) is
    the natural home.
  - **The benefit only exists for multi-frame `.zst`.** A single-frame stream — the common
    default from the `zstd` CLI and most writers — has exactly one frame, so frame-granularity
    seeking yields a single seek point (offset 0) and helps neither approach; the win is real
    only for *Seekable Zstd* files or anything compressed with frame splitting (e.g. `.tar.zst`
    written that way). Worth measuring how often multi-frame `.zst` actually occurs before
    investing in either.
  - **Frames without `Frame_Content_Size`** can't be indexed without decoding them (this is also
    `libzstd-seek`'s slow fallback). The native reader can simply build the index only when sizes
    are available (header field or seek table) and otherwise fall back to the rewind path.

  Note `pyzstd.SeekableZstdFile` is **not** a substitute either: it reads only the *Seekable
  Zstd* container, not plain `.zst`. See `dev-docs/library-analysis.md` (zstd).

- **Opt-in free-space pre-flight for extraction** — before extracting, sum the *declared*
  uncompressed sizes of the **selected** members and compare against
  `shutil.disk_usage(dest).free`; if short, fail fast with a typed error *before writing
  anything*, instead of dying partway and leaving a half-written mess (the current
  behavior). Cheap where it matters: ZIP central directory, 7z/RAR headers, and TAR
  per-member headers all carry uncompressed sizes, so the estimate needs no decompression.
  **Deliberately opt-in and best-effort**, for real reasons — not laziness:
  - It is **not** a zip-bomb defense and must not be sold as one. Declared sizes can be
    absent, wrong, or adversarial; the ratio-guard / `ExtractionPolicy` already own the
    hostile-archive axis. This knob is a *convenience* against honest "disk too small"
    mistakes, so it trusts the metadata by design.
  - Free space is **approximate and racy**: transparent FS compression (btrfs/zfs), sparse
    files, reflink/dedupe, quotas, and other writers all move the target (TOCTOU). Advisory
    only; never a hard guarantee.
  - **Skip gracefully when the total is unknowable** — single-file `gz`/`bz2` (no reliable
    stored size) or a streamed/piped TAR — rather than blocking extraction.
  - Interacts with overwrite policy: replacing existing files changes the *net* delta, which
    a naive sum ignores; best-effort accepts that imprecision.
  Home: a library extract option / `ExtractionPolicy`-adjacent knob (the library has the
  sizes), surfaced by the CLI as a flag (opt-in first; could default-on for the CLI later if
  it proves low-friction). Small change of its own — not part of `cli-v1`. Verdict: a nice,
  cheap UX win worth doing, provided it ships clearly labeled as advisory so nobody mistakes
  it for a safety control.

## CLI (post-`cli-v1` follow-ups)

> Parked from PR #131 review decisions (Brief 4) so they survive merge of #120.
> The `cli-v1` change itself is implemented; these are the consciously deferred
> pieces — promote each with its own OpenSpec change when scheduled.

- ~~**Smart-dest post-hoc hoist (streaming / no-index)**~~ — **Done on #120**
  (R4): always-wrap then hoist a single top-level entry to cwd after a successful
  no-index extract. Remaining related work: stdin archive sources (Decision 15
  reserved `-`).

- **Skip-damaged-member iteration for `test` / salvage-adjacent reads** — CLI
  `test` now counts open-time failures and still prints the summary, but once
  `stream_members` raises the generator is dead and later members are lost
  (solid / poisoned streams). Library-side: surface per-member open errors
  without terminating iteration (e.g. yield `(member, error)` or a documented
  "skip damaged unit" mode) so `test` can continue where the format allows.
  Overlaps salvage (above) but is narrower — integrity reporting, not
  best-effort recovery of truncated archives.
- **Library `verify` / `VerifyReport` primitive** (api-coherence E2 / **Q5**) —
  **deferred past 0.2.0** when archiving the api-coherence deep review.
  CLI `test` hand-rolls the loop today; unclear whether callers verify without
  extracting often enough to justify a first-class API. Adjacent to
  skip-damaged-member iteration above.
- **`--json` machine output** (cli-product **P4** / Q2) — **wait for the `hash`
  verb / a designed member schema**; do not ship a provisional JSON-lines surface
  in 0.2.0. Flag name when it lands: `--json` (not `--porcelain`). Parked when
  archiving `cli-product/` (2026-07-20); also debt-ledger DD7.
- **`--raw` / TTY-only control-byte quoting** (cli-product **Q4** remainder) —
  recommended style (escape everywhere, backslash) already applied; optional
  `--raw` hatch for scripts that need exact names before `--json` exists is
  additive. Parked as debt-ledger DD8.
- **Lazy `ArchiveMember` derivation (perf L5)** — only named lever to bring
  ZIP open+list from ~4.4× (nightly realistic, 2026-07-23) into the aspirational
  2–3× peer band (`review/archive/2026-07-28-performance/listing-attribution.md`). Touches equality
  / accounting / listing contract → needs its own OpenSpec. **Deferred past
  0.2.0** when deciding debt-ledger Q2 (2026-07-20): bands are aspirational;
  measured ratios are good enough for everyday use. Same story for 7z listing
  ~2.1× / RAR ~2.4× vs ~1.25× native-par.

## Strategy & adoption (2026-07 review backlog)

> Parked here from the 2026-07 architecture-review discussion so nothing is lost.
> Security/compat items with a threat angle live in `dev-docs/threat-model.md` (the gap
> register); the product framing lives in `VISION.md`. These are the rest.

- **Salvage / best-effort read mode** — the founding use case (indexing decades of
  messy backups) is full of truncated and corrupt archives, and today every backend is
  all-or-error. A `salvage=True`-style read mode would yield every recoverable member
  plus per-member/status errors instead of one terminal exception: for ZIP, walk local
  headers when the central directory is gone; for TAR, resync on the next valid header;
  for single-file streams, return the decodable prefix with a truncation flag. Nobody
  does this well; it is both a founding need and a differentiator. Needs its own spec
  (interacts with error-handling and the equivalence matrix).
- **Hashes without decompression** — dedupe workflows can often use the digests the
  archive already stores (`member.hashes`: CRC32, RAR5 BLAKE2sp, …) instead of reading
  data. Document the recipe; consider a helper that returns "best available digest +
  provenance (stored vs computed)" so an indexer can choose cheap-but-weak vs
  costly-but-strong uniformly.
- **Benchmarks as a CI gate** — suite tracking open/list/read/extract wall time vs
  stdlib (`zipfile`/`tarfile`) and py7zr/libarchive where comparable, plus
  **bytes-decompressed and seek counts** (the real bottlenecks — re-decompression and
  seek storms — hide in wall time on small corpora). Budget per `VISION.md`: ≤1.3×
  stdlib common paths, ~2× when justified. Stand up before any perf-sensitive claim.
- **Public backend API** — stabilize/export the `ReadBackend` ABC + registry so rare
  formats (CAB, CPIO, SquashFS, WIM, XAR, DMG…) can be third-party plugins instead of
  a solo compatibility treadmill. Decide pre-1.0 (it constrains how freely the backend
  contract can change afterwards).
- **fsspec adapter** — expose an opened archive as an fsspec filesystem
  (`ArchiveFileSystem`); big adoption channel (pandas/dask/HF datasets ecosystems) and
  a good stress test of the reader contract. Also the natural place for
  `open_archive("https://…")` stories rather than teaching core about URLs.
- **Migration guide** — `zipfile`/`tarfile`/`shutil.unpack_archive`/`patool` →
  archivey, gotcha-by-gotcha ("`tarfile.extractall` without `filter=` does X; here it
  cannot happen"). Cheap, high-leverage for the "default library" goal.
- **Warnings-as-data sweep** — audit every `logger.warning` in the library: each should
  (also) be queryable as data (member/info field, `FormatInfo`, `CostReceipt`,
  `ExtractionResult`), since most applications never surface logging. See
  `dev-docs/threat-model.md` C2.
- **Extraction collision handling + `OverwritePolicy.RENAME`** — deterministic
  cross-platform handling of casefold/normalization collisions (threat-model O2), plus
  an opt-in RENAME policy (`name (1)`) for archives with intentional duplicates.
- **Writing, done properly, later** — writing is deliberately post-reading (possibly
  post-1.0). When specced, design in from the start: **reproducible output**
  (`SOURCE_DATE_EPOCH`, stable member ordering, normalized metadata — the build-tool
  adoption wedge) and the **metadata-fidelity boundary** (xattrs/ACLs — threat-model
  C3; read-side is additive later, but write-side fidelity must be a day-one decision
  of the writing spec, since it shapes `add_member` and the round-trip contract).
- **Free-threading position** (threat-model C4) — parallel extraction / parallel
  decode under 3.13t; interacts with the existing parallel-extraction idea above.
- **CLI earlier, as dev tool + demo** — ~~`archivey list/test/extract` was
  invaluable…~~ **Done in `cli-v1` (PR #120).** Remaining CLI backlog lives under
  **CLI (post-`cli-v1` follow-ups)** above.
