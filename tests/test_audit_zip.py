"""Audit reproducers for the ZIP backend (extraction-security audit).

Each test asserts the promised behaviour and is marked ``xfail(strict=True)`` while the
bug stands; the ``reason`` names the defect. Remove the marker when the fix lands.
"""

from __future__ import annotations

import base64
import io
import struct
import zipfile
import zlib
from pathlib import Path

import pytest

import archivey
from archivey.exceptions import EncryptionError, UnsupportedFeatureError
from tests.zipcrypto import _Keys, build_zipcrypto_zip

# 7-Zip 16.02 `7z a -tzip -mm=LZMA:eos=off`: one LZMA (method 14) member `f.txt` with
# general-purpose bit 1 clear, so the LZMA stream has no end-of-stream marker and the
# decoder must stop at the declared uncompressed size (APPNOTE 4.4.4, bit 1). 7-Zip
# and stdlib zipfile both read it.
_LZMA_NO_EOS_ZIP = base64.b64decode(
    "UEsDBD8AAAAOAGliPF0isOxhKQAAAEYAAAAFAAAAZi50eHQXAQUAXQAQAAAANhpKHwigJcHelLqQzDsr"
    "HzU68l/Vnn5zst7TcnaHoFBLAQI/Az8AAAAOAGliPF0isOxhKQAAAEYAAAAFACQAAAAAAAAAIICkgQAA"
    "AABmLnR4dAoAIAAAAAAAAQAYADqmUpRDT90BAAAAAAAAAAAAAAAAAAAAAFBLBQYAAAAAAQABAFcAAABM"
    "AAAAAAA="
)
_LZMA_NO_EOS_PAYLOAD = b"".join(b"line %d\n" % i for i in range(10))


@pytest.mark.xfail(
    strict=True,
    reason=(
        "AUDIT: ZIP LZMA member without an EOS marker (flag bit 1 clear) is not "
        "bounded to its declared size; reads past the end raise TruncatedError"
    ),
)
def test_lzma_member_without_eos_marker_extracts(tmp_path: Path) -> None:
    with zipfile.ZipFile(io.BytesIO(_LZMA_NO_EOS_ZIP)) as zf:
        info = zf.infolist()[0]
        assert info.compress_type == zipfile.ZIP_LZMA
        assert not info.flag_bits & 0x2
        assert zf.read(info) == _LZMA_NO_EOS_PAYLOAD  # stdlib reads it

    archivey.extract(io.BytesIO(_LZMA_NO_EOS_ZIP), tmp_path / "out")
    assert (tmp_path / "out" / "f.txt").read_bytes() == _LZMA_NO_EOS_PAYLOAD

    # The canonical read loop: keep reading until b"".
    with archivey.open_archive(io.BytesIO(_LZMA_NO_EOS_ZIP)) as ar:
        (member,) = ar.members()
        with ar.open(member) as stream:
            chunks = []
            while chunk := stream.read(16):
                chunks.append(chunk)
    assert b"".join(chunks) == _LZMA_NO_EOS_PAYLOAD


# A ZipCrypto LZMA member written with password b"right" and the 11 random header bytes
# below. They were searched for (offline, in C) so that the wrong password b"wrong"
# passes the one-byte header check (1 in 256) and decrypts the ZIP LZMA header to a
# well-formed 5-byte properties blob whose dictionary size is over 2 GiB. That is what
# about one colliding wrong password in five does to any ZipCrypto LZMA member.
_LZMA_COLLISION_HEADER = bytes.fromhex("40c0c20000000000555555")
_LZMA_COLLISION_PAYLOAD = b"archivey audit lzma payload\n" * 64


