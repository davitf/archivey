"""Run a pyppmd decoder in a child process, so a native crash cannot kill the caller.

pyppmd 1.3.1 (and 1.2.0) segfaults when it is asked to decode after a corrupt stream
has already hit its end: the model returns its end symbol early, ``eof`` goes up, and
the next ``decode`` starts a new worker on a model that has finished. From Python that
state cannot be told apart from a valid stream that is waiting for more input, because
``eof`` also rises (and never clears) on a valid stream whose compressed bytes happen to
end in zeros at a chunk boundary. The one-symbol decode ``PpmdDecoder`` sends at close
to stop a worker that may be parked reaches the same state on a member that ended early
(reports/ppmd-in-process-crash-2026-10-20.md). So every PPMd member decodes in a child
process unless ``process_isolation`` is ``NONE``.

:func:`open_ppmd_child` returns a :class:`ChildPpmdDecoder`, which has the surface of
``pyppmd.Ppmd7Decoder`` / ``Ppmd8Decoder`` as ``PpmdDecoder`` uses it, so all the
decoding logic stays in ``PpmdDecoder``; the child only owns the native object. The
child runs the generic decoder worker (``decoder_worker.py``), which imports pyppmd and
nothing from ``archivey``. The child may serve a later member only after one that ended
cleanly: ``PpmdDecoder`` calls ``spoil`` otherwise.
"""

from __future__ import annotations

from archivey.internal.streams.child_process import describe_exit
from archivey.internal.streams.codecs.decoder_child import (
    DecoderChild,
    decoder_child_unavailable_reason,
)
from archivey.internal.streams.codecs.isolated import (
    PPMD_ERRORS,
    ChildPpmdDecoder,
)


class PpmdChildAllocationError(RuntimeError):
    """The child started, then died or raised ``MemoryError`` constructing the decoder.

    The constructor allocates the member's declared ``mem_size``, and pyppmd aborts
    the process rather than raising when that allocation is refused (a container
    memory limit, ``RLIMIT_AS``). Decoding in-process would allocate the same amount
    and abort the caller, so ``PpmdDecoder`` refuses the member with this message and
    does not suggest it.
    """


def _allocation_error(mem_size: int, detail: str) -> PpmdChildAllocationError:
    return PpmdChildAllocationError(
        f"the PPMd decoder process could not allocate this member's model "
        f"(mem_size={mem_size} bytes): {detail}. A memory limit on this process (a "
        "container limit, RLIMIT_AS) is the likely cause, and decoding in-process "
        "would fail the same way and take this process down with it. Run with more "
        "memory, or lower DecoderLimits.max_decoder_memory to refuse such members up "
        "front."
    )


class _PpmdChild(DecoderChild):
    describe = "PPMd decoder process"

    def __init__(self, codec: str, args: tuple[object, ...], *, mem_size: int) -> None:
        self._mem_size = mem_size
        super().__init__(codec, args, known=PPMD_ERRORS)

    def _death_error(self, returncode: int | None) -> Exception:
        if self._building:
            return _allocation_error(self._mem_size, describe_exit(returncode))
        return super()._death_error(returncode)


def ppmd_child_unavailable_reason() -> str | None:
    """Why no child process can be started to run the worker, or ``None`` when one
    can."""
    return decoder_child_unavailable_reason("PPMd")


def open_ppmd_child(
    *, variant: int, order: int, mem_size: int, restore_method: int = 0
) -> ChildPpmdDecoder:
    """A pyppmd decoder in a child process (taken from the pool).

    Raises ``DecoderChildStartError`` when no child starts or it cannot import pyppmd,
    and :class:`PpmdChildAllocationError` when it dies or raises ``MemoryError``
    building the model.
    """
    if variant == 8:
        codec, args = "ppmd8", (order, mem_size, restore_method)
    else:
        codec, args = "ppmd7", (order, mem_size)
    try:
        child = _PpmdChild(codec, args, mem_size=mem_size)
    except MemoryError as exc:
        raise _allocation_error(mem_size, str(exc) or "MemoryError") from exc
    return ChildPpmdDecoder(child)
