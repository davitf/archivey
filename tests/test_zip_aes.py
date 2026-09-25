"""WinZip AES (AE-1 / AE-2) ZIP member decryption tests."""

from __future__ import annotations

import hashlib
import hmac
import io
import os
import struct
import subprocess
import zipfile
from pathlib import Path

import pytest

from archivey import open_archive
from archivey.exceptions import (
    CorruptionError,
    EncryptionError,
    PackageNotInstalledError,
    TruncatedError,
)
from archivey.internal.backends.zip_aes import (
    WinZipAesDecryptStream,
    WinZipAesInfo,
    derive_winzip_aes_keys,
    open_winzip_aes_member,
    parse_winzip_aes_extra,
)
from archivey.types import CompressionAlgorithm, HashAlgorithm
from tests.conftest import requires, requires_binary
from tests.zip_aes_fixture import aes_ctr_le_encrypt, build_aes_zip

_PASSWORD = b"secret"
_PAYLOAD = b"winzip-aes-payload\n" * 40


def _build_aes_zip(
    *,
    payload: bytes,
    password: bytes,
    vendor_version: int,
    strength: int,
    method: int,
    name: bytes = b"secret.txt",
    tamper_hmac: bool = False,
) -> bytes:
    """Single-entry wrapper around the shared AES ZIP builder."""
    return build_aes_zip(
        [(name, payload)],
        password=password,
        vendor_version=vendor_version,
        strength=strength,
        method=method,
        tamper_hmac=tamper_hmac,
    )


