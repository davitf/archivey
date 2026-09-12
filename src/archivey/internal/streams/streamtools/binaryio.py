"""Adapt arbitrary caller-provided objects to the uniform ``BinaryIO`` the library relies on.

Sources reach the library in many shapes — a path, a real file object, a ``BytesIO``, or a
codec library's file-like that is *almost* a ``BinaryIO`` (e.g. missing ``readinto``). This
module is the single place that classifies those objects (``is_filename`` / ``is_stream`` /
``is_seekable``) and coerces them to a consistent ``BinaryIO`` (``ensure_binaryio`` /
``ensure_bufferedio`` / ``ensure_full_count_reads`` / ``BinaryIOWrapper``), so the rest of
the stream layer can assume one interface — including that a ``read(n)`` on the archive
source returns ``n`` bytes short of EOF (``ensure_full_count_reads``).
"""

from __future__ import annotations

import io
import logging
import mmap
import os
import stat
from typing import (
    TYPE_CHECKING,
    Any,
    BinaryIO,
    Protocol,
    TypeGuard,
    cast,
    runtime_checkable,
)

# The ``streamtools`` subpackage is deliberately free of any archivey dependency — pure
# stdlib binary-stream plumbing — so it could one day be lifted out as a standalone library.
# Hence a plain stdlib logger rather than importing archivey's logging module; the name still
# places it under the "archivey.streams" hierarchy when used inside archivey.
logger = logging.getLogger("archivey.streams")

if TYPE_CHECKING:
    from _typeshed import WriteableBuffer


@runtime_checkable
class ReadableStream(Protocol):
    """Minimal readable-binary protocol: just ``read``."""

    def read(self, n: int = ..., /) -> bytes: ...


_BLOCKING_READ_MESSAGE = (
    "underlying stream returned no data without reaching EOF (non-blocking "
    "stream?); archivey requires a blocking stream"
)


def try_readinto(stream: Any, b: "WriteableBuffer") -> int | None:
    """Call ``stream.readinto(b)`` when it is a real implementation.

    Returns the filled-byte count, or ``None`` if ``stream`` has no usable
    ``readinto`` so the caller can fall back to ``read``:

    - the attribute is missing (a partial file-like with only ``read``);
    - the call raises ``NotImplementedError`` or ``UnsupportedOperation``
      (``io.RawIOBase`` advertises ``readinto`` but the default raises;
      some duck-typed objects advertise it and then refuse).

    A native ``readinto`` that returns ``None`` is the non-blocking empty
    case and raises ``BlockingIOError``, matching :meth:`BinaryIOWrapper.read`.

    Implementations must raise before writing into ``b``. A ``readinto`` that
    consumes from the source and then refuses has already lost those bytes;
    falling back to ``read`` would deliver the *next* ones as if they were
    first. That is not detectable here at all: a ``readinto`` that consumed
    without writing leaves no trace in ``b`` (``io.RawIOBase``'s default and
    the advertised-then-refuse file-likes raise before touching the buffer).
    """
    readinto = getattr(stream, "readinto", None)
    if readinto is None:
        return None
    try:
        n = readinto(b)
    except (NotImplementedError, io.UnsupportedOperation):
        # See docstring: raise-before-write is a contract, not something this
        # except can verify.
        return None
    if n is None:
        raise BlockingIOError(_BLOCKING_READ_MESSAGE)
    return n


