"""File-type tests on a stored Unix ``st_mode``, for the ZIP, 7z and RAR backends.

TAR types members by the header typeflag, so it has no mode to test. ISO keeps its own,
stricter test: a Rock Ridge PX mode always carries file-type bits, and its symlink and
directory records are read first, so any other non-regular mode there is ``OTHER``.
"""

from __future__ import annotations

import stat

_NOT_SPECIAL = frozenset({0, stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK})
UNIX_FILE_TYPE_MASK = 0o170000


def is_special_file_mode(mode: int) -> bool:
    """True when ``mode``'s file-type bits name anything other than a regular file,
    a directory or a symlink: a device, a FIFO, a socket or an unknown type.

    Such a member is ``MemberType.OTHER``, whatever bytes it stores, as it is in TAR
    and ISO:
    an empty file in its place would lose what the archive said it was. A mode with
    no file-type bits says nothing about the type and is not special.

    Masked in Python, not with ``stat.S_IFMT``: a RAR5 attribute is a vint that can
    exceed a C ``unsigned long``, which the C helper refuses with ``OverflowError``.
    """
    return (mode & UNIX_FILE_TYPE_MASK) not in _NOT_SPECIAL
