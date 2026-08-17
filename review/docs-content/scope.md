# Scope — Topic 8 pass 0

Routing only. No guide prose. Measured against the published guide at `d4668c3`
(unchanged through `main` @ `5d08f31` for every file under `docs/`). Method:
[`../docs/outline.md`](../docs/outline.md) **D-f** — *does the reader do something
differently after reading this block?*

| Answer | Destination |
|---|---|
| Yes — changes what they write, configure, or expect | **The guide** |
| No — only how impressed they are | **`dev-docs/threat-model.md`** (or the spec / test that already proves it) |
| A lookup — field, enum member, signature | **The docstring**, surfaced through `api.md` |

D-a–D-e stand (page boundaries). This file decides **depth within** each page.
`outline.md`'s projected-sizes table is not a target set; D-f's `extracting.md`
228 → ~110–130 is the calibration that matters.

---

## Where the cuts concentrate

Three pages carry almost all of the over-proof:

1. **`extracting.md` (228 → ~110–130).** §What is enforced is a threat-model inventory
   (module paths, layer counts, ADR/PR citations). §Policies spends ~30 lines on
   `abort_on` enum members the page itself calls a narrow escape hatch, against two
   table rows for the default. Cut enforcement depth to the threat model; move enum
   member tables to docstrings; keep the contract sentence, the actionable names /
   limits / hardening blocks, and a one-line "abort_on exists" with a link.
2. **`formats.md` §Stored digests (49 → ~20).** The matrix is archivey knowledge and
   stays; the 30-line `hashlib` loop demonstrates that Python has `for`. Cut to a
   short use-case pointer (Home already has recipes; VISION's founding use case stays
   visible without a worked implementation here).
3. **`support-matrix.md` §Free-threaded (~105 lines — largest section in the guide).**
   D-f left this unruled; the test says: keep the claim scope, the install line, the
   "what is *not* claimed" list, and the summary table (a free-threaded adopter acts
   on all of them). The measured package-by-package table stays too — it changes which
   extra they install. Trim only the repeated "this is the wheel ecosystem, not us"
   arguing voice (register, pass 3), not the facts.

Secondary drains (not cuts of whole sections, but lookups leaving the guide):

- `abort_on` / `ExtractionStatus` / `OverwritePolicy` member glossaries → docstrings
  (`AbortOn` is already richer than the brief's "13 lines" calibration assumed;
  `ExtractionStatus` is still **one line** against a guide that names six statuses).
- `access-and-cost.md`'s planned **config-at-a-glance** field table → docstring /
  `ArchiveyConfig` reference, not a second prose essay (D-c placement stands if a
  thin "see the config fields" pointer stays on that page).

Pages that are already near the right depth: `index`, `gotchas`, `cli`, `philosophy`,
`api`, `acknowledgements`. Pages whose §B "write more" rows largely **dissolve**
because `#224`/`#225`/`#232`/`#235` already landed the prose: `opening-and-listing`,
`reading-members`, most of `errors-and-diagnostics`.

---

## Per-page scope

Nav order follows `mkdocs.yml` / Home's user-guide list, then the missing sixteenth
page. Every current heading-level block is named; sub-bullets are collapsed when they
share one ruling.

### 1. `index.md` — Home (93 lines)

**Job.** One screen that says what Archivey is, shows it working in thirty seconds,
and routes to the rest of the guide.

**Not here.** The long-form pitch (`philosophy.md`, `README.md`). Per-format quirks
(`formats.md`). Safety mechanism depth (`extracting.md`). Cost mechanics
(`access-and-cost.md`). Contributor procedure (repo `CONTRIBUTING.md` / `dev-docs/`,
already only a pointer block). Any fifth recipe.

**Blocks.**

| Block | Ruling | Destination |
|---|---|---|
| Opening what-it-is + six-line list example | Guide | Stays — the "is this the library?" test |
| §Thirty seconds (four recipes) | Guide | Stays — D-a rests on these; each changes what the reader pastes first |
| Recipe → page links | Guide | Stays |
| §Highlights (seven bullets) | Guide | Stays as routing claims; accuracy pass verifies each |
| §User guide (numbered nav) | Guide | Stays — must gain `how-it-works.md` when that page ships (D2) |
| §For contributors (repo pointers) | Guide | Stays thin; not a second site |

