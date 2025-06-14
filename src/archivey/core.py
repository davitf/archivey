import os
from typing import Any

from archivey.base_reader import ArchiveReader, StreamingOnlyArchiveReaderWrapper
from archivey.config import ArchiveyConfig, default_config, get_default_config
from archivey.exceptions import ArchiveNotSupportedError
from archivey.folder_reader import FolderReader
from archivey.formats import detect_archive_format
from archivey.types import (
    SINGLE_FILE_COMPRESSED_FORMATS,
    TAR_COMPRESSED_FORMATS,
    ArchiveFormat,
)

# from archivey.iso_reader import IsoReader


def _normalize_archive_path(archive_path: str | bytes | os.PathLike) -> str:
    if isinstance(archive_path, os.PathLike):
        return str(archive_path)
    elif isinstance(archive_path, bytes):
        return archive_path.decode("utf-8")
    elif isinstance(archive_path, str):
        return archive_path

    raise TypeError(f"Invalid archive path type: {type(archive_path)} {archive_path}")


def open_archive(
    archive_path: str | bytes | os.PathLike,
    *,
    config: ArchiveyConfig | None = None,
    streaming_only: bool = False,
    **kwargs: Any,
) -> ArchiveReader:
    """Open an archive and return the appropriate reader."""
    archive_path = _normalize_archive_path(archive_path)

    if not os.path.exists(archive_path):
        raise FileNotFoundError(f"Archive file not found: {archive_path}")

    format = detect_archive_format(_normalize_archive_path(archive_path))

    pwd = kwargs.get("pwd")
    if pwd is not None and not isinstance(pwd, (str, bytes)):
        raise TypeError("Password must be a string or bytes")

    if config is None:
        config = get_default_config()

    with default_config(config):
        use_libarchive = config.use_libarchive
        use_rar_stream = config.use_rar_stream

        reader: ArchiveReader

        if use_libarchive:
            raise ArchiveNotSupportedError("LibArchiveReader is not implemented")

        if format == ArchiveFormat.RAR:
            if use_rar_stream:
                from archivey.rar_reader import RarStreamReader

                reader = RarStreamReader(archive_path, pwd=pwd)
            else:
                from archivey.rar_reader import RarReader

                reader = RarReader(archive_path, pwd=pwd)

        elif format == ArchiveFormat.ZIP:
            from archivey.zip_reader import ZipReader

            reader = ZipReader(archive_path, pwd=pwd)

        elif format == ArchiveFormat.SEVENZIP:
            from archivey.sevenzip_reader import SevenZipReader

            reader = SevenZipReader(
                archive_path, pwd=pwd, streaming_only=streaming_only
            )

        elif format == ArchiveFormat.TAR or format in TAR_COMPRESSED_FORMATS:
            from archivey.tar_reader import TarReader

            reader = TarReader(
                archive_path, pwd=pwd, format=format, streaming_only=streaming_only
            )

        elif format in SINGLE_FILE_COMPRESSED_FORMATS:
            from archivey.single_file_reader import SingleFileReader

            reader = SingleFileReader(
                archive_path,
                pwd=pwd,
                format=format,
            )

        elif format == ArchiveFormat.ISO:
            raise ArchiveNotSupportedError("ISO reader is not yet implemented")
            # reader = IsoReader(archive_path, password=pwd)

        elif format == ArchiveFormat.FOLDER:
            reader = FolderReader(archive_path)

        else:
            raise ArchiveNotSupportedError(f"Unsupported archive format: {format}")

        if streaming_only:
            return StreamingOnlyArchiveReaderWrapper(reader)

        return reader
