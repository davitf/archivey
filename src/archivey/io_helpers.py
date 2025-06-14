import io
import logging
from dataclasses import dataclass, field
from typing import IO, Any, BinaryIO, Callable, Optional, cast

from archivey.exceptions import ArchiveError

logger = logging.getLogger(__name__)


class ErrorIOStream(io.RawIOBase, BinaryIO):
    """A stream that raises an exception on read operations."""

    def __init__(self, exc: Exception):
        """Initialize the error stream."""
        self._exc = exc

    def read(self, size: int = -1) -> bytes:
        """Raise the stored exception."""
        raise self._exc

    def write(self, b: bytes) -> int:
        """Raise the stored exception."""
        raise self._exc

    def readable(self) -> bool:
        return True  # pragma: no cover - trivial

    def writable(self) -> bool:
        return False  # pragma: no cover - trivial

    def seekable(self) -> bool:
        return False  # pragma: no cover - trivial


class ExceptionTranslatingIO(io.RawIOBase, BinaryIO):
    """A wrapper around an IO object that translates exceptions during operations.

    Args:
        inner: The inner IO object to wrap
        exception_translator: A function that takes an exception and returns either
            a translated exception or None (in which case the original exception is raised)
    """

    def __init__(
        self,
        # TODO: can we reduce the number of types here?
        inner: BinaryIO | io.IOBase | Callable[..., BinaryIO | io.IOBase] | IO[bytes],
        exception_translator: Callable[[Exception], Optional[ArchiveError]],
    ):
        super().__init__()
        self._translate = exception_translator
        self._inner: BinaryIO | io.IOBase | IO[bytes] | None = None

        if isinstance(inner, Callable):
            try:
                self._inner = inner()
            except Exception as e:
                self._translate_exception(e)
        else:
            self._inner = inner

    def _translate_exception(self, e: Exception) -> None:
        translated = self._translate(e)
        if translated is not None:
            raise translated from e

        if not isinstance(e, ArchiveError):
            logger.error(f"Unknown exception when reading IO: {e}", exc_info=e)
        raise e

    def read(self, n: int = -1) -> bytes:
        assert self._inner is not None
        try:
            logger.info(
                f"Reading {self._inner} {n} bytes, current tell(): {self._inner.tell()}"
            )
            return self._inner.read(n)
        except Exception as e:
            self._translate_exception(e)
            return b""  # pragma: no cover - unreachable, _translate_exception always raises

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        assert self._inner is not None
        try:
            logger.info(
                f"Seeking {self._inner} to {offset} whence={whence}, current tell(): {self._inner.tell()}"
            )
            return self._inner.seek(offset, whence)
        except Exception as e:
            logger.error(f"Exception when seeking {self._inner}: {e}", exc_info=e)
            self._translate_exception(e)
            return (
                0  # pragma: no cover - unreachable, _translate_exception always raises
            )

    def tell(self) -> int:
        assert self._inner is not None
        return self._inner.tell()

    def readable(self) -> bool:
        assert self._inner is not None
        return self._inner.readable()

    def writable(self) -> bool:
        assert self._inner is not None
        return self._inner.writable()

    def seekable(self) -> bool:
        assert self._inner is not None
        return self._inner.seekable()

    def write(self, b: Any) -> int:
        assert self._inner is not None
        try:
            return self._inner.write(b)
        except Exception as e:
            self._translate_exception(cast(Exception, e))
            return (
                0  # pragma: no cover - unreachable, _translate_exception always raises
            )

    def writelines(self, lines: Any) -> None:
        assert self._inner is not None
        try:
            self._inner.writelines(lines)
        except Exception as e:
            self._translate_exception(cast(Exception, e))

    def close(self) -> None:
        try:
            if self._inner is not None:
                self._inner.close()
        except Exception as e:
            self._translate_exception(cast(Exception, e))
        super().close()

    def __str__(self) -> str:
        return f"ExceptionTranslatingIO({self._inner!s})"

    def __repr__(self) -> str:
        return f"ExceptionTranslatingIO({self._inner!r})"