def readinto_via_read(src: ReadableStream, b: "WriteableBuffer") -> int:
    """Fill ``b`` from ``src.read``, for streams that have no ``readinto``.

    Copies at most ``len(b)`` bytes. A ``read`` that returns more than the
    buffer is a contract violation: those extra bytes are already consumed and
    cannot be delivered without losing them, so this raises ``ValueError``
    rather than truncating.

    That ``ValueError`` means the *source object* is broken, not that a
    payload is damaged. Callers that translate ``ValueError`` into
    archive-corruption errors must carve this out, the way a closed-handle
    ``ValueError`` already is. Not reachable through the routes exercised
    today (seekable ZIP, streaming TAR): slicers and full-count gathers clamp
    the request before it reaches this helper.
    """
    mv = memoryview(b).cast("B")
    data = src.read(len(mv))
    if len(data) > len(mv):
        raise ValueError(
            f"read({len(mv)}) returned {len(data)} bytes; the excess is already consumed "
            "and cannot be delivered without losing it"
        )
    mv[: len(data)] = data
    return len(data)


def read_exact(stream: ReadableStream, n: int) -> bytes:
    """Read up to ``n`` bytes, treating a short non-empty return as "ask again".

    Stops only on empty (EOF) or once ``n`` bytes are gathered. That is the
    ``io.RawIOBase`` contract: a short chunk is not a terminal signal.

    This is the *exception*, not the default. Most bounded reads in the stream
    layer issue a plain ``inner.read(n)``, because their inner is full-count
    (fill-or-EOF) and a short return there is a terminal signal to forward, not
    to retry — see ADR 0014 and the comment in ``SlicingStream.read``. Use this
    only where the caller will not read again, so a short must be gathered here.
    """
    if n < 0:
        raise ValueError("n must be non-negative")

    data = bytearray()
    while len(data) < n:
        chunk = stream.read(n - len(data))
        if not chunk:
            break
        data.extend(chunk)
    return bytes(data)


def _is_fifo_or_chardev(stream: Any) -> bool:
    """Whether ``stream`` is backed by an OS pipe/FIFO or character device.

    Such objects are never randomly seekable, yet some lie about it (see
    :func:`is_seekable`). We check the file *type* via ``fstat`` rather than probing with
    ``seek()``. Returns ``False`` for anything without a real OS file descriptor (``BytesIO``,
    codec wrappers, network responses), whose ``fileno()`` raises.
    """
    fileno = getattr(stream, "fileno", None)
    if fileno is None:
        return False
    try:
        mode = os.fstat(fileno()).st_mode
    except (OSError, ValueError, io.UnsupportedOperation):
        return False
    return stat.S_ISFIFO(mode) or stat.S_ISCHR(mode)


_BUFFER_TYPES = (io.BufferedReader, io.BufferedRandom)


def is_seekable(stream: Any) -> bool:
    """Whether ``stream`` can actually seek.

    Unwraps ``BufferedReader`` / ``BufferedRandom`` so a detached buffer
    (``raw is None`` after ``detach()``) is treated as non-seekable rather than
    raising. ``BufferedReader.seekable()`` already forwards to the raw, so this
    is not "because the buffer reports True over a non-seekable raw" — that
    claim was wrong. ``fileno()`` also forwards, so the FIFO/char-device
    override below does not need the unwrap.

    Streams that lack a ``seekable()`` method are conservatively treated as
    non-seekable — except known types we can assert statically (``mmap``).

    We deliberately do *not* probe by calling ``seek()``: that would make this predicate
    side-effecting (it's called on hot paths and on streams someone is mid-read on), and a
    no-op probe isn't even conclusive. But ``seekable()`` cannot simply be trusted either: a
    Windows ``os.pipe()`` reader reports ``seekable()=True`` while ``seek()`` returns a
    plausible offset *without actually repositioning* (verified by
    ``test_windows_pipe_seek_characterization``) — which would silently corrupt random-access
    reads. So when ``seekable()`` claims ``True`` we confirm the underlying object isn't a
    pipe/FIFO or character device (which are never seekable) and override the claim if it is.

    ``seekable()`` on some stdlib objects is broken rather than missing — notably
    ``tarfile.ExFileObject`` in ``r|`` (streaming) mode, whose ``seekable()`` delegates
    to ``tarfile._Stream`` which has no ``seekable()`` method (``AttributeError``). Those
    member streams are forward-only by design (``r|`` forbids backward seeks), so treating
    them as non-seekable is correct.
    """
    if isinstance(stream, _BUFFER_TYPES):
        raw = stream.raw
        return False if raw is None else is_seekable(raw)
    # mmap is always seekable but (before Python 3.13) exposes no seekable() method and is
    # not an io.IOBase, so the generic check below would miss it.
    if isinstance(stream, mmap.mmap):
        return True
    seekable = getattr(stream, "seekable", None)
    if seekable is None:
        logger.debug(
            "Stream %r has no seekable() method; treating as non-seekable", stream
        )
        return False
    try:
        if not seekable():
            return False
    except AttributeError:
        # e.g. tarfile.ExFileObject in r| mode → tarfile._Stream (no seekable()); see docstring.
        logger.debug(
            "Stream %r seekable() raised AttributeError; treating as non-seekable",
            stream,
        )
        return False
    if _is_fifo_or_chardev(stream):
        logger.debug(
            "Stream %r reports seekable() but is a pipe/char device; treating as "
            "non-seekable (its seek() does not reposition)",
            stream,
        )
        return False
    return True


