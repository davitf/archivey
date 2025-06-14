import bz2
import gzip
import lzma
from os import PathLike
from typing import TYPE_CHECKING, BinaryIO, Optional

from archivey.config import ArchiveyConfig
from archivey.types import ArchiveFormat

if TYPE_CHECKING:
    import indexed_bzip2
    import lz4.frame
    import pyzstd
    import rapidgzip
    import xz
    import zstandard
else:
    try:
        import lz4.frame
    except ImportError:
        lz4 = None

    try:
        import zstandard
    except ImportError:
        zstandard = None

    try:
        import pyzstd
    except ImportError:
        pyzstd = None

    try:
        import rapidgzip
    except ImportError:
        rapidgzip = None

    try:
        import indexed_bzip2
    except ImportError:
        indexed_bzip2 = None

    try:
        import xz
    except ImportError:
        xz = None


from archivey.exceptions import (
    ArchiveCorruptedError,
    ArchiveEOFError,
    ArchiveError,
    PackageNotInstalledError,
)
from archivey.io_helpers import ExceptionTranslatingIO


def _translate_gzip_exception(e: Exception) -> Optional[ArchiveError]:
    if isinstance(e, gzip.BadGzipFile):
        return ArchiveCorruptedError(f"Error reading GZIP archive: {repr(e)}")
    elif isinstance(e, EOFError):
        return ArchiveEOFError(f"GZIP file is truncated: {repr(e)}")
    return None  # pragma: no cover -- all possible exceptions should have been handled


def open_gzip_stream(path: str) -> BinaryIO:
    return ExceptionTranslatingIO(gzip.open(path, mode="rb"), _translate_gzip_exception)


def _translate_rapidgzip_exception(e: Exception) -> Optional[ArchiveError]:
    exc_text = str(e)
    if isinstance(e, RuntimeError) and "IsalInflateWrapper" in exc_text:
        return ArchiveCorruptedError(f"Error reading RapidGZIP archive: {repr(e)}")
    elif isinstance(e, ValueError) and "Mismatching CRC32" in exc_text:
        return ArchiveCorruptedError(f"Error reading RapidGZIP archive: {repr(e)}")
    elif isinstance(e, ValueError) and "Failed to detect a valid file format" in str(e):
        # If we have opened a gzip stream, the magic bytes are there. So if the library
        # fails to detect a valid format, it's because the file is truncated.
        return ArchiveEOFError(f"Possibly truncated GZIP stream: {repr(e)}")

    # Found in rapidgzip 0.11.0
    elif (
        isinstance(e, ValueError)
        and "End of file encountered when trying to read zero-terminated string"
        in exc_text
    ):
        return ArchiveEOFError(f"Possibly truncated GZIP stream: {repr(e)}")
    return None  # pragma: no cover -- all possible exceptions should have been handled


def open_rapidgzip_stream(path: str) -> BinaryIO:
    return ExceptionTranslatingIO(
        rapidgzip.open(path, parallelization=0), _translate_rapidgzip_exception
    )


def _translate_bz2_exception(e: Exception) -> Optional[ArchiveError]:
    exc_text = str(e)
    if isinstance(e, OSError) and "Invalid data stream" in exc_text:
        return ArchiveCorruptedError(f"BZ2 file is corrupted: {repr(e)}")
    elif isinstance(e, EOFError):
        return ArchiveEOFError(f"BZ2 file is truncated: {repr(e)}")
    # elif isinstance(e, ValueError):
    #     return ArchiveFormatError("No valid BZ2 stream found")
    return None  # pragma: no cover -- all possible exceptions should have been handled


def open_bzip2_stream(path: str) -> BinaryIO:
    return ExceptionTranslatingIO(bz2.open(path), _translate_bz2_exception)


def _translate_indexed_bzip2_exception(e: Exception) -> Optional[ArchiveError]:
    exc_text = str(e)
    if isinstance(e, RuntimeError) and "Calculated CRC" in exc_text:
        return ArchiveCorruptedError(f"Error reading Indexed BZIP2 archive: {repr(e)}")
    elif isinstance(e, RuntimeError) and exc_text == "std::exception":
        return ArchiveCorruptedError(f"Error reading Indexed BZIP2 archive: {repr(e)}")
    elif isinstance(e, ValueError) and "[BZip2 block data]" in exc_text:
        return ArchiveCorruptedError(f"Error reading Indexed BZIP2 archive: {repr(e)}")
    return None  # pragma: no cover -- all possible exceptions should have been handled


