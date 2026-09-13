# Error Handling

## Purpose

Archivey exposes one archive-error root for archive/environment failures while
keeping caller misuse outside that root. Errors preserve typed context, causes,
and tracebacks so callers can catch broadly, specialize narrowly, and still debug
the original failure.

## Related specs

| Spec | Relationship |
| --- | --- |
| `archive-reading` | Reader lifecycle, member streams, passwords, and close observables |
| `diagnostics` | Diagnostic value, delivery, callback, and retention rules |
| `access-mode-and-cost` | Access-mode legality and stream capability table |
| `reader-concurrency` | Ownership, overlap, worker, and teardown details |
| `compressed-streams` | Codec exception translation and digest verification |
## Requirements

### Requirement: Single rooted archive exception hierarchy

The system SHALL define every library-detected archive/environment failure under
this exact `ArchiveyError` hierarchy:

```text
ArchiveyError(Exception)
├── OpenError
│   ├── FormatDetectionError
│   ├── UnsupportedFormatError
│   └── StreamNotSeekableError
├── ReadError
│   ├── CorruptionError
│   ├── TruncatedError
│   ├── EncryptionError
│   └── LinkTargetNotFoundError
├── WriteError
├── ExtractionError
│   └── FilterRejectionError
│       ├── PathTraversalError
│       ├── SymlinkEscapeError
│       ├── SpecialFileError
│       ├── UnportableNameError
│       └── DeceptiveNameError
├── ResourceLimitError
├── UnsupportedFeatureError
├── PackageNotInstalledError
├── UnsupportedOperationError
└── DiagnosticRaisedError
```

Subclass boundaries SHALL keep their existing meanings:
`UnsupportedFeatureError` / `PackageNotInstalledError` may occur at open or read
time, `StreamNotSeekableError` is an `OpenError`, and
`UnsupportedOperationError` describes an archive/backend/access-mode operation
that cannot be provided, not a caller-code bug. `DiagnosticRaisedError` is direct
because advisory escalation can happen during detection, open, read, stream, or
extraction. `ResourceLimitError` is direct because configurable resource caps can
trip during listing materialization or extraction bomb guarding; it is not an
`ExtractionError` subclass.

| Error split | Meaning |
| --- | --- |
| `UnsupportedOperationError` | Valid API call against a reader/backend/mode that cannot provide the requested operation: random access on `streaming=True`, write through read-only RAR, operation on closed reader. |
| `UnsupportedFeatureError` | Valid archive uses a recognized feature Archivey does not implement: unsupported ZIP method, AES ZIP entry, 7z BCJ2, unknown coder. |
| `ResourceLimitError` | A configured listing or extraction resource limit was exceeded (`ListingLimits` / `ExtractionLimits` bomb guards). |

The three name-related `FilterRejectionError` subclasses are kept apart because a caller
triaging a batch of rejections acts differently on each:

| Error split | Meaning |
| --- | --- |
| `PathTraversalError` | The name tries to reach **outside** the destination, or cannot name a path at all (`..`, absolute, NUL, unencodable). |
| `UnportableNameError` | The name cannot be written **as spelled** on this platform, and the policy declined to rewrite it. |
| `DeceptiveNameError` | The name is writable and stays inside the destination, but is built to **display as something other than what it is** — a bidi override or isolate. Nothing is wrong with the archive or the platform; the name is a lie. |

#### Scenario: archive exception matrix

| Case | Expected |
| --- | --- |
| Any open/read/extract/write failure detected by Archivey | Instance of `ArchiveyError`; `except ArchiveyError` catches it |
| Diagnostic policy escalates | `DiagnosticRaisedError` is caught by `except ArchiveyError` |
| Member name with a bidi override, extracted | `DeceptiveNameError`; caught by `except FilterRejectionError` and by `except ExtractionError` |

### Requirement: Caller misuse remains outside ArchiveyError

The system SHALL define `ArchiveyUsageError(Exception)` outside `ArchiveyError`
for detected caller-code bugs. `except ArchiveyError` MUST NOT swallow misuse.

`ConcurrentAccessError(ArchiveyUsageError)` SHALL be raised when a second member
stream opens while another is live on a reader opened without
`concurrent_members=True`. Its message SHALL include the recorded `open_archive()` call
site (`file:line`) and SHALL name `concurrent_members=True` as the parameter that would
have allowed the operation.

