import io
import logging
import os
import struct
from datetime import datetime, timezone
from typing import BinaryIO, List

from archivey.base_reader import BaseArchiveReaderRandomAccess
from archivey.compressed_streams import open_stream
from archivey.exceptions import (
    ArchiveFormatError,
)
from archivey.types import (
    SINGLE_FILE_COMPRESSED_FORMATS,
    ArchiveFormat,
    ArchiveInfo,
    ArchiveMember,
    CreateSystem,
    MemberType,
)

logger = logging.getLogger(__name__)


def _read_null_terminated_bytes(f: io.BufferedReader) -> bytes:
    str_bytes = bytearray()
    while True:
        b = f.read(1)
        if not b or b == b"\x00":
            break
        str_bytes.extend(b)
    return bytes(str_bytes)


def read_gzip_metadata(
    path: str, member: ArchiveMember, use_stored_metadata: bool = False
):
    """
    Extract metadata from a .gz file without decompressing and update the ArchiveMember:
    - original internal filename (if present) goes into extra field
    - modification time (as POSIX timestamp)
    - CRC32 of uncompressed data
    - uncompressed size (modulo 2^32)
    - compression method
    - compression level
    - operating system
    - extra field data
    """

    extra_fields = {}

    with open(path, "rb") as f:
        # Read the fixed 10-byte GZIP header
        header = f.read(10)
        if len(header) != 10 or header[:2] != b"\x1f\x8b":
            raise ArchiveFormatError("Not a valid GZIP file")

        # Parse header fields
        id1, id2, cm, flg, mtime_timestamp, xfl, os = struct.unpack("<4BIBB", header)

        if mtime_timestamp != 0:
            extra_fields["mtime"] = datetime.fromtimestamp(
                mtime_timestamp, tz=timezone.utc
            ).replace(tzinfo=None)
            if use_stored_metadata:
                member.mtime = extra_fields["mtime"]

        # Add compression method and level
        extra_fields["compress_type"] = cm  # 8 = deflate, consistent with ZIP
        extra_fields["compress_level"] = xfl  # Compression level (0-9)

        member.create_system = (
            CreateSystem(os)
            if os in CreateSystem._value2member_map_
            else CreateSystem.UNKNOWN
        )

        # Handle optional fields
        if flg & 0x04:  # FEXTRA
            # The extra field contains a 2-byte length and then the data
            xlen = struct.unpack("<H", f.read(2))[0]
            extra_fields["extra"] = f.read(xlen)  # Store raw extra field data

        if flg & 0x08:  # FNAME
            # The filename is a null-terminated string
            name_bytes = _read_null_terminated_bytes(f)
            extra_fields["original_filename"] = name_bytes.decode(
                "utf-8", errors="replace"
            )
            if use_stored_metadata:
                member.filename = extra_fields["original_filename"]

        if flg & 0x10:  # FCOMMENT
            comment_bytes = _read_null_terminated_bytes(f)
            extra_fields["comment"] = comment_bytes.decode("utf-8", errors="replace")

        if flg & 0x02:  # FHCRC
            f.read(2)  # Skip CRC16

        if extra_fields:
            if member.extra is None:
                member.extra = {}
            member.extra.update(extra_fields)

        # Now seek to trailer and read CRC32 and ISIZE
        f.seek(-8, 2)
        crc32, isize = struct.unpack("<II", f.read(8))
        member.crc32 = crc32
        member.file_size = isize


def _read_xz_multibyte_integer(data: bytes, offset: int) -> tuple[int, int]:
    """
    Read a multi-byte integer from the data at the given offset.
    """
    value = 0
    shift = 0
    while True:
        b = data[offset]
        offset += 1
        value |= (b & 0x7F) << shift
        if b & 0x80 == 0:
            break
        shift += 7

    return value, offset


XZ_MAGIC_FOOTER = b"YZ"
XZ_STREAM_HEADER_MAGIC = b"\xfd7zXZ\x00"


