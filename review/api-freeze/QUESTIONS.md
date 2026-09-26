# Questions for the maintainer

Each is a decision the review cannot make. Options first, recommendation marked. None
of these blocks the diagnostics fix (D0-1), which is docs only and needs no decision.

## Q1 · `archivey.detection_cost`: document as is, or re-export? (A-1)

The budget is now a config field typed with a class from this module, and the
`ArchiveyConfig` docstring sends users there for the presets. Nothing on `docs/api.md`
mentions the module.

- **(a) Document the module where it is**, the way `terminal` is: a "Detection cost"
  section on `api.md`, a clause on the page's opening sentence, `__all__` stays at 90.
  Move `MutableDetectionCostReceipt` and `default_detection_budget` under `internal/`.
- (b) Re-export `DetectionBudget` and the three presets from `archivey` (94 names),
  document the receipt types under the module.
- (c) Leave it: the package docstring already says "public, not re-exported".

**Recommendation: (a).** Docs only, matches the `terminal` precedent, and (c) freezes
11 names with no page.

## Q2 · `MemberStreams`: demote, or make it honest? (A-3, A-4)

Its docstring calls it "the internal representation" and says `reader.member_streams`
"is not part of the typed public contract".

- **(a) Demote**: out of `__all__` and `api.md`, still importable from
  `archivey.types`; rename the reader attribute `_member_streams`.
- (b) Keep it public: rewrite the docstring to drop "internal", add `member_streams`
  to the `ArchiveReader` ABC so the attribute is contract.

**Recommendation: (a).** No caller constructs one and no public method takes one.

## Q3 · `DiagnosticSeverity` with one value (D0-5)

- **(a) Keep**, with a docstring sentence saying every diagnostic is `WARNING` today and
  the "stop or note" distinction is disposition.
- (b) Remove the enum and the `severity` field before the tag; add both back when a
  second level exists.

**Recommendation: (a).** Removing a field from `Diagnostic` and `to_dict()` output is
more churn than a sentence, and the reason to keep is written down.

## Q4 · `detect_format(collector=)` (A-2)

- **(a) Drop it from the public signature**: `open_archive` calls a private function in
  `internal/detection.py` that takes the collector; `detect_format` keeps `config` and
  `follow_stub_volumes`.
- (b) Keep it and type it `object`, documented as "for internal callers".

**Recommendation: (a).** It is the one `internal/` type on the surface, and the change
is mechanical.

## Q5 · `WriteError` at the package root (A-5)

Importable from `archivey`, out of `__all__`, and nothing raises it.

- **(a) Remove the import from `__init__.py`** before the tag; keep the class in
  `exceptions.py`.
- (b) Leave it, on the reasoning in the `noqa` comment ("write API not shipped yet;
  kept importable").

**Recommendation: (a).** A name a user can `except` and never see is a small promise
with no upside, and after the tag it cannot be withdrawn.

## Q6 · Should `extract_all()`'s report include open-phase diagnostics? (D0-2)

- **(a) No; document the two scopes** (the table in `0-diagnostics.md` §The page) and
  add one test that pins them against each other.
- (b) Yes: make both reports span the reader's whole life, so the field means one
  thing.

**Recommendation: (a).** (b) double-counts for a caller who reads `reader.diagnostics`
too, and changes a report's contents on every second `extract_all()` call.

## Q7 · The guide's exception table (D-1)

Not a fork, a confirmation: add the eight missing rows (`ReadError` first). If the
maintainer would rather the table stay short, the page should at least stop relying on
`ReadError` at line 214 without listing it.

## Handed off

Nothing in this review is a hostile-input finding. The one bound it touched, the
retention budget on the diagnostics collector, is a resource cap and belongs to the
`ResourceLimitError` register in `dev-docs/threat-model.md`, where the family already
is.

## Rulings (2026-09-26)

The maintainer answered each question in the project thread, one at a time.

| Q | Ruling | Note |
| --- | --- | --- |
| Q1 | (a) document `detection_cost` where it is | "minimizes root exports, these are too niche for root" |
| Q2 | (a) demote `MemberStreams` | |
| Q3 | **(b) remove** `DiagnosticSeverity`, the field and the JSON key | "let's simplify the API" (against the recommendation) |
| Q4 | (a) drop `collector=` from `detect_format` | |
| Q5 | **(c) delete** the `WriteError` class | the third option offered in the thread, not in the file above |
| Q6 | (a) document the two report scopes, one pinning test | |
| Q7 | (a) add the missing table rows | whether every exception type is needed is a follow-up after 0.2.0, tracked internally |

All seven are implemented in the fix PR that follows this one, together with D0-1 (the
page) and the Low docstring findings.
