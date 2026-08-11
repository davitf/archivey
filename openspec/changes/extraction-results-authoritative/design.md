# Design — extraction-results-authoritative

## Context

Three reviewer responses to `dev-docs/discussions/diagnostics-archive-vs-usage.md`
agreed on more than they disagreed on. This change implements the union of what they
agreed, plus the two proposals that survived a check against the tree.

The circulated record, all under `dev-docs/discussions/`:

| Document | Author | Prompt |
| --- | --- | --- |
| `diagnostics-archive-vs-usage.md` | the question as circulated, with a resolution banner pointing here | — |
| `reviewer-1-opinion.md` | Grok 4.5 via Cursor | neutral, doc-only |
| `reviewer-2-opinion.md` | Claude Opus 5 via Claude Code | neutral, doc-only — identical framing to reviewer 1 |
| `reviewer-3-opinion.md` | Claude Fable 5, plain chat, no repo access | leading ("is this over-engineered?") |

Reviewers 1 and 2 received the same neutral prompt, so their agreement is a paired
comparison; reviewer 3's framing was solicited, so its agreement corroborates less.
Each opinion file carries its own byline and verbatim prompt. Where the notes are
cited below, the citation is to an argument that was checked against the tree, not to
the fact that a reviewer made it.

Verified before designing (repo at `9b170c0`):

- `SUPERSEDED` means a non-current duplicate, decided from `is_current` at listing
  time before any write is attempted (`extraction.py:365-370`), yielding
  `path=None, error=None` (`safe-extraction/spec.md:597-599`). It is **not** a free
  slot for a destination collision, which is a different event at a different stage.
- `requested_path != path and status == EXTRACTED` is the defined `RENAME` marker
  (`safe-extraction/spec.md:603-606`), and `requested` is computed from the
  already-sanitized name (`extraction.py:565`), so it is both occupied and currently
  equal for a sanitized member.
- A `REPLACE` collision is silent in `results` by design (`extraction.py:591-597`).
  Repro — a ZIP holding `A.txt` and `a.txt` under `OverwritePolicy.REPLACE`:

  ```
  member='A.txt'  status=extracted  path='A.txt'  requested='A.txt'
  member='a.txt'  status=extracted  path='A.txt'  requested='A.txt'
  on disk → 'A.txt': 'second member content'
  ```

- The dual channel is normative, not drift: `safe-extraction/spec.md:608-609`
  requires exactly one matching diagnostic per continued `BLOCKED`/`FAILED` result.

## Decisions

### D1 — Two clauses, not one

Option A's drafted ceiling ("not determinable from the declared contract, and
actionable") is the test that has actually been applied three times, and it is right
for **admission**. It is not sufficient on its own: all four extraction codes pass it
and were still misplaced. The missing axis is **placement** — an operation that
returns a structured per-item report already has a home for per-item outcomes.

Splitting the two also fixes a flaw in reviewer 2's single-clause formulation, which
let "would a caller want to escalate this?" override placement and so kept
`EXTRACTION_MEMBER_BLOCKED` alive. Escalation is a *capability question*, answered
separately by D4, not a reason to duplicate a fact.

Rejected: keeping the archive-vs-usage cut in any normative form. It is an accurate
description of the taxonomy's shapes and has never decided anything; all three
reviewers rejected it independently.

### D2 — `OVERWRITTEN` as a new status, revised retroactively

The clobbered member is the only participant whose outcome is currently wrong: it
reports `EXTRACTED` for content that no longer exists. A new status is the smallest
honest signal, and it completes the family — `NOT_OVERWRITTEN` (existing destination
kept, this member not written), `SUPERSEDED` (non-current, never written),
`OVERWRITTEN` (written, then replaced by a later member).

`path=None`, because nothing at that path is this member's content any more;
`requested_path` retains the destination it wrote to, so a caller can join the pair
to the replacing member's `path`. That join is now typed rather than inferred from
two `EXTRACTED` rows sharing a path.

The revision is retroactive: the earlier member's result already exists when the
collision is detected. `collision_map` currently stores `key -> Path`
(`extraction.py:337, 715`) and must become `key -> (Path, result_index)`; the
coordinator already revises `results` by index in `_resolve_orphan`, so the mechanism
exists. Result order stays member-processing order.

The other three resolutions need no new signal: `RENAME` is the existing
`requested_path != path` marker, `SKIP` yields `NOT_OVERWRITTEN` with `requested_path`
set, `ERROR` yields `FAILED` with the error. So the full `{renamed, replaced, skipped,
errored}` vocabulary the diagnostic carried is derivable from results once
`OVERWRITTEN` exists.

Rejected: reusing `SUPERSEDED` (occupied — reviewer 1 checked and is right); a
`replaced_by` member reference (heavier, and the path join already answers it).

