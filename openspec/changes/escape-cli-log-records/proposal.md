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

## Accepted residuals

- A message that interpolates an already-escaped string — `{name!r}` (repr escapes
  first) or `{exc}` of another `ArchiveyError` — renders doubly-escaped: `\\x1b`
  where `\x1b` would do. Lossless and safe, but visibly inconsistent with the
  `member=` field on the same line, which renders from the raw attribute. Fixing it
  means auditing call sites one at a time, which is the burden this design exists to
  avoid.
- A native Windows path in an exception message has its separators doubled by the
  backslash escape. Print sites avoid this by rendering member-derived paths relative
  and `/`-separated first; exception messages do not.
