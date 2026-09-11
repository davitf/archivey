"""Shared bases for archivey's read-only stream wrappers.

``io.RawIOBase``'s defaults are the wrong way round for a *wrapper*: ``readable``/``writable``/
``seekable`` default to ``False``, and ``read``/``readall`` are implemented in terms of
``readinto``. Every read-only wrapper would otherwise re-declare ``readable()->True`` /
``writable()->False`` and supply a ``readinto``. These bases invert that once:

- :class:`ReadOnlyIOStream` provides the read-only surface (``readable``/``writable``/``write``)
  and a single canonical ``readinto``/``readall`` built on the subclass's ``read``. A subclass
  implements ``read`` (+ whatever it actually changes); ``read`` is left abstract so a subclass
  that forgets it fails loudly instead of looping through ``RawIOBase``.
- :class:`DelegatingStream` additionally holds one inner ``BinaryIO`` and forwards
  ``read``/``readinto``/``seek``/``tell``/``seekable``/``close`` to it, so a wrapper that only
  changes one operation overrides just that method.

This module is part of the codec-/format-agnostic ``streamtools`` core: it imports only from
``streamtools`` itself (``is_seekable``), nothing from the rest of ``archivey``.
"""

from __future__ import annotations

import abc
import io
from typing import TYPE_CHECKING, Any, BinaryIO, Never

from archivey.internal.streams.streamtools.binaryio import is_seekable, source_name

if TYPE_CHECKING:
    from _typeshed import WriteableBuffer


class ReadOnlyIOStream(io.RawIOBase, BinaryIO):
    """Base for a read-only ``BinaryIO``: subclasses implement ``read`` (and what they change).

    Deliberately defines only the *read-only surface* (``read``/``readinto``/``readall`` +
    ``readable``/``writable``/``write``). It does **not** define ``seek``/``tell``/``seekable``/
    ``close``: those genuinely vary per wrapper — sequential vs. seekable, owns-its-inner vs. a
    non-owning view — so subclasses declare them, or inherit ``RawIOBase``'s non-seekable
    defaults (``seekable()->False``, ``seek`` raising). :class:`DelegatingStream` supplies the
    forwarding versions for wrappers that do own one inner stream.
    """

    @abc.abstractmethod
    def read(self, n: int = -1, /) -> bytes:
        # @abstractmethod marks the subclass contract. On Python 3.12+ ABCMeta already
        # rejects constructing a subclass that omits read(); on 3.11, io.RawIOBase's C
        # __new__ still allows construction, so this body is the runtime guard — a
        # forgotten read() fails loudly instead of looping via RawIOBase.
        raise NotImplementedError

    def readinto(self, b: "WriteableBuffer", /) -> int:
        """Canonical ``readinto``: read into ``b`` via the subclass's ``read``."""
        mv = memoryview(b).cast("B")
        data = self.read(len(mv))
        mv[: len(data)] = data
        return len(data)

    def readall(self) -> bytes:
        chunks = bytearray()
        while True:
            chunk = self.read(io.DEFAULT_BUFFER_SIZE)
            if not chunk:
                break
            chunks.extend(chunk)
        return bytes(chunks)

    def readable(self) -> bool:
        return True

    def writable(self) -> bool:
        return False

    def write(self, b: Any, /) -> int:
        raise io.UnsupportedOperation("write")

    @property
    def mode(self) -> str:
        """Always ``"rb"``: every stream here is read-only binary by construction.

        Like :meth:`name`, this override exists because ``typing.BinaryIO`` is in our
        MRO. ``typing.IO.mode`` is a real runtime property whose body returns ``None``,
        so a subclass that does not override it advertises ``mode = None`` rather than
        having no ``mode`` at all. Libraries that duck-type it break on that: pycdlib
        does ``'b' not in fp.mode``, which raises ``TypeError`` on ``None``.

        Unlike :meth:`name`, there is a correct answer for every stream in this module,
        so this one returns a value instead of raising.
        """
        return "rb"

    @property
    def name(self) -> Never:
        """Always raises :exc:`AttributeError`. The raise *is* the contract.

        Deleting this property does **not** remove the attribute. ``typing.BinaryIO``
        is in our MRO and ``typing.IO.name`` is a real runtime property returning
        ``None``, so a subclass that does not override it advertises ``name = None``
        to duck-typing consumers — worse than absence, because ``hasattr`` says yes
        and the value is unusable. pycdlib on Windows does
        ``fp.name.startswith(...)`` on a device path and raises ``AttributeError`` on
        exactly that.

        ``typing.IO`` declaring ``name`` at all is arguably a typing bug, because the
        stdlib treats it as optional: ``open(path, "rb").name`` is the path, while
        ``io.BytesIO()`` and ``io.BufferedReader(io.BytesIO())`` have no ``name``
        attribute whatsoever. Streams without a real path must match that duck-typing
        surface, so raising is how we opt back out of the declaration.

        We keep the ``BinaryIO`` base regardless — it is what makes these wrappers
        nominally ``BinaryIO`` for the checkers, which is the point of having it.
        Dropping it costs 64 errors on both pyrefly and ty (measured 2026-09-11) and
        would mean replacing ``typing.BinaryIO`` with a Protocol across the internal
        surface.

        ``Never`` is the honest return type: this returns nothing, ever. It is also
        why subclasses that *do* have a path (:class:`DelegatingStream` here, and
        ``PeekableStream`` outside this module) need ``# pyrefly: ignore[bad-override]``
        — widening ``Never`` to ``str`` is a deliberate LSP exception, not an
        oversight.

        Do not "fix" this by returning a string or ``None``. See :meth:`mode`, which
        overrides the same ``typing.IO`` stub for the same reason.
        """
        raise AttributeError("name")


