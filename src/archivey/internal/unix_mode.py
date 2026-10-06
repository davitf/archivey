"""File-type tests on a stored Unix ``st_mode``, shared by the backends."""

from __future__ import annotations

import stat

_NOT_SPECIAL = frozenset({0, stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK})
_S_IFMT = 0o170000


def is_special_file_mode(mode: int) -> bool:
    """True when ``mode``'s file-type bits name anything other than a regular file,
    a directory or a symlink: a device, a FIFO, a socket or an unknown type.

    Such a member is ``MemberType.OTHER`` in every format, whatever bytes it stores:
    an empty file in its place would lose what the archive said it was. A mode with
    no file-type bits says nothing about the type and is not special.

    Masked in Python, not with ``stat.S_IFMT``: a RAR5 attribute is a vint that can
    exceed a C ``unsigned long``, which the C helper refuses with ``OverflowError``.
    """
    return (mode & _S_IFMT) not in _NOT_SPECIAL