---

### 2. `install.md` — Install and extras (34 lines)

**Job.** Make "I `pip install`ed it and RAR / ISO / an extended codec didn't work"
impossible: what to install for *this* format, including the binary no extra can
supply.

**Not here.** Per-format behavioural quirks (`formats.md`). Free-threaded wheel matrix
detail (`support-matrix.md`). Why each library was chosen (`how-it-works.md`). Cost of
using an accelerator (`access-and-cost.md`).

**Blocks.**

| Block | Ruling | Destination |
|---|---|---|
| Opening + four `pip install` lines | Guide | Stays |
| "Four extras, no per-format ones" | Guide | Stays — changes what they type |
| RAR needs system `unrar` (short) | Guide | Stays |
| §What each format needs (pointer only) | Guide, but incomplete | **Gap:** the install-by-extra matrix and `format_availability()` still belong here (see §B) |
| §Free-threaded builds (one para + link) | Guide | Stays — detail stays on `support-matrix.md` |

---

### 3. `opening-and-listing.md` — Opening and listing (203 lines)

**Job.** Point Archivey at a source, get past detection / passwords / multi-volume,
and answer "what's inside?"

**Not here.** Reading bytes (`reading-members.md`). Access-pattern cost
(`access-and-cost.md`). Extraction policy (`extracting.md`). The damage *contract*
matrix (`errors-and-diagnostics.md` — D-e); this page keeps the one-line honesty
promise + link. Per-format listing quirks (`formats.md`). Stored-digest dedupe
(`formats.md`).

**Blocks.**

