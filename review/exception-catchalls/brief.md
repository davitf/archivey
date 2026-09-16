# Brief — catch-all `except` clauses in `src/`

Commissioned 2026-09-11 against `main` @ `8e88e4f`. Sibling of
[`../typing-escape-hatches/brief.md`](../typing-escape-hatches/brief.md): that one audits
weakened types, this one audits blind exception handlers. Same four dispositions,
disjoint sources, so the two run in parallel.

Conventions inherited from [`../README.md`](../README.md) §Conventions every brief
inherits — including its **Error contract** bullet, which is the standard this review
measures against. Not repeated here.

## The ask

> Each blind `except` in `src/` either re-raises, or explains why swallowing is correct.
> Verify that both halves are true, and tighten the ones that are neither.

`CONTRIBUTING.md` §Exception translation states the contract: archive problems surface as
`ArchiveyError` subclasses via the reader translator; **no catch-all** that converts
unknowns; unrecognized exceptions propagate; `OSError` / `KeyboardInterrupt` /
`MemoryError` propagate unless a spec says otherwise. `ArchiveyUsageError` sits outside
the tree (ADR 0012).

A blind handler is where that contract is most easily lost, and the loss is silent — the
symptom is an exception the user never sees, not a crash.

## Calibration — read this before planning the work

**This population is in good shape, and the brief is scoped accordingly.** Recon found no
smoking gun. Every one of the 30 marked sites already carries an inline reason on the
same line, and they fall into five recognisable patterns (below) rather than looking like
accumulated carelessness.

So this is a **verification** review, not an excavation. The question is not "how many
violations are there" — it is "are these 30 recorded reasons still true, and are the
patterns applied consistently?" A finding here will most often be *this reason no longer
matches what the handler does*, not *someone was lazy*.

Do not manufacture severity to justify the review. A large "what is actually fine"
section is the expected outcome, and per [`../README.md`](../README.md) it is a required
deliverable, not a consolation prize.

## Scope

`src/` only.

| In scope | Not in scope |
|---|---|
| `except Exception` / `except BaseException` in `src/`, marked or not | The same in `tests/`, `benchmarks/`, `scripts/`, `review/` |
| Whether each handler re-raises, translates, or deliberately swallows | Type-checker suppressions ([`../typing-escape-hatches/`](../typing-escape-hatches/brief.md)) |
| Whether `# noqa: BLE001`'s inline reason still matches the code | The `ArchiveyError` hierarchy's shape (settled: ADR 0012, `error-handling` spec) |
| Consistency of the five patterns across backends | Adding new exception types, or re-litigating translation policy |

## Why now

1. **The contract is load-bearing and stated in three places** — `CONTRIBUTING.md`,
   ADR 0012, `openspec/specs/error-handling/spec.md` — and nothing has checked the code
   against it as a sweep.
2. **Freeze proximity.** What exceptions escape the public API is a compatibility surface.
   Tightening a handler after `0.2.0` changes what callers catch.
3. **Nothing covers it.** No typing or exception theme in `STATUS.md` or `backlog.md`.
4. **It is bounded.** 55 blind handlers, 30 with markers. Finishable.

## Known seeds

Verified on `8e88e4f`. A floor, not the job.

### Census in `src/`

| Shape | Count |
|---|---|
| `except Exception` | 25 (21 carrying `# noqa: BLE001`) |
| `except BaseException` | 30 |
| bare `except:` | **0** |
| `# noqa: BLE001` markers total | 30 |

`archive_stream.py` 9, `codecs.py` 8, `verify.py` 5, `base_reader.py` 3,
`rar_reader.py` 3, `decompress.py` 1, `extraction.py` 1.

**The ~25 unmarked blind catches are the first thing to check.** Ruff's BLE001 does not
fire on a handler that re-raises, so the absence of a marker is itself an implicit claim
("this one re-raises"). Verify that claim rather than assuming ruff's exemption is exactly
the contract's.

### The five patterns

Every marked site fits one. Judge each *against its pattern*, and flag sites that sit
between two.

1. **Teardown hygiene** — swallow deliberately during close/finalize.
   `extraction.py:1693` "best-effort close; nothing left to do",
   `archive_stream.py:202` "never raise from a finalizer",
   `codecs.py:186`, `codecs.py:739`, `decompress.py:701`.
   *Ask:* can a real `ArchiveyError` about the archive's integrity be lost here, as
   opposed to a genuine teardown nuisance?

2. **Error combination** — capture, then raise, so a second failure does not mask the
   first. `base_reader.py:540` / `:1795` / `:2000`, `rar_reader.py:360` / `:377` / `:506`,
   `archive_stream.py:546` / `:549` / `:562`.
   *Ask:* does every path actually reach a `raise`? These are the handlers where an early
   return would silently convert "both failed" into "neither did".

