# Public API at the 0.2.0 freeze: summary

**Reviewed on `main` at `878c75f`** (2026-09-25, after `#465`, `#468`, `#470`, `#471`
and `#475`), `[all]` config, `unrar` and `7z` present. Baseline: `./scripts/check.sh`
green; `./scripts/test.sh` 5439 passed, 47 skipped, 3 deselected, 4 xfailed. Every
observation was reproduced on that commit; the scripts are in each theme file's §Repro.
A **non-security** review: nothing here is a hostile-input finding, and the one bound
it touched is handed to `dev-docs/threat-model.md` (`QUESTIONS.md` §Handed off).

## Headline

**The surface is ready to freeze. The diagnostics mechanism is sound and consistent;
the gap is that the docs never explain it.** A new user cannot tell from the docs alone
when to look at diagnostics or what to do with them (`0-diagnostics.md` §The verdict).
That is fixed with prose, before the tag, with no public name changing: the one-page
explanation is written (`0-diagnostics.md` §The page) and is the proposed replacement
for the opening of §Diagnostics in `docs/errors-and-diagnostics.md`.

Beyond diagnostics, the 90 names in `__all__` all earn their place; the 15 arrivals
since July each have a verdict of keep (`A-surface.md`). Three small surface cleanups
are worth doing before the tag because they are free now and breaking later: an
internal type on `detect_format`'s signature, a public class that calls itself internal
(`MemberStreams`), and an exception nothing raises (`WriteError`). One module
(`archivey.detection_cost`) became API through `#475` without a page. The exception tree
is the right shape; the guide's table is missing eight of its 26 classes.

## Findings by severity

| Id | Sev | Finding | Recommendation | Decision |
| --- | --- | --- | --- | --- |
| D0-1 | High | The guide never explains how diagnostics work (exception vs diagnostic, the views, the summary, the policy); `api.md:57` points at an unpublished spec | Paste §The page into the guide | none needed |
| D0-2 | Medium | `ExtractionReport.diagnostics` covers the whole open under `archivey.extract()` and only the call under `reader.extract_all()` | Document both scopes; one pinning test | Q6 |
| D0-3 | Medium | The logger is a second channel, on by default, and the guide reads as if it were the old one | Keep; say so in the guide | none needed |
| A-1 | Medium | `archivey.detection_cost` is API (typed config field, docstring references) without a page | Document it like `terminal`; move two implementation names internal | Q1 |
| A-2 | Medium | `detect_format(collector=)` puts `internal.DiagnosticCollector` on a public signature | Private entry for `open_archive`; drop the parameter | Q4 |
| D-1 | Medium | Guide exception table names 15 of 26 classes; `ReadError` is used on the page and absent | Add eight rows | Q7 |
| A-3, A-4 | Low | `MemberStreams` docstring says "internal representation"; `reader.member_streams` is off the ABC | Demote and rename `_member_streams` | Q2 |
| A-5 | Low | `WriteError` importable from the root, nothing raises it | Remove the root import | Q5 |
| D0-4..D0-8 | Low | Views undocumented; `DiagnosticSeverity` has one value; `IGNORE` still counts; ZIP password rule; where the context classes are | One sentence each, all in §The page | Q3 for severity |
| B-1, C-1, D-2, D-3 | Low | `INDEXED` docstring; `api.md` opening sentence; three `Unsupported*` names; no `Raises:` blocks | Docstring clauses; nothing else | none needed |

Every other question the brief asked came back fine and is recorded under "What is
actually fine" in each theme file so it is not re-raised.

## What the maintainer decides

Seven questions in [`QUESTIONS.md`](QUESTIONS.md), options with a recommendation each.
None blocks D0-1.

## Files

| File | What it holds |
| --- | --- |
| [`0-diagnostics.md`](0-diagnostics.md) | The maintainer's question, the verdict, the one-page explanation, D0-1..D0-8, what is fine, repro |
| [`A-surface.md`](A-surface.md) | Verdict per name (90 + 19), the 15 arrivals, A-1..A-5 |
| [`B-semantics.md`](B-semantics.md) | Cost receipts across eight sources, one-mechanism evidence, B-1..B-4 |
| [`C-size.md`](C-size.md) | The count (123 importable), the CLI as second consumer, C-1 |
| [`D-errors.md`](D-errors.md) | The tree, the three name errors, D-1..D-3 |
| [`QUESTIONS.md`](QUESTIONS.md) | Q1..Q7 and the security hand-off |
| [`brief.md`](brief.md) | The commission, refreshed at `c599fc5` |