def _zipcrypto_lzma_with_colliding_wrong_password() -> bytes:
    data = _LZMA_COLLISION_PAYLOAD
    name = b"a.txt"
    blob = bytearray(
        build_zipcrypto_zip(b"right", name, data, compression=zipfile.ZIP_LZMA)
    )
    start = 30 + len(name)
    (compressed_size,) = struct.unpack_from("<I", blob, 18)
    # Decrypt the builder's member to recover the ZIP LZMA body, then re-encrypt it
    # behind the searched header.
    keys = _Keys(b"right")
    plaintext = bytearray()
    for cipher in blob[start : start + compressed_size]:
        plain = cipher ^ keys.keystream_byte()
        plaintext.append(plain)
        keys.update(plain)
    stored = bytes(plaintext[12:])
    header = _LZMA_COLLISION_HEADER + bytes([zlib.crc32(data) >> 24])
    keys = _Keys(b"right")
    encrypted = bytearray()
    for plain in header + stored:
        encrypted.append(plain ^ keys.keystream_byte())
        keys.update(plain)
    assert blob[start + len(encrypted) : start + len(encrypted) + 4] == b"PK\x01\x02"
    blob[start : start + len(encrypted)] = encrypted
    return bytes(blob)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "AUDIT: a wrong ZipCrypto candidate's garbage LZMA dict size raises "
        "ResourceLimitError, which aborts the password search before the right one"
    ),
)
def test_colliding_wrong_candidate_does_not_hide_the_right_password() -> None:
    blob = _zipcrypto_lzma_with_colliding_wrong_password()
    with archivey.open_archive(io.BytesIO(blob), password="right") as ar:
        assert ar.read(ar.members()[0]) == _LZMA_COLLISION_PAYLOAD  # fixture sanity

    # format-zip "Confirm multi-candidate ZipCrypto passwords": a wrong candidate that
    # passes the verification byte is rejected and the correct one reads (LZMA row).
    with archivey.open_archive(io.BytesIO(blob), password=["wrong", "right"]) as ar:
        assert ar.read(ar.members()[0]) == _LZMA_COLLISION_PAYLOAD


@pytest.mark.xfail(
    strict=True,
    reason=(
        "AUDIT: a lone colliding wrong ZipCrypto password on an LZMA member raises "
        "ResourceLimitError instead of the ambiguous EncryptionError"
    ),
)
def test_colliding_lone_wrong_password_is_reported_as_password_or_damage() -> None:
    blob = _zipcrypto_lzma_with_colliding_wrong_password()
    # format-zip: for an LZMA or PPMd member the open raises EncryptionError saying
    # the password may be wrong or the member corrupt.
    with archivey.open_archive(io.BytesIO(blob), password="wrong") as ar:
        member = ar.members()[0]
        with pytest.raises(EncryptionError):
            ar.read(member)


def _patched_data_zip(payload: bytes) -> bytes:
    """One DEFLATE member with general-purpose bit 5 ("compressed patched data") set."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        info = zipfile.ZipInfo("a.txt", (2020, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        zf.writestr(info, payload)
    blob = bytearray(buf.getvalue())
    cd = blob.rfind(b"PK\x01\x02")
    for flags_at in (6, cd + 8):
        (flags,) = struct.unpack_from("<H", blob, flags_at)
        struct.pack_into("<H", blob, flags_at, flags | 0x20)
    return bytes(blob)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "AUDIT: flag bit 5 (PKWARE compressed patched data) is ignored; the patch "
        "stream is returned as the member's content"
    ),
)
def test_compressed_patched_data_member_is_refused() -> None:
    # The body is a patch against some other file, not the file. stdlib zipfile
    # refuses it (NotImplementedError "compressed patched data (flag bit 5)"); the
    # spec says an unsupported entry gets UnsupportedFeatureError and "no guessed
    # output". Here the stored CRC is the one of the bytes returned, so no check
    # notices.
    blob = _patched_data_zip(b"patch-stream-bytes " * 20)
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        with pytest.raises(NotImplementedError):
            zf.read("a.txt")
    with archivey.open_archive(io.BytesIO(blob)) as ar:
        member = ar.members()[0]
        with pytest.raises(UnsupportedFeatureError):
            ar.read(member)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "AUDIT: encoding='idna' passes open_archive validation but members() raises a "
        "raw UnicodeError from the raw_name re-encode (surrogateescape unsupported)"
    ),
)
def test_idna_encoding_does_not_raise_raw_unicode_error() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("hello.txt", b"x")
    with archivey.open_archive(io.BytesIO(buf.getvalue()), encoding="idna") as ar:
        try:
            members = ar.members()
        except archivey.ArchiveyError:
            return  # a typed refusal is acceptable
        assert [m.name for m in members] == ["hello.txt"]
