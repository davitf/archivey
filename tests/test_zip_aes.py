"""WinZip AES (AE-1 / AE-2) ZIP member decryption tests."""

from __future__ import annotations

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
)
from archivey.internal.zip_aes import parse_winzip_aes_extra
from archivey.types import CompressionAlgorithm, HashAlgorithm
from tests.conftest import requires, requires_binary
from tests.zip_aes_fixture import build_aes_zip

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


@requires("cryptography")
def test_aes_wrong_password_fails_fast() -> None:
    data = _build_aes_zip(
        payload=_PAYLOAD, password=_PASSWORD, vendor_version=2, strength=3, method=8
    )
    with open_archive(io.BytesIO(data), password=b"wrong") as ar:
        with pytest.raises(EncryptionError, match="Wrong password"):
            ar.read(ar.members()[0])


@requires("cryptography")
def test_aes_tampered_hmac_raises_corruption() -> None:
    data = _build_aes_zip(
        payload=_PAYLOAD,
        password=_PASSWORD,
        vendor_version=2,
        strength=3,
        method=0,
        tamper_hmac=True,
    )
    with open_archive(io.BytesIO(data), password=_PASSWORD) as ar:
        with pytest.raises(CorruptionError, match="HMAC"):
            ar.read(ar.members()[0])


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

    The ciphertext body is garbage — only used to exercise the ``[crypto]``-absent path,
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
    import archivey.internal.zip_aes as zip_aes_module

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
