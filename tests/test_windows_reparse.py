"""Windows reparse points in ZIP and 7z: symlinks, junctions, and what is lost.

The archives under `tests/fixtures/external/junction/` were built by 7-Zip on a Windows
runner, because a junction cannot be created anywhere else; the temporary CI job that
made them is gone, and `tests/fixtures/external/README.md` carries its exact recipe
along with the full finding. What those archives contain is the point of these tests,
so the assertions here are deliberately about the *measured* shapes rather than the
ones the format would allow.
"""

from __future__ import annotations

import struct
import zipfile
from pathlib import Path

import pytest

from archivey import open_archive
from archivey.internal.windows_reparse import (
    FILE_ATTRIBUTE_REPARSE_POINT,
    IO_REPARSE_TAG_MOUNT_POINT,
    IO_REPARSE_TAG_SYMLINK,
    parse_reparse_data,
)
from archivey.types import MemberType

_JUNCTION_DIR = Path(__file__).parent / "fixtures" / "external" / "junction"

# FILE_ATTRIBUTE_DIRECTORY, for the directory-shaped entries built below.
_FILE_ATTRIBUTE_DIRECTORY = 0x10


def _reparse_buffer(tag: int, substitute: str, print_name: str) -> bytes:
    """Assemble a REPARSE_DATA_BUFFER the way Windows lays one out."""
    subst = substitute.encode("utf-16-le") + b"\0\0"
    printed = print_name.encode("utf-16-le") + b"\0\0"
    # Offsets are into PathBuffer; lengths exclude the terminating NUL.
    names = struct.pack("<HHHH", 0, len(subst) - 2, len(subst), len(printed) - 2)
    flags = struct.pack("<I", 0) if tag == IO_REPARSE_TAG_SYMLINK else b""
    body = names + flags + subst + printed
    return struct.pack("<IHH", tag, len(body), 0) + body


# --------------------------------------------------------------------------------
# The parser
# --------------------------------------------------------------------------------


def test_junction_buffer_yields_the_target_and_the_flag() -> None:
    parsed = parse_reparse_data(
        _reparse_buffer(
            IO_REPARSE_TAG_MOUNT_POINT, "\\??\\C:\\tree\\target", "C:\\tree\\target"
        )
    )
    assert parsed is not None
    assert parsed.is_junction
    assert parsed.target == "C:/tree/target"


def test_symlink_buffer_is_not_a_junction() -> None:
    parsed = parse_reparse_data(
        _reparse_buffer(
            IO_REPARSE_TAG_SYMLINK, "target\\payload.txt", "target\\payload.txt"
        )
    )
    assert parsed is not None
    assert not parsed.is_junction
    assert parsed.target == "target/payload.txt"


def test_junction_falls_back_to_the_substitute_name_without_its_nt_prefix() -> None:
    """A junction written with no PrintName still resolves, minus the `\\??\\`."""
    parsed = parse_reparse_data(
        _reparse_buffer(IO_REPARSE_TAG_MOUNT_POINT, "\\??\\C:\\tree\\target", "")
    )
    assert parsed is not None
    assert parsed.target == "C:/tree/target"


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(b"", id="empty"),
        pytest.param(b"hello\r\n", id="ordinary-file-content"),
        pytest.param(
            struct.pack("<IHH", IO_REPARSE_TAG_MOUNT_POINT, 0, 0), id="header-only"
        ),
        # A real tag, but one whose payload is not a link target at all.
        pytest.param(
            struct.pack("<IHH", 0x80000013, 4, 0) + b"\0\0\0\0", id="dedup-tag"
        ),
    ],
)
def test_non_link_buffers_are_declined(data: bytes) -> None:
    """Anything that is not a link buffer parses to None rather than a made-up path."""
    assert parse_reparse_data(data) is None


def test_truncated_payload_does_not_raise() -> None:
    whole = _reparse_buffer(IO_REPARSE_TAG_SYMLINK, "target", "target")
    # Every prefix is either declined or parsed into a shorter target; none may raise.
    for cut in range(len(whole)):
        parse_reparse_data(whole[:cut])


