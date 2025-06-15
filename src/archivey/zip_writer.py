import os
import stat
import zipfile
from datetime import datetime
from typing import IO

from .base_writer import ArchiveWriter
from .types import ArchiveFormat, ArchiveMember, MemberType


class ZipWriter(ArchiveWriter):
    """Writer for ZIP archives."""

    def __init__(self, archive_path: str | os.PathLike):
        super().__init__(ArchiveFormat.ZIP, archive_path)
        self._zip = zipfile.ZipFile(self.archive_path, "w")

    def close(self) -> None:
        self._zip.close()

    def add_member(self, member: ArchiveMember) -> IO[bytes] | None:
        if member.type == MemberType.FILE:
            return self._zip.open(member.filename, "w")

        filename = member.filename
        if member.type == MemberType.DIR and not filename.endswith("/"):
            filename += "/"

        info = zipfile.ZipInfo(filename)
        if member.mtime is not None:
            info.date_time = member.date_time  # type: ignore[assignment]

        if member.type == MemberType.DIR:
            info.external_attr = (stat.S_IFDIR | 0o755) << 16
            self._zip.writestr(info, b"")
        elif member.type == MemberType.SYMLINK:
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            data = (member.link_target or "").encode("utf-8")
            self._zip.writestr(info, data)
        else:
            raise NotImplementedError(f"Unsupported member type: {member.type}")
        return None
