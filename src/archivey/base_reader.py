import abc
import logging
import os
import posixpath
import threading
from collections import defaultdict
from typing import BinaryIO, Callable, Collection, Iterator, List, Union

from archivey.config import ArchiveyConfig, get_default_config
from archivey.exceptions import (
    ArchiveMemberCannotBeOpenedError,
    ArchiveMemberNotFoundError,
)
from archivey.extraction_helper import ExtractionHelper
from archivey.io_helpers import LazyOpenIO
from archivey.types import ArchiveFormat, ArchiveInfo, ArchiveMember, MemberType
from archivey.unique_ids import UNIQUE_ID_GENERATOR

logger = logging.getLogger(__name__)


def _build_member_included_func(
    members: Collection[Union[ArchiveMember, str]]
    | Callable[[ArchiveMember], bool]
    | None,
) -> Callable[[ArchiveMember], bool]:
    if members is None:
        return lambda _: True
    elif isinstance(members, Callable):
        return members

    filenames: set[str] = set()
    internal_ids: set[int] = set()

    if members is not None and not isinstance(members, Callable):
        for member in members:
            if isinstance(member, ArchiveMember):
                internal_ids.add(member.member_id)
            else:
                filenames.add(member)

    return lambda m: m.filename in filenames or m.member_id in internal_ids


def _build_iterator_filter(
    members: Collection[Union[ArchiveMember, str]]
    | Callable[[ArchiveMember], bool]
    | None,
    filter: Callable[[ArchiveMember], Union[ArchiveMember, None]] | None,
) -> Callable[[ArchiveMember], ArchiveMember | None]:
    """Build a filter function for the iterator.

    Args:
        members: A collection of members or a callable to filter members.
        filter: A filter function to apply to each member. If specified, only
            members for which the filter returns True will be yielded.
            The filter may be called for all members either before or during the
            iteration, so don't rely on any specific behavior.
    """
    member_included = _build_member_included_func(members)

    def _apply_filter(member: ArchiveMember) -> ArchiveMember | None:
        if not member_included(member):
            return None

        if filter is None:
            return member
        else:
            filtered = filter(member)
            # Check the filtered still refers to the same member
            if filtered is not None and filtered.member_id != member.member_id:
                raise ValueError(
                    f"Filter returned a member with a different internal ID: {member.filename} {member.member_id} -> {filtered.filename} {filtered.member_id}"
                )

            return filtered

    return _apply_filter