# Methods/properties a real BinaryIO exposes; used by is_stream() to decide whether an
# object can be passed through unwrapped.
_IO_METHODS = (
    "read",
    "seek",
    "tell",
    "close",
    "readable",
    "writable",
    "seekable",
    "readinto",
)


def is_filename(obj: Any) -> TypeGuard[str | bytes | os.PathLike]:
    """Whether ``obj`` is a path-like (str / bytes / ``os.PathLike``)."""
    return isinstance(obj, (str, bytes, os.PathLike))


def source_name(source: Any) -> str | None:
    """Best-effort human-readable name for a source, for error messages and metadata.

    A path-like source yields its string form; a file-like stream yields its ``name``
    attribute when that is a path (``open()`` sets it, ``BytesIO`` does not). ``open()``
    with a bytes path stores ``name`` as bytes — typeshed notes this — and this
    decodes it with ``os.fsdecode`` rather than widening the return type. An integer
    fd stored as ``name`` yields ``None``.
    """
    if is_filename(source):
        return os.fsdecode(source)
    name = getattr(source, "name", None)
    if isinstance(name, str):
        return name
    if isinstance(name, bytes):
        return os.fsdecode(name)
    return None


def _peel_passthrough(stream: Any) -> Any:
    """Walk opt-in pass-through wrappers so a seek counter does not hide cheap size.

    Only wrappers that set ``peel_for_source_size`` are unwrapped. Transforming
    wrappers (decrypt, BCJ, ``OutputCountingStream``) must not opt in — their
    cheap size is not the inner file's. The peel is for the cheapness decision
    and metadata; :func:`source_byte_size` still I/Os the original wrapper on
    the ``SEEK_END`` fallback so the counter sees those seeks.
    """
    seen: set[int] = set()
    while getattr(stream, "peel_for_source_size", False) is True:
        ident = id(stream)
        if ident in seen:
            break
        seen.add(ident)
        inner = getattr(stream, "_inner", None)
        if inner is None:
            break
        stream = inner
    return stream


def _metadata_end_size(stream: Any) -> int | None:
    """Byte size from metadata that does not move the handle, else ``None``.

    ``BufferedRandom`` is excluded: only ``SEEK_END`` flushes a pending write, so
    ``fstat`` would under-report. Block/character devices report ``st_size == 0``;
    those fall through to the seek probe.
    """
    if isinstance(stream, io.BufferedRandom):
        return None
    target = stream
    if isinstance(stream, io.BufferedReader):
        raw = stream.raw
        if raw is not None:
            target = raw
    if isinstance(target, io.BytesIO):
        view = target.getbuffer()
        try:
            return view.nbytes
        finally:
            view.release()
    if isinstance(target, mmap.mmap):
        return len(target)
    if isinstance(target, io.FileIO):
        try:
            st = os.fstat(target.fileno())
        except (OSError, ValueError):
            return None
        if stat.S_ISREG(st.st_mode):
            return st.st_size
        return None
    return None


