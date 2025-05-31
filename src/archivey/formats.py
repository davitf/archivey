import io
import logging
import os

from archivey.types import (
    COMPRESSION_FORMAT_TO_TAR_FORMAT,
    SINGLE_FILE_COMPRESSED_FORMATS,
    TAR_COMPRESSED_FORMATS,
    ArchiveFormat,
)


def detect_archive_format_by_signature(
    path_or_file: str | bytes | io.IOBase,
) -> ArchiveFormat:
    SIGNATURES = [
        (b"\x50\x4b\x03\x04", ArchiveFormat.ZIP),
        (b"\x52\x61\x72\x21\x1a\x07\x00", ArchiveFormat.RAR),  # RAR4
        (b"\x52\x61\x72\x21\x1a\x07\x01\x00", ArchiveFormat.RAR),  # RAR5
        (b"\x37\x7a\xbc\xaf\x27\x1c", ArchiveFormat.SEVENZIP),
        (b"\x1f\x8b", ArchiveFormat.GZIP),
        (b"\x42\x5a\x68", ArchiveFormat.BZIP2),
        (b"\xfd\x37\x7a\x58\x5a\x00", ArchiveFormat.XZ),
        (b"\x28\xb5\x2f\xfd", ArchiveFormat.ZSTD),
        (b"\x04\x22\x4d\x18", ArchiveFormat.LZ4),
    ]

    # Support both file paths and file-like objects
    close_after = False
    if isinstance(path_or_file, (str, bytes)):
        f = open(path_or_file, "rb")
        close_after = True
    else:
        f = path_or_file
        f.seek(0)

    sig = f.read(8)
    if close_after:
        f.close()

    for magic, name in SIGNATURES:
        if sig.startswith(magic):
            return name

    # Check for tar "ustar" magic at offset 257
    if isinstance(path_or_file, (str, bytes)):
        with open(path_or_file, "rb") as tf:
            tf.seek(257)
            if tf.read(5) == b"ustar":
                return ArchiveFormat.TAR
    else:
        pos = f.tell()
        f.seek(257)
        if f.read(5) == b"ustar":
            return ArchiveFormat.TAR
        f.seek(pos)

    return ArchiveFormat.UNKNOWN


_EXTENSION_TO_FORMAT = {
    ".tar": ArchiveFormat.TAR,
    ".tar.gz": ArchiveFormat.TAR_GZ,
    ".tar.bz2": ArchiveFormat.TAR_BZ2,
    ".tar.xz": ArchiveFormat.TAR_XZ,
    ".tar.zst": ArchiveFormat.TAR_ZSTD,
    ".tar.lz4": ArchiveFormat.TAR_LZ4,
    ".tgz": ArchiveFormat.TAR_GZ,
    ".tbz2": ArchiveFormat.TAR_BZ2,
    ".txz": ArchiveFormat.TAR_XZ,
    ".tzst": ArchiveFormat.TAR_ZSTD,
    ".tlz4": ArchiveFormat.TAR_LZ4,
    ".gz": ArchiveFormat.GZIP,
    ".bz2": ArchiveFormat.BZIP2,
    ".xz": ArchiveFormat.XZ,
    ".zst": ArchiveFormat.ZSTD,
    ".lz4": ArchiveFormat.LZ4,
    ".zip": ArchiveFormat.ZIP,
    ".rar": ArchiveFormat.RAR,
    ".7z": ArchiveFormat.SEVENZIP,
}


def has_tar_extension(filename: str) -> bool:
    base_filename, ext = os.path.splitext(filename.lower())
    return _EXTENSION_TO_FORMAT.get(
        ext
    ) in TAR_COMPRESSED_FORMATS or base_filename.endswith(".tar")


def detect_archive_format_by_filename(filename: str) -> ArchiveFormat:
    """Detect the compression format of an archive based on its filename."""
    filename_lower = filename.lower()
    for ext, format in _EXTENSION_TO_FORMAT.items():
        if filename_lower.endswith(ext):
            return format
    return ArchiveFormat.UNKNOWN


logger = logging.getLogger(__name__)


def detect_archive_format(filename: str) -> ArchiveFormat:
    format_by_signature = detect_archive_format_by_signature(filename)
    format_by_filename = detect_archive_format_by_filename(filename)

    # To detect cases like a .tar.gz file mistakenly having been renamed to .zip,
    # assume it's a tar file if the compression format is supported by tar
    # and the filename matches any multi-file format.
    if (
        format_by_signature in COMPRESSION_FORMAT_TO_TAR_FORMAT
        and format_by_filename not in SINGLE_FILE_COMPRESSED_FORMATS
    ):
        format = COMPRESSION_FORMAT_TO_TAR_FORMAT[format_by_signature]
    effective_format: ArchiveFormat

    if format_by_signature == ArchiveFormat.UNKNOWN:
        effective_format = format_by_filename
    elif format_by_signature in COMPRESSION_FORMAT_TO_TAR_FORMAT and \
         format_by_filename == COMPRESSION_FORMAT_TO_TAR_FORMAT[format_by_signature]:
        # e.g. signature is GZIP, filename is TAR_GZ. Prefer TAR_GZ.
        effective_format = format_by_filename
    else:
        effective_format = format_by_signature

    if effective_format != format_by_filename and \
       format_by_signature != ArchiveFormat.UNKNOWN and \
       format_by_filename != ArchiveFormat.UNKNOWN and \
       effective_format != ArchiveFormat.UNKNOWN:
        # Log only if there's a meaningful discrepancy where both detections yielded a result,
        # and they differed, and the effective_format isn't simply a fallback to UNKNOWN.
        logger.warning(
            f"{filename}: Format by signature ({format_by_signature}) and "
            f"format by filename ({format_by_filename}) differ. Effective format: {effective_format}"
        )

    return effective_format