class ArchiveReader(abc.ABC):
    """Abstract base class for archive streams."""

    def __init__(
        self,
        format: ArchiveFormat,
        archive_path: str | bytes | os.PathLike,
        random_access_available: bool,
        members_list_available: bool,
    ):
        """Initialize the archive reader.

        Args:
            format: The format of the archive
            archive_path: The path to the archive file
        """
        self.format = format
        self.archive_path = (
            archive_path.decode("utf-8")
            if isinstance(archive_path, bytes)
            else str(archive_path)
        )
        self.config: ArchiveyConfig = get_default_config()
        self._member_id_to_member: dict[int, ArchiveMember] = {}
        self._filename_to_members: dict[str, list[ArchiveMember]] = defaultdict(list)
        self._normalized_path_to_last_member: dict[str, ArchiveMember] = {}
        self._all_members_registered: bool = False
        self._member_id_counter: int = 0
        self._member_id_lock: threading.Lock = threading.Lock()

        self._archive_id: int = UNIQUE_ID_GENERATOR.next_id()

        self._random_access_supported = random_access_available
        self._members_list_available = members_list_available

    def _resolve_link_target(
        self, member: ArchiveMember, visited_members: set[int] = set()
    ) -> None:
        if member.link_target is None:
            return

        # Run the search even if we had previously resolved the link target, as it
        # may have been overwritten by a later member with the same filename.

        if member.type == MemberType.HARDLINK:
            # Look for the last member with the same filename and a lower member_id.
            link_target = member.link_target
            if link_target is None:
                logger.warning(f"Hardlink target is None for {member.filename}")
                return

            members = self._filename_to_members.get(link_target, [])
            target_member = max(
                (m for m in members if m.member_id < member.member_id),
                key=lambda m: m.member_id,
                default=None,
            )
            if target_member is None:
                logger.warning(
                    f"Hardlink target {link_target} not found for {member.filename}"
                )
                return

            # If the target is another hardlink, recursively resolve it.
            # As we always look for members with a lower member_id, this will not
            # loop forever.
            if target_member.type == MemberType.HARDLINK:
                self._resolve_link_target(target_member)
                # This is guaranteed to point to the final non-hardlink in the chain.
                target_member = target_member.link_target_member
                if target_member is None:
                    logger.warning(
                        f"Hardlink target {link_target} not found for {member.filename} (when following hardlink)"
                    )
                    return

            member.link_target_member = target_member
            member.link_target_type = target_member.type

        elif member.type == MemberType.SYMLINK:
            normalized_link_target = posixpath.normpath(
                posixpath.join(posixpath.dirname(member.filename), member.link_target)
            )
            target_member = self._normalized_path_to_last_member.get(
                normalized_link_target
            )
            if target_member is None:
                logger.warning(
                    f"Symlink target {normalized_link_target} not found for {member.filename}"
                )
                return

            if target_member.is_link:
                if target_member.member_id in visited_members:
                    logger.error(
                        f"Symlink loop detected: {member.filename} -> {target_member.filename}"
                    )
                    return
                self._resolve_link_target(
                    target_member, visited_members | {member.member_id}
                )
                if target_member.link_target_member is None:
                    logger.warning(
                        f"Link target {target_member.filename} {target_member.member_id} does not have a valid target (when resolving {member.filename} {member.member_id})"
                    )
                    return

                target_member = target_member.link_target_member

            member.link_target_member = target_member
            member.link_target_type = target_member.type

    def register_member(self, member: ArchiveMember) -> None:
        with self._member_id_lock:
            self._member_id_counter += 1
            member._member_id = self._member_id_counter
        member._archive_id = self._archive_id

        assert member.member_id not in self._member_id_to_member, (
            f"Member {member.filename} already registered with member_id {member.member_id}"
        )

        logger.info(f"Registering member {member.filename} ({member.member_id})")

        members_with_filename = self._filename_to_members[member.filename]
        if member not in members_with_filename:
            members_with_filename.append(member)
            members_with_filename.sort(key=lambda m: m.member_id)

        normalized_path = posixpath.normpath(member.filename)
        if (
            normalized_path not in self._normalized_path_to_last_member
            or self._normalized_path_to_last_member[normalized_path].member_id
            < member.member_id
        ):
            self._normalized_path_to_last_member[normalized_path] = member

        self._member_id_to_member[member.member_id] = member

        self._resolve_link_target(member)

    def set_all_members_registered(self) -> None:
        self._all_members_registered = True

    @abc.abstractmethod
    def close(self) -> None:
        """Close the archive stream and release any resources."""
        pass

    def get_members_if_available(self) -> List[ArchiveMember] | None:
        """Get a list of all members in the archive, or None if not available. May not be available for stream archives."""
        if self._all_members_registered:
            return list(self._member_id_to_member.values())

        if not self._members_list_available:
            return None

        return self.get_members()

    def iter_members_with_io(
        self,
        members: Union[
            Collection[Union[ArchiveMember, str]], Callable[[ArchiveMember], bool], None
        ] = None,
        *,
        pwd: bytes | str | None = None,
        filter: Callable[[ArchiveMember], ArchiveMember | None] | None = None,
    ) -> Iterator[tuple[ArchiveMember, BinaryIO | None]]:
        """Iterate over all members in the archive.

        Args:
            filter: A filter function to apply to each member. If specified, only
            members for which the filter returns True will be yielded.
            The filter may be called for all members either before or during the
            iteration, so don't rely on any specific behavior.
            pwd: Password to use for decryption, if needed and different from the one
            used when opening the archive. May not be supported by all archive formats.

        Returns:
            A (ArchiveMember, BinaryIO) iterator over the members. Each stream should
            be read before the next member is retrieved. The stream may be None if the
            member is not a file.
        """
        # This is a default implementation for random-access readers which support
        # open().
        assert self._random_access_supported, (
            "Non-random access readers must override iter_members_with_io()"
        )

        filter_func = _build_iterator_filter(members, filter)

        for member in self.get_members():
            filtered = filter_func(member)
            if filtered is None:
                continue

            try:
                # TODO: some libraries support fast seeking for files (either all,
                # or only non-compressed ones), so we should set seekable=True
                # if possible.
                stream = (
                    LazyOpenIO(self.open, member, pwd=pwd, seekable=False)
                    if member.is_file
                    else None
                )
                yield filtered, stream

            finally:
                if stream is not None:
                    stream.close()

    @abc.abstractmethod
    def get_archive_info(self) -> ArchiveInfo:
        """Get detailed information about the archive.

        Returns:
            ArchiveInfo: Detailed format information including compression method
        """
        pass

    def has_random_access(self) -> bool:
        """Check if opening members is possible (i.e. not streaming-only access)."""
        return self._random_access_supported

    def _extract_pending_files(
        self, path: str, extraction_helper: ExtractionHelper, pwd: bytes | str | None
    ):
        """Extract pending files from the archive. Intended to be overridden by subclasses.

        For some libraries, extraction using extractall() or similar is faster than
        opening each member individually, so subclasses should override this method
        if it's beneficial.

        All directories needed are guaranteed to exist. The pending files are either
        regular files, or links if the archive does not store link targets in the header.
        Metadata attributes for the extracted files will be applied afterwards.
        """
        members_to_extract = extraction_helper.get_pending_extractions()
        for member in members_to_extract:
            stream = self.open(member, pwd=pwd) if member.is_file else None
            extraction_helper.extract_member(member, stream)
            if stream:
                stream.close()

    def _extractall_with_random_access(
        self,
        path: str,
        filter_func: Callable[[ArchiveMember], Union[ArchiveMember, None]],
        pwd: bytes | str | None,
        extraction_helper: ExtractionHelper,
    ):
        # For readers that support random access, register all members first to get
        # a complete list of members that need to be extracted, so that the
        # subclass can extract all files at once (which may be faster).
        for member in self.get_members():
            filtered_member = filter_func(member)
            if filtered_member is None:
                continue

            extraction_helper.extract_member(filtered_member, None)

        # Extract regular files
        self._extract_pending_files(path, extraction_helper, pwd=pwd)

    def _extractall_with_streaming_mode(
        self,
        path: str,
        filter_func: Callable[[ArchiveMember], Union[ArchiveMember, None]],
        pwd: bytes | str | None,
        extraction_helper: ExtractionHelper,
    ):
        for member, stream in self.iter_members_with_io(filter=filter_func, pwd=pwd):
            logger.debug(f"Writing member {member.filename}")
            extraction_helper.extract_member(member, stream)
            if stream:
                stream.close()

    def extractall(
        self,
        path: str | os.PathLike | None = None,
        members: Union[
            List[Union[ArchiveMember, str]], Callable[[ArchiveMember], bool], None
        ] = None,
        *,
        pwd: bytes | str | None = None,
        filter: Callable[[ArchiveMember], Union[ArchiveMember, None]] | None = None,
    ) -> dict[str, ArchiveMember]:
        if path is None:
            path = os.getcwd()
        else:
            path = str(path)

        filter_func = _build_iterator_filter(members, filter)

        extraction_helper = ExtractionHelper(
            self.archive_path,
            path,
            self.config.overwrite_mode,
            can_process_pending_extractions=self.has_random_access(),
        )

        if self._random_access_supported:
            self._extractall_with_random_access(
                path, filter_func, pwd, extraction_helper
            )
        else:
            self._extractall_with_streaming_mode(
                path, filter_func, pwd, extraction_helper
            )

        extraction_helper.apply_metadata()

        return extraction_helper.extracted_members_by_path

    # Context manager support
    def __enter__(self) -> "ArchiveReader":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def get_members(self) -> List[ArchiveMember]:
        """Get a list of all members in the archive. May need to read the archive to get the members."""
        if not self._members_list_available:
            raise ValueError("Archive reader does not support get_members().")

        # Default implementation for random-access readers.
        assert self._random_access_supported, (
            "Non-random access readers must override get_members()"
        )
        if not self._all_members_registered:
            for _ in self.iter_members_with_io():
                pass
            assert self._all_members_registered

        return list(self._member_id_to_member.values())

    def _resolve_member_to_open(
        self, member_or_filename: ArchiveMember | str
    ) -> tuple[ArchiveMember, str]:
        filename = (
            member_or_filename.filename
            if isinstance(member_or_filename, ArchiveMember)
            else member_or_filename
        )
        final_member = member = self.get_member(member_or_filename)

        logger.info(
            f"Resolving link target for {member.filename} {member.type} {member.member_id}"
        )

        if member.is_link:
            # If the user is opening a link, open the target member instead.
            self._resolve_link_target(member)
            logger.info(
                f"Resolved link target for {member.filename} {member.type} {member.member_id}: {member.link_target}"
            )
            if member.link_target_member is None:
                raise ArchiveMemberCannotBeOpenedError(
                    f"Link target not found: {member.filename} (when opening {filename})"
                )
            logger.info(
                f"  target_member={member.link_target_member.member_id} {member.link_target_member.filename} {member.link_target_member.type}"
            )
            final_member = member.link_target_member

        logger.info(
            f"Final member: orig {filename} {member.member_id} {final_member.filename} {final_member.type}"
        )
        if not final_member.is_file:
            if final_member is not member:
                raise ArchiveMemberCannotBeOpenedError(
                    f"Cannot open {final_member.type} {final_member.filename} (redirected from {filename})"
                )

            raise ArchiveMemberCannotBeOpenedError(
                f"Cannot open {final_member.type} {filename}"
            )

        return final_member, filename

    @abc.abstractmethod
    def open(
        self, member_or_filename: ArchiveMember | str, *, pwd: bytes | str | None = None
    ) -> BinaryIO:
        """Open a member for reading.

        Args:
            member: The member to open
            pwd: Password to use for decryption, if needed and different from the one
            used when opening the archive.
        """
        pass

    def get_member(self, member_or_filename: ArchiveMember | str) -> ArchiveMember:
        if isinstance(member_or_filename, ArchiveMember):
            if member_or_filename.archive_id != self._archive_id:
                raise ValueError(
                    f"Member {member_or_filename.filename} is not from this archive"
                )
            return member_or_filename

        if not self._all_members_registered:
            self.get_members()

        if member_or_filename not in self._filename_to_members:
            raise ArchiveMemberNotFoundError(f"Member not found: {member_or_filename}")
        return self._filename_to_members[member_or_filename][-1]

    def extract(
        self,
        member_or_filename: ArchiveMember | str,
        path: str | None = None,
        pwd: bytes | str | None = None,
    ) -> str | None:
        if path is None:
            path = os.getcwd()

        if self._random_access_supported:
            member = self.get_member(member_or_filename)
            extraction_helper = ExtractionHelper(
                self.archive_path,
                path,
                self.config.overwrite_mode,
                can_process_pending_extractions=False,
            )

            stream = self.open(member, pwd=pwd) if member.is_file else None

            extraction_helper.extract_member(member, stream)
            if stream:
                stream.close()

            extraction_helper.apply_metadata()

        # Fall back to extractall().
        logger.warning(
            "extract() may be slow for streaming archives, use extractall instead if possible. ()"
        )
        d = self.extractall(
            path=path,
            members=[member_or_filename],
            pwd=pwd,
        )
        return list(d.keys())[0] if len(d) else None