`ArchiveyUsageError` SHALL also cover:

- reader-wide exclusive pass overlap (`__iter__`, `stream_members`,
  `extract_all`, materialization, active worker calls);
- close overlapping an active worker call without declared concurrency;
- any reader operation/property after `close()` except repeated `close()` /
  `__exit__`;
- same-reader password-provider reentry into password-requiring work;
- using an `ArchiveMember` from another reader;
- member I/O after a caller closes its supplied source early;
- `open_archive(streaming=True, concurrent_members=True)`;
- `open()` / `read()` of a resolved non-payload member (`DIRECTORY`, `ANTI`,
  `OTHER`). A symlink/hardlink that fails to resolve remains
  `LinkTargetNotFoundError` (`ArchiveyError`) — that is an archive property,
  not caller misuse. A link that resolves to a non-`FILE` then hits the
  non-payload rule above.

The later operation SHALL fail before changing state and MUST leave the earlier
operation/stream usable. Internal owner-child operations are exempt only through
explicit internal tokens; public reentry does not inherit them. Closed stream I/O
continues to raise `ValueError`, and unsupported stream positioning continues to
raise `io.UnsupportedOperation`.

#### Scenario: usage error matrix

| Case | Expected |
| --- | --- |
| `except ArchiveyError` wraps code that raises `ArchiveyUsageError` | Usage error propagates past the handler |
| Second overlapping member stream without `concurrent_members=True` | `ConcurrentAccessError` with open-site `file:line` naming `concurrent_members=True`; first stream still readable |
| Exclusive pass/materialization is active and conflicting public op begins | Later op raises `ArchiveyUsageError`; active op remains valid |
| Operation/property after `reader.close()` | `ArchiveyUsageError`; already-open member stream follows lifecycle lease |
| Repeated `reader.close()` | No error; no repeated backend teardown |
| Unsupported `seek()` on a stream | `io.UnsupportedOperation`, not archivey-typed |

### Requirement: Close teardown failures preserve state and causes

The system SHALL make explicit reader/member close irrevocably close state before
propagating translated teardown errors. Repeated close MUST NOT retry or re-raise
the teardown. If final member close encounters both an inner-stream close failure
and backend teardown failure, both translated errors SHALL be preserved in an
`ExceptionGroup` after state and leases are released.

#### Scenario: teardown matrix

| Case | Expected |
| --- | --- |
| Explicit close teardown fails | Translated close error propagates after state becomes closed |
| Repeated close after teardown failure | No retry and no repeated error |
| Inner-stream close and backend teardown both fail | `ExceptionGroup` preserves both translated errors |

### Requirement: DiagnosticRaisedError is the typed escalation bridge

The system SHALL expose the escalated immutable diagnostic on a direct
`ArchiveyError` subtype:

```python
class DiagnosticRaisedError(ArchiveyError):
    diagnostic: Diagnostic
```

`source_format`, `archive_name`, and `member_name` SHALL be stamped through the
central context mechanism. Escalation alone has no underlying exception, so
`__cause__` may be `None`; logging/callback exceptions propagate themselves.
`DiagnosticRaisedError` MUST halt extraction even under `OnError.CONTINUE`.

#### Scenario: diagnostic escalation matrix

| Case | Expected |
| --- | --- |
| Code resolves to `RAISE` and delivery succeeds | `DiagnosticRaisedError` carries the exact emitted diagnostic plus stamped context |
| Member diagnostic escalates during `OnError.CONTINUE` | Error propagates immediately; extraction does not record `FAILED`/`BLOCKED` or continue |

### Requirement: Archive EOF strictness takes precedence

For `ARCHIVE_EOF_MARKER_MISSING`, `ArchiveyConfig.strict_archive_eof=True`
SHALL force `TruncatedError` after the diagnostic policy-controlled
count/retention/log/callback steps. This terminal `TruncatedError` SHALL take
precedence over `DiagnosticRaisedError`; with strict EOF disabled, ordinary
diagnostic disposition applies. Logging-handler or callback exceptions still
propagate at their earlier delivery step.

#### Scenario: strict EOF matrix

