import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from enum import StrEnum
elif sys.version_info >= (3, 11):
    from enum import StrEnum
else:
    from backports.strenum import StrEnum

from dataclasses import dataclass, field
import dataclasses
from datetime import datetime
from enum import IntEnum
from typing import Any, Optional, Tuple


class ArchiveFormat(StrEnum):
    """Supported compression formats."""

    ZIP = "zip"
    RAR = "rar"
    SEVENZIP = "7z"

    GZIP = "gz"
    BZIP2 = "bz2"
    XZ = "xz"
    ZSTD = "zstd"
    LZ4 = "lz4"

    TAR = "tar"
    TAR_GZ = "tar.gz"
    TAR_BZ2 = "tar.bz2"
    TAR_XZ = "tar.xz"
    TAR_ZSTD = "tar.zstd"
    TAR_LZ4 = "tar.lz4"

    ISO = "iso"
    FOLDER = "folder"

    UNKNOWN = "unknown"


SINGLE_FILE_COMPRESSED_FORMATS = [
    ArchiveFormat.GZIP,
    ArchiveFormat.BZIP2,
    ArchiveFormat.XZ,
    ArchiveFormat.ZSTD,
    ArchiveFormat.LZ4,
]
TAR_COMPRESSED_FORMATS = [
    ArchiveFormat.TAR_GZ,
    ArchiveFormat.TAR_BZ2,
    ArchiveFormat.TAR_XZ,
    ArchiveFormat.TAR_ZSTD,
    ArchiveFormat.TAR_LZ4,
]

COMPRESSION_FORMAT_TO_TAR_FORMAT = {
    ArchiveFormat.GZIP: ArchiveFormat.TAR_GZ,
    ArchiveFormat.BZIP2: ArchiveFormat.TAR_BZ2,
    ArchiveFormat.XZ: ArchiveFormat.TAR_XZ,
    ArchiveFormat.ZSTD: ArchiveFormat.TAR_ZSTD,
    ArchiveFormat.LZ4: ArchiveFormat.TAR_LZ4,
}

TAR_FORMAT_TO_COMPRESSION_FORMAT = {
    v: k for k, v in COMPRESSION_FORMAT_TO_TAR_FORMAT.items()
}


class MemberType(StrEnum):
    FILE = "file"
    DIR = "dir"
    SYMLINK = "symlink"
    HARDLINK = "hardlink"
    OTHER = "other"


class CreateSystem(IntEnum):
    """Operating system on which the archive member was created."""

    FAT = 0
    AMIGA = 1
    VMS = 2
    UNIX = 3
    VM_CMS = 4
    ATARI_ST = 5
    OS2_HPFS = 6
    MACINTOSH = 7
    Z_SYSTEM = 8
    CPM = 9
    TOPS20 = 10
    NTFS = 11
    QDOS = 12
    ACORN_RISCOS = 13
    UNKNOWN = 255


@dataclass
class ArchiveInfo:
    """Detailed information about an archive's format."""

    format: str  # Will be ArchiveFormat from formats.py
    version: Optional[str] = None  # e.g. "4" for RAR4, "5" for RAR5
    is_solid: bool = False
    extra: Optional[dict[str, Any]] = None
    comment: Optional[str] = None


@dataclass
class ArchiveMember:
    """Represents a file within an archive."""

    filename: str
    file_size: Optional[int]
    compress_size: Optional[int]
    mtime: Optional[datetime]
    type: MemberType

    mode: Optional[int] = None
    crc32: Optional[int] = None
    compression_method: Optional[str] = None  # e.g. "deflate", "lzma", etc.
    comment: Optional[str] = None
    create_system: Optional[CreateSystem] = None
    encrypted: bool = False
    extra: dict[str, Any] = field(default_factory=dict)
    link_target: Optional[str] = None
    link_target_type: Optional[MemberType] = None
    link_target_member: Optional["ArchiveMember"] = None

    # The raw info from the archive reader
    raw_info: Optional[Any] = None

    # A unique identifier for this member within the archive. Used to distinguish members
    # and preserve ordering, but not for direct indexing. Assigned by register_member().
    _member_id: Optional[int] = None

    @property
    def member_id(self) -> int:
        if self._member_id is None:
            raise ValueError("Member index not yet set")
        return self._member_id

    # A unique identifier for the archive. Used to distinguish between archives.
    # Filled by register_member().
    _archive_id: Optional[int] = None

    @property
    def archive_id(self) -> int:
        if self._archive_id is None:
            raise ValueError("Archive ID not yet set")
        return self._archive_id

    # Properties for zipfile compatibility (and others, as much as possible)
    @property
    def date_time(self) -> Optional[Tuple[int, int, int, int, int, int]]:
        """Returns the date and time as a tuple."""
        if self.mtime is None:
            return None
        return (
            self.mtime.year,
            self.mtime.month,
            self.mtime.day,
            self.mtime.hour,
            self.mtime.minute,
            self.mtime.second,
        )

    @property
    def is_file(self) -> bool:
        return self.type == MemberType.FILE

    @property
    def is_dir(self) -> bool:
        return self.type == MemberType.DIR

    @property
    def is_link(self) -> bool:
        return self.type == MemberType.SYMLINK or self.type == MemberType.HARDLINK

    @property
    def is_other(self) -> bool:
        return self.type == MemberType.OTHER

    @property
    def CRC(self) -> Optional[int]:
        return self.crc32

    def replace(self, **kwargs: Any) -> "ArchiveMember":
        """Return a new :class:`ArchiveMember` with updated fields."""
        return dataclasses.replace(self, **kwargs)