class BaseArchiveReaderRandomAccess(ArchiveReader):
    """Abstract base class for archive readers which support random member access."""

    def __init__(
        self,
        format: ArchiveFormat,
        archive_path: str | bytes | os.PathLike,
    ):
        super().__init__(
            format,
            archive_path,
            random_access_available=True,
            members_list_available=True,
        )


#     def get_members_if_available(self) -> List[ArchiveMember] | None:
#         return self.get_members()

#     def has_random_access(self) -> bool:
#         return True

#     def extractall(
#         self,
#         path: str | os.PathLike | None = None,
#         members: Union[
#             List[Union[ArchiveMember, str]], Callable[[ArchiveMember], bool], None
#         ] = None,
#         *,
#         pwd: bytes | str | None = None,
#         filter: Callable[[ArchiveMember], Union[ArchiveMember, None]] | None = None,
#     ) -> dict[str, str]:
#         written_paths: dict[str, str] = {}

#         if path is None:
#             path = os.getcwd()
#         else:
#             path = str(path)

#         filter_func = _build_iterator_filter(members, filter)

#         extraction_helper = ExtractionHelper(
#             self.archive_path,
#             path,
#             self.config.overwrite_mode,
#             can_process_pending_extractions=True,
#         )

#         for member in self.get_members():
#             filtered_member = filter_func(member)
#             if filtered_member is None:
#                 continue