def open_indexed_bzip2_stream(path: str) -> BinaryIO:
    return ExceptionTranslatingIO(
        indexed_bzip2.open(path, parallelization=0), _translate_indexed_bzip2_exception
    )


def _translate_lzma_exception(e: Exception) -> Optional[ArchiveError]:
    if isinstance(e, lzma.LZMAError):
        return ArchiveCorruptedError(f"Error reading LZMA archive: {repr(e)}")
    elif isinstance(e, EOFError):
        return ArchiveEOFError(f"LZMA file is truncated: {repr(e)}")
    return None  # pragma: no cover -- all possible exceptions should have been handled


def open_lzma_stream(path: str) -> BinaryIO:
    return ExceptionTranslatingIO(lzma.open(path), _translate_lzma_exception)


def _translate_python_xz_exception(e: Exception) -> Optional[ArchiveError]:
    if isinstance(e, xz.XZError):
        return ArchiveCorruptedError(f"Error reading XZ archive: {repr(e)}")
    return None  # pragma: no cover -- all possible exceptions should have been handled


def open_python_xz_stream(path: str) -> BinaryIO:
    if xz is None:
        raise PackageNotInstalledError(
            "python-xz package is not installed, required for XZ archives"
        ) from None  # pragma: no cover -- lz4 is installed for main tests

    return ExceptionTranslatingIO(lambda: xz.open(path), _translate_python_xz_exception)


def _translate_zstandard_exception(e: Exception) -> Optional[ArchiveError]:
    if isinstance(e, zstandard.ZstdError):
        return ArchiveCorruptedError(f"Error reading Zstandard archive: {repr(e)}")
    return None  # pragma: no cover -- all possible exceptions should have been handled


def open_zstandard_stream(path: str) -> BinaryIO:
    if zstandard is None:
        raise PackageNotInstalledError(
            "zstandard package is not installed, required for Zstandard archives"
        ) from None  # pragma: no cover -- lz4 is installed for main tests
    return ExceptionTranslatingIO(zstandard.open(path), _translate_zstandard_exception)


def _translate_pyzstd_exception(e: Exception) -> Optional[ArchiveError]:
    if isinstance(e, pyzstd.ZstdError):
        return ArchiveCorruptedError(f"Error reading Zstandard archive: {repr(e)}")
    elif isinstance(e, EOFError):
        return ArchiveEOFError(f"Zstandard file is truncated: {repr(e)}")
    return None  # pragma: no cover -- all possible exceptions should have been handled


def open_pyzstd_stream(path: str) -> BinaryIO:
    if pyzstd is None:
        raise PackageNotInstalledError(
            "pyzstd package is not installed, required for Zstandard archives"
        ) from None  # pragma: no cover -- pyzstd is installed for main tests
    return ExceptionTranslatingIO(pyzstd.open(path), _translate_pyzstd_exception)


def _translate_lz4_exception(e: Exception) -> Optional[ArchiveError]:
    if isinstance(e, RuntimeError) and str(e).startswith("LZ4"):
        return ArchiveCorruptedError(f"Error reading LZ4 archive: {repr(e)}")
    elif isinstance(e, EOFError):
        return ArchiveEOFError(f"LZ4 file is truncated: {repr(e)}")
    return None  # pragma: no cover -- all possible exceptions should have been handled


def open_lz4_stream(path: str) -> BinaryIO:
    if lz4 is None:
        raise PackageNotInstalledError(
            "lz4 package is not installed, required for LZ4 archives"
        ) from None  # pragma: no cover -- lz4 is installed for main tests

    return ExceptionTranslatingIO(lz4.frame.open(path), _translate_lz4_exception)


def open_stream(
    format: ArchiveFormat, path: str | PathLike, config: ArchiveyConfig
) -> BinaryIO:
    path = str(path)
    if format == ArchiveFormat.GZIP:
        if config.use_rapidgzip:
            return open_rapidgzip_stream(path)
        else:
            return open_gzip_stream(path)

    elif format == ArchiveFormat.BZIP2:
        if config.use_indexed_bzip2:
            return open_indexed_bzip2_stream(path)
        else:
            return open_bzip2_stream(path)

    elif format == ArchiveFormat.XZ:
        if config.use_python_xz:
            return open_python_xz_stream(path)
        else:
            return open_lzma_stream(path)

    elif format == ArchiveFormat.LZ4:
        return open_lz4_stream(path)

    elif format == ArchiveFormat.ZSTD:
        if config.use_zstandard:
            return open_zstandard_stream(path)
        else:
            return open_pyzstd_stream(path)

    raise ValueError(f"Unsupported archive format: {format}")  # pragma: no cover
