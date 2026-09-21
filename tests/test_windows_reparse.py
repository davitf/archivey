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
import tarfile
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import archivey
from archivey import ExtractionStatus, OverwritePolicy, open_archive
from archivey.diagnostics import DiagnosticCode
from archivey.exceptions import LinkTargetNotFoundError
from archivey.internal.backends import directory_reader
from archivey.internal.backends.rar_reader import _rar_member_extra_and_link
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


@pytest.mark.parametrize("which", ["substitute", "print"])
def test_an_odd_name_length_is_declined_rather_than_raising(which: str) -> None:
    """A name length is a byte count over UTF-16 code units, so an odd one is malformed.

    `errors=` does not cover a trailing half code unit — `surrogatepass` raises on it
    too — so an unguarded decode sends `UnicodeDecodeError` out of `members()` from a
    crafted archive. `test_truncated_payload_does_not_raise` cannot reach this: cutting
    the buffer leaves the declared offsets out of bounds, so the bounds guard
    short-circuits before any decode runs. The length has to be odd *and* in bounds.
    """
    subst_length, print_length = (3, 0) if which == "substitute" else (0, 3)
    body = struct.pack("<HHHH", 0, subst_length, 0, print_length) + b"abcd"
    data = struct.pack("<IHH", IO_REPARSE_TAG_MOUNT_POINT, len(body), 0) + body
    parsed = parse_reparse_data(data)
    assert parsed is not None
    assert parsed.is_junction
    # Declined, not decoded to the even prefix: reporting half a path as the target
    # would be inventing one.
    assert parsed.target == ""


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


@pytest.mark.parametrize(
    "fixture",
    [
        pytest.param("junction_7zip_snl.zip", id="zip"),
        pytest.param("junction_7zip_snl.7z", id="7z"),
    ],
)
@pytest.mark.parametrize(
    "name", ["tree/junction_dir", "tree/symlink_dir", "tree/symlink_file"]
)
def test_every_reparse_member_is_flagged_as_one(fixture: str, name: str) -> None:
    """The weaker fact the archive does record, in both formats.

    `is_junction` needs the tag out of the member's data and 7-Zip does not store it for
    a directory reparse point, so it is unset on all three. The attribute bit is in the
    header, so this one is set on all three — including the file symlink, which is what
    lets a caller tell a Windows symlink from a POSIX one.
    """
    with open_archive(_JUNCTION_DIR / fixture) as archive:
        member = next(m for m in archive.members() if m.name == name)
        assert member.is_reparse_point
        assert member.extra["is_reparse_point"] is True
        assert not member.is_junction


def test_a_plain_unix_symlink_is_not_flagged(tmp_path: Path) -> None:
    """The flag says "Windows", so a POSIX symlink must not carry it."""
    archive = tmp_path / "unix_symlink.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        info = zipfile.ZipInfo("link")
        info.create_system = 3  # UNIX
        info.external_attr = (0o120777 << 16) | 0x20
        zf.writestr(info, b"target.txt")
    with open_archive(archive) as opened:
        (member,) = opened.members()
        assert member.type is MemberType.SYMLINK
        assert member.link_target == "target.txt"
        assert not member.is_reparse_point
        assert "is_reparse_point" not in member.extra


def test_a_reinterpreted_member_keeps_the_reparse_flag(tmp_path: Path) -> None:
    """The flag records what the archive said, so it survives the re-typing.

    `is_reparse_point` is deliberately not gated on the member's type: the archive did
    flag this entry, and that is the explanation for why it looked odd.
    """
    archive = tmp_path / "odd_reparse.zip"
    _zip_with_reparse_member(
        archive,
        name="tree/weird",
        attributes=0x20 | FILE_ATTRIBUTE_REPARSE_POINT,
        data=b"not a reparse buffer",
    )
    with open_archive(archive) as opened:
        (member,) = opened.members()
        assert member.type is MemberType.FILE
        assert member.is_reparse_point


# --------------------------------------------------------------------------------
# The other two formats that state the kind
# --------------------------------------------------------------------------------


def _rar_info(redirect_type: int) -> SimpleNamespace:
    """The fields `_rar_member_extra_and_link` reads, and nothing else."""
    return SimpleNamespace(
        file_redir=(redirect_type, 0, "target"),
        is_file_version_history=lambda: False,
        file_version=None,
        extract_version=None,
        file_encryption=None,
        crc32=None,
        blake2sp_hash=None,
        host_os=None,
        ctime=None,
    )