def _7z_aes_zip(tmp_path: Path, *, strength: str = "AES256") -> tuple[Path, bytes]:
    payload = _PAYLOAD + os.urandom(32)
    src = tmp_path / "payload.bin"
    src.write_bytes(payload)
    archive = tmp_path / f"aes_{strength}.zip"
    result = subprocess.run(
        [
            "7z",
            "a",
            "-tzip",
            f"-mem={strength}",
            f"-p{_PASSWORD.decode()}",
            str(archive),
            src.name,
            "-y",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not archive.is_file():
        pytest.skip(f"7z cannot write AES ZIP: {result.stderr}")
    with zipfile.ZipFile(archive) as zf:
        info = zf.infolist()[0]
        if info.compress_type != 99:
            pytest.skip(f"7z did not emit method 99 (got {info.compress_type})")
    return archive, payload


@requires("cryptography")
@pytest.mark.parametrize(
    ("vendor_version", "strength", "method"),
    [
        (1, 1, 0),  # AE-1, 128, STORED
        (1, 3, 8),  # AE-1, 256, DEFLATE
        (2, 1, 0),  # AE-2, 128, STORED
        (2, 3, 8),  # AE-2, 256, DEFLATE
        (2, 2, 8),  # AE-2, 192, DEFLATE
    ],
)
def test_handbuilt_aes_roundtrip(
    vendor_version: int, strength: int, method: int
) -> None:
    data = _build_aes_zip(
        payload=_PAYLOAD,
        password=_PASSWORD,
        vendor_version=vendor_version,
        strength=strength,
        method=method,
    )
    with open_archive(io.BytesIO(data), password=_PASSWORD) as ar:
        (member,) = ar.members()
        assert member.is_encrypted
        assert member.extra["zip.aes_vendor_version"] == vendor_version
        assert member.extra["zip.aes_strength"] == strength
        if vendor_version == 2:
            assert HashAlgorithm.CRC32 not in member.hashes
        else:
            assert HashAlgorithm.CRC32 in member.hashes
        expected_algo = (
            CompressionAlgorithm.STORED if method == 0 else CompressionAlgorithm.DEFLATE
        )
        assert member.compression[0].algo is expected_algo
        assert ar.read(member) == _PAYLOAD


@requires_binary("7z")
@requires("cryptography")
@pytest.mark.parametrize("strength", ["AES128", "AES256"])
def test_7z_aes_zip_roundtrip(tmp_path: Path, strength: str) -> None:
    archive, payload = _7z_aes_zip(tmp_path, strength=strength)
    with open_archive(archive, password=_PASSWORD) as ar:
        (member,) = ar.members()
        assert member.is_encrypted
        assert HashAlgorithm.CRC32 not in member.hashes  # 7z emits AE-2
        assert ar.read(member) == payload


_EXTERNAL_DIR = Path(__file__).parent / "fixtures" / "external"


def _patch_stored_crc(data: bytes, value: int) -> bytes:
    """Rewrite both copies of the CRC in a single-member `_build_aes_zip` output.

    The LFH is at offset 0 and the central directory starts where the EOCD says, so
    this only holds for the one-entry archives built above.
    """
    buf = bytearray(data)
    eocd = buf.rindex(b"PK\x05\x06")
    offset_cd = struct.unpack_from("<I", buf, eocd + 16)[0]
    struct.pack_into("<I", buf, 14, value)
    struct.pack_into("<I", buf, offset_cd + 16, value)
    return bytes(buf)


@requires_binary("7z")
@requires("cryptography")
def test_handbuilt_ae1_is_accepted_by_7z(tmp_path: Path) -> None:
    """Cross-check the AE-1 builder against an independent implementation.

    7-Zip writes AE-2, so `test_7z_aes_zip_roundtrip` cannot reach AE-1: without this,
    every AE-1 fixture in the suite is bytes we assembled and then read back ourselves.
    7-Zip *reads* AE-1 and checks its CRC, so accepting our fixture validates the key
    derivation, the CTR keystream, the HMAC and the AE-1 CRC rule in one verdict.

    The two corrupted-CRC cases are the control: they are what makes the pass mean
    something, and they pin the asymmetry the AE-1/AE-2 split is about — 7-Zip rejects a
    wrong CRC on AE-1 and ignores one on AE-2.
    """
    archive = tmp_path / "ae1.zip"

    def check_7z(payload: bytes) -> int:
        archive.write_bytes(payload)
        return subprocess.run(
            ["7z", "t", f"-p{_PASSWORD.decode()}", str(archive)],
            capture_output=True,
            text=True,
        ).returncode

    ae1 = _build_aes_zip(
        payload=_PAYLOAD, password=_PASSWORD, vendor_version=1, strength=3, method=8
    )
    ae2 = _build_aes_zip(
        payload=_PAYLOAD, password=_PASSWORD, vendor_version=2, strength=3, method=8
    )
    assert check_7z(ae1) == 0
    assert check_7z(ae2) == 0
    assert check_7z(_patch_stored_crc(ae1, 0xDEADBEEF)) != 0
    assert check_7z(_patch_stored_crc(ae2, 0xDEADBEEF)) == 0


@requires("cryptography")
def test_external_ae1_archive_from_pyzipper() -> None:
    """A third-party AE-1 member, not one of ours.

    `pyzipper` 0.3.0 (2019) through 0.3.6 (2022) wrote AE-1 for every AES member
    regardless of size, and 0.3.6 was the only release available until 0.4.0 switched to
    AE-2 in 2026 — so AE-1 is real traffic, not a legacy branch. See
    `tests/fixtures/external/README.md`.
    """
    with open_archive(
        _EXTERNAL_DIR / "aes_ae1_pyzipper036.zip", password=_PASSWORD
    ) as ar:
        (member,) = ar.members()
        assert member.is_encrypted
        assert member.extra["zip.aes_vendor_version"] == 1
        assert HashAlgorithm.CRC32 in member.hashes  # AE-1 keeps the plaintext CRC
        assert ar.read(member) == b"AE-1 keeps the plaintext CRC in the header.\n"


@requires("cryptography")
def test_aes_wrong_password_fails_fast() -> None:
    data = _build_aes_zip(
        payload=_PAYLOAD, password=_PASSWORD, vendor_version=2, strength=3, method=8
    )
    with open_archive(io.BytesIO(data), password=b"wrong") as ar:
        with pytest.raises(EncryptionError, match="Wrong password"):
            ar.read(ar.members()[0])


@pytest.mark.parametrize("method", [0, 8], ids=["stored", "deflate"])
@requires("cryptography")
def test_aes_tampered_hmac_raises_corruption(method: int) -> None:
    data = _build_aes_zip(
        payload=_PAYLOAD,
        password=_PASSWORD,
        vendor_version=2,
        strength=3,
        method=method,
        tamper_hmac=True,
    )
    with open_archive(io.BytesIO(data), password=_PASSWORD) as ar:
        with pytest.raises(CorruptionError, match="HMAC"):
            ar.read(ar.members()[0])


@pytest.mark.parametrize("method", [0, 8], ids=["stored", "deflate"])
@requires("cryptography")
def test_aes_tampered_hmac_partial_read_then_close_is_quiet(method: int) -> None:
    """Partial read then close is not an HMAC verdict (ADR 0014 / ARC-45).

    STORED used to drain and raise from ``close()``. Compressed members already
    skipped that check (S1-F1): the decompressor borrows the decrypt stream.
    Removing the drain makes both paths match CRC members.
    """
    # Incompressible and larger than one codec feed so DEFLATE cannot exhaust
    # the ciphertext (and therefore the HMAC) on a 16-byte read.
    payload = os.urandom(300 * 1024)
    data = _build_aes_zip(
        payload=payload,
        password=_PASSWORD,
        vendor_version=2,
        strength=3,
        method=method,
        tamper_hmac=True,
    )
    with open_archive(io.BytesIO(data), password=_PASSWORD) as ar:
        with ar.open(ar.members()[0]) as stream:
            assert len(stream.read(16)) == 16
            stream.close()  # must not raise CorruptionError


class _CloseCounter(io.BytesIO):
    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        super().close()


def _aes_decrypt_stream(source: io.BytesIO) -> WinZipAesDecryptStream:
    return WinZipAesDecryptStream(
        source,
        enc_key=b"\x00" * 16,
        auth_key=b"\x00" * 16,
        cipher_len=50,
    )


@requires("cryptography")
def test_aes_decrypt_stream_close_releases_source() -> None:
    """Partial read then close still owns the source; a second close is a no-op.

    After ARC-45, releasing ``_source`` is the only thing ``close()`` does.
    """
    src = _CloseCounter(b"\x00" * 60)
    stream = _aes_decrypt_stream(src)
    assert len(stream.read(16)) == 16
    stream.close()
    assert src.closed
    assert src.close_calls == 1
    assert stream.closed
    stream.close()
    assert src.close_calls == 1


@requires("cryptography")
def test_aes_decrypt_stream_close_marks_wrapper_closed_when_source_raises() -> None:
    """A teardown OSError from the source still marks the wrapper closed.

    ADR 0014: teardown errors propagate. ``super().close()`` must still run so a
    later ``close()`` does not retry the source.
    """

    class BoomSource(io.BytesIO):
        def close(self) -> None:
            super().close()
            raise OSError("teardown")

    stream = _aes_decrypt_stream(BoomSource(b"\x00" * 60))
    stream.read(16)
    with pytest.raises(OSError, match="teardown"):
        stream.close()
    assert stream.closed
    stream.close()  # no-op; must not re-raise


def _truncated_aes_parts() -> tuple[bytes, bytes, bytes, bytes, bytes]:
    salt = b"\x11" * 16
    enc_key, auth_key, _ = derive_winzip_aes_keys(_PASSWORD, salt=salt, key_len=32)
    ciphertext = aes_ctr_le_encrypt(enc_key, _PAYLOAD)
    mac = hmac.new(auth_key, ciphertext, hashlib.sha1).digest()[:10]
    return salt, enc_key, auth_key, ciphertext, mac


@requires("cryptography")
@pytest.mark.parametrize(
    ("cut", "match"),
    [("ciphertext", "ciphertext before HMAC"), ("mac", "Truncated WinZip AES HMAC")],
)
def test_aes_stream_short_source_raises_truncated(cut: str, match: str) -> None:
    """A source that ends before the declared ciphertext or HMAC is a truncation."""
    _, enc_key, auth_key, ciphertext, mac = _truncated_aes_parts()
    body = ciphertext[:400] if cut == "ciphertext" else ciphertext + mac[:4]
    stream = WinZipAesDecryptStream(
        io.BytesIO(body), enc_key=enc_key, auth_key=auth_key, cipher_len=len(ciphertext)
    )
    with pytest.raises(TruncatedError, match=match):
        stream.read(-1)


@requires("cryptography")
@pytest.mark.parametrize(
    ("keep", "match"), [(8, "salt"), (17, "password-verification value")]
)
def test_aes_member_short_envelope_raises_truncated(keep: int, match: str) -> None:
    """A payload cut inside the salt or the verifier is a truncation too."""
    salt, *_ = _truncated_aes_parts()
    raw = io.BytesIO((salt + b"\x00\x00")[:keep])
    with pytest.raises(TruncatedError, match=match):
        open_winzip_aes_member(
            raw,
            aes=WinZipAesInfo(vendor_version=2, strength=3, actual_method=0),
            password=_PASSWORD,
            compress_size=len(_PAYLOAD) + 28,
        )


@requires("cryptography")
def test_aes_member_impossible_declared_size_stays_corruption() -> None:
    """A declared size too small to hold the envelope is a bad header, not a short read."""
    with pytest.raises(CorruptionError, match="too short") as exc:
        open_winzip_aes_member(
            io.BytesIO(b"\x00" * 64),
            aes=WinZipAesInfo(vendor_version=2, strength=3, actual_method=0),
            password=_PASSWORD,
            compress_size=10,
        )
    assert not isinstance(exc.value, TruncatedError)


@requires("cryptography")
def test_aes_multi_password_selects_winner() -> None:
    data = _build_aes_zip(
        payload=_PAYLOAD, password=_PASSWORD, vendor_version=2, strength=3, method=8
    )
    with open_archive(
        io.BytesIO(data), password=[b"nope", b"also-wrong", _PASSWORD]
    ) as ar:
        assert ar.read(ar.members()[0]) == _PAYLOAD


def _minimal_aes_zip_bytes() -> bytes:
    """A tiny method-99 ZIP with a valid 0x9901 extra (no cryptography needed to build).

    The ciphertext body is garbage — only used to exercise the cryptography-absent path,
    which fails before decryption.
    """
    name = b"x.txt"
    # AE-2, strength 1 (128), actual method STORED
    aes_extra = struct.pack("<H2sBH", 2, b"AE", 1, 0)
    extra = struct.pack("<HH", 0x9901, len(aes_extra)) + aes_extra
    # salt(8) + verify(2) + cipher(1) + hmac(10)
    body = b"\0" * (8 + 2 + 1 + 10)
    flags = 0x1
    local = struct.pack(
        "<IHHHHHIIIHH",
        0x04034B50,
        51,
        flags,
        99,
        0,
        0,
        0,
        len(body),
        1,
        len(name),
        len(extra),
    )
    local += name + extra + body
    cd = struct.pack(
        "<IHHHHHHIIIHHHHHII",
        0x02014B50,
        51,
        51,
        flags,
        99,
        0,
        0,
        0,
        len(body),
        1,
        len(name),
        len(extra),
        0,
        0,
        0,
        0,
        0,
    )
    cd += name + extra
    eocd = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, 1, 1, len(cd), len(local), 0)
    return local + cd + eocd


def test_aes_without_crypto_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    data = _minimal_aes_zip_bytes()
    import archivey.internal.backends.zip_aes as zip_aes_module

    monkeypatch.setattr(zip_aes_module, "_crypto_available", lambda: False)
    with open_archive(io.BytesIO(data), password=_PASSWORD) as ar:
        (member,) = ar.members()
        assert member.is_encrypted  # detection still works
        with pytest.raises(PackageNotInstalledError, match="cryptography"):
            ar.read(member)


def test_parse_aes_extra_roundtrip() -> None:
    extra = struct.pack("<HH", 0x9901, 7) + struct.pack("<H2sBH", 2, b"AE", 3, 8)
    info = parse_winzip_aes_extra(extra)
    assert info is not None
    assert info.is_ae2
    assert info.key_bits == 256
    assert info.actual_method == 8
    assert parse_winzip_aes_extra(b"") is None


def test_aes_stream_guard_runs_before_the_cryptography_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``[core-only]`` install must reach the typed error, not ``ImportError``.

    The other test here patches ``_crypto_available`` while the real package is still
    importable, so it cannot see which of the two lines runs first. Making the import
    itself fail can: with the guard below the import, this raised ``ImportError``.
    """
    import sys

    import archivey.internal.backends.zip_aes as zip_aes_module

    monkeypatch.setattr(zip_aes_module, "_crypto_available", lambda: False)
    monkeypatch.setitem(sys.modules, "cryptography.hazmat.primitives.ciphers", None)

    with pytest.raises(PackageNotInstalledError, match="cryptography"):
        WinZipAesDecryptStream(
            io.BytesIO(b""), enc_key=b"\0" * 32, auth_key=b"\0" * 32, cipher_len=0
        )


# The password ladder: several candidates are confirmed, not taken on pw_verify.

_COLLIDER = b"collides-on-pw-verify"


def _collide_pw_verify(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``_COLLIDER`` pass the 2-byte check while deriving its own (wrong) keys.

    A real collision takes ~65 000 PBKDF2 runs to find, so the test forges one: the
    reader's cheap check then admits a wrong password, as it does for 1 in 65 536.
    """
    import archivey.internal.backends.zip_aes as zip_aes

    original = zip_aes.derive_winzip_aes_keys

    def derive(password: bytes, *, salt: bytes, key_len: int) -> tuple[bytes, ...]:
        keys = original(password, salt=salt, key_len=key_len)
        if password != _COLLIDER:
            return keys
        right = original(_PASSWORD, salt=salt, key_len=key_len)
        return keys[0], keys[1], right[2]

    monkeypatch.setattr(zip_aes, "derive_winzip_aes_keys", derive)


@requires("cryptography")
@pytest.mark.parametrize("vendor_version", [1, 2], ids=["ae1", "ae2"])
@pytest.mark.parametrize("method", [0, 8], ids=["stored", "deflate"])
def test_aes_candidate_passing_pw_verify_does_not_shadow_the_right_one(
    monkeypatch: pytest.MonkeyPatch, vendor_version: int, method: int
) -> None:
    data = _build_aes_zip(
        payload=_PAYLOAD,
        password=_PASSWORD,
        vendor_version=vendor_version,
        strength=3,
        method=method,
    )
    _collide_pw_verify(monkeypatch)
    with open_archive(io.BytesIO(data), password=[_COLLIDER, _PASSWORD]) as ar:
        assert ar.read(ar.members()[0]) == _PAYLOAD


@requires("cryptography")
@pytest.mark.parametrize("method", [0, 8], ids=["stored", "deflate"])
def test_aes_only_colliding_candidates_report_the_ambiguity(
    monkeypatch: pytest.MonkeyPatch, method: int
) -> None:
    data = _build_aes_zip(
        payload=_PAYLOAD,
        password=_PASSWORD,
        vendor_version=2,
        strength=3,
        method=method,
    )
    _collide_pw_verify(monkeypatch)
    with open_archive(io.BytesIO(data), password=[_COLLIDER, b"plain-wrong"]) as ar:
        with pytest.raises(
            EncryptionError, match=r"password\(s\) may be wrong, or .* may be corrupt"
        ):
            ar.read(ar.members()[0])


@requires("cryptography")
def test_aes_lone_colliding_password_fails_on_the_hmac(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With one password there is nothing to choose between: the HMAC at EOF decides."""
    data = _build_aes_zip(
        payload=_PAYLOAD, password=_PASSWORD, vendor_version=2, strength=3, method=0
    )
    _collide_pw_verify(monkeypatch)
    with open_archive(io.BytesIO(data), password=_COLLIDER) as ar:
        with pytest.raises(CorruptionError, match="HMAC"):
            ar.read(ar.members()[0])


def _ctr_stream(payload: bytes, *, tamper_mac: bool = False) -> WinZipAesDecryptStream:
    """A decrypt stream over ``payload`` encrypted as WinZip AES-256, MAC appended."""
    enc_key, auth_key = os.urandom(32), os.urandom(32)
    cipher = aes_ctr_le_encrypt(enc_key, payload)
    mac = hmac.new(auth_key, cipher, hashlib.sha1).digest()[:10]
    if tamper_mac:
        mac = bytes([mac[0] ^ 1]) + mac[1:]
    return WinZipAesDecryptStream(
        io.BytesIO(cipher + mac),
        enc_key=enc_key,
        auth_key=auth_key,
        cipher_len=len(cipher),
    )


@requires("cryptography")
def test_aes_decrypt_stream_seeks_at_every_block_phase() -> None:
    # The counter for byte p is 1 + p // 16 and the stage discards p % 16 keystream
    # bytes; every phase of the first blocks, and backward after forward.
    payload = os.urandom(100)
    stream = _ctr_stream(payload)
    for target in [*range(0, 50), 99, 100, 17, 0, 64, 3]:
        assert stream.seek(target) == target
        assert stream.tell() == target
        assert stream.read(7) == payload[target : target + 7]
        assert stream.tell() == min(target + 7, 100)
    assert stream.seek(-10, io.SEEK_END) == 90
    assert stream.read() == payload[90:]
    assert stream.seek(-5, io.SEEK_CUR) == 95
    assert stream.read() == payload[95:]


@requires("cryptography")
def test_aes_decrypt_stream_seek_forfeits_the_hmac() -> None:
    payload = os.urandom(100)
    stream = _ctr_stream(payload, tamper_mac=True)
    stream.seek(5)
    assert stream.read() == payload[5:]  # no HMAC verdict after a seek


@requires("cryptography")
def test_aes_decrypt_stream_seek_to_current_position_keeps_the_hmac() -> None:
    payload = os.urandom(100)
    stream = _ctr_stream(payload, tamper_mac=True)
    assert stream.read(20) == payload[:20]
    stream.seek(20)
    with pytest.raises(CorruptionError, match="HMAC"):
        stream.read()


@pytest.mark.parametrize("method", [0, 8], ids=["stored", "deflate"])
@requires("cryptography")
def test_aes_member_seeks(method: int) -> None:
    payload = os.urandom(5000) + _PAYLOAD
    data = _build_aes_zip(
        payload=payload,
        password=_PASSWORD,
        vendor_version=1,
        strength=3,
        method=method,
    )
    with open_archive(
        io.BytesIO(data), password=_PASSWORD, seekable_members=True
    ) as ar:
        with ar.open(ar.members()[0]) as stream:
            assert stream.seekable()
            stream.seek(4099)
            assert stream.read(100) == payload[4099:4199]
            stream.seek(17)
            assert stream.read() == payload[17:]
            stream.seek(0)
            assert stream.read() == payload


@requires("cryptography")
def test_aes_stored_out_of_range_seeks_match_an_unencrypted_member() -> None:
    """A STORED member's seek reaches the decrypt stage directly, and lands where the
    same member unencrypted does: a relative underflow clamps to 0 and a past-end
    seek keeps its position."""
    payload = b"hello"
    encrypted = _build_aes_zip(
        payload=payload, password=_PASSWORD, vendor_version=2, strength=3, method=0
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("x.txt", payload)
    results = []
    for blob in (encrypted, buf.getvalue()):
        with open_archive(
            io.BytesIO(blob), password=_PASSWORD, seekable_members=True
        ) as ar:
            with ar.open(ar.members()[0]) as stream:
                out: list[object] = []
                for offset, whence in (
                    (-10, io.SEEK_END),
                    (-100, io.SEEK_CUR),
                    (1000, io.SEEK_SET),
                ):
                    out += [stream.seek(offset, whence), stream.tell(), stream.read(2)]
                results.append(out)
    assert results[0] == results[1] == [0, 0, b"he", 0, 0, b"he", 1000, 1000, b""]