#             extraction_helper.extract_member(member, None)

#         # Extract regular files
#         self._extract_pending_files(path, extraction_helper, pwd=pwd)

#         extraction_helper.apply_metadata()

#         return written_paths


class StreamingOnlyArchiveReaderWrapper(ArchiveReader):
    """Wrapper for archive readers that only support streaming access."""

    def __init__(self, reader: ArchiveReader):
        self.reader = reader
        self.format = reader.format
        self.archive_path = reader.archive_path
        self.config = reader.config

    def close(self) -> None:
        self.reader.close()

    def get_members_if_available(self) -> List[ArchiveMember] | None:
        return self.reader.get_members_if_available()

    def iter_members_with_io(
        self, *args, **kwargs
    ) -> Iterator[tuple[ArchiveMember, BinaryIO | None]]:
        return self.reader.iter_members_with_io(*args, **kwargs)

    def get_archive_info(self) -> ArchiveInfo:
        return self.reader.get_archive_info()

    def has_random_access(self) -> bool:
        return False

    def extractall(self, *args, **kwargs) -> dict[str, ArchiveMember]:
        return self.reader.extractall(*args, **kwargs)

    # Unsupported methods for streaming-only readers

    def get_members(self) -> List[ArchiveMember]:
        raise ValueError(
            "Streaming-only archive reader does not support get_members()."
        )

    def open(
        self, member: ArchiveMember, *, pwd: bytes | str | None = None
    ) -> BinaryIO:
        raise ValueError("Streaming-only archive reader does not support open().")

    def extract(
        self,
        member_or_filename: ArchiveMember | str,
        path: str | None = None,
        pwd: bytes | str | None = None,
        preserve_links: bool = True,
    ) -> str | None:
        raise ValueError("Streaming-only archive reader does not support extract().")
