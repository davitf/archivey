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
