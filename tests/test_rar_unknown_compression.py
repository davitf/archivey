"""A RAR member compressed with a version ``unrar`` does not know is unsupported.

``unrar`` 7.00 decodes RAR5 compression-info versions 0 and 1 and RAR 1.5-4
``UNP_VER`` 13 to 29. For anything else it prints "Unknown method" and "You may need
a newer version of RAR" and writes nothing, which archivey used to report as a
truncation ("ended after 0 of N bytes"). The data is not short; it is newer than the
decoder. Same ruling as lzip version 0.
"""

from __future__ import annotations

import io
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from archivey import ArchiveyConfig, open_archive
from archivey.config import RarDecompressor
from archivey.exceptions import TruncatedError, UnsupportedFeatureError
from archivey.internal.backends.rar_parser import load_vint, parse_rar_archive
from tests.atheris_fuzz.crc_fixup import fixup_rar_header_crcs
from tests.conftest import binary_refusal

_FIXTURES = Path(__file__).parent / "fixtures" / "rar"


def _set_rar5_version(data: bytes, member_index: int, version: int) -> bytes:
    """Set the compression-info version bits of the ``member_index``-th FILE header."""
    buf = bytearray(data)
    pos = 8
    seen = 0
    while pos < len(buf):
        size, body = load_vint(bytes(buf), pos + 4)
        end = body + size
        block_type, p = load_vint(bytes(buf), body)
        flags, p = load_vint(bytes(buf), p)
        if flags & 0x0001:
            _, p = load_vint(bytes(buf), p)
        data_size = 0
        if flags & 0x0002:
            data_size, p = load_vint(bytes(buf), p)
        if block_type == 2:
            if seen == member_index:
                file_flags, p = load_vint(bytes(buf), p)
                _, p = load_vint(bytes(buf), p)  # unpacked size
                _, p = load_vint(bytes(buf), p)  # attributes
                p += 4 if file_flags & 0x0002 else 0  # mtime
                p += 4 if file_flags & 0x0004 else 0  # CRC32
                buf[p] = (buf[p] & ~0x3F) | version
                return fixup_rar_header_crcs(bytes(buf), broken=False)
            seen += 1
        pos = end + data_size
    raise AssertionError(f"no FILE header {member_index}")


def _set_rar4_unp_ver(data: bytes, version: int) -> bytes:
    """Set ``UNP_VER`` on every FILE header of a RAR 1.5-4 archive."""
    buf = bytearray(data)
    pos = 7
    while pos + 7 <= len(buf):
        block_type = buf[pos + 2]
        flags = int.from_bytes(buf[pos + 3 : pos + 5], "little")
        size = int.from_bytes(buf[pos + 5 : pos + 7], "little")
        add = 0
        if block_type == 0x74:
            add = int.from_bytes(buf[pos + 7 : pos + 11], "little")
            buf[pos + 24] = version
        elif flags & 0x8000:
            add = int.from_bytes(buf[pos + 7 : pos + 11], "little")
        if block_type == 0x7B:
            break
        pos += size + add
    return fixup_rar_header_crcs(bytes(buf), broken=False)


