# Outline of the final user guide

The worklist for phase 3's second change (the page splits) **and** the starting
point for Topic 8 (content). Written between the two, deliberately: outlining
before the moves would have meant outlining a tree about to lose 80% of its files;
outlining after the splits would be too late to move a page boundary cheaply.

Measured against `main` @ `d34489f`, after `docs-ia-unpublish-maintainer-tree`
landed. Every source citation is a live `file:lines` in that tree.

| Field | What it is for |
|---|---|
| **Purpose** | one sentence; if a section does not serve it, it belongs elsewhere |
| **Reader question** | the thing someone types into the search box before they land here |
| **Sections** | in order, with the main points each must make |
| **Not here** | what a reader might expect and will not find, and where it lives instead |
| **Sources** | `file:lines` to move in. Blank means new prose — the Topic 8 worklist |

---

## Proportions, and the denominator that makes them mean anything

The independent pass argued safe extraction should be ~25% of a guide and
access/cost ~20%, against our 6.3% and 10.4% (`brief.md:179-197`). Those targets
were never percentages of *everything published* — its own outline excludes the
generated API reference and has no migration or platform page, because it derived
from `src/` and `tests/` and had no evidence base for either
(`independent/proposed-outline.md:1-6, 136-153`).

So there are two denominators, and only one of them is comparable:

| | Lines | safe-extraction | access-and-cost |
|---|---:|---:|---:|
| **Core teaching pages** — install, reading, gotchas, safe-extraction, access-and-cost, formats, errors, cli | ~1,175 | **23.8%** | **16.6%** |
| All published pages, incl. migrating / support-matrix / philosophy / api / acknowledgements | ~1,985 | 14.1% | 9.8% |

Against the comparable denominator the target shape lands where the independent
pass argued it should, and the remaining access/cost gap is ~3 points rather than
the ~10 the raw comparison suggested. **This is not a reason to relax**: the
safe-extraction figure assumes the ~90 lines of new prose in §4 below actually get
written. Merging alone gets it to ~200 lines / 17%, which is
[`page-shape.md`](page-shape.md) §1's own estimate.

Projected sizes, for sequencing rather than as targets:

| Page | Now | Projected | Shape |
|---|---:|---:|---|
| `index.md` | 57 | ~55 | unchanged |
| `install.md` | — | ~70 | **new**, split from `usage.md` + new matrix |
| `reading.md` | — | ~220 | **new**, split from `usage.md` + ADR 0014 |
| `gotchas.md` | 155 | ~65 | shrink to a digest |
| `safe-extraction.md` | 93 | ~280 | grow ~3× |
| `access-and-cost.md` | 154 | ~195 | rename + absorb |
| `formats.md` | 185 | ~185 | unchanged size, two fixes |
| `errors-and-diagnostics.md` | — | ~90 | **new** |
| `cli.md` | — | ~70 | **new**, split from `usage.md` |
| `migrating.md` | 174 | 174 | unchanged |
| `support-matrix.md` | 152 | 152 | unchanged |
| `philosophy.md` | 79 | 79 | unchanged |
| `how-it-works.md` | — | ~150 | **new**, all new prose (D2) |
| `api.md` | 90 | 90 | unchanged |
| `acknowledgements.md` | 98 | 98 | unchanged |

---

## 1. `index.md` — Home

**Purpose.** One screen that says what Archivey is, shows it working, and routes.

**Reader question.** "Is this the library I want, and where do I start?"

**Sections.**

1. One-paragraph what-it-is + the six-line example. Unchanged.
2. Highlights — seven bullets. Unchanged.
3. User guide — renumber for the new page set and order.
4. For contributors — the repo pointer block, already rewritten.

**Not here.** The pitch (that is `README.md`, and `philosophy.md` for the long
form). Anything a reader has to scroll for.

**Sources.** `docs/index.md:1-57`, nav list renumbered.

---

## 2. `install.md` — Install and extras **(new)**

**Purpose.** Make "I `pip install`ed it and RAR didn't work" impossible to reach.

**Reader question.** "What do I have to install for *my* format?"

This is the independent pass's #1 predicted abandonment
(`independent/proposed-outline.md:9-20`), and today install is 16 lines at the top
of a page called "Basic usage" while the format × extra × tool answer is spread
across three other pages.

