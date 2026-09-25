"""Native RAR header-parser pins (not the reader / unrar boundary)."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest

from archivey.exceptions import CorruptionError, EncryptionError
from archivey.internal.backends.rar_parser import (
    RarKdfCache,
    _HeaderDecryptStream,
    _rar3_s2k,
    _Rar3Sha1,
    parse_rar_archive,
    parse_rar_volumes,
)
from archivey.internal.backends.sevenzip_aes import SevenZipKeyCache
from tests.conftest import requires, requires_binary

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
    ("name", "cut", "what"),
    [
        # Cut inside the first header's 16-byte IV (RAR5) / 8-byte salt (RAR3).
        ("encrypted_header__.rar", 46, "RAR5 header IV"),
        ("encrypted_header__rar4.rar", 20, "RAR3 header salt"),
    ],
)
def test_short_header_salt_or_iv_is_corruption_not_a_wrong_password(
    name: str, cut: int, what: str
) -> None:
    """The salt/IV read does not depend on the password, so running out of bytes there
    is damage. It used to be re-wrapped as ``EncryptionError``, which sent the reader
    through every password candidate and reported a truncated archive as a wrong
    password, even with the right one.
    """
    data = _fixture(name).read_bytes()[:cut]
    with pytest.raises(CorruptionError, match=what) as info:
        parse_rar_archive(io.BytesIO(data), password="header_password")
    assert not isinstance(info.value, EncryptionError)


@requires("cryptography")
@pytest.mark.parametrize(
    "name", ["encrypted_header__.rar", "encrypted_header__rar4.rar"]
)
def test_a_password_with_no_unicode_form_is_a_wrong_candidate(name: str) -> None:
    """``bytes`` that are not UTF-8 cannot be any RAR password. On the header walk that
    is a wrong candidate, so the loop reaches the right one; alone it is
    ``EncryptionError``, never a raw ``UnicodeDecodeError``."""
    import archivey

    data = _fixture(name).read_bytes()
    with archivey.open_archive(
        io.BytesIO(data), password=[b"\xff\xfe", "header_password"]
    ) as reader:
        assert reader.members()
    with pytest.raises(EncryptionError):
        archivey.open_archive(io.BytesIO(data), password=b"\xff\xfe")


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


_TINYVOL_HP = [f"tinyvol_hp.part{n}.rar" for n in range(1, 5)]


def _count_rar5_derivations(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Record the round count of every RAR5 PBKDF2 the parser runs."""
    import archivey.internal.backends.rar_parser as rar_parser

    rounds: list[int] = []
    real = rar_parser.pbkdf2_hmac

    def counting(
        name: str, password: bytes, salt: bytes, iterations: int, dklen: int
    ) -> bytes:
        rounds.append(iterations)
        return real(name, password, salt, iterations, dklen)

    monkeypatch.setattr(rar_parser, "pbkdf2_hmac", counting)
    return rounds


@requires("cryptography")
def test_header_encrypted_volume_set_derives_each_key_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every part of an ``-hp`` set repeats the encryption record, salt included.

    Each volume used to derive the PswCheck and the header key again: eight
    derivations for this four-part set, each costing up to ``2**24`` rounds at
    the archive's choosing. One cache across the set does each once.
    """
    rounds = _count_rar5_derivations(monkeypatch)
    handles = [_fixture(name).open("rb") for name in _TINYVOL_HP]
    try:
        archive = parse_rar_volumes(handles, password="header_password")
    finally:
        for handle in handles:
            handle.close()
    assert archive.has_header_encryption
    assert [m.filename for m in archive.members] == ["payload.bin"]
    assert {m.volume_index for m in archive.members} == {0}
    # AES key at 2**kdf_count, PswCheck at +32; kdf_count is RARLAB's 15.
    assert sorted(rounds) == [1 << 15, (1 << 15) + 32]


@requires("cryptography")
@pytest.mark.parametrize(
    "name",
    ["encrypted_header__.rar", "encrypted_header__rar4.rar"],
)
def test_kdf_cache_carries_header_keys_between_parses(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second parse through the same cache derives nothing.

    The reader parses once per password candidate, and a caller that passes its
    own cache shares header keys with its member reads.
    """
    import archivey.internal.backends.rar_parser as rar_parser

    rounds = _count_rar5_derivations(monkeypatch)
    rar3_calls: list[bytes] = []
    real_rar3 = rar_parser._rar3_key_iv

    def counting_rar3(wstr: bytes, salt: bytes) -> tuple[bytes, bytes]:
        rar3_calls.append(salt)
        return real_rar3(wstr, salt)

    monkeypatch.setattr(rar_parser, "_rar3_key_iv", counting_rar3)
    cache = RarKdfCache()
    path = _fixture(name)
    with path.open("rb") as handle:
        first = parse_rar_archive(handle, password="header_password", kdf_cache=cache)
    derived = len(rounds) + len(rar3_calls)
    assert derived > 0
    with path.open("rb") as handle:
        second = parse_rar_archive(handle, password="header_password", kdf_cache=cache)
    assert len(rounds) + len(rar3_calls) == derived
    assert [m.filename for m in second.members] == [m.filename for m in first.members]


@requires("cryptography")
@pytest.mark.parametrize(
    "name",
    ["encrypted_header__.rar", "encrypted_header__rar4.rar"],
)
def test_kdf_cache_does_not_answer_for_another_password(name: str) -> None:
    """The cache is keyed by password: a wrong candidate after the right one
    still fails, and the right one after a wrong one still succeeds."""
    cache = RarKdfCache()
    path = _fixture(name)
    for password, ok in (
        ("not-the-password", False),
        ("header_password", True),
        ("not-the-password", False),
    ):
        with path.open("rb") as handle:
            if ok:
                archive = parse_rar_archive(handle, password=password, kdf_cache=cache)
                assert archive.members
            else:
                with pytest.raises((CorruptionError, EncryptionError)):
                    parse_rar_archive(handle, password=password, kdf_cache=cache)


@requires("cryptography")
def test_kdf_caches_keep_passwords_and_keys_out_of_repr() -> None:
    """Both caches hold candidate passwords and the keys derived from them."""
    rar = RarKdfCache()
    with _fixture("encrypted_header__.rar").open("rb") as handle:
        parse_rar_archive(handle, password="header_password", kdf_cache=rar)
    sevenzip = SevenZipKeyCache()
    # NumCyclesPower 4, IV flag only: no salt, one IV byte.
    sevenzip.aes_params_from_properties(
        "header_password".encode("utf-16-le"), b"\x44\x00\x00"
    )
    for cache in (rar, sevenzip):
        text = repr(cache)
        assert "header_password" not in text
        assert "\\x" not in text, text


@requires("cryptography")
@requires_binary("unrar")
def test_header_encrypted_volume_set_reads_back() -> None:
    """The committed ``-hp`` volume set is a real set, not only a listing."""
    from archivey import open_archive

    with open_archive(_fixture(_TINYVOL_HP[0]), password="header_password") as archive:
        assert archive.read("payload.bin") == b"ABCDEFGH" * 200
