"""Encrypted ZIP members read from a source that cannot seek, with one password.

The pass decrypts a member whose local header states its size, and a bit-3 STORED
ZipCrypto member (its end is found on the ciphertext). Each read must give what the
seekable read gives: the same bytes, or the same kind of error.
"""

from __future__ import annotations

import io
import struct
import zipfile
import zlib
from pathlib import Path

import pytest

import archivey
from archivey.diagnostics import DiagnosticCode, EncryptedVerificationContext
from archivey.exceptions import (
    ArchiveyError,
    CorruptionError,
    EncryptionError,
    UnsupportedFeatureError,
)
from archivey.internal.backends.zip_reader import ZipReadBackend
from tests.conftest import requires
from tests.streams_util import NonSeekableBytesIO
from tests.zipcrypto import (
    build_stored_zipcrypto_zip,
    build_zipcrypto_zip,
    corrupt_zipcrypto_payload,
)

_COLLISION = (
    Path(__file__).parent / "fixtures" / "zipcrypto" / "check_byte_collision.zip"
)
_PASSWORD = b"secret"
_TEXT = b"the quick brown fox jumps over the lazy dog\n" * 40


@pytest.fixture(autouse=True)
def _pipes_open(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ZipReadBackend, "SUPPORTS_STREAMING_NON_SEEKABLE", True)


def _aes_zip(**kwargs: object) -> bytes:
    from tests.zip_aes_fixture import build_aes_zip

    return build_aes_zip([(b"a.txt", _TEXT)], password=_PASSWORD, **kwargs)  # type: ignore[arg-type]


def _with_plain_member(data: bytes) -> bytes:
    """``data`` with an unencrypted ``plain.txt`` after its members, so a test can
    see that the pass goes on past the encrypted one."""
    with zipfile.ZipFile(io.BytesIO(data)) as src:
        entries = src.infolist()
    eocd = data.rindex(b"PK\x05\x06")
    cd_size, cd_offset = struct.unpack_from("<II", data, eocd + 12)
    body = data[:cd_offset]
    directory = data[cd_offset : cd_offset + cd_size]
    name = b"plain.txt"
    payload = b"plain"
    crc = zlib.crc32(payload)
    local = (
        struct.pack(
            "<IHHHHHIIIHH",
            0x04034B50,
            20,
            0,
            0,
            0,
            0,
            crc,
            len(payload),
            len(payload),
            len(name),
            0,
        )
        + name
        + payload
    )
    central = (
        struct.pack(
            "<IHHHHHHIIIHHHHHII",
            0x02014B50,
            20,
            20,
            0,
            0,
            0,
            0,
            crc,
            len(payload),
            len(payload),
            len(name),
            0,
            0,
            0,
            0,
            0,
            len(body),
        )
        + name
    )
    count = len(entries) + 1
    directory += central
    end = struct.pack(
        "<IHHHHIIH",
        0x06054B50,
        0,
        0,
        count,
        count,
        len(directory),
        len(body) + len(local),
        0,
    )
    return body + local + directory + end


def _bit3_stored_zipcrypto(
    data: bytes, *, dos_time: int = 0x5A3C, stored: bytes | None = None
) -> bytes:
    """One STORED ZipCrypto member written as a pipe writer would: bit 3, zero sizes
    and CRC in the local header, a descriptor after the data. The check byte is then
    the DOS time's high byte. ``stored`` replaces the encrypted data."""
    from tests.zipcrypto import _encrypt

    name = b"a.txt"
    crc = zlib.crc32(data)
    enc = (
        _encrypt(_PASSWORD, (dos_time >> 8) & 0xFF, data) if stored is None else stored
    )
    flags = 0x1 | 0x8
    local = (
        struct.pack(
            "<IHHHHHIIIHH", 0x04034B50, 20, flags, 0, dos_time, 0, 0, 0, 0, len(name), 0
        )
        + name
        + enc
        + struct.pack("<IIII", 0x08074B50, crc, len(enc), len(data))
    )
    central = (
        struct.pack(
            "<IHHHHHHIIIHHHHHII",
            0x02014B50,
            20,
            20,
            flags,
            0,
            dos_time,
            0,
            crc,
            len(enc),
            len(data),
            len(name),
            0,
            0,
            0,
            0,
            0,
            0,
        )
        + name
    )
    end = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, 1, 1, len(central), len(local), 0)
    return local + central + end