# --------------------------------------------------------------------------------
# The real archives
# --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture",
    [
        pytest.param("junction_7zip_snl.zip", id="zip"),
        pytest.param("junction_7zip_snl.7z", id="7z"),
    ],
)
def test_7zip_file_symlink_reports_its_real_target(fixture: str) -> None:
    """The buffer is decoded, not handed back as if it were a path.

    Before this was parsed, 7z reported those 92 bytes UTF-8-decoded as the link
    target and ZIP did not notice the member was a link at all.
    """
    with open_archive(_JUNCTION_DIR / fixture) as archive:
        member = next(m for m in archive.members() if m.name == "tree/symlink_file")
        assert member.type is MemberType.SYMLINK
        assert member.link_target == "target/payload.txt"
        assert not member.is_junction


@pytest.mark.parametrize(
    "fixture",
    [
        pytest.param("junction_7zip_snl.zip", id="zip"),
        pytest.param("junction_7zip_snl.7z", id="7z"),
    ],
)
@pytest.mark.parametrize("name", ["tree/junction_dir", "tree/symlink_dir"])
def test_7zip_directory_reparse_point_is_a_link_with_no_target(
    fixture: str, name: str
) -> None:
    """7-Zip stores no data for a directory reparse point, so the target is gone.

    A junction is always a directory reparse point, which is why `is_junction` is
    False here for the junction as well as the symlink: the tag that would have
    separated them is inside the data that was never written. Both backends agree,
    and the member keeps the trailing slash off its name.
    """
    with open_archive(_JUNCTION_DIR / fixture) as archive:
        member = next(m for m in archive.members() if m.name == name)
        assert member.type is MemberType.SYMLINK
        assert member.link_target is None
        assert not member.is_junction


def test_the_junction_and_the_symlink_are_indistinguishable_on_disk() -> None:
    """The finding itself, asserted so it cannot regress unnoticed.

    If a later 7-Zip starts storing the reparse data for directory reparse points,
    this fails and the junction flag becomes reachable from a real archive.
    """
    with zipfile.ZipFile(_JUNCTION_DIR / "junction_7zip_snl.zip") as zf:
        junction = zf.getinfo("tree/junction_dir/")
        symlink = zf.getinfo("tree/symlink_dir/")
    assert junction.external_attr == symlink.external_attr
    assert junction.external_attr & FILE_ATTRIBUTE_REPARSE_POINT
    assert junction.file_size == symlink.file_size == 0


# --------------------------------------------------------------------------------
# A writer that does keep the data
# --------------------------------------------------------------------------------


def _zip_with_reparse_member(
    path: Path, *, name: str, attributes: int, data: bytes
) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        info = zipfile.ZipInfo(name)
        info.create_system = 0  # FAT, as every Windows writer of these uses
        info.external_attr = attributes
        zf.writestr(info, data)


def test_a_stored_junction_buffer_sets_the_flag(tmp_path: Path) -> None:
    """No Windows tool writes this, but the format allows it and the reader handles it.

    This is the one path on which the `archive-data-model` junction promise can be
    kept, so it is worth having even though the probe found nothing that produces it.
    """
    archive = tmp_path / "stored_junction.zip"
    _zip_with_reparse_member(
        archive,
        name="tree/junction_dir/",
        attributes=_FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT,
        data=_reparse_buffer(
            IO_REPARSE_TAG_MOUNT_POINT, "\\??\\C:\\tree\\target", "C:\\tree\\target"
        ),
    )
    with open_archive(archive) as opened:
        (member,) = opened.members()
        assert member.name == "tree/junction_dir"
        assert member.type is MemberType.SYMLINK
        assert member.is_junction
        assert member.extra["is_junction"] is True
        assert member.link_target == "C:/tree/target"


def test_a_reparse_member_whose_data_is_not_a_link_keeps_no_target(
    tmp_path: Path,
) -> None:
    """The attribute bit alone is not enough to invent a target from."""
    archive = tmp_path / "odd_reparse.zip"
    _zip_with_reparse_member(
        archive,
        name="tree/weird",
        attributes=0x20 | FILE_ATTRIBUTE_REPARSE_POINT,
        data=b"not a reparse buffer",
    )
    with open_archive(archive) as opened:
        (member,) = opened.members()
        assert member.type is MemberType.SYMLINK
        assert member.link_target is None
        assert not member.is_junction
