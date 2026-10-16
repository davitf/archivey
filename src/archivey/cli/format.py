"""Shared formatting helpers for CLI output."""

from __future__ import annotations

import os
from datetime import datetime

from archivey.cost import AccessCost, CostReceipt, ListingCost, StreamCapability
from archivey.exceptions import ArchiveyError, ArchiveyUsageError
from archivey.terminal import display_path, escape_control_chars
from archivey.types import (
    ArchiveFormat,
    ArchiveMember,
    CompressionMethod,
    HashAlgorithm,
    MemberType,
)

_TYPE_MARK = {
    MemberType.FILE: "f",
    MemberType.DIRECTORY: "d",
    MemberType.SYMLINK: "l",
    MemberType.HARDLINK: "h",
    MemberType.ANTI: "A",
    MemberType.OTHER: "?",
}


def format_format_label(fmt: ArchiveFormat) -> str:
    """Human format name (``zip``, ``7z``, ``tar.gz``) rather than enum spellings."""
    ext = fmt.file_extension()
    if ext:
        return ext
    return fmt.display_name.lower().replace("_", "-")


def escape_member_name(name: str) -> str:
    """Backslash-escape control bytes in a member name for safe terminal display.

    A thin alias over :func:`archivey.terminal.escape_control_chars`, kept because
    every CLI call site is escaping a member name (or a path built from one) and
    reads better for saying so. Exception messages escape themselves; the CLI does
    not escape them again, or the backslashes would double.
    """
    return escape_control_chars(name)


def escape_path(path: str | os.PathLike[str]) -> str:
    """Render a path for terminal display: ``/``-separated, then escaped.

    For every path the CLI prints that is not already relative to an extraction root —
    the wrapper directory, the hoist destination, a collision's destination, the
    closing summary. Most are built from a member's top-level name or from the archive's
    own filename, and either can carry control bytes. Rendering ``/``-separated first
    keeps a native Windows path from having every separator doubled by the escape
    (:func:`archivey.terminal.display_path`).
    """
    return escape_control_chars(display_path(path))


def format_error_detail(exc: BaseException) -> str:
    """Render an exception for terminal display, escaping only what has not been.

    Archivey's own exceptions escape their message at construction, so escaping them
    again here would double every backslash they wrote. Anything else — an ``OSError``
    carrying a destination filename, a third-party error — has had nothing done to it
    and still needs escaping.

    Call sites should not have to know which of the two they are holding; several hold
    a union (``ExtractionResult.error`` is ``ArchiveyError | OSError``). This is the
    one place that decides.
    """
    if isinstance(exc, (ArchiveyError, ArchiveyUsageError)):
        return str(exc)
    return escape_control_chars(str(exc))


def format_mode(mode: int | None) -> str:
    if mode is None:
        return "---------"
    bits = mode & 0o777
    perms = ""
    for who in (6, 3, 0):
        tri = (bits >> who) & 0o7
        perms += "r" if tri & 0o4 else "-"
        perms += "w" if tri & 0o2 else "-"
        perms += "x" if tri & 0o1 else "-"
    return perms


def format_mtime(dt: datetime | None) -> str:
    if dt is None:
        return "-"
    # Naive wall-clock as stored; drop tz for compact display when aware.
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    return dt.strftime("%Y-%m-%d %H:%M")


def format_size(size: int | None) -> str:
    if size is None:
        return "-"
    return str(size)


def format_hash_value(value: bytes | int) -> str:
    """Format a stored digest for CLI display (values are ``bytes``; ``int`` accepted)."""
    if isinstance(value, int):
        return f"{value:08x}" if value <= 0xFFFFFFFF else hex(value)
    return value.hex()


def format_member_line(
    member: ArchiveMember,
    *,
    digests: bool = False,
    verbose: bool = False,
) -> str:
    """Layer-1 listing line; optional stored digests and verbose extras."""
    mark = _TYPE_MARK.get(member.type, "?")
    if member.is_encrypted:
        status = "E"
    elif not member.is_current:
        status = "~"  # superseded / non-current revision
    else:
        status = "-"
    mode = format_mode(member.mode)
    size = format_size(member.size)
    mtime = format_mtime(member.modified)
    name = escape_member_name(member.name)
    if member.is_link and member.link_target is not None:
        name = f"{name} -> {escape_member_name(member.link_target)}"

    parts = [f"{mark}{status}", mode, f"{size:>10}", mtime, name]
    line = "  ".join(parts)

    if digests and member.hashes:
        digest_bits = " ".join(
            f"{algo.value}={format_hash_value(val)}"
            for algo, val in sorted(member.hashes.items())
        )
        line = f"{line}  [{digest_bits}]"

    if verbose and member.diagnostics:
        diag = "; ".join(
            d.message for d in member.diagnostics if getattr(d, "message", None)
        )
        if diag:
            line = f"{line}  ({diag})"

    return line


def format_compression(methods: tuple[CompressionMethod, ...]) -> str:
    """The member's codec chain, pre-filters first: ``deflate``, ``bcj2+lzma2``.

    A RAR method level shows after a colon (``rar:3``); ``-`` when the format
    records no method (TAR, ISO).
    """
    if not methods:
        return "-"
    return "+".join(
        m.algo.value if m.level is None else f"{m.algo.value}:{m.level}"
        for m in methods
    )


