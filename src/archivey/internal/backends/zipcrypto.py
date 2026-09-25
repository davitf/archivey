"""Traditional ZipCrypto (PKWARE) stream cipher.

The decrypt stage of the ZIP member pipeline (:class:`ZipCryptoDecryptStream`, ahead of
the shared codec layer) and multi-candidate password confirmation for STORED members
(parallel CRC over raw ciphertext). Independent of :mod:`zipfile`'s private
``_ZipDecrypter``.

APPNOTE.txt §6.1: a 96-bit key state seeded from the password, a 12-byte
encryption header whose final plaintext byte is the verification value, then
payload bytes, each XORed with a keystream byte derived from the evolving state.
"""

from __future__ import annotations

import io
import zlib
from collections.abc import Sequence
from typing import BinaryIO

from archivey.internal.streams.streamtools import ReadOnlyIOStream, read_exact

#: Length of the encryption header in front of every ZipCrypto member's data.
ZIPCRYPTO_HEADER_LEN = 12


def _make_crc_table() -> list[int]:
    table = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = (c >> 1) ^ 0xEDB88320 if (c & 1) else (c >> 1)
        table.append(c)
    return table


_CRC_TABLE = _make_crc_table()


def _crc32_update(crc: int, byte: int) -> int:
    return ((crc >> 8) & 0xFFFFFF) ^ _CRC_TABLE[(crc ^ byte) & 0xFF]


class ZipCryptoKeys:
    """The 96-bit ZipCrypto key state."""

    __slots__ = ("k0", "k1", "k2")

    def __init__(self, password: bytes) -> None:
        self.k0, self.k1, self.k2 = 0x12345678, 0x23456789, 0x34567890
        for b in password:
            self.update(b)

    def update(self, byte: int) -> None:
        self.k0 = _crc32_update(self.k0, byte) & 0xFFFFFFFF
        self.k1 = (self.k1 + (self.k0 & 0xFF)) & 0xFFFFFFFF
        self.k1 = (self.k1 * 134775813 + 1) & 0xFFFFFFFF
        self.k2 = _crc32_update(self.k2, (self.k1 >> 24) & 0xFF) & 0xFFFFFFFF

    def keystream_byte(self) -> int:
        temp = (self.k2 | 2) & 0xFFFF
        return ((temp * (temp ^ 1)) >> 8) & 0xFF

    def copy(self) -> ZipCryptoKeys:
        clone = ZipCryptoKeys.__new__(ZipCryptoKeys)
        clone.k0, clone.k1, clone.k2 = self.k0, self.k1, self.k2
        return clone

    def decrypt(self, data: bytes) -> bytes:
        """Decrypt ``data`` and advance the state past it.

        The same arithmetic as :meth:`keystream_byte` and :meth:`update`, inlined over
        local variables: this loop is the whole cost of reading a ZipCrypto member.
        """
        k0, k1, k2 = self.k0, self.k1, self.k2
        table = _CRC_TABLE
        out = bytearray(len(data))
        for i, c in enumerate(data):
            t = k2 | 2
            c ^= ((t * (t ^ 1)) >> 8) & 0xFF
            out[i] = c
            k0 = (k0 >> 8) ^ table[(k0 ^ c) & 0xFF]
            k1 = ((k1 + (k0 & 0xFF)) * 134775813 + 1) & 0xFFFFFFFF
            k2 = (k2 >> 8) ^ table[(k2 ^ (k1 >> 24)) & 0xFF]
        self.k0, self.k1, self.k2 = k0, k1, k2
        return bytes(out)


def keys_after_header(
    password: bytes, header_ciphertext: bytes
) -> tuple[ZipCryptoKeys, int]:
    """Decrypt the 12-byte header; return the state after it and its last plaintext byte.

    The last byte is the check byte a correct password reproduces.
    """
    keys = ZipCryptoKeys(password)
    plain = keys.decrypt(header_ciphertext[:ZIPCRYPTO_HEADER_LEN])
    return keys, plain[-1]