def _fixtures() -> list[pytest.ParameterSet]:
    zc = build_zipcrypto_zip
    return [
        pytest.param(
            lambda: zc(_PASSWORD, b"a.txt", _TEXT, compression=zipfile.ZIP_STORED),
            id="zipcrypto-stored",
        ),
        pytest.param(lambda: zc(_PASSWORD, b"a.txt", _TEXT), id="zipcrypto-deflate"),
        pytest.param(
            lambda: zc(_PASSWORD, b"a.txt", _TEXT, compression=zipfile.ZIP_BZIP2),
            id="zipcrypto-bzip2",
        ),
        pytest.param(
            lambda: zc(_PASSWORD, b"a.txt", _TEXT, compression=zipfile.ZIP_LZMA),
            id="zipcrypto-lzma",
        ),
        pytest.param(lambda: _bit3_stored_zipcrypto(_TEXT), id="zipcrypto-bit3-stored"),
        pytest.param(
            lambda: _bit3_stored_zipcrypto(b""), id="zipcrypto-bit3-stored-empty"
        ),
        pytest.param(
            lambda: _bit3_stored_zipcrypto(b"", stored=b"\x01\x02\x03\x04"),
            id="zipcrypto-bit3-shorter-than-its-header",
        ),
        pytest.param(
            lambda: _aes_zip(vendor_version=1, method=0),
            id="aes-ae1-stored",
            marks=requires("cryptography"),
        ),
        pytest.param(
            lambda: _aes_zip(vendor_version=1, method=8),
            id="aes-ae1-deflate",
            marks=requires("cryptography"),
        ),
        pytest.param(
            lambda: _aes_zip(vendor_version=2, method=8),
            id="aes-ae2-deflate",
            marks=requires("cryptography"),
        ),
    ]


def _file_read(data: bytes, password: object) -> dict[str, object]:
    out: dict[str, object] = {}
    with archivey.open_archive(io.BytesIO(data), password=password) as reader:  # type: ignore[arg-type]
        for member in reader.members():
            try:
                out[member.name] = reader.read(member)
            except ArchiveyError as exc:
                out[member.name] = (type(exc), str(exc))
    return out


def _pipe_read(data: bytes, password: object) -> dict[str, object]:
    out: dict[str, object] = {}
    with archivey.open_archive(
        NonSeekableBytesIO(data),
        streaming=True,
        password=password,  # type: ignore[arg-type]
    ) as reader:
        for member, stream in reader.stream_members():
            assert stream is not None
            try:
                out[member.name] = stream.read()
            except ArchiveyError as exc:
                out[member.name] = (type(exc), str(exc))
    return out


@pytest.mark.parametrize("build", _fixtures())
def test_a_pipe_decrypts_as_the_file_read_does(build) -> None:  # noqa: ANN001
    data = _with_plain_member(build())
    expected = _file_read(data, _PASSWORD)
    assert expected["plain.txt"] == b"plain"
    assert _pipe_read(data, _PASSWORD) == expected
    assert _pipe_read(data, _PASSWORD.decode()) == expected


@pytest.mark.parametrize("build", _fixtures())
def test_a_wrong_or_missing_password_fails_as_from_a_file(build) -> None:  # noqa: ANN001
    data = _with_plain_member(build())
    for password in (b"not the password", None):
        expected = _file_read(data, password)
        kind, _ = expected["a.txt"]  # type: ignore[misc]
        # The member shorter than its encryption header is refused for that first.
        assert issubclass(kind, (EncryptionError, CorruptionError))
        assert _pipe_read(data, password) == expected


def test_a_real_bit3_stored_member_reads_and_a_bit3_deflated_one_is_refused() -> None:
    """Info-ZIP writes both members with a descriptor; only decrypting the deflated
    one would find where it ends."""
    data = _COLLISION.read_bytes()
    with archivey.open_archive(io.BytesIO(data), password=_PASSWORD) as reader:
        stored = reader.read(
            next(m for m in reader.members() if m.name == "stored.txt")
        )
    with archivey.open_archive(
        NonSeekableBytesIO(data), streaming=True, password=_PASSWORD
    ) as reader:
        passes = reader.stream_members()
        member, stream = next(passes)
        assert member.name == "stored.txt" and stream is not None
        assert stream.read() == stored
        member, stream = next(passes)
        assert member.name == "deflated.txt" and stream is not None
        with pytest.raises(UnsupportedFeatureError, match="only decrypting"):
            stream.read()


