"""AES decrypt stage via the ``[recommended]`` extra (``cryptography`` package).

Format parsers must not import ``cryptography`` directly — only this module does
(the backend stays swappable). AES is a *pipeline stage* ahead of a decompressor
(e.g. AES → LZMA2 for an encrypted 7z folder).

Layers here:

- :class:`DecryptStage` / :func:`open_aes_decrypt_stage` — feed ciphertext chunks,
  get plaintext (used when composing inside a larger open).
- :class:`AesDecryptStream` / :func:`open_aes_decrypt_stream` — pull ``BinaryIO``
  wrapper over a ciphertext source (7z member data: CBC, 7z zero-pad, optional
  seek). RAR headers and WinZip AES keep their own pull streams; see the
  class docstring.
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
from dataclasses import dataclass, field
from typing import BinaryIO, Protocol

from archivey.exceptions import PackageNotInstalledError, UnsupportedFeatureError
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


@dataclass(frozen=True)
class AesParams:
    """Inputs to an AES-CBC decrypt stage: the derived key and the initialization vector."""

    key: bytes
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
        if len(params.iv) != 16:
            raise ValueError(f"AES-CBC IV must be 16 bytes, got {len(params.iv)}")
        cipher = Cipher(algorithms.AES(params.key), modes.CBC(params.iv))
        self._decryptor = cipher.decryptor()
        self._buf = bytearray()

    def update(self, data: bytes) -> bytes:
        if not data:
            return b""
        self._buf.extend(data)
        # CBC requires 16-byte blocks; hold a partial trailing block until finalize.
        n = len(self._buf) & ~0x0F
        if n == 0:
            return b""
        block = bytes(self._buf[:n])
        del self._buf[:n]
        return self._decryptor.update(block)

    def finalize(self) -> bytes:
        if self._buf:
            # Pad remaining ciphertext to a full block with zeros (7z AES convention).
            padlen = (-len(self._buf)) & 15
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

    Three AES pull streams share :class:`DecryptStage` and stay separate:

    | Wrapper | Mode | Why it is not this class |
    | --- | --- | --- |
    | This class | CBC | 7z member data: unbounded ``read(-1)``, 7z zero-pad of a short last block, plaintext ``tell``/``seek``, ``owns_inner`` (default borrow) |
    | ``_HeaderDecryptStream`` | CBC | RAR headers: non-owning **ciphertext** ``tell``, ``read_exact`` of each AES block, reject ``read(-1)``, no 7z zero-pad |
    | ``WinZipAesDecryptStream`` | CTR | ZIP AE-x: HMAC over the whole ciphertext, so a random seek would skip MAC updates (or force a full ciphertext pass) |

    A flag for each of those four RAR divergences would make this class wrong
    for 7z or wrong for headers. The stage is shared; the pull stream is not.
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

    def read(self, n: int = -1, /) -> bytes:
        if n == 0:
            return b""
        while not self._eof and (n < 0 or len(self._buf) < n):
            chunk = self._source.read(65536 if n < 0 else max(n - len(self._buf), 1))
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
        return self._pos

    def seekable(self) -> bool:
        return self._seekable

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        if not self._seekable:
            raise io.UnsupportedOperation("seek")
        if whence == io.SEEK_SET:
            new_pos = offset
        elif whence == io.SEEK_CUR:
            new_pos = self._pos + offset
        elif whence == io.SEEK_END:
            size = self._plaintext_size()
            if size is None:
                self.read(-1)
                size = self._pos
            new_pos = size + offset
        else:
            raise ValueError(f"Invalid whence: {whence}")
        if new_pos < 0:
            raise ValueError(f"Negative seek position {new_pos}")
        if new_pos == self._pos:
            return self._pos
        buf_end = self._pos + len(self._buf)
        if self._pos < new_pos <= buf_end:
            del self._buf[: new_pos - self._pos]
            self._pos = new_pos
            return self._pos
        size = self._plaintext_size()
        if size is not None and new_pos >= size:
            cipher_len = self._cipher_len()
            if cipher_len is not None:
                self._source.seek(self._cipher_start + cipher_len)
            self._buf.clear()
            self._eof = True
            self._pos = new_pos
            return self._pos
        block, intra = divmod(new_pos, 16)
        if not self._restart_at_block(block):
            self._pos = new_pos
            return self._pos
        self._pos = block * 16
        if intra:
            skipped = self.read(intra)
            if len(skipped) < intra:
                self._pos = new_pos
        return self._pos

    def _cipher_len(self) -> int | None:
        total = source_byte_size(self._source)
        if total is None:
            return None
        return max(0, total - self._cipher_start)

    def _plaintext_size(self) -> int | None:
        cipher_len = self._cipher_len()
        if cipher_len is None:
            return None
        remainder = cipher_len & 15
        if remainder == 0:
            return cipher_len
        # Short last ciphertext block is zero-padded then decrypted: 16 plaintext
        # bytes for that block (the 7z convention in ``DecryptStage.finalize``).
        return cipher_len + (16 - remainder)

    def _restart_at_block(self, block: int) -> bool:
        """Recreate the CBC decryptor so plaintext block ``block`` is next.

        Returns False when the preceding ciphertext block is not there (past
        EOF). A spent ``DecryptStage`` cannot be reused after ``finalize``;
        every reposition builds a fresh one.
        """
        if block == 0:
            iv = self._params.iv
        else:
            self._source.seek(self._cipher_start + (block - 1) * 16)
            iv = read_exact(self._source, 16)
            if len(iv) < 16:
                self._buf.clear()
                self._eof = True
                return False
        self._source.seek(self._cipher_start + block * 16)
        self._stage = open_aes_decrypt_stage(AesParams(key=self._params.key, iv=iv))
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
    if len(iv) < 16:
        iv = iv + bytes(16 - len(iv))
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
