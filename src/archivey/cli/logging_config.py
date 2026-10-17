"""CLI logging setup (library loggers → stderr), scoped to one invocation."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TextIO

# The library logs this when ``extract_all`` skips a member under CONTINUE. ``extract``
# prints its own ``blocked:`` / ``failed:`` line for every such member from the report.
_MEMBER_SKIP_MESSAGE = "Skipping %s %r: %s"


class _MemberSkipHold(logging.Filter):
    """Hold back the library's member-skip warnings while ``held`` is a list."""

    def __init__(self, handler: logging.Handler) -> None:
        super().__init__()
        self.handler = handler
        self.held: list[logging.LogRecord] | None = None

    def filter(self, record: logging.LogRecord) -> bool:
        if self.held is not None and record.msg == _MEMBER_SKIP_MESSAGE:
            self.held.append(record)
            return False
        return True


# The hold on the handler of the invocation in progress; None outside cli_logging.
_hold: _MemberSkipHold | None = None


@contextmanager
def cli_logging(*, verbose: bool, err: TextIO) -> Iterator[None]:
    """Route the ``archivey`` logger tree to ``err`` for one CLI invocation (D4).

    Default level is WARNING so normalization / diagnostic warnings are visible
    with a real formatter instead of Python's last-resort handler; ``-v`` lowers
    the level to INFO. The handler is installed only for the duration of the
    invocation and ``propagate`` is left untouched: with a handler present the
    last-resort handler never fires, and library records still reach root-level
    handlers (pytest's caplog, an embedding app). Mutating ``propagate`` or
    keeping a process-global handler would leak state into everything else in
    the process — on pytest 8.3 (the supported floor) it silently blinds every
    later caplog-based warning assertion.

    The handler does **not** escape. Archive-derived text is escaped where it becomes
    a message — exceptions escape their own (:mod:`archivey.exceptions`), and log call
    sites interpolate member names with ``%r`` — so escaping again here would double
    every backslash the exception already wrote. It would also be the weaker place to
    do it: a formatter only guards records reaching a handler the CLI installed, and
    an uncaught exception's traceback never passes through one.
    """
    global _hold
    root = logging.getLogger("archivey")
    handler: logging.StreamHandler[TextIO] = logging.StreamHandler(err)
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    hold = _MemberSkipHold(handler)
    handler.addFilter(hold)
    old_level, old_hold = root.level, _hold
    root.addHandler(handler)
    root.setLevel(logging.INFO if verbose else logging.WARNING)
    _hold = hold
    try:
        yield
    finally:
        _hold = old_hold
        root.removeHandler(handler)
        root.setLevel(old_level)
        handler.close()


@contextmanager
def member_skips_reported_elsewhere() -> Iterator[None]:
    """Keep the library's ``Skipping …`` warnings off stderr for a run that reports.

    ``extract`` prints a ``blocked:`` or ``failed:`` line for each skipped member once
    ``extract_all`` returns its report, so the warning would report the member twice.
    When the run raises instead, there is no report: the held warnings are written
    then, before the caller prints why the run stopped. Only the CLI's own handler is
    held back; handlers an embedding application installed still see every record.
    """
    hold = _hold
    if hold is None or hold.held is not None:
        yield
        return
    hold.held = []
    try:
        yield
    except BaseException:
        held, hold.held = hold.held, None
        for record in held:
            hold.handler.handle(record)
        raise
    finally:
        hold.held = None
