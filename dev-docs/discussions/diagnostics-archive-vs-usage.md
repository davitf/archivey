# Diagnostics: are we conflating "something is odd about this archive" with "something is odd about your call"?

**Written to be circulated and read standalone.** No prior knowledge of the codebase is
assumed; every fact you need to form an opinion is inline, with file references if you
want to check one. Dated 2026-08-10, against `main` @ `9b170c0`.

> **RESOLVED — 2026-08-11.** This brief did its job and the question is settled. The
> text below is preserved exactly as circulated; it is the record of what was known
> and argued at the time, not a description of current behaviour. **Do not treat any
> statement below as the present contract.**
>
> **What was decided.** The archive-versus-usage cut is rejected as an admission rule
> and retired as doctrine; it survives only as prose describing the taxonomy's shapes.
> It is replaced by two written clauses — an *admission* rule (report only what the
> caller could not determine from the declared contract and can act on) and a
> *placement* rule (when an operation returns a structured per-item report, that report
> is the sole carrier of per-item outcomes). Applying placement, extraction leaves the
> diagnostics channel: the four `EXTRACTION_*` codes go, their facts move onto
> `ExtractionResult`, and a named `abort_on` opt-in preserves the escalation that
> relocation would otherwise have deleted. Named `DiagnosticPolicy` presets replace
> hand-curating overrides across the whole taxonomy.
>
> **Where the answers came from.** All three reviewer opinions
> ([1](reviewer-1-opinion.md), [2](reviewer-2-opinion.md), [3](reviewer-3-opinion.md))
> rejected the archive-vs-usage cut independently, and all three rejected options B, C
> and D. They split on how much of extraction should leave the channel; reviewer 3's
> job-versus-stream placement clause resolved it, and reviewer 1's pass against the
> tree established that two proposed relocations targeted already-occupied signals.
>
> **Two claims below were checked against the code and did not survive.** `SUPERSEDED`
> is not available for a `REPLACE` collision — it means a non-current duplicate decided
> at listing time. And `requested_path != path` is already the defined `RENAME` marker,
> so it cannot also carry a portability rewrite. Both relocations use new signals
> instead. See the addendum in [reviewer 2's opinion](reviewer-2-opinion.md).
>
> **Where it went.** OpenSpec change `extraction-results-authoritative` (PR #234).
> The six questions at the end are answered there, not here.

**How this document was meant to be read.** The concrete questions are at the end.
Disagreeing with the framing was an explicitly useful answer — the archive-vs-usage cut
was itself one of the things in question, and it is the thing that lost. If you only
read one section, read [The conflation](#the-conflation) and then
[The options](#the-options).

---

## 30-second background

Archivey is a Python library for reading archives — ZIP, TAR, 7z, RAR, ISO, and the
single-file compressors — behind one interface. It is pre-1.0 and has not had a public
release yet; the first one (`0.2.0`) is close, which is why surface questions are being
settled now.

The library has a **diagnostics** system: a structured channel for things it wants to tell
you that are not errors. Twenty-two event types ship today.

The question in this document is whether those twenty-two are actually one kind of thing.

---

## Part 1 — What the diagnostics system is

### What a caller sees

Every advisory event becomes an immutable `Diagnostic` value with a stable machine
`code`, a human `message` (explicitly *not* stable — don't parse it), and a typed,
JSON-safe `context` payload specific to that code.

```python
with archivey.open_archive("photos.zip") as archive:
    for member in archive:
        ...
    for d in archive.diagnostics.retained:
        print(d.code, d.context.to_dict())
# member_name_encoding_inferred {'kind': 'name_encoding', 'archive_name': 'photos.zip', ...}
```

They surface in three places: on the reader (`archive.diagnostics`), attached to the
specific member they concern (`member.diagnostics`), and on the result of a standalone
format detection (`FormatInfo.diagnostics`). An extraction run returns an
`ExtractionReport` carrying both its per-member results **and** a diagnostic summary —
remember that dataclass, it comes back later.

### The policy knob

`DiagnosticPolicy` has a default disposition plus per-code overrides. Three dispositions:

| Disposition | Counted | Retained | Logged at WARNING | Raises |
|---|---|---|---|---|
| `IGNORE` | yes | no | no | no |
| `COLLECT` **(default)** | yes | budget permitting | yes | no |
| `RAISE` | yes | budget permitting | yes | `DiagnosticRaisedError` |

So `RAISE` turns any advisory into a hard failure for callers who want that. This matters
a lot below: it is the only mechanism in the library for "I want to be *stopped* if this
happens," and it only works on things that are diagnostics.

Retention is bounded (`max_retained_diagnostic_references`, default 256) but *counts* stay
exact, so a pathological archive cannot exhaust memory through this channel.

### The relationship to logging

Diagnostics are the source of truth; WARNING log lines are a projection of them. The spec
(`openspec/specs/logging/spec.md`) states the direction explicitly: warning logs are
ordered projections of diagnostics, never an independent channel.

---

## Part 2 — Why it exists: data over logs

This is a founding commitment, from `VISION.md`:

> Behavior differences between formats are surfaced as **data** (explicit fields, `None`,
> documented sentinels) — never silent guesses.
>
> Anything the library can only *warn* about should ideally also be **queryable as data** —
> a logging warning most applications never see is a surprise deferred, not avoided.

That last clause is the whole argument. A library that calls `logger.warning("filename had
a weird encoding")` has technically told you, and in practice has told nobody: most
applications configure no handler on that logger, and the ones that do are drowning in
lines from everything else. The information exists and is unreachable at the point where a
program could act on it.

The spec turned that into a rule with teeth (`openspec/specs/diagnostics/spec.md`):

> **No advisory SHALL be log-only.** Every condition the library reports as advice to the
> caller SHALL be emitted through the central diagnostic path with a code, so it is
> queryable on `reader.diagnostics` and escalatable by `DiagnosticPolicy`; the WARNING log
> line is the projection of that emission, never a substitute for it.

**Note the shape of that rule: it is a floor, not a ceiling.** It says everything advisory
must become a code. Nothing anywhere says what fails to qualify. "Should X be a
diagnostic?" currently has a documented argument for yes and no documented argument for
no.

---

## Part 3 — The conflation

### The rule that was supposed to be the ceiling

There is exactly one written statement of an admission rule anywhere in the repository,
and it is not in a spec. It lives in a review observation file
(`review/docs/observations.md`, O-23) and reads:

> Diagnostics are archive-related, not usage-related.

It was written to settle a specific proposal — should the library warn when you read
members of a solid archive out of order, which is slow? — and the answer was no: the cost
receipt already told you at open time, so a warning would be restating advice the API
surface already gives.

That reasoning is good. The *rule* wrapped around it has not survived contact with the
taxonomy.

### Eight of the twenty-two break it

| Code | What it fires on | Archive-related? | Age |
|---|---|---|---|
| `ENCODING_ARGUMENT_UNUSED` | you passed `encoding=`, the resolved backend ignores it | no — your argument | new |
| `PASSWORD_ARGUMENT_UNUSED` | you passed `password=`, nothing needed it | no — your argument | new |
| `EXPLICIT_FORMAT_LISTED_EMPTY` | you asserted `format=X`, listing came back empty, detection disagrees | partly — your assertion | new |
| `STREAM_REWIND_REDECOMPRESSES` | *you* seeked backwards and it cost real work | no — your access pattern | pre-existing |
| `EXTRACTION_MEMBER_BLOCKED` | a member was blocked by *your* `ExtractionPolicy` | archive × your config | pre-existing |
| `EXTRACTION_MEMBER_FAILED` | a member failed mid-extraction | mostly — but reported twice, see below | pre-existing |
| `EXTRACTION_NAME_COLLISION` | two members wanted the same destination path | archive × your destination | pre-existing |
| `EXTRACTION_NAME_SANITIZED` | a name was rewritten to be portable under *your* policy | archive × your config | pre-existing |

The other fourteen are unambiguously about the bytes: a normalized member name, an
inferred encoding, a format/extension conflict, a vanished file during a directory scan, a
missing TAR end marker, trailing junk, an invalid timestamp, an unresolvable symlink, an
unverifiable digest, a degraded seek index, an empty archive, and so on.

**The dates matter more than the count.** The taxonomy went from 15 codes to 22 on
2026-08-09, but only three of the eight above are among the new ones. Five were already there
when the archive-vs-usage rule was written — which means the rule did not drift out of
true, it was **contradicted by the taxonomy on the day it was written**. The rewind code
was noticed at the time and waved through as a defensible edge case; the four extraction
codes were not noticed at all.

So this is not a rule with one awkward exception. It is a rule that never described the
library, sitting in a file that other in-flight work uses as source material.

### Why it matters beyond tidiness

Three concrete consequences, in rough order of how much they cost:

**1. `RAISE` means two unrelated things.** A caller who sets a `RAISE` default is saying
"stop me if this archive is not what I think it is." They now also get stopped when *their
own call* was imprecise — an unused `password=`, a backward seek. Those are reasonable
things to want to be stopped by, but they are a different want, and today there is one
switch for both.

**2. Extraction has two parallel channels for the same facts.** `ExtractionReport` carries
`results` — one `ExtractionResult` per member, each with `status` (`EXTRACTED` /
`BLOCKED` / `FAILED` / `NOT_OVERWRITTEN` / `SUPERSEDED`), an `error`, and the intended
`requested_path` — *and* a diagnostic summary that re-reports blocked and failed members
as `EXTRACTION_MEMBER_BLOCKED` / `EXTRACTION_MEMBER_FAILED`. A caller asking "what
happened during extraction?" has two places to look, with no stated rule about which is
authoritative.

**3. The one thing that has ever been rejected was rejected on a clause nobody wrote
down.** The solid-out-of-order-open warning was refused because the cost receipt already
said it. That is an "is this already knowable?" test, not an "is this about the archive?"
test — and it is unwritten, which is why three separate reviews have now had to rederive
it.

---

## Part 4 — Taking the removal proposal seriously

The proposal on the table is: **delete the usage-related codes.** Below is what each one
actually costs, because the answer is not uniform — some of these are near-duplicates of
data the caller already has, and some are the only record of their fact. Counts are
occurrences across `src/`, `tests/`, `openspec/specs/` and `docs/`, as a rough size signal.

### The two that genuinely duplicate a return value

**`EXTRACTION_MEMBER_BLOCKED` and `EXTRACTION_MEMBER_FAILED`.** Every blocked or failed
member already appears in `report.results` with `status` and `error` populated. The
diagnostic adds a `failure_group_id` for the hardlink case (where one failed source
produces N failed link results) and nothing else.

For `_FAILED` that makes removal nearly free: the `OnError` knob (`STOP` by default,
`CONTINUE` to collect) already governs failures, so between it and `results` a caller has
everything.

**`_BLOCKED` is the opposite, and this is the sharpest single fact in this document.**
`OnError` explicitly does *not* govern blocked members — from its own docstring:

> A policy `BLOCKED` outcome … is always recorded and continued, under either value.
> Aborting the whole extraction on the first unsafe member is a separate future opt-in.

That "separate future opt-in" **already exists**, unlabelled: setting `RAISE` on
`EXTRACTION_MEMBER_BLOCKED` is exactly "stop me on the first unsafe member." Verified by
running it — a TAR containing an absolute-path member, extracted with that one override:

```python
pol = DiagnosticPolicy(overrides={
    DiagnosticCode.EXTRACTION_MEMBER_BLOCKED: DiagnosticDisposition.RAISE})
with archivey.open_archive(tar, config=ArchiveyConfig(diagnostic_policy=pol)) as a:
    a.extract_all("out")
# DiagnosticRaisedError: Skipping file '/etc/evil': Absolute path not allowed
```

Without the override the same call returns normally with a `BLOCKED` result. So one of the
codes proposed for deletion is currently the only way to get a behaviour the library's own
docstring describes as not yet implemented — and nobody wrote that down, because it arrived
as a side effect of a code existing rather than as a designed feature.

Footprint: 29 occurrences.

### The two that are the only record of their fact

**`EXTRACTION_NAME_COLLISION`.** Under `OverwritePolicy.REPLACE`, two archive members
claiming the same destination produce **one file and one plain `EXTRACTED` result** — the
return value cannot tell you a member was silently overwritten by a later one. The
diagnostic is the entire audit trail. (Under `RENAME` the result *does* record it, via
`requested_path != path`.) Removing this without replacing it re-introduces a silent
overwrite, which is close to the opposite of the project's stated values.

**`EXTRACTION_NAME_SANITIZED`.** A name rewritten for portability — a trailing dot
stripped, a non-representable byte escaped — extracts successfully, and the on-disk name
differs from the archive name. Same situation: the diagnostic is the record.

Footprint: 13 occurrences — the smallest of the three groups, and the hardest to remove.

### The four where "you couldn't have known" is a real defence

**`ENCODING_ARGUMENT_UNUSED` / `PASSWORD_ARGUMENT_UNUSED`.** These look like pure
usage-nagging until you notice that the format is usually *detected*, not declared. A
caller passing `encoding="cp1252"` to `open_archive()` does not necessarily know they are
about to get a 7z reader that has no concept of a name encoding. The event is "your
argument met this archive and evaporated," which is a fact about the meeting, not about
either party.

**`STREAM_REWIND_REDECOMPRESSES`.** This one was rebuilt on 2026-08-09 to fire on measured
re-decode cost rather than codec identity, precisely so that `RAISE` would work as a
tripwire against accidental quadratic seek loops. Deleting it deletes that tripwire. The
underlying fact — "this stream has no usable random-access index" — is genuinely about the
archive; only the trigger is about the caller.

**`EXPLICIT_FORMAT_LISTED_EMPTY`.** You said `format=TAR`, the listing came back with zero
members, and content detection would have refused those bytes outright. Two of those three
facts are about the archive; only the assertion is yours. This one exists because the
alternative — refusing a wrong `format=` — was considered and rejected: `format=` is an
override, and an override that second-guesses you is not an override.

Footprint: 67 occurrences across the four, the largest group by a wide margin.

### What removal cannot mean

The "no advisory shall be log-only" rule blocks the easy exit. A removed code cannot
become a `logger.warning` — that is the exact failure mode the whole system was built to
prevent. Removal has to mean one of: *the fact moves into a return value or field*, *the
fact becomes an exception*, or *the fact was not worth reporting at all*. Each of the
eight needs one of those three answers, and two of them (the collision and sanitize
records) have no obvious home today.

---

## Part 5 — The options

### A. Keep everything; write the missing ceiling rule

Add a normative admission rule to the diagnostics spec — the drafted wording is:

> A `DiagnosticCode` SHALL report something a caller could not have determined from the
> declared contract and can act on. It MAY describe a property of the archive, or an
> outcome of the caller's request meeting the archive. It SHALL NOT restate advice the API
> surface already carries.

All 22 codes pass this. The rejected solid-open proposal still fails it. **This rejects the
archive-vs-usage cut entirely** and replaces it with a knowability-and-actionability cut.

*For:* nothing ships differently; the test that has actually been used three times becomes
the written test. *Against:* it is prose no tool can check, and it legitimizes the
two-channel extraction reporting rather than fixing it.

### B. Remove the usage-related codes

*For:* one axis, one meaning; `RAISE` goes back to meaning one thing; the extraction double
channel collapses. *Against:* it partially reverts a change set merged on 2026-08-09; it deletes the
seek tripwire; and two of the eight have no replacement home, so it is not a deletion but a
redesign of the extraction result type.

### C. Keep one taxonomy, add an explicit axis

Give `Diagnostic` a `subject` field — `ARCHIVE` vs `REQUEST` (naming is a detail) — and let
`DiagnosticPolicy` resolve by axis as well as by code. Callers filter; both kinds stay
queryable; "stop me if the archive is wrong" and "stop me if my call was sloppy" become two
switches.

*For:* nothing is lost, the distinction becomes machine-readable rather than a doctrine, and
it answers the `RAISE` ambiguity directly. *Against:* one more public field on a frozen
public dataclass, and it makes every future code a two-part decision instead of one. Also,
some events are genuinely on the boundary — an unused `password=` is arguably both.

### D. Two separate surfaces

Split the collector: archive diagnostics stay on `reader.diagnostics`; request-shaped
events go somewhere else entirely.

*For:* the strongest version of the separation. *Against:* the most public surface added, and
callers who want "everything odd that happened" now merge two lists. Probably only worth it
if we think the two kinds have genuinely different lifetimes or budgets.

### E. Case-by-case, using "is this fact already in a return value?"

Delete `EXTRACTION_MEMBER_BLOCKED` / `_FAILED` (duplicated by `ExtractionResult`), keep the
rest, and write down the test that produced that split.

*For:* fixes the concrete duplication with the smallest diff and no new surface. *Against:*
leaves the taxonomy mixed, so the next proposal still has no rule to be measured against —
unless we also do A.

---

## Part 6 — What is actually time-boxed

Worth separating, because "pre-1.0, decide now" is doing a lot of unexamined work in this
kind of discussion.

**Genuinely tag-gated.** The code names and their typed context dataclasses are public
exports. Removing a code after `0.2.0` is a breaking change. *Adding* one is also not
purely additive: a caller running a `RAISE` default starts raising on an event their
working program never saw. So the membership of the taxonomy has real freeze weight, and
options B, C, D and E all want to happen before the tag.

**Not tag-gated.** Option A's rule is prose. It can be written any time, and writing it
does not foreclose doing B/C/D/E later.

---

## Part 7 — Questions

1. **Is archive-vs-usage the right cut at all**, or is the better cut "could the caller
   have known this from the declared contract?" (The second one admits all 22 codes; the
   first one rejects 8.)
2. **Should `RAISE` be able to distinguish the two kinds?** If yes, option C follows almost
   automatically. If no, the two kinds may not need separating at all.
3. **What is authoritative for extraction outcomes** — `report.results` or the diagnostic
   summary? Whatever the answer, the losing channel should probably stop carrying the fact.
4. **For a `REPLACE` overwrite and a portability rewrite, where should the record live** if
   not in a diagnostic? These are the two cases with no home, and they are the ones where
   silence is most clearly wrong.
5. **Is the seek tripwire worth keeping as a diagnostic?** It is the clearest "about your
   access pattern" event in the taxonomy, and also the one with the most concrete argument
   for existing.
6. **Should "abort on the first blocked member" be a real, named knob** rather than an
   emergent property of `RAISE` on one code? If yes, that capability survives any decision
   here, and `EXTRACTION_MEMBER_BLOCKED` becomes freely removable. If no, we should at
   minimum document that the override is how you get it.

---

## Appendix — the full taxonomy

Fourteen archive-shaped, eight request-shaped, as classified in Part 3.

| Code | Fires when |
|---|---|
| `MEMBER_NAME_NORMALIZED` | a stored name was normalized to a different logical name |
| `MEMBER_NAME_ENCODING_INFERRED` | the name encoding was guessed, not declared |
| `MEMBER_NAME_BIDI_CONTROL` | a name contains bidirectional formatting controls |
| `FORMAT_EXTENSION_CONFLICT` | magic bytes disagree with the file extension |
| `EXTENSION_FORMAT_UNCONFIRMED` | format chosen by extension, listing empty, content detection would refuse |
| `EMPTY_ARCHIVE` | a listing completed with zero members |
| `SCAN_DIRECTORY_VANISHED` / `SCAN_ENTRY_VANISHED` | a path disappeared mid-scan (directory source) |
| `ARCHIVE_EOF_MARKER_MISSING` | the end-of-archive marker is absent or short |
| `ARCHIVE_TRAILING_DATA` | non-zero bytes past a complete trailer under strict mode |
| `MEMBER_TIMESTAMP_INVALID` | a stored timestamp cannot be represented |
| `SYMLINK_TARGET_UNAVAILABLE` | a symlink target could not be resolved |
| `DIGEST_UNVERIFIABLE` | a stored digest exists but cannot be checked |
| `SEEK_INDEX_DEGRADED` | a seek index was built but is unusable or partial |
| **`EXPLICIT_FORMAT_LISTED_EMPTY`** | **you asserted a format; listing empty; detection disagrees** |
| **`ENCODING_ARGUMENT_UNUSED`** | **your `encoding=` could not be acted on** |
| **`PASSWORD_ARGUMENT_UNUSED`** | **your `password=` was never needed** |
| **`STREAM_REWIND_REDECOMPRESSES`** | **your backward seek discarded more than the threshold** |
| **`EXTRACTION_MEMBER_BLOCKED`** | **a member was blocked by a safety/policy check** |
| **`EXTRACTION_MEMBER_FAILED`** | **a member failed during extraction** |
| **`EXTRACTION_NAME_COLLISION`** | **two members claimed one destination path** |
| **`EXTRACTION_NAME_SANITIZED`** | **a name was rewritten for portability** |

Definitions live in `src/archivey/diagnostics.py`; the normative contract is
`openspec/specs/diagnostics/spec.md`.
