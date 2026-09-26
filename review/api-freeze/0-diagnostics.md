# 0. Diagnostics: can a new user tell when to look, and what to do?

The maintainer's question, answered first. Method: start at `docs/index.md`, follow the
links a new user would, and note every point where a question could only be answered by
opening `src/` or an OpenSpec spec. Then check the mechanism against the docs on real
archives. Every observation below was reproduced on `main` at `878c75f` with the script
in §Repro.

## The verdict

**No. Today a new user cannot tell, from the docs alone, how diagnostics work or how to
use them.** The docs answer *which* codes exist (nine of the 22, in a table) and say
*prefer `reader.diagnostics` over logs*. They do not say what a diagnostic is relative to
an exception, where the five places a diagnostic can land differ, what a
`DiagnosticSummary` contains, or when a caller should change the policy. The mechanism
itself is sound and consistent; the gap is entirely in explanation. That is the good
outcome: it is fixable with prose, before the tag, without touching a public name.

The one-page explanation the maintainer asked for is §The page below. It is written to
be pasted into `docs/errors-and-diagnostics.md` in place of the current §Diagnostics
opening. If it reads as clear, that is the fix for D0-1.

## The page

### How diagnostics work

Archivey has two ways of telling you something went wrong. An **exception** means the
operation could not give you a correct answer, so it stopped. A **diagnostic** means the
operation finished and its answer is correct, but something on the way is worth knowing:
a name was rewritten to display safely, a password you offered was never needed, a
timestamp in the archive was invalid and is `None`, an archive listed no members. Nothing
you would want a batch job to stop for is a diagnostic by default, and nothing that makes
the result wrong is only a diagnostic.

Each diagnostic is a frozen `Diagnostic` record with a stable `code` (a
`DiagnosticCode`, the thing to match on), a human `message` (not stable; do not parse
it), and a typed `context` with the structured facts, such as `member_name` or
`archive_name`. `to_dict()` on either gives JSON.

**Where a diagnostic lands.** Every diagnostic is recorded once, at the moment it
happens, in one collector that belongs to the reader. You read that record through
whichever view fits what you were doing:

| You call | Where the diagnostics are | What it covers |
| --- | --- | --- |
| `open_archive(...)` and anything on the reader | `reader.diagnostics` | Everything since detection started, cumulative, including any of the rows below |
| `reader.open(member)` / `reader.read(member)` | `stream.diagnostics` on the returned stream | That one member read |
| `reader.members()` / `reader.stream_members()` | `member.diagnostics` on each `ArchiveMember` | The diagnostics about that member (a rewritten name, an invalid timestamp) |
| `reader.members_report()` | `report.diagnostics` | The listing |
| `reader.extract_all(...)` | `report.diagnostics` | **That extraction call only.** Diagnostics from opening the archive are on `reader.diagnostics`, not here |
| `archivey.extract(...)` (one-shot) | `report.diagnostics` | Detection, open and extraction together, because the call opened the reader for you |
| `detect_format(...)` | `FormatInfo.diagnostics` | Detection alone |

Each view is a `DiagnosticSummary`, a snapshot taken when you read the property:
`total_count` and `counts` (per code) are exact; `retained` holds the full records, in
order, up to a budget (`ArchiveyConfig.max_retained_diagnostic_references`, 256 by
default); `dropped_count` says how many records the budget did not keep. The counts are
always right even when the records are not all there.

Two more things happen when a diagnostic is recorded, and both are on by default:
it is **logged** at `WARNING` under the `archivey` logger hierarchy, which is why a
script with `logging.basicConfig()` prints a line and why the `archivey` command prints
`WARNING:` lines; and if you set `ArchiveyConfig(on_diagnostic=...)` your **callback**
is called with the record as it happens. Neither is the source of truth. The summary is.

**What to do about one.** For most programs: nothing. Read the result, and if you care
about a specific condition, check `reader.diagnostics.counts` for its code after the
operation. For a program that must not proceed on an anomalous archive, set a policy:

```python
from archivey import ArchiveyConfig, DiagnosticPolicy

config = ArchiveyConfig(diagnostic_policy=DiagnosticPolicy.strict())
```

A `DiagnosticPolicy` gives every code one of three dispositions. `COLLECT` (the default
for every code) records, logs and calls back. `RAISE` does all of that and then raises
`DiagnosticRaisedError` (an `ArchiveyError`, carrying the `Diagnostic`) from the call
that hit it. `IGNORE` counts the event and does nothing else: no record, no log line,
no callback. `DiagnosticPolicy.strict()` raises on `ARCHIVE_INTEGRITY_CODES`, the codes
that say the archive's own bytes or metadata are anomalous, and leaves the others alone;
its membership is versioned with the code list, so upgrading does not start raising on
new codes. `pedantic()` raises on everything, including codes added later. To adjust
one code, pass `overrides={DiagnosticCode.X: DiagnosticDisposition.RAISE}`; to silence
one code's log line, set it to `IGNORE`.

