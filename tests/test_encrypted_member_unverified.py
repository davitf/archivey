"""``ENCRYPTED_MEMBER_UNVERIFIED``: a partial read under a password no digest checked.

A ZipCrypto password that passes the one-byte header check can be wrong, and then the
member decrypts to readable garbage that only the CRC at EOF notices. A caller who stops
reading before EOF never hears about it, unless this diagnostic fires. It must fire for
exactly that shape and stay silent when the password was checked against a digest.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from archivey import ArchiveyConfig, DiagnosticPolicy, open_archive
from archivey.diagnostics import (
    ARCHIVE_INTEGRITY_CODES,
    DiagnosticCode,
    EncryptedVerificationContext,
)
from archivey.exceptions import DiagnosticRaisedError, EncryptionError
from archivey.types import ArchiveMember
from tests.conftest import requires, requires_binary

_FIXTURES = Path(__file__).parent / "fixtures"
_COLLISION = _FIXTURES / "zipcrypto" / "check_byte_collision.zip"
_AE1 = _FIXTURES / "external" / "aes_ae1_pyzipper036.zip"
# Wrong passwords that pass `stored.txt`'s check byte (tests/fixtures/zipcrypto/README.md).
_STORED_COLLISION = "wrong896"
_CODE = DiagnosticCode.ENCRYPTED_MEMBER_UNVERIFIED


def _plaintext(name: str) -> bytes:
    with open_archive(_COLLISION, password="secret") as reader:
        return reader.read(_member(reader, name))


def _member(reader: object, name: str) -> ArchiveMember:
    return next(m for m in reader.members() if m.name == name)  # type: ignore[attr-defined]


def _unverified(reader: object) -> list[EncryptedVerificationContext]:
    diagnostics = reader.diagnostics.retained  # type: ignore[attr-defined]
    contexts = [d.context for d in diagnostics if d.code is _CODE]
    assert all(isinstance(c, EncryptedVerificationContext) for c in contexts)
    return contexts


def test_colliding_zipcrypto_password_partial_read_is_reported() -> None:
    with open_archive(_COLLISION, password=_STORED_COLLISION) as reader:
        member = _member(reader, "stored.txt")
        with reader.open(member) as stream:
            # The colliding password reaches the read (it is not refused at the check
            # byte) and returns bytes that are not the member's.
            head = stream.read(16)
        assert len(head) == 16 and head != _plaintext("stored.txt")[:16]
        (context,) = _unverified(reader)
        assert context.check == "weak_open_check"
        assert context.reason == "partial_read"
        assert context.member_name == "stored.txt"
        assert context.to_dict()["kind"] == "encrypted_verification"

        # Proof it was the wrong key and not a quirk of the read: to EOF, the CRC fails.
        with pytest.raises(EncryptionError):
            reader.read(member)


def test_correct_single_zipcrypto_password_partial_read_is_reported_too() -> None:
    # One password and a one-byte check: nothing tells a right key from a colliding one.
    with open_archive(_COLLISION, password="secret") as reader:
        with reader.open(_member(reader, "stored.txt")) as stream:
            assert stream.read(5) == _plaintext("stored.txt")[:5]
        assert len(_unverified(reader)) == 1


def test_zipcrypto_read_to_eof_is_not_reported() -> None:
    with open_archive(_COLLISION, password="secret") as reader:
        for name in ("stored.txt", "deflated.txt"):
            with reader.open(_member(reader, name)) as stream:
                while stream.read(1000):
                    pass
        assert _unverified(reader) == []


def test_open_and_close_without_reading_is_not_reported() -> None:
    with open_archive(_COLLISION, password="secret") as reader:
        with reader.open(_member(reader, "stored.txt")):
            pass
        assert _unverified(reader) == []


def test_crc_confirmed_candidate_partial_read_is_not_reported() -> None:
    # Two candidates: the STORED shared pass confirms "secret" against the CRC, so the
    # partial read that follows restates nothing the caller does not know.
    with open_archive(_COLLISION, password=[_STORED_COLLISION, "secret"]) as reader:
        with reader.open(_member(reader, "stored.txt")) as stream:
            assert stream.read(5) == _plaintext("stored.txt")[:5]
        assert _unverified(reader) == []


def test_compressed_member_inside_the_prefix_confirms_on_its_crc() -> None:
    # deflated.txt is smaller than the confirm prefix: its CRC decides, so the winner
    # is CONFIRMED and a partial read is not reported.
    with open_archive(_COLLISION, password=["wrong173", "secret"]) as reader:
        member = _member(reader, "deflated.txt")
        with reader.open(member) as stream:
            assert stream.read(3) == _plaintext("deflated.txt")[:3]
        assert _unverified(reader) == []


def test_strict_collects_and_pedantic_raises() -> None:
    assert _CODE not in ARCHIVE_INTEGRITY_CODES
    with open_archive(
        _COLLISION,
        password="secret",
        config=ArchiveyConfig(diagnostic_policy=DiagnosticPolicy.strict()),
    ) as reader:
        with reader.open(_member(reader, "stored.txt")) as stream:
            stream.read(1)
        assert len(_unverified(reader)) == 1

    with open_archive(
        _COLLISION,
        password="secret",
        config=ArchiveyConfig(diagnostic_policy=DiagnosticPolicy.pedantic()),
    ) as reader:
        stream = reader.open(_member(reader, "stored.txt"))
        stream.read(1)
        with pytest.raises(DiagnosticRaisedError):
            stream.close()


def test_extraction_never_reports(tmp_path: Path) -> None:
    with open_archive(_COLLISION, password="secret") as reader:
        reader.extract_all(tmp_path)
        assert _unverified(reader) == []


def test_unencrypted_partial_read_is_not_reported(tmp_path: Path) -> None:
    import zipfile

    plain = tmp_path / "plain.zip"
    with zipfile.ZipFile(plain, "w") as zf:
        zf.writestr("a.txt", b"x" * 10000)
    with open_archive(plain) as reader:
        with reader.open(_member(reader, "a.txt")) as stream:
            stream.read(1)
        assert _unverified(reader) == []


@requires("cryptography")
def test_winzip_aes_partial_read_is_reported() -> None:
    # pw_verify is two bytes (2⁻¹⁶); the HMAC at EOF is the check a partial read skips.
    with open_archive(_AE1, password="secret") as reader:
        member = next(m for m in reader.members() if m.is_file)
        with reader.open(member) as stream:
            stream.read(1)
        (context,) = _unverified(reader)
        assert context.check == "weak_open_check"


@requires("cryptography")
def test_winzip_aes_full_read_is_not_reported() -> None:
    with open_archive(_AE1, password="secret") as reader:
        member = next(m for m in reader.members() if m.is_file)
        reader.read(member)
        assert _unverified(reader) == []


@requires_binary("zip")
def test_large_compressed_member_ambiguous_candidates_budget_exhausted(
    tmp_path: Path,
) -> None:
    # A DEFLATE member past the confirm prefix: the survivor is INCONCLUSIVE, so a
    # partial read is reported with the budget as the reason.
    src = tmp_path / "big.txt"
    words = [b"alpha", b"bravo", b"charlie", b"delta"]
    src.write_bytes(b" ".join(words[(i * 7) % 4] for i in range(60000)))
    archive = tmp_path / "big.zip"
    result = subprocess.run(
        ["zip", "-q", "-P", "secret", "-9", str(archive), src.name],
        cwd=tmp_path,
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        pytest.skip("zip CLI could not build the fixture")
    with open_archive(archive, password=["nope", "secret"]) as reader:
        with reader.open(_member(reader, "big.txt")) as stream:
            assert stream.read(5) == src.read_bytes()[:5]
        (context,) = _unverified(reader)
        assert context.check == "confirm_budget_exhausted"
        # A survivor of an ambiguous set without a CRC match stays out of known-good.
        assert reader._passwords._known_good == []  # noqa: SLF001
