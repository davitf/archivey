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
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import archivey
from archivey import ExtractionStatus, OverwritePolicy, open_archive
from archivey.diagnostics import DiagnosticCode
from archivey.exceptions import LinkTargetNotFoundError
from archivey.internal.backends import directory_reader
from archivey.internal.backends.rar_parser import RarMemberInfo
from archivey.internal.backends.rar_reader import _rar_member_extra_and_link
from archivey.internal.extraction_types import OnError
from archivey.internal.windows_reparse import (
    FILE_ATTRIBUTE_REPARSE_POINT,
    IO_REPARSE_TAG_MOUNT_POINT,
    IO_REPARSE_TAG_SYMLINK,
    parse_reparse_data,
)
from archivey.types import MemberType
from tests.conftest import requires_binary

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
        assert by_name[name].status is ExtractionStatus.LINK_TARGET_UNAVAILABLE
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
    assert (
        by_name["tree/junction_dir"].status is ExtractionStatus.LINK_TARGET_UNAVAILABLE
    )
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


def test_a_directory_shaped_reparse_point_with_odd_data_stays_a_link(
    tmp_path: Path,
) -> None:
    """Re-typing pays for a file and not for a directory, so it is declined here.

    The re-type exists to keep unrecognised data reachable, and only a FILE's content is
    reachable — `open()` refuses a DIRECTORY. Re-typing this member would have promised
    "that data as its content" while making the content unreadable, and the entry has
    already lost the trailing slash that made it a directory, because the ZIP backend
    suppresses that rename's diagnostic for a link stored with the directory convention.
    Leaving it a targetless link keeps what the archive actually said.
    """
    archive = tmp_path / "odd_directory_reparse.zip"
    _zip_with_reparse_member(
        archive,
        name="tree/weird/",
        attributes=_FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT,
        data=b"not a reparse buffer",
    )
    with open_archive(archive) as opened:
        (member,) = opened.members()
        assert member.type is MemberType.SYMLINK
        # The name that normalization produced is right for what the member stayed:
        # a link carries no trailing slash. Re-typing was what made it inconsistent.
        assert member.name == "tree/weird"
        assert member.link_target is None
        assert not member.is_junction
        assert _unavailable_reasons(opened) == ["reparse_data_unrecognized"]


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


# --------------------------------------------------------------------------------
# A link whose target the archive will not yield
# --------------------------------------------------------------------------------


