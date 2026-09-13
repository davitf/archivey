# C. Extraction, policies, results

Spec: `safe-extraction`.
Pages: `extracting`, `index`, `cli`, `gotchas`, `migrating`, `opening-and-listing`.

| # | Claim | Stated at | Settles it | Ruling | V |
|---|---|---|---|---|---|
| C-1 | Extraction is **safe by default — you opt out, not in** | `extracting.md:3`, `index.md:21-22`, `index.md:56-57`, `philosophy.md:27-29`, `migrating.md:7-8` | `safe-extraction:130` | Keep | |
| C-2 | `[code]` the one-shot block runs, and its comment states the true defaults: `policy=ExtractionPolicy.STRICT`, `overwrite=ERROR`, `on_error=STOP` | `extracting.md:7-10`, `index.md:23` | `safe-extraction:21`, `src/archivey/internal/extraction_types.py:59`, `:75`, `:94` | Keep | |
| C-3 | **The archive is untrusted in every byte** — names, link targets, sizes, timestamps, comments, header structures, compressed streams; crafted archives are in scope for *all* guarantees | `extracting.md:14-16` | `safe-extraction:130` | `Trim to ~3 lines; rest → TM` — this clause is one of the two that stay | |
| C-4 | **An earlier extracted member is untrusted input to every later one** — which is why symlink targets are re-resolved against the live tree after creation | `extracting.md:17-20` | `safe-extraction:311` | `Trim`; the clause stays | |
| C-5 | `[TM]` The local process and other local processes are trusted; a local attacker racing the extraction is out of scope | `extracting.md:21-24` | `safe-extraction:130` (scope statement) | `→ TM` — verify when the threat-model edit is written | |
| C-6 | `[TM]` Optional dependencies and external tools are trusted code but not trusted to be robust; their failures surface as translated archivey errors, never silently wrong data | `extracting.md:25-27` | `error-handling:259`, `compressed-streams:137` | `→ TM` — verify when the threat-model edit is written | |
| C-7 | **Path traversal is rejected before any write**: `..` on any separator, absolute paths, drive letters, UNC prefixes, null bytes; the destination parent is resolved and containment-checked | `extracting.md:31-33`, `index.md:21-22`, `migrating.md:45-48`, `philosophy.md:27-28` | `safe-extraction:130` | `Trim → one clause` | |
| C-8 | `[TM]` A **file** member whose normalized name is `"."` or `""` is rejected with `PathTraversalError`; only a directory member may name the extraction root | `extracting.md:34-37` | `safe-extraction:130` | `→ TM` — verify when the threat-model edit is written | |
| C-9 | `[TM]` Symlink escapes are caught in **three layers** — lexical check at planning, parent-dir resolution, post-`os.symlink` re-resolution against the real filesystem — and escaping links are removed and rejected | `extracting.md:38-41` | `safe-extraction:311` | `→ TM` — verify when the threat-model edit is written | |
| C-10 | **Symlink escapes are blocked by default** (the caller-visible half of C-9) | `index.md:21-22`, `index.md:56-57`, `migrating.md:46-48`, `philosophy.md:27` | `safe-extraction:311` | Keep | |
| C-11 | `[TM]` Hardlink targets are containment-checked and **resolved positionally**, so a crafted duplicate-name archive cannot redirect a link | `extracting.md:42-43` | `safe-extraction:332` | `→ TM`; the caller-visible identity rule survives as C-35 | |
| C-12 | **Overwrite handling never writes through a symlink** — it replaces them, never follows | `extracting.md:44-45` | `safe-extraction:404`, `safe-extraction:829` | `Trim → one clause` | |
| C-13 | Writes are **atomic**: temp file + `os.replace`, so an interrupted extraction never leaves a half-written destination file | `extracting.md:45-46` | `safe-extraction:404` | `Trim → one clause` | |
| C-14 | Temp files are named **`.archivey-tmp-<random>`** and staged **inside the destination directory** | `extracting.md:80-82` | `safe-extraction:404` | Keep | |
| C-15 | Any Python-level failure removes them; **only a hard kill (SIGKILL, power loss) leaves one behind**, and leftovers are safe to delete before re-running | `extracting.md:82-84`, `extracting.md:181` | `safe-extraction:404` | Keep | |
| C-16 | **Special files** (devices, FIFOs, sockets) are **always** rejected — at every policy | `extracting.md:47-48` | `safe-extraction:130` | `Trim → one clause` | |
| C-17 | NTFS **junctions** are detected, flagged, and never traversed | `extracting.md:47-48` | `safe-extraction:130` | `Trim → one clause` | |
| C-18 | A member name or link target containing a bidi **override or isolate** (U+202A–202E, U+2066–2069) is rejected with `DeceptiveNameError` under `STRICT` **and `STANDARD`** | `extracting.md:49-52` | `safe-extraction:878`, `src/archivey/exceptions.py:174` | Keep, shorter | |
| C-19 | The three **directional marks** (U+061C, U+200E, U+200F) are **not** rejected — they reorder nothing and occur in legitimate Arabic and Hebrew filenames | `extracting.md:53-55`, `errors-and-diagnostics.md:61` | `safe-extraction:878`, `src/archivey/diagnostics.py:129` | Keep, shorter | |
| C-20 | Right-to-left script itself is unaffected: `فهرس.txt` contains no control character | `extracting.md:55-56` | `safe-extraction:878` | Keep, shorter | |
| C-21 | Listing and reading **always present either kind exactly as stored**, with a `MEMBER_NAME_BIDI_CONTROL` diagnostic | `extracting.md:56-57`, `errors-and-diagnostics.md:61` | `diagnostics:211`, `src/archivey/diagnostics.py:63` | Keep, shorter | |
| C-22 | **`TRUSTED` lifts the bidi rejection** and extracts the member under its stored name; a caller filter that renames also works at any policy since the check runs on the final name | `extracting.md:59-65` | `src/archivey/internal/extraction_types.py:48-65`, `safe-extraction:367` | `→ DS + one line` | |
| C-23 | **Decompression bombs** are capped five ways at extraction: cumulative output, per-member ratio, archive-wide static ratio, **live** ratio for unknown-size/pipe sources, and an entry-count cap | `extracting.md:66-68`, `extracting.md:189-190` | `safe-extraction:479`, `:499`, `:779`, `:807`, `:850` | `Trim → one clause` (§Limits is the detail) | |
| C-24 | The **global** guards halt even under `OnError.CONTINUE` | `extracting.md:68`, `extracting.md:176`, `gotchas.md:37-40` | `safe-extraction:521`, `safe-extraction:712` | Keep | |
| C-25 | **setuid/setgid/sticky are stripped except under `TRUSTED`**; ownership is applied only under `TRUSTED` as root | `extracting.md:69-70`, `extracting.md:178`, `philosophy.md:54` | `safe-extraction:367` | `Trim → one clause` | |
| C-26 | `[TM]` Cross-platform name safety under STRICT/STANDARD is casefold+NFC collision tracking, reserved device names and `:` rejected, trailing-dot/space strip, non-UTF-8 percent-escape sanitization, `OverwritePolicy.RENAME` | `extracting.md:71-73` | `safe-extraction:878` | `→ TM`; the caller-visible consequences survive as C-33/C-37/C-39 | |
| C-27 | `[TM]` C++-threaded accelerators are close-guarded with `weakref.finalize` so crafted-input error paths cannot leave aborting threads | `extracting.md:76-78` | `seekable-decompressor-streams:161` | `→ TM`; the caller rule survives as F-24 | |
| C-28 | `[code]` the §Policies block runs, and its `import` line resolves all five names (`ExtractionPolicy, OverwritePolicy, OnError, ExtractionLimits, ListingLimits`) | `extracting.md:88-106` | `src/archivey/config.py:97`, `:119`, `src/archivey/internal/extraction_types.py:36`, `:66`, `:83` | Keep | |
| C-29 | `ListingLimits(max_members=…)` passed via `ArchiveyConfig` makes `reader.members()` raise `ResourceLimitError` when the central directory is larger | `extracting.md:100-105`, `extracting.md:191-193` | `archive-reading:339`, `safe-extraction:104` | Keep | |
| C-30 | **`OnError` governs per-member failures only** — corrupt/truncated data, write errors, overwrite conflicts under `ERROR` | `extracting.md:108-109` | `safe-extraction:712` | Keep | |
| C-31 | **A policy block is always recorded as `BLOCKED` and extraction continues**, under either `STOP` or `CONTINUE` | `extracting.md:109-111`, `extracting.md:177`, `cli.md:28-29` | `safe-extraction:712`, `safe-extraction:595` | Keep | |
| C-32 | `abort_on` exists, is **independent of `OnError`**, and names exactly three events | `extracting.md:113-122` | `safe-extraction:951`, `src/archivey/internal/extraction_types.py:98-128` | Keep | |
| C-33 | `[code]` the `abort_on={AbortOn.BLOCKED_MEMBER}` example runs | `extracting.md:116-120` | — (executable) | Keep | |
| C-34 | `AbortOn.BLOCKED_MEMBER` fires when a member is refused by a path-safety check or a policy filter, and raises the underlying `FilterRejectionError` | `extracting.md:124-126` | `safe-extraction:951`, `src/archivey/internal/extraction_types.py:116` | `→ DS` — the depth exists as a `#` comment today (`scope.md` §Precondition) | |
| C-35 | `AbortOn.NAME_COLLISION` fires when a second member resolves to an already-written destination (non-`TRUSTED`), raising `NameCollisionError` | `extracting.md:127` | `safe-extraction:951`, `src/archivey/internal/extraction_types.py:121` | `→ DS` | |
| C-36 | `AbortOn.NAME_SANITIZED` fires when a name is rewritten to its portable spelling, raising `NameRewrittenError` | `extracting.md:128` | `safe-extraction:951`, `src/archivey/internal/extraction_types.py:128` | `→ DS` | |
| C-37 | **An abort is immediate: no later member is processed and no report is returned** — you handle an exception, not a return value | `extracting.md:130-131` | `safe-extraction:951` | Keep, one line | |
| C-38 | Output already written stays on disk; an abort **stops** the run, it does not roll it back | `extracting.md:131-133` | `safe-extraction:951`, `safe-extraction:712` | Keep, one line | |
| C-39 | `NAME_COLLISION` fires on **every** collision whatever `OverwritePolicy` does — replaced, skipped, errored or renamed — because the trigger is the collision, not its resolution | `extracting.md:135-137` | `safe-extraction:951`, `safe-extraction:404` | `→ DS` — verbatim in the `NAME_COLLISION` comment already | |
| C-40 | `NAME_SANITIZED` is a **narrow escape hatch**: it fires on a *successful* rewrite, and no policy or preset implies it | `extracting.md:139-141` | `safe-extraction:951` | `→ DS` | |
| C-41 | To merely **audit** rewrites, read `ExtractionResult.presented_name` and let extraction finish | `extracting.md:142-143`, `extracting.md:172` | `safe-extraction:595` | `→ DS` / Keep (the `Need to know` row) | |
| C-42 | **S-2 (pre-seeded by `scope.md` §Findings, found independently by #241).** The policy table lists **two** rows for a **three-member** enum: `STANDARD` is absent, while the page's own prose uses it four times | `extracting.md:145-149` (table) vs `extracting.md:51`, `:71`, `:173`, `:175` | `src/archivey/internal/extraction_types.py:36-64` (three members), `safe-extraction:367` | **Keep, fix** — carried in, not re-derived | |
| C-43 | `STRICT` is for untrusted archives and is the default | `extracting.md:147`, `extracting.md:9`, `migrating.md:82-83` | `src/archivey/internal/extraction_types.py:59`, `safe-extraction:21` | Keep, fix | |
| C-44 | `TRUSTED` allows ownership / sticky bits when running as root and **still refuses traversal** | `extracting.md:148`, `extracting.md:178`, `philosophy.md:54` | `src/archivey/internal/extraction_types.py:61-64`, `safe-extraction:367` | Keep, fix | |
| C-45 | Archivey's three policies are **`STRICT` / `STANDARD` / `TRUSTED`** and apply to *every* format, not just tar; `STRICT` is closest to `tarfile`'s `filter="data"` | `migrating.md:80-83` | `src/archivey/internal/extraction_types.py:36-64`, `safe-extraction:367` | Keep | |
| C-46 | `[code]` the selective-extract block (`reader.extract_all("out/", members=["only/this.txt"])`) runs | `extracting.md:152-155` | `safe-extraction:65` | Keep | |
| C-47 | `get(name)` is **last-wins** when names collide | `extracting.md:161`, `opening-and-listing.md:173-174`, `gotchas.md:28-29` | `archive-reading:406` | Keep | |
| C-48 | `extract_all(members=["x"])` matches **every** member named `x`; pass an `ArchiveMember` for one identity | `extracting.md:162-163`, `opening-and-listing.md:182-187`, `gotchas.md:28-31` | `archive-reading:805`, `src/archivey/internal/selection.py:11-38` | Keep | |
| C-49 | **Hardlink targets resolve to an earlier same-named member by `member_id`**, not to whichever `get` would return | `extracting.md:164-166` | `safe-extraction:332` | Keep — receives the rule from `42-43` | |
| C-50 | Members with `is_current=False` stay visible in listings but are **skipped on extract by default**, and the skip is reported as **`ExtractionStatus.SUPERSEDED`** — distinct from `NOT_OVERWRITTEN`, which is about a file already on disk | `extracting.md:166-167`, `opening-and-listing.md:176-180`, `formats.md:120-122` | `safe-extraction:254`, `src/archivey/internal/extraction_types.py` (`ExtractionStatus.SUPERSEDED`) | Keep | |
| C-51 | **Safe ≠ unlimited**: huge/hostile archives can still raise `ResourceLimitError` unless you raise limits | `extracting.md:171`, `extracting.md:189-197` | `safe-extraction:479` | Keep (the table row is `Trim to ~6 rows`; §Limits keeps it) | |
| C-52 | **STRICT rewrites some names** — trailing dots/spaces stripped, non-UTF-8 percent-escaped — so the disk path may differ from `member.name` | `extracting.md:172`, `gotchas.md:33-36` | `safe-extraction:878` | Keep (one of the six surviving rows) | |
| C-53 | Under `STRICT`/`STANDARD`, `README`/`readme` **and NFC/NFD twins collide on all platforms**, not just Windows | `extracting.md:173`, `gotchas.md:34-35` | `safe-extraction:878` | Keep (surviving row) | |
| C-54 | `OverwritePolicy.REPLACE` **is not a silent merge**: the clobbered member's result is revised to `OVERWRITTEN` | `extracting.md:173` | `safe-extraction:404`, `safe-extraction:595` | Keep (surviving row) | |
| C-55 | `OverwritePolicy.RENAME` produces `photo (1).jpg`-style names for intentional duplicates | `extracting.md:173`, `cli.md:18` | `safe-extraction:404`, `src/archivey/internal/extraction_types.py:80` | Keep (surviving row) | |
| C-56 | `ExtractionResult.collided_with` names the already-written path a member collided with, **under every resolution**, and is `None` when the destination was simply already on disk | `extracting.md:174` | `safe-extraction:595` | Keep (surviving row) | |
| C-57 | Reserved device names and `:` are rejected under `STRICT`/`STANDARD` **on every platform** (`CON`, `NUL`, `file:ads`) | `extracting.md:175` | `safe-extraction:878` | Keep (surviving row) | |
| C-58 | Excluding a hardlink's source can **orphan the link** (especially on streaming sources), and `OnError` decides fail vs continue | `extracting.md:179` | `safe-extraction:332` | Keep (surviving row) | |
| C-59 | Unlike `tarfile`, archivey **does not copy target bytes through a symlink** on symlink-hostile filesystems — you get a typed failure or skip | `extracting.md:180` | `safe-extraction:829` | Keep (surviving row) | |
| C-60 | **Nested-archive recursion is caller-driven**; a zip-quine loops only if you loop | `extracting.md:182`, `extracting.md:204-206`, `gotchas.md:41-44` | `safe-extraction:521` | Keep | |
| C-61 | **The bomb tracker is per-archive and not nesting-aware**, so a zip-of-zips can amplify past your budget one level at a time | `extracting.md:203-206`, `gotchas.md:41-43` | `safe-extraction:521`, `safe-extraction:779` | Keep, unchanged (Q5: the pointer is the coverage) | |
| C-62 | **Bomb guards apply during extraction; `ListingLimits` apply when materializing `members()`; `stream_members()` is intentionally unguarded** | `extracting.md:183`, `extracting.md:191-201`, `gotchas.md:37-40` | `safe-extraction:521`, `archive-reading:339` | Keep | |
| C-63 | `ExtractionLimits` caps total extracted bytes, compression ratio and entry count; trips raise `ResourceLimitError` | `extracting.md:189-190` | `safe-extraction:479`, `:499`, `:807` | Keep, unchanged | |
| C-64 | `ListingLimits` caps member count **and retained metadata bytes**, on `members()` / `scan_members()` / extract-prep materialization | `extracting.md:191-193` | `archive-reading:339`, `archive-reading:378` | Keep, unchanged | |
| C-65 | Limits are loosened per call with `limits=` (**extraction only**), `listing_limits` at `open_archive(config=…)`, or the two `UNLIMITED` sentinels | `extracting.md:195-197`, `philosophy.md:54-55` | `safe-extraction:104`, `src/archivey/config.py:97`, `:119` | Keep, unchanged | |
| C-66 | **must-explain #8 (§B row 4's survivor, ~3 lines, unwritten):** `extract_all(config=)` cannot raise the listing ceiling set at open time | *no page states it* | `safe-extraction:104`, `archive-reading:717` | **Guide** — the last open §B row-4 sub-row | |
| C-67 | RAR member data may be decompressed by the system `unrar`, whose availability and behaviour are part of your deployment's trust boundary — keep it updated | `extracting.md:218-220`, `index.md:54-55`, `formats.md:22-23` | `format-rar:127`, `packaging-and-extras:142` | Keep | |
| C-68 | Prefer extracting untrusted archives into a **dedicated directory with limited permissions**, then validating before promoting | `extracting.md:222-223` | — (operational guidance; no spec line) | Keep | |
| C-69 | **Every block and every name rewrite is recorded on the returned `ExtractionReport`**, not only in logs | `extracting.md:227-228`, `errors-and-diagnostics.md:43-45`, `gotchas.md:105-106` | `safe-extraction:595`, `safe-extraction:755` | Keep | |
| C-70 | `zipfile.extractall` **mangles** absolute paths and `..` but happily writes symlinks pointing outside the destination — archivey blocks both and reports `ExtractionStatus.BLOCKED` | `migrating.md:45-48` | `safe-extraction:130`, `safe-extraction:311`, `src/archivey/internal/extraction_types.py:144` | Keep — the page's strongest safety claim | |
| C-71 | `shutil.unpack_archive` returns `None`; `archivey.extract` returns an `ExtractionReport` saying what was written, skipped or blocked | `migrating.md:112-113` | `safe-extraction:755` | Keep | |
| C-72 | Archives that "worked" with `extractall` may now report `BLOCKED` members — check the `ExtractionReport` rather than assuming success | `migrating.md:161-163` | `safe-extraction:595` | Keep | |
| C-73 | `[code]` the `shutil.unpack_archive` before/after pair runs | `migrating.md:95-103` | — (executable) | Keep | |
| C-74 | `[code]` the `patool` / `subprocess 7z` before/after pair runs, and `archivey.extract("archive.7z", dest)` needs no external binary | `migrating.md:117-124` | — (executable) | Keep | |

## C — problems and gaps met while extracting

- **C-42 (S-2) has a second half nobody has stated.** The missing table row is the found
  defect; the *reason* it is easy to miss is that `STANDARD` has no `api.md`-rendered
  docstring either (`extraction_types.py:60` carries a `#` comment). Whoever fixes the
  table under Q1's carve-out can drain both at once.
- **C-66 is the one §B row-4 survivor and no page states it.** It is a *silence* claim in
  the brief's sense ("silence is a claim too"), so it is recorded as a row with no
  `Stated at` rather than as a gap note — otherwise it disappears at the next re-tally.
- Seven `extracting.md` blocks are `→ TM` — C-5, C-6, C-8, C-9, C-11, C-26, C-27 — and
  `formats.md`'s `NumCyclesPower` clamp (E-34) is an eighth elsewhere. All eight carry
  `[TM]` and are **not** verified here. The register must not inherit them unverified;
  that check belongs to whoever writes the threat-model edit.

---