**Sections.**

1. **The four extras**, with what each buys and why there is no per-format extra —
   member codecs are shared across containers, so a format name would be the wrong
   thing to install.
2. **The format × extra × external-tool matrix.** One table, the single answer to
   the reader question. Built from `formats.md`'s quick matrix, but organised by
   *what you install* rather than by format.
3. **`format_availability()` — ask the installed library.** The function exists and
   no page is built around it. Show its output, and that a format stays *known*
   when its dependency is absent: single-codec formats report `NONE`, multi-codec
   containers report `PARTIAL`, and both carry install hints. (must-explain #15 —
   currently undocumented anywhere.)
4. **RAR needs the RARLAB `unrar` binary**, and no pip extra can supply it.
   `unrar-free` / `unar` / `7z` are not substitutes. Listing works without it;
   reading bytes does not. (must-explain #14.)
5. **Free-threaded builds** — one paragraph and a link; `support-matrix.md` owns
   the detail.

**Not here.** Per-format quirks (`formats.md`). The free-threaded wheel matrix
(`support-matrix.md:60-97`). Why each library was chosen (`how-it-works.md`).

**Sources.** `docs/usage.md:3-18`; `docs/formats.md:6-27`;
`docs/acknowledgements.md:57-73`; `docs/support-matrix.md:60-80`.
**New:** §3 entirely; the matrix is a re-cut, not a move.

> **Sequencing (O-13).** `consolidate-optional-extras` shipped in #212, so the four
> extras above are current. Write against them, not against the older eleven.

---

## 3. `reading.md` — Reading archives **(new)**

**Purpose.** The contract for getting members and bytes out of an archive, from
open to close.

**Reader question.** "How do I list this, and how do I read one file out of it?"

The biggest new page, and the one carrying the most currently-undocumented
behaviour: eight of the 29 must-explain items land here with no home today.

**Sections.**

1. **Open and list.** `open_archive`, iteration, `members()`, `get()`,
   `reader.format` / `reader.cost`. Random access is the default.
2. **Sources.** Paths, file objects, directories, byte sequences. Three things a
   signature does not say: a **seekable stream is assumed to start at its current
   `tell()`** (must-explain #12); **only 7z and RAR accept multi-volume
   sequences**, a length-1 sequence is a scalar, and a directory path forces the
   directory backend even when `format=` says otherwise (must-explain #25).
3. **Detection.** `detect_format`, magic-before-extension, confidence and evidence.
   A conflict means **magic wins and `FORMAT_EXTENSION_CONFLICT` fires** — name the
   diagnostic (must-explain #22). `open_stream` vs `open_archive` on the same
   `.gz`, and the inner-TAR upgrade (must-explain #21).
4. **Read a member.** `open()` / `read()`, forward-only and one-live by default.
   `read()` is **all-or-raise and unbounded** — it has no size guard, which is a
   memory-bomb surface on untrusted input (must-explain #24). The chunked-loop
   recipe for recovering a truncated prefix.
5. **The integrity guarantee.** Content faults raise from `read`, never from
   `close`; the call × failure matrix. This contract's only copy today lives inside
   a 615-line ADR (D5).
6. **`stream_members()`.** Lifetime — the yielded stream is valid **only until the
   iterator advances** — and laziness: open, decompress and password errors surface
   on **first read**, not on yield, so "I iterated everything so passwords were
   checked" is false (must-explain #10). Both undocumented today.
7. **Streaming mode.** `streaming=True`, the single forward pass, what raises in
   that mode. Cross-link the cost consequences rather than restating them.
8. **Damaged archives.** `members()` / `scan_members()` complete-or-raise;
   `members_report()` for prefix + error; `__iter__` / `stream_members` yield then
   raise. Not salvage.
9. **Duplicate names and `is_current`.** Last-entry-wins, `SUPERSEDED` on extract,
   the filter one-liners.
10. **Passwords.** Single, list, `PasswordProvider`. Order matters. A static
    password on a format without encryption raises `UnsupportedOperationError`
    (must-explain #13); the ZipCrypto STORED trap is a `gotchas.md` line pointing
    at `access-and-cost.md`.
11. **Cheap dedupe with stored hashes.** The recipe, unchanged.
12. **Identity and lifetime.** `member in reader` is **identity, not name** — a
    string raises `TypeError` rather than falling back to iteration, which would
    consume a streaming pass (must-explain #19). A member from another reader
    raises. **Closing the reader does not invalidate already-open streams**
    (must-explain #20). **Non-file members cannot be `open()`ed** (must-explain
    #26). All four undocumented today.
13. **One-shot extract**, and why it has no `members=`. Note that `extract()`
    **auto-opens streaming for a non-seekable source** while `open_archive` refuses
    one — the inconsistency users hit first (must-explain #4, undocumented).

**Not here.** What an access pattern *costs* (`access-and-cost.md`). Extraction
policy (`safe-extraction.md`). The exception tree
(`errors-and-diagnostics.md`).

**Sources.** `docs/usage.md:20-183`;
`dev-docs/decisions/0014-integrity-verdicts-from-reads-not-close.md:320-375` (§5).
**New:** §2, §6, §12, §13's streaming note, and the named diagnostic in §3.

> **Boundary check — this page vs `access-and-cost.md`.** The independent outline
> had three pages here: opening/detection/passwords (~10%), access modes and cost
> (~20%), reading members (~8%). The target tree has two. The mapping holds: its §3
> and §5 are this page, its §4 is `access-and-cost.md`. The one genuinely split
> concept is `streaming=True` — the *contract* is §7 here, the *consequences* are
> on the cost page. Keep the contract here and link out; duplicating it is how
> `gotchas.md` became a third copy of two other pages.

---

## 4. `safe-extraction.md` — Safe extraction

**Purpose.** The page without which the library cannot be used safely on untrusted
input. Becomes the guide's largest page.

**Reader question.** "What does 'safe by default' actually block, and what does it
not?"

`VISION.md:26` makes this claim #1; `openspec/specs/safe-extraction/spec.md` is 809
lines, the largest in the tree; the page is 93 lines and its deepest sentence about
trust boundaries is a link *out of the guide*.

**Sections.**

1. **One-shot**, and the defaults spelled out.
2. **Trust boundaries.** What is trusted (the destination path you pass) versus what
   is not (every byte of the archive). Currently only written in an unpublished
   maintainer page. **This is where D3's remaining repo link gets dropped.**
3. **What is enforced.** The existing bullet list, plus the depth from the threat
   model: the **three-layer symlink defence** (lexical check, parent resolution,
   post-create re-resolution), extraction-root overwrite rejection, permission
   hygiene, and the atomic temp + `os.replace` write semantics.
4. **Policies.** `STRICT` / `STANDARD` / `TRUSTED`, and what each does *not* relax —
   `TRUSTED` still runs every universal path, symlink and special-file check
   (must-explain #17).
5. **`OnError` is about failures, not blocks.** A policy `BLOCKED` is always
   recorded and always continues, under `STOP` and `CONTINUE` alike. The single most
   misread knob on the page (must-explain #6).
6. **Names change on disk.** `STRICT` strips trailing dots and spaces and
   percent-escapes non-UTF-8 bytes; reserved names and `:` are rejected on **every**
   OS; case and NFC/NFD collisions are deliberate on every OS. `requested_path` and
   `EXTRACTION_NAME_SANITIZED` are how you see it.
7. **Overwrite.** `ERROR` / `REPLACE` / `SKIP` / `RENAME`. `REPLACE` unlinks then
   creates — it **never writes through** a pre-existing symlink.
8. **Limits and bombs.** `ExtractionLimits` vs `ListingLimits`, the actual defaults
   (2 GiB, ratio 1000 after 5 MiB, ~1M entries), that bomb trips **halt the run even
   under `CONTINUE`** (must-explain #7), and that `stream_members()` is unguarded by
   design. Also: a looser `config=` passed to `extract_all` **cannot** raise a
   listing ceiling set at open (must-explain #8, undocumented).
9. **Hardlinks and filters.** Excluding a hardlink's source orphans the link;
   seekable sources recover it in a second pass and write the content at the *link*
   path, forward-only sources cannot (must-explain #18).
10. **Nested archives.** Recursion is caller-driven and the bomb tracker is **not
    nesting-aware** — it checks expansion for individual archives. Bound depth and
    total size yourself; a worked recipe (threat-model O6).
11. **Hardening notes for callers.** Accelerators are not the defended fuzz surface;
    `unrar` is inside your trust boundary; extract to a scratch directory and promote.
    Currently in `SECURITY.md`, which GitHub renders for vulnerability reporters
    (O-7).
12. **What is out of scope.** Concurrent hostile modification of the destination
    during extraction; metadata fidelity (xattrs / ACLs / forks).

**Not here.** The extraction *report* API shape (`api.md`). Diagnostics as a
mechanism (`errors-and-diagnostics.md`).

**Sources.** `docs/safe-extraction.md:1-93`; `docs/gotchas.md:103-126` and
`91-102`; `dev-docs/threat-model.md:9-58` (§2, §3) and `:186-193` (§10);
`SECURITY.md:68-89` (§11).
**New:** the §10 bounded-recursion recipe, the "what `TRUSTED` does not relax"
framing in §4, and the §8 config-ceiling rule. ~90 lines — this is the gap the
merge cannot close, and the reason the 23.8% above is a plan rather than a fact.

---

## 5. `access-and-cost.md` — Access costs and pitfalls

**Purpose.** What each access pattern costs, and which knob to reach for.

**Reader question.** "Why is this slow, and what do I pass to make it not slow?"

**Sections.**

1. **Read `reader.cost`.** The four fields; cost describes what you *pay*, never
   what is *legal*.
2. **Access modes.** `streaming=False` vs `True`, what each refuses. `streaming` and
   `concurrent_members` are mutually exclusive (must-explain #3). Random-access open
   on a pipe fails rather than buffering.
3. **Solid archives and open order.** One forward pass; named `open()` may restart a
   block. `concurrent_members=True` makes overlapping streams *correct* and does
   **not** remove solid open-order cost (must-explain #11).
4. **Seeking inside compressed members.** Why seek is off by default; native indexes
   for XZ/lzip; rapidgzip for gzip/zlib/deflate/bzip2; the backward-seek
   re-decompress and its diagnostic.
5. **Accelerators.** `AUTO` falls back **silently**; `ON` raises
   `PackageNotInstalledError` — the loud/quiet split is undocumented today
   (must-explain #16). The 1 MiB AUTO threshold and why it exists.
6. **Concurrent member streams.** Materialize once, then fan out. Reader-wide passes
   stay single-owner.
7. **Streaming mode is one pass.** Including after an early `break`; `scan_members()`
   to drain.
8. **Passwords and confirmation cost.** The ZipCrypto STORED niche; 7z key
   derivation per candidate.
9. **Measurement.** `io_stats()` returns `None` unless the archive was opened inside
   `enable_measurement()` — opt-in *and* open-scoped, which is why counters read
   zero (must-explain #28, undocumented).
10. **Wall-time bands.** Aspirational, with the measured column. **Fix the
    `davitf/archivey-2` link** (O-4).
11. **Checklist.** The situation → knob table. Unchanged; it is the best thing on
    the page.

**Not here.** Accelerator *process* risk (`gotchas.md`, one line, linking to
`known-issues.md`). The stream contract itself (`reading.md`).

**Sources.** `docs/costs.md:1-154` (renamed); `docs/gotchas.md:13-25` and `27-37`
(the cost half, absorbed as the digest shrinks).
**New:** §5's ON-vs-AUTO split, §9.

---

## 6. `gotchas.md` — Gotchas

**Purpose.** The "read this next" digest. One line per trap plus a link to the page
that owns it. Not a third copy of anything.

**Reader question.** "What is going to bite me that I would not think to ask?"

**The inclusion rule is normative for this page (D4):** a topic belongs here only if
(a) a caller choice is likely to cause a mistake or a footgun, or (b) Archivey
cannot fulfil its intention of failing loudly and verifying. Format encyclopaedia,
unsupported-feature lists, full policy tables and "plan around this limitation" rows
belong to the owning page.

**Sections — two, and only two.**

1. **What you should / shouldn't do.** Seek/redecompress · solid open order ·
   streaming is one pass · `get()` last-wins and `extract_all(members=["x"])`
   matching every `x` · STRICT rewrites names and collides case-insensitively (one
   bullet) · don't close a source under a live accelerator stream · accelerators off
   for untrusted input under a latency budget.
2. **What you should be aware of.** 7z AES store/copy with no integrity anchor ·
   TAR residuals (trailer-less warns; streaming final header) · bare gzip/zlib +
   rapidgzip best-effort truncation · `.Z` zero-leftover silent cuts · `import
   archivey` patches pycdlib process-globally · a short "we differ from stdlib on
   corruption handling" orientation.

**Explicitly out** (D4 triage, decided): ZIP/ISO needing seek · multi-volume ZIP ·
ZIP UTF-8 bit-11 · the format-limitations table · the full policy table · listing
completeness vs `members_report` · the "what we can only warn about" meta section.

**Not here.** Everything above, each on its owning page.

**Sources.** `docs/gotchas.md`, reduced from 155 lines to ~65.
**Rewrite required:** the accelerator bullet. `_TrappingSource` contains Bug 3 — the
fault becomes a benign EOF toward rapidgzip and archivey re-raises. The page must
not say "the process dies" (D9 / O-15).

> **Spec conflict, still open.** `openspec/specs/documentation/spec.md:178-193`
> requires Gotchas to cover multi-volume ZIP, ZIP/ISO seek, UTF-8 bit-11 and TAR
> silent-shorten. D4 puts that quartet out of Gotchas. **The splits change must
> rewrite or drop that requirement** — until it does, the page and the spec
> disagree, and "`formats.md` covers it" is not a reading the spec supports.

---

## 7. `formats.md` — Formats and extras

**Purpose.** Per-format capability, quirks, and what each needs.

**Reader question.** "Why did this format do that?"

**Sections.** Quick matrix · ZIP · TAR · 7z · RAR · ISO · Directory · single-file
compressors · stored digests · detection. Structure unchanged; it is a good page.

Two fixes:

- **O-2 — still open.** `formats.md:137` says the rapidgzip truncation backstop
  covers a **path** `.gz`. `openspec/specs/seekable-decompressor-streams/spec.md:122-124`
  says **any declared-seekable source** — "a path or a caller-owned `BinaryIO` alike —
  not only path sources". `gotchas.md:87` already states it correctly, so the same
  fact is written two ways on the published site today. The prose is behind the spec,
  not in conflict with it; no decision to make. (`dev-docs/open-issues.md:133` carries
  the same stale wording, now unpublished and lower priority.)
- **ISO:** state the pycdlib process-global deque patch here, where a reader looking
  at ISO will find it, rather than only as a `gotchas.md` line (must-explain #29).

**O-14 is already fixed** — verified, not assumed. All three copies that tied
BLAKE2sp to an extra now say it needs no package: `formats.md:16`, `formats.md:105`,
`acknowledgements.md:73`. `consolidate-optional-extras` (#212) fixed the published
pages alongside the `pyproject.toml` comment, which is what O-14 asked for.

**Not here.** Cost consequences (`access-and-cost.md`). What to install
(`install.md`, which owns the matrix by extra).

**Sources.** `docs/formats.md:1-185`, plus the quartet D4 moves out of Gotchas
(`docs/gotchas.md:71-89`) folded into the per-format sections that own each row.

---

## 8. `errors-and-diagnostics.md` — Errors and diagnostics **(new)**

**Purpose.** What gets raised, what gets recorded, and how to tell them apart.

**Reader question.** "What do I catch, and where did that warning go?"

Diagnostics have a 181-line spec and, on the site, two lines at the bottom of
`safe-extraction.md` plus a bare symbol list in `api.md`.

**Sections.**

1. **Two roots, deliberately.** `ArchiveyError` for the archive and its
   environment; `ArchiveyUsageError` for bugs in your code, **outside** that tree so
   a blanket `except ArchiveyError` never swallows one. `UnsupportedOperationError`
   is the boundary case: an archive that genuinely cannot do the thing is an
   `ArchiveyError` (must-explain #1).
2. **The exception table.** Existing; unchanged.
3. **Translation.** Third-party and stdlib failures arrive as archivey types; you
   never catch a `zlib.error` or a `pycdlib` exception.
4. **Diagnostics are data, not logs.** `reader.diagnostics`, the extraction report,
   retention, and why `DiagnosticCode` is queryable rather than a log string.
5. **The codes worth knowing** — the ones a user should act on:
   `FORMAT_EXTENSION_CONFLICT`, `STREAM_REWIND_REDECOMPRESSES`,
   `ARCHIVE_EOF_MARKER_MISSING`, `DIGEST_UNVERIFIABLE`,
   `EXTRACTION_NAME_SANITIZED`, `member_name_encoding_inferred`. Not the full
   catalogue — `api.md` has that.
6. **Policy.** `IGNORE` / `COLLECT` / `RAISE`, and `DiagnosticRaisedError` as the
   always-stop path.
7. **Limits vs filters.** `ResourceLimitError` (a bomb or a cap) versus
   `FilterRejectionError` (a member refused) — different causes, different fixes.

**Not here.** The generated symbol list (`api.md`). What extraction policy blocks
(`safe-extraction.md`).

**Sources.** `docs/usage.md:185-217`; `docs/safe-extraction.md:90-93`.
**New:** §3, §4, §5, §6, §7 — roughly 55 of the page's 90 lines.

---

## 9. `cli.md` — Command line **(new)**

**Purpose.** The `archivey` command as a tool in its own right.

**Reader question.** "Can I just unzip this from the shell safely?"

`VISION.md:123` calls the CLI "a wedge and a dev tool… the safer `unzip`/`tar` that
demos the library in ten seconds". It has a 271-line spec and an archived product
review, and today it is 48 lines at the bottom of a page called "Basic usage" with
no nav entry — a reader looking for a command-line tool has no reason to open it.

**Sections.**

1. **Verbs**, with the aliases. Bare words, not dash-prefixed.
2. **Safe extract**, and the smart destination: no `-d` on a multi-entry archive
   lands in `./<stem>/` rather than splattering the working directory.
3. **CLI defaults differ from the library, on purpose** — overwrite is **rename**
   here and **error** there. Call it out as its own block: it is what breaks scripts
   ported from one to the other (must-explain #23).
4. **Filters.** Positionals include, `--exclude` subtracts, unmatched-pattern
   behaviour per verb.
5. **Exit codes**, especially `3` — completed with policy blocks and no member
   failure. The one an automation author must handle.
6. **Passwords on argv are visible to `ps`.** Say it here.
7. **Reserved:** `--salvage`, stdin `-`, `hash` / `create` / `convert`.

**Not here.** Library equivalents (`reading.md`, `safe-extraction.md`).

**Sources.** `docs/usage.md:219-266`.
**New:** §3 as its own block, §6.

---

## 10–15. The pages that do not change shape

| Page | Purpose | Change |
|---|---|---|
| `migrating.md` | zipfile / tarfile / shutil / patool / py7zr recipes | None. Re-point the two links that named `usage.md` sections now on `reading.md`. |
| `support-matrix.md` | What CI proves, and what it deliberately does not claim | None. The most honest page on the site — every claim is scoped to the job that proves it. |
| `philosophy.md` | Why Archivey exists, end-user framing | None. Moves down the nav: a reader who has not installed it does not need the manifesto before the install page. |
| `api.md` | Generated reference | None structurally. `ArchiveMember` is **mutable** for late-bound backend fields and callers should treat it as read-only and use `.replace()`; `modified` may be naive or aware, so compare via `modified_utc()` (must-explain #24) — docstring work, not page work. |
| `acknowledgements.md` | Credits: deps, oracles, design references | None. The BLAKE2sp attribution (O-14) was fixed in #212. |
| `how-it-works.md` | **New (D2).** Curated behind-the-scenes + a decisions summary | Entirely new prose. Six sections per `DECISIONS.md` D2: native-first parsing · the uniform stream layer · where the cost model comes from · backends and the registry · what is *not* ours · the decisions summary. A paragraph each, then a link out for depth. **Not** a mirror of the ADR index. |

---

## Coverage check — the 29 must-explain behaviours

`independent/must-explain.md` lists behaviours a competent user will hit that a
type signature does not reveal. Mapping each to its owning page is how this outline
proves it is complete rather than merely tidy.

| # | Behaviour | Owner | Today |
|---:|---|---|---|
| 1 | Two exception roots | errors | usage.md ✓ |
| 2 | One live forward-only stream by default | reading | usage.md ✓ |
| 3 | `streaming` + `concurrent_members` exclusive | access-and-cost | costs.md ✓ |
| 4 | `extract()` auto-streams a pipe; `open_archive` refuses one | reading | **gap** |
| 5 | `extract()` has no `members=` | reading | usage.md ✓ |
| 6 | `OnError.STOP` continues past blocks | safe-extraction | ✓ |
| 7 | Bomb limits halt under `CONTINUE` | safe-extraction | gotchas ✓ |
| 8 | `extract_all(config=)` cannot raise the open-time listing ceiling | safe-extraction | **gap** |
| 9 | Duplicate names: last wins | reading | usage.md ✓ |
| 10 | `stream_members` lifetime + laziness | reading | **gap** |
| 11 | Solid cost is orthogonal to concurrency | access-and-cost | costs.md ✓ |
| 12 | Mid-stream seekable sources start at `tell()` | reading | **gap** |
| 13 | Password shapes, order, ZipCrypto STORED | reading + gotchas | partial |
| 14 | RAR data needs RARLAB `unrar` | install + formats | ✓ |
| 15 | `PARTIAL` / `NONE`, not a vanished format | install | **gap** |
| 16 | `AUTO` falls back silently; `ON` raises | access-and-cost | partial |
| 17 | `TRUSTED` still runs the universal checks | safe-extraction | gotchas ✓ |
| 18 | Hardlink orphans and the seekable second pass | safe-extraction | thin |
| 19 | `member in reader` is identity | reading | **gap** |
| 20 | Close does not invalidate open streams | reading | support-matrix ✓ |
| 21 | `open_stream` vs `open_archive`; inner-TAR upgrade | reading + formats | partial |
| 22 | Magic wins over extension, with a diagnostic | reading + errors | partial |
| 23 | CLI defaults diverge from the library | cli | partial |
| 24 | `read()` unbounded; `ArchiveMember` mutable | reading + api | **gap** |
| 25 | Multi-volume and directory overrides | reading + formats | **gap** |
| 26 | Non-file members cannot be opened | reading | **gap** |
| 27 | `strict_archive_eof` defaults to warn | formats + gotchas | ✓ |
| 28 | Measurement is opt-in and open-scoped | access-and-cost | **gap** |
| 29 | ISO patches pycdlib process-wide | formats + gotchas | ✓ |

**Nine outright gaps, five partials.** Eight of the nine land on `reading.md` or
`install.md` — the two pages that do not exist yet, which is the outline's own
argument for splitting `usage.md` rather than polishing it.

Verify each against the code before writing: the independent pass could not see
intent and over-reports. Its own worked example is #29, which it flagged as
"surprising if documented nowhere" while the module docstring documents it
thoroughly — what was missing was only the *user-facing* surfacing
(`brief.md:207-212`; the module docstring is at `iso_reader.py:22-30`).

---

## What merging cannot supply

The splits are moves. These are the writing tasks that remain, in priority order:

| Where | What | Est. |
|---|---|---:|
| `safe-extraction.md` | Bounded-recursion recipe (O6), "what `TRUSTED` does not relax", the config-ceiling rule | ~90 |
| `errors-and-diagnostics.md` | Translation, diagnostics-as-data, the codes worth knowing, policy, limits vs filters | ~55 |
| `how-it-works.md` | All six sections (D2) | ~150 |
| `install.md` | `format_availability()` section; re-cutting the matrix by extra | ~45 |
| `reading.md` | Sources, `stream_members` lifetime, identity and lifetime, the `extract()` pipe note | ~45 |
| `access-and-cost.md` | ON-vs-AUTO, measurement | ~20 |
| `gotchas.md` | Rewrite the accelerator bullet for `_TrappingSource` | ~5 |

~410 lines of new prose. That is Topic 8's floor, before the accuracy pass it was
commissioned for.

## Open questions

1. **The `documentation` spec's Gotchas requirement** (`spec.md:178-193`) still
   requires the quartet D4 removed. The splits change must rewrite or drop it —
   flagged in §6, not resolved here.
2. **How much of `how-it-works.md` belongs in phase 3 at all.** It is the only page
   on this list that is 100% new prose, which makes it Topic 8 work sitting inside a
   splits change. Writing it last, or deferring it whole, are both defensible.