def format_ratio(member: ArchiveMember) -> str:
    """Space saved, as ``unzip -v`` prints it: ``75%`` for a member packed to a quarter."""
    size, packed = member.size, member.compressed_size
    if size is None or packed is None:
        return "-"
    if size == 0:
        return "0%"
    return f"{round(100 * (size - packed) / size)}%"


def _crc_column(member: ArchiveMember) -> str:
    crc = member.hashes.get(HashAlgorithm.CRC32)
    return format_hash_value(crc) if crc is not None else "-"


LONG_HEADER = "  ".join(
    [
        "ts",
        f"{'mode':<9}",
        f"{'size':>10}",
        f"{'packed':>10}",
        f"{'ratio':>5}",
        f"{'method':<10}",
        f"{'crc32':>8}",
        f"{'modified':<16}",
        "name",
    ]
)


def format_member_long_line(member: ArchiveMember) -> str:
    """``list --long`` line: the basic line plus packed size, ratio, method and CRC."""
    mark = _TYPE_MARK.get(member.type, "?")
    status = "E" if member.is_encrypted else ("~" if not member.is_current else "-")
    name = escape_member_name(member.name)
    if member.is_link and member.link_target is not None:
        name = f"{name} -> {escape_member_name(member.link_target)}"
    parts = [
        f"{mark}{status}",
        format_mode(member.mode),
        f"{format_size(member.size):>10}",
        f"{format_size(member.compressed_size):>10}",
        f"{format_ratio(member):>5}",
        f"{escape_control_chars(format_compression(member.compression)):<10}",
        f"{_crc_column(member):>8}",
        f"{format_mtime(member.modified):<16}",
        name,
    ]
    return "  ".join(parts)


def _format_time_detail(dt: datetime | None) -> str | None:
    return None if dt is None else dt.isoformat(sep=" ")


def format_member_details(member: ArchiveMember) -> list[str]:
    """``list --details`` block: one ``key: value`` line per field the member has.

    Fields the format does not record are left out rather than printed as ``-``, so
    the block shows what this archive actually stores. Every value is escaped: names,
    owners, the comment and the ``extra`` bag all come from the archive.
    """
    fields: list[tuple[str, object]] = [
        ("name", member.name),
        ("type", member.type.value),
    ]
    if member.is_link and member.link_target is not None:
        fields.append(("link_target", member.link_target))
    fields += [
        ("size", member.size),
        ("packed_size", member.compressed_size),
    ]
    if member.size is not None and member.compressed_size is not None:
        fields.append(("ratio", format_ratio(member)))
    if member.compression:
        fields.append(("method", format_compression(member.compression)))
    fields.append(("encrypted", "yes" if member.is_encrypted else "no"))
    if not member.is_current:
        fields.append(("current", "no"))
    if member.is_sparse:
        fields.append(("sparse", "yes"))
    fields += [
        ("modified", _format_time_detail(member.modified)),
        ("accessed", _format_time_detail(member.accessed)),
        ("created", _format_time_detail(member.created)),
        ("ctime", _format_time_detail(member.ctime)),
    ]
    if member.mode is not None:
        fields.append(("mode", f"{member.mode:04o} ({format_mode(member.mode)})"))
    fields += [
        ("uid", member.uid),
        ("gid", member.gid),
        ("owner", member.uname),
        ("group", member.gname),
    ]
    if member.create_system is not None:
        fields.append(("host_os", member.create_system.name.lower()))
    if member.windows_attrs is not None:
        fields.append(("windows_attrs", f"0x{member.windows_attrs:08x}"))
    for algo, val in sorted(member.hashes.items()):
        fields.append((algo.value, format_hash_value(val)))
    fields.append(("comment", member.comment))
    for key, value in sorted(member.extra.items()):
        if isinstance(value, bytes):
            value = value.hex()
        fields.append((f"extra.{key}", value))

    shown = [
        (escape_control_chars(k), escape_control_chars(str(v)))
        for k, v in fields
        if v is not None
    ]
    # A diagnostic's message is stored escaped already; escaping it again would
    # double its backslashes.
    shown += [
        ("diagnostic", f"{d.code.value}: {d.message}") for d in member.diagnostics
    ]
    width = max(len(k) for k, _ in shown) + 1
    return [f"{key + ':':<{width}} {text}" for key, text in shown]


def format_access_summary(cost: CostReceipt) -> str:
    """One-line human summary of ``CostReceipt`` for ``archivey info`` (Q5 / P14).

    Derived from the public open-time axes only — not accelerator install state
    (that lives in config / diagnostics, not the frozen receipt).
    """
    if cost.access_cost is AccessCost.SOLID:
        bits: list[str] = []
        if cost.listing_cost is ListingCost.REQUIRES_DECOMPRESSION:
            bits.append("listing requires decompression")
        elif cost.listing_cost is ListingCost.REQUIRES_SCANNING:
            bits.append("listing requires scan")
        bits.append("reading one member may decode earlier members in its block")
        if cost.solid_block_count is not None:
            n = cost.solid_block_count
            bits.append(f"{n} solid block{'s' if n != 1 else ''}")
        if cost.stream_capability is StreamCapability.FORWARD_ONLY:
            bits.append("forward-only source")
        return f"solid ({'; '.join(bits)})"

    if cost.listing_cost is ListingCost.INDEXED:
        head = "random (indexed)"
    elif cost.listing_cost is ListingCost.REQUIRES_SCANNING:
        head = "random (listing requires scan)"
    else:
        head = "random (listing requires decompression)"

    if cost.stream_capability is StreamCapability.FORWARD_ONLY:
        return f"{head}; forward-only source"
    return head