Two things are deliberately **not** diagnostics. What extraction did to each member
(blocked, renamed, collided, failed) is on `ExtractionReport.results`, one row per
member, and never also a diagnostic; `abort_on=` is the way to be stopped by one of
those. And a password offered to an unencrypted ZIP, 7z or RAR records nothing, because
those formats can use one; only a format with no encryption at all (TAR, ISO, a
directory, a single compressed file) records `PASSWORD_ARGUMENT_UNUSED`.

The full list of codes, one line each, is on `DiagnosticCode` in the API reference.
The context classes (`NameNormalizationContext` and the others) live in
`archivey.diagnostics`; you receive them on `Diagnostic.context` and can match on
`context.kind` or `isinstance`, but never need to construct one.

(End of the page.)

## Findings

Severity is the cost of shipping 0.2.0 without the fix. None of these is a bug in the
mechanism; every one is a gap between what the code does and what a user can learn.

### D0-1 · High · The guide never explains the mechanism

`docs/errors-and-diagnostics.md` §Diagnostics is two sentences ("Structured advisories
are queryable on the reader and on the extraction report... See the `diagnostics`
capability and the API reference") followed by a table of nine codes. The words
*summary*, *counts*, *retained*, *dropped*, *callback* and *logger* do not appear on
any guide page. `DiagnosticSummary`'s docstring, which is what the API page shows, is
one line: "Immutable point-in-time snapshot of diagnostic counts and retained detail."
A reader who wants to know what `retained` is, why `dropped_count` exists, or what
`total_count` counts under `IGNORE` has to read `openspec/specs/diagnostics/spec.md`,
which the site does not publish and which `docs/api.md:57` nevertheless points at
("See the `diagnostics` capability spec").

Where a new user has to leave the docs, in order of the walk:

| Question | Where the docs stop | Where the answer is |
| --- | --- | --- |
| Is this going to raise, or be recorded? | `errors-and-diagnostics.md` lists nine codes; the other 13 are mentioned in passing on other pages or not at all (`MEMBER_NAME_NORMALIZED`, `SCAN_*_VANISHED`, `MEMBER_TIMESTAMP_INVALID`, `SEEK_INDEX_DEGRADED` have zero mentions) | `DiagnosticCode` in `diagnostics.py:55`; each value has no docstring, so the API page shows names only |
| What is in `reader.diagnostics`? | "queryable on the reader" | `DiagnosticSummary`, `diagnostics.py:582`; the spec's retention section |
| Does `extract_all()`'s report include the open? | Not said | `reader.py:216` ("the delta for this extraction call") and a comment at `core.py:857` |
| Why did my script print a `WARNING:` line? | `gotchas.md:141` says "prefer `reader.diagnostics` ... over logs", implying logs are the old way, not that both fire | `open_archive` docstring, `core.py:334` ("Diagnostics also log at WARNING by default") |
| What do `stream.diagnostics` and `member.diagnostics` hold? | Neither appears on any guide page or on `api.md` | `archive_stream.py:218`, `types.py:733` |
| When should I use `strict()`? | The presets are described; no situation is named | Nowhere in prose; the reasoning is in the `ARCHIVE_INTEGRITY_CODES` docstring, which is on the API page |

**Recommendation.** Replace the opening of §Diagnostics in `errors-and-diagnostics.md`
with §The page above, keep the existing code table under it, and give `DiagnosticCode`
members and `DiagnosticSummary` fields one-line docstrings so the API page carries the
same facts. Change `api.md:57` to point at the guide section rather than the spec. This
is the whole of the fix for the maintainer's question; nothing else in this file blocks
the tag.

### D0-2 · Medium · Two reports named `diagnostics` with different scopes

Reproduced (§Repro E3): `archivey.extract("plain.tar", d, password="pw")` returns a
report whose `diagnostics.counts` has `PASSWORD_ARGUMENT_UNUSED: 1`.
`open_archive("plain.tar", password="pw").extract_all(d)` returns a report whose
`diagnostics.counts` is empty; the same event is on `reader.diagnostics`. Both are
`ExtractionReport.diagnostics`, one field, two scopes. The code is consistent and the
reason is good (the one-shot call owns the reader, so its report is the only place the
open-phase events could go). But a caller who starts with `extract()` and later refactors
to a reader, or the reverse, loses or gains events in the same field without any change
to the archive.

**Recommendation.** Document it (the table in §The page does). No shape change: making
`extract_all()` include open-phase events would double-count for a caller who reads
both, and a separate field name for one of the two would be a rename nobody asked for.
A one-line conformance test that pins the two scopes against each other is the durable
guard; none exists today (`tests/test_diagnostics*.py` test each path, not the pair).

### D0-3 · Medium · The logger is a second channel, on by default, and the guide reads as though it is the old one

Every `COLLECT` or `RAISE` diagnostic logs at `WARNING` (`diagnostics_collector.py:270`,
the `logging` spec). A user who has `logging.basicConfig()` in their script sees a
`WARNING archivey.diagnostics: password= was supplied for TAR...` line and nothing in
the guide tells them where it came from or that `reader.diagnostics` already has it. The
CLI prints the same lines, which is right for a CLI. `gotchas.md:141` ("Prefer
`reader.diagnostics` and the extraction report over logs. Advisories are queryable ...
not only in logs") is true but leaves the impression that logging is a legacy path.
Silencing one code's line means `IGNORE`, which also drops its record; silencing all of
them means configuring the `archivey` logger, which the guide never mentions.

**Recommendation.** Keep the behaviour: a library that logs warnings and also records
them is conventional, and the CLI depends on it. Say it in the guide in the words of
§The page ("Two more things happen ... both are on by default") and name the logger
hierarchy once (`archivey`, with `archivey.diagnostics` as the default child) so a user
can set its level.

### D0-4 · Low · `member.diagnostics` and `stream.diagnostics` are public and invisible

`ArchiveMember.diagnostics` (`types.py:733`) is what `archivey list -v` prints
(`cli/format.py:138`); `ArchiveStream.diagnostics` (`archive_stream.py:218`) is the
per-read view the `archive-reading` spec promises. Neither name appears on any guide
page, and `ArchiveStream` renders on `api.md` without that property being called out.
A user who finds a name-normalization event on `reader.diagnostics` has no way to learn
that the same record is attached to the member it is about.

**Recommendation.** The table in §The page covers both. Nothing to change in code.

### D0-5 · Low · `DiagnosticSeverity` is an axis with one value

`DiagnosticSeverity.WARNING` is the only member (`diagnostics.py:85`), and the
docstring says so: the axis exists "so a later informational taxonomy does not require
changing the value shape". At a freeze that is a promise about the future carried as a
public enum with nothing to branch on. It costs nothing to keep and the reasoning is
sound; it is only surprising to a reader who goes looking for `ERROR` or `INFO`.

**Recommendation.** Keep, and say in the `DiagnosticSeverity` docstring (which the API
page shows) that today every diagnostic is `WARNING` and the distinction between "stop"
and "note" is disposition, not severity. Question Q3 asks the maintainer to confirm.

### D0-6 · Low · `IGNORE` still counts, and the guide does not say so

Under `IGNORE`, `total_count` and `counts` still include the event (§Repro, IGNORE
test: `counts={PASSWORD_ARGUMENT_UNUSED: 1}`, `retained=()`). That is the spec's design
("Counts include every emitted event regardless of disposition") and it is the right one:
a count you cannot suppress is what makes "did this happen" answerable under any policy.
The guide describes `IGNORE` nowhere; the open_archive docstring says it "keeps the count
without the log line", which is the whole story in eight words.

**Recommendation.** Those eight words go in the guide (they are in §The page).

### D0-7 · Low · A password on an unencrypted ZIP records nothing

`open_archive("plain.zip", password="pw")` records no diagnostic (§Repro E1b); the
same on `plain.tar` records `PASSWORD_ARGUMENT_UNUSED`. The rule (per *format*, not per
archive) is stated in `opening-and-listing.md:198` ("a format that has no encryption at
all") and in the docstring. It is the right rule for the keyring use case. The surprise
is that a user checking "did my password get used?" on a ZIP gets silence either way.

**Recommendation.** Keep. One sentence in the guide making the per-format rule explicit
is in §The page. The alternative, an "archive had no encrypted member" diagnostic, would
fire on every plaintext ZIP in a keyring batch, which is the case the rule exists to
keep quiet.

### D0-8 · Low · The 17 context classes: keep demoted, say where they are

They are importable from `archivey` and from `archivey.diagnostics` (whose `__all__` has
30 names), out of `archivey.__all__`, and reachable only through `Diagnostic.context`.
Nothing in the guide says so; a user who wants to `match` on the context type finds the
class name in a `repr` and has to guess the import path.

**Recommendation.** Keep the July demotion. One line in the guide (in §The page) naming
`archivey.diagnostics` as their home and `context.kind` as the string to match on.

## What is actually fine

- **One mechanism.** There is exactly one collector per reader, and every view
  (`reader`, `stream`, `member`, both reports, `FormatInfo`) is a range or an
  attachment over it. The July brief's worry that advisory codes, the integrity set,
  per-member reports and the extraction report might be two mechanisms is not borne
  out: the integrity set is a policy preset, and the extraction report's per-member
  rows are deliberately a different channel (`errors-and-diagnostics.md:130`, the spec's
  placement clause). The placement rule ("a fact has one authoritative channel; a return
  value wins") is stated in the guide and held by the code.
- **Codes are the contract, messages are not.** Stated on the API page and in the spec,
  and the escaped-message design means the message is safe to print. Every code has a
  typed context with a `kind` discriminator, and `validate_code_context` refuses a wrong
  pairing at emit time (`diagnostics.py:500`).
- **The policy is frozen, hashable, and composable.** `strict()` is an ordinary policy
  equal to the same one built by hand; `ARCHIVE_INTEGRITY_CODES` is exported so a caller
  can build their own. The reasoning for each of the eight excluded codes is written
  down (`diagnostics.py:434`) and matches the guide.
- **`DiagnosticRaisedError` is an `ArchiveyError`.** So `except ArchiveyError` catches a
  policy escalation the same as a corruption error, and `escalate_as` keeps the typed
  error (`CorruptionError` with `format_unconfirmed=True`) where one already exists.
  That is the right side of the usage-error line: the archive caused it.
- **The CLI's use is the right example.** It prints the log projection, shows member
  diagnostics under `-v`, and never reads the summary structurally. A front end that
  wants more can, and the guide can point at `reader.diagnostics.retained` for it.
- **The retention budget is bounded and the counts are exact past it.** A hostile
  archive cannot make the summary grow without bound; that half of the design belongs in
  `dev-docs/threat-model.md` and is already there in spirit (the `ResourceLimitError`
  register). Not restated here.

## Repro

Run from a directory holding the fixtures the script builds (`plain.tar`, `plain.zip`,
`zeros.tar`, `zeros.gz`; built with `zipfile`/`tarfile`/zero-fill, see the review
session). `[all]` config, `main` at `878c75f`.

```python
import logging, tempfile
logging.basicConfig(level=logging.WARNING, format="LOG %(name)s %(levelname)s: %(message)s")
from archivey import *

# E1: tar + unused password. One record; reader has it, callback saw it, log line printed.
seen = []
cfg = ArchiveyConfig(on_diagnostic=lambda d: seen.append(d.code.name))
with open_archive("plain.tar", password="pw", config=cfg) as r:
    assert dict(r.diagnostics.counts) == {DiagnosticCode.PASSWORD_ARGUMENT_UNUSED: 1}
    assert seen == ["PASSWORD_ARGUMENT_UNUSED"]
    with r.open("a.txt") as s:
        s.read(); assert s.diagnostics.total_count == 0       # per-read view is empty

# E1b: zip + unused password. Nothing recorded (per-format rule).
with open_archive("plain.zip", password="pw") as r:
    r.members(); assert dict(r.diagnostics.counts) == {}

# E3: the two report scopes.
d = tempfile.mkdtemp()
assert extract("plain.tar", d, password="pw").diagnostics.total_count == 1
with open_archive("plain.tar", password="pw") as r:
    assert r.extract_all(d + "/2").diagnostics.total_count == 0
    assert r.diagnostics.total_count == 1

# IGNORE: counted, not retained.
pol = DiagnosticPolicy(overrides={DiagnosticCode.PASSWORD_ARGUMENT_UNUSED: DiagnosticDisposition.IGNORE})
with open_archive("plain.tar", password="pw", config=ArchiveyConfig(diagnostic_policy=pol)) as r:
    assert r.diagnostics.total_count == 1 and r.diagnostics.retained == ()

# strict(): EXTENSION_FORMAT_UNCONFIRMED raises on a zero-filled "z.tar"; EMPTY_ARCHIVE does not.
try:
    with open_archive("zeros.tar", config=ArchiveyConfig(diagnostic_policy=DiagnosticPolicy.strict())) as r:
        r.members()
except DiagnosticRaisedError as e:
    assert e.diagnostic.code is DiagnosticCode.EXTENSION_FORMAT_UNCONFIRMED
```