def _encrypted_archive_with_a_symlink(tmp_path: Path, archive_type: str) -> Path:
    """Write an archive holding one symlink whose target is encrypted with its data.

    The symlink is left dangling on purpose: a regular file alongside it would be
    encrypted too, so extracting without the password would fail on that member and
    never reach the link.
    """
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "link.txt").symlink_to("absent.txt")
    archive = tmp_path / ("enc.zip" if archive_type == "-tzip" else "enc.7z")
    subprocess.run(
        ["7z", "a", archive_type, "-snl", "-pSECRET", "-y", str(archive), "tree"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    return archive


@requires_binary("7z")
@pytest.mark.parametrize(
    "archive_type", [pytest.param("-tzip", id="zip"), pytest.param("-t7z", id="7z")]
)
def test_an_encrypted_link_says_why_its_target_is_missing(
    tmp_path: Path, archive_type: str
) -> None:
    """A link whose target is unreadable has to say so, whatever put it out of reach.

    `ExtractionStatus.LINK_TARGET_UNAVAILABLE` carries no reason of its own — it says only that nothing
    was written — so the reason travels on the diagnostics channel instead, and
    `SYMLINK_TARGET_UNAVAILABLE` being in `ARCHIVE_INTEGRITY_CODES` is what lets a
    strict policy refuse such an archive outright. A backend that returns quietly turns
    an unreadable link into a silent omission, which is the one outcome
    `safe-extraction` rules out. Listing without a password still has to work, so this
    is a diagnostic and not a raise.

    Extraction is a different question, and this archive *does* record the target: it
    is locked, not missing. So the link fails the way the encrypted file beside it
    does, rather than being dropped from the output under a status that reports
    nothing wrong. Both assertions are here because the diagnostic and the extraction
    outcome answer to different rules, and the earlier version of this test conflated
    them.
    """
    archive = _encrypted_archive_with_a_symlink(tmp_path, archive_type)
    with open_archive(archive) as opened:
        (link,) = [m for m in opened.members() if m.type is MemberType.SYMLINK]
        assert link.link_target is None
        assert _unavailable_reasons(opened) == ["password_required"]

    dest = tmp_path / "out"
    results = archivey.extract(archive, dest, on_error=OnError.CONTINUE)
    by_name = {r.member.name: r for r in results}
    assert by_name["tree/link.txt"].status is ExtractionStatus.FAILED
    assert isinstance(by_name["tree/link.txt"].error, LinkTargetNotFoundError)
    assert not (dest / "tree" / "link.txt").exists()

    # The library default aborts the extraction, as it does for the encrypted file.
    with pytest.raises(LinkTargetNotFoundError):
        archivey.extract(archive, tmp_path / "stop")


@pytest.mark.parametrize(
    ("field", "value", "reason", "in_archive"),
    [
        pytest.param(
            "is_encrypted", True, "target_data_encrypted", True, id="encrypted"
        ),
        pytest.param(
            "split_after", True, "target_data_split_across_volumes", True, id="split"
        ),
        pytest.param(
            "compress_type", 0x33, "target_data_compressed", True, id="compressed"
        ),
        pytest.param("file_size", 0, "no_target_data", False, id="empty"),
    ],
)
def test_a_rar4_link_whose_data_is_out_of_reach_says_why(
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
    reason: str,
    in_archive: bool,
) -> None:
    """The RAR4 fallthrough was the third silent path, and it has four causes.

    RAR3/4 store a symlink's target as the member's own data, so the reader reads it
    straight out of the archive — and declines to when that data is encrypted, split
    across volumes, compressed rather than stored, or simply absent. Every one of those
    left `link_target` unset and said nothing, so `safe-extraction`'s promise that a
    skipped link is always explained held for ZIP and for nothing else.

    Patching the parsed header rather than writing four archives is deliberate: RAR 7
    dropped `-ma4`, so the writer this repo installs cannot produce a RAR4 archive at
    all, and each flag here is the only thing that would differ between this fixture
    and the archive a RAR4 writer would emit. The branch reads nothing else off the
    member. Only the symlinks are touched, so the rest of the listing stays honest.
    """
    original_init = RarMemberInfo.__init__

    def patched_init(self: RarMemberInfo, *args: object, **kwargs: object) -> None:
        original_init(self, *args, **kwargs)  # type: ignore[arg-type]
        if self.is_symlink:
            setattr(self, field, value)

    monkeypatch.setattr(RarMemberInfo, "__init__", patched_init)

    fixture = Path(__file__).parent / "fixtures" / "rar" / "symlinks_solid__rar4.rar"
    with open_archive(fixture) as opened:
        links = [m for m in opened.members() if m.type is MemberType.SYMLINK]
        assert links, "the fixture should still list its symlinks"
        assert all(m.link_target is None for m in links)
        assert _unavailable_reasons(opened) == [reason] * len(links)

    # And what extraction does with it, which is the half the reason name only hints
    # at. Three of these four are targets the archive carries and this read could not
    # reach; recording those as an outcome would drop a symlink the archive describes
    # in full while the report says nothing went wrong. Only `no_target_data` is the
    # archive's own omission.
    with tempfile.TemporaryDirectory() as raw_dest:
        dest = Path(raw_dest)
        report = archivey.extract(fixture, dest / "continue", on_error=OnError.CONTINUE)
        by_id = {r.member.member_id: r for r in report.results}
        link_results = [by_id[m.member_id] for m in links]
        assert link_results, "the symlinks should reach a write decision"
        if in_archive:
            assert all(r.status is ExtractionStatus.FAILED for r in link_results)
            assert all(
                isinstance(r.error, LinkTargetNotFoundError) for r in link_results
            )
            # The library default aborts on it, as it did before this status existed.
            with pytest.raises(LinkTargetNotFoundError):
                archivey.extract(fixture, dest / "stop")
        else:
            assert all(
                r.status is ExtractionStatus.LINK_TARGET_UNAVAILABLE
                for r in link_results
            )
            assert all(r.error is None for r in link_results)
            # Not a failure, so the library default carries on through it.
            archivey.extract(fixture, dest / "stop")


def test_a_streaming_symlink_is_not_silently_skipped(tmp_path: Path) -> None:
    """An unset `link_target` means two things, and only one of them is the archive's.

    A ZIP or 7z symlink stores its target as the member's data, so in streaming mode it
    reaches the write decision with `link_target` still unset — the reader has not read
    it yet, and cannot go back for it. The archive records that target perfectly well.
    Calling it the archive's omission would report success while dropping an ordinary
    POSIX symlink from the output, with no error and no diagnostic.

    Streaming still cannot write such a link, which is a gap of its own and not this
    status's business. What matters is that it stays loud: a failure the caller sees,
    the way it behaved before `LINK_TARGET_UNAVAILABLE` existed. Nothing in the suite covered a
    streaming-mode symlink at all, which is how the silent version got through.
    """
    archive = tmp_path / "unixlink.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("target.txt", b"payload\n")
        info = zipfile.ZipInfo("link")
        info.create_system = 3  # Unix
        info.external_attr = (0o120777 << 16) | 0o120000
        zf.writestr(info, b"target.txt")

    # The library default, OnError.STOP: the whole extraction stops on it.
    with open_archive(archive, streaming=True) as opened:
        with pytest.raises(LinkTargetNotFoundError):
            opened.extract_all(tmp_path / "stop")

    # And under CONTINUE it is a recorded failure, not a skip.
    dest = tmp_path / "continue"
    with open_archive(archive, streaming=True) as opened:
        report = opened.extract_all(dest, on_error=OnError.CONTINUE)
    by_name = {r.member.name: r for r in report.results}
    assert by_name["link"].status is ExtractionStatus.FAILED
    assert isinstance(by_name["link"].error, LinkTargetNotFoundError)
    assert by_name["target.txt"].status is ExtractionStatus.EXTRACTED


def _unavailable_reasons(opened: object) -> list[str]:
    """The `reason` of every `SYMLINK_TARGET_UNAVAILABLE` the reader has emitted."""
    return [
        d.context.reason
        for d in opened.diagnostics.retained  # type: ignore[attr-defined]
        if d.code is DiagnosticCode.SYMLINK_TARGET_UNAVAILABLE
    ]
