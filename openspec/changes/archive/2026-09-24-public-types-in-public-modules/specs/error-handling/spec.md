## MODIFIED Requirements

### Requirement: Archive-derived text is escaped exactly once

Escaping composes badly: escaping already-escaped text doubles the backslashes the first
escape wrote, so a hostile name renders as `EV\\x1b[2KIL` where `EV\x1b[2KIL` was meant.
The escape SHALL therefore happen once, at the outermost message, and everything a
message interpolates SHALL be raw when it goes in.

Two helpers exist so that call sites do not each have to reason about it, and using them
is a requirement rather than a style preference:

- A member name, link target or member-derived path SHALL be interpolated with
  `archivey.terminal.quoted()`, which supplies the delimiting quotes **without** escaping. `!r`
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
(`archivey.terminal.display_path()`). Escaping doubles a backslash, so a native Windows path
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
