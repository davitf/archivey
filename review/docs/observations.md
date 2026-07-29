# Observations — content problems noticed while auditing

**Recorded, not acted on.** Rewriting page content is out of scope for this review
(brief, *Out of scope*); it is Topic 8 (`review/backlog.md:162`). The audit reads
every file anyway, so recording these is free. Topic 8 should start here rather
than from zero.

Cited against `4f154b9` (`main` @ `ce674bf` plus this review's prompt commits).
Where a claim depends on something not verified, it says so.

---

## O-1 — `AGENTS.md` makes two statements that are false today

`AGENTS.md:11-16`:

> there is no server, web UI, or runnable CLI (the `archivey` command in
> `openspec/specs/cli/spec.md` is planned, not implemented) … Implemented backends
> are ZIP, TAR, ISO, directory, and single-file-compressed …; **7z and RAR readers
> are not implemented yet** despite their specs/extras existing.

Both are wrong:

- The CLI ships. `pyproject.toml:49-50` declares
  `archivey = "archivey.cli.main:main"`; `src/archivey/cli/` exists; it landed in
  #120 and has an archived product review (`review/archive/2026-07-20-cli-product/`).
- The native readers ship. `src/archivey/internal/backends/sevenzip_reader.py` and
  `rar_reader.py` exist; `CONTRIBUTING.md:96` describes the core as including
  "native 7z read + RAR metadata".

**Severity: high for an agent guide** — an agent that believes this will not run
the CLI, will not test 7z/RAR paths, and may re-propose work that is done. This is
also the strongest argument for the `AGENTS`/`CLAUDE` consolidation (Q5): the file
that is *not* the canonical one is the one that rotted.

---

## O-2 — The rapidgzip gzip-truncation caveat exists four times; two copies are stale against the spec

The authoritative text, `openspec/specs/seekable-decompressor-streams/spec.md:125-126`:

> … for **any declared-seekable source** — a path or a caller-owned `BinaryIO`
> alike — **not only path sources**.

| Copy | Says | Correct? |
|---|---|---|
| `docs/gotchas.md:87` | "Archivey backstops **any seekable source** — a path or a caller-owned `BinaryIO` alike" | ✅ |
| `docs/internal/known-issues.md:158-162` | "on **any seekable source** (path or caller-owned `BinaryIO`)" | ✅ |
| `docs/formats.md:132` | "With the `[seekable]` rapidgzip accelerator on a **path** `.gz`…" | ❌ narrower than the spec |
| `docs/internal/open-issues.md:132-133` | "(empty→stdlib + single-member ISIZE on **path sources**)" | ❌ narrower than the spec |

This is the concrete case that the duplication is not theoretical: the same fact,
written four times, has already drifted in two of them. Both stale copies
under-promise (they describe an older, narrower backstop), so no user is misled
into unsafety — but a user reading `formats.md` will needlessly set
`use_rapidgzip=OFF` for a `BinaryIO` source that is in fact covered.

**Not a pause-and-ask case.** The spec is unambiguous and the prose is simply
behind it; there is no decision to make. Topic 8 fixes the two copies; this
review's §3 of [`page-shape.md`](page-shape.md) removes the reason a fifth copy
would ever be written.

---

## O-3 — `rapidgzip-upstream-report.md` points at a path that moved to the archive

`docs/internal/rapidgzip-upstream-report.md:11`:

> `openspec/changes/rapidgzip-truncation-investigation/UPSTREAM_TRUNCATION_REPORT.md`

That change was archived; the file is now at
`openspec/changes/archive/2026-07-24-rapidgzip-truncation-investigation/UPSTREAM_TRUNCATION_REPORT.md`
(verified — the file exists there and not at the cited path). It is written as
inline code, not a Markdown link, so `mkdocs build --strict` does not catch it.
This is exactly the class the phase-4 link checker exists for.

---

## O-4 — A published user page links to the pre-rename repository

`docs/costs.md:17` links the nightly benchmark run at
`https://github.com/davitf/archivey-2/actions/runs/29992136861`. The repo was
renamed to `davitf/archivey` (`CHANGELOG.md:42`;
`docs/internal/release-repo-cutover.md:7` records the rename as done 2026-07-25).
GitHub redirects renamed repositories, so the link most likely still resolves — it
is the wrong name on a user-facing page either way, and
`release-repo-cutover.md:62` explicitly listed "fix references" as a cutover step
that this one escaped.

**Not verified:** whether the redirect actually resolves (no outbound check made).

---

## O-5 — Six pages are built and reachable but absent from the nav

Confirmed by running the build. `uv run --group docs mkdocs build --strict` at
`ce674bf` is **green** and prints:

```
INFO - The following pages exist in the docs directory, but are not included in the "nav" configuration:
  - decisions/0014-integrity-verdicts-from-reads-not-close.md
  - internal/ppmd-exit-after-green-exploration.md
  - internal/ppmd-native-investigation-brief.md
  - internal/ppmd-native-investigation-results.md
  - internal/pyppmd-upstream-report.md
  - internal/rapidgzip-upstream-report.md
```

`--strict` does not fail on this. 1,846 lines are published at a URL, indexed by
the site search, and unreachable by navigation. Phase-4 guardrail #1 is a
non-empty check on this exact line.

---

## O-6 — ADR 0014 is marked `Status: accepted` but has an `## Open questions` section

`docs/decisions/0014-integrity-verdicts-from-reads-not-close.md:3` says
`**Status:** accepted`; line 493 opens `## Open questions`. The other 13 ADRs have
no such section. Related: at 615 lines it is 59% of the whole ADR corpus and ~25×
the median (24 lines) — see Q4. The `## Open questions` content also overlaps the
open `verification-integrity-mode` proposal (PR #185), which is where open
questions normally live.

---

## O-7 — User-facing security prose lives in `SECURITY.md`, not the guide

`SECURITY.md:68-89` ("Hardening notes for callers") tells users to leave
accelerators off for untrusted input under a latency budget, that `unrar` is part
of their deployment's trust boundary, and to extract into a scratch directory
before promoting. That is guide content in a file GitHub renders for vulnerability
reporters. `docs/safe-extraction.md` says none of it.

`SECURITY.md` should keep the reporting policy and scope; the caller guidance
belongs in `safe-extraction.md` with a link back. Folded into the growth plan in
[`page-shape.md`](page-shape.md) §1.

---

## O-8 — `docs/internal/index.md` understates `known-issues.md` by an order of magnitude

`internal/index.md:10` describes it as "Accelerator lifecycle / macOS coexistence
notes". The file is 709 lines covering stdlib `tarfile` EOF leniency, the pycdlib
process-global monkeypatch, three rapidgzip bugs, two distinct pyppmd native-abort
families with a version matrix and valgrind evidence, and an open intermittent
full-suite heap corruption with CI bandages and a bisect recipe. A contributor
reading the index will not open it, which is the opposite of what an index is for.

---

## O-9 — `open-issues.md` is a dated snapshot that has aged

`docs/internal/open-issues.md:10` pins itself to "2026-07-18 against `main` @
`93dc28e`" with one 2026-07-25 amendment. Since then #149/#162/#183/#191/#206/#207
and the #209 extras work have landed. Item **P6** (line 83) cites "PR #101 (still
open) / `docs/internal/rar-unrar-piping-investigation.md` (when merged)" — that
file does not exist in the tree, so the reference is to a future state that has not
arrived (PR #101 is indeed still open — verified against the repo's open PR list).

The dated-snapshot format is honest and better than an undated one. The
observation is only that it needs a refresh pass, which Topic 8 or the release
checklist can own.

---

## O-10 — `docs/grab-bag/` prose has drifted, as its own index predicts

Declared non-normative, so this is **not a defect** — recorded because it is the
evidence for "unpublish, don't delete" (Q1) rather than for keeping it visible to
users:

- `ARCHITECTURE.md` §1 module layout lists `internal/streams/decompressor_stream.py`;
  the file is `internal/streams/decompress.py`. It also annotates the 7z/RAR
  backends as "Phase 7" — they are Phase 6 (`openspec/project.md:101`).
- `SPEC.md` §2 lists a `[7z-write]` optional extra. It does not exist;
  `openspec/project.md:44` says "7z writing is not shipped (no `[7z-write]`)".
- `COMPARISON.md` carries a decision it explicitly records as later reversed (the
  `Intent` enum), which is correct behaviour for a historical document.

A user searching the published site for "7z-write" today finds an extra that was
never shipped.

---

## O-11 — Minor: the brief's own per-home line counts are transposed

`brief.md:45-46` gives `docs/internal/` 3,968 lines and `docs/grab-bag/` 2,831.
Measured: **3,731** and **3,068** — the same 237 lines attributed to the wrong
home. File counts (12 and 6) and the totals (6,799 non-user of 8,281 published,
excluding `decisions/`) are correct, so the headline "≈82% non-user" stands
unchanged. `docs/` is byte-identical between the brief's `403e7ff` baseline and
`ce674bf`, so this is a transcription slip, not drift.

Also `brief.md:170`: the code comment to update is at
`src/archivey/internal/streams/decompress.py`, not `decompressor_stream.py` (that
filename exists only in the stale grab-bag module map — see O-10).

---

## O-12 — Two runtime error messages embed documentation paths

`src/archivey/internal/streams/decompress.py:453` and `:467` raise `ValueError`s
whose text ends `"… — see docs/internal/known-issues.md)"`. These are strings a
user can see. They are repo paths, not URLs, so they are only actionable for
someone with a checkout — which stays true after the proposed move to
`dev-docs/known-issues.md`, but the strings must be updated in the same commit.
Listed in [`inventory.md`](inventory.md) §Migration mechanics.

Whether an error message should cite a maintainer document at all is a Topic 8
question, not one this review takes.

---

## O-13 — Coordination: the in-flight extras change will move the install story

`openspec/changes/consolidate-optional-extras/` (landed after the brief's baseline,
#209) proposes changing the optional-extras set. `docs/usage.md:5-9`,
`docs/formats.md:8-24`, `docs/acknowledgements.md:57-73` and
`docs/support-matrix.md:60-80` all encode the current extras. The proposed new
`install.md` ([`page-shape.md`](page-shape.md) §2) is where that lands.

**Sequencing note, not a finding:** if the extras change ships before the docs
migration, `install.md` should be written against the new extras rather than
migrated and then rewritten.
