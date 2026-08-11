# Reviewer 3 opinion — Response: is the diagnostics system over-engineered, and what does real usage say?

*Written by Claude Fable 5 in a plain chat, with no Claude Code environment and
therefore no repository access — reasoning from the circulated brief alone. Body added
as received, apart from this header.*

*The prompt, verbatim:*

> please help me with this decision. is this whole diagnostics thing over engineered?
> how will it be used in the real world, and how does that shape how it should change?
> please write your answer as a doc I can download

*Read the note against that prompt, because it explains the shape. "Is this
over-engineered?" is what the note's opening verdict answers, in those words. "How
will it be used in the real world" is what produced
[the user profiles](#who-will-actually-use-this) — the section that most changed the
final outcome. That reasoning was **solicited, not volunteered**: this reviewer was
asked the question the other two were not. The argument stands on its own merits — the
speculative-`password=` failure case and the blanket-`RAISE` instability point are true
regardless of who raised them — but it should not be read as this model independently
spotting a framing the others missed.*

*The unprompted contributions are the job-versus-stream placement clause and the
presets-over-a-field proposal, neither of which the prompt asks for.*

*Also worth knowing: this is the only one of the three notes whose author could not
check a claim against the code, and both of its proposed relocations (`SUPERSEDED` for
a `REPLACE` collision, `requested_path != path` for a portability rewrite) turned out
to target signals that are already occupied.*

A response to *Diagnostics: are we conflating "something is odd about this archive" with "something is odd about your call"?* (2026-08-10, `main` @ `9b170c0`).

## Verdict up front

The diagnostics system is not over-engineered as a whole. The core — stable codes, typed context, bounded retention, logs as a projection of data — is the library's actual differentiator and is correctly sized for the domain, because "reading untrusted archives" is precisely the domain where a warning nobody sees is a security bug deferred. What *is* over-engineered is one region of it: extraction reporting, where a structured per-member report and the diagnostics channel carry overlapping facts with no stated authority. And the debate the document frames — archive vs. usage — is largely the wrong axis. Real-world usage suggests a different cut, and once you make it, six of the eight contested codes resolve almost mechanically.

## Who will actually use this

There is no released version and no users, so this has to be reasoned from how comparable libraries get used. Three profiles cover essentially everyone.

**The casual caller (roughly 95% of installs).** Opens an archive, iterates, extracts, closes. Will never construct a `DiagnosticPolicy`, never read `reader.diagnostics`, never learn a code name. For this user the entire system is invisible, and its only job is to have sane defaults and to not slow anything down. It currently meets that bar. Nothing about this user argues for changing anything, and nothing about the 22-code taxonomy costs them anything — count of codes is not a complexity tax on people who never see them.

**The pipeline operator.** An ingest service, mail scanner, or upload handler processing untrusted archives at volume. This is the user the system was built for, and their realistic interaction is coarse, not fine. They will do one of three things: run defaults and check `report.results`; treat "any diagnostics at all?" as a quarantine flag; or set a blanket `RAISE` default as strict mode. What they will almost never do is curate a per-code override dictionary across 22 codes — that requires reading the whole taxonomy, forming an opinion on each entry, and re-forming it every release. Per-code overrides are the right escape hatch to have, but they are the 1% path, not the intended interface.

**The forensics or tooling author.** Wants everything, machine-readable, the more codes the better. This user is served by the current design and would be actively harmed by option B — for them, `EXTRACTION_NAME_COLLISION` under `REPLACE` is exactly the kind of fact they exist to catch.

## What this implies: the conflation bites through blanket RAISE, not through taxonomy aesthetics

The document's sharpest practical complaint — "`RAISE` means two unrelated things" — is real, but it is worth being precise about *where* it hurts, because that determines the fix.

A per-code `RAISE` override is never ambiguous; the caller named the code. The ambiguity only exists for the blanket `RAISE` default. And the blanket default is exactly what the pipeline operator will reach for, because it is the only policy expressible without reading the taxonomy. Now consider a completely ordinary pattern in that world: passing `password=` speculatively to every `open_archive()` call because some fraction of incoming archives are encrypted. Under a blanket `RAISE`, every unencrypted archive now raises `PASSWORD_ARGUMENT_UNUSED`. That single interaction makes strict mode unusable for the one audience it was designed for — not because the code is illegitimate, but because the only coarse switch sweeps it in.

There is a second, quieter version of the same problem: taxonomy growth. Adding a code is documented in Part 6 as not purely additive, because a blanket-`RAISE` caller starts raising on an event their working program never produced before. That means blanket `RAISE` over an open-ended default set is *inherently* unstable across versions, forever, independent of how the eight contested codes are classified. So the real design question is not "which codes belong" but "what should the coarse switches quantify over."

The lightest fix that fully answers this is not option C's new public field. It is named policy presets: something like `DiagnosticPolicy.strict()` raising on the archive-integrity set, and `DiagnosticPolicy.pedantic()` raising on everything including call-hygiene events. The archive/request distinction then lives as a curated set inside the policy — data, versioned with the library, adjustable per release — rather than as a frozen field on a public dataclass that makes every future code a two-part classification decision and forces a verdict on genuinely boundary events like the unused password. Presets give the pipeline operator the two switches question 2 asks for, keep the casual caller untouched, and leave per-code overrides as the forensics-grade escape hatch. If a machine-readable axis ever proves necessary, it can be added later; a preset cannot be un-shipped into a field, but a field can always be derived from the sets.

## Extraction is the genuinely over-engineered part, and the fix is structural, not classificatory

Extraction differs from everything else the library does in one way that matters: it is a *job that returns a report*. Opening and reading an archive is a stream of observations with no natural return-value home, which is what the diagnostics channel is for. Extraction already has a home — `ExtractionReport.results`, one typed result per member, with status, error, and paths. Routing extraction facts through diagnostics as well is the one place the system reports the same event twice with no stated authority, and it is the source of all four hardest codes in the document.

The resolution to question 3 should therefore be structural: **`report.results` is authoritative for extraction outcomes; the diagnostics channel stops carrying per-member extraction facts.** Concretely:

`EXTRACTION_MEMBER_FAILED` and `EXTRACTION_MEMBER_BLOCKED` are deleted, with their residual value relocated. The `failure_group_id` for hardlink fan-out moves onto `ExtractionResult`, where it arguably belonged anyway. And the accidental feature the document surfaces — `RAISE` on `_BLOCKED` being the only way to abort on the first unsafe member — is promoted to the real, named knob its own docstring promises (question 6: yes). This is not optional cleanup: a pipeline operator extracting untrusted input genuinely wants "abort on first unsafe member," and will not discover it as an emergent property of a policy override on a code they have never heard of. An undocumented safety behavior reachable only by accident is the opposite of the project's data-over-surprises commitment.

`EXTRACTION_NAME_COLLISION` and `EXTRACTION_NAME_SANITIZED` — the "no home" pair — do have a home once `results` is authoritative: the result type itself. The report already distinguishes `requested_path` from the final path, and already has a `SUPERSEDED` status in its enum. A `REPLACE` overwrite should produce a result for the superseded member rather than silently folding it into a single `EXTRACTED` entry; a sanitized name should be visible as `requested_path != path` exactly as `RENAME` already is. That closes the silent-overwrite hole *better* than the diagnostic did, because the fact lands in the channel the caller is already reading, adjacent to the member it concerns, instead of in a parallel list they must join by hand. The document's claim that removal "is not a deletion but a redesign of the extraction result type" is correct — and that redesign is small, pre-tag, and the right change regardless of what happens to the taxonomy.

## The four boundary codes should stay

`ENCODING_ARGUMENT_UNUSED`, `PASSWORD_ARGUMENT_UNUSED`, `EXPLICIT_FORMAT_LISTED_EMPTY`, and `STREAM_REWIND_REDECOMPRESSES` all survive the real-world test, because each reports something the caller could not have known when they made the call — the format is usually detected, not declared, so "your argument met this archive and evaporated" is a fact about the meeting, exactly as Part 4 argues. The rewind tripwire in particular is the kind of thing that catches an accidental quadratic seek loop in production before it becomes an incident, and its 2026-08-09 rebuild onto measured cost is what makes it a tripwire rather than a nag; deleting it (question 5) would remove real protective value to satisfy a classification. Their only real-world liability is the blanket-`RAISE` sweep, and presets fix that without touching them.

## Write the rule — but write two clauses, not one

Option A's drafted admission rule (reportable = not determinable from the declared contract, and actionable) matches the test that has actually been applied three times and should be written down regardless; it is prose, free, and forecloses nothing. But the extraction episode shows it is incomplete on its own — all four extraction codes *pass* it and were still wrong, for a reason it does not capture. The missing second clause is the job-vs-stream rule: **when an operation returns a structured per-item report, that report is the authoritative and sole carrier of per-item outcomes; diagnostics carry only facts with no return-value home.** With both clauses written, the next proposal has a real test, the two-channel problem cannot recur, and "no advisory shall be log-only" keeps its floor while finally having a ceiling.

## Direct answers to the six questions

**Q1** — No, archive-vs-usage is not the right cut. The knowability cut is right for admission; the job-vs-stream cut is right for *placement*. Together they admit 18 of the 22 codes and relocate 4, which is a more precise outcome than either the 22 or the 14 the document's two framings produce. **Q2** — Yes, but via curated presets rather than a `subject` field; the distinction should be library-maintained data, not a frozen public attribute forcing a binary verdict on boundary events. **Q3** — `report.results`, and the diagnostic channel exits the extraction business entirely. **Q4** — In `ExtractionResult`: `SUPERSEDED` status for the replaced member, `requested_path != path` for sanitization. **Q5** — Yes, keep it; it is the code with the strongest concrete protective argument in the taxonomy. **Q6** — Yes, a named knob; the current situation is an undocumented safety feature, which the project's own values class as a defect.

## Sequencing against the tag

Before `0.2.0`: the extraction-result redesign and the four code deletions, the abort-on-blocked knob, and the presets — all of these touch public surface. Any time: the two-clause admission rule. The document's Part 6 freeze analysis is right, with one addition worth writing into the diagnostics docs now: new codes may be added in minor versions, and callers using a blanket `RAISE` accept that risk — which is one more reason the presets, not the blanket default, should be the documented strict mode.