def read_xz_metadata(path: str, member: ArchiveMember):
    with open(path, "rb") as f:
        f.seek(-12, 2)  # Footer is always 12 bytes
        footer = f.read(12)

        if footer[-2:] != XZ_MAGIC_FOOTER:
            logger.warning(f"Invalid XZ footer, file possibly truncated: {path}")
            return

        # Backward Size (first 4 bytes) tells how far back the Index is, in 4-byte units minus 1
        backward_size_field = struct.unpack("<I", footer[4:8])[0]
        index_size = (backward_size_field + 1) * 4

        f.seek(-12 - index_size, 2)
        index_data = f.read(index_size)

        # Skip index indicator byte and reserved bits (first byte)
        if index_data[0] != 0x00:
            logger.warning(f"Invalid XZ footer, file possibly corrupted: {path}")
            return

        # Next 2–10 bytes are variable-length field counts and sizes
        # We just want the uncompressed size (encoded as a multi-byte integer)

        # Decode the first count (number of records)
        blocks = []
        total_uncompressed_size = 0

        offset = 1
        number_of_blocks, offset = _read_xz_multibyte_integer(index_data, offset)

        for _ in range(number_of_blocks):
            count, offset = _read_xz_multibyte_integer(index_data, offset)
            uncompressed_size, offset = _read_xz_multibyte_integer(index_data, offset)
            blocks.append((uncompressed_size, offset))
            total_uncompressed_size += uncompressed_size

        member.file_size = total_uncompressed_size


class SingleFileReader(BaseArchiveReaderRandomAccess):
    """Reader for raw compressed files (gz, bz2, xz, zstd, lz4)."""

    def __init__(
        self,
        archive_path: str,
        format: ArchiveFormat,
        *,
        pwd: bytes | str | None = None,
        **kwargs,
    ):
        """Initialize the reader.

        Args:
            archive_path: Path to the compressed file
            pwd: Password for decryption (not supported for compressed files)
            format: The format of the archive. If None, will be detected from the file extension.
            **kwargs: Additional options (ignored)
        """
        super().__init__(format, archive_path)
        if pwd is not None:
            raise ValueError("Compressed files do not support password protection")

        if format not in SINGLE_FILE_COMPRESSED_FORMATS:
            raise ValueError(f"Unsupported archive format: {format}")

        self.archive_path = archive_path
        self.ext = os.path.splitext(archive_path)[1].lower()
        self.use_stored_metadata = self.config.use_single_file_stored_metadata

        # Get the base name without compression extension
        # TODO: what if the file has no extension, or a wrong extension?
        # maybe we should remove only extensions from known formats, and add an extra
        # extension if it doesn't have one?
        self.member_name = (
            os.path.splitext(os.path.basename(archive_path))[0] or "unknown"
        )

        # Get file metadata
        mtime = datetime.fromtimestamp(os.path.getmtime(archive_path))

        # Create a single member representing the decompressed file
        self.member = ArchiveMember(
            filename=self.member_name,
            file_size=None,  # Not available for all formats
            compress_size=os.path.getsize(archive_path),
            mtime=mtime,
            type=MemberType.FILE,
            compression_method=self.format.value,
            crc32=None,
        )

        if self.format == ArchiveFormat.GZIP:
            read_gzip_metadata(archive_path, self.member, self.use_stored_metadata)
        elif self.format == ArchiveFormat.XZ:
            read_xz_metadata(archive_path, self.member)

        self.register_member(self.member)

        # Open the file to see if it's supported by the library and valid.
        # To avoid opening the file twice, we'll store the reference and return it
        # on the first open() call.
        self.fileobj = open_stream(self.format, self.archive_path, self.config)

    def close(self) -> None:
        """Close the archive and release any resources."""
        if self.fileobj is not None:
            self.fileobj.close()
            self.fileobj = None

    def get_members(self) -> List[ArchiveMember]:
        """Get a list of all members in the archive."""
        return [self.member]

    def get_archive_info(self) -> ArchiveInfo:
        """Get detailed information about the archive's format."""
        return ArchiveInfo(
            format=self.format.value,
            is_solid=False,
            extra=None,
        )

    def open(
        self, member_or_filename: ArchiveMember | str, *, pwd: bytes | None = None
    ) -> BinaryIO:
        if pwd is not None:
            raise ValueError("Compressed files do not support password protection")

        member, filename = self._resolve_member_to_open(member_or_filename)

        if self.fileobj is None:
            return open_stream(self.format, self.archive_path, self.config)

        else:
            # If there's an open file already, return it, but set the class field
            # to None so that further open() calls will open new streams.
            fileobj = self.fileobj
            self.fileobj = None
            return fileobj
