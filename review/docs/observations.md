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

---

## O-14 — Three published pages attribute BLAKE2sp to an extra; it is native and zero-dep

**Closed 2026-08-03** — verified fixed on `main` @ `d34489f`. All three copies now
state that BLAKE2sp needs no package: `docs/formats.md:16`, `docs/formats.md:105`,
`docs/acknowledgements.md:73`. `consolidate-optional-extras` (#212) fixed the
published pages alongside the `pyproject.toml` comment, which is what the last
paragraph below asked for. Recorded here rather than deleted, because the closing
argument still stands: a structural audit reading every file for *filing* did not
catch a factual error on a user page.

Added 2026-07-29 during maintainer review of this audit, so numbered after the fact.

`src/archivey/internal/hashing/blake2sp.py` implements BLAKE2sp on stdlib
`hashlib` — no third-party package, no extra. Three published lines disagree:

| Line | Says | Correct? |
|---|---|---|
| `docs/acknowledgements.md:64` | `[rar]` / `[crypto]` → cryptography "(Blake2sp backend still TBD)" | ❌ nothing is TBD; it shipped natively |
| `docs/formats.md:16` | RAR needs "`[rar]` for header crypto / Blake2sp" | ❌ conflates the two; only header crypto needs the extra |
| `docs/formats.md:101` | "`[rar]` / `[crypto]`: header-encrypted RAR5 **and Blake2sp verification**" | ❌ same conflation |

A user reading any of these installs an extra they do not need, or concludes RAR5
hash verification is unavailable to them when it is always available.

This is the published twin of `code-self-documentation.md` B1, which found the same
stale claim in `pyproject.toml:69-74`. B1 was scoped to the packaging comment; the
docs copies were not noticed by either pass. `consolidate-optional-extras` task 1.2
already deletes the `pyproject.toml` half, so **the two halves should be fixed in
the same change** rather than leaving the published pages behind — see that change's
task 4.4.

**Worth noting for Topic 8's framing:** this is a factual error on a user page that
a structural audit reading every file for *filing* did not catch. It is evidence
that the content pass has to be its own deliberate read, not a byproduct.

---

## O-15 — `known-issues.md` needs a triage pass after the IA move (D9)

Recorded with Q7 B. Phase 3 only moves the file to `dev-docs/known-issues.md`.
A **required follow-up** (Topic 8 accuracy pass, or a dedicated small change)
classifies every section: resolved (ours) / mitigated (ours) / upstream
unfixable / open we-can-fix / evidence-only — and routes items to IDEAS,
`open-issues`, threat-model register, or `investigations/` per
[`DECISIONS.md`](DECISIONS.md) D9. Also rewrite the Gotchas accelerator bullet
for `_TrappingSource` (Bug 3 is contained; “process dies” is stale).

---

## O-16 — The integrity guarantee overstated what a `CorruptionError` means

Raised by the maintainer 2026-08-04, reading the moved text on
`docs/reading-members.md`, and **fixed in `docs-ia-split-user-guide`** rather than
deferred: it is a factual error about a load-bearing safety claim on a published page.

The moved-in wording said a `CorruptionError` means *"discard everything read from this
member; none of it is trustworthy"*. The ADR it came from qualified that with "as a
complete intact member", but the bolding buried the qualifier and the sentence read as
the stronger claim.

What is actually true, and what the page now says:

| Claim | Correct version |
|---|---|
| Bytes read before a `CorruptionError` are worthless | **Unknown quality.** On a compressed member that fails mid-stream, some are probably fine — we cannot say which, or how much. Unverified, not known-bad. |
| `CorruptionError` vs `TruncatedError` tells you what happened | **A best-effort label, not a diagnosis.** Damage that decodes into a shorter stream is indistinguishable from real truncation. Don't branch on it. |
| Every error raises | **We try to raise on every error we can detect.** Some formats store no checksum; some damage decodes to something valid-looking. |

No spec had to change — `compressed-streams` specifies the *exception mapping*
(corrupt → `CorruptionError`, short → `TruncatedError`), not the reliability of the
distinction or the status of the prefix. `dev-docs/investigations/adr-0014-investigation.md`
carries a note recording the sharpened reading next to the original reasoning.

A third point was added the same day, and it is the reassuring half: **a chunked read
loop delivers every readable byte and then raises.** `read(member.size)` returns short
and quiet, but the next read raises — so `while chunk := stream.read(n)` cannot end
silently on a truncated member. Verified against
`tests/test_codecs.py::test_verify_expected_size_short_chunked_then_empty_raises`,
which asserts the loop collects the whole available prefix before the `TruncatedError`.
The page now shows that loop as the recover-the-prefix recipe.

**For Topic 8:** do not restore the stronger phrasing when tightening this section, and
keep the chunked-loop guarantee — it is the answer to "how do I get what is readable
out of a damaged member".

*(Correction 2026-08-04: an earlier draft called that a "VISION founding use case".
It is not. VISION's two load-bearing claims are safe-by-default and memory-safe
parsing of hostile input; the founding use case is indexing and deduplicating messy
backups, and "damaged input is a first-class citizen" is one of five priorities that
origin story implies — and that bullet is about not failing at open, i.e. the listing
side, not the read contract.)*

---

## O-17 — Dev-doc register leaked into the user guide

Raised by the maintainer 2026-08-04. Several pages read as too technical for their
audience, which is the predictable cost of the IA migration: `safe-extraction.md` took
its enforced-guarantees list from a threat model, `reading-members.md` took its
guarantee from an ADR, and `formats.md` was always written close to the specs. The
prose is accurate; the register is wrong for the reader.

**The audience, stated so the rewrite has a target:** a working developer who is not a
compression or archive-format specialist. They know Python and streams. They do not
know what a "solid folder", an "ISIZE trailer", a "check value" or a "terminal
boundary" is unless the page says.

**Rules for the Topic 8 rewrite:**

1. **Define or drop the jargon.** First use of a format term gets a half-sentence gloss,
   or the sentence gets rewritten without it.
2. **Lead with what the reader does, not with the mechanism.** "Don't close the source
   underneath a live stream" before the explanation of why the C++ layer objects.
3. **Cut the provenance voice.** "This is a deliberate idiom, not a trap", "the
   load-bearing asymmetry", "target contract; best-effort today on a few backends" are
   ADR register — they argue with a reviewer who is not present.
4. **Be shorter.** Most of these sections lose 20–30% with nothing of substance gone.
   The guarantee section on `reading-members.md` is the worked example: rewritten for
   O-16, it is both more accurate and shorter.
5. **Keep the honesty.** Plainer is not vaguer. "We can't tell which bytes are good"
   is plain *and* precise; "the prefix is best-effort salvageable" is neither.

---

## O-18 — Reader close vs escaped member streams: docs are right, but the design was questioned

Raised by the maintainer 2026-08-04 on the outline's must-explain #20 line ("closing
the reader does not invalidate already-open streams") — *"doesn't it? that surprised
me."*

**Checked, and the docs are correct.** It is specified
(`archive-reading/spec.md:543-580`, "Context-manager and close lifecycle"), tested
(`tests/test_member_streams.py::test_post_close_reader_ops_are_usage_errors`), and
**consistent across all seven backends** — zip, tar, tar.gz, bare gz, directory, 7z and
RAR all read fine after `reader.close()`, and `stream.close()` afterwards is clean. So
there is nothing for Topic 8 to fix in the prose.

One wording nuance worth keeping in mind when tightening: the spec requirement says a
stream **MAY** remain usable, while its own scenario table states it as an outcome. The
guide currently promises the stronger version. If the behaviour is ever revisited, the
guide is the thing that has to change first.

**What the check turned up is a product question, not a docs one**, and it is filed as
`dev-docs/open-issues.md` **P7**: an unclosed member stream leaks a file descriptor
that GC never reclaims (+1 on every backend measured), and `reader.close()` does not
release it. That is closer to the hazard the maintainer's instinct was pointing at than
the read-after-close behaviour itself.

