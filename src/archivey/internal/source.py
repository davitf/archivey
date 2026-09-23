"""``ArchiveSource`` — the one object every archive source becomes at the boundary.

``open_archive`` and ``open_stream`` build one of these from whatever the caller passed —
a path, a stream, or a volume list — and hand it to detection and then to the backend.
It **is** the stream they read (third-party parsers such as ``tarfile``, ``pycdlib`` and
``zipfile`` get it directly), and it carries every guarantee the raw source must give:

- **Full-count.** ``read(n)`` returns ``n`` bytes unless the source is exhausted. A raw
  ``read(n)`` may legally return short, and header parsers — archivey's and the stdlib's —
  issue one ``read(n)`` and treat a short as EOF. How the guarantee is supplied is chosen
  once, at construction: an already-buffered source (``BytesIO``, an ``open()`` handle, a
  path's own handle) passes through; a seekable raw source gets a fixed-size read buffer
  this object builds and owns; a non-seekable raw source is gathered by re-asking for the
  missing bytes, with no read-ahead.
- **Ownership.** It closes what archivey opened or built — a path's handle, a joined
  volume set, its own read buffer — and never the caller's object. The buffer is
  detached rather than closed, so the caller's raw stream survives it.
- **Bounded reads.** No ``read(n)`` asks the source for more than it can still supply:
  clamped when the remaining length is a fact, served in steps when it is not
  (:func:`read_within_reach`). pycdlib and ``tarfile`` call ``read(n)`` with a size read
  out of the archive and will never call an archivey method, so this is the ordinary
  ``read``, not a separate one. The bound runs over the full-count strategy, never over
  the raw inner: ``read_within_reach`` takes one ``read`` as final.
- **Cheap facts.** ``path`` when a real file exists, ``volume_paths`` for a joined set of
  files, ``size`` when it is a fact, ``size_hint``, ``name``, all settled at
  construction.

A non-seekable source also holds the **detection replay prefix**: :meth:`peek` fills it
without consuming, and ``read`` drains it before reaching the source. Detection and the
backend therefore see the same object, and nothing is swapped in between.

Only a size that is a **fact** clamps a read — ``stat`` on a path, a ``BytesIO``'s buffer,
``fstat`` on a regular file, a joined set's measured parts. An integer ``size`` attribute
on a caller's object (the fsspec convention) is a hint: it only steps a read, because
clamping on a hint that understates would truncate a legitimate read. :attr:`size` is the
fact alone, so every slice or shared view built over this object — which asks
``source_byte_size``, and that reads ``size`` first — clamps on a fact or steps too. The
hint survives as :attr:`size_hint`, for ``compressed_source_size``, which reports it.

What stays outside, as wrappers over this object that keep its guarantees: measurement
(``SeekCountingStream``), and ZIP's start offset (a ``SlicingStream``). Member-level
streams — slices, shared views, decompressors — sit above the source and have their own
ownership rules (``dev-docs/topics/stream-ownership.md``).
"""

from __future__ import annotations

import io
import os
import stat
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, Protocol, Sequence, cast

from archivey.internal.streams.streamtools import (
    DEFAULT_UNKNOWN_LENGTH_READ_STEP,
    ReadOnlyIOStream,
    SlicingStream,
    ensure_bufferedio,
    is_seekable,
    raise_if_text_stream,
    read_exact,
    read_within_reach,
    source_byte_size,
    source_name,
    source_size_fact,
)

if TYPE_CHECKING:
    from _typeshed import WriteableBuffer


class JoinedVolumes(Protocol):
    """What :meth:`ArchiveSource.for_volumes` needs from a joined set.

    ``ConcatenatedFile`` in ``volumes.py`` is the one implementation. It is named by shape
    here because ``volumes.py`` builds sources and this module must not import it back.
    """

    @property
    def size(self) -> int: ...
    @property
    def volume_count(self) -> int: ...
    @property
    def volume_paths(self) -> list[Path]: ...
    def read(self, n: int = -1, /) -> bytes: ...
    def seek(self, offset: int, whence: int = 0, /) -> int: ...
    def tell(self) -> int: ...
    def close(self) -> None: ...