def _seek_end_is_cheap(stream: Any) -> bool:
    """Whether ``SEEK_END`` on ``stream`` is O(1) — never a decompression or scan.

    Whitelist only: ``BytesIO``, ``FileIO``, ``mmap``, and those under a buffer.
    Duck checks (``fileno`` + ``S_ISREG``) are unsafe — ``GzipFile`` forwards
    ``fileno`` to the compressed file. Anything else advertises size via
    ``size`` or ``try_get_size()``.
    """
    if isinstance(stream, (io.BytesIO, io.FileIO, mmap.mmap)):
        return True
    inner = _under_buffer(stream)
    if inner is not stream:
        return _seek_end_is_cheap(inner)
    return False


def _under_buffer(stream: Any) -> Any:
    """The stream a ``BufferedReader``/``BufferedRandom`` wraps, for metadata probes.

    A buffer forwards the I/O methods and nothing else, so a wrapped stream's ``size`` /
    ``try_get_size()`` would vanish the moment the source boundary buffered it (see
    :func:`ensure_full_count_reads`) — and with it the cheap source size a nested
    ``open_archive(reader.open("inner.zip"))`` reports. Both probes leave the wrapped
    stream's read position where they found it, so consulting them through the buffer
    cannot desync it. Probe 4's metadata path peels a ``BufferedReader`` the same way;
    a ``SEEK_END`` fallback still runs on the buffer itself (it flushes ``BufferedRandom``).
    ``raw is None`` after ``detach()`` — this module's :class:`_NonClosingBufferedReader`
    — returns ``stream``.
    """
    if isinstance(stream, _BUFFER_TYPES):
        raw = stream.raw
        if raw is not None:  # None once detached (see _NonClosingBufferedReader)
            return raw
    return stream


def source_byte_size(source: Any) -> int | None:
    """Total byte size of a path or stream source when **cheaply** knowable, else ``None``.

    Cheap means no data is read or decompressed. Probe order:

    1. a path-like is ``stat``-ed;
    2. an integer ``size`` attribute is trusted — the fsspec convention, also exposed
       by archivey's own wrappers (``ArchiveStream``, ``SlicingStream``) when they
       know their length;
    3. a ``try_get_size()`` method (archivey's decompressor streams) is called — it
       answers from an index/trailer scan or returns ``None``, and its presence marks
       the stream as one whose ``SEEK_END`` may decompress, so its answer is final
       (no fall-through to the probe);
    4. a metadata end-size that does not move the handle (``fstat`` on a regular
       file, ``len(mmap)``, ``BytesIO.getbuffer().nbytes``), falling back to a
       ``SEEK_END``/restore round trip only for types whose end-seek is provably
       O(1) *and* whose metadata would lie (``BufferedRandom`` with an unflushed
       write; a block/character device). Probe 4 runs only on a seekable source:
       a whitelisted type that reports ``seekable() is False`` still yields
       ``None`` (the total size would overstate what a forward-only,
       already-consumed stream has left). An unrecognized seekable stream could
       be a decompressor whose end-seek decodes the entire payload, so it yields
       ``None`` instead.

    Probes 2 and 3 look through a ``BufferedReader`` to the stream it wraps
    (:func:`_under_buffer`). Probe 4's metadata path does too. Pass-through
    wrappers that set ``peel_for_source_size`` are peeled for the cheapness
    decision and the metadata read; the ``SEEK_END`` fallback still I/Os the
    original wrapper so a seek counter on the outside sees those two calls.
    """
    if is_filename(source):
        try:
            return os.stat(source).st_size
        except OSError:
            return None
    outer = source
    peeled = _peel_passthrough(source)
    metadata_source = _under_buffer(peeled)
    size = getattr(metadata_source, "size", None)
    if isinstance(size, int) and not isinstance(size, bool):
        return size
    try_get_size = getattr(metadata_source, "try_get_size", None)
    if callable(try_get_size):
        result = try_get_size()
        return result if isinstance(result, int) else None
    if not is_seekable(outer):
        return None
    metadata_end = _metadata_end_size(peeled)
    if metadata_end is not None:
        return metadata_end
    if _seek_end_is_cheap(peeled):
        try:
            pos = outer.tell()
            end = outer.seek(0, io.SEEK_END)
            outer.seek(pos)
        except OSError:
            return None
        return end
    return None


