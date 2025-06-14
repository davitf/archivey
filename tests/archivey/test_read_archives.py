import collections
import logging
import os
from datetime import datetime
from typing import Optional

import pytest

from archivey.config import ArchiveyConfig
from archivey.core import open_archive
from archivey.dependency_checker import get_dependency_versions
from archivey.exceptions import ArchiveException, ArchiveMemberCannotBeOpenedError
from archivey.types import ArchiveMember, CreateSystem, MemberType
from tests.archivey.sample_archives import (
    MARKER_MTIME_BASED_ON_ARCHIVE_NAME,
    SAMPLE_ARCHIVES,
    FileInfo,
    SampleArchive,
    filter_archives,
)
from tests.archivey.testing_utils import (
    get_crc32,
    normalize_newlines,
    skip_if_package_missing,
)


def check_member_metadata(
    member: ArchiveMember,
    sample_file: FileInfo | None,
    sample_archive: SampleArchive,
    archive_path: str | None = None,
):
    if sample_file is None:
        return

    features = sample_archive.creation_info.features

    if member.is_file:
        if features.file_size:
            assert member.file_size == len(sample_file.contents or b"")
        else:
            assert member.file_size is None

    if member.is_file and member.crc32 is not None:
        sample_crc32 = get_crc32(sample_file.contents or b"")
        assert member.crc32 == sample_crc32, (
            f"CRC32 mismatch for {member.filename}: got {member.crc32}, expected {sample_crc32}"
        )

    if sample_file.compression_method is not None:
        assert member.compression_method == sample_file.compression_method

    if features.file_comments:
        assert member.comment == sample_file.comment
    else:
        assert member.comment is None

    # Check permissions
    if sample_file.permissions is not None:
        assert member.mode is not None, (
            f"Permissions not set for {member.filename} in {sample_archive.filename} "
            f"(expected {oct(sample_file.permissions)})"
        )
        assert member.mode == sample_file.permissions, (
            f"Permission mismatch for {member.filename} in {sample_archive.filename}: "
            f"got {oct(member.mode) if member.mode is not None else 'None'}, "
            f"expected {oct(sample_file.permissions)}"
        )

    # 0-byte files may not be marked as encrypted (e.g. in 7z archives with header encryption)
    if sample_file.contents:
        assert member.encrypted == (
            sample_file.password is not None
            or (member.is_file and sample_archive.contents.header_password is not None)
        ), (
            f"Encrypted mismatch for {member.filename}: got {member.encrypted}, expected {sample_file.password is not None}"
        )

    if not features.mtime:
        assert member.mtime is None
    elif not features.hardlink_mtime and member.type == MemberType.HARDLINK:
        # Hardlinks may have the timestamp of the pointed file, don't check it.
        pass
    elif sample_file.mtime == MARKER_MTIME_BASED_ON_ARCHIVE_NAME:
        archive_file_mtime = datetime.fromtimestamp(
            os.path.getmtime(archive_path or sample_archive.get_archive_path())
        )
        assert member.mtime == archive_file_mtime, (
            f"Timestamp mismatch for {member.filename} (special check): "
            f"member mtime {member.mtime} vs archive mtime {archive_file_mtime}"
        )
    elif features.rounded_mtime:
        assert member.mtime is not None
        assert abs(member.mtime.timestamp() - sample_file.mtime.timestamp()) <= 1, (
            f"Timestamp mismatch for {member.filename}: {member.mtime} != {sample_file.mtime}"
        )
    else:  # Expect exact match
        assert member.mtime == sample_file.mtime, (
            f"Timestamp mismatch for {member.filename}: {member.mtime} != {sample_file.mtime}"
        )

    # TODO: set feature
    if member.create_system is not None:
        assert member.create_system in {
            CreateSystem.UNIX,
            CreateSystem.UNKNOWN,
        }


