"""A damaged link target costs that link its target, not the whole listing.

ZIP and 7z store a symlink's target as the member's data, so listing has to read and
verify it. (RAR3/4 stores it as data too, but reads it with no check, so a damaged
RAR3/4 target is returned as stored: there is nothing here to fail.) When that read fails its integrity check, only the link is wrong: the
listing keeps every member, the link has no ``link_target``, and
``SYMLINK_TARGET_UNAVAILABLE`` (``reason="target_data_damaged"``) says why. The fault
itself is raised where the caller touches the link: opening it, or extracting it.
"""

from __future__ import annotations

import io
import stat
import zipfile
from pathlib import Path

import pytest

from archivey import ExtractionStatus, open_archive
from archivey.config import ArchiveyConfig
from archivey.diagnostics import DiagnosticCode, DiagnosticPolicy, SymlinkTargetContext
from archivey.exceptions import CorruptionError, DiagnosticRaisedError
from archivey.reader import ArchiveReader
from archivey.types import MemberType, OnError
from tests.conftest import requires, requires_binary
from tests.test_link_target_cap import _sevenzip_with_link
from tests.zip_aes_fixture import build_aes_zip

_TARGET = b"zz-link-target-zz"
_PASSWORD = b"secret"


def _flip_byte(blob: bytes, needle: bytes) -> bytes:
    """``blob`` with the first byte of the only ``needle`` inverted."""
    assert blob.count(needle) == 1, f"expected one copy of {needle!r}"
    at = blob.index(needle)
    return blob[:at] + bytes([blob[at] ^ 0xFF]) + blob[at + 1 :]


def _damaged_zip_symlink() -> bytes:
    """A ZIP holding ``target.txt`` and a stored symlink whose data fails its CRC."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("target.txt", b"payload")
        info = zipfile.ZipInfo("link")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, _TARGET, compress_type=zipfile.ZIP_STORED)
    return _flip_byte(buf.getvalue(), _TARGET)


def _damaged_aes_symlink() -> bytes:
    """A WinZip AES ZIP whose one member is a symlink with a failing HMAC."""
    return build_aes_zip(
        [(b"link", _TARGET)],
        password=_PASSWORD,
        method=0,
        tamper_hmac=True,
        unix_mode=0o120777,
    )


def _damaged_7z_symlink(tmp_path: Path) -> bytes:
    """A stored 7z holding ``link``, a symlink whose data fails its CRC.

    Built as a regular file re-flagged as a link (`_sevenzip_with_link`) rather than
    with ``7z -snl``, which stores a Windows reparse buffer on Windows and exits 1 on
    macOS. 7z stores names as UTF-16, so the ASCII target occurs once, in the data.
    """
    return _flip_byte(_sevenzip_with_link(tmp_path, _TARGET, store=True), _TARGET)


def _link_diagnostics(ar: ArchiveReader) -> list[SymlinkTargetContext]:
    contexts = []
    for diagnostic in ar.diagnostics.retained:
        if diagnostic.code is DiagnosticCode.SYMLINK_TARGET_UNAVAILABLE:
            assert isinstance(diagnostic.context, SymlinkTargetContext)
            contexts.append(diagnostic.context)
    return contexts


def _assert_listed_targetless(ar: ArchiveReader, name: str = "link") -> None:
    (link,) = [m for m in ar.members() if m.name == name]
    assert link.type is MemberType.SYMLINK
    assert link.link_target is None
    (context,) = _link_diagnostics(ar)
    assert context.reason == "target_data_damaged"
    assert context.member_name == name


def test_damaged_zip_link_target_keeps_the_listing() -> None:
    with open_archive(io.BytesIO(_damaged_zip_symlink())) as ar:
        _assert_listed_targetless(ar)
        assert ar.read(ar.get("target.txt")) == b"payload"
        with pytest.raises(CorruptionError):
            ar.open(ar.get("link"))


@pytest.mark.parametrize(
    "passwords", [_PASSWORD, [b"wrong", _PASSWORD]], ids=["single", "candidates"]
)
@requires("cryptography")
def test_damaged_aes_link_target_keeps_the_listing(
    passwords: bytes | list[bytes],
) -> None:
    """Both password paths list the link: its HMAC failure is damage (S28-K4)."""
    with open_archive(io.BytesIO(_damaged_aes_symlink()), password=passwords) as ar:
        _assert_listed_targetless(ar)
        with pytest.raises(CorruptionError):
            ar.open(ar.get("link"))


@requires_binary("7z")
def test_damaged_7z_link_target_keeps_the_listing(tmp_path: Path) -> None:
    with open_archive(io.BytesIO(_damaged_7z_symlink(tmp_path))) as ar:
        _assert_listed_targetless(ar)
        with pytest.raises(CorruptionError):
            ar.open(ar.get("link"))


def test_damaged_link_target_in_a_streaming_pass() -> None:
    with open_archive(io.BytesIO(_damaged_zip_symlink()), streaming=True) as ar:
        names = [member.name for member, _ in ar.stream_members()]
        assert names == ["target.txt", "link"]
        assert [c.reason for c in _link_diagnostics(ar)] == ["target_data_damaged"]


def test_damaged_link_target_fails_only_that_link_at_extraction(
    tmp_path: Path,
) -> None:
    with open_archive(io.BytesIO(_damaged_zip_symlink())) as ar:
        report = ar.extract_all(tmp_path / "out", on_error=OnError.CONTINUE)
    by_name = {result.member.name: result for result in report.results}
    assert by_name["target.txt"].status is ExtractionStatus.EXTRACTED
    assert by_name["link"].status is ExtractionStatus.FAILED
    assert isinstance(by_name["link"].error, CorruptionError)
    assert (tmp_path / "out" / "target.txt").read_bytes() == b"payload"
    assert not (tmp_path / "out" / "link").is_symlink()


def test_damaged_link_target_under_strict_policy_refuses_the_listing() -> None:
    config = ArchiveyConfig(diagnostic_policy=DiagnosticPolicy.strict())
    with open_archive(io.BytesIO(_damaged_zip_symlink()), config=config) as ar:
        with pytest.raises(DiagnosticRaisedError):
            ar.members()
