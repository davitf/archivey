"""Parsing for the Windows ``REPARSE_DATA_BUFFER`` that archivers store as member data.

A Windows symlink and an NTFS junction are both *reparse points*, and the file
attribute that marks them (``FILE_ATTRIBUTE_REPARSE_POINT``) is the same for both.
What separates them is the **reparse tag**, and the tag does not live in any archive
header: it is the first field of the reparse data buffer, which archivers store as
the member's *content*. So a junction is recognised the same way a symlink target is
read — from the member's data — which is why both backends do this from
``_ensure_link_target`` rather than while listing.

Measured against archives built on a Windows runner (see
``tests/fixtures/external/README.md``): 7-Zip stores this buffer for a **file**
reparse point, and stores **nothing** for a directory one. A junction is always a
directory reparse point, so 7-Zip's output never carries a junction's tag or its
target — the parser below is correct and simply never sees one from that writer.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

__all__ = [
    "FILE_ATTRIBUTE_REPARSE_POINT",
    "IO_REPARSE_TAG_MOUNT_POINT",
    "IO_REPARSE_TAG_SYMLINK",
    "ReparsePoint",
    "parse_reparse_data",
]

# winnt.h: the attribute bit shared by every reparse point, symlink and junction alike.
FILE_ATTRIBUTE_REPARSE_POINT = 0x400

# winnt.h reparse tags. Only these two name a link a filesystem user would recognise;
# every other tag (deduplication, cloud placeholders, WSL sockets, …) describes a file
# whose content is not a link target at all, so the parser declines them.
IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003  # junction
IO_REPARSE_TAG_SYMLINK = 0xA000000C

# REPARSE_DATA_BUFFER: ULONG ReparseTag, USHORT ReparseDataLength, USHORT Reserved.
_HEADER = struct.Struct("<IHH")
# Both tags' payloads open with the same four offsets, in bytes, into PathBuffer.
_NAMES = struct.Struct("<HHHH")
# A SYMLINK payload has an extra ULONG Flags before PathBuffer; MOUNT_POINT has none.
_SYMLINK_FLAGS_SIZE = 4

# The NT object-manager prefix on a substitute name ("\??\C:\dir"). It is how the
# kernel names the target and is meaningless as a path, so it is stripped.
_NT_PREFIXES = ("\\??\\", "\\\\?\\")


@dataclass(frozen=True)
class ReparsePoint:
    """A parsed reparse data buffer."""

    tag: int
    """The raw ``IO_REPARSE_TAG_*`` value."""

    target: str
    """Where the link points, with ``\\`` converted to ``/``.

    A reparse path buffer is always a Windows path, so a backslash in it is always a
    separator — unlike a stored member name, where it can be a literal character (see
    :func:`~archivey.internal.naming.resolve_link_target_name`). May be ``""`` when the
    writer stored a buffer with both names empty.
    """

    is_junction: bool
    """True for ``IO_REPARSE_TAG_MOUNT_POINT``."""


def _decode_path(buffer: bytes, offset: int, length: int) -> str:
    if length <= 0 or offset < 0 or offset + length > len(buffer):
        return ""
    # surrogatepass, not surrogateescape: the source is UTF-16 code units, and Windows
    # permits unpaired surrogates in a path. Replacing them would silently rewrite a
    # target; passing them through keeps the round trip honest.
    return buffer[offset : offset + length].decode("utf-16-le", errors="surrogatepass")


def parse_reparse_data(data: bytes) -> ReparsePoint | None:
    """Parse a ``REPARSE_DATA_BUFFER``, or return ``None`` when ``data`` is not one.

    ``None`` means "this is not a link buffer I understand" — too short, a truncated
    payload, or a tag that is not a symlink or a junction. Callers treat that as "no
    link target here" and leave the member as they found it, because the alternative
    is reporting arbitrary bytes as a filesystem path.
    """
    if len(data) < _HEADER.size:
        return None
    tag, data_length, _reserved = _HEADER.unpack_from(data, 0)
    if tag not in (IO_REPARSE_TAG_MOUNT_POINT, IO_REPARSE_TAG_SYMLINK):
        return None

    payload = data[_HEADER.size :]
    # Trust the declared length only as far as the bytes actually present: a truncated
    # buffer should parse what is there rather than raise.
    if 0 <= data_length <= len(payload):
        payload = payload[:data_length]
    if len(payload) < _NAMES.size:
        return None

    subst_offset, subst_length, print_offset, print_length = _NAMES.unpack_from(
        payload, 0
    )
    paths_at = _NAMES.size + (
        _SYMLINK_FLAGS_SIZE if tag == IO_REPARSE_TAG_SYMLINK else 0
    )
    paths = payload[paths_at:]

    # PrintName is the form Windows shows the user and is already free of the NT
    # prefix; SubstituteName is the authoritative one and is what a junction always
    # fills in, so it is the fallback rather than the other way round.
    target = _decode_path(paths, print_offset, print_length)
    if not target:
        target = _decode_path(paths, subst_offset, subst_length)
        for prefix in _NT_PREFIXES:
            if target.startswith(prefix):
                target = target[len(prefix) :]
                break

    return ReparsePoint(
        tag=tag,
        target=target.replace("\\", "/"),
        is_junction=tag == IO_REPARSE_TAG_MOUNT_POINT,
    )
