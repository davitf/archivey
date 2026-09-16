"""Seek, tell, and ownership for the 7z AES-CBC pull stream.

Needs ``cryptography`` (``[all]`` / ``[all-lowest]``). The ``[core-only]`` leg
skips the whole module: there is no decryptor to wrap.
"""

from __future__ import annotations

import io
from collections.abc import Callable

import pytest

from archivey.exceptions import TruncatedError
from archivey.internal.streams import crypto
from archivey.internal.streams.crypto import AES_BLOCK_SIZE, AesDecryptStream, AesParams
from tests.conftest import requires
from tests.streams_util import NonSeekableBytesIO

pytestmark = requires("cryptography")

_KEY = b"\x11" * 32
_IV = b"\x22" * AES_BLOCK_SIZE
# 53 bytes so tests hit both writer-style plaintext pad and truncated ciphertext.
_PLAIN = bytes(range(53))


def _encrypt(plaintext: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    if len(plaintext) % AES_BLOCK_SIZE:
        raise ValueError("test helper encrypts whole AES blocks only")
    encryptor = Cipher(algorithms.AES(_KEY), modes.CBC(_IV)).encryptor()
    return encryptor.update(plaintext) + encryptor.finalize()


def _stage_decrypt(cipher: bytes) -> bytes:
    """Same stage the pull stream uses. A short last block raises TruncatedError."""
    stage = crypto.open_aes_decrypt_stage(AesParams(key=_KEY, iv=_IV))
    return stage.update(cipher) + stage.finalize()


def _open(cipher: bytes, *, owns_inner: bool = False) -> AesDecryptStream:
    return AesDecryptStream(
        io.BytesIO(cipher),
        AesParams(key=_KEY, iv=_IV),
        owns_inner=owns_inner,
    )


def test_aligned_roundtrip_via_open_helper() -> None:
    plaintext = _PLAIN[:48]
    cipher = _encrypt(plaintext)
    with crypto.open_aes_decrypt_stream(
        io.BytesIO(cipher), AesParams(key=_KEY, iv=_IV)
    ) as stream:
        assert stream.read() == plaintext


def test_writer_pads_plaintext_so_ciphertext_is_block_aligned() -> None:
    """7z AES encoder zero-pads plaintext, then stores a full ciphertext block.

    Pack size is therefore a multiple of AES_BLOCK_SIZE. A short last ciphertext
    block is truncated input, not the writer convention.
    """
    payload = _PLAIN  # 53 bytes
    padded = payload + bytes(AES_BLOCK_SIZE - (len(payload) % AES_BLOCK_SIZE))
    cipher = _encrypt(padded)
    assert len(cipher) % AES_BLOCK_SIZE == 0
    with _open(cipher) as stream:
        assert stream.read() == padded
        assert stream.seek(0, io.SEEK_END) == len(padded)


def test_short_ciphertext_finalize_raises_truncated() -> None:
    # Truncate a 64-byte ciphertext to 53: three full blocks plus five. AES
    # cannot recover the last block, so finalize raises rather than emitting
    # 16 garbage plaintext bytes. size / SEEK_END report the intact 48.
    cipher = _encrypt(bytes(range(64)))[:53]
    # Mutation A: restore zero-pad drain in finalize — DID NOT RAISE.
    with pytest.raises(TruncatedError, match="mid-block"):
        _stage_decrypt(cipher)
    with _open(cipher) as stream:
        # Mutation B: round _plaintext_size up to a whole block — reports 64.
        assert stream.size == 48
        assert stream.seek(0, io.SEEK_END) == 48
        # SEEK_END describes the recoverable payload; it does not itself raise.
        # The next read is empty because the cursor is already at that end.
        assert stream.read() == b""
    with _open(cipher) as stream:
        with pytest.raises(TruncatedError, match="mid-block"):
            stream.read()
    with _open(cipher) as stream:
        assert stream.read(48) == bytes(range(48))
        with pytest.raises(TruncatedError, match="mid-block"):
            stream.read(1)


def test_seekable_follows_source() -> None:
    cipher = _encrypt(_PLAIN[:32])
    with _open(cipher) as stream:
        assert stream.seekable() is True
    wrapped = AesDecryptStream(NonSeekableBytesIO(cipher), AesParams(key=_KEY, iv=_IV))
    try:
        assert wrapped.seekable() is False
        with pytest.raises(io.UnsupportedOperation):
            wrapped.seek(0)
        assert wrapped.read() == _PLAIN[:32]
    finally:
        wrapped.close()


def test_seek_block_boundary() -> None:
    plaintext = _PLAIN[:48]
    with _open(_encrypt(plaintext)) as stream:
        assert stream.seek(16) == 16
        assert stream.tell() == 16
        assert stream.read() == plaintext[16:]


def test_seek_mid_block() -> None:
    plaintext = _PLAIN[:48]
    with _open(_encrypt(plaintext)) as stream:
        assert stream.seek(17) == 17
        assert stream.read() == plaintext[17:]


def test_seek_backwards_after_partial_read() -> None:
    plaintext = _PLAIN[:48]
    with _open(_encrypt(plaintext)) as stream:
        assert stream.read(10) == plaintext[:10]
        assert stream.tell() == 10
        stream.seek(0)
        assert stream.read() == plaintext


def test_seek_past_eof_then_read_is_empty() -> None:
    plaintext = _PLAIN[:32]
    with _open(_encrypt(plaintext)) as stream:
        assert stream.seek(1000) == 1000
        assert stream.tell() == 1000
        assert stream.read() == b""
        stream.seek(0)
        assert stream.read() == plaintext


def test_seek_end_and_relative() -> None:
    plaintext = _PLAIN[:32]
    with _open(_encrypt(plaintext)) as stream:
        assert stream.seek(0, io.SEEK_END) == 32
        assert stream.seek(-8, io.SEEK_CUR) == 24
        assert stream.read() == plaintext[24:]


def test_read_all_then_rewind() -> None:
    """A spent CBC decryptor must not be reused after finalize."""
    plaintext = _PLAIN[:48]
    with _open(_encrypt(plaintext)) as stream:
        assert stream.read() == plaintext
        stream.seek(0)
        assert stream.read() == plaintext
        stream.seek(16)
        assert stream.read(8) == plaintext[16:24]


def test_short_last_block_mid_seek() -> None:
    cipher = _encrypt(bytes(range(64)))[:53]
    with _open(cipher) as stream:
        stream.seek(40)
        # Mutation A: restore zero-pad drain in finalize — returns leftover
        # intact bytes plus 16 garbage instead of raising.
        with pytest.raises(TruncatedError, match="mid-block"):
            stream.read()


def test_owns_inner_false_borrows_source() -> None:
    source = io.BytesIO(_encrypt(_PLAIN[:16]))
    stream = AesDecryptStream(source, AesParams(key=_KEY, iv=_IV), owns_inner=False)
    stream.close()
    assert not source.closed
    with pytest.raises(ValueError, match="closed file"):
        stream.read()
    with pytest.raises(ValueError, match="closed file"):
        stream.tell()
    with pytest.raises(ValueError, match="closed file"):
        stream.seek(0)


def test_owns_inner_true_closes_source() -> None:
    source = io.BytesIO(_encrypt(_PLAIN[:16]))
    stream = AesDecryptStream(source, AesParams(key=_KEY, iv=_IV), owns_inner=True)
    stream.close()
    assert source.closed


def test_unbounded_read_still_allowed() -> None:
    """RAR headers reject read(-1); the 7z member stream must not."""
    plaintext = _PLAIN[:32]
    with _open(_encrypt(plaintext)) as stream:
        assert stream.read(-1) == plaintext


def test_relative_underflow_clamps_like_bytesio() -> None:
    plaintext = _PLAIN[:32]
    with _open(_encrypt(plaintext)) as stream:
        assert stream.seek(-5, io.SEEK_CUR) == 0
        assert stream.seek(-99, io.SEEK_END) == 0
        with pytest.raises(ValueError, match="Negative seek"):
            stream.seek(-1, io.SEEK_SET)


class _UnknownSizeSeekable(io.RawIOBase):
    """Seekable, but not a type ``source_byte_size`` will measure."""

    def __init__(self, data: bytes) -> None:
        super().__init__()
        self._inner = io.BytesIO(data)

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def read(self, n: int = -1) -> bytes:
        return self._inner.read(n)

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        return self._inner.seek(offset, whence)

    def tell(self) -> int:
        return self._inner.tell()


def test_seek_end_refused_when_ciphertext_length_unknown() -> None:
    cipher = _encrypt(_PLAIN[:32])
    stream = AesDecryptStream(_UnknownSizeSeekable(cipher), AesParams(key=_KEY, iv=_IV))
    try:
        with pytest.raises(io.UnsupportedOperation, match="SEEK_END"):
            stream.seek(0, io.SEEK_END)
        assert stream.read() == _PLAIN[:32]
    finally:
        stream.close()


def test_read_asks_source_in_block_multiples() -> None:
    cipher = _encrypt(_PLAIN[:48])
    source = io.BytesIO(cipher)
    with AesDecryptStream(source, AesParams(key=_KEY, iv=_IV)) as stream:
        assert stream.read(5) == _PLAIN[:5]
        assert source.tell() == AES_BLOCK_SIZE
        assert stream.nearest_resume_offset(17) == AES_BLOCK_SIZE


class _ResumeSource(io.BytesIO):
    """Seekable ciphertext whose resume offset is injected, not derived."""

    def __init__(self, data: bytes, resume: Callable[[int], int | None] | None) -> None:
        super().__init__(data)
        self._resume = resume

    def nearest_resume_offset(self, target: int) -> int | None:
        if self._resume is None:
            return None
        return self._resume(target)


@pytest.mark.parametrize(
    ("cipher_start", "target", "inner_resume", "expected"),
    [
        # Inner can resume at the IV block we ask for: composed answer equals
        # block_start (the + AES_BLOCK_SIZE un-shift). Without it: 399_984.
        (0, 400_000, lambda t: t, 400_000),
        # Single-block xz: inner always resumes at origin.
        (0, 400_000, lambda t: 0, 16),
        (0, 400_000, lambda t: 200_000, 200_016),
        # Inner codec block not AES-aligned: round UP so the IV block
        # does not precede the inner's seek point.
        (0, 400_000, lambda t: 200_005, 200_032),
        (0, 400_000, lambda t: 199_999, 200_016),
        (0, 400_000, lambda t: 200_001, 200_032),
        (0, 7, lambda t: t, 0),
        (100, 400_000, lambda t: t, 400_000),
        # Production SharedView declines; composition is a no-op.
        (0, 400_000, None, 400_000),
    ],
    ids=(
        "inner_free",
        "inner_origin",
        "inner_midway",
        "inner_unaligned",
        "inner_unaligned_below",
        "inner_unaligned_plus_one",
        "block_zero",
        "nonzero_cipher_start",
        "inner_declines",
    ),
)
def test_nearest_resume_offset_composes_with_inner(
    cipher_start: int,
    target: int,
    inner_resume: Callable[[int], int | None] | None,
    expected: int,
) -> None:
    cipher = _encrypt(_PLAIN[:16])
    source = _ResumeSource(b"\x00" * cipher_start + cipher, inner_resume)
    source.seek(cipher_start)
    with AesDecryptStream(source, AesParams(key=_KEY, iv=_IV)) as stream:
        assert stream.nearest_resume_offset(target) == expected


def test_aes_params_repr_hides_key() -> None:
    params = AesParams(key=_KEY, iv=_IV)
    text = repr(params)
    assert "key=" not in text
    assert str(_KEY) not in text
    assert "iv=" in text