class LazyOpenIO(io.RawIOBase, BinaryIO):
    """A wrapper that defers opening of the underlying stream until needed.

    Args:
        open_fn: Function used to open the underlying stream.
        seekable: Optional hint whether the underlying stream is seekable.
            If provided, ``seekable()`` will return this value without opening
            the stream.
    """

    def __init__(
        self,
        open_fn: Callable[..., IO[bytes]],
        *args: Any,
        seekable: bool,
        **kwargs: Any,
    ) -> None:
        super().__init__()
        self._open_fn = open_fn
        self._args = args
        self._kwargs = kwargs
        self._inner: IO[bytes] | None = None
        self._seekable = seekable

    def _ensure_open(self) -> IO[bytes]:
        if self.closed:
            raise ValueError("I/O operation on closed file.")
        if self._inner is None:
            self._inner = self._open_fn(*self._args, **self._kwargs)
        return self._inner

    # ------------------------------------------------------------------
    # Basic IO methods
    # ------------------------------------------------------------------
    def read(self, n: int = -1) -> bytes:
        return self._ensure_open().read(n)

    def readable(self) -> bool:
        return True  # pragma: no cover - trivial

    def writable(self) -> bool:
        return False  # pragma: no cover - trivial

    def seekable(self) -> bool:
        return self._seekable  # pragma: no cover - trivial

    def close(self) -> None:  # pragma: no cover - simple delegation
        if self._inner is not None:
            self._inner.close()
        super().close()

    def __enter__(self) -> "LazyOpenIO":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


@dataclass
class IOStats:
    """Simple container for I/O statistics."""

    bytes_read: int = 0
    seek_calls: int = 0
    read_ranges: list[list[int]] = field(default_factory=lambda: [[0, 0]])


class StatsIO(io.RawIOBase, BinaryIO):
    """Wraps another IO object and tracks read/seek statistics."""

    def __init__(self, inner: BinaryIO, stats: IOStats) -> None:
        super().__init__()
        self._inner = inner
        self.stats = stats

    # Basic IO methods -------------------------------------------------
    def read(self, n: int = -1) -> bytes:
        data = self._inner.read(n)
        self.stats.bytes_read += len(data)
        self.stats.read_ranges[-1][1] += len(data)
        return data

    def readinto(self, b: bytearray | memoryview) -> int:  # type: ignore[override]
        if isinstance(self._inner, io.BufferedIOBase):
            n = self._inner.readinto(b)
        else:
            data = self._inner.read(len(b))
            assert len(data) <= len(b)
            b[: len(data)] = data
            n = len(data)

        self.stats.bytes_read += n
        self.stats.read_ranges[-1][1] += n
        return n

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        self.stats.seek_calls += 1
        newpos = self._inner.seek(offset, whence)
        self.stats.read_ranges.append([newpos, 0])
        return newpos

    def readable(self) -> bool:  # pragma: no cover - trivial
        return self._inner.readable()

    def writable(self) -> bool:  # pragma: no cover - trivial
        return self._inner.writable()

    def seekable(self) -> bool:  # pragma: no cover - trivial
        return self._inner.seekable()

    def write(self, b: Any) -> int:  # pragma: no cover - simple delegation
        return self._inner.write(b)

    def close(self) -> None:  # pragma: no cover - simple delegation
        self._inner.close()
        super().close()

    # Delegate unknown attributes --------------------------------------
    def __getattr__(self, item: str) -> Any:  # pragma: no cover - simple
        return getattr(self._inner, item)

    # Context manager support -----------------------------------------
    def __enter__(self) -> "StatsIO":  # pragma: no cover - trivial
        self._inner.__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:  # pragma: no cover - trivial
        self._inner.__exit__(exc_type, exc_val, exc_tb)
        self.close()