class _GatheringReader:
    """Full-count ``read(n)`` over a non-seekable raw source, with no buffer of its own.

    ``BufferedReader`` would also be full-count, but it reads *ahead*, and from a pipe that
    over-read cannot be given back. This re-asks for the bytes still missing instead
    (:func:`read_exact`), so ``read(n)`` takes exactly ``n`` bytes from the source.

    The inner is the caller's object, un-normalised. The one assumption made about it is
    that ``read(n)`` for ``n > 0`` returns at most ``n`` bytes, and empty only at EOF; more
    than ``n`` raises. ``read(-1)`` is never forwarded: an inner may have no ``readall``
    (``io.BufferedRWPair``, ``GzipFile`` over a pipe), so a drain is served as sized
    reads instead.
    """

    __slots__ = ("_inner",)

    def __init__(self, inner: BinaryIO) -> None:
        self._inner = inner

    def read(self, n: int = -1, /) -> bytes:
        if n < 0:
            # Drain in sized reads rather than forward ``read(-1)``; see the docstring.
            chunks = []
            while chunk := self.read(io.DEFAULT_BUFFER_SIZE):
                chunks.append(chunk)
            return b"".join(chunks)
        data = self._inner.read(n)
        if data is None:
            return b""
        got = len(data)
        if got == n:
            return data  # common case: no copy
        if got > n:
            raise ValueError(
                f"inner returned {got} bytes for read({n}): {self._inner!r}"
            )
        if got == 0:
            return b""
        return data + read_exact(self._inner, n - got)


