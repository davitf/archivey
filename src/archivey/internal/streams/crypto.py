"""AES decrypt stage via the ``[recommended]`` extra (``cryptography`` package).

Format parsers must not import ``cryptography`` directly — only this module does
(the backend stays swappable). AES is a *pipeline stage* ahead of a decompressor
(e.g. AES → LZMA2 for an encrypted 7z folder).

Layers here:

- :class:`DecryptStage` / :func:`open_aes_decrypt_stage` — feed ciphertext chunks,
  get plaintext (used when composing inside a larger open).
- :class:`AesDecryptStream` / :func:`open_aes_decrypt_stream` — pull ``BinaryIO``
  wrapper over a ciphertext source (7z member data: CBC, optional seek).
  RAR headers and WinZip AES keep their own pull streams; see the class
  docstring.
- :func:`derive_sevenzip_aes_key` / :func:`parse_sevenzip_aes_properties` —
  **7z-local** KDF helpers. RAR and WinZip-AES derive keys differently, so these
  are not on the generic :class:`CryptoBackend` surface; they live beside it for
  the 7z reader only.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from typing import BinaryIO, Protocol

from archivey.exceptions import PackageNotInstalledError, UnsupportedFeatureError
from archivey.internal.streams.resume import ask_resume_offset
from archivey.internal.streams.streamtools import (
    ReadOnlyIOStream,
    is_seekable,
    read_exact,
    source_byte_size,
)
from archivey.types import MissingComponent

# The package name surfaced to users, and the single install hint every AES raise site
# (here and in zip_aes) formats its message from.
CRYPTO_PACKAGE = "cryptography"
CRYPTO_REQUIREMENT = MissingComponent(
    CRYPTO_PACKAGE, "pip install archivey[recommended]", ("aes",)
)

# 7-Zip's own decoder clamp (7zAes.cpp ``k_NumCyclesPower_Supported_MAX``): accept
# ``NumCyclesPower <= 24`` or the ``0x3F`` no-hash sentinel; reject 25–62.
_SEVENZIP_MAX_CYCLES_POWER = 24
_SEVENZIP_NO_HASH_SENTINEL = 0x3F

# AES block size (also the CBC IV length). 7z writers pad *plaintext* to this
# and store a full ciphertext block, so a well-formed pack stream is aligned.
AES_BLOCK_SIZE = 16


@dataclass(frozen=True)
class AesParams:
    """Inputs to an AES-CBC decrypt stage: the derived key and the initialization vector."""

    key: bytes = field(repr=False)
    iv: bytes


class DecryptStage(Protocol):
    """A streaming decrypt transform: feed ciphertext, get plaintext; ``finalize`` flushes."""

    def update(self, data: bytes) -> bytes: ...
    def finalize(self) -> bytes: ...


class CryptoBackend(ABC):
    """Abstraction over a crypto library. The only thing format code may depend on."""

    name: str

    @abstractmethod
    def aes_cbc_decrypt_stage(self, params: AesParams) -> DecryptStage:
        """Create an AES-256-CBC decrypt stage for ``params``."""
        ...


class _CryptographyDecryptStage:
    """AES-256-CBC decrypt stage backed by ``cryptography`` Cipher."""

    def __init__(self, params: AesParams) -> None:
        # Local import: format parsers never import cryptography; only this wrapper does.
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        if len(params.key) not in (16, 24, 32):
            raise ValueError(
                f"AES key must be 16, 24, or 32 bytes, got {len(params.key)}"
            )
        if len(params.iv) != AES_BLOCK_SIZE:
            raise ValueError(
                f"AES-CBC IV must be {AES_BLOCK_SIZE} bytes, got {len(params.iv)}"
            )
        cipher = Cipher(algorithms.AES(params.key), modes.CBC(params.iv))
        self._decryptor = cipher.decryptor()
        self._buf = bytearray()

    def update(self, data: bytes) -> bytes:
        if not data:
            return b""
        self._buf.extend(data)
        # Hold a partial trailing block until finalize; CBC cannot decrypt it alone.
        n = len(self._buf) - (len(self._buf) % AES_BLOCK_SIZE)
        if n == 0:
            return b""
        block = bytes(self._buf[:n])
        del self._buf[:n]
        return self._decryptor.update(block)

    def finalize(self) -> bytes:
        if self._buf:
            # Drain path for a short last *ciphertext* chunk. AES cannot recover a
            # truncated block: the 16 output bytes are not payload. Well-formed 7z
            # never hits this — the encoder zero-pads *plaintext* (FilterCoder.cpp /
            # py7zr ``AESCompressor.flush``) and stores a full ciphertext block;
            # pack_size is a multiple of AES_BLOCK_SIZE. The extra plaintext zeros
            # are discarded by the AES coder's unpack_size, not here.
            padlen = (-len(self._buf)) % AES_BLOCK_SIZE
            self._buf.extend(bytes(padlen))
            out = self._decryptor.update(bytes(self._buf))
            self._buf.clear()
            out += self._decryptor.finalize()
            return out
        return self._decryptor.finalize()


class _CryptographyBackend(CryptoBackend):
    name = CRYPTO_PACKAGE

    def aes_cbc_decrypt_stage(self, params: AesParams) -> DecryptStage:
        return _CryptographyDecryptStage(params)


def _crypto_available() -> bool:
    """Whether the crypto backend's package is importable.

    Wrapped in a function (rather than an import-time flag) so tests can simulate the
    package being absent by patching this symbol.
    """
    return importlib.util.find_spec(CRYPTO_PACKAGE) is not None


def get_crypto_backend() -> CryptoBackend:
    """Return the crypto backend, or raise ``PackageNotInstalledError`` naming ``cryptography``.

    This is the single entry point to crypto for the whole library; format parsers call it
    rather than importing any crypto library themselves.
    """
    if not _crypto_available():
        raise PackageNotInstalledError(CRYPTO_REQUIREMENT.message("AES decryption"))
    return _CryptographyBackend()


def open_aes_decrypt_stage(params: AesParams) -> DecryptStage:
    """Convenience: resolve the crypto backend and build an AES-CBC decrypt stage."""
    return get_crypto_backend().aes_cbc_decrypt_stage(params)


class AesDecryptStream(ReadOnlyIOStream):
    """Pull ``BinaryIO`` that decrypts an underlying ciphertext stream via AES-CBC.

    This is the 7z member-data wrapper. CBC can restart at any block using the
    preceding ciphertext block as the IV, so when the ciphertext source is
    seekable this stream is too — that is what keeps ``seekable_members=True``
    on an encrypted 7z folder. ``tell`` is the **plaintext** offset.
    ``seekable()`` follows the ciphertext source. ``SEEK_END`` still needs a
    known ciphertext length (production ``SharedView`` / ``SlicingStream``
    expose ``.size``); an unsized seekable source raises
    ``io.UnsupportedOperation``. Mid-stream ``seek`` still works.

    Two AES-CBC pull streams share :class:`DecryptStage`.
    ``WinZipAesDecryptStream`` is CTR and builds its own cipher; it shares
    only the availability check.

    Folding ``_HeaderDecryptStream`` in is blocked by the header walk, not by
    flags this class is missing:

    - ``tell()`` is archive offset for both arms. ``header_fd`` is either the
      raw handle or the decrypt stream; a second method does not help while
      the raw handle is the other arm.
    - The stream sits mid-file on the shared archive handle, unbounded.
      Without a ``length=`` bound it would treat the rest of the file as
      ciphertext and report seekable; seeking would reposition the archive.

    Ownership is ``owns_inner`` (both borrow). ``read`` already gathers short
    source reads. A short last ciphertext block still drains here (follow-up:
    ``TruncatedError`` on both streams).
    """

    def __init__(
        self,
        source: BinaryIO,
        params: AesParams,
        *,
        owns_inner: bool = False,
    ) -> None:
        super().__init__()
        self._source = source
        self._params = params
        self._owns_inner = owns_inner
        self._seekable = is_seekable(source)
        # Ciphertext origin of this wrapper. Seekable sources are positioned at
        # the first ciphertext byte (a 7z pack ``SharedView`` starts at 0 of the
        # view). Non-seekable sources have no origin to restore; seek is refused.
        self._cipher_start = source.tell() if self._seekable else 0
        self._stage: DecryptStage = open_aes_decrypt_stage(params)
        self._buf = bytearray()
        self._eof = False
        self._pos = 0

    def _raise_if_closed(self) -> None:
        if self.closed:
            raise ValueError("I/O operation on closed file.")

    def read(self, n: int = -1, /) -> bytes:
        self._raise_if_closed()
        if n == 0:
            return b""
        while not self._eof and (n < 0 or len(self._buf) < n):
            # Round the source ask up to a block so CBC does not withhold a
            # partial block (and so the ciphertext cursor is derivable as
            # ``_cipher_start + _pos + len(_buf)`` for a full-count source —
            # ADR 0014). A short non-empty source read leaves bytes in the
            # stage buffer and the identity does not hold.
            if n < 0:
                ask = 65536
            else:
                want = max(n - len(self._buf), 1)
                extra = want % AES_BLOCK_SIZE
                ask = want if extra == 0 else want + (AES_BLOCK_SIZE - extra)
            chunk = self._source.read(ask)
            if not chunk:
                self._buf.extend(self._stage.finalize())
                self._eof = True
                break
            self._buf.extend(self._stage.update(chunk))
        if n < 0:
            out = bytes(self._buf)
            self._buf.clear()
        else:
            out = bytes(self._buf[:n])
            del self._buf[:n]
        self._pos += len(out)
        return out

    def tell(self, /) -> int:
        self._raise_if_closed()
        return self._pos

    def seekable(self) -> bool:
        return self._seekable

    def nearest_resume_offset(self, target: int) -> int:
        """Earliest plaintext offset this stream can genuinely restart at.

        Block-aligned, and reachable from the inner's nearest resume point
        without seeking behind it. Callers may **act** on this — "to reach X,
        resume from Y, so read forward from Y" — so an answer earlier than the
        true restart point costs them replay. Never round it down.
        """
        block_start = target - (target % AES_BLOCK_SIZE)
        iv_off = self._cipher_start + max(block_start - AES_BLOCK_SIZE, 0)
        resume = ask_resume_offset(self._source, iv_off)
        if resume is None:
            return block_start
        # Restart at plaintext block k iff the IV block at cs + 16(k-1) is
        # at or after the inner's resume. Smallest such k:
        # P = ceil(q / 16) * 16 + 16, q = resume - cipher_start.
        q = max(0, resume - self._cipher_start)
        composed = -(-q // AES_BLOCK_SIZE) * AES_BLOCK_SIZE + AES_BLOCK_SIZE
        return max(0, min(block_start, composed))

    @property
    def size(self) -> int | None:
        """Padded plaintext length when the ciphertext length is cheaply knowable.

        fsspec-style ``size``. This is the CBC-block-aligned length, not the
        payload length: a truncated 53-byte ciphertext reports 64, and
        well-formed 7z still includes up to 15 bytes of writer pad. The AES
        coder's ``unpack_size`` (next coder's ``pack_size``) trims the pad
        one layer up — which is why ``_bound_rapidgzip_source`` prefers
        ``params.pack_size`` over this.
        """
        return self._plaintext_size()

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        self._raise_if_closed()
        if not self._seekable:
            raise io.UnsupportedOperation("seek")

        new_pos = self._absolute_plaintext_pos(offset, whence)
        if new_pos == self._pos:
            return self._pos

        # Already decrypted into the buffer: consume from there.
        buf_end = self._pos + len(self._buf)
        if self._pos < new_pos <= buf_end:
            del self._buf[: new_pos - self._pos]
            self._pos = new_pos
            return self._pos

        # Past known plaintext end. Python files allow tell() past EOF;
        # the next read is empty.
        size = self._plaintext_size()
        if size is not None and new_pos >= size:
            cipher_len = self._cipher_len()
            if cipher_len is not None:
                # Source at EOF so a caller inspecting it agrees; this stream
                # will not read it again (_eof, and restart re-seeks).
                self._source.seek(self._cipher_start + cipher_len)
            self._buf.clear()
            self._eof = True
            self._pos = new_pos
            return self._pos

        # CBC restart: ciphertext block ``block - 1`` is the IV for ``block``.
        block, intra = divmod(new_pos, AES_BLOCK_SIZE)
        if not self._restart_at_block(block):
            # Size unknown, and the previous ciphertext block is past EOF —
            # same as seek-past-end. ``_restart_at_block`` already marked EOF.
            self._pos = new_pos
            return self._pos
        self._pos = block * AES_BLOCK_SIZE
        if intra:
            skipped = self.read(intra)
            if len(skipped) < intra:
                self._pos = new_pos
        return self._pos

    def _absolute_plaintext_pos(self, offset: int, whence: int) -> int:
        if whence == io.SEEK_SET:
            new_pos = offset
        elif whence == io.SEEK_CUR:
            new_pos = self._pos + offset
        elif whence == io.SEEK_END:
            size = self._plaintext_size()
            if size is None:
                # Production sources are SharedView / SlicingStream, which
                # expose .size, so this branch is cold. Unsized SEEK_END would
                # read(-1) the whole plaintext into _buf and throw it away.
                raise io.UnsupportedOperation(
                    "SEEK_END requires a known ciphertext length"
                )
            new_pos = size + offset
        else:
            raise ValueError(f"Invalid whence: {whence}")
        if new_pos < 0:
            # Match BytesIO / SlicingStream: relative underflow clamps to the
            # origin; only an explicitly negative SEEK_SET raises.
            if whence == io.SEEK_SET:
                raise ValueError(f"Negative seek position {new_pos}")
            new_pos = 0
        return new_pos

    def _cipher_len(self) -> int | None:
        total = source_byte_size(self._source)
        if total is None:
            return None
        return max(0, total - self._cipher_start)

    def _plaintext_size(self) -> int | None:
        """Plaintext length: ciphertext length, rounded **up** to a whole block.

        The round-up is the truncation policy's, not this method's: a short last
        ciphertext block still decrypts to 16 garbage bytes in ``finalize``, so
        SEEK_END has to agree with read-to-EOF. Maintainer decision (davitf,
        2026-09-16): that policy becomes ``TruncatedError`` — a short last block
        is corruption, not payload. **Move all three sites together, soon**
        (``finalize``, this method, and the ``size`` property); changing
        ``finalize`` alone leaves SEEK_END and ``size`` reporting 16 bytes
        that no longer exist.
        """
        cipher_len = self._cipher_len()
        if cipher_len is None:
            return None
        remainder = cipher_len % AES_BLOCK_SIZE
        if remainder == 0:
            # Writer convention: pack_size is already a full number of blocks.
            return cipher_len
        return cipher_len + (AES_BLOCK_SIZE - remainder)

    def _restart_at_block(self, block: int) -> bool:
        """Recreate the CBC decryptor so plaintext block ``block`` is next.

        Returns False when the preceding ciphertext block is not there (past
        EOF). A spent ``DecryptStage`` cannot be reused after ``finalize``;
        every reposition builds a fresh one.
        """
        if block == 0:
            iv = self._params.iv
            self._source.seek(self._cipher_start)
        else:
            self._source.seek(self._cipher_start + (block - 1) * AES_BLOCK_SIZE)
            iv = read_exact(self._source, AES_BLOCK_SIZE)
            if len(iv) < AES_BLOCK_SIZE:
                self._buf.clear()
                self._eof = True
                return False
            # ``read_exact`` left the source at the start of ``block``.
        self._stage = open_aes_decrypt_stage(replace(self._params, iv=iv))
        self._buf.clear()
        self._eof = False
        return True

    def close(self) -> None:
        if not self.closed:
            try:
                if self._owns_inner:
                    self._source.close()
            finally:
                super().close()


def open_aes_decrypt_stream(
    source: BinaryIO,
    params: AesParams,
    *,
    owns_inner: bool = False,
) -> BinaryIO:
    """Wrap ``source`` in an AES-CBC decrypt stream using the shared crypto backend.

    ``owns_inner`` defaults to borrow, matching :class:`DecompressorStream` /
    :class:`~archivey.internal.streams.streamtools.slice.SlicingStream`. The 7z
    pack view underneath is a :class:`~archivey.internal.streams.streamtools.slice.SharedView`.
    """
    return AesDecryptStream(source, params, owns_inner=owns_inner)


# --- 7z-local KDF (not on the generic CryptoBackend surface) ---------------------------


def derive_sevenzip_aes_key(password: bytes, *, salt: bytes, cycles: int) -> bytes:
    """Derive a 32-byte AES-256 key with the 7z SHA-256 scheme.

    ``password`` is the raw password bytes already encoded as UTF-16LE (callers that
    hold a ``str`` should encode first). ``cycles`` is ``NumCyclesPower`` from the AES
    coder properties (``0..0x3f``). The ``0x3f`` special case copies salt+password into
    a 32-byte key without hashing.

    Values ``25..62`` are rejected with :class:`UnsupportedFeatureError`, matching
    7-Zip's own decoder clamp (``k_NumCyclesPower_Supported_MAX = 24``).
    """
    if cycles < 0 or cycles > _SEVENZIP_NO_HASH_SENTINEL:
        raise ValueError(f"NumCyclesPower out of range: {cycles}")
    if cycles == _SEVENZIP_NO_HASH_SENTINEL:
        # The 0x3f sentinel means "no hashing": key = (salt + password), zero-padded to 32.
        return (salt + password + bytes(32))[:32]
    if cycles > _SEVENZIP_MAX_CYCLES_POWER:
        raise UnsupportedFeatureError(
            f"7z NumCyclesPower {cycles} exceeds the supported maximum "
            f"({_SEVENZIP_MAX_CYCLES_POWER}); values 25–62 are rejected to match 7-Zip "
            "(and to bound KDF cost)."
        )
    # Batch rounds to cut hashlib.update call overhead (same approach as py7zr).
    cat_cycle = 6
    if cycles > cat_cycle:
        rounds = 1 << cat_cycle
        stages = 1 << (cycles - cat_cycle)
    else:
        rounds = 1 << cycles
        stages = 1
    digest = hashlib.sha256()
    salt_password = salt + password
    s = 0
    for _ in range(stages):
        digest.update(
            b"".join(
                salt_password + (s + i).to_bytes(8, "little") for i in range(rounds)
            )
        )
        s += rounds
    return digest.digest()


def parse_sevenzip_aes_properties(properties: bytes) -> tuple[int, bytes, bytes]:
    """Parse 7z AES coder properties → ``(num_cycles_power, salt, iv)``.

    Raises ``ValueError`` when the property blob is malformed, and
    :class:`UnsupportedFeatureError` when ``NumCyclesPower`` is 25–62 (7-Zip's
    ``E_NOTIMPL`` clamp).
    """
    if not properties:
        raise ValueError("empty 7z AES properties")
    first = properties[0]
    cycles = first & 0x3F
    if cycles > _SEVENZIP_MAX_CYCLES_POWER and cycles != _SEVENZIP_NO_HASH_SENTINEL:
        raise UnsupportedFeatureError(
            f"7z NumCyclesPower {cycles} exceeds the supported maximum "
            f"({_SEVENZIP_MAX_CYCLES_POWER}); values 25–62 are rejected to match 7-Zip "
            "(and to bound KDF cost)."
        )
    if first & 0xC0 == 0:
        raise ValueError("7z AES properties missing salt/IV flags")
    salt_size = (first >> 7) & 1
    iv_size = (first >> 6) & 1
    if len(properties) < 2:
        raise ValueError("truncated 7z AES properties")
    second = properties[1]
    salt_size += second >> 4
    iv_size += second & 0x0F
    expected = 2 + salt_size + iv_size
    if len(properties) != expected:
        raise ValueError(
            f"7z AES properties length {len(properties)} != expected {expected}"
        )
    salt = properties[2 : 2 + salt_size]
    iv = properties[2 + salt_size : 2 + salt_size + iv_size]
    if len(iv) < AES_BLOCK_SIZE:
        iv = iv + bytes(AES_BLOCK_SIZE - len(iv))
    return cycles, salt, iv


@dataclass
class SevenZipKeyCache:
    """Cache derived 7z AES keys keyed by ``(password, salt, cycles)`` for one reader."""

    _cache: dict[tuple[bytes, bytes, int], bytes] = field(default_factory=dict)

    def derive(self, password: bytes, *, salt: bytes, cycles: int) -> bytes:
        key = (password, salt, cycles)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        derived = derive_sevenzip_aes_key(password, salt=salt, cycles=cycles)
        self._cache[key] = derived
        return derived

    def aes_params_from_properties(
        self, password: bytes, properties: bytes
    ) -> AesParams:
        cycles, salt, iv = parse_sevenzip_aes_properties(properties)
        key = self.derive(password, salt=salt, cycles=cycles)
        return AesParams(key=key, iv=iv)