@pytest.mark.parametrize(
    ("redirect_type", "reparse", "junction"),
    [
        pytest.param(1, False, False, id="unix-symlink"),
        pytest.param(2, True, False, id="windows-symlink"),
        pytest.param(3, True, True, id="windows-junction"),
    ],
)
def test_rar_flags_both_windows_redirect_types(
    redirect_type: int, reparse: bool, junction: bool
) -> None:
    """RAR names the kind in a header field, so it can flag while listing.

    A RAR5 Windows symlink (redirect type 2) is a reparse point just as much as a
    junction (type 3); only a Unix symlink (type 1) is not. Flagging the junction alone
    would make the key mean something narrower in RAR than in ZIP and 7z.
    """
    extra, _ = _rar_member_extra_and_link(_rar_info(redirect_type))  # type: ignore[arg-type]
    assert extra.get("is_reparse_point", False) is reparse
    assert extra.get("is_junction", False) is junction


@pytest.mark.parametrize("os_name", ["nt", "posix"])
def test_a_scanned_symlink_is_a_reparse_point_only_on_windows(
    monkeypatch: pytest.MonkeyPatch, os_name: str
) -> None:
    """Scanning a live tree: on Windows a symlink *is* a reparse point, there being no
    other kind; elsewhere it is a POSIX one and the key does not apply."""
    monkeypatch.setattr(directory_reader.os, "name", os_name)
    extra = directory_reader._link_extra(MemberType.SYMLINK, is_junction=False)
    assert extra.get("is_reparse_point", False) is (os_name == "nt")
    file_extra = directory_reader._link_extra(MemberType.FILE, is_junction=False)
    assert "is_reparse_point" not in file_extra


# --------------------------------------------------------------------------------
# Extracting a link the archive gave no target for
# --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fixture",
    [
        pytest.param("junction_7zip_snl.zip", id="zip"),
        pytest.param("junction_7zip_snl.7z", id="7z"),
    ],
)
def test_a_targetless_link_is_skipped_and_the_rest_extracts(
    fixture: str, tmp_path: Path
) -> None:
    """Nothing can be written for it, and nothing here went wrong, so it is not a failure.

    The library default is `OnError.STOP`, so treating it as a per-member failure aborted
    the whole archive on the first such member — and 7-Zip writes one for every directory
    symlink and every junction.
    """
    results = archivey.extract(_JUNCTION_DIR / fixture, tmp_path)
    by_name = {r.member.name: r for r in results}
    for name in ("tree/junction_dir", "tree/symlink_dir"):
        assert by_name[name].status is ExtractionStatus.SKIPPED
        assert by_name[name].path is None
        assert by_name[name].error is None
    assert by_name["tree/regular.txt"].status is ExtractionStatus.EXTRACTED
    assert by_name["tree/symlink_file"].status is ExtractionStatus.EXTRACTED
    assert (tmp_path / "tree" / "regular.txt").read_bytes() == b"plain\r\n"
    # Nothing was left behind at the skipped paths.
    assert not (tmp_path / "tree" / "junction_dir").exists()


def test_skipping_a_targetless_link_does_not_replace_what_is_there(
    tmp_path: Path,
) -> None:
    """A member that is not going to be written must not unlink an existing destination.

    `OverwritePolicy.REPLACE` unlinks first and creates fresh, so checking the target
    after overwrite resolution would destroy the entry on behalf of a member that then
    writes nothing.
    """
    dest = tmp_path / "out"
    (dest / "tree").mkdir(parents=True)
    existing = dest / "tree" / "junction_dir"
    existing.write_text("previously here", encoding="utf-8")
    results = archivey.extract(
        _JUNCTION_DIR / "junction_7zip_snl.zip",
        dest,
        overwrite=OverwritePolicy.REPLACE,
    )
    by_name = {r.member.name: r for r in results}
    assert by_name["tree/junction_dir"].status is ExtractionStatus.SKIPPED
    assert existing.read_text(encoding="utf-8") == "previously here"


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


def test_a_nameless_junction_buffer_still_sets_the_flag(tmp_path: Path) -> None:
    """The tag is the buffer's first field, so it survives a buffer that names nothing.

    Dropping the flag because a *different* field was empty would throw away the one
    thing the tag established, and `ReparsePoint.target` documents `""` as a legitimate
    value.
    """
    archive = tmp_path / "nameless_junction.zip"
    _zip_with_reparse_member(
        archive,
        name="tree/junction_dir/",
        attributes=_FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT,
        data=struct.pack("<IHH", IO_REPARSE_TAG_MOUNT_POINT, 8, 0)
        + struct.pack("<HHHH", 0, 0, 0, 0),
    )
    with open_archive(archive) as opened:
        (member,) = opened.members()
        assert member.type is MemberType.SYMLINK
        assert member.is_junction
        assert member.link_target is None


