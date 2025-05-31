import io
import logging
import lzma
from typing import Iterator, List, cast

try:
    import py7zr
    import py7zr.compressor
    import py7zr.exceptions
    import py7zr.helpers
    from py7zr.py7zr import ArchiveFile
except ImportError:
    py7zr = None  # type: ignore[assignment]
    ArchiveFile = None # type: ignore[misc,assignment]


from archivey.base_reader import ArchiveReader
from archivey.exceptions import (
    ArchiveCorruptedError,
    ArchiveEncryptedError,
    LibraryNotInstalledError,
)
from archivey.formats import ArchiveFormat
from archivey.types import (
    ArchiveInfo,
    ArchiveMember,
    MemberType,
)
from archivey.utils import bytes_to_str

logger = logging.getLogger(__name__)


class SevenZipReader(ArchiveReader):
    """Reader for 7-Zip archives."""

    def __init__(self, archive_path: str, *, pwd: bytes | str | None = None):
        super().__init__(ArchiveFormat.SEVENZIP)
        self.archive_path = archive_path
        self._members: list[ArchiveMember] | None = None
        self._format_info: ArchiveInfo | None = None

        if py7zr is None:
            raise LibraryNotInstalledError(
                "py7zr library is not installed. Please install it to work with 7-Zip archives."
            )

        try:
            self._archive = py7zr.SevenZipFile(
                archive_path, "r", password=bytes_to_str(pwd)
            )

        except py7zr.Bad7zFile as e:
            raise ArchiveCorruptedError(f"Invalid 7-Zip archive {archive_path}") from e
        except py7zr.PasswordRequired as e:
            raise ArchiveEncryptedError(
                f"7-Zip archive {archive_path} is encrypted"
            ) from e
        except TypeError as e:
            if "Unknown field" in str(e):
                raise ArchiveCorruptedError(
                    f"Corrupted header data or wrong password for {archive_path}"
                ) from e
            else:
                raise
        except EOFError as e:
            raise ArchiveCorruptedError(f"Invalid 7-Zip archive {archive_path}") from e
        except lzma.LZMAError as e:
            if "Corrupt input data" in str(e) and pwd is not None:
                raise ArchiveEncryptedError(
                    f"Corrupted header data or wrong password for {archive_path}"
                ) from e
            else:
                raise ArchiveCorruptedError(
                    f"Invalid 7-Zip archive {archive_path}"
                ) from e

    def close(self) -> None:
        """Close the archive and release any resources."""
        if self._archive:
            self._archive.close()
            self._archive = None
            self._members = None

    def _is_member_encrypted(self, file: ArchiveFile) -> bool:
        # This information is not directly exposed by py7zr, so we need to use an
        # internal function to infer it.
        if file.folder is None:
            return False

        return py7zr.compressor.SupportedMethods.needs_password(file.folder.coders)

    def get_members(self) -> List[ArchiveMember]:
        if self._archive is None:
            raise ValueError("Archive is closed")

        if self._members is None:
            self._members = []

            links_to_resolve = {}

            for file in self._archive.files:
                member = ArchiveMember(
                    filename=file.filename,
                    # The uncompressed field is wrongly typed in py7zr as list[int].
                    # It's actually an int.
                    file_size=file.uncompressed,  # type: ignore
                    compress_size=file.compressed,
                    mtime=py7zr.helpers.filetime_to_dt(file.lastwritetime).replace(
                        tzinfo=None
                    )
                    if file.lastwritetime
                    else None,
                    type=(
                        MemberType.DIR
                        if file.is_directory
                        else MemberType.LINK
                        if file.is_symlink
                        else MemberType.OTHER
                        if file.is_junction or file.is_socket
                        else MemberType.FILE
                    ),
                    mode=file.posix_mode,
                    crc32=file.crc32,
                    compression_method=None,  # Not exposed by py7zr
                    encrypted=self._is_member_encrypted(file),
                    raw_info=file,
                )

                if member.is_link:
                    links_to_resolve[member.filename] = member
                self._members.append(member)

            if links_to_resolve:
                self._archive.reset()
                # Wrong type in py7zr: read() actually always returns a dict.
                files = self._archive.read(list(links_to_resolve.keys()))
                for filename, file in files.items():  # type: ignore
                    links_to_resolve[filename].link_target = file.read().decode("utf-8")

        return self._members

    def open(self, member: ArchiveMember, *, pwd: str | None = None) -> io.IOBase:
        if self._archive is None:
            raise ValueError("Archive is closed")

        self._archive.reset()  # Needed after each read() call

        # TODO: can we pass all files to read() at once and return the IO objects for each file?
        # Will it decompress all files at once, or only when each IO object is read?

        try:
            # Hack: py7zr only supports setting a password when creating the
            # SevenZipFile object, not when reaading a specific file. When uncompressing
            # a file, the password is read from the file's folder, so we can set it
            # there directly.
            file_info = cast(ArchiveFile, member.raw_info)
            if pwd is not None and file_info.folder is not None:
                previous_password = file_info.folder.password
                file_info.folder.password = bytes_to_str(pwd)

            # Wrong type in py7zr: read() actually always returns a dict.
            return self._archive.read([member.filename])[member.filename]  # type: ignore
        except py7zr.exceptions.ArchiveError as e:
            raise ArchiveCorruptedError(f"Error reading member {member.filename}: {e}")
        except py7zr.PasswordRequired as e:
            raise ArchiveEncryptedError(
                f"Password required to read member {member.filename}"
            ) from e
        except lzma.LZMAError as e:
            raise ArchiveCorruptedError(
                f"Error reading member {member.filename}: {e}"
            ) from e
        finally:
            # Restore the folder to its previous state, to avoid side effects.
            if pwd is not None and file_info.folder is not None:
                file_info.folder.password = previous_password

    def iter_members(self) -> Iterator[ArchiveMember]:
        return iter(self.get_members())

    def get_archive_info(self) -> ArchiveInfo:
        """Get detailed information about the archive's format.

        Returns:
            ArchiveInfo: Detailed format information
        """
        if self._archive is None:
            raise ValueError("Archive is closed")

        sevenzip_info = self._archive.archiveinfo()

        if self._format_info is None:
            self._format_info = ArchiveInfo(
                format=self.get_format(),
                is_solid=sevenzip_info.solid,
                extra={
                    "is_encrypted": self._archive.password_protected,
                },
            )
        return self._format_info