def is_stream(obj: Any) -> TypeGuard[BinaryIO]:
    """Whether ``obj`` already satisfies the ``BinaryIO`` interface we rely on.

    ``io.RawIOBase`` / ``io.BufferedIOBase`` instances qualify directly.
    ``io.TextIOBase`` (``TextIOWrapper``, ``StringIO``) does not — ``read()``
    returns ``str``, not ``bytes``. Anything else must expose the full method
    set in :data:`_IO_METHODS` and a ``closed`` attribute.
    """
    if isinstance(obj, io.TextIOBase):
        return False
    if isinstance(obj, io.IOBase):
        return True
    if is_filename(obj):
        return False
    if not all(callable(getattr(obj, m, None)) for m in _IO_METHODS):
        return False
    return hasattr(obj, "closed")


def raise_if_text_stream(obj: Any) -> None:
    """Raise :class:`TypeError` if ``obj`` is a text-mode stream (``io.TextIOBase``).

    Public openers call this *before* the generic "unsupported source type" path so
    a text handle gets the same message whether it arrived bare, in a list, or via
    ``open_stream``.
    """
    if isinstance(obj, io.TextIOBase):
        raise TypeError(
            f"{type(obj).__name__} is a text-mode stream; a binary source is required "
            f"(open the file with mode 'rb')"
        )


