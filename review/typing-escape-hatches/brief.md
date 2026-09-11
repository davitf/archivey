# Brief — type-checker escape hatches in `src/`

Commissioned 2026-09-11 against `main` @ `8e88e4f`. Sibling of
[`../exception-catchalls/brief.md`](../exception-catchalls/brief.md): that one audits
blind `except` clauses, this one audits weakened types. The two share a method and run
on disjoint sources, so they can go in parallel.

Conventions inherited from [`../README.md`](../README.md) §Conventions every brief
inherits — baseline first, three dependency configs, VISION as tie-breaker, deliverable
shape. This brief does not repeat them.

## The ask

> Every place in `src/` where the type checkers are told to stop looking — is the thing
> they were told still true?

A `# type: ignore` is the visible form of a weakened type. `cast()`, `Any`, and a
`TypeGuard` that returns `True` for a value it does not describe are the same defect
without the comment. Audit all of them and give each a disposition.

The precedent is PR #324's hub finding 3. `is_stream()` was annotated
`TypeGuard[BinaryIO]` and returned `True` for `io.TextIOWrapper`. The checkers believed
it, so `ensure_binaryio()` handed the text handle straight through and callers got `str`
out of `read()` several layers down. The annotation was not documentation — it was a
false assertion the checker then trusted and propagated.

The opposite direction has precedent too, recorded in `CONTRIBUTING.md`: declaring the
named `ArchiveFormat` instances as `ClassVar`s removed ~20 `# type: ignore`s *and* the
errors they were masking. Fixing the type model is the preferred outcome, not
re-labelling the suppression.

## Scope

`src/` only. `tests/`, `benchmarks/`, `scripts/` and `review/` carry ~166 further
`# type: ignore` comments that **no gate checks** — pyrefly and ty are both pinned to
`src/`. Whether to extend coverage there is a separate, undecided question; do not
start on it and do not edit those trees.

| In scope | Not in scope |
|---|---|
| `# type: ignore` / `# pyrefly: ignore` / `# ty: ignore` in `src/` | The same comments outside `src/` (undecided; see above) |
| `cast()`, `Any` annotations, `TypeGuard`, checker-appeasing `assert isinstance` | `# noqa: BLE001` and blind `except` ([`../exception-catchalls/`](../exception-catchalls/brief.md)) |
| The `CONTRIBUTING.md` rule that describes the correct suppression form | `# noqa: F401` re-exports in `__init__.py` (legitimate; `__all__` already carries the contract) |
| Fixing the type model so a hatch becomes unnecessary | Feature work, or refactors that do not remove a hatch |

## Why now

1. **PR #324 proved the failure mode is live, not theoretical.** A lying `TypeGuard`
   shipped and produced a user-visible wrong-type error far from its cause.
2. **The population is small enough to finish and large enough to matter** — ~81 typing
   hatches plus 13 `assert isinstance`. Every one is inline and visible, because
   `pyproject.toml` sets **no** rule-suppression config for either checker: they run at
   defaults. There is no hidden leniency to discover first.
3. **Nothing has audited this.** Neither `STATUS.md` nor `backlog.md` carries a typing
   theme, so there is no settled ground to re-litigate.
4. **It is cheap before the freeze.** Most dispositions change no runtime behaviour, and
   a public signature that gets tightened after `0.2.0` is a compatibility event.

## Known seeds

A floor, not the job. Everything below was verified on `8e88e4f` unless marked.

### Census in `src/`

| Hatch | Count | Concentration |
|---|---|---|
| `# type: ignore[...]` | 2 | both dead — see below |
| `cast(...)` | 26 | `tar_reader` 6, `zip_reader` 5 |
| `Any` annotation | 37 | `streamtools/binaryio.py` 12, `iso_reader.py` 8 |
| `TypeGuard[...]` | 3 | `binaryio.py` ×2, `volumes.py` ×1 |
| `assert isinstance` | 13 | mixed: some real invariants, some checker appeasement |

### S1 — `# type: ignore[code]` is a *blanket* line suppression here

Pyrefly does not validate the code inside the brackets. A code that does not exist still
silences the error:

```
$ sed -i '165s/type: ignore\[override\]/type: ignore[totally-bogus-code]/' \
      src/archivey/internal/streams/streamtools/base.py
$ uv run pyrefly check
 INFO 0 errors (4 suppressed, 12 warnings not shown)
```

So every `# type: ignore[...]` in `src/` silences *any* future error on its line, not the
one named. Pyrefly's own `# pyrefly: ignore[code]` **is** code-checked and fails closed —
a wrong code restores the error. Same for `# ty: ignore[code]`.

**Consequence for `CONTRIBUTING.md:216-217`, which is in scope:** that bullet currently
offers `# type: ignore[attr-defined]` as an example of a *specific* suppression. It is
not specific in this repo. Rewrite it so the specific forms are
`# pyrefly: ignore[<code>]` / `# ty: ignore[<code>]`, say plainly why, and keep the
existing "carry an inline reason" bullet.

### S2 — both existing `src/` suppressions are dead

- `src/archivey/internal/backends/zip_reader.py:497` — `# type: ignore[arg-type]`
- `src/archivey/internal/diagnostics_collector.py:232` — `# type: ignore[misc]`

