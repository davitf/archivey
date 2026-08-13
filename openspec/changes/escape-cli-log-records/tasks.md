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

## 4. Close the gap register

- [x] 4.1 Flip threat-model O9 to implemented, naming the formatter and the tests

## 5. Verify

- [x] 5.1 Full suite in all three dependency configurations
- [x] 5.2 `openspec validate --all --strict`