class BinaryIOWrapper(io.RawIOBase, BinaryIO):
    """Adapt an object that exposes a *partial* file API to a read-only ``BinaryIO``.

    Most streams never need this. Every stdlib **binary** stream — ``open()`` in
    ``"rb"`` (``BufferedReader`` / ``FileIO``), ``BytesIO``, ``GzipFile`` /
    ``BZ2File`` / ``LZMAFile``, zipfile's ``ZipExtFile``, tarfile's
    ``ExFileObject`` — and modern network responses (``http.client.HTTPResponse``,
    ``urllib3>=2`` ``HTTPResponse``) subclass ``io.RawIOBase`` or
    ``io.BufferedIOBase``, so :func:`is_stream` passes them through unwrapped.
    A text-mode handle (``io.TextIOBase``) is not a binary stream: :func:`is_stream`
    returns ``False`` and :func:`ensure_binaryio` raises ``TypeError``.

    Wrapping is for objects that implement a few file methods *without* subclassing
    ``io.IOBase``, so the type checker won't accept them as ``BinaryIO`` and they may be
    missing methods we rely on. Concretely:

    - ``urllib3<2`` ``HTTPResponse`` (``requests``' ``response.raw`` on older installs):
      has ``read()`` but is not an ``io.IOBase`` and historically lacked ``readinto`` /
      ``seekable``.
    - ``py7zr`` / ``rarfile`` member handles (Phase 7) and arbitrary user objects exposing
      just ``read()`` (and maybe ``seek()``).

    Delegation is plain (each method forwards to ``self._raw``) rather than rebinding
    methods onto the instance: rebinding saves one attribute lookup per call but mutates the
    instance and defeats type checking, for no measurable gain on the large reads that
    matter. The wrapper deliberately does **not** close the wrapped object (it is often a
    temporary view onto a stream someone else owns).
    """

    def __init__(self, raw: Any) -> None:
        super().__init__()
        self._raw = raw

    def read(self, size: int = -1, /) -> bytes:
        data = self._raw.read(size)
        if data is None:
            # A read() returns None only for a *non-blocking* stream that has no data
            # available right now — never at EOF, where blocking and non-blocking streams
            # alike return b"". archivey's readers pull synchronously and cannot make
            # progress on a non-blocking source, so surface that explicitly instead of
            # fabricating b"" (which would look like EOF and silently truncate the data).
            raise BlockingIOError(_BLOCKING_READ_MESSAGE)
        return data

    def readinto(self, b: "WriteableBuffer", /) -> int:
        n = try_readinto(self._raw, b)
        if n is not None:
            return n
        return readinto_via_read(self, b)

    def write(self, data: Any, /) -> int:
        # Must exist and raise UnsupportedOperation: io.RawIOBase.write raises
        # NotImplementedError, which is the wrong exception for a read-only stream.
        raise io.UnsupportedOperation("write")

    def readable(self) -> bool:
        # Prefer the stream's own answer; fall back to "does it expose a reader?" for
        # partial file-likes that don't implement readable().
        raw_readable = getattr(self._raw, "readable", None)
        if raw_readable is not None:
            return bool(raw_readable())
        return hasattr(self._raw, "read") or hasattr(self._raw, "readinto")

    def writable(self) -> bool:
        return False

    @property
    def mode(self) -> str:
        """Always ``"rb"``.

        ``typing.BinaryIO`` is in the MRO, and ``typing.IO.mode`` is a real
        runtime property whose body returns ``None``. pycdlib does
        ``'b' not in fp.mode``, which raises ``TypeError`` on that ``None``.
        Copied from :class:`ReadOnlyIOStream` rather than subclassing it
        (``base.py`` imports this module). A mixin defined here could be
        shared with that class and ``PeekableStream``; parked as #329 C4.
        This class must stay an ``io.RawIOBase`` so :func:`ensure_bufferedio`
        can feed ``io.BufferedReader``.
        """
        return "rb"

    @property
    def name(self) -> str:
        """Path of the wrapped stream, or raise :exc:`AttributeError` if it has none.

        Same ``typing.BinaryIO`` trap as :meth:`mode`: the inherited ``name``
        returns ``None``, so ``hasattr`` is true and pycdlib's Windows
        ``fp.name.startswith(...)`` crashes. Raising keeps ``hasattr`` false
        for nameless streams; a real path is forwarded.
        """
        resolved = source_name(self._raw)
        if resolved is not None:
            return resolved
        raise AttributeError("name")

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        seek = getattr(self._raw, "seek", None)
        if seek is None:
            raise io.UnsupportedOperation("seek")
        pos = seek(offset, whence)
        if pos is not None:
            return pos
        # Some objects' seek() returns None instead of the new position (mmap before Python
        # 3.13); recover it so we honour the BinaryIO -> int contract. tell() gives the true
        # absolute position for any whence; without it, only a SEEK_SET offset is the
        # resulting position (a SEEK_CUR/SEEK_END result is unknowable, so don't guess).
        tell = getattr(self._raw, "tell", None)
        if tell is not None:
            return tell()
        if whence == io.SEEK_SET:
            return offset
        # We don't expect this to be reachable: every io.IOBase defines tell(), and a
        # duck-typed object that implements seek() conventionally implements tell() too. If
        # it happens, a position-tracking fallback could be added — but we want to hear
        # about the real stream type first rather than guess a position.
        raise io.UnsupportedOperation(
            f"cannot report the position after a relative/end seek on "
            f"{type(self._raw).__name__}: its seek() returned None and it has no tell(). "
            f"This is unexpected — please report it (with the stream type) to archivey."
        )

    def tell(self, /) -> int:
        tell = getattr(self._raw, "tell", None)
        if tell is None:
            raise io.UnsupportedOperation("tell")
        return tell()

    def seekable(self) -> bool:
        return is_seekable(self._raw)

    def close(self) -> None:
        # Do NOT close the wrapped stream. Like _NonClosingBufferedReader below, this
        # wrapper adapts a stream the *caller* owns; archivey must never close a stream the
        # user handed it. A plain RawIOBase.close() wouldn't touch self._raw anyway, but the
        # point is deliberate: closing happens implicitly — via a `with` block or GC
        # finalization of this wrapper — and must not take the caller's stream down with it.
        # super().close() only marks this wrapper closed.
        super().close()

    def __repr__(self) -> str:
        return f"BinaryIOWrapper({self._raw!r})"