| Block | Ruling | Destination |
|---|---|---|
| §Open and list + example | Guide | Stays |
| Default random-access note + streaming pointer | Guide | Stays |
| §`open_archive` or `open_stream`? | Guide | Stays — changes which entry point they call |
| §What you can open (source table) | Guide | Stays |
| Directory + conflicting `format=` rejected | Guide | Stays |
| Seekable stream starts at `tell()` | Guide | Stays (must-explain #12) |
| Non-seekable / which formats need seek | Guide | Stays |
| `format_availability().required_source` recipe | Guide | Stays |
| §Multi-volume archives | Guide | Stays |
| §Detection + content-wins + `FORMAT_EXTENSION_CONFLICT` | Guide | Stays |
| Inner-TAR / missing-package wrinkle | Guide | Stays |
| §Passwords (order, unused diagnostic, forms) | Guide | Stays |
| §Damaged archives (one-line + link) | Guide | Stays — D-e depth is elsewhere |
| §Duplicate names and `is_current` | Guide | Stays |
| Selector-by-name vs identity recipes | Guide | Stays |

---

### 4. `reading-members.md` — Reading members (184 lines)

**Job.** The contract for getting bytes out of a member, and what each outcome means
for the caller.

**Not here.** Enumeration / sources / passwords (`opening-and-listing.md`). Cost of
solid / seek / concurrency (`access-and-cost.md`). Extraction policy and limits detail
(`extracting.md`). Full damage call×failure matrix (`errors-and-diagnostics.md` —
D-e); this page keeps the footgun asymmetry + chunked-loop recipe + link. Exception
tree (`errors-and-diagnostics.md`).

**Blocks.**

| Block | Ruling | Destination |
|---|---|---|
| §Read a member + unbounded-`read()` warning | Guide | Stays |
| Forward-only / one-live defaults + flags | Guide | Stays |
| §Two ways to read (`open` vs `stream_members`) | Guide | Stays |
| `access_cost` steers solid vs direct | Guide | Stays (contract here; cost page owns consequences) |
| Stream lifetime until advance | Guide | Stays |
| Non-file → `None`; laziness / password | Guide | Stays |
| Header-encryption exception | Guide | Stays (expectation change) |
| Links: `open` follows, `stream_members` yields `None` | Guide | Stays |
| Pass owns the reader | Guide | Stays |
| §What a read gives you back (asymmetry + loop) | Guide | Stays — D-e footgun exception |
| Full integrity matrix pointer | Guide | Stays as link only |
| §Which members you can open | Guide | Stays |
| Identity `in` / cross-reader / close closes streams | Guide | Stays |
| §Streaming mode (pipes) | Guide | Stays |
| §One-shot extract (no `members=`, auto-stream) | Guide | Stays — three lines + link; code lives on `extracting.md` |

---

### 5. `gotchas.md` — Gotchas (107 lines)

**Job.** Footgun digest: one line per trap plus a link to the owning page (D4).

**Not here.** Format encyclopaedia, full policy tables, unsupported-feature lists,
"plan around this limitation" essays — each on its owning page. Threat-model residual
*analysis* (threat model); this page carries the D8 one-liners only.

**Blocks.**

| Block | Ruling | Destination |
|---|---|---|
| Inclusion-rule preamble | Guide | Stays — normative for the page |
| §What you should and shouldn't do (9 bullets) | Guide | Stays — each is a caller choice |
| Nested-archives / unguarded-`read` / accelerator bullets | Guide | Stays (D8 one-liners) |
| §What you should be aware of | Guide | Stays when it is an honesty residual the reader must decide about |
| Empty-listing essay (~15 lines) | Guide, shorten in pass 3 | Actionable ("check the count"), but arguing-with-a-reviewer voice → O-17 later |
| "Prefer diagnostics over logs" | Guide | Stays one line |

---

### 6. `extracting.md` — Extracting (228 lines) — primary cut

**Job.** What "safe by default" blocks and does not, which knobs change that, and what
lands on disk under the defaults.

**Not here.** Extraction *report* field reference (`api.md` / docstrings). Diagnostics
as a mechanism (`errors-and-diagnostics.md`). Symlink-defence layer counts, ADR/PR
citations, module paths (`dev-docs/threat-model.md` / specs / tests). Full
`AbortOn` / `ExtractionStatus` member glossaries (docstrings). Competitive comparison
with `tarfile` symlink-copy behaviour beyond one expectation line (keep one line if it
changes what they expect; depth is not this page's job).

**Blocks.**

| Block | Ruling | Destination |
|---|---|---|
| Opening "opt out, not in" | Guide | Stays |
| §One-shot | Guide | Stays |
| §Trust boundaries (archive / dest / process / tools) | Guide | Stays compact — changes what they treat as trusted |
| "O_NOFOLLOW future direction" aside | Threat model / IDEAS | Out of the guide |
| §What is enforced — path traversal / absolute / UNC / null (as **one clause**) | Guide | Stays per D-f worked ruling |
| Extraction-root overwrite (`.` / `""`) | Guide | Stays — changes what they expect on error |
| "Symlink escapes, three layers" + chained-attack prose | **Threat model** | Cut from guide (D-f worked ruling) |
| Hardlinks "resolved positionally" mechanism | **Threat model** | Cut; keep "hardlink + filter can orphan" under Names/Limits if actionable |
| Never write through symlink; atomic temp + `os.replace` | Guide | Stays — changes overwrite expectations |
| Special files / NTFS junctions rejected | Guide | Stays one clause |
| Bidi overrides vs directional marks | Guide, shorter | Stays (D-f worked ruling — Arabic/Hebrew filenames) |
| `TRUSTED` lifts bidi only — faithful-bytes rationale | Guide | Stays short — changes when they pick `TRUSTED` |
| Decompression bombs / CONTINUE still halts | Guide | Stays |
| Permission hygiene (setuid / ownership) | Guide | Stays one clause |
| Cross-platform name safety + **ADR 0013 / PRs #109/#123** | Guide facts; **citations → out** | Collision/rewrite facts stay under Names; ADR/PR impress-only |
| Error-honesty / translation bullet | Errors page | Pointer only; narrative is `errors-and-diagnostics.md` |
| Accelerator lifecycle `weakref.finalize` | Threat model / known-issues | Actionable "OFF under latency budget" stays in Hardening |
| Leftover `.archivey-tmp-*` | Guide | Stays (D-f worked ruling) |
| §Policies — defaults code sample | Guide | Stays |
| `OnError` = failures, not blocks | Guide | Stays (must-explain #6) |
| `abort_on=` exists + fail-closed example | Guide | Stays — name it and link |
| `abort_on` member table (`BLOCKED_MEMBER` / `NAME_COLLISION` / `NAME_SANITIZED`) | **Docstrings** | Guide keeps "three events; see `AbortOn`" |
| Abort immediacy / no report returned | Guide one sentence **or** docstring | Prefer docstring + one guide clause |
| `NAME_SANITIZED` "narrow escape hatch" essay (~6 lines) | **Docstring**; guide one sentence | D-f worked ruling |
| Policy table (`STRICT` / `TRUSTED` only) | Guide | Stays; accuracy pass must notice `STANDARD` absence |
| Selective `extract_all(members=…)` | Guide | Stays |
| §Names change on disk (table) | Guide | Stays — every row changes what they check on the result |
| §Limits (defaults, `stream_members` unguarded, nesting one-para) | Guide | Stays; nesting stays a **short** actionable pointer (not a threat-model recipe dump) |
| §Hardening notes (accelerators / `unrar` / scratch dir) | Guide | Stays |
| §Diagnostics pointer | Guide | Stays one line |

**Target after cuts:** ~110–130 (D-f). Net work is mostly **removal and relocation**,
not new prose (see §B).

---

### 7. `access-and-cost.md` — Access costs and pitfalls (188 lines)

**Job.** What each access pattern costs, and which knob to reach for so the common
path stays cheap.

**Not here.** Stream *contract* (lifetime, integrity) — `reading-members.md`.
Accelerator *process*-risk one-liner lives on `gotchas.md` with a link; upstream
analysis stays in `known-issues.md`. Extraction policy / bomb defaults —
`extracting.md`. Exhaustive `ArchiveyConfig` field glossary — **docstrings** /
`api.md` (see config-screen ruling below).

**Blocks.**

| Block | Ruling | Destination |
|---|---|---|
| §Wall-time bands + harness command + measured table | Guide | Stays — aspirational bands change whether they worry; fix stale `archivey-2` link in accuracy pass (O-4) |
| L5 / IDEAS follow-up paragraph | Thin or out | Does not change what they write today; register/IDEAS, not guide depth |
| §Read `reader.cost` (four fields) | Guide | Stays |
| `StreamCapability` ordered / cost kinds unordered | Guide | Stays — changes how they compare |
| §RAR listing cost | Guide | Stays — changes open-time expectations for large RAR |
| §Solid archives (do / don't samples) | Guide | Stays |
| Concurrency ≠ free on solid | Guide | Stays |
| §Seeking inside compressed members | Guide | Stays |
| Rewind diagnostic threshold (~1 MiB) rationale | Guide short | Keep "fires when rewind is expensive"; codec-index impress detail can shrink |
| AUTO 1 MiB threshold + ON / OFF | Guide | Stays; **gap:** state clearly that `ON` raises when missing and `AUTO` falls back silently |
| §Concurrent member streams | Guide | Stays |
| §Non-seekable sources | Guide | Stays |
| §Streaming mode is one pass | Guide | Stays |
| §Passwords and confirmation cost | Guide | Stays |
| §Accelerators and source lifetime | Guide | Stays (actionable: don't close under live stream; fault is contained) |
| Residual path-source `terminate` | known-issues (GitHub link OK) | Keep one line + link, not the analysis |
| §Checklist (situation → prefer) | Guide | Stays — best thing on the page |
| **Config-at-a-glance (not present)** | See §B | Field table → **docstrings**; optional thin "knobs live on `ArchiveyConfig`" pointer may stay here (D-c) |
| **Measurement (not present)** | See §B | Guide — `enable_measurement` / `IoStats` change whether counters read zero |

---

### 8. `formats.md` — Formats and extras (228 lines)

**Job.** Per-format capability, install needs, and the quirks that change what a
caller writes or expects for that format.

**Not here.** Install-by-extra matrix as the primary answer (`install.md` owns the
re-cut; this page keeps per-format rows). Cost consequences (`access-and-cost.md`).
Why libraries were chosen (`how-it-works.md` / library-analysis on GitHub). Full
codec scoring essays (library-analysis).

**Blocks.**

| Block | Ruling | Destination |
|---|---|---|
| §Quick matrix | Guide | Stays |
| RAR/`unrar` callout + recommended install lines | Guide | Stays |
| Link to library-analysis | GitHub depth OK (D3) | Not guide prose |
| §ZIP (codecs, multi-volume reject, encoding, AES) | Guide | Stays — each row changes install or `encoding=` / expectations |
| Wrongly-set UTF-8 flag unlistable | Guide | Stays — changes what they expect on failure |
| §TAR (solid compressed, hardlinks, EOF / `strict_archive_eof`, streaming caveat) | Guide | Stays — flag and caveat change config / trust |
| Stdlib silent-shorten *mechanism* depth | Guide, shorten | Keep "we raise / warn where tarfile stops"; trailer-byte zero rule can stay one clause |
| §7z (native, BCJ2, solid, AES garbage, header encrypt, NumCyclesPower) | Guide | Stays where it changes trust of payload or error type |
| §RAR (native meta, `unrar` data, versions, solid) | Guide | Stays |
| §ISO | Guide | Stays; pycdlib process-global patch should be **stated here** (must-explain #29) as well as gotchas — actionable process expectation |
| §Directory | Guide | Stays |
| §Single-file compressors | Guide | Stays — rapidgzip best-effort and `.Z` silence change `use_rapidgzip` / trust |
| §Stored digests — matrix | Guide | Stays |
| §Cheap dedupe — 30-line `hashlib` loop | **Cut to ~8** (D-f) | Keep use case visible (Home recipe / one short snippet); loop is not archivey knowledge |
| Provenance `stored` vs `computed` sentence | Guide | Stays |
| §Detection | Guide | Stays short; `FormatInfo` fields → docstrings |

---

### 9. `errors-and-diagnostics.md` — Errors and diagnostics (201 lines)

**Job.** What gets raised, what gets recorded, how to tell them apart, and — when the
archive is damaged — what you can still get (D-e's home).

**Not here.** Extraction policy blocks and `abort_on` teaching (`extracting.md`). Full
`DiagnosticCode` catalogue as prose (`api.md` / docstrings — page already says "a
handful… worth knowing"). Generated symbol list (`api.md`).

**Blocks.**

| Block | Ruling | Destination |
|---|---|---|
| §The exception tree + blanket-`except` example | Guide | Stays |
| Exception table | Guide | Stays |
| `ArchiveyUsageError` outside the tree | Guide | Stays |
| §Diagnostics preamble | Guide | Stays |
| §Things said with a diagnostic (code table) | Guide | Stays — each code changes whether they escalate or ignore |
| §What is *not* here: per-member extraction outcomes | Guide | Stays — changes where they look (`results` vs diagnostics) |
| §Named policy presets (`strict` / `pedantic`) | Guide | Stays |
| "New codes may appear in minor releases" | Guide | Stays — changes whether they use `default=RAISE` |
| §When an archive is damaged (whole) | **Guide — keep** | D-f worked ruling; D-e depth split is the point |
| §Listing a damaged archive + `members_report` | Guide | Stays |
| §The integrity guarantee + bullets | Guide | Stays |
| Chunked-loop recipe | Guide | Stays |
| `VerificationMode.STRICT` one-liner | Guide | Stays |
| §What each call does (matrix) | Guide | Stays |
| **Error-translation narrative (absent)** | See §B | Guide — changes what they `except` |
| **Limits vs filters distinction (thin)** | See §B | Guide one clause if not already obvious from the table |

---

### 10. `cli.md` — Command line (48 lines)

**Job.** The `archivey` command as a safer shell tool: verbs, safe-extract defaults,
filters, exit codes.

**Not here.** Library API equivalents (flow pages). Full policy semantics
(`extracting.md`). Terminal-escaping implementation detail (threat model O9 is
*closed*; if the guide states the caller-visible contract, one sentence here or on
errors — accuracy pass, not a new section of mechanism).

**Blocks.**

| Block | Ruling | Destination |
|---|---|---|
| Opening + verb demo | Guide | Stays |
| §Safer extract demo | Guide | Stays |
| §Notes (bare verbs, exit codes, reserved) | Guide | Stays |
| CLI overwrite default ≠ library (rename vs error) | Guide | Ensure it is unmistakable (must-explain #23) — already in demo comments; may deserve its own one-line note |
| Passwords on argv visible to `ps` | Guide if added | Still a §B/outline remainder; actionable |

---

### 11. `migrating.md` — Migrating (174 lines)

**Job.** Replace `zipfile` / `tarfile` / `shutil` / `patool` / `py7zr`/`rarfile` with
Archivey: before/after plus the behaviour deltas that bite.

**Not here.** First-principles teaching of safety or cost (link out). Format encyclopaedia
(`formats.md`). Philosophy manifesto (`philosophy.md`).

**Blocks.**

| Block | Ruling | Destination |
|---|---|---|
| Opening theme | Guide | Stays |
| §Cheat sheet | Guide | Stays |
| §From `zipfile` / `tarfile` / `shutil` / `patool` / `py7zr` | Guide | Stays — each "what changes" bullet is a migration action |
| §Things that will bite you | Guide | Stays |

---

### 12. `support-matrix.md` — Platforms and threading (152 lines)

**Job.** What CI proves, and what the project deliberately does not claim — especially
under free-threaded CPython.

**Not here.** How to use `concurrent_members` as a cost story (`access-and-cost.md`).
General install extras (`install.md`). Philosophy of fail-loud (`philosophy.md`).

**Blocks.**

| Block | Ruling | Destination |
|---|---|---|
| §Python and operating systems (CI table) | Guide | Stays |
| Non-CPython interpreters | Guide | Stays one short block |
| §Free-threaded — what is claimed + sample | Guide | Stays |
| Which extras are free-threaded (measured table) | Guide | Stays — changes the install line |
| `[recommended]` fails on 3.13t consequences | Guide | Stays |
| §What is *not* claimed | Guide | Stays — the honesty load-bearer |
| §Default is fail-fast | Guide | Stays (overlaps access page; keep the free-threaded framing) |
| §One live-stream caveat | Guide | Stays |
| §Thread-safety summary table | Guide | Stays |
| Repeated "wheel ecosystem not us" voice | Register (pass 3) | Facts stay; arguing voice shrinks |

D-f flag from outline ("not ruled"): **keep the section**; it is long because the
claim is narrow and the adopter acts on the narrowness. Do not treat length alone as
a cut signal.

---

### 13. `philosophy.md` — Philosophy (79 lines)

**Job.** Why Archivey exists and which defaults follow — end-user framing of VISION.

**Not here.** How-to recipes (flow pages). Behind-the-scenes architecture
(`how-it-works.md`). Maintainer adoption strategy (root `VISION.md`).

**Blocks.**

| Block | Ruling | Destination |
|---|---|---|
| One sentence | Guide | Stays |
| §Simple API | Guide | Stays |
| §Safe by design | Guide | Stays + link |
| §Don't-shoot-yourself + defaults list | Guide | Stays |
| §Escape hatches table | Guide | Stays |
| §Content-first | Guide | Stays |
| §Honest about damage and cost | Guide | Stays |
| §What this is not | Guide | Stays |

---

### 14. `api.md` — API reference (91 lines)

**Job.** Generated reference surface for public names, grouped for scanning.

**Not here.** Narrative teaching (guide pages). Completeness policy for the 31
`__all__` names without entries is a **Topic 8 §D question**, not a silent expansion
in pass 0.

**Blocks.**

| Block | Ruling | Destination |
|---|---|---|
| Opening completeness-ish sentence | Guide / fix later | Accuracy/§D — sentence must not overclaim |
| Opening / reader / data model / diagnostics / extraction / config / cost / measurement / errors sections | Guide (generated) | Stays; docstring enrichment from D-f drains lands *under* these directives |
| Exception-tree subtypes absent from `::: ` list | §D decision | Not routed as guide prose |

---

### 15. `acknowledgements.md` — Acknowledgements (98 lines)

**Job.** Credit deps, oracles, adapted source, and design references properly.

**Not here.** Install instructions (`install.md`). Codec *choice* rationale beyond
credit (`how-it-works.md` / library-analysis). Runtime behaviour teaching.

**Blocks.**

| Block | Ruling | Destination |
|---|---|---|
| Thanks | Guide | Stays |
| §Adapted source | Guide | Stays |
| §Format references / oracles | Guide | Stays |
| §Seekable-stream design references | Guide | Stays as credit; scoring stays on GitHub library-analysis |
| §Runtime dependencies | Guide | Stays — overlaps install but credit-shaped |
| §Development and test dependencies | Guide | Stays |

---

### 16. `how-it-works.md` — does not exist yet (D2)

**Job.** Curated behind-the-scenes for a curious user: six short architecture
sections plus a decisions summary — a paragraph each, then a link out for depth.
**Not** a mirror of the ADR index, and **not** the threat model.

**Not here.** Raw ADR corpus (`dev-docs/decisions/`). Threat-model gap register.
Capability-spec prose dumps. Install matrices (`install.md`). How-to recipes.

**Planned blocks (D2) — routed before writing.**

| Planned section | Ruling | Destination |
|---|---|---|
| Native-first parsing (why 7z/RAR headers in pure Python) | Guide, one paragraph | Depth → ADR 0001/0002 / VISION |
| Uniform stream layer | Guide, one paragraph | Depth → `compressed-streams` / library-analysis |
| Where the cost model comes from | Guide, one paragraph | Depth → ADR 0003 / access-mode spec |
| Backends and the registry | Guide, one paragraph | Depth → `backend-registry` spec |
| What is *not* ours (zipfile/tarfile/unrar/pycdlib) | Guide, one paragraph | Changes trust expectations; depth → ADRs |
| Decisions summary (one short entry per load-bearing ADR outcome) | Guide, bullets | Not a second ADR index; link out |
| Anything that only impresses (layer counts, fuzz surface essays) | Threat model / ADRs | Never inlined as proof |

**Needs** a `documentation` spec delta when the file is added (brief §B). Depth cap
under D-f: nearer **~90–120** than the outline's ~150 — paragraph-each is the
rule, not a floor to fill.

---

## §B worklist — re-derived under D-f

`outline.md` §"What merging cannot supply" estimated ~455 outstanding lines. That
figure is stale in both directions: **+226/−27** guide lines landed in
`#225`/`#232`/`#235` after it was written, and **D-f** converts several remaining
rows from prose tasks into docstring or threat-model tasks (or into cuts).

### Dissolved as guide-prose growth tasks

| Original row | Why dissolved |
|---|---|
| `opening-and-listing.md` ~25 (sources, named detection diagnostic, errors callout) | **Shipped** on the page: source table + `tell()` rule, `FORMAT_EXTENSION_CONFLICT`, damaged one-liner + link. Confirm in accuracy pass; do not re-plan as writing. |
| `reading-members.md` ~35 (`stream_members` lifetime, identity/lifetime, `extract()` pipe note) | **Shipped**: lifetime/laziness, identity `in`, close-closes-streams (aligned with `#225` close-on-reader-close), no-`members=` + auto-stream note. Integrity *matrix* correctly lives on errors (D-e), not here. |
| `extracting.md` — "what `TRUSTED` does not relax" | **Shipped** (bidi lift + "still won't traverse" table row + hardening). |
| `errors-and-diagnostics.md` — diagnostics-as-data, codes worth knowing, named policy presets | **Mostly shipped** in `#235` (+61 lines): diagnostics channel rules, code table, `strict()`/`pedantic()`, per-member-outcomes-not-here. |
| `access-and-cost.md` — AUTO threshold half of ON-vs-AUTO | **Shipped** (1 MiB `RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE` prose). Loud/quiet split still thin — see surviving row. |
| `extracting.md` ~90 as a **growth** estimate for enforcement depth | **Inverted by D-f.** The outline asked to *add* three-layer symlink defence and related threat-model inventory; D-f routes that inventory **out**. The page needs a **cut**, not ~90 new lines. |

### Converted: docstring tasks (not guide prose)

| Item | Why |
|---|---|
| `AbortOn` member table + `NAME_SANITIZED` essay on `extracting.md` | Lookup / escape-hatch reference. Guide names that `abort_on` exists and links. Enrich `AbortOn` / related exception docstrings. |
| `ExtractionStatus` value glosses appearing as guide teaching | Still a **1-line** class docstring; statuses named across extracting / opening / migrating should be defined beside the enum. |
| `access-and-cost.md` config-at-a-glance **field table** (finding 4 / outline §10) | Field → default → one-line "when to change" is a **lookup**. Put it on `ArchiveyConfig` / `ExtractionLimits` / `ListingLimits` docstrings (and `api.md`). D-c still allows a thin pointer section on this page; it does not require a second prose screen. |
| `FormatInfo` / detection evidence field tour on `formats.md` §Detection | Already points at the spec; fields are docstring material. |

### Converted: threat-model (or spec/test) tasks — not guide prose

| Item | Why |
|---|---|
| Symlink three-layer defence, positional hardlink resolution, ADR 0013 / PR citations inside §What is enforced | D-f worked rulings: nobody acts on layer counts or PR numbers. |
| Accelerator `weakref.finalize` lifecycle as enforcement bullet | Evidence; actionable guidance already in Hardening / gotchas. |
| Nested-archive **amplification analysis** (threat-model O6) beyond the short "bound depth/size yourself" pointer | Analysis stays in the threat model; guide + gotchas keep the actionable one-liner / short Limits para. A long "worked recipe" that re-proves amplification is not required. |

### Surviving guide tasks (write or cut)

| Page | Task | Kind | Notes |
|---|---|---|---|
| `how-it-works.md` | All six D2 sections + decisions summary | **Write** (new page) | ~90–120 under D-f paragraph-each rule; needs `documentation` spec delta + nav |
| `install.md` | `format_availability()` section (PARTIAL/NONE, install hints) | **Write** | must-explain #15; actionable |
| `install.md` | Re-cut format × extra × tool matrix by *what you install* | **Write** | Short table; detail rows can still link into `formats.md` |
| `extracting.md` | Cut §What is enforced to contract clauses; drain enum tables to docstrings | **Cut / relocate** | Primary size win; target ~110–130 |
| `extracting.md` | Config-ceiling rule: `extract_all(config=)` cannot raise open-time listing ceiling | **Write** (~few lines) | must-explain #8; still absent |
| `extracting.md` | Nested archives | **Keep short** | Do not expand into threat-model recipe; gotchas already digests |
| `errors-and-diagnostics.md` | Error-translation narrative (raw lib/`OSError` → archivey types; unrecognized propagate; `ArchiveyUsageError` outside) | **Write** | User-facing promise from `CONTRIBUTING.md`; still absent |
| `errors-and-diagnostics.md` | Limits vs filters (`ResourceLimitError` vs `FilterRejectionError`) | **Write** if one clause | May be only a sharpening of the existing table |
| `access-and-cost.md` | State `ON` raises / `AUTO` silent fallback explicitly | **Write** (one clause) | Completes ON-vs-AUTO |
| `access-and-cost.md` | Measurement: `enable_measurement` / `IoStats` opt-in + open-scoped | **Write** | must-explain #28; public and unexplained on this page |
| `access-and-cost.md` | Optional thin config pointer (not the field table) | **Write or skip** | Field table → docstrings; D-c satisfied by pointer + api.md |
| `formats.md` | Cut cheap-dedupe loop ~30 → ~8 | **Cut** | D-f worked ruling |
| `formats.md` | State pycdlib process-global patch on ISO section | **Write** one clause | must-explain #29; gotchas already has it |
| `cli.md` | Password-on-argv / `ps` visibility; CLI-vs-library overwrite default as its own note | **Write** short | Outline remainders; demo comments may already imply overwrite |
| `index.md` | Add `how-it-works.md` to user-guide list when it ships | **Edit** | Mechanical with the new page |

### Net

The live **guide-prose** floor is no longer ~455 lines of addition. Roughly:

- **New page:** `how-it-works.md` ~90–120
- **Small writes:** install (~30–40), errors translation (~10–15), access measurement + ON clause (~15–25), extracting config-ceiling (~5), formats ISO clause + dedupe cut, cli notes (~5–10)
- **Large cut:** `extracting.md` enforcement/policies drain (order of **~80–100 lines out**), formats dedupe cut (~20 net)

Accuracy (pass 1), register (pass 3), and the §D api.md completeness question remain
out of this file's job — they are sequenced after the maintainer steers on this
scope.

---

## Checkpoint (pass 0 → maintainer)

Hand this file back before any claim inventory. A wrong scope call is cheap now and
expensive after prose. In particular, confirm:

1. **`extracting.md` cut plan** (threat-model drain + docstring drain) matches intent.
2. **Config-at-a-glance → docstrings** (with optional thin pointer on
   `access-and-cost.md`) is an acceptable reading of D-c + D-f together.
3. **`support-matrix.md` free-threaded section stays** at roughly current factual
   density.
4. **`how-it-works.md` depth cap ~90–120** rather than outline's ~150.

Then start pass 1's claim inventory against the blocks this file marks **Guide**.
