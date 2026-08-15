## Why

Member names are attacker-controlled, and `cli/format.py`'s `escape_member_name`
exists because of it: a name carrying `\x1b[2K\r` lets an archive erase the line it
is being reported on and author what the operator reads in its place. GNU `ls` /
`tar` quote for the same reason.

PR #235 closed every CLI **print site** — the report lines, the error detail appended
to `failed:` / `blocked:`, and the hoist's messages. It did not close the library's
`logging` records, which reach the same stderr through the handler
`cli/logging_config.py` installs. Registered as threat-model **O9**.

The exposure is one field wide and reproducible on the branch that fixed the print
sites:

```
WARNING: Skipping file 'EV\x1b[2KIL\rHARMLESS.TXT': Destination already exists: /…/out/ev<ESC>[2Kil<CR>HARMLESS.txt
failed:  EV\x1b[2KIL\rHARMLESS.TXT: Destination already exists: /…/out/ev\x1b[2Kil\rHARMLESS.txt
```

Same fact, one line apart: the second is the fixed print site, the first is the log
record. In `logger.warning("Skipping %s %r: %s", type, name, error)` the *name* is
safe by accident — `%r` makes `repr` escape it — while the *error* is not, because its
message embeds a destination path built from that same name.

**The first attempt at this change escaped at the CLI's log handler, and that was the
wrong layer.** A formatter only guards records that reach a handler someone configured.
It does nothing for the route an archivey exception is most likely to take to a
terminal: propagating uncaught, with the interpreter printing the traceback — whose
final line is `str(exc)`, and never passes through a handler at all. The same gap
applies to `print(exc)` in embedding code and to third-party error reporters.

The text is dangerous because of where it *comes from*, not where it is displayed, so
that is where it should be made inert.

## What Changes

- **Exceptions escape their own message at construction.** One place in
  `ArchiveyError.__init__` covers all 26 subclasses, and `ArchiveyUsageError` matches
  it. `message`, `args[0]`, `str()` and `repr()` all render the escaped form; the
  structured attributes (`archive_name`, `member_name`, `source_format`) stay raw for
  callers that need the value to act on rather than to print.
- **Diagnostics do the same for `Diagnostic.message`**, which
  `diagnostics_collector` logs verbatim — the one library log site that interpolates
  neither an exception nor `%r`. `Diagnostic.context` stays raw as the structured
  channel.
- **The escaping primitive moves to `archivey/escaping.py`**, below both
  `exceptions` and `cli`, which cannot import from `archivey.cli`.
- **The CLI stops escaping what already escaped itself.** `format_error_detail`
  decides once, by type, so no call site has to know whether it is holding an
  archivey exception or an `OSError`.
- **No escaping formatter.** It would double every backslash an exception already
  wrote, on top of guarding the weaker of the two paths.
- **Write the requirement** into `error-handling` and `diagnostics` (messages are
  inert at construction) and `cli` (what reaches a terminal), so the guarantee has a
  spec to hold it rather than living only in a helper's docstring.
- **Close threat-model O9**, including the `exc_info` residual the handler-side
  design had to accept: the traceback's final line is the exception's message, which
  is now escaped at the source.

Not changed: `escape_member_name`'s behavior, the print sites for member names
(already correct), and the records the library emits — a caller embedding archivey and
routing its logs to a file or a structured sink still receives them verbatim.

## Impact

- `error-handling` — new requirement: exception messages are inert; `message` is
  documented escaped in the standard-attributes table
- `diagnostics` — new requirement: diagnostic messages are inert, context stays raw
- `cli` — new requirement: archive-derived text is escaped before terminal display,
  at the message source rather than the display site
- `src/archivey/escaping.py` — new home for the escaping primitive
- `src/archivey/exceptions.py`, `src/archivey/diagnostics.py` — escape at construction
- `src/archivey/cli/` — `format_error_detail`; no formatter; no double escaping
- `dev-docs/threat-model.md` — O9 open → implemented

## Escape exactly once

Review found the doubling this originally listed as a cosmetic residual was not rare:
**52 message sites** interpolated an archive-derived name with `{name!r}`, which escapes
first and then has its backslashes escaped by the message — essentially every safety
error in `filters.py`, `extraction.py`, `base_reader.py` and `reader_state.py`. So it is
fixed rather than accepted:

- `escaping.quoted()` supplies the delimiting quotes **without** escaping, so the
  message escapes once. All 52 sites converted; `!r` stays where the value is not
  archive-derived (a format, a code, an exception type).
- `raw_message` on both exception roots, and `raw_message_of()`, do the same job for a
  caught exception embedded in a new message. Two sites needed it, both broad
  `except Exception` handlers in `rar_parser.py` that can catch an `ArchiveyError`.
- Two static tests keep both rules true for future call sites — one rejecting `!r` on
  archive-derived text in a self-escaping message, one rejecting `%s` for a name in a
  `logger.*` call, which is the **inverse** rule: log records are not escaped by the CLI,
  so `%r` is what makes a name inert there.

## Escaping correctness

Review of `escape_control_chars` found three bugs, all fixed, and rendering is now
delegated to `repr` (whose escape set is exactly `not str.isprintable()` — verified
across the whole code space) rather than hand-rolled:

- **Astral code points produced a malformed escape.** `\u{code:04x}` emits five hex
  digits above `U+FFFF`, so `\U0001D173` rendered as `ᴗ3`, which reads back as
  `ᴗ` followed by `3`. 955,086 code points were affected.
- **The surrogate range was too wide.** `surrogateescape` only ever produces
  `U+DC80`–`U+DCFF`, but `U+DC00`–`U+DFFF` was reversed into a byte, so 768 code points
  arriving by another route were reported as characters the name never contained.
- **The losslessness claim was false.** `U+009B` and a surrogateescaped byte `0x9B` both
  render `\x9b`. The docstring now claims inertness rather than unique recoverability.

## Names live in structured fields, not message text

An inventory of all 418 exception construction sites found 36 putting a name into the
message, **20 of them printing it twice** — once in the text and once via `__str__`'s
`member=`. A general "constructor appends the name" mechanism does not fit (9 messages
carry two names), but the roles separate cleanly:

- `link_target` joins `member_name` as a structured attribute, rendered as `target=`.
  That covers the six `{name} -> {target}` messages.
- The single-name duplications drop the name from the text entirely.

31 of the 36 are now prose plus attributes, which also makes escaping moot for them —
there is no name left in the message to escape. Four are irreducible and documented in
`tasks.md`: two RAR volume filenames, a portability rewrite (`old -> new`), and two
`ArchiveyUsageError` messages, whose root has no structured attributes.

## Accepted residual

None outstanding. The Windows separator-doubling residual is closed: member-derived
paths in messages are rendered `/`-separated by `display_path()` before escaping, the
same rule the CLI's print sites already followed.