def check_iter_members(
    sample_archive: SampleArchive,
    archive_path: str,
    set_file_password_in_constructor: bool = True,
    skip_member_contents: bool = False,
    config: Optional[ArchiveyConfig] = None,
):
    skip_if_package_missing(sample_archive.creation_info.format, config)

    if (
        archive_path.endswith(".tar.zst")
        and config is not None
        and config.use_zstandard
    ):
        pytest.skip(
            "Skipping test for .tar.zst archives with zstandard enabled, as zstandard doesn't support seeking"
        )

    if sample_archive.skip_test:
        pytest.skip(f"Skipping test for {sample_archive.filename} as skip_test is True")

    if sample_archive.contents.has_multiple_passwords():
        pytest.skip(
            f"Skipping test for {sample_archive.filename} as it has multiple passwords"
        )

    features = sample_archive.creation_info.features

    # If the archive may have duplicate files, we need to compare the files in the
    # iterator with the ones in the sample_archive in the same order.
    # Otherwise, the archive should have only the last version of the file.
    expected_files_by_filename: collections.defaultdict[str, list[FileInfo]] = (
        collections.defaultdict(list)
    )

    for sample_file in sample_archive.contents.files:
        if features.dir_entries or sample_file.type != MemberType.DIR:
            expected_files_by_filename[sample_file.name].append(sample_file)

    # expected_filenames = set(expected_files_by_filename.keys())

    constructor_password = sample_archive.contents.header_password

    if (
        set_file_password_in_constructor
        and sample_archive.contents.has_password_in_files()
    ):
        assert constructor_password is None, (
            "Can't set file password in constructor if header password is already set"
        )
        assert not sample_archive.contents.has_multiple_passwords(), (
            "Can't set file password in constructor if there are multiple passwords"
        )
        constructor_password = next(
            iter(
                f.password
                for f in sample_archive.contents.files
                if f.password is not None
            )
        )

    archive_path_resolved = archive_path or sample_archive.get_archive_path()
    with open_archive(
        archive_path_resolved,
        pwd=constructor_password,
        config=config,
    ) as archive:
        assert archive.format == sample_archive.creation_info.format
        format_info = archive.get_archive_info()
        assert normalize_newlines(format_info.comment) == normalize_newlines(
            sample_archive.contents.archive_comment
        )

        members_iter = (
            ((m, None) for m in archive.get_members())
            if skip_member_contents
            else archive.iter_members_with_io()
        )

        # logger.info(f"files_by_name: {expected_files_by_filename}")
        # logger.info(f"skip_member_contents: {skip_member_contents}")
        # logger.info(f"members_iter: {members_iter}")

        all_contents_by_filename: collections.defaultdict[
            str, list[tuple[ArchiveMember, bytes | None]]
        ] = collections.defaultdict(list)
        all_non_dirs_in_archive = set()

        for member, stream in members_iter:
            logger.info(
                f"member: {member.filename} [{member.type}] [{member.member_id}] {stream=}"
            )
            filekey = member.filename
            if member.is_dir:
                assert member.filename.endswith("/"), (
                    f"Directory {member.filename} does not end with /"
                )
            else:
                assert not member.filename.endswith("/"), (
                    f"{member.type} {member.filename} ends with /"
                )

            if not skip_member_contents and member.is_file:
                assert stream is not None, (
                    f"Stream not provided for {member.filename} ({member.type})"
                )
            else:
                assert stream is None, (
                    f"Stream provided for {member.filename} ({member.type}) (data={stream.read()})"
                )

            # TODO: compare data for resolved links
            data = stream.read() if stream is not None else None

            all_contents_by_filename[filekey].append((member, data))
            if member.type != MemberType.DIR:
                all_non_dirs_in_archive.add(filekey)

        # Check that all expected filenames are present in the archive.
        assert not set(expected_files_by_filename.keys()) - set(
            all_contents_by_filename.keys()
        ), (
            f"Expected files {set(expected_files_by_filename.keys()) - set(all_contents_by_filename.keys())} not found in archive"
        )
        # The archive may contain extra dirs that were implicit in the file list,
        # but not other unexpected files.
        assert not all_non_dirs_in_archive - set(expected_files_by_filename.keys()), (
            f"Extra files {all_non_dirs_in_archive - set(expected_files_by_filename.keys())} found in archive"
        )

        # Check that the contents of the members are the same as the contents of the files.
        for filename, expected_files in expected_files_by_filename.items():
            actual_files = all_contents_by_filename[filename]
            if features.duplicate_files:
                assert len(actual_files) == len(expected_files), (
                    f"Expected {len(expected_files)} files for {filename}, got {len(actual_files)}"
                )
            else:
                assert len(actual_files) == 1, (
                    f"Expected 1 file for {filename}, got {len(actual_files)}"
                )
                # We expect only the last file with a given filename to be present.
                expected_files = [expected_files[-1]]

            actual_files.sort(key=lambda x: x[0].member_id)

            for i in range(len(expected_files)):
                logger.info(f"Checking {filename} ({i})")
                sample_file = expected_files[i]
                member, contents = actual_files[i]

                check_member_metadata(
                    member,
                    sample_file,
                    sample_archive,
                    archive_path=archive_path_resolved,
                )

                if sample_file.type == MemberType.FILE and not skip_member_contents:
                    assert contents == sample_file.contents

                if sample_file.contents is not None and archive.has_random_access():
                    with archive.open(member) as stream:
                        assert stream.read() == sample_file.contents
                else:
                    with pytest.raises((ValueError, ArchiveException)):
                        stream = archive.open(member)
                        logger.info(
                            f"Unexpected open() success for {member=}; data={stream.read()}"
                        )

            # Check that opening the file by filename gives the most recent contents.
            sample_file = expected_files[-1]
            if sample_file.contents is not None and archive.has_random_access():
                with archive.open(filename) as stream:
                    assert stream.read() == sample_file.contents
            else:
                with pytest.raises((ValueError, ArchiveException)):
                    archive.open(filename)


