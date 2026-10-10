"""File-type tests on a stored Unix ``st_mode``, for the ZIP, 7z, RAR and ISO backends.

TAR types members by the header typeflag, so it has no mode to test; it maps the
typeflag with :func:`special_file_type_from_tar_typeflag`. ISO reads its symlink and
directory records first, so any other non-regular PX mode there is special.

A special mode is an attribute, not structure (design rule DR-25): it never overrides a
format's directory marker, and when the entry carries a data stream the entry is a
``FILE`` whose bytes are the content, as unzip, 7-Zip, bsdtar and ``zipfile`` deliver
them (Info-ZIP's ``zip -FI`` writes exactly that shape for a named pipe). A special-mode
entry with no stream is ``OTHER``: an empty file in its place would lose what the
archive said it was. Either way ``extra["special_file_type"]`` keeps the stored type.
"""

from __future__ import annotations

import stat

from archivey.types import SpecialFileType

_NOT_SPECIAL = frozenset({0, stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK})
UNIX_FILE_TYPE_MASK = 0o170000

_SPECIAL_FILE_TYPES: dict[int, SpecialFileType] = {
    stat.S_IFIFO: "fifo",
    stat.S_IFCHR: "char_device",
    stat.S_IFBLK: "block_device",
    stat.S_IFSOCK: "socket",
}

_TAR_SPECIAL_TYPEFLAGS: dict[bytes, SpecialFileType] = {
    b"3": "char_device",
    b"4": "block_device",
    b"6": "fifo",
}


def is_special_file_mode(mode: int) -> bool:
    """True when ``mode``'s file-type bits name anything other than a regular file,
    a directory or a symlink: a device, a FIFO, a socket or an unknown type.

    A mode with no file-type bits says nothing about the type and is not special.

    Masked in Python, not with ``stat.S_IFMT``: a RAR5 attribute is a vint that can
    exceed a C ``unsigned long``, which the C helper refuses with ``OverflowError``.
    """
    return (mode & UNIX_FILE_TYPE_MASK) not in _NOT_SPECIAL


def special_file_type(mode: int) -> SpecialFileType | None:
    """The ``extra["special_file_type"]`` value for ``mode``, or ``None`` when the
    mode is not special (:func:`is_special_file_mode`)."""
    if not is_special_file_mode(mode):
        return None
    return _SPECIAL_FILE_TYPES.get(mode & UNIX_FILE_TYPE_MASK, "unknown")


def special_file_type_from_tar_typeflag(typeflag: bytes) -> SpecialFileType:
    """The ``extra["special_file_type"]`` value for a TAR member that tarfile's
    predicates typed neither file, directory, symlink nor hard link: a device or
    FIFO typeflag, or anything unknown (a GNU long-name placeholder, a vendor type)."""
    return _TAR_SPECIAL_TYPEFLAGS.get(typeflag, "unknown")
