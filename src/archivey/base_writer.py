import abc
import os
from datetime import datetime
from typing import IO

from .types import ArchiveFormat, ArchiveMember, MemberType


class ArchiveWriter(abc.ABC):
    """Base class for archive writers."""

    def __init__(self, format: ArchiveFormat, archive_path: str | os.PathLike):
        self.format = format
        self.archive_path = str(archive_path)

    def open(self, filename: str, *, mtime: datetime | None = None) -> IO[bytes]:
        member = ArchiveMember(
            filename=filename,
            file_size=None,
            compress_size=None,
            mtime=mtime,
            type=MemberType.FILE,
        )
        stream = self.add_member(member)
        if stream is None:
            raise TypeError("add_member() returned None for file member")
        return stream

    def add(
        self,
        filename: str,
        member_type: MemberType,
        *,
        link_target: str | None = None,
        mtime: datetime | None = None,
    ) -> None:
        if member_type == MemberType.FILE:
            raise ValueError("Use open() to add file members")
        member = ArchiveMember(
            filename=filename,
            file_size=None,
            compress_size=None,
            mtime=mtime,
            type=member_type,
            link_target=link_target,
        )
        self.add_member(member)

    @abc.abstractmethod
    def add_member(self, member: ArchiveMember) -> IO[bytes] | None:
        """Add a member to the archive.

        Returns a writable stream for ``MemberType.FILE`` entries or ``None`` for
        other member types.
        """

    @abc.abstractmethod
    def close(self) -> None:
        pass

    def __enter__(self) -> "ArchiveWriter":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