@pytest.mark.parametrize(
    "sample_archive",
    filter_archives(
        SAMPLE_ARCHIVES,
        extensions=["zip"],
    ),
    ids=lambda x: x.filename,
)
def test_read_zip_archives(sample_archive: SampleArchive, sample_archive_path: str):
    check_iter_members(sample_archive, archive_path=sample_archive_path)


logger = logging.getLogger(__name__)


@pytest.mark.parametrize(
    "sample_archive",
    filter_archives(
        SAMPLE_ARCHIVES,
        extensions=["tar", "tar.gz", "tar.bz2", "tar.xz", "tar.zst", "tar.lz4"],
    ),
    ids=lambda x: x.filename,
)
@pytest.mark.parametrize("alternative_packages", [False, True])
def test_read_tar_archives(
    sample_archive: SampleArchive, sample_archive_path: str, alternative_packages: bool
):
    logger.info(
        f"Testing {sample_archive.filename} with format {sample_archive.creation_info.format}"
    )

    if alternative_packages:
        config = ArchiveyConfig(
            use_rapidgzip=True,
            use_indexed_bzip2=True,
            use_python_xz=True,
            use_zstandard=True,
        )
    else:
        config = None

    skip_if_package_missing(sample_archive.creation_info.format, config)

    check_iter_members(
        sample_archive,
        archive_path=sample_archive_path,
        skip_member_contents=True,
        config=config,
    )


# @pytest.mark.parametrize(
#     "sample_archive",
#     filter_archives(SAMPLE_ARCHIVES, extensions=["iso"]),
#     ids=lambda x: x.filename,
# )
# def test_read_iso_archives(sample_archive: SampleArchive, sample_archive_path: str):
#     if not pathlib.Path(sample_archive_path).exists():
#         pytest.skip("ISO archive not available")
#     check_iter_members(sample_archive, archive_path=sample_archive_path)


@pytest.mark.parametrize(
    "sample_archive",
    filter_archives(SAMPLE_ARCHIVES, extensions=["rar"]),
    ids=lambda x: x.filename,
)
@pytest.mark.parametrize("use_rar_stream", [True, False])
def test_read_rar_archives(
    sample_archive: SampleArchive, sample_archive_path: str, use_rar_stream: bool
):
    deps = get_dependency_versions()
    if (
        sample_archive.contents.header_password is not None
        and deps.cryptography_version is None
    ):
        pytest.skip("Cryptography is not installed, skipping RAR encrypted-header test")

    if use_rar_stream and deps.unrar_version is None:
        pytest.skip("unrar not installed, skipping RarStreamReader test")

    config = ArchiveyConfig(use_rar_stream=use_rar_stream)

    has_password = sample_archive.contents.has_password()
    has_multiple_passwords = sample_archive.contents.has_multiple_passwords()
    first_file_has_password = sample_archive.contents.files[0].password is not None

    expect_failure = use_rar_stream and (
        has_multiple_passwords
        or (
            has_password
            and not first_file_has_password
            and not sample_archive.contents.header_password
        )
    )

    if expect_failure:
        with pytest.raises(ValueError):
            check_iter_members(
                sample_archive,
                archive_path=sample_archive_path,
                config=config,
            )
    else:
        check_iter_members(
            sample_archive,
            archive_path=sample_archive_path,
            config=config,
            skip_member_contents=deps.unrar_version is None,
        )


