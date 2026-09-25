"""Multi-password ZIP disambiguation for traditional ZipCrypto."""

from __future__ import annotations

import contextlib
import io
import os
import tempfile
import zipfile
import zlib
from collections.abc import Callable, Iterator
from typing import Any, BinaryIO, cast

import pytest

from archivey import (
    DiagnosticCode,
    MemberType,
    PasswordRequest,
    SymlinkTargetContext,
    open_archive,
)
from archivey.exceptions import (
    ArchiveyError,
    ArchiveyUsageError,
    CorruptionError,
    EncryptionError,
    TruncatedError,
)
from archivey.internal import password_confirm
from archivey.internal.backends import zip_reader, zipcrypto
from archivey.internal.password import is_wrong_password
from archivey.internal.password_confirm import PASSWORD_CONFIRM_PREFIX_BYTES
from tests.conftest import requires_zstd, zstd_backend
from tests.zipcrypto import (
    build_zipcrypto_zip,
    corrupt_zipcrypto_payload,
    find_check_byte_collision,
    find_check_byte_collisions,
)

RIGHT = b"very_secret_password"
DATA = b"This is very secret" * 8
NAME = "very_secret.txt"
UNCONFIRMED = r"password may be wrong .*or the encrypted member may be corrupt"
PasswordArg = (
    str | bytes | list[str | bytes] | Callable[[PasswordRequest], str | bytes | None]
)

COMPRESSION_METHODS = [
    pytest.param(zipfile.ZIP_STORED, id="stored"),
    pytest.param(zipfile.ZIP_DEFLATED, id="deflated"),
    pytest.param(zipfile.ZIP_BZIP2, id="bzip2"),
    pytest.param(zipfile.ZIP_LZMA, id="lzma"),
]


@contextlib.contextmanager
def _open_decoding_header(ar: Any, compression: int) -> Iterator[Any]:
    """``ar.open(NAME)``; for LZMA, the open is where a wrong key first shows.

    The ZIP LZMA header is peeled off the plaintext when the member opens, so garbage
    fails there rather than at the first read. Every other method opens cleanly.
    """
    if compression == zipfile.ZIP_LZMA:
        # Raises; the caller's ``pytest.raises`` sees it.
        ar.open(NAME)
        pytest.fail("a wrong key's LZMA header should not open")
    with ar.open(NAME) as stream:
        yield stream


def _read_member(blob: bytes, password: PasswordArg) -> bytes:
    with open_archive(io.BytesIO(blob), password=password) as ar:
        member = next(m for m in ar.members() if m.name == NAME)
        with ar.open(member) as fh:
            return fh.read()


@pytest.mark.parametrize("compression", COMPRESSION_METHODS)
def test_wrong_candidate_false_accept_does_not_shadow_right_password(
    compression: int,
) -> None:
    blob = build_zipcrypto_zip(RIGHT, NAME.encode(), DATA, compression=compression)
    collider = find_check_byte_collision(blob, NAME, RIGHT)

    assert collider != RIGHT
    assert _read_member(blob, [collider, RIGHT]) == DATA


def test_all_wrong_colliding_passwords_report_ambiguous_failure() -> None:
    blob = build_zipcrypto_zip(RIGHT, NAME.encode(), DATA)
    colliders = find_check_byte_collisions(blob, NAME, RIGHT, count=2)

    with pytest.raises(
        EncryptionError, match=r"password\(s\) may be wrong, or .* may be corrupt"
    ):
        _read_member(blob, colliders)


def test_provider_continues_after_colliding_password() -> None:
    blob = build_zipcrypto_zip(RIGHT, NAME.encode(), DATA)
    collider = find_check_byte_collision(blob, NAME, RIGHT)
    seen: list[PasswordRequest] = []

    def provider(request: PasswordRequest) -> bytes | None:
        seen.append(request)
        return {1: collider, 2: RIGHT}.get(request.attempt)

    assert _read_member(blob, provider) == DATA
    assert [request.attempt for request in seen] == [1, 2]
    assert all(request.member is not None for request in seen)


@pytest.mark.parametrize(
    "passwords", [[RIGHT], [RIGHT, RIGHT]], ids=["one", "duplicate"]
)
@pytest.mark.parametrize("compression", COMPRESSION_METHODS)
def test_single_distinct_candidate_is_not_eagerly_read(
    passwords: list[bytes], compression: int
) -> None:
    blob = corrupt_zipcrypto_payload(
        build_zipcrypto_zip(RIGHT, NAME.encode(), DATA, compression=compression)
    )

    with open_archive(io.BytesIO(blob), password=passwords) as ar:
        with pytest.raises(EncryptionError, match=UNCONFIRMED):
            with _open_decoding_header(ar, compression) as stream:
                stream.read()


