"""Shared helpers for the Phase-2 stream-layer tests."""

from __future__ import annotations

import functools
import io
import lzma
import random
import shutil
import struct
import subprocess
import sys
import zlib
from collections.abc import Callable
from typing import BinaryIO

import pytest


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


class SizedNonSeekable(NonSeekableBytesIO):
    """A non-seekable stream with an fsspec-style ``size`` attribute it sets itself.

    ``size`` is the caller's claim and need not match ``len(data)``: a test can
    inflate it to check that nothing trusts a claim it cannot verify.
    """

    def __init__(self, data: bytes, size: int) -> None:
        super().__init__(data)
        self.size = size


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
    over-read (a ``BufferedReader`` in front of this double takes
    ``io.DEFAULT_BUFFER_SIZE`` for a ``read(20)`` — 8 KiB through 3.13,
    128 KiB from 3.14).

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

    ``tell()`` still answers, matching :class:`NonSeekableBytesIO`:
    ``ConcatenatedFile`` probes ``tell()`` then ``seek()`` on ``BinaryIO``
    volumes, so a double that raised on both would test a different refusal
    path. Path volumes are sized with ``stat`` and are not probed.
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


class FactSizedReadRecorder(io.BytesIO):
    """A ``BytesIO`` that records the size asked of every ``read`` / ``readinto``.

    Its length is a fact to the source boundary — ``BytesIO``'s own buffer says how
    big it is — so a read against it is clamped to what is left, where the same bytes
    behind :class:`ReadSizeRecorder`'s ``size`` attribute, a caller's unverified claim,
    are only stepped.
    """

    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self.requested: list[int] = []

    def read(self, n: int | None = -1, /) -> bytes:
        self.requested.append(-1 if n is None else n)
        return super().read(n)

    def readinto(self, b, /) -> int:  # type: ignore[override]  # test double
        self.requested.append(len(b))
        return super().readinto(b)


class ReadSizeRecorder(io.RawIOBase):
    """A seekable in-memory stream that records the size asked of every ``read``.

    With ``advertise_size`` (the default), ``size`` is the fsspec convention
    :func:`source_byte_size` honours, so the reader gets the stream's length the same
    cheap way it gets a file's. Without it the attribute is absent, which is what an
    ordinary caller-supplied seekable file-like looks like: seekable, but not a path,
    not one of the types ``source_byte_size`` will end-seek, and so unmeasurable
    cheaply. The two send a backend down different branches of its bound, and a guard
    tested only on the advertised one is untested on the branch every compressed
    source takes.
    """

    def __init__(self, data: bytes, *, advertise_size: bool = True) -> None:
        super().__init__()
        self._inner = io.BytesIO(data)
        if advertise_size:
            self.size = len(data)
        self.requested: list[int] = []

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def read(self, n: int = -1, /) -> bytes:
        self.requested.append(n)
        return self._inner.read(n)

    def readinto(self, b, /) -> int:  # type: ignore[override]  # test double
        self.requested.append(len(b))
        return self._inner.readinto(b)

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
    drives ``XzDecompressorStream``'s block-resume random-access path) is produced via the
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


@functools.cache
def _hex_text_brotli() -> bytes:
    import brotli

    return brotli.compress(random.Random(0).randbytes(200_000).hex().encode())


def truncated_brotli(size: int) -> bytes:
    """A real compressed-first Brotli stream (about 200 KB) cut to ``size`` bytes.

    Compressed once per process: the compression takes about a quarter of a second.
    """
    compressed = _hex_text_brotli()
    assert len(compressed) > size
    return compressed[:size]


def brotli_compressed_metablock_header(*, first: bool = False) -> bytes:
    """Synthesize a 24-byte meta-block header that classifies as ``COMPRESSED``.

    Used by framing / completeness / SFX tests that need a chain walk to stop at a
    successor link without depending on a real Brotli encoder for that header alone.
    """
    from archivey.internal.streams.codecs.brotli_framing import (
        BrotliBlock,
        parse_metablock,
    )

    for seed in range(4096):
        hdr = bytes([(seed + j * 13) % 256 for j in range(24)])
        if parse_metablock(hdr, first=first).outcome is BrotliBlock.COMPRESSED:
            return hdr
    raise RuntimeError("no compressed meta-block header pattern found")


