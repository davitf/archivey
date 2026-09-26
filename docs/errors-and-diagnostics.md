# Errors and diagnostics

What gets raised, what gets recorded, and — if an archive turns out to be damaged —
what you can still get out of it.

## The exception tree

Every failure that comes from the archive or its environment derives from
[`ArchiveyError`][archivey.ArchiveyError], so one `except` covers them all:

```python
from archivey import open_archive, ArchiveyError

try:
    with open_archive("maybe.7z") as reader:
        reader.extract_all("out/")
except ArchiveyError as e:
    print("could not process archive:", e)
```

React to specific cases with the subtypes:

| Exception | Raised when |
| --- | --- |
| [`OpenError`][archivey.OpenError] | the source can't be opened — `FormatDetectionError` (unknown format), `UnsupportedFormatError` (the format is known and no backend can open it, usually a missing package), `StreamNotSeekableError` (a pipe, where the format or the access mode needs seek) |
| [`ReadError`][archivey.ReadError] | the archive opened but a member could not be read; the parent of the next three rows, and the thing to catch when you do not care which |
| [`EncryptionError`][archivey.EncryptionError] | a password is required, missing, or wrong; for a ZipCrypto member, also when its data fails its integrity check after the password passed the format's one-byte check, which a damaged member can cause too (see [Gotchas](gotchas.md)) |
| [`CorruptionError`][archivey.CorruptionError] / [`TruncatedError`][archivey.TruncatedError] | the archive is malformed or cut short |
| [`LinkTargetNotFoundError`][archivey.LinkTargetNotFoundError] | a symlink or hardlink member points at a target the archive does not contain |
| [`PackageNotInstalledError`][archivey.PackageNotInstalledError] | an optional package or tool is absent, or RARLAB `unrar`/`rar` is older than 6.0 (see [Install](install.md#getting-rarlab-unrar-or-rar)) |
| [`ExtractionError`][archivey.ExtractionError] | writing a member to disk failed; the parent of the next two rows |
| [`FilterRejectionError`][archivey.FilterRejectionError] | extraction blocked an unsafe member — `PathTraversalError`, `SymlinkEscapeError`, `SpecialFileError`, `UnportableNameError` (a name the destination OS cannot store safely, such as a Windows-reserved name), `DeceptiveNameError` (a name built to display as something it is not, such as a bidi override) |
| [`NameCollisionError`][archivey.NameCollisionError] / [`NameRewrittenError`][archivey.NameRewrittenError] | raised only when you opted in with `abort_on` (see [Safe extraction](extracting.md)); without it, a collision or a portable-name rewrite is recorded in the result, not raised |
| [`UnsupportedFeatureError`][archivey.UnsupportedFeatureError] | the format is handled but this archive uses a variant or codec the backend cannot decode (BCJ2, or PPMd without its package); unlike `UnsupportedFormatError` the archive opened, and unlike `UnsupportedOperationError` the problem is the archive, not the call |
| [`UnsupportedOperationError`][archivey.UnsupportedOperationError] | the call is not valid for this archive, backend or access mode — `members()` on a streaming reader, `seek()` where the format cannot |
| [`DiagnosticRaisedError`][archivey.DiagnosticRaisedError] | a diagnostic whose disposition you set to `RAISE` fired; carries the `Diagnostic` (see [Diagnostics](#diagnostics)) |
| [`ResourceLimitError`][archivey.ResourceLimitError] | a listing, extraction, or decoder safety limit was exceeded — member count and metadata bytes when a list is materialized (and, for RAR, member count and compressed RAR 1.5/2.x comment bytes at open), total bytes and ratio during extraction, the working memory an archive's own header asks a codec for, checked when the member is opened, or the total password-hashing rounds an encrypted archive asks for, checked before each key is derived |

Mistakes in **your** code are deliberately kept out of that hierarchy: opening a second
overlapping stream without `concurrent_members=True`, using a closed reader, and similar
misuse raise [`ArchiveyUsageError`][archivey.ArchiveyUsageError] (e.g.
`ConcurrentAccessError`), which is **not** an `ArchiveyError` — so a blanket
`except ArchiveyError` never silently swallows a bug. (When an *archive* genuinely can't
provide an operation — seeking a non-seekable member, a format that can't list — that is a
real `ArchiveyError`: `UnsupportedOperationError`.)

Every archivey exception can be pickled and copied with its message and attributes
intact, so one raised in a `ProcessPoolExecutor` or `multiprocessing` worker arrives in
the parent as the same type. As with any Python exception, its `__cause__` and
`__context__` do not travel with it.

Messages are safe to print. Control characters in an exception's message or a
`Diagnostic.message` are backslash-escaped when the object is built, so printing one, or
an uncaught traceback, cannot move the cursor or rewrite a terminal line. The structured
fields — `member_name`, `archive_name`, `link_target`, a diagnostic's `context` — stay
raw, and so does `raw_message`: escape them with
[`escape_control_chars()`][archivey.terminal.escape_control_chars] before you show them.

The same applies to an argument that is the wrong type or an unusable value — a
`config=` that is not an `ArchiveyConfig`, a `detection_budget=` that is not a
`DetectionBudget`, an `encoding=` naming a codec Python does not have, a
`members=` holding something that is neither a name nor an `ArchiveMember`.
Each is refused as `ArchiveyUsageError` at the call that made it, rather than failing
somewhere further in. The exceptions are the source and destination arguments, where a
wrong type raises `TypeError` as it would anywhere else in Python, and looking up a
member name that is not in the archive, which raises `KeyError` like a mapping.

`ArchiveyConfig`, `ExtractionLimits` and `ListingLimits` check their own fields when you
construct them, for the same reason: a limit is a promise about an operation that has not
started yet, so the constructor is the last place a message can still name what you wrote.
That also covers the values that would quietly switch a guard off — `None` on
`ratio_activation_threshold`, which is not optional, and a NaN or an infinity on
`max_ratio`, neither of which any ratio ever exceeds. Pass `None` on a field that allows
it to disable that guard on purpose.

### What is translated, and what passes through

The libraries archivey decodes with — `zipfile`, `tarfile`, `lzma`, `pycdlib`, `unrar`
and the others — raise their own exceptions. Archivey translates the ones it recognises
into the tree above and keeps the original as `__cause__`, so the traceback still shows
what the library said. It never converts *every* exception, and that shapes your
`except` clauses:

- **`OSError`, `KeyboardInterrupt` and `MemoryError` pass through as themselves.** A
  disk error, a permission error or a failing stream you passed in is not reported as
  `CorruptionError` or `TruncatedError`, so handle `OSError` beside `ArchiveyError` if
  your program cares about both. `seek()` on a stream that cannot seek raises
  `io.UnsupportedOperation`, which is also an `OSError`.
- **Under `OnError.CONTINUE`, a filesystem error while writing one member does not
  propagate.** It is recorded as that member's failure, in `ExtractionResult.error`, and
  extraction moves on. Under `OnError.STOP` it propagates as itself.
- **Any other type raised from inside archivey is a bug.** An `IndexError` or
  `struct.error` from a read means a library raised something archivey does not
  recognise yet. It is let through unchanged instead of being given a guessed type.
  [Report it](https://github.com/davitf/archivey/issues) rather than catching it.

## Diagnostics

### How diagnostics work

Archivey has two ways of telling you something went wrong. An **exception** means the
operation could not give you a correct answer, so it stopped. A **diagnostic** means the
operation finished and its answer is correct, but something on the way is worth knowing:
a name was rewritten to display safely, a password you offered was never needed, a
timestamp in the archive was invalid and is `None`, an archive listed no members. Nothing
you would want a batch job to stop for is a diagnostic by default, and nothing that makes
the result wrong is only a diagnostic.

Each diagnostic is a frozen [`Diagnostic`][archivey.Diagnostic] record with a stable
`code` (a [`DiagnosticCode`][archivey.DiagnosticCode], the thing to match on), a human
`message` (not stable; do not parse it), and a typed `context` with the structured facts,
such as `member_name` or `archive_name`. `to_dict()` on either gives JSON.

**Where a diagnostic lands.** Every diagnostic is recorded once, at the moment it
happens, in one collector that belongs to the reader. You read that record through
whichever view fits what you were doing:

| You call | Where the diagnostics are | What it covers |
| --- | --- | --- |
| `open_archive(...)` and anything on the reader | `reader.diagnostics` | Everything since detection started, cumulative, including any of the rows below |
| `reader.open(member)` | `stream.diagnostics` on the returned stream | That one member read |
| `reader.read(member)` | `reader.diagnostics` | `read()` opens and closes the stream inside the call, so there is no stream to ask |
| `reader.members()` / `reader.stream_members()` | `member.diagnostics` on each `ArchiveMember` | The diagnostics about that member (a rewritten name, an invalid timestamp) |
| `reader.members_report()` | `report.diagnostics` | The listing |
| `reader.extract_all(...)` | `report.diagnostics` | **That extraction call only.** Diagnostics from opening the archive are on `reader.diagnostics`, not here, and a second call gets a fresh window |
| `archivey.extract(...)` (one-shot) | `report.diagnostics` | Detection, open and extraction together, because the call opened the reader for you and there is no reader to ask |
| `detect_format(...)` | `FormatInfo.diagnostics` | Detection alone |

Each view is a [`DiagnosticSummary`][archivey.DiagnosticSummary], a snapshot taken when
you read the property: `total_count` and `counts` (per code) are exact; `retained` holds
the full records, in order, up to a budget
(`ArchiveyConfig.max_retained_diagnostic_references`, 256 by default); `dropped_count`
says how many records the budget did not keep. The counts are always right even when the
records are not all there.

Two more things happen when a diagnostic is recorded, and both are on by default: it is
**logged** at `WARNING` under the `archivey` logger hierarchy, which is why a script with
`logging.basicConfig()` prints a line and why the `archivey` command prints `WARNING:`
lines; and if you set `ArchiveyConfig(on_diagnostic=...)` your **callback** is called
with the record as it happens. Neither is the source of truth. The summary is.

**What to do about one.** For most programs: nothing. Read the result, and if you care
about a specific condition, check `reader.diagnostics.counts` for its code after the
operation. For a program that must not proceed on an anomalous archive, set a policy:

```python
from archivey import ArchiveyConfig, DiagnosticPolicy

config = ArchiveyConfig(diagnostic_policy=DiagnosticPolicy.strict())
```

A [`DiagnosticPolicy`][archivey.DiagnosticPolicy] gives every code one of three
dispositions. `COLLECT` (the default for every code) records, logs and calls back.
`RAISE` does all of that and then raises
[`DiagnosticRaisedError`][archivey.DiagnosticRaisedError] (an `ArchiveyError`, carrying
the `Diagnostic`) from the call that hit it. `IGNORE` counts the event and does nothing
else: no record, no log line, no callback. Whether a condition stops you or is only noted
is decided by the disposition; every diagnostic is a warning, there is no severity
axis. To adjust one code, pass `overrides={DiagnosticCode.X: DiagnosticDisposition.RAISE}`;
to silence one code's log line, set it to `IGNORE`. The [named presets](#named-policy-presets)
below cover the common cases.

Two things a reader might expect here are deliberately not diagnostics: what extraction
did to each member, which lives on `ExtractionReport.results` (see
[what is not here](#what-is-not-here-per-member-extraction-outcomes) below), and a
password offered to a format that can use one (see the `PASSWORD_ARGUMENT_UNUSED` row
in the next table).

The full list of codes, one line each, is on [`DiagnosticCode`][archivey.DiagnosticCode]
in the API reference; the ones that need more than a line are in the next table. The
context classes (`NameNormalizationContext` and the others) live in
`archivey.diagnostics`; you receive them on `Diagnostic.context` and can match on
`context.kind` or `isinstance`, but never need to construct one.

### Things that are said with a diagnostic rather than an exception

A handful of conditions are real enough to tell you about and not wrong enough to
refuse. Each has a `DiagnosticCode` you can match on, and any of them can be escalated
to an exception with a `DiagnosticPolicy` if your program would rather stop:

| Code | Means |
| --- | --- |
| `EMPTY_ARCHIVE` | The listing finished, with no error, and there were no members. Not an error: an empty tar is a real thing (`tar cf empty.tar --files-from /dev/null`), and it is byte-identical to a zero-filled junk file of the same size. |
| `EXTENSION_FORMAT_UNCONFIRMED` | The format came from the **filename**, nothing in the bytes confirmed it, and either the listing came back empty or a read failed. The classic shapes are 32 KiB of zeros called `z.tar` (empty listing) and zeros called `backup.gz` (a read error). On a read error the exception also has `format_unconfirmed=True`, as for `PROBE_FORMAT_UNCONFIRMED`. |
| `PROBE_FORMAT_UNCONFIRMED` | A single-file format came from a content probe with nothing corroborating it (no matching extension, no inner-TAR upgrade), and a decode failed (at `open_archive`, which decodes the first byte of a seekable single-file source, or on a later read) or a limit refused the read (`ResourceLimitError`, such as a declared LZMA dictionary over `DecoderLimits.max_decoder_memory`) — at any detection confidence. The matching exception also has `format_unconfirmed=True`. After a decode failure, partial output may already have been produced. |
| `EXPLICIT_FORMAT_LISTED_EMPTY` | You passed `format=`, the listing came back empty, and detection disagrees. `format=` stays an override — wrong extensions are exactly what it is for — so this tells you rather than refusing. |
| `PASSWORD_ARGUMENT_UNUSED` | You passed a password or a password list to a format with no encryption (a `PasswordProvider` callable does not count: it was never asked). Passing a keyring across a batch of mixed archives is the intended use, so it is accepted and simply never consulted. |
| `ENCODING_ARGUMENT_UNUSED` | You passed `encoding=` to a backend that decodes names another way — 7z stores UTF-16, RAR decodes in its own parser, directory and single-file names come from the filesystem. |
| `MEMBER_SELECTOR_UNMATCHED` | An entry in your `members=` collection matched no member, so a typo does not look like an archive that lacks the file. One diagnostic for each such entry, reported once every member has been offered to the selector: by `stream_members()` only when you iterate to the end, and by `extract_all()` before it writes or creates anything when the listing is free, else at the end. A predicate selector is never reported. If you set this code to `RAISE`, `extract_all()` refuses with nothing written only when the listing is free. On TAR and forward-only streams the members before the end are already on disk, and the error replaces the report. |
| `MEMBER_NAME_BIDI_CONTROL` | A member name contains a Unicode bidi formatting control. The context names the exact codepoints, because an *override* (U+202A–202E, U+2066–2069 — how `evil‮gnp.exe` displays as a `.png`) is a different thing from a *directional mark* (U+061C, U+200E, U+200F), which appears in ordinary Arabic and Hebrew filenames. |
| `MEMBER_HEADER_RECORD_SKIPPED` | One optional record in a member's header was malformed and was dropped; the member is listed without whatever it carried. Today this is the RAR5 extra area — a checksum, a timestamp, a redirect target. The field it would have filled is **absent, never wrong**, and the context names the record and the parse failure. Refusing the whole archive over one bad checksum record would discard every member that parsed, and `unrar` itself lists such archives. A member header is attacker-sized, so how many records one member may drop is capped, and a record whose declared *size* cannot be used stops the walk outright — there is no way to find the next record. Either way one diagnostic reports it with `list_truncated` set and names which fault ended the walk, and a member whose header was cut short is reported as encrypted rather than as plaintext, since the walk may have stopped before the record that would have said so. That last part also decides how the member reads: a member archivey reads by slicing the archive (stored, not solid, not split) is sliced only once its bytes have been checked against a checksum that survived the damage — where no checksum survived, or the bytes fail it, the read raises `CorruptionError` naming the header. A cut-short member that needs `unrar` is decoded by it as before, with any surviving checksum checked as the member is read. The archive as a whole is not reported encrypted by one such member. A RAR5 archive's own `CMT` and `QO` service headers carry the same records and are reported the same way, in every volume; they are not members, so those diagnostics carry no member name and the message says what the archive does without — the comment, or the quick-open index. How many of them one archive may report is capped, because nothing lists them and `max_members` therefore never counted them; past the cap one more diagnostic says how many went undescribed. |

#### What is *not* here: per-member extraction outcomes

Extraction is the one operation that returns a structured per-item report, so
[`ExtractionReport.results`][archivey.ExtractionReport] is the **sole** record of what
happened to each member — blocked, failed, collided, renamed, rewritten. None of it is
also a diagnostic. A fact has exactly one authoritative channel, and when a return value
can carry it, the return value wins.

Practically: to find out what extraction did, read `results`, not
`report.diagnostics`. The summary still carries what was observed while *reading* the
archive during extraction (invalid timestamps, unresolvable symlinks, unverifiable
digests, stream rewinds) — those have no per-member result to live on.

Escalation is not lost with the codes: `abort_on` (see
[Safe extraction](extracting.md)) is the named opt-in for being stopped by a blocked
member, a collision, or a name rewrite.

### Named policy presets

Hand-curating a disposition per code means re-reading the whole taxonomy every release.
Two constructors do it for you:

```python
from archivey import ArchiveyConfig, DiagnosticPolicy, ARCHIVE_INTEGRITY_CODES

config = ArchiveyConfig(diagnostic_policy=DiagnosticPolicy.strict())
```

- **`DiagnosticPolicy.strict()`** raises on `ARCHIVE_INTEGRITY_CODES` — the codes that
  report the archive's own bytes or metadata as anomalous — and collects the rest.
- **`DiagnosticPolicy.pedantic()`** raises on everything.

Eight codes are deliberately outside the strict set: `EMPTY_ARCHIVE` (an empty archive
is legitimate), `PASSWORD_ARGUMENT_UNUSED`, `ENCODING_ARGUMENT_UNUSED` and
`MEMBER_SELECTOR_UNMATCHED` (argument hygiene — a pipeline passing a password, or one
list of names, to every call would otherwise raise on every archive they do not fit),
`EXPLICIT_FORMAT_LISTED_EMPTY` (an override that halts you is not an override),
`STREAM_REWIND_REDECOMPRESSES` (your access pattern, not the archive — most useful as a
targeted tripwire), `ENCRYPTED_MEMBER_UNVERIFIED` (it fires when you close an encrypted
member's stream before EOF, or after a seek that gave up its CRC, having read bytes that
no checksum has checked yet; under `strict()` a peek at a ZipCrypto member would raise), and
`PROBE_FORMAT_UNCONFIRMED` (it is emitted while the matching `TruncatedError` or
`CorruptionError` is raised, and that error already carries `format_unconfirmed=True`).
`ARCHIVE_INTEGRITY_CODES` is exported, so you can build your own policy from it.

**New codes may appear in minor releases.** A policy with `default=RAISE` is therefore
not version-stable: an upgrade can start raising on events your working program never
produced. `strict()`, whose membership is versioned alongside the taxonomy, is the
recommended strict mode. Removing a code stays a breaking change.

## When an archive is damaged

The rest of this page is the detail behind "we raise rather than quietly returning
wrong data". Most callers never need it; reach for it when you are recovering data
from archives you do not control.

### Listing a damaged archive

`members()` / `scan_members()` assert a **complete** listing (raise on terminal
archive damage). When you want the recoverable prefix *and* the error together, use
`members_report()`:

```python
with archivey.open_archive("messy.tar") as reader:
    report = reader.members_report()
    for member in report:                    # recovered members (may be a prefix)
        print(member.name)
    if report.error is not None:             # incomplete listing
        raise report.error
```

`__iter__` / `stream_members()` **yield the prefix then raise** on the same failures.
Diagnostics alone are not the primary signal. This is not salvage (resync past damage);
`--salvage` remains reserved. Random-access extract still fail-closes before writing
when listing ends in terminal damage.

### The integrity guarantee

**Read a member to its end and Archivey checks it.** Where the archive stores a
checksum or an authentication tag, a full read verifies it and raises if it does not
match. Stop early and nothing is checked. Errors always come from `read()`, never from
`close()` — so a `finally` block can't mask one.

"To its end" means `read(-1)`, reading until `read()` returns `b""`, or — for a member
with a declared size — reading that many bytes.

What that does and does not promise:

- **We try to raise on every error we can detect** — not on every error. Some formats
  store no checksum at all, and some damage decodes into something that looks
  perfectly valid.
- **`CorruptionError` vs `TruncatedError` is a best-effort guess, not a diagnosis.**
  Damage that happens to decode into a shorter stream is indistinguishable from a
  genuine truncation. Don't branch on which one you got — `except archivey.ReadError`
  catches both.
- **Bytes delivered before the error are of unknown quality.** When a compressed
  member fails mid-stream, some of what you already read is probably fine — but we
  can't tell you which part, or how much. Treat the prefix as unverified: not
  known-good, not known-bad.
- **A full-length return means the checksum matched.** Trust it as far as you trust
  that digest.
- **Once a stream has raised, it stays failed.** Every later `read()` or `seek()` on
  it raises the same error, so seeking back cannot hand you the damaged member as
  clean data. Open the member again if you want to retry.
- **A short return with no exception does not mean "complete".** `read(member.size)`
  on a truncated member hands back what it has and stays quiet. Check the length — or
  just read again, because the *next* read raises.

That last point is what makes the ordinary chunked loop safe: it delivers every byte
that was readable and *then* raises, rather than ending quietly on a short member. So
the recoverable prefix and the error both reach you.

```python
buf = bytearray()
try:
    with reader.open("member.bin") as stream:
        while chunk := stream.read(1 << 20):
            buf.extend(chunk)
except archivey.ReadError:
    ...  # buf holds everything that was readable; the member is damaged
```

If you need certainty regardless of how you read — partial reads, seeks, or "never
hand me unverified bytes" — `VerificationMode.STRICT` verifies a whole member before
returning any of it.

#### What each call does

For a member whose declared size is 500 bytes, truncated after 110:

| Call | Corrupt at full length | Truncated after 110 of 500 |
| --- | --- | --- |
| `read(109)` | not yet at the end — no error | returns 109, no error |
| `read(110)` | not yet at the end — no error | returns 110, no error |
| `read(111)` | not yet at the end — no error | returns 110; the next `read()` raises |
| `read(member.size)` | raises `CorruptionError` | returns 110 short, **no exception** |
| `read(-1)` | raises `CorruptionError` | raises `TruncatedError` |
| chunked until `b""` | raises on the chunk that reaches the size, and withholds it | delivers the prefix, then raises |
| partial read, then `close()` | quiet — you stopped early | quiet — you stopped early |

The one row worth remembering is **`read(member.size)`**: it raises on corruption but
returns a short buffer on truncation. Known-wrong bytes are withheld; an apparently
incomplete prefix is handed over. So a short return from that call is a signal, not a
success — check the length.

Members with no declared size have nothing to read *to*, so `read(n)` can't
self-certify at all. Use `read(-1)` or read until `b""`.
