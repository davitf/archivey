# Typing escape hatches — SUMMARY

Brief: [`brief.md`](brief.md). Linear [ARC-20](https://linear.app/archivey/issue/ARC-20).
Measured at `main` @ `94468bd0` (2026-09-17). Inventory plus Q1 (public
`extra: dict[str, object]`). Theme files: [`inventory.md`](inventory.md),
[`typeguards.md`](typeguards.md), [`binaryio-and-typeshed.md`](binaryio-and-typeshed.md),
[`QUESTIONS.md`](QUESTIONS.md).

## Headline

**No new #324-class lie.** The one TypeGuard that shipped a wrong type is still
the one #324 fixed (`is_stream` vs `TextIOBase`). `is_filename` is honest.
`is_stream` and `_is_source_sequence` still over-claim, but the extra True cases
fail loudly later rather than returning `str` from `read()`.

The live population is **89 sites** (3 checked suppressions, 22 `typing.cast`,
48 `Any` annotations, 3 `TypeGuard`, 13 `assert isinstance` — 12 once the branch
merged `main` in and R6 went). The brief's "26
casts / 38 Any" was a grep: it counted two `memoryview.cast` calls as
`typing.cast`, and counted `Any` roughly per line rather than per annotation.

**Five casts delete with both checkers clean.** About half the `Any` sites
tighten to `object` the same way. The rest cluster in two known gaps: typeshed
models `BinaryIO` and `io.IOBase` / `IO[bytes]` as separate hierarchies (same
gap as seed S5b), and pycdlib/optional-codec objects have no stubs.

S2 and S3 are still closed. A **third** `# pyrefly: ignore[bad-override]` has
landed since the brief (`FullCountStream.name`), same KEEP-WITH-REASON as the
other two. All three fail closed.

## Baseline (`94468bd0`, `[all]`)

| Gate | Result |
|---|---|
| `./scripts/check.sh` | green. pyrefly `0 errors (3 suppressed, 12 warnings not shown)`; ty clean; ruff clean |
| `./scripts/test.sh` | 3353 passed, 35 skipped, 3 deselected, 5 xfailed |
| `unrar` / `7z` | present (skips are not the quiet-missing-binary trap) |

S6 is answered: the twelve warnings appear with `--min-severity=warn` (the
default is `error`). Listed under [What is actually fine](#what-is-actually-fine).

## Census vs the brief

| Hatch | Brief (`fb88c1b0`) | Now (`94468bd0`) | Notes |
|---|---|---|---|
| `# type: ignore` | 0 | **0** | S2 stays closed |
| `# pyrefly: ignore[bad-override]` | 2 | **3** | `FullCountStream.name` joined the S5b pair |
| `# ty: ignore` | 0 | 0 | ty is silent on the three `name` overrides |
| `typing.cast` | 26 | **22** | brief grep included `memoryview.cast` (2) |
| `Any` annotations | 38 | **48** | counted per parameter / return / alias, not per line |
| `TypeGuard` | 3 | 3 | |
| `assert isinstance` | 13 | 13 at `94468bd0`, **12** at this branch's head | R6 (`sevenzip_reader.py:302`) was removed by a 7z commit merged in from `main` |

Method: AST + tokenize over `src/`. Per site: delete the hatch (cast → the
value; `Any` → `object`; `TypeGuard[…]` → `bool`; drop the assert; drop or
bogus-code the pyrefly directive), then `uv run pyrefly check` and `uv run ty check`.
Tree restored after each probe.

## Findings, ranked

| ID | Severity | What | Disposition | Status |
|---|---|---|---|---|
| **S1** | 🟡 | `CONTRIBUTING.md` offered `# type: ignore[attr-defined]` as a *specific* suppression | rewrite the rule to pyrefly/ty native forms | **done in this PR** |
| **S6** | 🟢 | "12 warnings not shown" | `--min-severity=warn`; list below | **answered** |
| **C-del** | 🟢 | 5 casts both checkers accept without | DELETE | staged PR 1 |
| **G2** | 🟡 | `is_stream` still True for write-only `IOBase` and duck objects whose `read()` returns `str` | FIX-IN-CODE the predicate | staged PR 4 |
| **G3** | 🟡 | `_is_source_sequence` proves `Sequence`, not `Sequence[SourceItem]` (`bytearray` is True) | TIGHTEN the predicate | staged PR 4 |
| **C-overload** | 🟢 | 4 casts exist only because `_track_source_seeks: Path \| BinaryIO -> Path \| BinaryIO` | `@overload` | staged PR 3 |
| **C-typeshed** | 🟢 | ~10 casts are `IO[bytes]` / `BufferedIOBase` / `SpooledTemporaryFile` / `PyCdlibIO` vs `BinaryIO` | KEEP-WITH-REASON (S5b gap) | staged PR 7 comments |
| **A-object** | 🟢 | ~25 `Any` sites accept `object` on both checkers | TIGHTEN | staged PR 2 |
| **A-iso** | 🟢 | pycdlib dir-record / date bags | Protocol or `TYPE_CHECKING` stubs | staged PR 5 |
| **A-codec** | 🟢 | `_decomp: Any` on four optional codec wrappers | small Protocol per codec | staged PR 5 |
| **Q1** | 🟡 | public `ArchiveMember.extra` / `ArchiveInfo.extra` / `replace(**kwargs)` are `Any` | **DECIDED A** — `dict[str, object]` | **done** |

No 🔴. Nothing here undercuts a VISION safety/cost claim. The #324 failure
mode is the ranking reason this review exists; G2 is the leftover of that, not
a new one.

## Staged fix PRs

Smallest and most mechanical first, as the brief asked. Each remaining PR is
one category.

1. **DELETE the five dead casts** — `progress.py:112`, `tar_reader.py:470`,
   `tar_reader.py:772`, `selection.py:19`, `full_count.py:168`. Annotation-only;
   `./scripts/test.sh` is enough.
2. **TIGHTEN internal `Any` → `object`** where both checkers stayed clean
   (`binaryio.py` helpers, `verify` algorithm params, `ReadOnlyIOStream.write`,
   ISO getattr/kwargs). `listing_limits` already moved with Q1. **`_raw` is not in
   this PR** — see the nested item.
   - **Staged PR 2b — `ArchiveMember._raw: Any` → `object`** (A25), its own
     change, after PR 1. It must add a narrowing at `tar_reader.py:470` that
     does not exist today: the assert R11 records is in a different function.
     The inventory's sequence note has the detail.
3. **`@overload` on `_track_source_seeks`** — drops four Path/BinaryIO casts in
   `tar_reader` / `zip_reader`.
4. **TypeGuard predicates** — G2 and G3. Runtime-visible; needs tests.
5. **Remaining `Any`** — ISO pycdlib Protocol, codec `_decomp` Protocols,
   `ZipFile._lock` as `ContextManager`, `verify.py` `Mapping[HashAlgorithm \| str, …]`.
6. ~~**Public `Any` on `types.py`**~~ **done (Q1 A).** `replace(**kwargs: object)`
   removes the `Any` but adds no checking — the keyword names and value types are
   still unverified, and the docstring now says so. The key → type map that Q1's
   contract now needs is PR 8.
7. **KEEP comments** on surviving typeshed `BinaryIO` casts, and name
   `FullCountStream` next to `PeekableStream` in the `ReadOnlyIOStream.name`
   docstring.
8. **The `extra` key map** (maintainer, 2026-09-20) — **this PR.**
   Q1 made every `extra` value an `object` a caller must narrow, and narrowing
   correctly needs to know which key holds what. PEP 728 TypedDicts were the
   first shape; mypy rejects `extra_items=` and then treats the TypedDict as
   having no keys, so that shape was dropped. The map is now two runtime
   `dict[str, object]` subclasses (`MemberExtra`, `ArchiveInfoExtra`) whose
   `__getitem__` is overloaded once per known key. `docs/formats.md` and
   `docs/api.md` point at them. (Before this PR that page documented 4 of ~17
   keys and the complete table lived in `QUESTIONS.md`.) One class per bag
   (member extras and `ArchiveInfo.extra` stay separate). Unknown keys stay
   legal and read as `object`. The `EXTRA_*` constants stay, marked `Final`.
   **Measured 2026-09-20 on pyrefly 1.1.1, ty 0.0.60, pyright 1.1.414, and
   mypy 1.19.1 at the 3.11 floor:** a known-key subscript read carries its
   declared type on all four; unknown keys read and write as `object` with no
   error. Three accepted costs:

   - Writes are not type-checked. The `str, object` fallback is what unknown
     keys need; overloading `__setitem__` does not recover write checking.
   - `.get()` keeps returning `object`. Overloading `get` disagrees across
     the four checkers.
   - A plain dict is not assignable to the field. Construction sites wrap
     `MemberExtra({...})` / `ArchiveInfoExtra({...})`.
   No `typing_extensions`, no `TYPE_CHECKING` wrap, no `cast` factory —
   `field(default_factory=MemberExtra)` is writable directly.

   Supersedes the per-format-aliases-only note that stood here before.

## What is actually fine

- **S2 / S3.** `grep -c "type: ignore" src/` is still 0. The two
  `# pyrefly: ignore[bad-override]` sites the brief signed off are unchanged in
  form. Do not re-open.
- **S5b.** `ReadOnlyIOStream.name -> Never` plus the widening suppressions is
  still the right model. Dropping `BinaryIO` was measured at 64/64 and is not
  on the table. The docstring still carries all three points (deleting the
  property does not remove it; `typing.IO.name` is the root; dropping the base
  is too expensive).
- **The third suppression (`FullCountStream.name`)** is the same KEEP, not a
  new pattern. It fails closed (bogus code restores `bad-override`). ty never
  errors, so there is no `# ty: ignore`. The only residue is the base docstring
  naming `PeekableStream` and not `FullCountStream`.
- **`is_filename`.** `isinstance(obj, (str, bytes, os.PathLike))` matches the
  `TypeGuard` target. Not a #324.
- **Every `assert isinstance`** (13 as measured, 12 on the merged tree). Both
  checkers stay clean without them *because `member._raw` is `Any`*. They are
  runtime invariants for backend handles (`ZipInfo`, `TarInfo`, `RarMemberInfo`,
  `_MemberRaw`, `PlainHeader`), not checker appeasement. Keep them. If `_raw` is
  later tightened to `object`, they become the narrowing — do not delete them in
  PR 1. Note that `tar_reader.py:470` has **no** assert to become one; PR 2b adds it.
- **`selection.py:18`.** Pyrefly warns `redundant-cast`; ty still needs the
  cast (`Collection ∩ Callable`). Checker disagreement is data: keep the cast
  for ty, no pyrefly suppression (warnings are not the gate).
- **S6 — the twelve warnings**, `--min-severity=warn`:

  | Count | Code | Where |
  |---|---|---|
  | 4 | `deprecated` | `@contextmanager` annotated `-> Iterator` not `Generator` (`cli/common.py`, `logging_config.py`, `zip_reader.py`, `measurement.py`) |
  | 3 | `redundant-condition` | integer literal used as condition, equivalent to `False` (`extract_cmd.py` ×2, `test_cmd.py`) |
  | 2 | `redundant-cast` | `selection.py:18-19` (18 is a ty-needed cast; 19 is a DELETE) |
  | 2 | `unnecessary-type-conversion` | `sfx.py` `bytes()`, `codecs.py` `bool()` |
  | 1 | `untyped-import` | `tqdm` in `cli/progress.py` |

  None of these is a hidden error. The `deprecated` / `redundant-condition` /
  `unnecessary-type-conversion` rows are outside this review's hatch census;
  they are recorded so the next pass does not re-open S6. Not a staged PR
  unless someone wants a drive-by.

## Definition of done (this PR vs the review)

| Done item | This PR | Later |
|---|---|---|
| Every site has a disposition with evidence | yes — [`inventory.md`](inventory.md) | |
| No `# type: ignore[...]` in `src/` | already 0 | keep it that way |
| Surviving suppressions fail closed | verified on all 3 | |
| `CONTRIBUTING.md` describes forms that are actually specific | **yes** | |
| "12 warnings not shown" answered | **yes** | |
| `SUMMARY.md` records what is fine | **yes** | |
| Staged fix PRs | 6 done (Q1); 8 this PR | 1, 2, 2b, 3, 4, 5, 7 |