def password_matches_check_byte(
    password: bytes, header_ciphertext: bytes, check_byte: int
) -> bool:
    """Whether ``password`` satisfies ZipCrypto's 1-byte header check.

    ``header_ciphertext`` must be the first 12 encrypted bytes of the member.
    """
    if len(header_ciphertext) < ZIPCRYPTO_HEADER_LEN:
        return False
    _, plain_last = keys_after_header(password, header_ciphertext)
    return plain_last == check_byte


def parallel_plaintext_crc32(
    passwords: Sequence[bytes],
    header_ciphertext: bytes,
    body: BinaryIO,
    *,
    chunk_size: int = 64 * 1024,
) -> list[tuple[bytes, int]]:
    """Decrypt ``body`` with each password in parallel; return ``(password, crc32)``.

    ``body`` is the ZipCrypto ciphertext *after* the 12-byte encryption header (e.g. a
    :class:`~archivey.internal.streams.streamtools.slice.SlicingStream` over that range).
    Constant memory beyond the current chunk: one ZipCrypto state and running CRC per
    candidate. Candidate order is preserved (ties resolved by the caller via first match).
    """
    states = [
        keys_after_header(password, header_ciphertext)[0] for password in passwords
    ]
    crcs = [0] * len(states)
    while True:
        chunk = body.read(chunk_size)
        if not chunk:
            break
        for i, keys in enumerate(states):
            crcs[i] = zlib.crc32(keys.decrypt(chunk), crcs[i])
    return [
        (password, crc & 0xFFFFFFFF)
        for password, crc in zip(passwords, crcs, strict=True)
    ]


class ZipCryptoDecryptStream(ReadOnlyIOStream):
    """The plaintext of one ZipCrypto member body: the decrypt stage ahead of the codec.

    ``source`` is the member's payload positioned just after the 12-byte header, ``keys``
    the cipher state after that header (:func:`keys_after_header`), and ``length`` the
    body's declared length (the member's compressed size less the header). Positions
    are plaintext offsets from the start of the body, which is the ciphertext offset
    too: ZipCrypto adds no bytes past the header.

    The cipher state depends on every earlier byte, so a backward seek restarts from
    the saved post-header state and a forward seek decrypts what it skips. The source
    must be seekable for the backward case. ``read(n)`` is full-count: ``n`` bytes or
    what is left before the source ends. Close owns ``source``.
    """

    def __init__(self, source: BinaryIO, keys: ZipCryptoKeys, *, length: int) -> None:
        # First: close() reads it, and IOBase.__del__ runs close() on a refused instance.
        self._source = source
        super().__init__()
        self._length = length
        self._start_keys = keys.copy()
        self._keys = keys
        self._pos = 0
        self._origin = source.tell() if source.seekable() else 0

    def read(self, n: int = -1, /) -> bytes:
        if n == 0:
            return b""
        data = (
            self._source.read() if n is None or n < 0 else read_exact(self._source, n)
        )
        if not data:
            return b""
        self._pos += len(data)
        return self._keys.decrypt(data)

    def seekable(self) -> bool:
        return self._source.seekable()

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        if whence == io.SEEK_SET:
            target = offset
        elif whence == io.SEEK_CUR:
            target = self._pos + offset
        elif whence == io.SEEK_END:
            target = self._length + offset
        else:
            raise ValueError(f"invalid whence ({whence})")
        if target < 0:
            raise ValueError(f"negative seek position {target}")
        if target < self._pos:
            self._source.seek(self._origin)
            self._keys = self._start_keys.copy()
            self._pos = 0
        while self._pos < target:
            if not self.read(min(64 * 1024, target - self._pos)):
                break
        return self._pos

    def close(self) -> None:
        if self.closed:
            return
        try:
            self._source.close()
        finally:
            super().close()
