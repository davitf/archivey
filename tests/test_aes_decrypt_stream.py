"""Seek, tell, and ownership for the 7z AES-CBC pull stream.

Needs ``cryptography`` (``[all]`` / ``[all-lowest]``). The ``[core-only]`` leg
skips the whole module: there is no decryptor to wrap.
"""

from __future__ import annotations

import io

import pytest

from archivey.internal.streams import crypto
from archivey.internal.streams.crypto import AesDecryptStream, AesParams
from tests.conftest import requires
from tests.streams_util import NonSeekableBytesIO

pytestmark = requires("cryptography")

_KEY = b"\x11" * 32
_IV = b"\x22" * 16
# Three AES blocks plus five extra bytes so the 7z short-last-block pad is hit.
_PLAIN = bytes(range(53))


def _encrypt(plaintext: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    if len(plaintext) % 16:
        raise ValueError("test helper encrypts whole AES blocks only")
    encryptor = Cipher(algorithms.AES(_KEY), modes.CBC(_IV)).encryptor()
    return encryptor.update(plaintext) + encryptor.finalize()


def _stage_decrypt(cipher: bytes) -> bytes:
    """Oracle: the same 7z zero-pad finalize the pull stream uses."""
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


def test_short_last_block_zero_pads_like_7z() -> None:
    # 53 ciphertext bytes: three full blocks plus five, then 7z-pad on finalize.
    cipher = _encrypt(bytes(range(64)))[:53]
    expected = _stage_decrypt(cipher)
    assert len(expected) == 64
    assert expected[:48] == bytes(range(48))
    with _open(cipher) as stream:
        assert stream.read() == expected


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
    expected = _stage_decrypt(cipher)
    with _open(cipher) as stream:
        stream.seek(50)
        assert stream.read() == expected[50:]


def test_owns_inner_false_borrows_source() -> None:
    source = io.BytesIO(_encrypt(_PLAIN[:16]))
    stream = AesDecryptStream(source, AesParams(key=_KEY, iv=_IV), owns_inner=False)
    stream.close()
    assert not source.closed


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
