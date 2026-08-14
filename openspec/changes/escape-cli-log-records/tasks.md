## 1. Specify the guarantee

- [x] 1.1 Add the escaping requirement to `openspec/specs/cli/spec.md` (via the delta),
      covering the print path and the log path, so the behavior has a written test
- [x] 1.2 Sync the delta into the main `cli` spec

## 2. Escape the log path

- [x] 2.1 Add the escaping `logging.Formatter` to `cli/logging_config.py`
- [x] 2.2 Escape the record's *message* only, leaving an `exc_info` traceback alone —
      escaping a traceback's newlines would collapse it into one unreadable line
- [x] 2.3 Do not mutate the shared `LogRecord`: other handlers (pytest's `caplog`, an
      embedding app's) format the same record, and `cli_logging`'s docstring is explicit
      that library records must still reach them unchanged

## 3. Tests

- [x] 3.1 Red-green: a member name carrying `\x1b[2K` + `\r` reaches stderr escaped
      through the log path, not only the print path
- [x] 3.2 A record with `exc_info` keeps its traceback readable across lines
- [x] 3.3 The formatter does not alter the record other handlers see
- [x] 3.4 Ordinary messages are unchanged (no separator doubling, no cosmetic churn)

## 4. Review follow-ups (PR #236)

- [x] 4.1 The new SHALL overclaimed: `archivey test`'s `FAIL` detail, the extract abort
      notice, and `main()`'s three top-level handlers printed the exception unescaped.
      Reproduced a live spoof via `x --abort-on name-collision`. Maintainer decision:
      finish the sites rather than narrow the requirement
- [x] 4.2 Drop the false "a traceback is archivey's own text" rationale from the formatter
      docstring and the spec — the final line is the exception's message, which may be the
      archive's. Maintainer decision: record as an accepted residual (no call site passes
      `exc_info`), not escape it
- [x] 4.3 Record the two residuals O9 omitted: `exc_info` exception lines, and native
      Windows paths doubling their separators in log messages
- [x] 4.4 Disambiguate the PR #235 cross-reference rather than removing it

## 5. Close the gap register

- [x] 5.1 Flip threat-model O9 to implemented, naming the formatter and the tests

## 6. Verify

- [x] 6.1 Full suite in all three dependency configurations
- [x] 6.2 `openspec validate --all --strict`

## 7. Rework: escape at the message source, not the display site (PR #236)

Sections 1–6 landed a handler-side formatter. Review established that this guards the
weaker of the two paths — a formatter never sees an uncaught exception's traceback,
which is the likeliest way an archivey message reaches a terminal. Reworked in place
rather than landed and reverted, so the spec history never claims a guarantee we no
longer hold. The unchecked boxes below supersede the checked ones above.

- [x] 7.1 Move the escaping primitive to `archivey/escaping.py`, below both
      `exceptions` and `cli`; `escape_member_name` becomes a thin CLI-facing alias
- [x] 7.2 Escape `message` at construction in `ArchiveyError` and `ArchiveyUsageError`;
      keep `archive_name` / `member_name` / `source_format` raw
- [x] 7.3 Escape `Diagnostic.message` at construction; keep `context` raw as the
      structured channel. Closes the one library log site (`log.warning("%s", message)`)
      that interpolates neither an exception nor `%r`
- [x] 7.4 Delete `_EscapingFormatter` — it would double every backslash an exception
      already wrote — and document in `cli_logging` why the handler does not escape
- [x] 7.5 Add `format_error_detail`, deciding by type in one place, so call sites
      holding an `ArchiveyError | OSError` union do not each have to reason about it
- [x] 7.6 Convert the CLI's exception print sites: `main()`'s handlers, `archivey test`'s
      FAIL lines, the extract abort notice, the `failed:` / `blocked:` detail, the hoist.
      `CliError` is outside the archivey hierarchy and keeps its print-site escape
- [x] 7.7 Rewrite the spec: `error-handling` and `diagnostics` gain the
      escape-at-construction requirements, `cli` is rewritten to escape at the source
- [x] 7.8 Rewrite the tests: the log-path tests now assert the exception and the
      diagnostic escape themselves, and that the CLI handler leaves records alone
- [x] 7.9 Cover the route that motivated the rework — an uncaught `ArchiveyError`'s
      traceback reaching stderr inert, with no display site involved
- [x] 7.10 Record the two residuals this design has: double-escaping when a message
      interpolates `{name!r}` or `{exc}`, and Windows separators doubling in messages
- [x] 7.11 Re-verify: full suite in all three dependency configurations,
      `openspec validate --all --strict`, and the live spoof reproduction