def _pack_bits(fields: list[tuple[int, int]]) -> bytes:
    """Pack ``(value, width)`` fields LSB-first, as Brotli does, zero-padded to a byte."""
    acc = pos = 0
    for value, width in fields:
        acc |= value << pos
        pos += width
    return acc.to_bytes((pos + 7) // 8, "little")


def brotli_declared_metablock_header(
    length: int, *, metadata: bool = False, first: bool = False
) -> bytes:
    """A non-last uncompressed (or metadata) meta-block header declaring ``length`` bytes.

    ``first`` prepends a one-bit WBITS field (window 16). The header ends byte-aligned,
    so the declared bytes follow it directly (RFC 7932 §9.2).
    """
    fields: list[tuple[int, int]] = [(0, 1)] if first else []
    fields.append((0, 1))  # ISLAST
    if metadata:
        nbytes = (max(length - 1, 1).bit_length() + 7) // 8 if length else 0
        fields += [(3, 2), (0, 1), (nbytes, 2)]  # MNIBBLES = 0, reserved, MSKIPBYTES
        if nbytes:
            fields.append((length - 1, nbytes * 8))
    else:
        nibbles = max(4, ((length - 1).bit_length() + 3) // 4)
        fields += [(nibbles - 4, 2), (length - 1, nibbles * 4), (1, 1)]
    return _pack_bits(fields)


def brotli_link_cap_residual() -> bytes:
    """Random data that every Brotli probe check accepts and a decoder rejects.

    Nine uncompressed meta-blocks of 8 KiB each: the chain walk gives up at its link
    cap (``CHAIN_MAX_LINKS``) and cannot disprove, the source is over the 64 KiB
    completion window, and nothing past the walk is decoded. A garbage compressed
    header follows, so a read copies about 72 KiB and then fails. The first block is
    uncompressed, so detection reports ``BROTLI`` / ``GUESS``.
    """
    from archivey.internal.streams.codecs.brotli_framing import CHAIN_MAX_LINKS

    rng = random.Random(7)
    parts = [
        brotli_declared_metablock_header(8192, first=(i == 0)) + rng.randbytes(8192)
        for i in range(CHAIN_MAX_LINKS + 1)
    ]
    return b"".join(parts) + brotli_compressed_metablock_header() + b"Z" * 32


def assert_seek_underflow_matches_bytesio(stream: BinaryIO) -> None:
    """A relative seek before the start clamps to 0; a negative SEEK_SET is ValueError.

    That is what ``io.BytesIO`` does. A compressed member used to raise
    ``ValueError("Invalid offset")`` on the relative case, which a backend translator
    (ZIP) then reported as ``CorruptionError`` on an undamaged archive.
    """
    content = stream.read()
    # Inside the member: a TAR member's seek past its end returns the member size
    # (dev-docs/known-issues.md), which is not what this test is about.
    start = min(5, len(content))
    assert stream.seek(start) == start
    assert stream.seek(-100, io.SEEK_CUR) == 0
    assert stream.read() == content
    assert stream.seek(-(len(content) + 100), io.SEEK_END) == 0
    assert stream.tell() == 0
    assert stream.read() == content
    with pytest.raises(ValueError) as excinfo:
        stream.seek(-1)
    # The caller's own error, not a translated archive error.
    assert type(excinfo.value) is ValueError
    assert_unknown_whence_is_value_error(stream)
    # The refused seeks did not move the stream.
    assert stream.tell() == len(content)


# Non-integer seek arguments, each refused by ``io.BytesIO`` with a TypeError.
NON_INTEGER_SEEKS: tuple[tuple[object, ...], ...] = (
    (1.5,),
    (None,),
    ("1",),
    (0, 1.5),
    (0, None),
)


def assert_non_integer_seek_is_type_error(stream: BinaryIO, content: bytes) -> None:
    """A float, ``None`` or str offset or whence raises ``TypeError`` as ``io.BytesIO``
    does, with its message, and leaves the stream where it was and correct.

    Passed inward, ``seek(1.5)`` made a codec stream consume a chunk before failing (a
    later ``seek(0)`` read wrong bytes), backends report damage that was not there, and
    the stored paths kept a float position (cross-format K1).
    """

    # Untyped handles: the arguments are wrong on purpose.
    seek: Callable[..., int] = stream.seek
    reference_seek: Callable[..., int] = io.BytesIO(content).seek

    def refuse(at: int) -> None:
        for args in NON_INTEGER_SEEKS:
            with pytest.raises(TypeError) as expected:
                reference_seek(*args)
            with pytest.raises(TypeError) as excinfo:
                seek(*args)
            assert type(excinfo.value) is TypeError, args
            assert str(excinfo.value) == str(expected.value), args
            position = stream.tell()
            assert type(position) is int and position == at, args

    # First on a fresh stream, then after a partial read.
    refuse(0)
    head = min(3, len(content))
    assert stream.read(head) == content[:head]
    refuse(head)
    assert stream.read() == content[head:]
    assert stream.seek(0) == 0
    assert stream.read() == content
    # A bool is an int to io.BytesIO too.
    if content:
        assert stream.seek(True) == 1
        assert stream.read() == content[1:]


# Non-integer read sizes, each refused by ``io.BytesIO`` with a TypeError. ``None``
# is not here: it means read to EOF.
NON_INTEGER_READS: tuple[object, ...] = (1.5, 2.0, "3", b"3")


def assert_non_integer_read_is_type_error(stream: BinaryIO, content: bytes) -> None:
    """A float or str ``read`` size raises ``TypeError`` as ``io.BytesIO`` does, with
    its message, and leaves the stream where it was and correct.

    The read after the refusal is the half that matters. Passed inward, ``read(1.5)``
    failed inside zlib after the compressed chunk had left the source, so an undamaged
    gzip or deflate member then raised ``TruncatedError`` on every read, even after
    ``seek(0)``; a stored member raised a ``TypeError`` and survived.
    """

    # Untyped handles: the arguments are wrong on purpose.
    read: Callable[..., bytes] = stream.read
    reference_read: Callable[..., bytes] = io.BytesIO(content).read

    def refuse(at: int) -> None:
        for size in NON_INTEGER_READS:
            with pytest.raises(TypeError) as expected:
                reference_read(size)
            with pytest.raises(TypeError) as excinfo:
                read(size)
            assert type(excinfo.value) is TypeError, size
            assert str(excinfo.value) == str(expected.value), size
            assert stream.tell() == at, size

    # First on a fresh stream, then after a partial read.
    refuse(0)
    head = min(3, len(content))
    assert stream.read(head) == content[:head]
    refuse(head)
    assert stream.read() == content[head:]
    assert stream.seek(0) == 0
    assert stream.read() == content
    # A bool is an int to io.BytesIO too, and None reads to EOF.
    assert stream.seek(0) == 0
    assert read(True) == content[:1]
    assert read(None) == content[1:]


# Integer read sizes outside ``Py_ssize_t``, each refused by ``io.BytesIO`` with an
# OverflowError before anything is read.
OVERSIZED_READS: tuple[int, ...] = (sys.maxsize + 1, 2**70, -sys.maxsize - 2, -(2**70))


def assert_oversized_read_is_overflow_error(stream: BinaryIO, content: bytes) -> None:
    """An integer ``read`` size too wide for ``Py_ssize_t`` raises ``OverflowError`` as
    ``io.BytesIO`` does, with its message, and leaves the stream where it was and correct.

    The read after the refusal is the half that matters. Passed inward, ``read(2**70)``
    failed inside the gzip, bzip2 or xz decoder after the compressed chunk had left the
    source, so the undamaged member then raised ``TruncatedError`` or
    ``CorruptionError`` on every read, even after ``seek(0)``.
    """

    reference_read = io.BytesIO(content).read

    def refuse(at: int) -> None:
        for size in OVERSIZED_READS:
            with pytest.raises(OverflowError) as expected:
                reference_read(size)
            with pytest.raises(OverflowError) as excinfo:
                stream.read(size)
            assert type(excinfo.value) is OverflowError, size
            assert str(excinfo.value) == str(expected.value), size
            assert stream.tell() == at, size

    # First on a fresh stream, then after a partial read.
    refuse(0)
    head = min(3, len(content))
    assert stream.read(head) == content[:head]
    refuse(head)
    assert stream.read() == content[head:]
    assert stream.seek(0) == 0
    assert stream.read() == content


def assert_unknown_whence_is_value_error(stream: BinaryIO) -> None:
    """An unknown ``whence`` is the caller's ``ValueError`` on every format.

    Without the check in ``ArchiveStream.seek`` the ZIP translator reports it as
    ``CorruptionError`` on an undamaged archive.
    """
    with pytest.raises(ValueError) as excinfo:
        stream.seek(0, 7)
    assert type(excinfo.value) is ValueError
    # The message the shared rule in ``check_seek_args`` gives, so the public layer
    # and the backends behind it cannot drift apart.
    assert str(excinfo.value) == "Invalid whence: 7"