def test_a_reparse_member_whose_data_is_not_a_link_keeps_its_content(
    tmp_path: Path,
) -> None:
    """The bit is a candidate; the data decides, and here the data is a file's content.

    Windows sets `FILE_ATTRIBUTE_REPARSE_POINT` for deduplication stubs, cloud
    placeholders and WSL entries too. Typing on the bit alone would make this member a
    link with no target, so `open()` on it would raise and its real content — the one
    thing we know is in the archive — would be unreachable.
    """
    archive = tmp_path / "odd_reparse.zip"
    _zip_with_reparse_member(
        archive,
        name="tree/weird",
        attributes=0x20 | FILE_ATTRIBUTE_REPARSE_POINT,
        data=b"not a reparse buffer",
    )
    with open_archive(archive) as opened:
        (member,) = opened.members()
        assert member.type is MemberType.FILE
        assert member.link_target is None
        assert not member.is_junction
        with opened.open(member) as stream:
            assert stream.read() == b"not a reparse buffer"


def test_a_directory_reparse_point_with_no_data_stays_a_link(tmp_path: Path) -> None:
    """The other side of the rule: nothing to reinterpret, so the link type stands."""
    archive = tmp_path / "empty_reparse.zip"
    _zip_with_reparse_member(
        archive,
        name="tree/junction_dir/",
        attributes=_FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT,
        data=b"",
    )
    with open_archive(archive) as opened:
        (member,) = opened.members()
        assert member.type is MemberType.SYMLINK
        assert member.link_target is None


def test_the_reparse_bit_is_read_only_from_a_dos_creator(tmp_path: Path) -> None:
    """`0x400` in the low word is a Win32 attribute only for a DOS/Windows creator.

    Every other creator puts its own platform's bits there, so the bit is a
    coincidence. A directory is the case where that is observable: it carries no data
    for the parser to overrule the type with, so a widened creator test turns an
    ordinary Macintosh-created directory into a link with no target — and drops its
    trailing slash on the way.
    """
    archive = tmp_path / "mac_creator.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        info = zipfile.ZipInfo("tree/plain/")
        info.create_system = 7  # MACINTOSH
        info.external_attr = _FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT
        zf.writestr(info, b"")
    with open_archive(archive) as opened:
        (member,) = opened.members()
        assert member.type is MemberType.DIRECTORY
        assert member.name == "tree/plain/"
        assert member.link_target is None


@pytest.mark.parametrize(
    "fixture",
    [
        pytest.param("junction_7zip_snl.zip", id="zip"),
        pytest.param("junction_7zip_snl.7z", id="7z"),
    ],
)
def test_a_missing_target_is_reported_once_per_member(fixture: str) -> None:
    """Looking for a target that is not there must not repeat on every access.

    Nothing the lookup can learn changes between two calls, so a second one only
    re-reads the member and emits the same diagnostic again — counting one targetless
    link as two in `DiagnosticSummary.counts`.
    """
    code = DiagnosticCode.SYMLINK_TARGET_UNAVAILABLE
    with open_archive(_JUNCTION_DIR / fixture) as archive:
        members = archive.members()
        assert archive.diagnostics.counts.get(code) == 2
        for member in members:
            if member.type is MemberType.SYMLINK and member.link_target is None:
                with pytest.raises(LinkTargetNotFoundError):
                    archive.open(member)
        assert archive.diagnostics.counts.get(code) == 2


def test_a_tar_symlink_spelled_as_a_directory_is_still_reported(tmp_path: Path) -> None:
    """The ZIP suppression must not reach a backend that never asked for it.

    A ZIP directory reparse point is *stored* with the directory convention's trailing
    slash, so dropping it is the format's spelling rather than an author override. A TAR
    `SYMTYPE` entry named `link/` is an anomaly, and `MEMBER_NAME_NORMALIZED` is an
    archive-integrity code, so suppressing it there would silently stop a strict policy
    refusing that archive.
    """
    archive = tmp_path / "slashed_symlink.tar"
    with tarfile.open(archive, "w") as tf:
        info = tarfile.TarInfo("link/")
        info.type = tarfile.SYMTYPE
        info.linkname = "target.txt"
        tf.addfile(info)
    with open_archive(archive) as opened:
        (member,) = opened.members()
        assert member.name == "link"
        assert opened.diagnostics.counts.get(DiagnosticCode.MEMBER_NAME_NORMALIZED) == 1
