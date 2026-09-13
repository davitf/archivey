"""Native RAR header-parser pins (not the reader / unrar boundary)."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest

from archivey.exceptions import CorruptionError, EncryptionError
from archivey.internal.backends.rar_parser import (
    _HeaderDecryptStream,
    _rar3_s2k,
    _Rar3Sha1,
    parse_rar_archive,
)
from tests.conftest import requires

_FIXTURES = Path(__file__).parent / "fixtures" / "rar"


def _fixture(name: str) -> Path:
    path = _FIXTURES / name
    if not path.is_file():
        pytest.fail(f"committed fixture {name} is missing")
    return path


def _aes_cbc_encrypt(key: bytes, iv: bytes, plaintext: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return encryptor.update(plaintext) + encryptor.finalize()


@requires("cryptography")
def test_header_decrypt_tell_is_ciphertext_cursor_not_plaintext() -> None:
    """A short read leaves leftover plaintext in ``_buf``; ``tell`` stays on the
    ciphertext block boundary. Subtracting leftover would report the plaintext
    offset and is the wrong ``data_offset`` for the next salt/IV (#315 threads 1–2).
    """
    key = b"\x00" * 16
    iv = b"\x01" * 16
    plaintext = b"0123456789abcdef" * 2  # two AES blocks
    ciphertext = _aes_cbc_encrypt(key, iv, plaintext)
    stream = _HeaderDecryptStream(io.BytesIO(ciphertext), key, iv)

    first = stream.read(7)
    assert first == plaintext[:7]
    assert len(stream._buf) == 9
    assert stream.tell() == 16
    # Thread 1 counterfactual: tell() - len(_buf) would be 7, the plaintext
    # offset, which is the wrong data_offset for the next salt/IV. The walk
    # pin is test_encrypted_header_plaintext_tell_breaks_the_walk.

    rest = stream.read(9)
    assert rest == plaintext[7:16]
    assert stream._buf == b""
    assert stream.tell() == 16


@requires("cryptography")
def test_header_decrypt_read_is_bounded_by_caller_not_8kib() -> None:
    """#332 CR1 / CR-P1: ``read`` does not impose an 8 KiB cap. Callers already
    bound the ask (RAR5 ``_RAR5_MAX_HEADER``, RAR3 ``uint16`` header_size).
    Unbounded ``read(-1)`` stays rejected.
    """
    key = b"\x00" * 16
    iv = b"\x01" * 16
    plaintext = b"0123456789abcdef" * ((9 * 1024) // 16)  # 9 KiB, 16-aligned
    ciphertext = _aes_cbc_encrypt(key, iv, plaintext)
    stream = _HeaderDecryptStream(io.BytesIO(ciphertext), key, iv)
    got = stream.read(len(plaintext))
    assert got == plaintext

    with pytest.raises(CorruptionError, match="Unbounded read"):
        stream.read(-1)


@requires("cryptography")
def test_rar3_wrong_header_password_is_encryption_error() -> None:
    """Raising the per-read cap must not turn a RAR3 wrong password into
    ``CorruptionError`` — candidate iteration only retries ``EncryptionError``.
    """
    path = _fixture("encrypted_header__rar4.rar")
    with (
        path.open("rb") as handle,
        pytest.raises(EncryptionError, match="wrong password"),
    ):
        parse_rar_archive(handle, password="not-the-password")


@requires("cryptography")
@pytest.mark.parametrize(
    "name",
    ["encrypted_header__.rar", "encrypted_header__rar4.rar"],
)
def test_encrypted_header_data_offset_skips_aes_block_padding(name: str) -> None:
    """On real ``-hp`` fixtures, every FILE header_size is not 16-aligned, and
    ``data_offset - header_offset`` is the padded ciphertext span. That span is
    what ``tell()`` reports; a plaintext ``tell`` would equal ``header_size``.
    """
    path = _fixture(name)
    with path.open("rb") as handle:
        archive = parse_rar_archive(handle, password="header_password")
    assert archive.has_header_encryption
    assert archive.members
    saw_unaligned_header = False
    for member in archive.members:
        span = member.data_offset - member.header_offset
        assert span % 16 == 0, (
            f"{member.filename}: ciphertext span {span} is not AES-aligned"
        )
        assert span >= member.header_size
        if member.header_size % 16 != 0:
            saw_unaligned_header = True
            assert span > member.header_size
    assert saw_unaligned_header, (
        "fixture no longer has a non-16-aligned header_size; the tell() pin "
        "needs a header that leaves AES padding in _buf"
    )


@requires("cryptography")
@pytest.mark.parametrize(
    "name",
    ["encrypted_header__.rar", "encrypted_header__rar4.rar"],
)
def test_encrypted_header_plaintext_tell_breaks_the_walk(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Red-green for thread 1: if ``tell`` subtracted leftover ``_buf``, listing
    these fixtures fails — the next salt/IV is read from inside AES padding.
    """
    import archivey.internal.backends.rar_parser as rar_parser

    orig_tell = rar_parser._HeaderDecryptStream.tell

    def plaintext_tell(self: rar_parser._HeaderDecryptStream) -> int:
        return orig_tell(self) - len(self._buf)

    monkeypatch.setattr(rar_parser._HeaderDecryptStream, "tell", plaintext_tell)
    path = _fixture(name)
    with path.open("rb") as handle, pytest.raises((CorruptionError, EncryptionError)):
        parse_rar_archive(handle, password="header_password")


def test_rar3_sha1_hashes_then_mutates_bytearray_seed() -> None:
    """The maintainer's reading of the rarbug (#315 thread 8): hashlib SHA-1 of
    this chunk is correct, then complete SHA-1 blocks in the input bytearray
    are scrambled so a reused seed hashes different bytes next time.

    ``dpos`` starts at the first block boundary in this chunk, so with an empty
    hasher the first 64 bytes stay put and the second block is rewritten.
    ``bytes`` input cannot be mutated and must fail loud — a silent skip would
    derive a different key.
    """
    seed = bytearray(range(128))
    original = bytes(seed)
    hasher = _Rar3Sha1()
    hasher.update(seed)
    assert hasher.digest() == hashlib.sha1(original).digest()
    assert bytes(seed[:64]) == original[:64]
    assert bytes(seed[64:]) != original[64:]

    frozen = bytes(range(128))
    hasher_b = _Rar3Sha1()
    with pytest.raises(TypeError, match="mutable seed"):
        hasher_b.update(frozen)
    assert frozen == bytes(range(128))


def test_rar3_sha1_short_seed_is_not_mutated() -> None:
    """Seed ≤ 64 bytes never crosses a SHA-1 block, so the WinRAR mutation
    does not run. ``header_password`` + 8-byte salt is in this class.
    """
    seed = bytearray("header_password".encode("utf-16le") + b"\x00" * 8)
    assert len(seed) <= 64
    original = bytes(seed)
    hasher = _Rar3Sha1()
    hasher.update(seed)
    assert bytes(seed) == original


@requires("rarfile")
def test_rar3_s2k_matches_rarfile_for_a_long_password() -> None:
    """Long password+salt (> 64 bytes) exercises the mutation path; the
    derived key/IV must still match rarfile's ``rar3_s2k``.
    """
    import rarfile

    password = "x" * 40  # 80 UTF-16LE bytes + 8-byte salt > 64
    salt = bytes(range(8))
    assert _rar3_s2k(password, salt) == rarfile.rar3_s2k(password, salt)