@pytest.mark.parametrize(
    "sample_archive",
    filter_archives(
        SAMPLE_ARCHIVES,
        extensions=["rar"],
        custom_filter=lambda x: x.contents.has_password()
        and not x.contents.has_multiple_passwords()
        and x.contents.header_password is None,
    ),
    ids=lambda x: x.filename,
)
@pytest.mark.parametrize("use_rar_stream", [True, False])
def test_read_rar_archives_with_password_in_constructor(
    sample_archive: SampleArchive, sample_archive_path: str, use_rar_stream: bool
):
    deps = get_dependency_versions()
    if use_rar_stream and deps.unrar_version is None:
        pytest.skip("unrar not installed, skipping RarStreamReader test")

    config = ArchiveyConfig(use_rar_stream=use_rar_stream)
    check_iter_members(
        sample_archive,
        archive_path=sample_archive_path,
        config=config,
        set_file_password_in_constructor=True,
        skip_member_contents=deps.unrar_version is None,
    )


@pytest.mark.parametrize(
    "sample_archive",
    filter_archives(
        SAMPLE_ARCHIVES,
        extensions=["zip", "7z"],
        custom_filter=lambda x: x.contents.has_password()
        and not x.contents.has_multiple_passwords()
        and x.contents.header_password is None,
    ),
    ids=lambda x: x.filename,
)
def test_read_zip_and_7z_archives_with_password_in_constructor(
    sample_archive: SampleArchive,
    sample_archive_path: str,
):
    check_iter_members(
        sample_archive,
        archive_path=sample_archive_path,
        set_file_password_in_constructor=True,
    )


@pytest.mark.parametrize(
    "sample_archive",
    filter_archives(SAMPLE_ARCHIVES, extensions=["7z"]),
    ids=lambda x: x.filename,
)
def test_read_sevenzip_py7zr_archives(
    sample_archive: SampleArchive, sample_archive_path: str
):
    check_iter_members(sample_archive, archive_path=sample_archive_path)


@pytest.mark.parametrize(
    "sample_archive",
    filter_archives(
        SAMPLE_ARCHIVES, prefixes=["single_file", "single_file_with_metadata"]
    ),
    ids=lambda x: x.filename,
)
@pytest.mark.parametrize("alternative_packages", [False, True])
def test_read_single_file_compressed_archives(
    sample_archive: SampleArchive, sample_archive_path: str, alternative_packages: bool
):
    if alternative_packages:
        config = ArchiveyConfig(
            use_rapidgzip=True,
            use_indexed_bzip2=True,
            use_python_xz=True,
            use_zstandard=True,
            use_single_file_stored_metadata=True,
        )
    else:
        config = ArchiveyConfig(use_single_file_stored_metadata=True)

    check_iter_members(sample_archive, archive_path=sample_archive_path, config=config)


@pytest.mark.parametrize(
    "sample_archive",
    filter_archives(SAMPLE_ARCHIVES, prefixes=["symlinks", "symlinks_solid"]),
    ids=lambda x: x.filename,
)
def test_read_symlinks_archives(
    sample_archive: SampleArchive, sample_archive_path: str
):
    check_iter_members(sample_archive, archive_path=sample_archive_path)


@pytest.mark.parametrize(
    "sample_archive",
    filter_archives(SAMPLE_ARCHIVES, prefixes=["symlink_loop"]),
    ids=lambda x: x.filename,
)
def test_symlink_loop_archives(sample_archive: SampleArchive, sample_archive_path: str):
    """Ensure that archives with symlink loops do not cause infinite loops."""
    with open_archive(sample_archive_path) as archive:
        for member in archive.get_members():
            if member.type == MemberType.SYMLINK:
                if member.link_target == "file5.txt":
                    with archive.open(member) as fh:
                        fh.read()
                else:
                    with pytest.raises(ArchiveMemberCannotBeOpenedError):
                        archive.open(member)
            else:
                with archive.open(member) as fh:
                    fh.read()


@pytest.mark.parametrize(
    "sample_archive",
    filter_archives(
        SAMPLE_ARCHIVES, prefixes=["hardlinks_nonsolid", "hardlinks_solid"]
    ),
    ids=lambda x: x.filename,
)
def test_read_hardlinks_archives(
    sample_archive: SampleArchive, sample_archive_path: str
):
    check_iter_members(sample_archive, archive_path=sample_archive_path)
