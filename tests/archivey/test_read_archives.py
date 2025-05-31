import glob
import logging
import os
import pathlib
import zlib
from dataclasses import dataclass
from datetime import datetime
from unittest.mock import patch

import pytest
from sample_archives import (
    MARKER_MTIME_BASED_ON_ARCHIVE_NAME,
    SAMPLE_ARCHIVES,
    ArchiveInfo,
    GenerationMethod,
    filter_archives,
)

from archivey.archive_stream import ArchiveStream
from archivey.exceptions import (
    ArchiveCorruptedError,
    ArchiveEOFError,
    LibraryNotInstalledError,
)
from archivey.types import ArchiveFormat, MemberType


def normalize_newlines(s: str | None) -> str | None:
    return s.replace("\r\n", "\n") if s else None


def full_path_to_archive(archive_filename: str) -> str:
    return os.path.join(os.path.dirname(__file__), "../test_archives", archive_filename)


@dataclass
class ArchiveFormatFeatures:
    dir_entries: bool = True
    file_comments: bool = True


FORMAT_FEATURES = {
    ArchiveFormat.RAR: ArchiveFormatFeatures(dir_entries=False, file_comments=False),
    ArchiveFormat.SEVENZIP: ArchiveFormatFeatures(
        dir_entries=False, file_comments=False
    ),
}

DEFAULT_FORMAT_FEATURES = ArchiveFormatFeatures()


def get_crc32(data: bytes) -> int:
    """
    Compute CRC32 checksum for a file within an archive.
    Returns a hex string.
    """
    crc32_value: int = 0

    # Read the file in chunks
    crc32_value = zlib.crc32(data, crc32_value)
    return crc32_value & 0xFFFFFFFF


def check_read_archive(
    sample_archive: ArchiveInfo,
    use_rar_stream: bool = False,
    set_file_password_in_constructor: bool = False,
):
    # print("STARTING TEST", sample_archive.filename)
    if sample_archive.skip_test:
        pytest.skip(f"Skipping test for {sample_archive.filename} as skip_test is True")

    features = FORMAT_FEATURES.get(
        sample_archive.format_info.format, DEFAULT_FORMAT_FEATURES
    )
    archive_base_dir = os.path.join(os.path.dirname(__file__), "..")

    files_by_name = {file.name: file for file in sample_archive.contents.files}
    allow_timestamp_rounding_error = (
        sample_archive.format_info.generation_method == GenerationMethod.ZIPFILE
    )

    constructor_password = sample_archive.contents.header_password

    if set_file_password_in_constructor and sample_archive.contents.has_password():
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

    with ArchiveStream(
        sample_archive.get_archive_path(archive_base_dir),
        pwd=constructor_password,
        use_rar_stream=use_rar_stream,
        use_single_file_stored_metadata=True,
    ) as archive:
        assert archive.get_format() == sample_archive.format_info.format
        format_info = archive.get_archive_info()
        assert normalize_newlines(format_info.comment) == normalize_newlines(
            sample_archive.contents.archive_comment
        )
        actual_filenames: list[str] = []

        for member in archive.info_iter():
            sample_file = files_by_name.get(member.filename, None)

            if member.is_file and sample_file is not None and member.crc32 is not None:
                sample_crc32 = get_crc32(sample_file.contents or b"")
                assert member.crc32 == sample_crc32, (
                    f"CRC32 mismatch for {member.filename}: got {member.crc32}, expected {sample_crc32}"
                )

            if sample_file is None:
                if member.type == MemberType.DIR:
                    logging.warning(
                        f"Archive {sample_archive.filename} contains unexpected dir {member.filename}"
                    )
                    continue
                else:
                    pytest.fail(
                        f"Archive {sample_archive.filename} contains unexpected file {member.filename}"
                    )

            actual_filenames.append(member.filename)

            if sample_file.compression_method is not None:
                assert member.compression_method == sample_file.compression_method

            if features.file_comments:
                assert member.comment == sample_file.comment
            else:
                assert member.comment is None

            if member.is_file and member.file_size is not None:
                assert member.file_size == len(sample_file.contents or b"")

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
            # elif member.permissions is not None:
            #         # For other formats, if permissions happen to be set by the library
            #         # and we have an expected mode, check it.
            #         assert member.permissions == sample_file.permissions, (
            #             f"Permission mismatch for {member.filename} in {sample_archive.filename} (optional check): "
            #             f"got {oct(member.permissions)}, expected {oct(sample_file.permissions)}"
            #         )

            assert member.encrypted == (
                sample_file.password is not None
                or (
                    member.is_file
                    and sample_archive.contents.header_password is not None
                )
            ), (
                f"Encrypted mismatch for {member.filename}: got {member.encrypted}, expected {sample_file.password is not None}"
            )

            assert member.mtime is not None
            if sample_file.mtime == MARKER_MTIME_BASED_ON_ARCHIVE_NAME:
                archive_file_mtime = datetime.fromtimestamp(
                    os.path.getmtime(sample_archive.get_archive_path(archive_base_dir))
                )
                assert member.mtime == archive_file_mtime, (
                    f"Timestamp mismatch for {member.filename} (special check): "
                    f"member mtime {member.mtime} vs archive mtime {archive_file_mtime}"
                )
            elif allow_timestamp_rounding_error:
                assert (
                    abs(member.mtime.timestamp() - sample_file.mtime.timestamp()) <= 1
                ), (
                    f"Timestamp mismatch for {member.filename}: {member.mtime} != {sample_file.mtime}"
                )
            else:
                assert member.mtime == sample_file.mtime, (
                    f"Timestamp mismatch for {member.filename}: {member.mtime} != {sample_file.mtime}"
                )

            if sample_file.type == MemberType.FILE:
                with archive.open(
                    member,
                    pwd=sample_file.password if constructor_password is None else None,
                ) as f:
                    contents = f.read()
                    assert contents == sample_file.contents

        expected_filenames = set(
            file.name
            for file in sample_archive.contents.files
            if features.dir_entries or file.type != MemberType.DIR
        )

        missing_files = expected_filenames - set(actual_filenames)
        extra_files = set(actual_filenames) - expected_filenames

        assert not missing_files, f"Missing files: {missing_files}"
        assert not extra_files, f"Extra files: {extra_files}"