def test_a_password_that_only_passes_the_check_byte_fails_as_unverified() -> None:
    """``wrong896`` passes ``stored.txt``'s one-byte check (fixture README); only the
    CRC at the end shows it is wrong, and then it may be a wrong password or damage."""
    data = _COLLISION.read_bytes()
    with archivey.open_archive(io.BytesIO(data), password="wrong896") as reader:
        with pytest.raises(EncryptionError) as from_file:
            reader.read(next(m for m in reader.members() if m.name == "stored.txt"))
    with archivey.open_archive(
        NonSeekableBytesIO(data), streaming=True, password="wrong896"
    ) as reader:
        passes = reader.stream_members()
        _, stream = next(passes)
        assert stream is not None
        with pytest.raises(EncryptionError) as from_pipe:
            stream.read()
    assert str(from_pipe.value) == str(from_file.value)


@pytest.mark.parametrize("method", [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED])
def test_damage_under_a_zipcrypto_password_fails_as_from_a_file(method: int) -> None:
    data = corrupt_zipcrypto_payload(
        build_zipcrypto_zip(_PASSWORD, b"a.txt", _TEXT, compression=method)
    )
    expected = _file_read(data, _PASSWORD)
    assert expected["a.txt"][0] is EncryptionError  # type: ignore[index]
    assert _pipe_read(data, _PASSWORD) == expected


@requires("cryptography")
def test_a_tampered_aes_hmac_fails_as_from_a_file() -> None:
    data = _aes_zip(vendor_version=2, method=8, tamper_hmac=True)
    expected = _file_read(data, _PASSWORD)
    assert expected["a.txt"][0] is CorruptionError  # type: ignore[index]
    assert _pipe_read(data, _PASSWORD) == expected


def test_several_passwords_are_refused_for_now() -> None:
    data = build_stored_zipcrypto_zip([(_PASSWORD, b"a.txt", _TEXT)])
    with archivey.open_archive(
        NonSeekableBytesIO(data), streaming=True, password=["other", "secret"]
    ) as reader:
        passes = reader.stream_members()
        _, stream = next(passes)
        assert stream is not None
        with pytest.raises(UnsupportedFeatureError, match="give one password"):
            stream.read()


def _unverified(reader: archivey.ArchiveReader) -> list[EncryptedVerificationContext]:
    return [
        d.context  # type: ignore[misc]
        for d in reader.diagnostics.retained
        if d.code is DiagnosticCode.ENCRYPTED_MEMBER_UNVERIFIED
    ]


@pytest.mark.parametrize("read_all", [True, False])
def test_a_partial_read_is_reported_as_unverified(read_all: bool) -> None:
    data = _with_plain_member(
        build_zipcrypto_zip(_PASSWORD, b"a.txt", _TEXT, compression=zipfile.ZIP_STORED)
    )
    with archivey.open_archive(
        NonSeekableBytesIO(data), streaming=True, password=_PASSWORD
    ) as reader:
        for member, stream in reader.stream_members():
            assert stream is not None
            if member.name == "a.txt":
                with stream:
                    if read_all:
                        assert stream.read() == _TEXT
                    else:
                        # Even all of its bytes: the CRC runs on the read that finds
                        # the end.
                        assert stream.read(len(_TEXT)) == _TEXT
        contexts = _unverified(reader)
    if read_all:
        assert contexts == []
    else:
        (context,) = contexts
        assert context.member_name == "a.txt"
        assert context.check == "weak_open_check"
        assert context.reason == "partial_read"


