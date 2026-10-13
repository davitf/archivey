"""``ArchiveMember.member_state_final``: whether archivey can still change the member.

It is false while a member handed to the caller can still be updated in place: during a
forward pass, until the pass ends (a later member with the same name can still make it
``is_current=False``), and for a symlink whose target is stored as member data, until
that target has been read. It is true everywhere else.
"""

from __future__ import annotations

import io
import stat
import tarfile
import zipfile
from pathlib import Path

import pytest

from archivey import open_archive
from archivey.config import ArchiveyConfig, ListingLimits
from archivey.types import ArchiveMember
from tests.conftest import requires_binary
from tests.streams_util import NonSeekableBytesIO


def _tar(
    entries: list[tuple[str, bytes]], *, symlink: tuple[str, str] | None = None
) -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w") as tf:
        for name, data in entries:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        if symlink is not None:
            info = tarfile.TarInfo(symlink[0])
            info.type = tarfile.SYMTYPE
            info.linkname = symlink[1]
            tf.addfile(info)
    return out.getvalue()


def _zip(
    path: Path,
    entries: list[tuple[str, bytes]],
    *,
    symlink: tuple[str, str] | None = None,
) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries:
            zf.writestr(name, data)
        if symlink is not None:
            info = zipfile.ZipInfo(symlink[0])
            info.create_system = 3  # Unix
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(info, symlink[1])


_DUPLICATES = [("a.txt", b"first"), ("b.txt", b"other"), ("a.txt", b"second")]


@pytest.mark.parametrize("seekable", [True, False], ids=["file", "pipe"])
def test_a_tar_forward_pass_settles_its_members_when_it_ends(
    tmp_path: Path, seekable: bool
) -> None:
    """Every member the pass yields reads false until the pass ends, then true.

    The first ``a.txt`` shows why: it reads ``is_current`` true while it is yielded,
    and only the end of the pass, which has seen the second copy, makes it false.
    """
    data = _tar(_DUPLICATES)
    path = tmp_path / "dup.tar"
    path.write_bytes(data)
    source = path if seekable else NonSeekableBytesIO(data)
    seen: list[tuple[ArchiveMember, bool, bool]] = []
    with open_archive(source, streaming=True) as reader:
        for member, _stream in reader.stream_members():
            seen.append((member, member.member_state_final, member.is_current))
    assert [(final, current) for _, final, current in seen] == [
        (False, True),
        (False, True),
        (False, True),
    ]
    members = [member for member, _, _ in seen]
    assert [m.member_state_final for m in members] == [True, True, True]
    assert [m.is_current for m in members] == [False, True, True]


def test_a_tar_listing_is_final_when_returned(tmp_path: Path) -> None:
    """A random-access listing is complete before the caller sees any member."""
    path = tmp_path / "dup.tar"
    path.write_bytes(_tar(_DUPLICATES))
    with open_archive(path) as reader:
        assert [m.member_state_final for m in reader.members()] == [True, True, True]
        assert [m.member_state_final for m, _ in reader.stream_members()] == [
            True,
            True,
            True,
        ]


@pytest.mark.filterwarnings("ignore:Duplicate name:UserWarning")
def test_a_streaming_zip_pass_settles_its_members_when_it_ends(tmp_path: Path) -> None:
    """A seekable ZIP read with ``streaming=True`` is a forward pass too."""
    path = tmp_path / "dup.zip"
    _zip(path, _DUPLICATES)
    with open_archive(path, streaming=True) as reader:
        seen = [(m, m.member_state_final) for m, _ in reader.stream_members()]
    assert [final for _, final in seen] == [False, False, False]
    assert [m.member_state_final for m, _ in seen] == [True, True, True]
    assert [m.is_current for m, _ in seen] == [False, True, True]


def test_a_pass_over_the_listing_limits_settles_what_it_stops_keeping(
    tmp_path: Path,
) -> None:
    """A pass that crosses ``ListingLimits`` stops keeping members; none can change.

    The kept prefix is stamped when the pass stops keeping it, and a member yielded
    after that is never stamped, so both read true from then on.
    """
    data = _tar(_DUPLICATES)
    config = ArchiveyConfig(listing_limits=ListingLimits(max_members=1))
    with open_archive(
        NonSeekableBytesIO(data), streaming=True, config=config
    ) as reader:
        seen = [(m, m.member_state_final) for m, _ in reader.stream_members()]
    assert [final for _, final in seen] == [False, True, True]
    assert [m.member_state_final for m, _ in seen] == [True, True, True]


@pytest.mark.parametrize("read_link_targets", [True, False])
def test_a_tar_symlink_carries_its_target_in_the_header(
    tmp_path: Path, read_link_targets: bool
) -> None:
    """Nothing is left to read for a target the header carries."""
    path = tmp_path / "link.tar"
    path.write_bytes(_tar([("a.txt", b"x")], symlink=("link", "a.txt")))
    config = ArchiveyConfig(read_link_targets=read_link_targets)
    with open_archive(path, config=config) as reader:
        link = reader.get("link")
        assert link.link_target == "a.txt"
        assert link.member_state_final


def test_a_zip_symlink_is_final_once_its_target_is_read(tmp_path: Path) -> None:
    """A ZIP symlink's target is its data. Unread, the member can still change."""
    path = tmp_path / "link.zip"
    _zip(path, [("a.txt", b"x")], symlink=("link", "a.txt"))
    with open_archive(path, config=ArchiveyConfig(read_link_targets=False)) as reader:
        link = reader.get("link")
        assert link.link_target is None
        assert not link.member_state_final
        assert reader.get("a.txt").member_state_final
        reader.extract_all(tmp_path / "out")
        assert link.link_target == "a.txt"
        assert link.member_state_final


def test_a_zip_symlink_read_at_listing_is_final(tmp_path: Path) -> None:
    path = tmp_path / "link.zip"
    _zip(path, [("a.txt", b"x")], symlink=("link", "a.txt"))
    with open_archive(path) as reader:
        link = reader.get("link")
        assert link.link_target == "a.txt"
        assert link.member_state_final


_FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    "fixture",
    [
        pytest.param("sevenzip/links_mid_folder_solid.7z", id="7z"),
        pytest.param(
            "rar/symlinks_solid__rar4.rar",
            id="rar4",
            # The links' targets read natively; the files they point at are RAR data.
            marks=requires_binary("unrar"),
        ),
    ],
)
def test_a_data_stored_link_target_is_final_once_extraction_reads_it(
    tmp_path: Path, fixture: str
) -> None:
    """7z and RAR 4 store a symlink's target as its data too."""
    with open_archive(
        _FIXTURES / fixture, config=ArchiveyConfig(read_link_targets=False)
    ) as reader:
        links = [m for m in reader.members() if m.is_link]
        assert links
        assert [m.member_state_final for m in links] == [False] * len(links)
        reader.extract_all(tmp_path / "out")
        assert [m.member_state_final for m in links] == [True] * len(links)
        assert all(m.link_target for m in links)


def test_a_rar5_link_target_is_in_the_header() -> None:
    with open_archive(
        _FIXTURES / "rar/symlinks_solid__.rar",
        config=ArchiveyConfig(read_link_targets=False),
    ) as reader:
        links = [m for m in reader.members() if m.is_link]
        assert links
        assert all(m.member_state_final and m.link_target for m in links)