@pytest.mark.parametrize(
    "sample_archive",
    filter_archives(SAMPLE_ARCHIVES, extensions=["zip"]),
    ids=lambda x: x.filename,
)
def test_read_zip_archives(sample_archive: ArchiveInfo):
    check_read_archive(sample_archive)


CORRUPTED_ARCHIVES_DIR = pathlib.Path(__file__).parent / "../test_corrupted_archives"


def get_corrupted_archives(suffix: str) -> list[str]:
    """Helper to get list of corrupted archive paths for parametrization."""
    if not CORRUPTED_ARCHIVES_DIR.exists():
        return []
    return glob.glob(str(CORRUPTED_ARCHIVES_DIR / suffix))


@pytest.mark.parametrize(
    "archive_path_str",
    get_corrupted_archives("*.truncated"),
    ids=lambda x: pathlib.Path(str(x)).name
    if isinstance(x, (str, pathlib.Path))
    else "invalid_param",
)
def test_read_truncated_archives(archive_path_str: str):
    """Test that reading truncated archives raises ArchiveEOFError."""
    archive_path = pathlib.Path(archive_path_str)
    with pytest.raises(ArchiveEOFError):
        with ArchiveStream(archive_path):
            pass  # Opening is enough to trigger for some formats, iteration for others


@pytest.mark.parametrize(
    "archive_path_str",
    get_corrupted_archives("*.corrupted"),
    ids=lambda x: pathlib.Path(str(x)).name
    if isinstance(x, (str, pathlib.Path))
    else "invalid_param",
)
def test_read_corrupted_archives_general(archive_path_str: str):
    """Test that reading generally corrupted archives raises ArchiveCorruptedError."""
    archive_path = pathlib.Path(archive_path_str)
    with pytest.raises(ArchiveCorruptedError):
        # For many corrupted archives, error might be raised on open or during iteration
        with ArchiveStream(str(archive_path)) as archive:
            for _ in archive.info_iter():
                pass


@pytest.mark.parametrize(
    "sample_archive",
    filter_archives(
        SAMPLE_ARCHIVES,
        extensions=["tar", "tar.gz", "tar.bz2", "tar.xz", "tar.zst", "tar.lz4"],
    ),
    ids=lambda x: x.filename,
)
def test_read_tar_archives(sample_archive: ArchiveInfo):
    check_read_archive(sample_archive)


@pytest.mark.parametrize(
    "sample_archive",
    filter_archives(SAMPLE_ARCHIVES, extensions=["rar"]),
    ids=lambda x: x.filename,
)
@pytest.mark.parametrize("use_rar_stream", [True, False])
def test_read_rar_archives(sample_archive: ArchiveInfo, use_rar_stream: bool):
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
            check_read_archive(sample_archive, use_rar_stream=use_rar_stream)
    else:
        check_read_archive(sample_archive, use_rar_stream=use_rar_stream)


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
    sample_archive: ArchiveInfo, use_rar_stream: bool
):
    check_read_archive(
        sample_archive,
        use_rar_stream=use_rar_stream,
        set_file_password_in_constructor=True,
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
    sample_archive: ArchiveInfo,
):
    check_read_archive(
        sample_archive,
        set_file_password_in_constructor=True,
    )


@pytest.mark.parametrize(
    "sample_archive",
    filter_archives(SAMPLE_ARCHIVES, extensions=["7z"]),
    ids=lambda x: x.filename,
)
def test_read_sevenzip_py7zr_archives(sample_archive: ArchiveInfo):
    check_read_archive(sample_archive)


@pytest.mark.parametrize(
    "sample_archive",
    filter_archives(
        SAMPLE_ARCHIVES, prefixes=["single_file", "single_file_with_metadata"]
    ),
    ids=lambda x: x.filename,
)
def test_read_single_file_compressed_archives(sample_archive: ArchiveInfo):
    check_read_archive(sample_archive)


# Tests for LibraryNotInstalledError
DUMMY_RAR_PATH = full_path_to_archive("dummy.rar")
DUMMY_7Z_PATH = full_path_to_archive("dummy.7z")


@patch('archivey.rar_reader.rarfile', None)
def test_rarfile_not_installed_raises_exception():
    """Test that LibraryNotInstalledError is raised for .rar when rarfile is not installed."""
    with pytest.raises(LibraryNotInstalledError) as excinfo:
        ArchiveStream(DUMMY_RAR_PATH)
    assert str(excinfo.value) == "rarfile library is not installed. Please install it to work with RAR archives."


@patch('archivey.sevenzip_reader.py7zr', None)
def test_py7zr_not_installed_raises_exception():
    """Test that LibraryNotInstalledError is raised for .7z when py7zr is not installed."""
    with pytest.raises(LibraryNotInstalledError) as excinfo:
        ArchiveStream(DUMMY_7Z_PATH)
    assert str(excinfo.value) == "py7zr library is not installed. Please install it to work with 7-Zip archives."