def _link_targets(data: bytes, password: object, *, pipe: bool) -> list[object]:
    source = NonSeekableBytesIO(data) if pipe else io.BytesIO(data)
    with archivey.open_archive(source, streaming=pipe, password=password) as reader:  # type: ignore[arg-type]
        members = reader.members_report().members
        reasons = [
            getattr(d.context, "reason", None)
            for d in reader.diagnostics.retained
            if d.code is DiagnosticCode.SYMLINK_TARGET_UNAVAILABLE
        ]
        return [(m.name, m.type, m.link_target) for m in members] + reasons


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(
            lambda: build_zipcrypto_zip(
                _PASSWORD, b"link", b"target.txt", unix_mode=0o120777
            ),
            id="zipcrypto",
        ),
        pytest.param(
            lambda: _aes_zip_link(),
            id="aes",
            marks=requires("cryptography"),
        ),
    ],
)
@pytest.mark.parametrize("password", [_PASSWORD, b"not the password", None])
def test_an_encrypted_link_target_is_read_as_from_a_file(build, password) -> None:  # noqa: ANN001
    """The pass decrypts a short member for its target before the directory says
    it is a link, without the caller asking."""
    data = build()
    assert _link_targets(data, password, pipe=True) == _link_targets(
        data, password, pipe=False
    )


def _aes_zip_link() -> bytes:
    from tests.zip_aes_fixture import build_aes_zip

    return build_aes_zip(
        [(b"link", b"target.txt")], password=_PASSWORD, unix_mode=0o120777
    )


def _method_6_link() -> bytes:
    """A ZipCrypto symlink whose method says Implode in both headers: the password
    is right, and the codec is refused once the stage is built."""
    data = bytearray(
        build_zipcrypto_zip(
            _PASSWORD,
            b"link",
            b"target.txt",
            compression=zipfile.ZIP_STORED,
            unix_mode=0o120777,
        )
    )
    struct.pack_into("<H", data, 8, 6)
    struct.pack_into("<H", data, data.index(b"PK\x01\x02") + 10, 6)
    return bytes(data)


def _counted_decrypts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    from archivey.internal.backends import zip_forward_reader

    calls: list[str] = []
    original = zip_forward_reader._PassData._decrypted

    def counted(self, loc):  # noqa: ANN001, ANN202
        calls.append(self.member.name)
        return original(self, loc)

    monkeypatch.setattr(zip_forward_reader._PassData, "_decrypted", counted)
    return calls


def test_a_link_whose_codec_is_refused_says_so_as_from_a_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = _method_6_link()
    calls = _counted_decrypts(monkeypatch)
    with pytest.raises(UnsupportedFeatureError) as from_file:
        _link_targets(data, _PASSWORD, pipe=False)
    with pytest.raises(UnsupportedFeatureError) as from_pipe:
        _link_targets(data, _PASSWORD, pipe=True)
    assert str(from_pipe.value) == str(from_file.value)
    assert calls == ["link"]


def test_a_failed_open_is_not_retried_on_the_data_past_the_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stage read the encryption header; a second open would read the next
    twelve bytes as one. The caller's error stands, for the link target too."""
    data = _method_6_link()
    calls = _counted_decrypts(monkeypatch)
    with archivey.open_archive(io.BytesIO(data), password=_PASSWORD) as reader:
        with pytest.raises(UnsupportedFeatureError) as from_file:
            reader.read(reader.members()[0])
    with archivey.open_archive(
        NonSeekableBytesIO(data), streaming=True, password=_PASSWORD
    ) as reader:
        passes = reader.stream_members()
        _, stream = next(passes)
        assert stream is not None
        for _ in range(2):
            with pytest.raises(UnsupportedFeatureError) as from_pipe:
                stream.read()
            assert str(from_pipe.value) == str(from_file.value)
        # The directory makes it a link, and its target raises what the read did,
        # as the file's listing does.
        with pytest.raises(UnsupportedFeatureError) as at_end:
            next(passes)
        assert str(at_end.value) == str(from_file.value)
    assert calls == ["link"]


@requires("cryptography")
def test_no_key_is_derived_for_a_member_nobody_reads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Short members are kept as ciphertext in case the directory makes them links;
    only a link's is decrypted, as a file read only reads a link's target."""
    from tests.zip_aes_fixture import build_aes_zip

    data = build_aes_zip(
        [(f"m{i}.txt".encode(), b"x") for i in range(20)], password=_PASSWORD
    )
    calls = _counted_decrypts(monkeypatch)
    with archivey.open_archive(
        NonSeekableBytesIO(data), streaming=True, password=_PASSWORD
    ) as reader:
        for _ in reader.stream_members():
            pass
        assert len(reader.members_report().members) == 20
    assert calls == []