@pytest.mark.parametrize("compression", COMPRESSION_METHODS)
def test_single_colliding_password_is_reported_as_a_password_failure(
    compression: int,
) -> None:
    """A lone wrong password that passes the check byte is not called corruption."""
    blob = build_zipcrypto_zip(RIGHT, NAME.encode(), DATA, compression=compression)
    collider = find_check_byte_collision(blob, NAME, RIGHT)

    with pytest.raises(EncryptionError, match=UNCONFIRMED) as caught:
        _read_member(blob, collider)
    assert type(caught.value) is EncryptionError
    # Only the check byte could have said "wrong"; it did not, so no wrong-password mark.
    assert not is_wrong_password(caught.value)
    assert _read_member(blob, RIGHT) == DATA


@pytest.mark.parametrize(
    "compression", [p for p in COMPRESSION_METHODS if p.id != "stored"]
)
def test_single_colliding_password_fails_a_forward_seek(compression: int) -> None:
    """A forward seek decodes what it skips, so the seek itself names both causes."""
    blob = build_zipcrypto_zip(RIGHT, NAME.encode(), DATA, compression=compression)
    collider = find_check_byte_collision(blob, NAME, RIGHT)

    with open_archive(io.BytesIO(blob), password=collider, seekable_members=True) as ar:
        with pytest.raises(EncryptionError, match=UNCONFIRMED):
            with _open_decoding_header(ar, compression) as stream:
                stream.seek(len(DATA))


def test_single_colliding_password_stored_seek_reports_unverified() -> None:
    """A STORED member has no decoder to object, and a seek forfeits the CRC.

    So the seek succeeds, and closing the stream reports that the bytes were never
    checked (ADR 0014: a seek off the read frontier gives up the checksum).
    """
    blob = build_zipcrypto_zip(
        RIGHT, NAME.encode(), DATA, compression=zipfile.ZIP_STORED
    )
    collider = find_check_byte_collision(blob, NAME, RIGHT)

    with open_archive(io.BytesIO(blob), password=collider, seekable_members=True) as ar:
        with ar.open(NAME) as stream:
            stream.seek(len(DATA) - 4)
            assert len(stream.read()) == 4
        codes = [d.code for d in ar.diagnostics.retained]
    assert DiagnosticCode.ENCRYPTED_MEMBER_UNVERIFIED in codes


@pytest.mark.parametrize("compression", COMPRESSION_METHODS)
def test_single_colliding_password_fails_readinto(compression: int) -> None:
    """A caller's ``readinto`` on the member names both causes too."""
    blob = build_zipcrypto_zip(RIGHT, NAME.encode(), DATA, compression=compression)
    collider = find_check_byte_collision(blob, NAME, RIGHT)

    with open_archive(io.BytesIO(blob), password=collider) as ar:
        with pytest.raises(EncryptionError, match=UNCONFIRMED):
            with _open_decoding_header(ar, compression) as stream:
                buf = bytearray(len(DATA) + 1)
                while stream.readinto(buf):
                    pass


class _FailingInner(io.RawIOBase):
    """An inner stream whose every read and seek raises ``error``."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def read(self, size: int = -1, /) -> bytes:
        raise self.error

    def readinto(self, b: Any, /) -> int:
        raise self.error

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        raise self.error


def _call(stream: Any, call: str) -> None:
    if call == "read":
        stream.read(10)
    elif call == "readinto":
        stream.readinto(bytearray(10))
    else:
        stream.seek(5)


@pytest.mark.parametrize(
    ("error", "payload_complete"),
    [
        pytest.param(CorruptionError("Digest mismatch for 'crc32'"), True, id="crc"),
        pytest.param(CorruptionError("invalid block type"), False, id="codec"),
        pytest.param(TruncatedError("File is truncated"), True, id="decoder-short"),
    ],
)
@pytest.mark.parametrize("call", ["read", "readinto", "seek"])
def test_unconfirmed_stream_translates_integrity_failures(
    error: Exception, payload_complete: bool, call: str
) -> None:
    """Each entry point, ``readinto``'s zero-copy path included, names both causes."""
    stream = zip_reader._UnconfirmedZipCryptoStream(
        cast(Any, _FailingInner(error)), payload_complete=lambda: payload_complete
    )
    with pytest.raises(EncryptionError, match=UNCONFIRMED) as caught:
        _call(stream, call)
    assert caught.value.__cause__ is error


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(TruncatedError("File is truncated"), id="truncated-file"),
        pytest.param(OSError("source storage unavailable"), id="oserror"),
        pytest.param(ArchiveyUsageError("already closed"), id="lifecycle"),
    ],
)
@pytest.mark.parametrize("call", ["read", "readinto", "seek"])
def test_unconfirmed_stream_passes_other_failures_through(
    error: Exception, call: str
) -> None:
    """Only a failure a wrong password can cause becomes the ambiguous error.

    A payload the file cuts short, an I/O failure or a lifecycle fault stays itself,
    so the ordinary translator still reports it.
    """
    stream = zip_reader._UnconfirmedZipCryptoStream(
        cast(Any, _FailingInner(error)), payload_complete=lambda: False
    )
    with pytest.raises(type(error)) as caught:
        _call(stream, call)
    assert caught.value is error