3. **Translator handoff** — `except Exception as e: self._fail(e)`, re-raised through the
   translator. `archive_stream.py:280` / `:396` / `:414`.
   *Ask:* does `_fail` raise on **every** path, including when the translator returns
   `None` for an unrecognized exception? The contract says unrecognized ones propagate
   raw; confirm that is what happens rather than being converted.

4. **C-boundary trap** — `codecs.py:282-313` traps `BaseException` in the source shim and
   returns a sentinel (`False`, `b""`, `0`) so no Python exception unwinds through the
   accelerator's C++ frames; `_AcceleratorStream._reraise_trapped` surfaces it later.
   **This one is already reasoned in the code** (`codecs.py:189-199`), and the reasoning
   looks sound: `read`/`readinto`/`seek` all re-check (`:228`, `:233`, `:238`), and the
   comment argues the re-raise always precedes data reaching the caller.
   *Ask, without assuming it is wrong:* is that "always precedes" claim true on every
   path? And is a trapped `KeyboardInterrupt` on a handle the caller closes without
   reading the intended outcome, given that the contract says `KeyboardInterrupt` and
   `MemoryError` propagate? If yes, the answer belongs in that comment explicitly — it
   currently reasons about errors, not about interrupts.

5. **Diagnostic probe** — a probe must never break a read, so it returns a fallback.
   `codecs.py:219` "a diagnostic probe never breaks a read" (returns `None`),
   `verify.py:259` "opaque accel errors ≈ no trailing data" (returns `b""`).
   *Ask:* `verify.py:259` substitutes `b""` for *unknown* failure. Is "no trailing data"
   a safe default for a **verification** path, or can it turn a real integrity failure
   into a pass? This is the seed with the clearest route to a VISION-ranked finding
   (claim 3, damaged input is first class).

### The disposition rule (maintainer, 2026-09-11)

> Real occurrences should either re-raise or explain why the catch-all makes sense;
> others should be tightened.

## Dispositions

| Disposition | When |
|---|---|
| **KEEP-WITH-REASON** | Swallowing is correct and the inline reason says why, accurately. If the reason is right but thin, improve the comment — that is still this bucket |
| **TIGHTEN** | The handler is broader than its job. Narrow to the exception classes actually expected, or re-raise what does not belong |
| **FIX-IN-CODE** | The handler exists to paper over a structural problem. Fix the structure |
| **DELETE** | The handler catches nothing that can occur, or duplicates a guard upstream |

`except BaseException` carries a higher bar than `except Exception`: it swallows
`KeyboardInterrupt` and `MemoryError`, which the contract says propagate. Each of the 30
needs an explicit reason for reaching past `Exception`, not an inherited one.

## Suggested process

1. **Baseline** per [`../README.md`](../README.md), then build the census table: every
   blind handler, its pattern, its stated reason, and what it does on each exit path.
2. **Unmarked first** — the ~25 without `# noqa: BLE001`. Confirm each really re-raises.
   A false negative here is worth more than a marked handler with a good comment.
3. **Then by pattern, not by file.** Patterns 5 and 1 first (they can swallow real
   errors), then 3, then 2, then 4.
4. For each candidate finding, name the **concrete input or state** that reaches the
   handler and what the caller sees instead of the truth. A handler that cannot be
   reached by any input is itself a finding (DELETE).
5. Where a fix changes what escapes the public API, **pause and ask** with a decision
   packet ([`dev-docs/pair-workflow.md`](../../dev-docs/pair-workflow.md) §Decision
   packet) before implementing.

## Hard constraints

- **Every fix here changes runtime behaviour.** Unlike the typing sibling, nothing in this
  review is inert. Each fix needs a test proving the exception now escapes (or is now
  translated), and the three-config rule before pushing where extras or versions matter.
- Red–green for anything framed as a bug fix: the failing repro first.
- `./scripts/check.sh` passes on every commit.
- Do not remove a `# noqa: BLE001` without changing the handler — the marker is not the
  finding, the handler is.
- Do not widen the `ArchiveyError` tree or add exception types to make a handler tidier.
  That is a contract change and goes through OpenSpec, not through this review.

## Definition of done

- Every blind handler in `src/`, marked or not, has a recorded disposition and pattern.
- Every `except BaseException` has an explicit reason for reaching past `Exception`.
- Every surviving `# noqa: BLE001` reason accurately describes its handler.
- The five patterns are named somewhere durable — a `dev-docs/topics/` page if they
  survive review as the house rules, so the sixth one written is written deliberately.
- `SUMMARY.md` with a substantial "what is actually fine" section, per §Calibration.
