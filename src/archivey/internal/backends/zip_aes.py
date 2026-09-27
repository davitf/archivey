"""WinZip AES (AE-1 / AE-2) decryption for ZIP members.

Wire format (APPNOTE / WinZip AE-x)::

    [salt][pw_verify(2)][AES-CTR ciphertext][HMAC-SHA1(10)]

The member's ``compress_type`` is 99; extra field ``0x9901`` carries vendor version
(AE-1=1 / AE-2=2), strength (1/2/3 → 128/192/256), and the actual compression method.
Key material is PBKDF2-HMAC-SHA1(password, salt, 1000) → enc_key ‖ auth_key ‖ pw_verify.
CTR uses a little-endian counter starting at 1 (no nonce). HMAC covers ciphertext only.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import struct
from dataclasses import dataclass
from typing import BinaryIO

from archivey.exceptions import (
    CorruptionError,
    PackageNotInstalledError,
    TruncatedError,
)
from archivey.internal.password import wrong_password_error
from archivey.internal.streams.crypto import (
    CRYPTO_REQUIREMENT,
    AesCtrParams,
    KeystreamStage,
    _crypto_available,
    open_aes_ctr_stage,
)
from archivey.internal.streams.streamtools import ReadOnlyIOStream, read_exact

# WinZip AES extra-field header id.
_AES_EXTRA_ID = 0x9901
_HMAC_LEN = 10
_PBKDF2_ITERS = 1000


@dataclass(frozen=True)
class WinZipAesInfo:
    """Parsed AE-x parameters from extra field ``0x9901``."""

    vendor_version: int  # 1 = AE-1, 2 = AE-2
    strength: int  # 1 / 2 / 3 → 128 / 192 / 256 bits
    actual_method: int  # underlying ZIP compression method id

    @property
    def key_bits(self) -> int:
        return {1: 128, 2: 192, 3: 256}[self.strength]

    @property
    def key_len(self) -> int:
        return self.key_bits // 8

    @property
    def salt_len(self) -> int:
        return self.key_bits // 16

    @property
    def is_ae2(self) -> bool:
        return self.vendor_version == 2


def parse_winzip_aes_extra(extra: bytes) -> WinZipAesInfo | None:
    """Return AE info from a ZIP extra field blob, or ``None`` when absent/malformed."""
    i = 0
    while i + 4 <= len(extra):
        hdr_id, size = struct.unpack_from("<HH", extra, i)
        if i + 4 + size > len(extra):
            break
        data = extra[i + 4 : i + 4 + size]
        if hdr_id == _AES_EXTRA_ID and size >= 7:
            vendor_version, vendor_id, strength, actual_method = struct.unpack(
                "<H2sBH", data[:7]
            )
            if vendor_id != b"AE" or strength not in (1, 2, 3):
                return None
            if vendor_version not in (1, 2):
                return None
            return WinZipAesInfo(vendor_version, strength, actual_method)
        i += 4 + size
    return None


def derive_winzip_aes_keys(
    password: bytes, *, salt: bytes, key_len: int
) -> tuple[bytes, bytes, bytes]:
    """PBKDF2-HMAC-SHA1 → ``(enc_key, auth_key, pw_verify)``."""
    derived = hashlib.pbkdf2_hmac(
        "sha1", password, salt, _PBKDF2_ITERS, dklen=key_len * 2 + 2
    )
    enc_key = derived[:key_len]
    auth_key = derived[key_len : key_len * 2]
    pw_verify = derived[key_len * 2 :]
    return enc_key, auth_key, pw_verify


class WinZipAesDecryptStream(ReadOnlyIOStream):
    """Decrypt an AE ciphertext body and verify the trailing HMAC-SHA1(10).

    ``source`` must be positioned at the start of the ciphertext (after salt +
    pw_verify) and bounded to ``cipher_len + 10`` (ciphertext + MAC). The HMAC is
    checked by the read that returns the last plaintext byte, before it returns.
    ``close`` is teardown only — it does not drain or authenticate (ADR 0014).

    Positions are plaintext offsets, equal to ciphertext offsets: CTR adds no bytes.
    Seeking needs a seekable ``source``. CTR decrypts from any offset (the counter
    for byte ``p`` is ``1 + p // 16``). The HMAC covers the ciphertext, not the
    plaintext, so a seek does not give it up: the HMAC takes in ciphertext only while
    reads continue its hashed prefix, and the read that reaches the end re-reads the
    rest of the ciphertext from ``source`` to complete it. That costs one sequential
    read of the unhashed ciphertext and no decryption. Seek positions follow
    :class:`~archivey.internal.streams.streamtools.SlicingStream` (and ``BytesIO``):
    only a negative ``SEEK_SET`` raises, a relative seek below 0 clamps to 0, and a
    seek past the end returns the requested position, where reads return ``b""``.

    Not :class:`~archivey.internal.streams.crypto.AesDecryptStream` (7z CBC). Close
    always owns ``source`` — the ZIP member payload slice has no borrow caller, so
    there is no ``owns_inner``.
    """

    def __init__(
        self,
        source: BinaryIO,
        *,
        enc_key: bytes,
        auth_key: bytes,
        cipher_len: int,
    ) -> None:
        super().__init__()
        # First: close() reads it, and IOBase.__del__ runs close() on a refused instance.
        self._source = source
        if cipher_len < 0:
            raise ValueError("cipher_len must be non-negative")
        if not _crypto_available():
            raise PackageNotInstalledError(
                CRYPTO_REQUIREMENT.message("WinZip AES decryption")
            )
        self._enc_key = enc_key
        self._ctr = self._ctr_stage_at(0)
        self._hmac = hmac.new(auth_key, digestmod=hashlib.sha1)
        self._hashed = 0  # length of the ciphertext prefix the HMAC has taken in
        self._authenticated = False
        self._cipher_len = cipher_len
        # The plaintext length, exactly (CTR adds no bytes). ``source_byte_size`` reads
        # it, so the codec layer's accelerator threshold and source bound see the true
        # input size of a member decoded from this stage.
        self.size = cipher_len
        self._cipher_remaining = cipher_len
        self._origin = source.tell() if source.seekable() else 0
        self._buf = bytearray()
        self._overshoot = 0  # how far a seek put the position past the ciphertext end

    def _ctr_stage_at(self, offset: int) -> KeystreamStage:
        # WinZip AE: the whole 16-byte counter block, little-endian, starting at 1.
        block, into = divmod(offset, 16)
        stage = open_aes_ctr_stage(
            AesCtrParams(
                self._enc_key, initial_counter=1 + block, counter_byteorder="little"
            )
        )
        if into:
            stage.process(bytes(into))  # discard the keystream before ``offset``
        return stage

    def _pull(self) -> None:
        """Decrypt the next chunk; at the ciphertext end, authenticate instead."""
        if self._cipher_remaining > 0:
            at = self._cipher_len - self._cipher_remaining
            chunk = self._source.read(min(65536, self._cipher_remaining))
            if not chunk:
                raise TruncatedError("Truncated WinZip AES ciphertext before HMAC")
            self._cipher_remaining -= len(chunk)
            if at <= self._hashed < at + len(chunk):
                # Extend the hashed prefix by whatever of this chunk continues it.
                self._hmac.update(chunk[self._hashed - at :])
                self._hashed = at + len(chunk)
            self._buf.extend(self._ctr.process(chunk))
            return
        self._authenticate()

    def _authenticate(self) -> None:
        """Complete the HMAC over the whole ciphertext and compare it with the MAC.

        Runs once, from a read at the ciphertext end. The source is then at the MAC.
        When seeks left part of the ciphertext unhashed, that part is read from the
        source again first; the source ends at the MAC either way.
        """
        if self._authenticated:
            return
        if self._hashed < self._cipher_len:
            self._source.seek(self._origin + self._hashed)
            while self._hashed < self._cipher_len:
                chunk = self._source.read(min(65536, self._cipher_len - self._hashed))
                if not chunk:
                    raise TruncatedError("Truncated WinZip AES ciphertext before HMAC")
                self._hmac.update(chunk)
                self._hashed += len(chunk)
        mac = read_exact(self._source, _HMAC_LEN)
        if len(mac) != _HMAC_LEN:
            raise TruncatedError("Truncated WinZip AES HMAC")
        self._authenticated = True
        expected = self._hmac.digest()[:_HMAC_LEN]
        if not hmac.compare_digest(mac, expected):
            raise CorruptionError(
                "WinZip AES HMAC mismatch (wrong password or tampered ciphertext)"
            )

    def read(self, size: int | None = -1) -> bytes:
        if size is None:  # read to EOF, as on any ``io`` stream
            size = -1
        # Past the end nothing is left to read, and a position the caller seeked
        # past the end is not a read that reached it: no verdict there.
        if size == 0 or self._overshoot:
            return b""
        while size < 0 or len(self._buf) < size:
            if self._cipher_remaining <= 0 and self._authenticated:
                break
            before = len(self._buf)
            self._pull()
            if len(self._buf) == before and self._cipher_remaining <= 0:
                break
        if size < 0:
            out = bytes(self._buf)
            self._buf.clear()
        else:
            out = bytes(self._buf[:size])
            del self._buf[:size]
        if out and not self._buf and self._cipher_remaining <= 0:
            # This read hands out the last plaintext byte: authenticate before it
            # returns, so a consumer that knows the size and never reads past it
            # still gets the verdict (and a mismatch withholds the last chunk).
            self._authenticate()
        return out

    def seekable(self) -> bool:
        return self._source.seekable()

    def tell(self) -> int:
        return (
            self._cipher_len - self._cipher_remaining - len(self._buf) + self._overshoot
        )

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        pos = self.tell()
        if whence == io.SEEK_SET:
            if offset < 0:
                raise ValueError(f"negative seek position {offset}")
            target = offset
        elif whence == io.SEEK_CUR:
            target = max(0, pos + offset)
        elif whence == io.SEEK_END:
            target = max(0, self._cipher_len + offset)
        else:
            raise ValueError(f"invalid whence ({whence})")
        if target == pos:
            return pos
        reach = min(target, self._cipher_len)
        self._source.seek(self._origin + reach)
        self._buf.clear()
        self._cipher_remaining = self._cipher_len - reach
        self._ctr = self._ctr_stage_at(reach)
        self._overshoot = target - reach
        return target

    def close(self) -> None:
        if self.closed:
            return
        try:
            # Do not drain remaining ciphertext for HMAC: close is teardown,
            # not a verdict (ADR 0014).
            self._source.close()
        finally:
            super().close()


def open_winzip_aes_member(
    raw: BinaryIO,
    *,
    aes: WinZipAesInfo,
    password: bytes,
    compress_size: int,
) -> BinaryIO:
    """Peel salt/pw_verify from ``raw``, verify the password, return a decrypt stream.

    ``raw`` is the full member payload (salt + verify + ciphertext + HMAC) of length
    ``compress_size``. Raises ``EncryptionError`` on a wrong password (fast-fail on the
    2-byte verification value) and ``PackageNotInstalledError`` when ``cryptography``
    (the ``[recommended]`` extra) is absent.
    """
    if not _crypto_available():
        raise PackageNotInstalledError(
            CRYPTO_REQUIREMENT.message("WinZip AES decryption")
        )
    salt_len = aes.salt_len
    overhead = salt_len + 2 + _HMAC_LEN
    # A declared size too small for the envelope is an impossible header, not a short
    # read, so it stays CorruptionError; the four short reads below are TruncatedError.
    if compress_size < overhead:
        raise CorruptionError(
            f"WinZip AES member too short for salt/verify/HMAC ({compress_size} < {overhead})"
        )
    salt = read_exact(raw, salt_len)
    if len(salt) != salt_len:
        raise TruncatedError("Truncated WinZip AES salt")
    stored_verify = read_exact(raw, 2)
    if len(stored_verify) != 2:
        raise TruncatedError("Truncated WinZip AES password-verification value")

    enc_key, auth_key, pw_verify = derive_winzip_aes_keys(
        password, salt=salt, key_len=aes.key_len
    )
    if not hmac.compare_digest(stored_verify, pw_verify):
        raise wrong_password_error("Wrong password for this ZIP member")

    cipher_len = compress_size - overhead
    return WinZipAesDecryptStream(
        raw, enc_key=enc_key, auth_key=auth_key, cipher_len=cipher_len
    )