@pytest.mark.parametrize(
    "passwords", [RIGHT, [RIGHT, b"also-wrong"]], ids=["single", "candidates"]
)
def test_damaged_encrypted_symlink_names_both_causes(passwords: PasswordArg) -> None:
    """A symlink target that fails its check is not reported as a missing password."""
    blob = corrupt_zipcrypto_payload(
        build_zipcrypto_zip(
            RIGHT,
            b"link",
            b"target.txt",
            compression=zipfile.ZIP_STORED,
            unix_mode=0o120777,
        )
    )

    with open_archive(io.BytesIO(blob), password=passwords) as ar:
        (member,) = ar.members()
        assert member.type is MemberType.SYMLINK
        assert member.link_target is None
        (diagnostic,) = [
            d
            for d in ar.diagnostics.retained
            if d.code is DiagnosticCode.SYMLINK_TARGET_UNAVAILABLE
        ]
    assert isinstance(diagnostic.context, SymlinkTargetContext)
    assert diagnostic.context.reason == "password_or_damage"
    assert "may be corrupt" in diagnostic.message


def test_corrupt_encrypted_data_with_multiple_candidates_reports_ambiguity() -> None:
    blob = corrupt_zipcrypto_payload(build_zipcrypto_zip(RIGHT, NAME.encode(), DATA))

    with open_archive(io.BytesIO(blob), password=[RIGHT, b"also-wrong"]) as ar:
        with pytest.raises(
            EncryptionError, match=r"password\(s\) may be wrong, or .* may be corrupt"
        ):
            ar.open(NAME)


def test_structural_bad_zip_is_corruption_not_password_ambiguity() -> None:
    blob = bytearray(build_zipcrypto_zip(RIGHT, NAME.encode(), DATA))
    blob[30] ^= 0x01  # local-header name no longer matches the central directory

    with open_archive(io.BytesIO(blob), password=[RIGHT, b"also-wrong"]) as ar:
        with pytest.raises(CorruptionError, match="Error reading ZIP archive"):
            ar.open(NAME)


def test_provider_encryption_error_is_not_rewritten_after_candidate_failure() -> None:
    blob = build_zipcrypto_zip(RIGHT, NAME.encode(), DATA)
    collider = find_check_byte_collision(blob, NAME, RIGHT)
    provider_error = EncryptionError("password service unavailable")

    def provider(request: PasswordRequest) -> bytes:
        if request.attempt == 1:
            return collider
        raise provider_error

    with pytest.raises(EncryptionError, match="password service unavailable") as caught:
        _read_member(blob, provider)

    assert caught.value is provider_error


def test_confirmed_winner_is_reopened_fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    """Confirmation opens the winner once to validate, then re-opens for the caller."""
    blob = build_zipcrypto_zip(RIGHT, NAME.encode(), DATA)
    collider = find_check_byte_collision(blob, NAME, RIGHT)

    original = zip_reader.keys_after_header
    tried: list[bytes] = []

    def tracking_keys(
        password: bytes, header: bytes
    ) -> tuple[zipcrypto.ZipCryptoKeys, int]:
        # One call per decrypt stage the reader opens.
        tried.append(password)
        return original(password, header)

    monkeypatch.setattr(zip_reader, "keys_after_header", tracking_keys)
    with open_archive(io.BytesIO(blob), password=[collider, RIGHT]) as ar:
        assert ar.read(NAME) == DATA

    assert tried == [collider, RIGHT, RIGHT]


