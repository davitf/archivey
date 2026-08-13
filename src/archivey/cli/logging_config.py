"""CLI logging setup (library loggers → stderr), scoped to one invocation."""

from __future__ import annotations

import copy
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TextIO

from archivey.cli.format import escape_member_name


class _EscapingFormatter(logging.Formatter):
    """A formatter that escapes the record's message for safe terminal display.

    Library log messages interpolate archive-derived text — member names directly, and
    destination paths (built from those names) via the errors they embed. Those reach the
    same stderr the CLI's own report lines go to, so a name carrying ``\x1b[2K\r`` could
    erase the log line reporting it and author what the operator reads instead. The print
    sites escape; without this the log path is the way around them.

    Escaping happens **here**, at the CLI's handler, rather than at the library's
    ``logger.*`` call sites. Two reasons: a call site would have to remember, once per
    message, for every message that might carry a member-derived path — the class of bug
    this closes is precisely one that is missed one site at a time — and library records
    may be routed somewhere a control byte is harmless or wanted (a file, a structured
    sink, a test's ``caplog``). Presentation is the CLI's decision about its own terminal.

    The record is **copied** before its message is replaced. Other handlers format the
    same record — pytest's ``caplog``, an embedding application's — and ``cli_logging``
    below is explicit that library records must still reach them; mutating the shared
    record in place, even with a restore afterwards, would corrupt what they see.

    Only the message is escaped. An ``exc_info`` traceback is rendered by the base class
    from the untouched record and is archivey's own text, not the archive's: escaping its
    newlines would collapse a traceback onto one unreadable line.
    """

    def format(self, record: logging.LogRecord) -> str:
        escaped = copy.copy(record)
        escaped.msg = escape_member_name(record.getMessage())
        escaped.args = ()
        return super().format(escaped)


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
    """
    root = logging.getLogger("archivey")
    handler: logging.StreamHandler[TextIO] = logging.StreamHandler(err)
    handler.setFormatter(_EscapingFormatter("%(levelname)s: %(message)s"))
    old_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.INFO if verbose else logging.WARNING)
    try:
        yield
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)
        handler.close()
