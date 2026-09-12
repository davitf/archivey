"""Shared helpers for the Phase-2 stream-layer tests."""

from __future__ import annotations

import io
import lzma
import shutil
import struct
import subprocess
import zlib


class NonSeekableBytesIO(io.RawIOBase):
    """A ``BytesIO`` that reports (and behaves as) non-seekable, for forward-only tests."""

    def __init__(self, data: bytes) -> None:
        super().__init__()
        self._inner = io.BytesIO(data)

    def readable(self) -> bool:
        return True

    def read(self, n: int = -1, /) -> bytes:
        return self._inner.read(n)

    def readinto(self, b) -> int:  # type: ignore[override]  # test double; broad buffer type
        return self._inner.readinto(b)

    def seekable(self) -> bool:
        return False

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        raise io.UnsupportedOperation("seek")

    def tell(self, /) -> int:
        return self._inner.tell()


class ShortReadBytesIO(io.RawIOBase):
    """A ``BytesIO`` whose ``read``/``readinto`` never return more than ``max_chunk``.

    ``io.RawIOBase.read(n)`` is documented to return *up to* ``n`` bytes, and real sources
    do: sockets, FUSE mounts, and user-written wrappers hand back short chunks mid-stream
    with no EOF in sight. :class:`NonSeekableBytesIO` and :class:`CountingBytesIO` both
    delegate to ``BytesIO``, which always returns the full count, so neither exercises
    that — which is how short-returning sources stayed invisible until they were reported
    as corrupt archives (see ``test_short_read_sources.py``).

    Seekable by default. That still leaves one axis pair untested: a source that is
    short-returning *and* non-seekable. :class:`NonSeekableBytesIO` is always full-count,
    so it cannot stand in. :class:`ShortReadNonSeekable` is this class with
    ``seekable=False``. The cap lives here so the two cannot drift.

    ``max_chunk=1`` is the worst legal case, and the one that catches a parser reading a
    fixed-size header with a single ``read(n)`` and treating the short return as EOF.

    ``consumed`` counts bytes taken from the inner, so a test can see whether a wrapper
    over-read (a ``BufferedReader`` in front of this double takes 8192 for a ``read(20)``).

    ``cap_drain`` also caps ``read(-1)``. That is deliberately illegal ``RawIOBase``
    behaviour — ``read(-1)`` dispatches to ``readall()``, which must drain — and exists
    only so a full-count wrapper's drain branch can be proved not to depend on the inner
    obeying that.
    """

    def __init__(
        self,
        data: bytes,
        max_chunk: int = 1,
        *,
        seekable: bool = True,
        cap_drain: bool = False,
    ) -> None:
        super().__init__()
        self._inner = io.BytesIO(data)
        self._max_chunk = max_chunk
        self._seekable = seekable
        self._cap_drain = cap_drain
        self.consumed = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return self._seekable

    def read(self, n: int = -1, /) -> bytes:
        # read(-1) means "everything up to EOF"; it has no legal short form, unless
        # cap_drain is set to violate that on purpose (see class docstring).
        if n is None or n < 0:
            data = (
                self._inner.read(self._max_chunk)
                if self._cap_drain
                else self._inner.read()
            )
        else:
            data = self._inner.read(min(n, self._max_chunk))
        self.consumed += len(data)
        return data

    def readinto(self, b) -> int:  # type: ignore[override]  # test double; broad buffer type
        mv = memoryview(b).cast("B")
        data = self.read(len(mv))
        mv[: len(data)] = data
        return len(data)

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        if not self._seekable:
            raise io.UnsupportedOperation("seek")
        return self._inner.seek(offset, whence)

    def tell(self, /) -> int:
        return self._inner.tell()


class ShortReadNonSeekable(ShortReadBytesIO):
    """Short-returning *and* non-seekable.

    :class:`ShortReadBytesIO` is seekable; :class:`NonSeekableBytesIO` delegates to
    ``BytesIO`` and is therefore always full-count. Neither covers this axis pair,
    which is why the non-seekable half of the source-boundary contract went untested.
    """

    def __init__(
        self, data: bytes, max_chunk: int = 1, *, cap_drain: bool = False
    ) -> None:
        super().__init__(data, max_chunk, seekable=False, cap_drain=cap_drain)


