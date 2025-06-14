import logging
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Iterator, List, Optional

from archivey.base_reader import BaseArchiveReaderRandomAccess
from archivey.exceptions import ArchiveError, ArchiveIOError, ArchiveMemberNotFoundError
from archivey.io_helpers import ErrorIOStream, LazyOpenIO
from archivey.types import ArchiveFormat, ArchiveInfo, ArchiveMember, MemberType

logger = logging.getLogger(__name__)


class FolderReader(BaseArchiveReaderRandomAccess):
    """
    Reads a folder on the filesystem as an archive.
    """

    format = ArchiveFormat.FOLDER
    magic = None  # Not applicable for folders
    magic_offset = -1

    def __init__(
        self,
        archive_path: str | bytes | os.PathLike,
    ):
        super().__init__(ArchiveFormat.FOLDER, archive_path)
        self.path = Path(self.archive_path).resolve()  # Store absolute path

        if not self.path.is_dir():
            raise ValueError(f"Path is not a directory: {self.path}")

    def _get_member_type(self, lstat_result: os.stat_result) -> MemberType:
        """Determines the MemberType from a path and its lstat result."""
        if stat.S_ISDIR(lstat_result.st_mode):
            return MemberType.DIR
        elif stat.S_ISLNK(lstat_result.st_mode):
            return MemberType.SYMLINK
        elif stat.S_ISREG(lstat_result.st_mode):
            return MemberType.FILE
        return MemberType.OTHER

    def _convert_entry_to_member(self, entry_path: Path) -> ArchiveMember:
        """Converts a filesystem path to an ArchiveMember."""
        filename = str(entry_path.relative_to(self.path)).replace(os.sep, "/")

        try:
            # Use lstat to get info about the link itself, not the target
            stat_result = entry_path.lstat()

        except OSError as e:
            # Could be a broken symlink or permission error
            # Create a placeholder member
            return ArchiveMember(
                filename=filename,
                file_size=0,
                compress_size=0,
                mtime=None,
                type=MemberType.OTHER,  # Or be more specific if possible from error
                comment=f"Error reading entry: {e}",
                raw_info=e,
            )

        member_type = self._get_member_type(stat_result)

        link_target: Optional[str] = None
        if member_type == MemberType.SYMLINK:
            try:
                link_target = os.readlink(entry_path)
            except OSError:
                link_target = "Error reading link target"

        return ArchiveMember(
            filename=filename,
            file_size=stat_result.st_size,
            compress_size=stat_result.st_size,  # No compression for folders
            mtime=datetime.fromtimestamp(stat_result.st_mtime, tz=timezone.utc).replace(
                tzinfo=None
            ),
            type=member_type,
            mode=stat_result.st_mode,
            link_target=link_target,
        )

    def _iter_member_infos(self) -> Iterator[ArchiveMember]:
        for root, dirnames, filenames in os.walk(
            self.path, topdown=True, followlinks=False
        ):
            dirpath = Path(root)
            for dirname in dirnames:
                yield self._convert_entry_to_member(dirpath / dirname)
            for filename in filenames:
                yield self._convert_entry_to_member(dirpath / filename)

    def iter_members_with_io(
        self, *, pwd: bytes | str | None = None
    ) -> Iterator[tuple[ArchiveMember, BinaryIO | None]]:
        if pwd is not None:
            raise ArchiveError("Password is not supported for FolderReader")

        for member in self._iter_member_infos():
            if member.is_file:
                try:
                    stream = LazyOpenIO(self.open, member, seekable=True)
                except (IOError, OSError) as e:
                    logger.warning("Error opening member %s: %s", member.filename, e)
                    archive_error = ArchiveIOError(
                        f"Error opening member {member.filename}: {e}",
                    )
                    archive_error.__cause__ = e
                    stream = ErrorIOStream(archive_error)
            else:
                stream = None

            yield member, stream
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    logger.warning(
                        "Error closing member %s", member.filename, exc_info=True
                    )

    def get_members(self) -> List[ArchiveMember]:
        return list(self._iter_member_infos())

    def open(
        self,
        member_or_filename: ArchiveMember | str,
        *,
        pwd: Optional[str | bytes] = None,
    ) -> BinaryIO:
        # pwd is ignored for FolderReader

        member_name = (
            member_or_filename.filename
            if isinstance(member_or_filename, ArchiveMember)
            else member_or_filename
        )

        # Convert archive path (with '/') to OS-specific path
        os_specific_member_path = member_name.replace("/", os.sep)
        full_path = self.path / os_specific_member_path

        if not full_path.exists():
            raise ArchiveMemberNotFoundError(
                f"Member not found: {member_name} (resolved to {full_path})"
            )

        if full_path.is_dir():
            raise ArchiveError(
                f"Cannot open directory '{member_name}' as a file stream."
            )

        # It's good practice to ensure the resolved path is still within the archive root
        # to prevent potential directory traversal issues if member_name contains '..'
        try:
            resolved_full_path = full_path.resolve()
            if (
                self.archive_path not in resolved_full_path.parents
                and resolved_full_path != self.archive_path
            ):
                # This check needs to be careful. If archive_path is /foo/bar and resolved_full_path is /foo/bar/file.txt
                # then archive_path is in resolved_full_path.parents.
                # If archive_path is /foo/bar and resolved_full_path is /foo/baz/file.txt (due to symlink or ..) this is bad.
                # A more robust check:
                if not str(resolved_full_path).startswith(str(self.archive_path)):
                    raise ArchiveMemberNotFoundError(
                        f"Access to member '{member_name}' outside archive root is denied."
                    )

        except OSError as e:  # e.g. broken symlink during resolve()
            raise ArchiveMemberNotFoundError(
                f"Error resolving path for member '{member_name}': {e}"
            ) from e

        try:
            return full_path.open("rb")
        except OSError as e:
            raise ArchiveError(
                # TODO: better exception type
                f"Cannot open member '{member_name}': {e}"
            ) from e

    def get_archive_info(self) -> ArchiveInfo:
        return ArchiveInfo(
            format=self.format.value,
            comment=str(self.archive_path),  # Use folder path as comment
            # is_solid, version, extra are not applicable for folders
        )

    def close(self) -> None:
        # No-op for FolderReader, as there's no main file handle to close.
        # Individual files are opened and closed in the open() method.
        pass
