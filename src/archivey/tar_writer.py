import io
import os
import tarfile
import time
from typing import IO

from .base_writer import ArchiveWriter
from .types import ArchiveFormat, ArchiveMember, MemberType


_FORMAT_TO_MODE = {
    ArchiveFormat.TAR: "w",
    ArchiveFormat.TAR_GZ: "w:gz",
    ArchiveFormat.TAR_BZ2: "w:bz2",
    ArchiveFormat.TAR_XZ: "w:xz",
}


class _TarFileWriteStream(io.BytesIO):
    def __init__(self, writer: "TarWriter", member: ArchiveMember):
        super().__init__()
        self._writer = writer
        self._member = member

    def close(self) -> None:
        if not self.closed:
            data = self.getvalue()
            info = tarfile.TarInfo(self._member.filename)
            info.size = len(data)
            info.mtime = int(time.time())
            self._writer._tar.addfile(info, io.BytesIO(data))
        super().close()


class TarWriter(ArchiveWriter):
    """Writer for TAR archives."""

    def __init__(self, archive_path: str | os.PathLike, format: ArchiveFormat):
        if format not in _FORMAT_TO_MODE:
            raise NotImplementedError(f"Unsupported TAR format: {format}")
        super().__init__(format, archive_path)
        mode = _FORMAT_TO_MODE[format]
        self._tar = tarfile.open(self.archive_path, mode)

    def close(self) -> None:
        self._tar.close()

    def add_member(self, member: ArchiveMember) -> IO[bytes] | None:
        if member.type == MemberType.FILE:
            return _TarFileWriteStream(self, member)

        info = tarfile.TarInfo(member.filename.rstrip("/") + ("/" if member.type == MemberType.DIR else ""))
        info.mtime = int(time.time())
        if member.type == MemberType.DIR:
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            self._tar.addfile(info)
        elif member.type == MemberType.SYMLINK:
            info.type = tarfile.SYMTYPE
            info.linkname = member.link_target or ""
            info.mode = 0o777
            self._tar.addfile(info)
        else:
            raise NotImplementedError(f"Unsupported member type: {member.type}")
        return None
