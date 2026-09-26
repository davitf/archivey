"""Capture the ``open_archive()`` caller location for capability-error breadcrumbs."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from types import FrameType

from archivey.terminal import display_path


@dataclass(frozen=True)
class OpenSite:
    """Where the caller invoked ``open_archive`` (outside archivey frames).

    Only the ``file:line`` is captured — that is all any consumer reads (the
    ``ConcurrentAccessError`` breadcrumb). We deliberately do NOT retain a full
    ``traceback.extract_stack()`` snapshot: it cost an unconditional stack format on
    every ``open_archive`` and held ``FrameSummary`` objects for the reader's whole
    lifetime, which the founding "open millions of archives" dedupe workload pays for
    with nothing reading it back.
    """

    filename: str
    lineno: int

    @property
    def location(self) -> str:
        """``file:line``, ``/``-separated.

        This lands in a ``ConcurrentAccessError`` message, which escapes itself, and a
        native Windows filename would have every separator doubled by that escape — in
        the one string whose whole job is to be read and pasted back to find the call.
        """
        return f"{display_path(self.filename)}:{self.lineno}"


def capture_open_site(
    *, skip_module_prefixes: tuple[str, ...] = ("archivey.",)
) -> OpenSite:
    """Return the first non-archivey caller's ``file:line`` (cheap frame walk only).

    ``sys._getframe`` exists on every interpreter archivey supports (CPython and PyPy),
    and the walk always stops: the bottom frame of a real stack is ``__main__``,
    ``runpy``, ``threading``, a test module or an ``exec`` with no ``__name__``, none of
    them under ``archivey.``. ``<unknown>:0`` is kept only as the result for a stack
    made entirely of archivey frames.
    """
    # Start from the caller of this function.
    frame: FrameType | None = sys._getframe(1)
    while frame is not None:
        mod = frame.f_globals.get("__name__", "")
        if not any(
            mod == p.rstrip(".") or mod.startswith(p) for p in skip_module_prefixes
        ):
            return OpenSite(filename=frame.f_code.co_filename, lineno=frame.f_lineno)
        frame = frame.f_back
    return OpenSite(filename="<unknown>", lineno=0)