def _write(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def _config(decompressor: str) -> ArchiveyConfig:
    return ArchiveyConfig(rar_decompressor=RarDecompressor(decompressor))


@pytest.mark.parametrize("decompressor", ["unrar", "unar"])
def test_an_unknown_rar5_version_is_unsupported_not_truncated(
    tmp_path: Path, decompressor: str
) -> None:
    """Refused before any process runs, so it needs neither program installed."""
    data = (_FIXTURES / "hostile_argv__.rar").read_bytes()
    path = _write(tmp_path, "v2.rar", _set_rar5_version(data, 1, 2))
    with open_archive(path, config=_config(decompressor)) as archive:
        member = archive.members()[1]
        with pytest.raises(UnsupportedFeatureError, match="compression version 2"):
            with archive.open(member) as stream:
                stream.read()


@pytest.mark.parametrize("decompressor", ["unrar", "unar"])
def test_the_other_members_still_read(tmp_path: Path, decompressor: str) -> None:
    refusal = binary_refusal(decompressor)
    if refusal is not None:
        pytest.skip(refusal)
    data = (_FIXTURES / "hostile_argv__.rar").read_bytes()
    path = _write(tmp_path, "v2.rar", _set_rar5_version(data, 1, 2))
    outcomes: dict[str, object] = {}
    with open_archive(path, config=_config(decompressor)) as archive:
        for member, stream in archive.stream_members():
            if stream is None:
                continue
            try:
                outcomes[member.name] = len(stream.read())
            except UnsupportedFeatureError:
                outcomes[member.name] = UnsupportedFeatureError
    assert outcomes == {
        "canary.txt": 1408,
        "-inul": UnsupportedFeatureError,
        "@atfile": 1216,
    }


def test_rar5_version_1_is_known() -> None:
    """RAR 7.0's algorithm version; ``unrar`` 7.00 decodes it."""
    data = (_FIXTURES / "hostile_argv__.rar").read_bytes()
    archive = parse_rar_archive(io.BytesIO(_set_rar5_version(data, 1, 1)))
    assert archive.members[1].unknown_compression_version() is None
    archive = parse_rar_archive(io.BytesIO(_set_rar5_version(data, 1, 2)))
    assert archive.members[1].unknown_compression_version() is not None


def test_a_stored_member_reads_whatever_version_it_declares(tmp_path: Path) -> None:
    """``unrar`` copies stored data without looking at the version, and so does
    archivey's direct read."""
    data = (_FIXTURES / "stored_m0.rar").read_bytes()
    path = _write(tmp_path, "stored_v7.rar", _set_rar5_version(data, 0, 7))
    with open_archive(path) as archive:
        (member,) = archive.members()
        with archive.open(member) as stream:
            assert len(stream.read()) == member.size


@pytest.mark.parametrize("version", [10, 30, 36, 99])
def test_an_unknown_rar4_version_is_unsupported(tmp_path: Path, version: int) -> None:
    """``unrar`` 7.00 says "Unknown method" below 13 and above 29, measured on this
    fixture; 36 included."""
    data = (_FIXTURES / "hostile_argv__rar4.rar").read_bytes()
    path = _write(tmp_path, f"v{version}.rar", _set_rar4_unp_ver(data, version))
    with open_archive(path) as archive:
        member = archive.members()[0]
        with pytest.raises(UnsupportedFeatureError, match=f"version {version}"):
            with archive.open(member) as stream:
                stream.read()


def test_the_solid_unrar_pass_refuses_the_member_and_keeps_the_ones_before(
    tmp_path: Path,
) -> None:
    """``unrar p`` writes nothing for the member, so it takes no room in the pipe.
    Members before it still read; one after it in the same solid stream cannot be
    decoded (``unrar`` stops there), which is a separate failure."""
    refusal = binary_refusal("unrar")
    if refusal is not None:
        pytest.skip(refusal)
    data = (_FIXTURES / "basic_solid__.rar").read_bytes()
    path = _write(tmp_path, "solid_v2.rar", _set_rar5_version(data, 2, 2))
    outcomes: dict[str, object] = {}
    with open_archive(path, config=_config("unrar")) as archive:
        for member, stream in archive.stream_members():
            if stream is None:
                continue
            try:
                outcomes[member.name] = len(stream.read())
            except UnsupportedFeatureError:
                outcomes[member.name] = UnsupportedFeatureError
            except TruncatedError:
                outcomes[member.name] = TruncatedError
    assert outcomes["file1.txt"] == 13
    assert outcomes["subdir/file2.txt"] is UnsupportedFeatureError


@pytest.mark.skipif(shutil.which("unrar") is None, reason="needs the unrar CLI")
@pytest.mark.parametrize(
    ("fixture", "mutate"),
    [
        ("hostile_argv__.rar", lambda d: _set_rar5_version(d, 1, 2)),
        ("hostile_argv__rar4.rar", lambda d: _set_rar4_unp_ver(d, 36)),
    ],
    ids=["rar5-v2", "rar4-v36"],
)
def test_unrar_reports_an_unknown_method(
    tmp_path: Path, fixture: str, mutate: Callable[[bytes], bytes]
) -> None:
    """The oracle: ``unrar`` names the same members as unknown, not as damaged."""
    path = _write(tmp_path, "oracle.rar", mutate((_FIXTURES / fixture).read_bytes()))
    result = subprocess.run(
        ["unrar", "t", "-p-", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    output = result.stdout + result.stderr
    assert "Unknown method" in output, output
