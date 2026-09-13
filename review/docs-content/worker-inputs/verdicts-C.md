# Verdicts — Worker C (Extraction, policies, results)

Config for this pass: **`[all]`** (`uv sync --group dev --extra all`). Spec citations
preferred over `src/` when both speak (O-26). Spec Settles-it line numbers drift — matched
by requirement title/text. `[code]` rows executed with `uv run --no-sync` against temp
ZIP/TAR fixtures under `/tmp/c-verify*` plus `tests/fixtures/sevenzip/lz4.7z` for C-74.
`[TM]` rows were **not** verified (left for the threat-model edit).

| # | V | Evidence |
|---|---|---|
| C-1 | verified | Guide + `safe-extraction` Non-Bypassable Universal Path-Safety Constraints: default reject/raise; `TRUSTED` does not bypass. One-shot defaults `STRICT`. Same “opt out” framing on `index.md`, `philosophy.md`, `migrating.md`. |
| C-2 | verified | `[code]` `archivey.extract("archive.zip","out/")` ran; comment defaults match signature: `policy=STRICT`, `overwrite=ERROR`, `on_error=STOP` (`safe-extraction` One-Shot Extraction API). |
| C-3 | verified | Universal path-safety applies under all policies including `TRUSTED` (spot-check: `../x` → `BLOCKED`/`PathTraversalError` under `TRUSTED`). Spec: archive bytes are the untrusted input; crafted archives in scope. |
| C-4 | verified | `safe-extraction` Symlink Escape Re-Validated at Extraction Time: post-`os.symlink` re-resolve against live tree; chained attacks via earlier members. Matches extracting trust-boundary bullet. |
| C-5 | left for TM | `[TM]` out of scope this pass. |
| C-6 | left for TM | `[TM]` out of scope this pass. |
| C-7 | verified | Spec universal safety matrix + spot-check: `../x` and `/abs` → `BLOCKED`/`PathTraversalError` before write; `ok.txt` extracted. |
| C-8 | left for TM | `[TM]` out of scope this pass. |
| C-9 | left for TM | `[TM]` out of scope this pass. |
| C-10 | verified | Spec symlink revalidation + spot-check: escaping symlink → `BLOCKED`/`SymlinkEscapeError`; link removed (`link exists? False`). Caller-visible half of C-9. |
| C-11 | left for TM | `[TM]` out of scope this pass. |
| C-12 | verified | `safe-extraction` Overwrite Policy: `REPLACE` never write-through; `os.replace` replaces symlink entry. Spot-check: dest symlink to outside file replaced; outside bytes unchanged (`OUTSIDE`). |
| C-13 | verified | Same Overwrite Policy: FILE replacement via temp + `os.replace`; mid-stream failure preserves existing, discards temp. |
| C-14 | verified | Spec stages beside destination; implementation prefix `.archivey-tmp-` present in extraction coordinator; temps live under dest parent. |
| C-15 | verified | Spec: failure discards temp. Spot-check: successful extract leaves no `.archivey-tmp-*`. “Only hard kill / power loss leaves one” is the ordinary consequence of Python-level cleanup (not reproduced with SIGKILL here). |
| C-16 | verified | Spec: `MemberType.OTHER` → `SpecialFileError` all policies. Spot-check FIFO → `BLOCKED` under `STRICT`/`STANDARD`/`TRUSTED`. |
| C-17 | unverifiable (platform) | NTFS junctions are Windows-only (`tests/test_directory.py` skips unless `win32`). Linux session cannot create/traverse junctions. Settles-it cite `safe-extraction` universal safety does not name junctions; surface rule lives in `archive-data-model` (`is_junction`). |
| C-18 | verified | Spec bidi name matrix + spot-check: U+202E → `DeceptiveNameError`/`BLOCKED` under `STRICT`/`STANDARD`; extracts under `TRUSTED`. |
| C-19 | verified | Spec: directional marks accepted. Spot-check: U+200E name extracted under `STRICT`; listing emits `MEMBER_NAME_BIDI_CONTROL`. |
| C-20 | verified | Spec + spot-check: `فهرس.txt` extracts; no rejection. |
| C-21 | verified | Spec listing presents stored name; `diagnostics` `MEMBER_NAME_BIDI_CONTROL`. Spot-check: retained diagnostic code `MEMBER_NAME_BIDI_CONTROL`; `members()` shows stored name. |
| C-22 | verified | Spec: `TRUSTED` extracts bidi; filter rename rescues at any policy. Spot-check: `TRUSTED` wrote stored name; `STRICT`+filter rename → `safe.exe` extracted. |
| C-23 | verified | Five extraction guards in `safe-extraction`: cumulative bytes, per-member ratio, static archive-wide ratio, live archive-wide ratio, max entries. |
| C-24 | verified | Spec OnError: global resource guards always-stop. Spot-check: `OnError.CONTINUE` + tiny `max_extracted_bytes` → `ResourceLimitError` (always-stop subclass); later members not all written. |
| C-25 | verified | Spec metadata policy + extraction chown gate: ownership only under `TRUSTED` as root. Spot-check: setuid stripped under `STRICT`/`STANDARD`, kept under `TRUSTED`. Guide “sticky stripped except `TRUSTED`” matches **code**; `safe-extraction` matrix says `STANDARD` strips setuid/setgid only (sticky preserved) — harvest. |
| C-26 | left for TM | `[TM]` out of scope this pass. |
| C-27 | left for TM | `[TM]` out of scope this pass. |
| C-28 | verified | `[code]` policies block: all five imports resolve; `extract(...)` + `open_archive(... ListingLimits ...)` ran. |
| C-29 | verified | Spec Listing resource limits (`archive-reading`) + spot-check: `ListingLimits(max_members=5)` → `ResourceLimitError` on `members()`. |
| C-30 | verified | Spec Error Policy (OnError): failures only (corrupt/write/overwrite ERROR). Matches `OnError` enum docstring. |
| C-31 | verified | Spec: blocks always `BLOCKED` and continue under either OnError. Spot-check: default `STOP` with `../evil` + `good.txt` returned report with `BLOCKED`+`EXTRACTED`; `good.txt` on disk. CLI prose matches. |
| C-32 | verified | Spec Abort-on-event: three members; independent of OnError. Spot-check: `list(AbortOn)` = `BLOCKED_MEMBER`, `NAME_COLLISION`, `NAME_SANITIZED`. |
| C-33 | verified | `[code]` `abort_on={AbortOn.BLOCKED_MEMBER}` on traversal zip → `PathTraversalError` (FilterRejectionError subclass). |
| C-34 | verified | Spec abort table + spot-check: raises underlying `FilterRejectionError` (`PathTraversalError` in MRO). |
| C-35 | verified | Spec + spot-check: `README`/`readme` + `abort_on={NAME_COLLISION}` → `NameCollisionError`. |
| C-36 | verified | Spec + spot-check: trailing-dot `foo.` + `abort_on={NAME_SANITIZED}` → `NameRewrittenError`; without abort, rewrite to `foo` with `presented_name='foo.'`. |
| C-37 | verified | Spec: abort immediate, no report. Spot-check: exception, no returned report. |
| C-38 | verified | Spec: earlier output stays. Spot-check: `a.txt` kept, later `c.txt` absent after abort on `../evil`. |
| C-39 | verified | Spec: collision fires for every OverwritePolicy resolution. Spot-check: `REPLACE` + `abort_on={NAME_COLLISION}` → `NameCollisionError`. |
| C-40 | verified | Spec: narrow hatch; no policy implies it. Spot-check: default `STRICT` rewrite of `foo.` completes with report (no raise). |
| C-41 | verified | Spec ExtractionResult `presented_name` + spot-check audit path without abort. |
| C-42 | verified | **Defect claim true (S-2):** policy table has only `STRICT`/`TRUSTED`; enum has three members including `STANDARD`; prose uses `STANDARD` four times. |
| C-43 | verified | Spec + enum: `STRICT` default for untrusted; one-shot default `STRICT`. |
| C-44 | verified | Spec metadata + universal safety: `TRUSTED` may apply ownership/sticky; path safety still on. Spot-check: `TRUSTED` still `BLOCKED` on `../x`. |
| C-45 | verified | Spec Policy-Specific Metadata Transforms: three policies, all formats; closest to tarfile `data`/`tar`/`fully_trusted` mental model. Matches `migrating.md`. |
| C-46 | verified | `[code]` `extract_all(..., members=["only/this.txt"])` wrote only that member. |
| C-47 | verified | `archive-reading` Name lookup: `get` last-wins. Spot-check: duplicate `x.txt` → `get` returns second (`b"second"`). |
| C-48 | verified | `archive-reading` Collection form of MemberSelector: str matches every name; `ArchiveMember` by identity. Spot-check: `members=["x"]` → two results; single `ArchiveMember` → one identity. |
| C-49 | verified | `archive-reading` Transparent link following (positional hardlink: most recent matching target strictly before link) + extraction uses `link_target_member`. Spot-check: hardlink before later `src` got `FIRST`; `get("src")` is later `SECOND`. |
| C-50 | verified | Spec Skip non-current: `SUPERSEDED` vs `NOT_OVERWRITTEN`. Spot-check: non-current → `SUPERSEDED`; pre-existing+`SKIP` → `NOT_OVERWRITTEN`. |
| C-51 | verified | Spec bomb limits / ResourceLimitError. Spot-checks under C-24/C-63. |
| C-52 | verified | Spec cross-platform name safety O3: `STRICT` strips trailing dot/space. Spot-check: `name. ` → disk `name`, `presented_name='name. '`. |
| C-53 | verified | Spec O2: casefold+NFC collisions on all platforms. Spot-check: `README`/`readme` and NFC/NFD `café` collide on Linux. |
| C-54 | verified | Spec: `REPLACE` revises earlier to `OVERWRITTEN`. Spot-check: `README`→`OVERWRITTEN`, `readme`→`EXTRACTED`. |
| C-55 | verified | Spec RENAME `name (N)` before suffix. Spot-check: casefold pair → `readme (1)`; algorithm matches `photo (1).jpg` style. |
| C-56 | verified | Spec `collided_with` under every resolution; `None` for pre-existing. Spot-check: SKIP/REPLACE/RENAME/ERROR set field on colliding member; pre-existing obstacle → `None`. |
| C-57 | verified | Spec O3/O4: `CON`/`NUL`/`:` rejected under `STRICT`/`STANDARD` every platform. Spot-check: all → `BLOCKED`/`UnportableNameError` on Linux. |
| C-58 | verified | Spec hardlink matrix: excluded source on forward-only → per-member failure / OnError. Spot-check streaming: `CONTINUE`→`FAILED`; `STOP` raises. (Seekable recovers by materializing at link path — claim’s “especially streaming” holds.) |
| C-59 | verified | Spec Symlink extraction is target-independent / fails safe. Spot-check: archivey creates real symlink (does not copy target bytes through it). Hostile-FS failure mode per spec (not exercised on Linux). |
| C-60 | verified | Spec bomb scope + nested caller-driven. Spot-check: zip-of-zip extracts outer file only; no auto inner extract. |
| C-61 | verified | Spec: bomb tracker per extraction call / not nesting-aware; recursion caller-driven. Matches Limits prose. |
| C-62 | verified | Spec bomb-scope + ListingLimits: extract bombs vs listing materialization; `stream_members` unguarded. Spot-check: `max_members=5` still streamed 20 members. |
| C-63 | verified | Spec ExtractionLimits + spot-check `max_entries=3` → `ResourceLimitError`. |
| C-64 | verified | Spec ListingLimits: `max_members` + `max_metadata_bytes` on materializing paths. Fields present on dataclass. |
| C-65 | verified | Spec limits/config matrix: per-call `limits=`; listing at open; `UNLIMITED` sentinels exist. Spot-check both `UNLIMITED` objects. |
| C-66 | verified | **Silence claim true:** reader opened with `listing_limits=max_members=5`; `extract_all(config=ArchiveyConfig(listing_limits=…1000))` still raised `ResourceLimitError` at listing ceiling 5 (`archive-reading` / `safe-extraction` config lifetime). No guide page states it. |
| C-67 | verified | `format-rar` Use RARLAB unrar only for member data; trust/deployment boundary. `unrar` present this session. |
| C-68 | verified | Operational guidance only (no spec line); consistent with safe-default posture; nothing to falsify. |
| C-69 | verified | Spec ExtractionReport / per-member results sole record of blocks/rewrites. Spot-check: blocked traversal recorded on report as `BLOCKED`. |
| C-70 | verified | Spot-check: `zipfile.extractall` mangled `../evil.txt` into dest as `evil.txt`; archivey → `BLOCKED`/`PathTraversalError`, nothing written. Symlink-escape half via C-10 + spec. |
| C-71 | verified | `[code]`/`migrating` pair: `shutil.unpack_archive` returns `None`; `archivey.extract` returns `ExtractionReport`. |
| C-72 | verified | Spec BLOCKED results + migrating bite #1. Spot-check traversal zip “worked” for zipfile, `BLOCKED` for archivey. |
| C-73 | verified | `[code]` migrating shutil before/after on `bundle.tar.xz`: both wrote `hi.txt`; archivey returned `ExtractionReport`, shutil `None`. |
| C-74 | verified | `[code]` `archivey.extract` on `lz4.7z` succeeded with `7z` scrubbed from `PATH` (`which 7z` → None); native reader, no external binary. |