### D3 — `presented_name` for portable rewrites

A caller `filter` rename produces **three** names: the archive name
(`result.member.name`), the filter's output, and the portable rewrite
(`result.path`). The removed diagnostic recorded the middle-to-final pair, so for a
filtered member the pair is not reconstructible from the result at all. A dedicated
`presented_name: str | None` field — the full relative name before portable
rewriting, `None` when no rewrite occurred — is the only signal that covers the
filtered case.

Rejected: reviewer 3's `requested_path != path`. That comparison is already the
`RENAME` marker, and overloading it makes one signal mean two things — the defect
this change exists to remove.

### D4 — `abort_on`, one parameter, not a second policy engine

Moving a fact out of diagnostics removes its escalation, because dispositions apply
to diagnostics only. That trade was accepted for the blocked member (a named knob
replaces it) but nobody had proposed a replacement for collision and sanitize; left
alone, this change would silently delete a caller's ability to abort on a silent
overwrite.

`abort_on: Collection[AbortOn]` is one parameter carrying a small closed enum. It is
deliberately **not** a per-event disposition map: that would rebuild `DiagnosticPolicy`
inside extraction under a different name, moving the duplication rather than removing
it. The only choice offered is fatal-or-not.

`AbortOn.BLOCKED_MEMBER` re-raises the underlying `FilterRejectionError`, matching
`OnError.STOP`'s propagate-the-original behaviour. `NAME_COLLISION` and
`NAME_SANITIZED` raise new `NameCollisionError` / `NameRewrittenError`
(`ExtractionError` subclasses), since neither has an underlying exception.

`MEMBER_FAILED` is deliberately absent: `OnError.STOP` is already the named knob for
"raise on the first failure", and adding a second spelling would be the same
multiplication D4 avoids.

This also converts an accident into a contract. `RAISE` on
`EXTRACTION_MEMBER_BLOCKED` already implements abort-on-first-unsafe-member — the
behaviour `OnError`'s own docstring (`extraction_types.py:83`) and
`safe-extraction/spec.md:640-641` both describe as unimplemented, and which
`test_raise_disposition_stops_despite_continue` locks in without naming.

### D5 — Presets over a `subject` field

All three reviewers rejected adding a `subject` axis to the frozen public
`Diagnostic`. Reviewer 3's argument for what to do instead is the strongest evidence
in any of the notes: a per-code override map requires reading the whole taxonomy and
re-forming an opinion every release, so the blanket `default=RAISE` is the only strict
mode most callers can express — and under it, speculatively passing `password=` to
every call (ordinary in a pipeline that sees a mix of encrypted and plain archives)
raises on every unencrypted archive.

Presets put the distinction in library-maintained, versioned data instead of a frozen
field, keep boundary events from forcing a binary verdict, and are a one-way door in
the safe direction: a field can later be derived from the sets, but a shipped field
cannot be withdrawn.

`EMPTY_ARCHIVE` is excluded from `strict()` deliberately. An empty tar is legitimate —
the `diagnostics` spec goes out of its way to say the library must not treat zero
members as an error — so raising on it under a preset named "strict" would be
surprising. It is in `pedantic()` only.

### D6 — Taxonomy growth is a documented hazard, not a fixable one

Adding any code makes a previously-working `default=RAISE` start raising. That is
inherent and permanent, independent of how this change classifies anything. The
honest response is to write it down: new codes MAY appear in minor releases, so
`default=RAISE` is not version-stable and the presets — whose membership is versioned
alongside the taxonomy — are the documented strict mode.

### D7 — Fold in the `MEMBER_NAME_ENCODING_INFERRED` drift rather than defer it

`DiagnosticCode.MEMBER_NAME_ENCODING_INFERRED` had no row in the `diagnostics` spec's
context table, although the enum member (`diagnostics.py:61`), `NameEncodingContext`
(`:116`), the kind-map entry (`:359`) and the emission site
(`internal/backends/zip_reader.py:695`) all ship.

Surfacing alone stopped being sufficient once this change named the code in
`ARCHIVE_INTEGRITY_CODES`: the preset and the same capability's "closed" table would
have contradicted each other on the day they landed, recreating in miniature exactly
the rule-versus-taxonomy debt this change exists to pay off.

The row is therefore added here. `AGENTS.md`'s pause-and-ask rule guards against
silently choosing between competing designs; there is no competition here — the
implementation is complete and consistent, and the spec simply omitted a row. The
discrepancy was disclosed to the maintainer and the fix was chosen deliberately, which
is what the rule asks for. Adding the row records shipped reality.

Rejected: excluding the code from the preset (ships a knowingly incomplete `strict()`,
when an inferred name encoding is precisely an archive-integrity fact); a separate
prerequisite change (correct sequencing, but a round trip for one table row).