| Case | Expected |
| --- | --- |
| EOF code resolves to `IGNORE`, `strict_archive_eof=True` | Exact count increments; `TruncatedError` raised without retention/logging/callback delivery |
| EOF code resolves to `RAISE`, delivery succeeds, strict EOF true | Retain/log/callback according to `RAISE`; raise `TruncatedError` instead of `DiagnosticRaisedError` |
| EOF code resolves to `RAISE`, strict EOF false | `DiagnosticRaisedError` after delivery |

### Requirement: Terminal archive listing errors stay loud without hiding members

When a listing pass recovers one or more members and then hits a terminal
archive-level failure (corruption, truncation, or format EOF escalation such as
Option F TAR rejected header / strict missing trailer), the system SHALL surface
**both** the recovered prefix and the typed `ArchiveyError`. It MUST NOT use
diagnostics alone as the primary honesty channel for that failure.

Required surfaces:

| API | Contract |
| --- | --- |
| `members_report()` | Always returns `MemberListReport` with prefix in `members` and the failure in `error` |
| `__iter__` / `stream_members` (either access mode) | Yield every recovered member, then raise the same error |
| `members()` / `scan_members()` | Raise the error; MUST NOT return a partial list |

The system SHALL NOT publish a successful complete member cache for an incomplete
listing. `ResourceLimitError` from listing caps remains raise-only on these APIs
and is outside this damage-oriented requirement.

#### Scenario: partial listing honesty matrix

| Case | Expected |
| --- | --- |
| TAR rejected header after 3 members; `members_report()` | `len(members)==3`, `error` is `CorruptionError` |
| Same archive; RA `__iter__` | Yields 3 members, then raises `CorruptionError` |
| Same archive; `members()` | Raises `CorruptionError`; no list returned |
| Same archive; only `reader.diagnostics` consulted without listing API | Insufficient — callers must use report or catch after iteration |
| `ListingLimits` trip mid-list | `ResourceLimitError` raised; not soft-returned as report `error` |

### Requirement: Every ArchiveyError carries standard attributes

The system SHALL ensure every `ArchiveyError` instance carries:

| Attribute | Type | Contract |
| --- | --- | --- |
| `message` | `str` | Human-readable explanation, stored **escaped** for terminal safety |
| `raw_message` | `str` | The same text as the call site wrote it, unescaped; for embedding in a message that will escape it |
| `source_format` | `ArchiveFormat \| None` | Format being processed, if known |
| `archive_name` | `str \| None` | Path or source stream `name`; `None` for anonymous streams; never fabricated; **raw** |
| `member_name` | `str \| None` | Member in context, if any; **raw** |
| `link_target` | `str \| None` | Symlink/hardlink target in context, if any; **raw** |
| `format_unconfirmed` | `bool` | `True` when the format claim rested on a content probe alone, with nothing corroborating it (`detected_by="content_probe"`, no matching extension and no inner-TAR upgrade) — **whatever confidence detection reported**; default `False` for every other raise |
| `__cause__` | `BaseException \| None` | Original exception via `raise ... from exc` when wrapping |

`__str__` SHALL append `format_unconfirmed=True` when the flag is set (alongside
`format=…`), so a printed exception carries the same signal as the attribute.

#### Scenario: context attribute matrix

| Case | Expected |
| --- | --- |
| `CorruptionError` while reading ZIP member `"data/file.txt"` | `source_format == ArchiveFormat.ZIP`; `member_name == "data/file.txt"`; `format_unconfirmed is False` |
| `FormatDetectionError` before any member | `member_name is None`; `format_unconfirmed is False` |
| Probe-only Brotli read fails as `TruncatedError` | `format_unconfirmed is True`; `str(exc)` contains `format_unconfirmed=True` |
| Probe + `.br` read fails | `format_unconfirmed is False` — extension corroborated |
| Probe-only **LZMA Alone** read fails | `format_unconfirmed is True` — `PROBABLE` confidence, but nothing corroborated it |
| Probe-only Brotli, **compressed-first** (`PROBABLE`), read fails | `format_unconfirmed is True` — confidence is not the test |

### Requirement: Original cause and traceback are preserved centrally

The system SHALL preserve original decoding-library exceptions as `__cause__`
using `raise ... from exc`; libraries MUST NOT swallow the original traceback.
Type translation is per underlying library (for example `zipfile`, `tarfile`,
`lzma`, `unrar`, crypto backend), not per format. The `ArchiveReader` base class
SHALL centrally stamp `source_format`, `archive_name`, and `member_name` on
propagating `ArchiveyError`s; backends do not hand-fill those fields.

