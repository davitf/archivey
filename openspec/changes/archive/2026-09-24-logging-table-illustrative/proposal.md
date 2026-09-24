# The logger table is illustrative and names archivey.diagnostics

## Why

The `logging` spec listed four loggers as if the list were complete. The library also
emits on `archivey.streams`, `archivey.integrity` and `archivey.diagnostics`. The
last one is the default logger for a diagnostic's WARNING projection, though most
emit sites name a subsystem logger instead. The maintainer ruled that
nobody needs an exhaustive list of logger names: the table says it is illustrative, and
gains the one row a reader configuring logging is most likely to need.

## What changes

- `logging`: the table under "Logging under the archivey logger hierarchy" is
  introduced as the loggers the library includes, not limited to these.
- `logging`: a row for `archivey.diagnostics`.

No test keeps the table in sync with `archivey.internal.logs`; that module stays the
single place a logger name is written in code, guarded by `tests/test_logs.py`.

## Impact

Spec text only.