def test_provider_password_is_reused_as_known_good() -> None:
    blob = build_zipcrypto_zip(RIGHT, NAME.encode(), DATA)
    calls = 0

    def provider(request: PasswordRequest) -> bytes:
        nonlocal calls
        calls += 1
        return RIGHT

    with open_archive(io.BytesIO(blob), password=provider) as ar:
        assert ar.read(NAME) == DATA
        assert ar.read(NAME) == DATA

    assert calls == 1


def test_unrelated_oserror_propagates_and_failed_stream_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blob = build_zipcrypto_zip(RIGHT, NAME.encode(), DATA)

    class FailingStream(io.BytesIO):
        def read(self, _size: int = -1) -> bytes:
            raise OSError("source storage unavailable")

    failed_stream = FailingStream()

    with open_archive(io.BytesIO(blob), password=[b"one", b"two"]) as ar:
        # Focused ZIP backend test: the member's raw payload is the failing stream.
        monkeypatch.setattr(ar, "_raw_member_stream", lambda _info: failed_stream)
        with pytest.raises(OSError, match="source storage unavailable"):
            ar.open(NAME)

    assert failed_stream.closed


def test_source_close_failure_after_prefix_confirm_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A close failure on a candidate's payload stream must not be swallowed."""
    blob = build_zipcrypto_zip(RIGHT, NAME.encode(), DATA)

    class CloseFailingStream(io.BytesIO):
        close_attempted = False

        def read(self, size: int = -1) -> bytes:
            # Pretend confirmation consumed the prefix successfully.
            if size == 0:
                return b""
            return b"x" * (size if size > 0 else 16)

        def close(self) -> None:
            self.close_attempted = True
            super().close()
            raise OSError("source close failed")

    failed_source = CloseFailingStream()

    with open_archive(io.BytesIO(blob), password=[b"one", b"two"]) as ar:
        monkeypatch.setattr(ar, "_raw_member_stream", lambda _info: failed_source)
        with pytest.raises(OSError, match="source close failed"):
            ar.open(NAME)

    assert failed_source.close_attempted


