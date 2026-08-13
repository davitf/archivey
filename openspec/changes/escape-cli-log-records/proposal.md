## Why

Member names are attacker-controlled, and `cli/format.py`'s `escape_member_name`
exists because of it: a name carrying `\x1b[2K\r` lets an archive erase the line it
is being reported on and author what the operator reads in its place. GNU `ls` /
`tar` quote for the same reason.

PR #235 closed every CLI **print site** — the report lines, the error detail appended
to `failed:` / `blocked:`, and the hoist's messages. It did not close the library's
`logging` records, which reach the same stderr through the handler
`cli/logging_config.py` installs. Registered as threat-model **O9**; this change is
that item being tackled.

The exposure is one field wide and reproducible on the branch that fixed the print
sites:

```
WARNING: Skipping file 'EV\x1b[2KIL\rHARMLESS.TXT': Destination already exists: /…/out/ev<ESC>[2Kil<CR>HARMLESS.txt
failed:  EV\x1b[2KIL\rHARMLESS.TXT: Destination already exists: /…/out/ev\x1b[2Kil\rHARMLESS.txt
```

Same fact, one line apart: the second is the fixed print site, the first is the log
record. In `logger.warning("Skipping %s %r: %s", type, name, error)` the *name* is
safe by accident — `%r` makes `repr` escape it — while the *error* is not, because its
message embeds a destination path built from that same name. Any library log record or
exception message carrying a member-derived path has this shape, so fixing the one
call site would not close the class.

Escaping is also **unspecified**: no requirement in `cli`, `logging`, or anywhere else
says CLI output escapes attacker-controlled text, even though the behavior ships and
carries a security rationale in its docstring. A fix with no requirement behind it is
one refactor away from silently regressing — which is exactly what happened to the
print sites.

## What Changes

- **Escape at the CLI's log handler, not at the library's call sites.** A
  `logging.Formatter` in `cli/logging_config.py` escapes the record's *message*. The
  library keeps emitting structured records unchanged, and the CLI decides how its own
  terminal is protected.
- **Write the requirement** into `cli`: output to a terminal escapes archive-derived
  text, on both the print path and the log path, so the guarantee has a spec to hold it
  rather than living only in a helper's docstring.
- **Close threat-model O9.**

Not changed: `escape_member_name` itself, the print sites (already correct), and the
records the library emits — a caller embedding archivey and routing its logs to a file
or a structured sink is unaffected.

## Impact

- `cli` — new requirement: attacker-controlled text is escaped before terminal display
- `src/archivey/cli/logging_config.py` — the escaping formatter
- `dev-docs/threat-model.md` — O9 open → implemented