No internal library exception SHALL escape unwrapped when it originates from a
decoding library taxonomy. A backend that serves raw bytes through no decoding
library (directory backend, stored member stream) SHALL NOT wrap plain `OSError`;
there is no codec taxonomy to translate.

#### Scenario: translation matrix

| Case | Expected |
| --- | --- |
| `zipfile.BadZipFile` is wrapped as `CorruptionError` | `__cause__` is the original `BadZipFile` |
| `traceback.print_exc()` after catching `ArchiveyError` | Chained output includes original exception and traceback |
| Decoder raises an unexpected taxonomy exception | Re-raised as an `ArchiveyError` subclass with `raise ... from exc` |
| Context-free translator raises while reading 7z member `"data/file.txt"` from `"/tmp/a.7z"` | Base reader stamps `SEVEN_Z`, archive name, and member name |

### Requirement: Genuine runtime and I/O errors are not reclassified

The system SHALL translate only errors from a decoding library's archive/codec
taxonomy. Filesystem `OSError`, dropped caller-supplied streams, `KeyboardInterrupt`,
`MemoryError`, and similar runtime failures SHALL propagate unchanged and MUST NOT
be converted into `CorruptionError`, `TruncatedError`, or another `ArchiveyError`.

#### Scenario: runtime error matrix

| Case | Expected |
| --- | --- |
| Underlying file read raises `OSError` mid-member | Original `OSError` propagates unchanged |
| Source bytes are readable but decoder reports corrupt/truncated data | `CorruptionError` / `TruncatedError` because the failure is decoding-origin |

### Requirement: Exception messages are inert for terminal display

Exception messages interpolate archive-derived text — a member name, or a destination
path built from one — and that text is attacker-controlled. Both exception roots
(`ArchiveyError` and `ArchiveyUsageError`) SHALL escape their `message` at construction,
losslessly, so the stored message carries no control sequence that could rewrite or spoof
a terminal line.

The escaped form SHALL be what `message`, `args[0]`, `str(exc)` and `repr(exc)` all
render, so no accessor hands a caller the raw form by accident.

Escaping SHALL happen at construction rather than at any display site. An exception
message reaches a terminal by routes no single consumer configures — `print(exc)`,
`logging.exception`, a third-party error reporter, and an uncaught exception whose
traceback the interpreter writes itself, whose final line is `str(exc)`. A display-side
escape protects only the routes someone remembered to wire up; the last of these has no
display site to wire.

The structured attributes (`archive_name`, `member_name`, `link_target`,
`source_format`) SHALL remain **raw**, so callers that need the real value to act on —
rather than to print — still have it. `__str__` renders the names through `!r`, which
escapes them for display.

A name that is available as a structured attribute SHALL NOT also be interpolated into
the message text: `__str__` already renders it, so doing both prints the same name twice
in one line. Messages carrying a member name and its link target SHALL pass both as
attributes and keep the message itself prose.

#### Scenario: an exception message cannot spoof a terminal line

| Case | Expected |
| --- | --- |
| `ExtractionError` whose message embeds `/out/ev\x1b[2Kil\rSPOOF.txt` | `str(exc)` and `exc.message` carry `\x1b` as four literal characters; no raw ESC |
| The same exception propagating uncaught | The traceback's final line is escaped; no display site was involved |
| `exc.member_name` for a member named `ev\x1b[2Kil.txt` | The raw name, unescaped — it is data, not display |
| `ArchiveyUsageError` | Escaped on the same terms, though its text is usually archivey's own |
| A message with no control bytes | Unchanged |

### Requirement: Archive-derived text is escaped exactly once

Escaping composes badly: escaping already-escaped text doubles the backslashes the first
escape wrote, so a hostile name renders as `EV\\x1b[2KIL` where `EV\x1b[2KIL` was meant.
The escape SHALL therefore happen once, at the outermost message, and everything a
message interpolates SHALL be raw when it goes in.

Two helpers exist so that call sites do not each have to reason about it, and using them
is a requirement rather than a style preference:

- A member name, link target or member-derived path SHALL be interpolated with
  `escaping.quoted()`, which supplies the delimiting quotes **without** escaping. `!r`
  SHALL NOT be used: it escapes first, and the message escape then escapes the
  backslashes it introduced. `quoted()` SHALL *choose* its delimiter (`"` when the text
  contains `'` and no `"`) rather than escape one, since escaping would reintroduce the
  doubling.
- A caught exception that may be an archivey exception SHALL be interpolated with
  `raw_message_of()`, which yields `raw_message` for archivey exceptions and `str(exc)`
  for any other. A handler catching only third-party types MAY interpolate directly.

`ArchiveyError` and `ArchiveyUsageError` SHALL expose `raw_message` — the text as the
call site wrote it — alongside the escaped `message`, since the escaped form cannot be
embedded in another message without doubling.

A filesystem path interpolated into a message SHALL be rendered `/`-separated first
(`escaping.display_path()`). Escaping doubles a backslash, so a native Windows path
would otherwise have every separator doubled; after this rendering a surviving
backslash is a character in a *name*, which is what the escape is for.

The inverse rule holds for `logger.*` call sites, whose records the CLI does **not**
escape: there `%r` is what makes an interpolated name inert and SHALL be kept.

#### Scenario: escaping composes predictably

| Case | Expected |
| --- | --- |
| A message interpolating a name with `quoted(name)` | Escaped once: `'ev\x1b[2Kil.txt'` |
| A message interpolating a name with `{name!r}` | Doubly-escaped — the form this requirement forbids |
| Wrapping an archivey exception with `raw_message_of(exc)` | Escaped once |
| Wrapping a third-party exception directly | Escaped once — it was never escaped |
| `exc.raw_message` | The unescaped text the call site wrote |
| A log record interpolating a name with `%r` | Inert; the CLI handler does not escape it |
| A native Windows path in a message | Rendered `/`-separated by `display_path()` first, so the escape has no separators to double |

### Requirement: A decode failure on probe-only evidence names its provenance

When a single-file member's format was chosen by a content probe and **nothing else
agreed**, a decoding failure while reading that member SHALL:

1. Keep the same exception **type** (`TruncatedError` / `CorruptionError` as today) — no
   new subclass; callers catching those types must keep working.
2. Set `format_unconfirmed=True` on the exception (see the standard-attributes
   requirement).
3. Rewrite the **message** so it reports that the format identification was unconfirmed,
   rather than presenting as a plain truncation or corruption of a file whose format is
   settled. The message MUST NOT imply that nothing was produced: a read may already have
   delivered a full buffer (65 536 bytes measured) of bytes copied verbatim from the
   source before the decoder errors. The message MUST NOT name a confidence level, which
   is no longer what the stamp keys on.
4. Emit diagnostic `PROBE_FORMAT_UNCONFIRMED` (see `diagnostics`) — a new code, not a
   stretch of `EXTENSION_FORMAT_UNCONFIRMED`.

**The trigger is provenance, not confidence.** The question this signal answers is "was
there any evidence besides one probe?", which `DetectionConfidence` does not track:
confidence grades *how strong* the evidence is. Keying on `GUESS` left 68 of 128 measured
real-world fabrications unstamped — LZMA Alone, which reports `PROBABLE` unconditionally,
and Brotli's compressed-first class, which was moved to `PROBABLE` precisely *because* the
flag was confidence-keyed. Separating them also lets confidence be retuned later without
silently changing which errors are stamped.

A claim SHALL count as **corroborated**, and therefore not stamped, when either of these
holds: the filename agrees with the detected format; or the probe hit was upgraded to a
`TAR_*` format because a TAR header was found in the decompressed prefix, which is a
second independent signal obtained by actually decompressing. Exact magic and SFX scans do
not reach this requirement at all, being different detection paths.

**"The filename agrees" SHALL be the exact negation of what raises a
`FORMAT_EXTENSION_CONFLICT`** — the extension's format equals the detected format, or is
the documented *deferred inner-TAR* case where a `TAR_*` extension stands over a bare
compressor result because the inner-TAR probe could not run. So `foo.tar.br` reported as
bare `BROTLI` corroborates, and the rule generalizes past `.br` to every magic-less codec
(`.lzma`, `.zz`). One predicate SHALL serve both, so the system cannot both warn that a
name conflicts and count it as corroboration.