# ---------------------------------------------------------------------------
# Task 1.2 — wrong-key confirmation fails within the bound (wide margin)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "compression",
    [
        pytest.param(zipfile.ZIP_DEFLATED, id="deflated"),
        pytest.param(zipfile.ZIP_BZIP2, id="bzip2"),
        pytest.param(zipfile.ZIP_LZMA, id="lzma"),
    ],
)
def test_wrong_key_rejected_within_tight_prefix_bound(
    compression: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even a 4 KiB confirmation bound rejects colliding wrong keys (≪ 64 KiB)."""
    monkeypatch.setattr(password_confirm, "PASSWORD_CONFIRM_PREFIX_BYTES", 4 * 1024)
    monkeypatch.setattr(zip_reader, "PASSWORD_CONFIRM_PREFIX_BYTES", 4 * 1024)
    blob = build_zipcrypto_zip(RIGHT, NAME.encode(), DATA * 64, compression=compression)
    collider = find_check_byte_collision(blob, NAME, RIGHT)
    assert _read_member(blob, [collider, RIGHT]) == DATA * 64


# ---------------------------------------------------------------------------
# Task 3.5 — large compressed member confirmation is bounded
# ---------------------------------------------------------------------------


def test_large_compressed_confirmation_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plaintext = b"bounded-confirm-payload\n" * 8000  # well over 64 KiB when repeated
    plaintext = plaintext * 8  # ~1.5 MiB+
    assert len(plaintext) > PASSWORD_CONFIRM_PREFIX_BYTES
    blob = build_zipcrypto_zip(
        RIGHT, NAME.encode(), plaintext, compression=zipfile.ZIP_DEFLATED
    )
    collider = find_check_byte_collision(blob, NAME, RIGHT)

    created_temps: list[str] = []
    real_named = tempfile.NamedTemporaryFile

    def tracking_temp(*args: Any, **kwargs: Any) -> Any:
        tmp = real_named(*args, **kwargs)
        created_temps.append(tmp.name)
        return tmp

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", tracking_temp)
    monkeypatch.setattr(tempfile, "SpooledTemporaryFile", tracking_temp)

    bytes_read = {"n": 0}
    original_exact = zip_reader.read_exact

    def counting_exact(stream: Any, n: int) -> bytes:
        data = original_exact(stream, n)
        bytes_read["n"] = max(bytes_read["n"], len(data))
        return data

    monkeypatch.setattr(zip_reader, "read_exact", counting_exact)

    assert _read_member(blob, [collider, RIGHT]) == plaintext
    assert created_temps == []
    assert bytes_read["n"] <= PASSWORD_CONFIRM_PREFIX_BYTES


# ---------------------------------------------------------------------------
# Task 3.6 — STORED shared CRC pass
# ---------------------------------------------------------------------------


def test_stored_uses_shared_crc_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    plaintext = os.urandom(128 * 1024)
    blob = build_zipcrypto_zip(
        RIGHT, NAME.encode(), plaintext, compression=zipfile.ZIP_STORED
    )
    collider = find_check_byte_collision(blob, NAME, RIGHT)

    crc_calls = {"n": 0}
    original = zipcrypto.parallel_plaintext_crc32

    def counting_crc(*args: Any, **kwargs: Any) -> Any:
        crc_calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(zipcrypto, "parallel_plaintext_crc32", counting_crc)
    monkeypatch.setattr(zip_reader, "parallel_plaintext_crc32", counting_crc)

    assert _read_member(blob, [collider, RIGHT]) == plaintext
    assert crc_calls["n"] == 1


def test_stored_crc_match_ties_resolve_by_candidate_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plaintext = b"tie-break stored"
    blob = build_zipcrypto_zip(
        RIGHT, NAME.encode(), plaintext, compression=zipfile.ZIP_STORED
    )
    colliders = find_check_byte_collisions(blob, NAME, RIGHT, count=2)
    expected = zlib.crc32(plaintext) & 0xFFFFFFFF

    def fake_crc(
        passwords: list[bytes], header: bytes, body: Any, **kwargs: Any
    ) -> list[tuple[bytes, int]]:
        while body.read(65536):
            pass
        return [(p, expected) for p in passwords]

    monkeypatch.setattr(zipcrypto, "parallel_plaintext_crc32", fake_crc)
    monkeypatch.setattr(zip_reader, "parallel_plaintext_crc32", fake_crc)

    with open_archive(
        io.BytesIO(blob), password=[colliders[0], colliders[1], RIGHT]
    ) as ar:
        stream = ar.open(NAME)
        # Earliest CRC "match" wins confirmation and is recorded known-good.
        assert cast(Any, ar)._passwords._known_good[0] == colliders[0]
        with stream, pytest.raises(CorruptionError):
            stream.read()


def test_stored_caller_stream_is_crc_checked() -> None:
    plaintext = b"stored caller crc\n" * 100
    blob = corrupt_zipcrypto_payload(
        build_zipcrypto_zip(
            RIGHT, NAME.encode(), plaintext, compression=zipfile.ZIP_STORED
        )
    )
    with open_archive(io.BytesIO(blob), password=RIGHT) as ar:
        with pytest.raises(EncryptionError, match=UNCONFIRMED):
            ar.read(NAME)


# ---------------------------------------------------------------------------
# Task 3.7 — corruption beyond confirmed prefix surfaces on caller's read
# ---------------------------------------------------------------------------


def test_corruption_beyond_prefix_fails_caller_read_as_corruption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prefix confirmation accepts; trailing corruption fails the caller's read.

    Damage this late is the codec's to report, as for an unencrypted member: here
    the deflate stream ends short of the declared size.
    """
    import struct

    plaintext = b"prefix-ok-then-corrupt\n" * 8000
    assert len(plaintext) > 64 * 1024
    blob = bytearray(
        build_zipcrypto_zip(
            RIGHT, NAME.encode(), plaintext, compression=zipfile.ZIP_DEFLATED
        )
    )
    name_len, extra_len = struct.unpack_from("<HH", blob, 26)
    comp_size = struct.unpack_from("<I", blob, 18)[0]
    payload_start = 30 + name_len + extra_len
    # Flip a late ciphertext byte (keep the ZipCrypto header intact).
    late = payload_start + comp_size - 8
    assert late > payload_start + 12
    blob[late] ^= 0xFF

    # Tight bound so confirmation only sees the good prefix.
    monkeypatch.setattr(password_confirm, "PASSWORD_CONFIRM_PREFIX_BYTES", 4096)
    monkeypatch.setattr(zip_reader, "PASSWORD_CONFIRM_PREFIX_BYTES", 4096)

    with open_archive(io.BytesIO(bytes(blob)), password=[RIGHT, b"also-wrong"]) as ar:
        stream = ar.open(NAME)
        with stream, pytest.raises((CorruptionError, TruncatedError)):
            stream.read()


# ZipCrypto members decode through the shared codec layer.


@requires_zstd()
def test_zipcrypto_member_decodes_a_method_stdlib_cannot() -> None:
    """ZipCrypto over Zstd (method 93): stdlib ``zipfile`` never decoded this."""
    payload = DATA * 50
    compressed = zstd_backend().compress(payload)
    blob = build_zipcrypto_zip(
        RIGHT, NAME.encode(), payload, compression=93, compressed=compressed
    )
    assert _read_member(blob, RIGHT) == payload
    assert _read_member(blob, [b"wrong", RIGHT]) == payload


@requires_zstd()
def test_zipcrypto_zstd_confirm_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Several candidates on a large Zstd member confirm on a prefix, not the CRC."""
    payload = os.urandom(PASSWORD_CONFIRM_PREFIX_BYTES * 4)
    compressed = zstd_backend().compress(payload)
    blob = build_zipcrypto_zip(
        RIGHT, NAME.encode(), payload, compression=93, compressed=compressed
    )
    plans: list[password_confirm.PasswordConfirmPlan] = []
    real_plan = zip_reader.plan_password_confirm

    def spy(*args: Any, **kwargs: Any) -> password_confirm.PasswordConfirmPlan:
        plans.append(real_plan(*args, **kwargs))
        return plans[-1]

    monkeypatch.setattr(zip_reader, "plan_password_confirm", spy)
    assert _read_member(blob, [b"wrong", RIGHT]) == payload
    assert [plan.read_bytes for plan in plans] == [PASSWORD_CONFIRM_PREFIX_BYTES]


@pytest.mark.parametrize("compression", COMPRESSION_METHODS)
@pytest.mark.parametrize("passwords", [RIGHT, [b"wrong", RIGHT]], ids=["one", "two"])
def test_zipcrypto_member_seeks(compression: int, passwords: PasswordArg) -> None:
    """Back and forth: the stage restarts from its header for a backward seek."""
    payload = bytes(range(256)) * 300
    blob = build_zipcrypto_zip(RIGHT, NAME.encode(), payload, compression=compression)

    with open_archive(
        io.BytesIO(blob), password=passwords, seekable_members=True
    ) as ar:
        with ar.open(NAME) as stream:
            stream.seek(40_000)
            assert stream.read(100) == payload[40_000:40_100]
            stream.seek(123)
            assert stream.read(10) == payload[123:133]
            stream.seek(-5, io.SEEK_END)
            assert stream.read() == payload[-5:]


def _seek_outcomes(stream: BinaryIO) -> list[object]:
    """Where out-of-range seeks land (or what they raise), and what a read there returns."""
    out: list[object] = []
    for offset, whence in (
        (-10, io.SEEK_END),
        (-100, io.SEEK_CUR),
        (1000, io.SEEK_SET),
    ):
        try:
            out.append(stream.seek(offset, whence))
        except ArchiveyError as exc:
            out.append(type(exc))
        out += [stream.tell(), stream.read(2)]
    return out


def test_zipcrypto_stored_out_of_range_seeks_match_an_unencrypted_member() -> None:
    """A STORED member's seek reaches the decrypt stage directly, and lands where the
    same member unencrypted does: a relative underflow clamps to 0 and a past-end
    seek keeps its position."""
    payload = b"hello"
    encrypted = build_zipcrypto_zip(
        RIGHT, NAME.encode(), payload, compression=zipfile.ZIP_STORED
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(NAME, payload)
    results = []
    for blob, password in ((encrypted, RIGHT), (buf.getvalue(), None)):
        with open_archive(
            io.BytesIO(blob), password=password, seekable_members=True
        ) as ar:
            with ar.open(NAME) as stream:
                results.append(_seek_outcomes(stream))
    assert results[0] == results[1] == [0, 0, b"he", 0, 0, b"he", 1000, 1000, b""]


def test_zipcrypto_stage_matches_stdlib() -> None:
    """The decrypt stage produces what stdlib's decrypter does, byte for byte."""
    blob = build_zipcrypto_zip(
        RIGHT, NAME.encode(), DATA * 20, compression=zipfile.ZIP_DEFLATED
    )
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        expected = zf.read(NAME, pwd=RIGHT)
    assert _read_member(blob, RIGHT) == expected