class ArchiveSource(ReadOnlyIOStream):
    """A read-only stream over one archive source, carrying every source guarantee.

    Build one with :meth:`for_path`, :meth:`for_stream` or :meth:`for_volumes`; the
    constructor itself is private. See the module docstring for what each guarantee
    means and where it comes from.

    Which private fields are set depends only on the shape it was built from:

    ============================  ==========  ============  ===========  ========
    shape                         ``size``    replay        gatherer     seekable
    ============================  ==========  ============  ===========  ========
    path (regular file)           fact        none          none         yes
    path (block device)           ``None``    none          none         yes
    path (FIFO, device, socket)   ``None``    yes           none         no
    directory                     ``None``    yes (unused)  none         no
    buffered caller stream        fact/None   none          none         yes
    seekable raw caller stream    fact/None   none          none         yes
    non-seekable buffered stream  ``None``    yes           none         no
    non-seekable raw stream       ``None``    yes           yes          no
    joined volume set             fact        none          none         yes
    ============================  ==========  ============  ===========  ========

    So a fact length exists only on a seekable source, and replay and gathering only on
    a non-seekable one. ``__init__`` asserts both, and :meth:`read`'s inlined clamp
    relies on them: a fact length alone proves there is nothing to replay or gather.
    A new shape or strategy must keep that true, or change ``read``, ``readinto``,
    ``_read_all``, :meth:`peek` and :meth:`close` together.
    """

    # ``is_seekable`` takes :meth:`seekable` at its word for this class instead of also
    # asking ``fileno()`` whether the object is a pipe: the answer was settled at
    # construction — by ``is_seekable`` itself on a caller's stream, and by ``stat``'s
    # file type on a path — and asking again would open a path source's handle only to
    # learn what ``stat`` already said.
    _SEEKABLE_IS_SETTLED = True

    # The step an unknown-length read is served in. What the number buys is documented
    # once, on :data:`DEFAULT_UNKNOWN_LENGTH_READ_STEP`.
    _UNKNOWN_LENGTH_READ_STEP = DEFAULT_UNKNOWN_LENGTH_READ_STEP

    # Class-level defaults, so that ``close()`` — which ``IOBase``'s finalizer calls — can
    # run on an instance whose construction failed partway. Every one of these is
    # replaced per instance before it matters.
    _owned: BinaryIO | JoinedVolumes | None = None
    _buffer: io.BufferedIOBase | None = None
    _replay: bytearray | None = None

    def __init__(
        self,
        *,
        path: Path | None,
        reader: BinaryIO | None,
        seekable: bool,
        size: int | None,
        length: int | None,
        name: str | None,
        caller_stream: BinaryIO | None = None,
        owned: BinaryIO | JoinedVolumes | None = None,
        gatherer: _GatheringReader | None = None,
        buffer: io.BufferedIOBase | None = None,
        volume_paths: Sequence[Path] = (),
        volume_count: int = 1,
        joined: JoinedVolumes | None = None,
        is_directory: bool = False,
        position: int = 0,
        open_path: Path | None = None,
    ) -> None:
        super().__init__()
        self._path = path
        # What a lazy handle opens. The same as ``path`` except for a path naming a pipe
        # or device, which has no ``path`` to offer (see :meth:`for_path`).
        self._open_path = path if open_path is None else open_path
        # ``None`` for a path source until its first read (the handle opens lazily), and
        # for a directory, which has no stream at all.
        self._reader = reader
        self._seekable = seekable
        self._size = size
        # The total length when it is a fact, which is all the clamp may use; ``_size``
        # can be a hint.
        self._length = length
        self._name = name
        self._caller_stream = caller_stream
        self._owned = owned
        self._buffer = buffer
        self._volume_paths = list(volume_paths)
        self._volume_count = volume_count
        self._joined = joined
        self._is_directory = is_directory
        # Logical position, kept here rather than asked of the reader on every read: the
        # clamp needs it, and it is only ever moved through this object. A caller's
        # seekable stream starts wherever the caller left it.
        self._pos = position
        # Only a non-seekable source replays a prefix; a seekable one is rewound instead.
        self._replay = None if seekable else bytearray()
        # Set for a non-seekable raw source, whose reads all go through it; ``_reader`` is
        # then the caller's object, used for nothing but identity.
        self._gatherer = gatherer
        # The two implications the class docstring's table states and ``read`` relies on.
        assert length is None or seekable, "a fact length on a non-seekable source"
        assert gatherer is None or not seekable, "a gatherer on a seekable source"

    # --- construction --------------------------------------------------------------------

    @classmethod
    def for_path(cls, path: Path, *, volume_count: int = 1) -> ArchiveSource:
        """A source over a file or directory named by ``path``; nothing is opened yet.

        The file's handle opens on the first read, seek or ``fileno()``. A backend that
        needs only the path — the directory reader, ``unrar`` — therefore leaves no handle
        open on the archive, which matters on Windows, where an open handle blocks deleting
        or renaming it. ``volume_count`` is the size of the set ``path`` is the first volume
        of, when discovery found siblings the path does not join (RAR's naming schemes).
        """
        if path.is_dir():
            return cls(
                path=path,
                reader=None,
                seekable=False,
                size=None,
                length=None,
                name=str(path),
                is_directory=True,
            )
        size: int | None
        seekable = True
        try:
            st = os.stat(path)
        except OSError:
            # Let the first read raise the real error with its own errno; detection and
            # the backends expect ``open()``'s ``FileNotFoundError`` and friends.
            size = None
        else:
            size = st.st_size if stat.S_ISREG(st.st_mode) else None
            # The rule ``is_seekable`` applies to an open handle: a pipe, a character
            # device or a socket cannot reposition (a Windows pipe claims it can and
            # then does not); a block device can.
            mode = st.st_mode
            seekable = not (
                stat.S_ISFIFO(mode) or stat.S_ISCHR(mode) or stat.S_ISSOCK(mode)
            )
        return cls(
            # A pipe's path is no file a backend could reopen and re-read: reading it
            # again consumes different bytes. It goes on as a stream archivey opened,
            # through the one handle this source owns.
            path=path if seekable else None,
            open_path=path,
            reader=None,
            seekable=seekable,
            size=size,
            length=size,
            name=str(path),
            volume_count=volume_count,
        )

    @classmethod
    def for_stream(cls, stream: BinaryIO) -> ArchiveSource:
        """Borrow a caller's stream: full-count, never closed by archivey."""
        raise_if_text_stream(stream)
        if isinstance(stream, ArchiveSource):
            return stream
        size = source_byte_size(stream)
        fact = source_size_fact(stream)
        name = source_name(stream)
        seekable = is_seekable(stream)
        # Where the caller left it: the archive starts there (see
        # :meth:`rebase_to_current_position`), and the clamp counts from it.
        position = stream.tell() if seekable else 0
        if isinstance(stream, io.BufferedIOBase):
            # Already full-count: ``BufferedIOBase.read(n)`` keeps asking its raw until it
            # has ``n`` or reaches EOF. No second buffer, and ``fileno()`` still forwards.
            return cls(
                path=None,
                reader=stream,
                seekable=seekable,
                size=size,
                length=fact,
                name=name,
                caller_stream=stream,
                position=position,
            )
        if not seekable:
            return cls(
                path=None,
                reader=stream,
                gatherer=_GatheringReader(stream),
                seekable=False,
                size=size,
                length=None,
                name=name,
                caller_stream=stream,
            )
        # A seekable raw source: a fixed-size buffer is full-count and its read-ahead is
        # recoverable by seeking. It is archivey's, so it closes with this object — by
        # detaching, so the caller's raw stays open.
        buffer = ensure_bufferedio(stream)
        return cls(
            path=None,
            # typeshed keeps BufferedIOBase and BinaryIO apart; at runtime it is one.
            reader=cast("BinaryIO", buffer),
            seekable=True,
            size=size,
            length=fact,
            name=name,
            caller_stream=stream,
            buffer=buffer,
            position=position,
        )

    @classmethod
    def for_volumes(
        cls, joined: JoinedVolumes, *, name: str | None = None
    ) -> ArchiveSource:
        """Own a joined volume set.

        The joined set's ``read`` already gathers across volumes, re-asking each one until
        the request or the volume ends, so it passes through, and its caller-stream parts
        need no full-count strategy of their own; it never closes them either. Its size is
        a fact: the sum of ``stat`` sizes for file volumes, and for stream volumes the
        lengths it measured to place its offsets.
        """
        return cls(
            path=None,
            # ConcatenatedFile, the one implementation, is a BinaryIO; the protocol only
            # names the facts read off it here.
            reader=cast("BinaryIO", joined),
            seekable=True,
            size=joined.size,
            length=joined.size,
            name=name,
            owned=joined,
            volume_paths=joined.volume_paths,
            volume_count=joined.volume_count,
            joined=joined,
        )

    # --- cheap facts ---------------------------------------------------------------------

    @property
    def path(self) -> Path | None:
        """The file this source reads, when there is one; ``None`` for a stream or a set.

        Also ``None`` for a path naming a pipe or a device: such a path is read once,
        through this source, and never handed to a backend to reopen.
        """
        return self._path

    @property
    def is_directory(self) -> bool:
        """Whether this source names a directory; it then has no stream at all."""
        return self._is_directory

    @property
    def volume_paths(self) -> list[Path]:
        """The files of a joined set, in order, when every volume is a file; else empty."""
        return list(self._volume_paths)

    @property
    def volume_count(self) -> int:
        """How many volumes this source spans (``1`` for a single file or stream)."""
        return self._volume_count

    @property
    def joined(self) -> JoinedVolumes | None:
        """The joined set this source reads, for a volume list; else ``None``."""
        return self._joined

    @property
    def size(self) -> int | None:
        """The source's total length when it is a fact, else ``None``.

        The one length anything may clamp a read to. ``source_byte_size`` reads it first,
        so a slice or shared view over this source clamps on a fact or not at all.
        """
        return self._length

    @property
    def size_hint(self) -> int | None:
        """The source's total length when cheaply known, a caller's claim included.

        What ``source_byte_size`` said of the caller's object, measured once: an fsspec
        ``size`` attribute counts here and not in :attr:`size`. For reporting
        (``compressed_source_size``), never for bounding a read.
        """
        return self._size

    @property
    def name(self) -> str:  # pyrefly: ignore[bad-override]  # base is Never; a source has a path when the caller gave one
        """The source's path, or raise :exc:`AttributeError` if it has none.

        Raising keeps ``hasattr(..., "name")`` false for a nameless stream, the
        :class:`ReadOnlyIOStream` contract.
        """
        if self._name is None:
            raise AttributeError("name")
        return self._name

    # --- stream surface ------------------------------------------------------------------

    def seekable(self) -> bool:
        return self._seekable

    def _stream(self) -> BinaryIO:
        reader = self._reader
        if reader is not None:
            return reader
        if self.closed:
            raise ValueError("I/O operation on closed file.")
        if self._is_directory:
            raise io.UnsupportedOperation(f"{self._path} is a directory, not a stream")
        assert self._open_path is not None
        handle = open(self._open_path, "rb")
        self._owned = handle
        self._reader = handle
        # A size that ``stat`` could not answer at construction (the file appeared since,
        # or is not a regular file) stays a non-fact: reads step.
        return handle

    def read(self, n: int | None = -1, /) -> bytes:
        if n is None or n < 0:
            return self._read_all()
        # The common case, inlined: a sized read of a source whose length is a fact —
        # which proves there is nothing to replay and no gathering (the class
        # docstring's table). This is ``read_within_reach``'s clamp branch; every read
        # of an archive pays for this method's frames, so the rest of the dispatch is
        # kept off it (task 8.2 of the change).
        reader = self._reader
        length = self._length
        if length is not None and reader is not None:
            data = reader.read(min(n, max(length - self._pos, 0)))
            self._pos += len(data)
            return data
        if n == 0:
            return b""
        replay = self._replay
        if replay:
            head = bytes(replay[:n])
            del replay[:n]
            self._pos += len(head)
            if len(head) == n:
                return head
            return head + self._read_source(n - len(head))
        return self._read_source(n)

    def _full_count_reader(self) -> BinaryIO | _GatheringReader:
        if self._gatherer is not None:
            return self._gatherer
        return self._stream()

    def _read_source(self, n: int) -> bytes:
        reader = self._full_count_reader()
        remaining = None if self._length is None else self._length - self._pos
        data = read_within_reach(
            reader, n, remaining=remaining, step=self._UNKNOWN_LENGTH_READ_STEP
        )
        self._pos += len(data)
        return data

    def _read_all(self) -> bytes:
        replay = self._replay
        head = b""
        if replay:
            head = bytes(replay)
            replay.clear()
            self._pos += len(head)
        if self._length is not None:
            rest = self._read_source(max(self._length - self._pos, 0))
        elif self._gatherer is not None:
            # Sized reads only: the inner may have no ``readall``.
            chunks = []
            while True:
                chunk = self._read_source(io.DEFAULT_BUFFER_SIZE)
                if not chunk:
                    break
                chunks.append(chunk)
            rest = b"".join(chunks)
        else:
            rest = self._stream().read(-1) or b""
            self._pos += len(rest)
        return head + rest if head else rest

    def readinto(self, b: WriteableBuffer, /) -> int:
        # The caller already allocated ``b``, so only the clamp matters here, not the
        # step. A replayed prefix or the gathering strategy goes through ``read``.
        if self._replay or self._gatherer is not None or self._reader is None:
            return super().readinto(b)
        view = memoryview(b).cast("B")
        if self._length is not None:
            remaining = max(self._length - self._pos, 0)
            if len(view) > remaining:
                view = view[:remaining]
        readinto = getattr(self._reader, "readinto", None)
        if readinto is None:
            return super().readinto(b)
        got = readinto(view) or 0
        self._pos += got
        return got

    def peek(self, n: int) -> bytes:
        """The first ``n`` unconsumed bytes, without consuming them; non-seekable only.

        Fills the replay prefix detection reads from. Fewer than ``n`` bytes come back
        only when the source ends first. A seekable source is rewound instead of replayed,
        so this refuses there rather than growing a buffer nobody drains.
        """
        if n < 0:
            raise ValueError("peek size must be non-negative")
        replay = self._replay
        if replay is None:
            raise io.UnsupportedOperation("peek: the source is seekable; rewind it")
        missing = n - len(replay)
        if missing > 0:
            # One full-count read: a short return is EOF. Not bounded by the step —
            # ``n`` is detection's own window, never a number read out of the archive.
            chunk = self._full_count_reader().read(missing)
            if chunk:
                replay.extend(chunk)
        return bytes(replay[:n])

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        if not self._seekable:
            raise io.UnsupportedOperation("seek")
        self._pos = self._stream().seek(offset, whence)
        return self._pos

    def tell(self, /) -> int:
        if self.closed:
            raise ValueError("I/O operation on closed file.")
        if not self._seekable:
            # Forward-only: the seek-required refusals rely on ``tell`` raising here, as
            # it does on the pipe itself.
            raise io.UnsupportedOperation("tell")
        # Tracked, not asked of the reader: a path source nobody has read yet sits at 0,
        # and answering that must not open it.
        return self._pos

    def fileno(self) -> int:
        """Forward ``fileno`` for a file or a caller's buffered stream.

        A gathered raw source does not forward it: the object this reads is not the one
        the descriptor would describe to a caller that bypasses it.
        """
        if self._gatherer is not None or self._joined is not None:
            raise io.UnsupportedOperation("fileno")
        return self._stream().fileno()

    def rebase_to_current_position(self) -> None:
        """Make the current position this source's offset 0.

        A caller's seekable stream is taken to hold the archive starting wherever it was
        positioned. Detection peeks from there and restores it; this then makes every
        backend see ``tell() == 0`` at the first archive byte. The offset arithmetic stays
        in :class:`SlicingStream`, which this puts between itself and its reader.
        """
        if not self._seekable or self._pos == 0:
            return
        start = self._pos
        # The slice borrows its inner, so closing this object still closes exactly what
        # it closed before: its own buffer and nothing of the caller's. It is told the
        # fact length rather than probing its inner, which could answer with a hint.
        self._reader = SlicingStream(
            self._stream(),
            start=start,
            source_size=self._length,
            probe_source_size=False,
        )
        if self._size is not None:
            self._size = max(self._size - start, 0)
        if self._length is not None:
            self._length = max(self._length - start, 0)
        self._pos = 0

    def close(self) -> None:
        if self.closed:
            return
        try:
            owned = self._owned
            self._owned = None
            if owned is not None:
                owned.close()
        finally:
            try:
                buffer = self._buffer
                self._buffer = None
                if buffer is not None:
                    # A ``_NonClosingBufferedReader``: it detaches, so the caller's raw
                    # stays open.
                    buffer.close()
            finally:
                if self._replay is not None:
                    self._replay.clear()
                # Dropping the readers is what refuses a read after close, at no cost
                # to the read path: every read that would touch the stream then reaches
                # ``_stream()``, which checks ``closed`` (``read(0)`` and an empty
                # ``readinto`` return first, as nothing is read). A borrowed caller
                # stream would otherwise go on serving bytes to whatever still holds
                # this source.
                self._reader = None
                self._gatherer = None
                super().close()

    def __repr__(self) -> str:
        if self._is_directory:
            return f"ArchiveSource(directory={self._path!r})"
        if self._path is not None:
            return f"ArchiveSource(path={self._path!r})"
        if self._open_path is not None:
            return f"ArchiveSource(pipe={self._open_path!r})"
        if self._joined is not None:
            return f"ArchiveSource(volumes={self._volume_count})"
        return f"ArchiveSource({self._caller_stream!r})"


__all__ = ["ArchiveSource", "JoinedVolumes"]