def ensure_binaryio(obj: Any) -> BinaryIO:
    """Return ``obj`` as a ``BinaryIO``, wrapping it only if it doesn't already qualify.

    The result is a valid ``BinaryIO`` but not necessarily an ``io.RawIOBase`` (an
    already-qualifying ``BytesIO`` is a ``BufferedIOBase``, returned unchanged). Callers
    that specifically need a ``RawIOBase`` — e.g. to feed ``io.BufferedReader`` — should use
    :func:`ensure_bufferedio`, which handles that requirement internally.
    """
    raise_if_text_stream(obj)
    if is_stream(obj):
        return obj
    logger.debug(
        "Wrapping %r in BinaryIOWrapper to satisfy the BinaryIO interface", obj
    )
    return BinaryIOWrapper(obj)


class _NonClosingBufferedReader(io.BufferedReader):
    """A ``BufferedReader`` that detaches instead of closing its raw stream.

    A normal ``io.BufferedReader`` closes its underlying raw stream when the buffer itself
    is closed — and that close is usually *implicit*: leaving a ``with`` block, or the
    reader being finalized by GC. When we temporarily buffer a **caller-owned** stream
    (e.g. to peek a header), that would close a stream archivey doesn't own, breaking the
    caller's later reads. Detaching on close severs the link to the raw stream first, so
    closing the buffer leaves the source open and usable. (See
    ``test_ensure_bufferedio_does_not_close_raw_source`` and its plain-``BufferedReader``
    contrast for the behaviour this guards against.)
    """

    def close(self) -> None:
        # detach() makes IOBase.closed raise, so a second close cannot consult
        # it. .raw returns None after a *direct* detach() without raising —
        # that is the guard, not super().closed. Set the flag *after* detach
        # so a raising detach cannot claim the raw is gone while it is still
        # attached. getattr: no __init__ override, so the attribute may not
        # exist yet.
        if getattr(self, "_detached", False):
            return
        if self.raw is not None:
            self.detach()
        self._detached = True

    @property
    def closed(self) -> bool:
        return getattr(self, "_detached", False) or self.raw is None or super().closed


def ensure_bufferedio(obj: Any) -> io.BufferedIOBase:
    """Return ``obj`` as a buffered reader, without taking ownership of it.

    An already-buffered stream is returned unchanged; otherwise it is wrapped in a
    non-closing ``BufferedReader``. ``io.BufferedReader`` requires its underlying object to
    be an ``io.RawIOBase`` (it rejects a merely stream-like object), so a non-``RawIOBase``
    source is first adapted via :class:`BinaryIOWrapper` (which *is* a ``RawIOBase``) — this
    is why we branch on ``RawIOBase`` here rather than calling :func:`ensure_binaryio`,
    whose result may be a ``BufferedIOBase`` that ``BufferedReader`` would reject.

    A ``RawIOBase`` whose ``readinto`` is the ``RawIOBase`` default (raises
    ``NotImplementedError``) is also wrapped: ``BufferedReader.read(n)`` drives
    ``readinto``, and a class that only implemented ``read()`` would otherwise
    raise a bare ``NotImplementedError`` from the buffer. A class that
    *overrides* ``readinto`` and then refuses is **not** wrapped — that is the
    MRO probe's trade-off (see the comment at the branch).
    """
    raise_if_text_stream(obj)
    if isinstance(obj, io.BufferedIOBase):
        return obj
    raw: io.RawIOBase
    # Do not *call* readinto to decide. A zero-length probe still runs work on
    # ReadOnlyIOStream subclasses (a pending solid member skip-decodes;
    # ArchiveStream opens). Compare the unbound method: the RawIOBase default
    # raises NotImplementedError, so wrap those.
    #
    # A class that *overrides* readinto and then refuses is not wrapped. The
    # buffer raises that refusal at read time. The previous call-based probe
    # caught this via BinaryIOWrapper; it was given up so wrap-time cannot
    # skip-decode a pending solid member. Parked as #329 C1.
    if (
        isinstance(obj, io.RawIOBase)
        and type(obj).readinto is not io.RawIOBase.readinto
    ):
        raw = obj
    else:
        raw = BinaryIOWrapper(obj)
    return _NonClosingBufferedReader(raw)


