import os
from datetime import timedelta
import pytest

from archivey.core import open_archive
from archivey.exceptions import ArchiveEncryptedError
from archivey.types import ArchiveFormat, MemberType
from tests.archivey.sample_archives import (
    BASIC_ARCHIVES,
    DUPLICATE_FILES_ARCHIVES,
    SAMPLE_ARCHIVES,
    SampleArchive,
    filter_archives,
)
from tests.archivey.testing_utils import remove_duplicate_files, skip_if_package_missing


@pytest.mark.parametrize(
    "sample_archive",
    filter_archives(
        SAMPLE_ARCHIVES,
        prefixes=["hardlinks_nonsolid", "hardlinks_solid"],
        custom_filter=lambda a: a.creation_info.format != ArchiveFormat.ISO,
    ),
    ids=lambda a: a.filename,
)
def test_open_hardlinks(sample_archive: SampleArchive, sample_archive_path: str):
    skip_if_package_missing(sample_archive.creation_info.format, None)

    with open_archive(sample_archive_path) as archive:
        for info in sample_archive.contents.files:
            if info.type != MemberType.HARDLINK:
                continue
            with archive.open(info.name) as f:
                link_data = f.read()
            with archive.open(info.link_target) as f:
                target_data = f.read()
            assert link_data == target_data
            member_obj = archive.get_member(info.name)
            with archive.open(member_obj) as f:
                assert f.read() == target_data


@pytest.mark.parametrize(
    "sample_archive",
    [
        a
        for a in DUPLICATE_FILES_ARCHIVES
        if a.creation_info.features.duplicate_files
        and a.creation_info.format != ArchiveFormat.SEVENZIP
    ],
    ids=lambda a: a.filename,
)
def test_open_duplicate_files(sample_archive: SampleArchive, sample_archive_path: str):
    skip_if_package_missing(sample_archive.creation_info.format, None)

    with open_archive(sample_archive_path) as archive:
        members = archive.get_members()
        assert len(members) == len(sample_archive.contents.files)

        for member, sample_file in zip(members, sample_archive.contents.files):
            if sample_file.type != MemberType.FILE:
                continue
            with archive.open(member) as f:
                assert f.read() == (sample_file.contents or b"")

        for info in remove_duplicate_files([f for f in sample_archive.contents.files if f.type == MemberType.FILE]):
            with archive.open(info.name) as f:
                assert f.read() == (info.contents or b"")


@pytest.mark.parametrize(
    "sample_archive",
    filter_archives(
        SAMPLE_ARCHIVES,
        prefixes=["basic_nonsolid"],
        extensions=["zip"],
    ),
    ids=lambda a: a.filename,
)
def test_filter_functions(tmp_path, sample_archive: SampleArchive, sample_archive_path: str):
    skip_if_package_missing(sample_archive.creation_info.format, None)

    def _filter(member):
        if member.filename.endswith("file2.txt"):
            return None
        return member.replace(
            filename=f"prefixed/{member.filename}",
            mtime=(member.mtime + timedelta(days=1)) if member.mtime else None,
        )

    with open_archive(sample_archive_path) as archive:
        for member, stream in archive.iter_members_with_io(filter=_filter):
            assert member.filename.startswith("prefixed/")
            original_name = member.filename[len("prefixed/") :]
            orig_info = next(f for f in sample_archive.contents.files if f.name == original_name)
            if orig_info.mtime:
                expected_mtime = orig_info.mtime + timedelta(days=1)
                if sample_archive.creation_info.features.rounded_mtime:
                    assert abs(member.mtime.timestamp() - expected_mtime.timestamp()) <= 1
                else:
                    assert member.mtime == expected_mtime
            if member.is_file:
                assert stream.read() == (orig_info.contents or b"")
                with archive.open(member) as fh:
                    assert fh.read() == (orig_info.contents or b"")

        dest = tmp_path / "out"
        dest.mkdir()
        archive.extractall(dest, filter=_filter)

    extracted = {str(p.relative_to(dest)).replace(os.sep, "/") for p in dest.rglob("*")}
    expected = {
        f"prefixed/{f.name.rstrip('/')}"
        for f in sample_archive.contents.files
        if not f.name.endswith("file2.txt")
    }
    assert expected <= extracted