class CountingBytesIO(io.RawIOBase):
    """A seekable ``BytesIO`` that counts ``read`` calls and bytes read.

    Used to assert that seeking decompresses only the needed block(s) rather than the
    whole stream from the start.
    """

    def __init__(self, data: bytes) -> None:
        super().__init__()
        self._inner = io.BytesIO(data)
        self.read_calls = 0
        self.bytes_read = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def read(self, n: int = -1, /) -> bytes:
        data = self._inner.read(n)
        if data:
            self.read_calls += 1
            self.bytes_read += len(data)
        return data

    def readinto(self, b) -> int:  # type: ignore[override]  # test double; broad buffer type
        n = self._inner.readinto(b)
        if n:
            self.read_calls += 1
            self.bytes_read += n
        return n

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        return self._inner.seek(offset, whence)

    def tell(self, /) -> int:
        return self._inner.tell()


def make_lzip_member(data: bytes, dict_size_bits: int = 20) -> bytes:
    """Build one lzip member from ``data`` using stdlib ``lzma``.

    lzip omits the 13-byte LZMA_ALONE header from its raw stream; compress with
    FORMAT_ALONE (which includes it), strip the header, then wrap in the lzip
    header/trailer.
    """
    filters: list[dict] = [
        {
            "id": lzma.FILTER_LZMA1,
            "dict_size": 1 << dict_size_bits,
            "lc": 3,
            "lp": 0,
            "pb": 2,
        }
    ]
    compressed_alone = lzma.compress(data, format=lzma.FORMAT_ALONE, filters=filters)
    lzma_raw = compressed_alone[13:]
    header = b"LZIP" + bytes([1, dict_size_bits])
    member_total = len(header) + len(lzma_raw) + 20
    trailer = struct.pack(
        "<IQQ", zlib.crc32(data) & 0xFFFFFFFF, len(data), member_total
    )
    return header + lzma_raw + trailer


def make_multi_member_lzip(parts: list[bytes], dict_size_bits: int = 20) -> bytes:
    return b"".join(make_lzip_member(p, dict_size_bits) for p in parts)


def make_multi_stream_xz(parts: list[bytes]) -> bytes:
    return b"".join(lzma.compress(p, format=lzma.FORMAT_XZ) for p in parts)


def xz_cli_available() -> bool:
    """Whether the ``xz`` CLI is on PATH (needed to build a multi-block XZ stream)."""
    return shutil.which("xz") is not None


def make_multiblock_xz(data: bytes, block_size: int) -> bytes:
    """Compress ``data`` into a *single* XZ stream split into multiple blocks.

    stdlib ``lzma`` always emits one block per stream, so the multi-block layout (which
    drives ``XzDecompressorStream``'s block-chain random-access path) is produced via the
    ``xz`` CLI's ``--block-size``. Guard callers with :func:`xz_cli_available`.
    """
    result = subprocess.run(
        ["xz", "-z", "-c", f"--block-size={block_size}"],
        input=data,
        capture_output=True,
        check=True,
    )
    return result.stdout


def make_unix_compress(data: bytes) -> bytes:
    """LZW-compress ``data`` into a standard unix-compress (`.Z`) stream.

    Fixtures are produced with ``ncompress`` (an LZW *compressor*); Archivey decodes
    natively. Imported lazily so this module still imports in the core-only test leg,
    where ncompress is absent (callers guard with ``requires``).
    """
    import ncompress

    out = io.BytesIO()
    ncompress.compress(io.BytesIO(data), out)
    return out.getvalue()


def lzma2_raw_filters() -> list[dict]:
    """A FORMAT_RAW LZMA2 filter spec, as a 7z folder coder would supply."""
    return [{"id": lzma.FILTER_LZMA2, "preset": 6}]


def compress_lzma2_raw(data: bytes) -> bytes:
    return lzma.compress(data, format=lzma.FORMAT_RAW, filters=lzma2_raw_filters())


def brotli_compressed_metablock_header(*, first: bool = False) -> bytes:
    """Synthesize a 24-byte meta-block header that classifies as ``COMPRESSED``.

    Used by framing / completeness / SFX tests that need a chain walk to stop at a
    successor link without depending on a real Brotli encoder for that header alone.
    """
    from archivey.internal.streams.brotli_framing import BrotliBlock, parse_metablock

    for seed in range(4096):
        hdr = bytes([(seed + j * 13) % 256 for j in range(24)])
        if parse_metablock(hdr, first=first).outcome is BrotliBlock.COMPRESSED:
            return hdr
    raise RuntimeError("no compressed meta-block header pattern found")