## Notes for coordinator

### Wrong rows
- *(none)*

### `[TM]` left unverified
- C-5, C-6, C-8, C-9, C-11, C-26, C-27 (seven rows)

### Config / platform notes
- Everyday verification: **`[all]`**.
- C-17: Windows-only junctions → `unverifiable (platform)` on this Linux session.
- C-74 scrubbed `7z` from `PATH` to prove native extract; restored after.
- C-24/C-63 always-stop limit errors surface as `_AlwaysStopResourceLimitError` (subclass of `ResourceLimitError`).

### Cross-cluster / process
- **B-26 pattern on C-25:** guide+code strip sticky under `STANDARD`; `safe-extraction` metadata matrix says `STANDARD` strips setuid/setgid only (sticky preserved). Guide claim verified; spec drift harvested.
- **C-42 (S-2)** confirmed: three-member enum vs two-row table; `STANDARD` also lacks a rendered docstring (comment only) — same fix surface.
- **C-66** is the §B row-4 silence survivor; fact verified, still unwritten on any page.
- Settles-it line numbers drift (same lesson as A/B); matched by requirement title.
- C-49 Settles-it pointed at Hardlink Two-Pass; the positional-vs-`get` rule is sharper under `archive-reading` Transparent link following.
- C-17 Settles-it pointed at universal path-safety; junction surfacing is `archive-data-model`.

### Counts
- **verified:** 66
- **wrong:** 0
- **unverifiable:** 1 (C-17 platform)
- **left for TM:** 7
- **total rows:** 74