@pytest.mark.parametrize(
    "sample_archive",
    filter_archives(
        SAMPLE_ARCHIVES,
        prefixes=["encryption"],
        extensions=["rar"],
    ),
    ids=lambda a: a.filename,
)
def test_encrypted_password_in_constructor(sample_archive: SampleArchive, sample_archive_path: str):
    skip_if_package_missing(sample_archive.creation_info.format, None)
    pwd = sample_archive.contents.files[0].password or sample_archive.contents.header_password

    with open_archive(sample_archive_path, pwd=pwd) as archive:
        for info in sample_archive.contents.files:
            with archive.open(info.name) as f:
                assert f.read() == (info.contents or b"")


@pytest.mark.parametrize(
    "sample_archive",
    filter_archives(
        SAMPLE_ARCHIVES,
        prefixes=["encryption"],
        extensions=["rar"],
    ),
    ids=lambda a: a.filename,
)
def test_encrypted_password_open(sample_archive: SampleArchive, sample_archive_path: str):
    skip_if_package_missing(sample_archive.creation_info.format, None)
    pwd = sample_archive.contents.files[0].password or sample_archive.contents.header_password

    with open_archive(sample_archive_path) as archive:
        for info in sample_archive.contents.files:
            with archive.open(info.name, pwd=pwd) as f:
                assert f.read() == (info.contents or b"")


@pytest.mark.parametrize(
    "sample_archive",
    filter_archives(
        SAMPLE_ARCHIVES,
        prefixes=["encryption"],
        extensions=["rar"],
    ),
    ids=lambda a: a.filename,
)
def test_encrypted_password_extractall(tmp_path, sample_archive: SampleArchive, sample_archive_path: str):
    skip_if_package_missing(sample_archive.creation_info.format, None)
    pwd = sample_archive.contents.files[0].password or sample_archive.contents.header_password

    dest = tmp_path / "out"
    dest.mkdir()
    with open_archive(sample_archive_path) as archive:
        archive.extractall(dest, pwd=pwd)

    for info in sample_archive.contents.files:
        path = dest / info.name
        if info.type == MemberType.FILE:
            with open(path, "rb") as f:
                assert f.read() == (info.contents or b"")


@pytest.mark.parametrize(
    "sample_archive",
    filter_archives(
        SAMPLE_ARCHIVES,
        prefixes=["encryption"],
        extensions=["rar"],
    ),
    ids=lambda a: a.filename,
)
def test_encrypted_wrong_password_open(sample_archive: SampleArchive, sample_archive_path: str):
    skip_if_package_missing(sample_archive.creation_info.format, None)
    with open_archive(sample_archive_path) as archive:
        member = next(m for m in archive.get_members() if m.is_file)
        with pytest.raises(ArchiveEncryptedError):
            with archive.open(member, pwd="wrong"):
                pass


@pytest.mark.parametrize(
    "sample_archive",
    filter_archives(
        SAMPLE_ARCHIVES,
        prefixes=["encryption_several_passwords"],
        extensions=["rar"],
    ),
    ids=lambda a: a.filename,
)
def test_encrypted_wrong_password_iter(sample_archive: SampleArchive, sample_archive_path: str):
    skip_if_package_missing(sample_archive.creation_info.format, None)
    with open_archive(sample_archive_path) as archive:
        members = list(archive.iter_members_with_io(pwd="wrong"))
        member, stream = members[0]
        with pytest.raises(ArchiveEncryptedError):
            stream.read(1)
        # Skipping read on others should not raise
        for m, s in members[1:]:
            pass