def ensure_full_count_reads(stream: BinaryIO) -> BinaryIO:
    """Make a source's ``read(n)`` return the full count short of EOF.

    ``io.RawIOBase.read(n)`` is an *up-to-n* contract and real sources use the
    latitude, but header parsers — archivey's own and the stdlib's
    (``zipfile``/``tarfile``/``pycdlib``) — pull a fixed-size structure with one
    ``read(n)`` and read anything shorter as EOF, so a healthy archive from a
    short-returning source was reported as corrupt.

    A **seekable** source is wrapped in ``io.BufferedReader``, whose ``read``
    promises the full count. Its readahead is bounded and recoverable — the
    over-read stays in the buffer and the source can be repositioned anyway — and
    it also collapses the parsers' many tiny reads. Already-buffered sources
    (``open()``'s ``BufferedReader``, ``BytesIO``) pay nothing.

    A **non-seekable** source is returned unchanged, and that is a known gap, not
    a design: this function does not currently honour its contract for pipes and
    sockets. Two corrections to the reasons previously given here:

    * It is *not* because buffering would make the source look seekable.
      ``BufferedReader.seekable()`` forwards to the raw (see :func:`is_seekable`),
      so a buffered pipe still reports ``False``.
    * It is *not* what ADR 0010 (``no-silent-buffer-nonseekable``) forbids either.
      That rule is about faking seekability and materializing a pipe into memory
      or a temp file, neither of which a full-count wrapper does.

    The real obstacle is narrower: ``BufferedReader`` reads *ahead*, and from a
    pipe that over-read is unrecoverable — ``_NonClosingBufferedReader`` detaches
    on close and strands whatever it pulled. So the buffer cannot simply be
    applied to both branches.

    Until it is fixed, the guarantee on the non-seekable path comes from
    downstream layers this function does not own, and they do not cover the same
    ground: ``PeekableStream`` (only when detection runs — an explicit ``format=``
    skips that wrap), ``ensure_bufferedio`` inside ``DecompressorStream`` (only
    when a codec is in the chain), and, for a plain non-seekable TAR, stdlib
    ``tarfile._Stream``'s own buffering. The last one is why no test fails today.

    The fix is a ``FullCountStream`` that gathers with :func:`read_exact` — asking
    only for the bytes still missing, so it consumes exactly what was requested
    and buffers nothing — applied to the non-seekable branch. Tracked in the
    ``full-count-non-seekable-sources`` OpenSpec change; do not paper over this
    with a ``BufferedReader`` on both branches.

    Why the boundary is here, and the listing-amplification measurement, live
    in the archived ``short-read-source-contract`` change.
    """
    raise_if_text_stream(stream)
    if not is_seekable(stream):
        return stream
    # BufferedIOBase is a BinaryIO at runtime; typeshed models the two separately.
    return cast("BinaryIO", ensure_bufferedio(stream))