Removing both leaves `pyrefly: 0 errors` and `ty: All checks passed`. **DELETE** them.
Keep the surrounding explanatory comments (e.g. `zip_reader.py:496`, "typeshed types
`ZipFile` too narrowly") — they document a real constraint even with no suppression
attached.

### S3 — two more exist only on PR #324's branch

`streamtools/base.py:165` and `streams/peekable.py:86` (`cursor/streamtools-wave1-fixes-f93c`).
Both are load-bearing for pyrefly (`bad-override`); ty does not object. They are already
covered by finding **F6** in that PR's review. Do not duplicate that work — sequence
after #324 merges, or coordinate.

### S4 — start at `streamtools/binaryio.py`

12 `Any`, 2 `cast`, and 2 of the 3 `TypeGuard`s, including the `is_stream` that #324 just
corrected. Densest file, live precedent, and the module every archive source crosses. It
calibrates the rest.

### S5 — the `TypeGuard`s deserve their own pass

`binaryio.py:179` (`is_filename`), `binaryio.py:278` (`is_stream`),
`volumes.py:469` (`_is_source_sequence`).

A `TypeGuard` is an **unchecked promise**: the checker trusts the return value with no
verification of the predicate. For each, ask whether the runtime check really establishes
the narrowed type for *every* input, hostile and edge cases included. Where it does not,
either fix the predicate or narrow the declared type to what the check actually proves.
`is_stream` is the worked example of getting this wrong; #324 fixed one input class
(`io.TextIOBase`) and its PR body records two more left open — write-only `IOBase`, and
duck-typed objects whose `read()` returns `str`.

### S6 — a standing unknown in the gate

`uv run pyrefly check` reports **"12 warnings not shown"** on a clean tree. Neither
`--output-format=json` (0 entries) nor `min-text` surfaces them, and `--help` lists no
warnings flag. Low priority, but find out what they are and record the answer. A gate
that hides twelve of anything is worth one hour.

## Dispositions

Every site gets exactly one, recorded with evidence.

| Disposition | When | Note |
|---|---|---|
| **DELETE** | The hatch is unnecessary | Prove it: remove, run both checkers, show clean |
| **FIX-IN-CODE** | The type model is wrong | `ClassVar`, `Protocol`, `@overload`, real narrowing, a parameterized generic, splitting a union. **Preferred outcome** |
| **TIGHTEN** | Genuinely needed but too broad | `Any` → Protocol or union; `cast` → narrower target or a runtime check; `# type: ignore[x]` → the native directive of whichever checker actually errors |
| **KEEP-WITH-REASON** | Unavoidable — third-party stub gap or checker bug | Native, code-checked form + inline reason + link to the upstream issue. A legitimate outcome, **not** a failure. Do not contort code to eliminate it |

## Suggested process

Per site, no exceptions:

1. Delete the hatch (or replace `cast(X, v)` with `v`, `Any` with the real type).
2. Run **both** `uv run pyrefly check` and `uv run ty check`. Record what each says.
   They disagree, and the disagreement is data: a site only one checker objects to gets
   that checker's native directive only.
3. No error → **DELETE**.
4. Error → try **FIX-IN-CODE** first. Only if the model genuinely cannot be fixed,
   **TIGHTEN**.
5. For any suppression kept or added, confirm it **fails closed**: swap in a bogus code
   and check the error comes back. If a bogus code also suppresses, the directive form is
   wrong.
6. Never widen a signature or add `Any` to make an error go away. That trades a visible
   hatch for an invisible one and is the failure this review exists to catch.

Order: suppressions → `TypeGuard` → `cast` → `Any` → `assert isinstance`. Mechanical
first, so the judgement-heavy work starts with a calibrated sense of what the checkers
actually complain about.

## Deliverable

1. **An inventory first, before any code change.** One row per site: `file:line`, hatch
   kind, what each checker says without it, proposed disposition, one-line reason. Land
   this on its own. ~81 sites is not reviewable as one diff, and the inventory is what
   makes the rest tractable.
2. Then **staged fix PRs grouped by category**, smallest and most mechanical first. Not
   one big PR.
3. `SUMMARY.md`, `QUESTIONS.md`, and a **"what is actually fine"** section per
   [`../README.md`](../README.md), so the next pass does not re-verify settled sites.

## Hard constraints

- `./scripts/check.sh` passes on every commit (ruff, format, pyrefly, ty, openspec, docs).
- DELETE / TIGHTEN of a pure annotation changes no runtime behaviour; a normal
  `./scripts/test.sh` run suffices.
- **FIX-IN-CODE can change runtime behaviour.** Those need tests, and the three-config
  rule (`[all]`, `[all-lowest]`, `[core-only]`) before pushing if the change touches
  anything version- or extras-dependent.
- If removing a hatch reveals a **real bug** — as it did in #324 — stop and report it as
  its own finding with a repro. Never fold a bug fix silently into an annotation PR.
- **Pause and ask** on any site whose honest fix changes a public signature or a
  documented contract. Use the decision packet in
  [`dev-docs/pair-workflow.md`](../../dev-docs/pair-workflow.md) §Decision packet — six
  fields, one question at a time. Do not pick a winner silently.

## Definition of done

- Every site in the census has a recorded disposition with evidence.
- No `# type: ignore[...]` remains in `src/`; what survives is native and code-checked.
- Every surviving suppression has been shown to fail closed.
- `CONTRIBUTING.md:216-217` describes the forms that are actually specific here.
- The "12 warnings not shown" question is answered.
- `SUMMARY.md` records what is fine, so this is not re-run from scratch.
