"""Shared formatting helpers for CLI output."""

from __future__ import annotations

from datetime import datetime

from archivey.cost import AccessCost, CostReceipt, ListingCost, StreamCapability
from archivey.escaping import escape_control_chars
from archivey.exceptions import ArchiveyError, ArchiveyUsageError
from archivey.types import ArchiveFormat, ArchiveMember, MemberType

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

    A thin alias over :func:`archivey.escaping.escape_control_chars`, kept because
    every CLI call site is escaping a member name (or a path built from one) and
    reads better for saying so. Exception messages escape themselves; the CLI does
    not escape them again, or the backslashes would double.
    """
    return escape_control_chars(name)


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
            f"{algo}={format_hash_value(val)}"
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
        elif cost.listing_cost is ListingCost.INDEXED:
            bits.append("reading one member may decode earlier members in its block")
        if cost.solid_block_count is not None:
            n = cost.solid_block_count
            bits.append(f"{n} solid block{'s' if n != 1 else ''}")
        if cost.stream_capability is StreamCapability.FORWARD_ONLY:
            bits.append("forward-only source")
        return f"solid ({'; '.join(bits)})" if bits else "solid"

    if cost.listing_cost is ListingCost.INDEXED:
        head = "random (indexed)"
    elif cost.listing_cost is ListingCost.REQUIRES_SCANNING:
        head = "random (listing requires scan)"
    else:
        head = "random (listing requires decompression)"

    if cost.stream_capability is StreamCapability.FORWARD_ONLY:
        return f"{head}; forward-only source"
    return head
