## ADDED Requirements

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
| A native Windows path in a message | Separators doubled by the backslash escape — lossless, and the reason print sites render member-derived paths `/`-separated first |

## MODIFIED Requirements

### Requirement: Every ArchiveyError carries standard attributes

The system SHALL ensure every `ArchiveyError` instance carries:

| Attribute | Type | Contract |
| --- | --- | --- |
| `message` | `str` | Human-readable explanation, stored **escaped** for terminal safety |
| `raw_message` | `str` | The same text as the call site wrote it, unescaped; for embedding in a message that will escape it |
| `source_format` | `ArchiveFormat | None` | Format being processed, if known |
| `archive_name` | `str | None` | Path or source stream `name`; `None` for anonymous streams; never fabricated; **raw** |
| `member_name` | `str | None` | Member in context, if any; **raw** |
| `link_target` | `str | None` | Symlink/hardlink target in context, if any; **raw** |
| `__cause__` | `BaseException | None` | Original exception via `raise ... from exc` when wrapping |

#### Scenario: context attribute matrix

| Case | Expected |
| --- | --- |
| `CorruptionError` while reading ZIP member `"data/file.txt"` | `source_format == ArchiveFormat.ZIP`; `member_name == "data/file.txt"` |
| `FormatDetectionError` before any member | `member_name is None` |
| Member named `ev\x1b[2Kil.txt` | `member_name` is the raw name; a message embedding it is escaped |