Agreement SHALL NOT be reduced to the `stream` component alone. Every container format
shares `StreamFormat.UNCOMPRESSED`, so a `stream`-only test makes a `.zip` name corroborate
a `TAR` result. No content probe can produce a container format today — every one is a
`RAW_STREAM` codec — but `ReadBackend.CONTENT_PROBES` exists so a container backend can
register one, and that seam MUST NOT silently arm this.

> **Contested, and scheduled for replacement.** PR #263's design analysis holds that the
> filename must not decide whether a failure is stamped, keying the signal on the winning
> candidate's **content-evidence class** instead: `NAME` ranks below `BOUNDED_PROBE`, so a
> matching extension is retained as evidence but cannot promote the class, and a failure
> whose winning class is still `BOUNDED_PROBE` is stamped whether or not the name agrees
> (§6). Its §9 goes further and drops the `.br`-raises-confidence rule too, so a bounded
> Brotli probe is `GUESS` with or without the extension. It accepts the consequence
> explicitly — a genuinely truncated `x.br` carries the flag — on the grounds that
> `format_unconfirmed` must mean "the bytes did not confirm this identity", not "the
> identity is probably wrong", and requires the winning evidence ledger to be a public
> outcome so a caller can see the `NAME` item and present the error accordingly.
>
> **Scope of that follow-up: two sites, not one.** The filename decides the stamp here via
> `_extension_corroborates`, and in `_brotli_probe_confidence` via the `.br`-to-`PROBABLE`
> rule shipped in #261. They are the same rule expressed twice; removing only the first
> leaves the second contradicting §9. Neither is introduced by this change.
>
> **Sequencing, measured.** This requirement never suppresses a stamp that the previous
> confidence-keyed rule produced: "old stamps, new does not" reduces to `GUESS` **and**
> corroborated, and that pair is unreachable — every extension that corroborates a Brotli
> result has `stream is BROTLI`, which is exactly what makes `_brotli_probe_confidence`
> report `PROBABLE`; the inner-TAR arm forces `PROBABLE`; and the zlib and LZMA Alone
> probes are unconditionally `PROBABLE`. Measured on the residual fixtures, against the
> pre-change tree: extensionless Brotli and extensionless LZMA Alone went from silent to
> stamped, and the `.br` / `.lzma` cases were silent before and after. So this change is a
> strict increase in what is stamped, and reverting it would restore a larger blind spot
> (Alone and zlib never stamped at all) rather than remove the contested rule.

Today an uncorroborated source raises `TruncatedError (member=…, format=BROTLI)` — or
`CorruptionError (format=LZMA_ALONE)` — asserting two things that are not known: that the
bytes are that format, and that the file is truncated or corrupt. For a magic-less format
identified only by decoding a bounded prefix, the likelier explanation is that the file was
never that format.

This SHALL NOT refuse the open: a probe-only identification that reads cleanly is a
success, and an extensionless stream the probe identified correctly must stay readable.
A corroborated result keeps today's type, message, and `format_unconfirmed=False`.

#### Scenario: unconfirmed-format decode failure

| Case | Expected |
| --- | --- |
| Probe-only Brotli result (`GUESS`), decode fails | Same `TruncatedError`/`CorruptionError` type; `format_unconfirmed is True`; message names unconfirmed identification; `PROBE_FORMAT_UNCONFIRMED` diagnostic |
| Probe-only Brotli result, **compressed-first** (`PROBABLE`), decode fails | Same treatment — stamped. Confidence does not gate the signal |
| Probe-only **LZMA Alone** result (`PROBABLE`), decode fails | Same treatment — stamped |
| Probe match corroborated by extension, decode fails | Ordinary truncation/corruption message; `format_unconfirmed is False`; no probe-unconfirmed diagnostic |
| Probe hit upgraded to `TAR_BROTLI` via an inner-TAR header, decode fails | Corroborated: `format_unconfirmed is False` |
| Probe-only result, decode succeeds | Success; no error and no diagnostic |
| Decode fails after bytes were already delivered | Error still raised; message does not claim zero output |
| `DiagnosticPolicy.pedantic()`, probe-only decode fails | Same typed error with `format_unconfirmed=True` — not `DiagnosticRaisedError` |
| Format came from exact magic, decode fails | Untouched — this requirement does not apply |