class DelegatingStream(ReadOnlyIOStream):
    """A read-only wrapper around one inner ``BinaryIO``, forwarding to it by default.

    Subclasses override only the method whose behavior they change (e.g. just ``seek`` to add a
    warning, or just ``close`` to add a cleanup guard).

    **Consistency caveat (``readinto_passthrough``).** By default ``readinto`` forwards straight
    to ``inner.readinto`` (zero-copy), which *bypasses this class's ``read``*. That is correct
    for a plain delegator, but a subclass that overrides ``read`` with a side effect (tracking
    bytes, hashing, a check at EOF) would have that side effect skipped on ``readinto``-driven
    reads. Such a subclass MUST pass ``readinto_passthrough=False``, which routes ``readinto``
    through ``read`` (the :class:`ReadOnlyIOStream` implementation) so the override always runs.
    (We use an explicit flag rather than auto-detecting an overridden ``read``: a plain
    pass-through override of ``read`` should keep the zero-copy path, and silent auto-detection
    would make that choice invisible and bug-prone.)
    """

    def __init__(self, inner: BinaryIO, *, readinto_passthrough: bool = True) -> None:
        super().__init__()
        self._inner = inner
        self._readinto_passthrough = readinto_passthrough

    def nearest_resume_offset(self, target: int) -> int | None:
        """Forward the rewind-cost query inward (see ``ArchiveStream._maybe_warn_rewind``).

        Decompressed streams reach the public handle through several of these wrappers
        (truncation backstop, accelerator guard, byte counters), and only the innermost
        one knows the seek-point table. Forwarding by default here keeps every wrapper
        transparent to the question; a wrapper that *changes* the offset space (a slice)
        must override or decline. ``None`` from an inner that cannot answer means "no cost
        signal", which the caller treats as "say nothing" rather than "free".
        """
        ask = getattr(self._inner, "nearest_resume_offset", None)
        if ask is None:
            return None
        offset = ask(target)
        return offset if isinstance(offset, int) else None

    def read(self, n: int = -1, /) -> bytes:
        return self._inner.read(n)

    def readinto(self, b: "WriteableBuffer", /) -> int:
        # Zero-copy passthrough when allowed and the inner exposes readinto; otherwise route
        # through self.read() (the read()-based base), so an overridden read() is not bypassed.
        if self._readinto_passthrough:
            inner_readinto = getattr(self._inner, "readinto", None)
            if inner_readinto is not None:
                return inner_readinto(b)
        return super().readinto(b)

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        return self._inner.seek(offset, whence)

    def tell(self, /) -> int:
        return self._inner.tell()

    def seekable(self) -> bool:
        # is_seekable() handles the edge cases a bare inner.seekable() misses (a BufferedReader
        # over a non-seekable raw; a pipe that reports seekable()=True but cannot reposition).
        return is_seekable(self._inner)

    def close(self) -> None:
        if self.closed:
            return
        self._inner.close()
        super().close()

    @property
    def name(self) -> str:  # pyrefly: ignore[bad-override]  # base is Never; this returns a path when the inner has one
        """Path of the inner stream, or raise :exc:`AttributeError` if it has none.

        Raising keeps ``hasattr(..., "name")`` false when the inner is nameless
        (the :class:`ReadOnlyIOStream` contract). Returning ``None`` would make
        ``hasattr`` true and crash pycdlib's Windows ``fp.name.startswith(...)``.
        """
        resolved = source_name(self._inner)
        if resolved is not None:
            return resolved
        raise AttributeError("name")
