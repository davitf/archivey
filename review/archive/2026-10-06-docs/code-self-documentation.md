# Code changes that would reduce the documentation burden

Filtered out of the independent code-derived pass
([`independent/`](independent/)). Those artifacts answer "what must the docs say";
this one asks the inverse: **what could the code say itself, so the docs don't have to?**

Every item below was re-verified against `main` @ `49f221f` before being listed — the
independent agent could not see intent, so its raw output is an input, not a worklist.
Items it raised that turned out to be already-handled are recorded at the bottom so they
are not re-raised.

## Why this is a separate artifact

Do **not** point an agent at the independent brief and ask it to fix the code. That pass
was explicitly denied `VISION.md`, the ADRs, the specs and the threat model, so it cannot
distinguish a defect from a deliberate trade — and it says so. A large share of its
"why?" items have recorded answers (usage errors outside the tree → ADR 0012; opt-in
`MemberStreams` → ADR 0003; no implicit pipe buffering → ADR 0010). An agent told to
"fix" from that list would re-litigate settled design against a public API that is about
to freeze.

The filter applied here: **would a competent user still need this explained after the
change?** If yes, it is a docs task. Only if no does it belong below.

---

## A. Error messages that name the wrong thing (mechanical, no design change)

**A1 — ZIP codec install hints point at the `[7z]` extra.**
`streams/codecs.py:1455` (brotli), `:1523` (pyppmd), `:1579` (inflate64) all emit
`pip install archivey[7z]`. A user hitting a Deflate64 or PPMd member **in a ZIP** is told
to install the "7z" extra. It is technically correct — that extra does provide the codec —
and reads like the library misidentified their file.

Options: name the capability rather than the format (`archivey[7z]` provides "extended
member codecs"), or add a codec-oriented alias extra. Either removes a documentation
line permanently. Rationale-gap 4 asks whether `[7z]` is the intended umbrella — that is
the decision to make first.

---

## B. Stale statements in shipped files (fix now — they actively mislead)

**B1 — the `[rar]` extra's TODO is out of date.** `pyproject.toml:69-74` says the bundle
should also pull a Blake2sp backend, that "no standalone package decided yet", and to
"resolve when format-rar lands". Verified: format-rar landed, and BLAKE2sp is implemented
natively on stdlib in `src/archivey/internal/hashing/blake2sp.py` (no third-party
package, `hashlib`-based). The TODO describes a problem that was solved a different way.

This is the clearest case in the set: a comment in shipped packaging metadata that tells a
reader the opposite of what is true. Replace it with one line recording that BLAKE2sp is
native and zero-dep.

---

## C. API vocabulary split (needs a decision, and the window closes at `0.2.0`)

**C1 — two vocabularies for one concept.** `open_archive` takes
`member_streams=MemberStreams.SEEKABLE` (a flag enum); `open_stream` takes
`seekable=True` (a bool, `core.py:282`). The docstring explains why concurrency is
meaningless for a single stream — which justifies *dropping CONCURRENT*, not *changing
the spelling of SEEKABLE*.

Every user who learns one API pays to learn the other, and no doc sentence makes that
free. Worth deciding deliberately rather than by accretion, and **pre-`0.2.0` is the only
time it is free** — after the tag it is a breaking change. Note this is a public-surface
question that overlaps the archived api-coherence review; check its findings before
reopening.

---

## D. Raised by the independent pass, verified as already handled — do not re-raise

- **ISO monkeypatches `pycdlib.pycdlib.collections` process-wide**
  (`must-explain.md` 29, `rationale-gaps.md` 6). The agent flagged it as "surprising if
  documented nowhere". It is in fact documented thoroughly *in the code*:
  `backends/iso_reader.py:20-30` module docstring states the process-global side effect,
  the trade, and points at `known-issues.md`; `:115-131` explains why the guard is
  installed once and permanently. **No code change.** The real gap is that none of this
  reaches a *user-facing* page — a docs task, and a good example of the pass mistaking
  "absent from published docs" for "absent from the code".
- **Usage errors outside the `ArchiveyError` tree** — ADR 0012, deliberate.
- **`MemberStreams` opt-in defaults** — ADR 0003, deliberate.
- **No implicit buffering of a pipe** — ADR 0010, deliberate.
- **`OnError` not aborting on the first `BLOCKED` member** — a recorded deferral with a
  comment at `extraction_types.py:79-80` naming it a future opt-in.

---

## Expanded elsewhere

A1 and C1 turned out to be bigger than message edits — both are public-surface shape
questions with a `0.2.0` deadline. Options, recommendations and costs are in
[`api-surface-suggestions.md`](api-surface-suggestions.md). Headline: the extras are
already **not** one-per-format (`7z` is a codec bundle; `rar` is byte-identical to
`crypto` and cannot express its real `unrar` binary requirement), and both ADR 0003 and
ADR 0004 point *toward* aligning the capability vocabulary rather than against it.

## Suggested handling

- **A1 + B1**: one small change, no decisions needed beyond A1's wording. Both are
  message/comment edits with no behavioural effect.
- **C1**: a maintainer decision, and time-boxed by the release. If the answer is "keep
  both", record it as an ADR so the next reviewer does not re-derive it — which is
  exactly what happened here.
- **D**: no action; listed so the next pass does not spend budget on it.

None of this blocks the docs IA review. A1/B1 can land independently at any time.
